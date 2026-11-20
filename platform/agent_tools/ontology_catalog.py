"""Read-only task object selection and source-version admission checks."""
from __future__ import annotations

from collections import Counter
from contextlib import closing
from copy import deepcopy
import hashlib
import json
import math
import sqlite3
from types import SimpleNamespace

from ontology_agent import ontology_taxonomy
from .ontology import METRICS
from .ontology_runtime import OntologyRuntimeTools


CLASSES = {"UnmannedAircraft": "无人机飞行器", "LoiteringMunition": "巡飞弹 / 游荡弹药",
           "UnmannedAerialSystem": "无人机平台 / 系统"}
PURPOSES = {"overview": "能力概览", "requirements": "需求符合性", "evidence": "证据补全"}
METRIC_LABELS = {"enduranceHours": ("续航", "h"), "payloadKg": ("任务载荷", "kg"),
                 "mtowKg": ("最大起飞重量", "kg"), "maxSpeedMps": ("最大速度", "m/s")}


class SelectionChanged(ValueError):
    pass


def _hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


def source_path(profile):
    return OntologyRuntimeTools(None)._source_path(SimpleNamespace(profile=profile))


def source_version_hash(profile, path):
    source = profile.source_policy.get("ontologySource", {})
    return _hash({"path": str(path), "source": source})


def metric_options(profile):
    configured = profile.model_policy.get("config_json", {}).get("allowedMetrics", list(METRIC_LABELS))
    allowed = profile.scope_policy.get("allowedMetrics", configured)
    return [{"key": key, "label": label, "unit": unit} for key, (label, unit) in METRIC_LABELS.items()
            if key in configured and key in allowed]


def _object(row, aliases=()):
    taxonomy = ontology_taxonomy(row["entity_type"], row.get("category"), row.get("subcategory"),
                                 row["preferred_name"], row.get("english_name"), row.get("canonical_key"))
    ready = bool(row.get("entity_revision_id")) and row.get("review_status") not in {"rejected", "withdrawn"}
    return {"entityId": row["entity_id"], "name": row["preferred_name"], "englishName": row.get("english_name"),
            "country": row.get("origin_country") or "未知", "manufacturer": row.get("manufacturer") or "未知",
            "lifecycle": row.get("lifecycle_status") or "未知", "revision": row.get("current_revision") or 0,
            "reviewStatus": row.get("review_status"), "objectClass": taxonomy["ontologyClass"],
            "objectClassLabel": taxonomy["ontologyClassLabel"], "category": taxonomy.get("equipmentSubclass"),
            "categoryLabel": taxonomy.get("equipmentSubclassLabel"), "rawCategory": row.get("category"),
            "classificationBasis": "display-inferred", "aliases": list(aliases), "selectable": ready,
            "unavailableReason": "" if ready else "对象已撤回或拒绝" if row.get("review_status") in {"rejected", "withdrawn"} else "尚无可读取的属性版本"}


def _rows(con):
    return [dict(row) for row in con.execute("""SELECT e.*,r.entity_revision_id,r.attributes_json
        FROM ontology_entity e LEFT JOIN ontology_entity_revision r
        ON r.entity_id=e.entity_id AND r.revision=e.current_revision
        WHERE e.entity_type='EquipmentModel' ORDER BY e.preferred_name,e.entity_id""")]


def _scope(profile, entity_id):
    allowed = profile.scope_policy.get("allowedEntities")
    return allowed is None or entity_id in allowed


def catalog(profile, *, object_class="UnmannedAircraft", category="", query="", offset=0, limit=30):
    if object_class not in CLASSES or not isinstance(query, str) or len(query) > 200:
        raise ValueError("对象范围或搜索词无效")
    if type(offset) is not int or not 0 <= offset <= 100000 or type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("分页参数无效")
    path = source_path(profile)
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as con:
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA query_only=ON"); con.execute("BEGIN")
        aliases = {}
        for row in con.execute("SELECT entity_id,alias FROM ontology_entity_alias WHERE review_status NOT IN ('rejected','withdrawn')"):
            aliases.setdefault(row["entity_id"], []).append(row["alias"])
        objects = [_object(row, aliases.get(row["entity_id"], ())) for row in _rows(con)
                   if _scope(profile, row["entity_id"])]
    classes = [{"key": key, "label": label, "count": sum(o["objectClass"] == key for o in objects)}
               for key, label in CLASSES.items()]
    scoped = [o for o in objects if o["objectClass"] == object_class]
    categories = [{"key": key, "label": next(o["categoryLabel"] for o in scoped if o["category"] == key), "count": count}
                  for key, count in sorted(Counter(o["category"] for o in scoped).items())]
    if category and category not in {item["key"] for item in categories}:
        scoped = []
    term = query.strip().casefold()
    matching = [o for o in scoped if (not category or o["category"] == category) and
                (not term or any(term in str(v or "").casefold() for v in
                    [o["entityId"], o["name"], o["englishName"], *o["aliases"]]))]
    return {"sourceKey": profile.source_policy.get("ontologySource", {}).get("source_key", "uav-ontology"),
            "classes": classes, "categories": categories, "objects": matching[offset:offset + limit],
            "total": len(matching), "readable": sum(o["selectable"] for o in matching), "offset": offset,
            "hasMore": offset + limit < len(matching), "metrics": metric_options(profile)}


def preflight(profile, entity_id, metrics):
    options = {item["key"]: item for item in metric_options(profile)}
    if not isinstance(entity_id, str) or not 1 <= len(entity_id) <= 200:
        raise ValueError("请选择一个具体机型")
    if (not isinstance(metrics, list) or not metrics or any(not isinstance(key, str) or key not in options for key in metrics)
            or len(metrics) != len(set(metrics))):
        raise ValueError("请选择该 Agent 支持的目标属性")
    if not _scope(profile, entity_id):
        raise PermissionError("对象超出该 Agent 的配置范围")
    path = source_path(profile)
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as con:
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA query_only=ON"); con.execute("BEGIN")
        row = con.execute("""SELECT e.*,r.entity_revision_id,r.attributes_json FROM ontology_entity e
            LEFT JOIN ontology_entity_revision r ON r.entity_id=e.entity_id AND r.revision=e.current_revision
            WHERE e.entity_id=? AND e.entity_type='EquipmentModel'""", (entity_id,)).fetchone()
        if row is None:
            raise ValueError("所选对象不是本体库中的无人机装备对象")
        row = dict(row); obj = _object(row)
        if obj["objectClass"] not in CLASSES:
            raise ValueError("所选对象类别不支持能力分析")
        raw = row.get("attributes_json")
        if obj["selectable"]:
            if not isinstance(raw, str) or len(raw.encode("utf-8")) > 16 * 1024 * 1024:
                raise ValueError("本体属性版本为空或超出读取限制，请先修复源数据")
            try:
                document = json.loads(raw)
            except ValueError:
                raise ValueError("本体属性版本格式异常，请先修复源数据") from None
            if not isinstance(document, dict) or not isinstance(document.get("attributes", {}), dict):
                raise ValueError("本体属性必须是对象，请先修复源数据")
            attributes = document.get("attributes", {})
        else:
            attributes = {}
        checks = []
        for metric in metrics:
            attribute, units = next((key, units) for key, (name, units) in METRICS.items() if name == metric)
            group = attributes.get(attribute, {})
            raw_values = group.get("values", []) if isinstance(group, dict) else []
            values = [v for v in raw_values if isinstance(v, dict)] if isinstance(raw_values, list) else []
            conflicting = "conflict" in str(group.get("state", "")) if isinstance(group, dict) else False
            exact = len(values) == 1 and values[0].get("qualifier") == "exact" and group.get("state") == "reported"
            value = values[0].get("normalizedValue", values[0].get("value")) if values else None
            numeric = (exact and isinstance(value, (int, float)) and not isinstance(value, bool)
                       and math.isfinite(value) and value >= 0 and isinstance(values[0].get("unit"), str)
                       and values[0]["unit"] in units)
            references = [v.get("evidenceRefs") if isinstance(v.get("evidenceRefs"), list) else [] for v in values]
            evidence_ids = {ref["evidenceId"] for refs in references for ref in refs
                            if isinstance(ref, dict) and isinstance(ref.get("evidenceId"), str)}
            linked = {eid: dict(e) for eid in evidence_ids for e in con.execute(
                "SELECT quote,verification_status,source_ref,source_version FROM ontology_evidence_link WHERE evidence_id=?", (eid,))}
            def source(ref):
                return linked.get(ref.get("evidenceId"), {}) if isinstance(ref, dict) and isinstance(ref.get("evidenceId"), str) else {}
            has_evidence = bool(values) and all(any(str(source(ref).get("quote") or "").strip() for ref in refs) for refs in references)
            verified = has_evidence and all(bool(refs) and all(source(ref).get("verification_status") == "verified"
                and str(source(ref).get("quote") or "").strip() for ref in refs) for refs in references)
            checks.append({**options[metric], "state": "missing" if not values else "conflicting" if conflicting or len(values) > 1 else "reported" if numeric else "qualified",
                "values": [{key: v.get(key) for key in ("value", "normalizedValue", "unit", "qualifier", "conditions", "variant")} for v in values[:3]],
                "evidence": [{"evidenceId": eid, "quote": e["quote"], "sourceRef": e["source_ref"],
                              "sourceVersion": e["source_version"], "verificationStatus": e["verification_status"]}
                             for eid, e in sorted(linked.items())[:5]],
                "hasEvidence": bool(has_evidence), "conditionsDeclared": bool(values) and all(bool(v.get("conditions")) for v in values),
                "reviewed": bool(values) and all(v.get("reviewStatus") in {"approved", "accepted", "published"} for v in values),
                "truthVerified": bool(verified) and all(v.get("truthVerified") is True for v in values)})
    selection = None
    if obj["selectable"]:
        selection = {"entityId": entity_id, "revision": obj["revision"], "entityRevisionId": row["entity_revision_id"],
                     "revisionHash": hashlib.sha256(raw.encode()).hexdigest(), "sourceKey": profile.source_policy.get("ontologySource", {}).get("source_key", "uav-ontology"),
                     "sourceVersionHash": source_version_hash(profile, path), "objectClass": obj["objectClass"],
                     "identityHash": _hash({key: obj[key] for key in ("entityId", "name", "englishName", "country", "manufacturer", "objectClass", "category")})}
    return {"object": obj, "metrics": checks, "selection": selection, "canSubmit": obj["selectable"],
            "availableCount": sum(c["state"] != "missing" for c in checks), "targetCount": len(checks),
            "sourceKey": profile.source_policy.get("ontologySource", {}).get("source_key", "uav-ontology")}


def bind_selection(profile, data):
    """Recheck browser expectations before any Run/budget/queue writes."""
    expected = data.get("entitySelection")
    if not isinstance(expected, dict):
        raise ValueError("请先选择机型并完成资料预检")
    purpose = data.get("analysisPurpose", "overview")
    if not isinstance(purpose, str) or purpose not in PURPOSES:
        raise ValueError("分析目的无效")
    context = data.get("analysisContext", {})
    if not isinstance(context, dict) or set(context) - {"configuration", "requirementText"} or any(
        not isinstance(v, str) or len(v) > 4000 for v in context.values()):
        raise ValueError("构型、工况或需求说明无效")
    if purpose == "requirements" and not context.get("requirementText", "").strip():
        raise ValueError("需求符合性分析需要填写阈值与适用工况")
    requirements = data.get("requirements", {})
    if not isinstance(requirements, dict):
        raise ValueError("目标属性必须是对象")
    result = preflight(profile, data.get("entityId"), list(requirements))
    if not result["canSubmit"]:
        raise ValueError(result["object"]["unavailableReason"])
    if expected != result["selection"]:
        raise SelectionChanged("机型资料或数据源已变化，请重新预检后提交")
    bound = deepcopy(data)
    bound["entitySelection"] = result["selection"]
    bound["selectedObject"] = {key: result["object"][key] for key in ("entityId", "name", "englishName", "country", "manufacturer", "objectClass", "categoryLabel", "classificationBasis", "revision")}
    bound["selectionReason"] = {"purpose": purpose, "targetMetrics": list(data["requirements"]),
                                "basis": "user-selected-specific-model", "evidenceVerification": "requires-review"}
    return bound
