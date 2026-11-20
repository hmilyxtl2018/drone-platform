"""Executable ontology workbench Agent for the UAV candidate dataset.

The Agent is intentionally local-first: it reads the reviewable ontology
candidate tables, computes deterministic statistics, optionally reports model
tool availability, and delegates object evidence collection to the existing
verification queue.  It never publishes trusted ontology facts directly.
"""

from __future__ import annotations

import json
import re
import hashlib
import secrets
from urllib.parse import urlsplit, urlunsplit
from datetime import datetime, timezone
from typing import Any

try:
    from equipment_verification import EquipmentVerificationQueue, TARGETS
    from intelligence_store import IntelligenceNotFound, IntelligenceValidationError
except ModuleNotFoundError:  # pragma: no cover
    from .equipment_verification import EquipmentVerificationQueue, TARGETS
    from .intelligence_store import IntelligenceNotFound, IntelligenceValidationError


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _json(value: Any, default: Any) -> Any:
    if value in (None, ""):
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _value(document: dict[str, Any], key: str) -> dict[str, Any]:
    bucket = (document.get("attributes") or {}).get(key) or {}
    values = bucket.get("values") or []
    return values[0] if values else {}


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _avg(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 1) if values else None


def _trace(step: str, status: str = "done", detail: str = "", **extra: Any) -> dict[str, Any]:
    return {"step": step, "status": status, "detail": detail, "at": _now(), **extra}


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _id(prefix: str, *parts: str) -> str:
    source = "|".join(str(part) for part in parts if part is not None)
    return f"{prefix}-{hashlib.sha256(source.encode('utf-8')).hexdigest()[:20]}"


def _equipment_subclass(text: str) -> tuple[str, str]:
    """Normalize the mission-oriented second-level UAV classification.

    The workbook's ``类别`` and ``子类别`` values are source labels rather than
    ontology classes.  Keep the source values intact, while exposing a stable
    second-level class for filtering and analysis.  A model may still carry
    multiple mission roles in its raw attributes; this field is the primary
    display bucket only.
    """
    patterns = (
        (r"察打|攻击|ucav|armed|strike", "strike", "察打一体 / 攻击型"),
        (r"侦察|监视|isr|recon|surveillance", "reconnaissance", "侦察监视型"),
        (r"电子战|干扰|电子支援|ew|electronic warfare|jamming", "electronic-warfare", "电子战型"),
        (r"中继|通信|数据链|relay|communications", "communications-relay", "通信中继型"),
        (r"运输|补给|货运|物流|cargo|logistic|delivery", "logistics", "运输补给型"),
        (r"僚机|忠诚僚机|协同|蜂群|cca|loyal wingman|wingman|teaming", "collaborative-wingman", "无人僚机 / 协同作战型"),
        (r"靶机|试验靶|target drone|target", "target", "靶机 / 试验型"),
        (r"舰载|航母|shipborne|carrier", "shipborne", "舰载无人机"),
    )
    for pattern, key, label in patterns:
        if re.search(pattern, text, flags=re.I):
            return key, label
    return "general-purpose", "通用任务型"


def ontology_taxonomy(entity_type: str | None, category: str | None = None, subcategory: str | None = None,
                      preferred_name: str | None = None, english_name: str | None = None,
                      canonical_key: str | None = None) -> dict[str, str]:
    """Return the stable display taxonomy used by the workbench.

    ``entity_type`` is the ontology family; category/subcategory describe the
    equipment specialization.  A platform/system is deliberately separate
    from an individual unmanned aircraft model so their attributes cannot be
    mixed during review or trend aggregation.
    """
    entity_type = str(entity_type or "")
    canonical_key = str(canonical_key or "")
    text = " ".join(str(value or "") for value in (preferred_name, english_name, category, subcategory))
    if entity_type == "Organization" or canonical_key.startswith("organization-"):
        return {"ontologyLayer": "organization", "ontologyLayerLabel": "组织", "ontologyClass": "Organization", "ontologyClassLabel": "组织机构"}
    if entity_type == "Program" or canonical_key.startswith("program-"):
        return {"ontologyLayer": "program", "ontologyLayerLabel": "项目", "ontologyClass": "Program", "ontologyClassLabel": "项目 / 计划"}
    if entity_type == "Technology" or canonical_key.startswith("technology-"):
        return {"ontologyLayer": "technology", "ontologyLayerLabel": "技术", "ontologyClass": "Technology", "ontologyClassLabel": "技术对象"}
    if entity_type == "EventRecord" or canonical_key.startswith("event-"):
        return {"ontologyLayer": "event", "ontologyLayerLabel": "事件", "ontologyClass": "EventRecord", "ontologyClassLabel": "公开事件"}
    if entity_type == "EquipmentModel" or canonical_key.startswith("equipment-"):
        if re.search(r"巡飞|游荡弹|loitering|loitering munition", text, flags=re.I):
            return {"ontologyLayer": "uav", "ontologyLayerLabel": "无人机", "ontologyClass": "LoiteringMunition", "ontologyClassLabel": "巡飞弹 / 游荡弹药", "equipmentSubclass": "loitering-munition", "equipmentSubclassLabel": "巡飞弹 / 游荡弹药"}
        if re.search(r"系统|平台|system|platform|\buas\b|istar|tuas|stuas", text, flags=re.I):
            subclass_key, subclass_label = _equipment_subclass(text)
            return {"ontologyLayer": "uav", "ontologyLayerLabel": "无人机", "ontologyClass": "UnmannedAerialSystem", "ontologyClassLabel": "无人机平台 / 系统", "equipmentSubclass": "system-platform", "equipmentSubclassLabel": "无人机平台 / 系统"}
        subclass_key, subclass_label = _equipment_subclass(text)
        return {"ontologyLayer": "uav", "ontologyLayerLabel": "无人机", "ontologyClass": "UnmannedAircraft", "ontologyClassLabel": "无人机飞行器", "equipmentSubclass": subclass_key, "equipmentSubclassLabel": subclass_label}
    return {"ontologyLayer": "other", "ontologyLayerLabel": "其他主体", "ontologyClass": "Thing", "ontologyClassLabel": "待分类主体"}


SCOPE_TARGETS = {
    "all": ["identity", "performance", "systems", "collaboration", "industrial", "combat", "imagery"],
    "missing": ["identity", "performance", "systems", "collaboration", "industrial", "combat"],
    "identity": ["identity"],
    "performance": ["performance"],
    "systems": ["systems"],
    "collaboration": ["collaboration"],
    "industrial": ["industrial"],
    "combat": ["combat"],
    "imagery": ["imagery"],
}

ATTRIBUTE_ALIASES = {
    "category": ["类别", "category", "type"],
    "subcategory": ["子类别", "subcategory"],
    "lifecycle-status": ["状态", "服役状态", "lifecycle", "status"],
    "first-flight-year": ["首飞", "first flight"],
    "service-entry-year": ["服役", "service entry"],
    "payload-capacity": ["载荷", "payload"],
    "max-speed": ["速度", "speed"],
    "ceiling": ["升限", "ceiling", "altitude"],
    "flight-range": ["航程", "range"],
    "endurance": ["续航", "endurance"],
    "sensor-payload": ["传感器", "sensor", "electro-optical", "infrared", "eo/ir", "laser designator", "payload"],
    "data-link": ["数据链", "data link", "communications relay", "ground data terminal", "data terminal", "tcdl", "interoperability", "interoperable", "encryption"],
    "primary-weapons": ["武器", "weapon", "armament", "munition"],
    "swarm-compatibility": ["蜂群", "swarm", "manned-unmanned team", "teaming", "collaborative", "协同"],
    "autonomy-mode": ["自主", "autonomy", "autonomous", "mission autonomy"],
    "human-oversight": ["人工监督", "human oversight", "human-in-the-loop", "operator", "ground control", "mission payload operator"],
    "combat-status": ["实战", "combat", "operational", "combat operations", "deployed", "sustainment"],
    "combat-conflict-count": ["冲突", "conflict", "operation iraqi freedom", "operation enduring freedom", "theater"],
    "combat-hours-or-production": ["小时", "hours", "生产", "production", "flew over", "flight hours"],
    "reference-unit-cost": ["单价", "cost"],
    "production-count": ["产量", "production"],
}


class OntologyAgent:
    def __init__(self, store, *, trend_provider: Any = None, verification_queue: EquipmentVerificationQueue | None = None):
        self.store = store
        self.trend_provider = trend_provider
        self.queue = verification_queue or EquipmentVerificationQueue(store)

    def _provider_status(self) -> dict[str, Any]:
        provider = self.trend_provider
        metadata = provider.metadata() if hasattr(provider, "metadata") else None
        configured = bool(getattr(provider, "configured", False))
        return {
            "id": (metadata or {}).get("id") or ("deepseek-trend-provider" if configured else "deterministic-agent"),
            "model": (metadata or {}).get("model"),
            "configured": configured,
            "role": "draft-analysis-only",
            "writesOntology": False,
        }

    def _entities(self) -> list[dict[str, Any]]:
        with self.store._connect() as con:
            rows = con.execute(
                "SELECT entity_id,canonical_key,entity_type,preferred_name,english_name,category,subcategory,lifecycle_status,"
                "origin_country,manufacturer,user_countries_json,combat_status,current_revision,review_status,updated_at "
                "FROM ontology_entity ORDER BY preferred_name"
            ).fetchall()
        entities = []
        for row in rows:
            taxonomy = ontology_taxonomy(row["entity_type"], row["category"], row["subcategory"], row["preferred_name"], row["english_name"], row["canonical_key"])
            entities.append({
                "entityId": row["entity_id"],
                "canonicalKey": row["canonical_key"],
                "entityType": row["entity_type"],
                "preferredName": row["preferred_name"],
                "englishName": row["english_name"],
                "category": row["category"] or taxonomy["ontologyClassLabel"],
                "subcategory": row["subcategory"],
                **taxonomy,
                "lifecycleStatus": row["lifecycle_status"],
                "originCountry": row["origin_country"] or "未知",
                "manufacturer": row["manufacturer"] or "未知",
                "userCountries": _json(row["user_countries_json"], []),
                "combatStatus": row["combat_status"],
                "currentRevision": row["current_revision"],
                "reviewStatus": row["review_status"],
                "updatedAt": row["updated_at"],
            })
        return entities

    def _revision_documents(self) -> dict[str, dict[str, Any]]:
        with self.store._connect() as con:
            rows = con.execute(
                "SELECT entity_id,attributes_json FROM ontology_entity_revision "
                "WHERE revision=(SELECT current_revision FROM ontology_entity e2 WHERE e2.entity_id=ontology_entity_revision.entity_id)"
            ).fetchall()
        docs: dict[str, dict[str, Any]] = {}
        for row in rows:
            docs[row["entity_id"]] = _json(row["attributes_json"], {})
        return docs

    def summary(self) -> dict[str, Any]:
        entities = self._entities()
        docs = self._revision_documents()
        category_counts: dict[str, int] = {}
        ontology_layer_counts: dict[str, int] = {}
        ontology_class_counts: dict[str, int] = {}
        ontology_subclass_counts: dict[str, int] = {}
        country_counts: dict[str, int] = {}
        manufacturer_counts: dict[str, int] = {}
        years: dict[str, int] = {}
        performance: dict[str, dict[str, Any]] = {}
        coverage_rows: list[dict[str, Any]] = []
        for entity in entities:
            category = entity["category"]
            layer = entity.get("ontologyLayerLabel") or "其他主体"
            ontology_class = entity.get("ontologyClassLabel") or "待分类主体"
            ontology_subclass = entity.get("equipmentSubclassLabel") or ontology_class
            country = entity["originCountry"]
            manufacturer = entity["manufacturer"]
            category_counts[category] = category_counts.get(category, 0) + 1
            ontology_layer_counts[layer] = ontology_layer_counts.get(layer, 0) + 1
            ontology_class_counts[ontology_class] = ontology_class_counts.get(ontology_class, 0) + 1
            ontology_subclass_counts[ontology_subclass] = ontology_subclass_counts.get(ontology_subclass, 0) + 1
            country_counts[country] = country_counts.get(country, 0) + 1
            manufacturer_counts[manufacturer] = manufacturer_counts.get(manufacturer, 0) + 1
            doc = docs.get(entity["entityId"]) or {}
            completeness = doc.get("completeness") or {}
            coverage = _number(completeness.get("coverageRatio"))
            coverage_rows.append({"entityId": entity["entityId"], "label": entity["preferredName"], "coverageRatio": coverage})
            first_flight = _value(doc, "first-flight-year").get("normalizedValue")
            try:
                year = int(first_flight)
                if 1900 <= year <= 2100:
                    years[str(year)] = years.get(str(year), 0) + 1
            except (TypeError, ValueError):
                pass
            bucket = performance.setdefault(category, {"category": category, "models": 0, "endurance": [], "speed": [], "payload": [], "range": [], "ceiling": []})
            bucket["models"] += 1
            for key, target, unit in (
                ("endurance", "endurance", "h"),
                ("max-speed", "speed", "km/h"),
                ("payload-capacity", "payload", "kg"),
                ("flight-range", "range", "km"),
                ("ceiling", "ceiling", "m"),
            ):
                item = _value(doc, key)
                number = _number(item.get("normalizedValue"))
                if number is None:
                    continue
                source_unit = str(item.get("unit") or "")
                if key == "max-speed" and source_unit.lower() in {"kt", "kts", "ktas"}:
                    number *= 1.852
                elif key == "payload-capacity" and source_unit.lower() in {"lb", "lbs", "pound", "pounds"}:
                    number *= 0.45359237
                elif source_unit and source_unit.lower() != unit.lower():
                    continue
                bucket[target].append(float(number))
        performance_rows = [{
            "category": row["category"],
            "models": row["models"],
            "avgEndurance": _avg(row["endurance"]),
            "avgSpeed": _avg(row["speed"]),
            "avgPayload": _avg(row["payload"]),
            "avgRange": _avg(row["range"]),
            "avgCeiling": _avg(row["ceiling"]),
        } for row in performance.values()]
        coverage_known = [row["coverageRatio"] for row in coverage_rows if row["coverageRatio"] is not None]
        return {
            "entityCount": len(entities),
            "categoryCounts": self._sorted_counts(category_counts),
            "ontologyLayerCounts": self._sorted_counts(ontology_layer_counts),
            "ontologyClassCounts": self._sorted_counts(ontology_class_counts),
            "ontologySubclassCounts": self._sorted_counts(ontology_subclass_counts),
            "countryCounts": self._sorted_counts(country_counts),
            "manufacturerCounts": self._sorted_counts(manufacturer_counts),
            "firstFlightByYear": [{"label": key, "count": value} for key, value in sorted(years.items())],
            "performanceByCategory": sorted(performance_rows, key=lambda row: (-row["models"], row["category"])),
            "coverage": {
                "knownCount": len(coverage_known),
                "average": _avg(coverage_known),
                "lowest": sorted([row for row in coverage_rows if row["coverageRatio"] is not None], key=lambda row: row["coverageRatio"])[:8],
            },
        }

    @staticmethod
    def _sorted_counts(counts: dict[str, int]) -> list[dict[str, Any]]:
        return [{"label": key, "count": value} for key, value in sorted(counts.items(), key=lambda item: (-item[1], item[0]))]

    def state(self) -> dict[str, Any]:
        summary = self.summary()
        with self.store._connect() as con:
            verification_count = con.execute("SELECT COUNT(*) FROM ontology_verification_job").fetchone()[0]
            source_count = con.execute("SELECT COUNT(*) FROM ontology_source_reference").fetchone()[0]
        return {
            "ok": True,
            "summary": summary,
            "tools": {
                "database": {"configured": True, "status": "connected", "writesOntology": False},
                "statistics": {"configured": True, "status": "ready", "writesOntology": False},
                "deepseek": self._provider_status(),
                "verificationQueue": {"configured": True, "status": "ready", "jobCount": verification_count},
                "sourceRegistry": {"configured": True, "status": "ready", "sourceCount": source_count},
                "humanReview": {"configured": True, "status": "required"},
            },
            "trace": [_trace("agent-state", detail="读取本体候选库、工具状态和统计摘要")],
        }

    def _model_explanation(self, *, question: str, view: str, summary: dict[str, Any], response: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        """Ask the optional model to explain tool-owned facts.

        The database query and all numbers are produced before this method is
        called.  The model receives only the bounded structured result and can
        add interpretation; it cannot change facts, call publication APIs, or
        write ontology state.  When no compatible provider is configured, the
        deterministic result remains the complete response.
        """
        provider = getattr(self.trend_provider, "claim_provider", None)
        if not bool(getattr(self.trend_provider, "configured", False)):
            return {"status": "skipped", "reason": "provider-not-configured"}, _trace("model-explanation", status="skipped", detail="DeepSeek 未配置，保留数据库统计")
        request_json = getattr(provider, "_request_json", None)
        if not callable(request_json):
            return {"status": "skipped", "reason": "provider-interface-unavailable"}, _trace("model-explanation", status="skipped", detail="当前模型提供方不支持解释接口")
        payload = {
            "question": question[:1000],
            "view": view,
            "facts": response.get("facts") or [],
            "tables": response.get("tables") or [],
            "chartSpecs": response.get("charts") or [],
            "summary": summary,
        }
        system = (
            "你是本体分析 Agent 的解释层。输入 JSON 已由数据库工具实时计算，字段中的数字是事实。"
            "只输出 JSON：{answer:string, insights:[string]}; answer 不超过 240 字，insights 最多 4 条。"
            "只能解释输入事实、比较已给出的数字并指出数据口径或缺口，不能改写数字、补造来源、"
            "推断未提供的作战效果，也不能执行写库、审核或发布动作。不要输出思维链。"
        )
        try:
            result = request_json(
                [{"role": "system", "content": system}, {"role": "user", "content": json.dumps(payload, ensure_ascii=False, separators=(",", ":"))}],
                thinking=False,
                output_token_limit=1024,
            )
            if not isinstance(result, dict):
                raise ValueError("model response must be a JSON object")
            answer = str(result.get("answer") or result.get("summary") or "").strip()[:1200]
            insights = [str(item).strip()[:300] for item in (result.get("insights") or []) if str(item).strip()][:4]
            if not answer and not insights:
                raise ValueError("model returned no explanation")
            model_name = getattr(provider, "model", None) or self._provider_status().get("model")
            return {"status": "completed", "model": model_name, "text": answer, "insights": insights}, _trace("model-explanation", detail=f"DeepSeek 解释 {view} 视角的数据库统计")
        except Exception as exc:
            return {"status": "failed", "reason": "provider-error"}, _trace("model-explanation", status="failed", detail=f"DeepSeek 解释失败：{str(exc)[:180]}")

    def query(self, payload: dict[str, Any]) -> dict[str, Any]:
        question = str(payload.get("question") or "").strip()
        requested_view = str(payload.get("view") or "").strip()
        computed_at = _now()
        summary = self.summary()
        view = self._view(question, requested_view)
        trace = [_trace("database-read", detail=f"读取 {summary['entityCount']} 个候选对象"), _trace("deterministic-analysis", detail=f"视角：{view}")]
        response = self._answer(view, question, summary)
        model_explanation, model_trace = self._model_explanation(question=question, view=view, summary=summary, response=response)
        response["ok"] = True
        response["view"] = view
        response["question"] = question
        response["summary"] = summary
        response["queryExecution"] = {
            "status": "completed",
            "computedAt": computed_at,
            "source": "ontology_entity + ontology_entity_revision",
            "statistics": "数据库工具实时读取 · 确定性统计",
            "databaseRead": True,
            "model": model_explanation,
        }
        response["modelExplanation"] = model_explanation
        response["toolStatus"] = self.state()["tools"]
        response["trace"] = trace + [model_trace] + response.get("trace", [])
        return response

    @staticmethod
    def _view(question: str, requested: str = "") -> str:
        if requested in {"overview", "taxonomy", "geography", "capability", "governance"}:
            return requested
        q = question.casefold()
        if re.search(r"国家|国别|厂商|产业|使用国|出口", q):
            return "geography"
        if re.search(r"续航|速度|载荷|航程|升限|性能|能力|指标", q):
            return "capability"
        if re.search(r"任务|运行|审核|发布|补证|流程|工具|mcp|deepseek", q):
            return "governance"
        if re.search(r"类别|分类|结构|分布|缺口", q):
            return "taxonomy"
        return "overview"

    def _answer(self, view: str, question: str, summary: dict[str, Any]) -> dict[str, Any]:
        if view == "taxonomy":
            rows = summary["categoryCounts"]
            top = "、".join(f"{row['label']}（{row['count']}）" for row in rows[:5]) or "暂无数据"
            return {
                "answer": f"类别结构显示，当前无人机候选对象主要集中在 {top}。建议从类别规模、属性覆盖率和待核验任务三个维度判断数据集是否均衡。",
                "facts": [{"label": "类别数", "value": len(rows)}, {"label": "最大类别", "value": rows[0]["label"] if rows else "--"}, {"label": "对象数", "value": summary["entityCount"]}],
                "tables": [{"title": "类别对象分布", "columns": ["类别", "对象数"], "rows": [[row["label"], row["count"]] for row in rows]}],
                "charts": [{"type": "bar", "title": "类别分布", "rows": rows}],
                "actions": [{"kind": "filter-category", "label": "按类别筛选对象"}],
                "evidence": [{"kind": "database", "label": "ontology_entity.category"}],
            }
        if view == "geography":
            countries = summary["countryCounts"]
            manufacturers = summary["manufacturerCounts"][:8]
            top = "、".join(f"{row['label']}（{row['count']}）" for row in countries[:5]) or "暂无数据"
            return {
                "answer": f"国家/厂商视角显示，来源国主要为 {top}。这些计数反映数据集覆盖，不代表能力排名；授权生产、出口和厂商关系仍需逐条来源核验。",
                "facts": [{"label": "来源国", "value": len(countries)}, {"label": "主要来源国", "value": countries[0]["label"] if countries else "--"}, {"label": "厂商记录", "value": len(summary["manufacturerCounts"])}],
                "tables": [{"title": "主要来源国", "columns": ["来源国", "对象数"], "rows": [[row["label"], row["count"]] for row in countries[:12]]}, {"title": "主要厂商", "columns": ["厂商", "对象数"], "rows": [[row["label"], row["count"]] for row in manufacturers]}],
                "charts": [{"type": "bar", "title": "来源国分布", "rows": countries}],
                "actions": [{"kind": "ask", "label": "追问某个国家或厂商"}],
                "evidence": [{"kind": "database", "label": "ontology_entity.origin_country / manufacturer"}],
            }
        if view == "capability":
            rows = summary["performanceByCategory"]
            table_rows = [[row["category"], row["models"], row["avgEndurance"], row["avgSpeed"], row["avgPayload"], row["avgRange"], row["avgCeiling"]] for row in rows]
            return {
                "answer": "能力指标视角只统计公开字段中单位明确、口径可比的值。续航、速度、载荷、航程和升限不能直接合成战力分；具体对象仍需核对测试条件和来源性质。",
                "facts": [{"label": "可比较类别", "value": len(rows)}, {"label": "最大样本类别", "value": rows[0]["category"] if rows else "--"}, {"label": "统计口径", "value": "公开字段均值"}],
                "tables": [{"title": "类别公开性能均值", "columns": ["类别", "型号数", "续航 h", "速度 km/h", "载荷 kg", "航程 km", "升限 m"], "rows": table_rows}],
                "charts": [{"type": "comparison", "title": "类别能力指标", "rows": rows}],
                "actions": [{"kind": "object-action", "label": "选择对象后核验性能参数", "action": "performance"}],
                "evidence": [{"kind": "database", "label": "ontology_entity_revision.attributes_json"}],
            }
        if view == "governance":
            tools = self.state()["tools"]
            rows = [[key, "已配置" if value.get("configured") else "未配置", value.get("status") or value.get("role") or "--"] for key, value in tools.items()]
            return {
                "answer": "运行编排应分成读库、确定性统计、可选 DeepSeek 草稿、对象级开源补证、候选证据入队、人工审核发布。当前第一版已把读库和统计做成后端 API；外部检索只在对象补证按钮触发。",
                "facts": [{"label": "DeepSeek", "value": "已配置" if tools["deepseek"]["configured"] else "未配置"}, {"label": "核验任务", "value": tools["verificationQueue"]["jobCount"]}, {"label": "发布边界", "value": "人工审核"}],
                "tables": [{"title": "工具状态", "columns": ["工具", "配置", "状态"], "rows": rows}],
                "charts": [],
                "actions": [{"kind": "object-action", "label": "选择对象并生成补证计划", "action": "plan"}],
                "evidence": [{"kind": "runtime", "label": "Agent 工具状态来自后端 state()"}],
            }
        return {
            "answer": f"当前无人机本体候选库包含 {summary['entityCount']} 个对象、{len(summary['categoryCounts'])} 个类别、{len(summary['firstFlightByYear'])} 个首飞年份。你可以按类别、国家/厂商、能力指标或任务运行继续追问。",
            "facts": [{"label": "对象数", "value": summary["entityCount"]}, {"label": "类别数", "value": len(summary["categoryCounts"])}, {"label": "平均覆盖率", "value": summary["coverage"]["average"]}],
            "tables": [{"title": "数据集概览", "columns": ["指标", "值"], "rows": [["对象数", summary["entityCount"]], ["类别数", len(summary["categoryCounts"])], ["来源国数", len(summary["countryCounts"])], ["首飞年份数", len(summary["firstFlightByYear"])]]}],
            "charts": [{"type": "bar", "title": "类别分布", "rows": summary["categoryCounts"]}, {"type": "bar", "title": "来源国分布", "rows": summary["countryCounts"]}],
            "actions": [{"kind": "ask", "label": "按类别分析"}, {"kind": "ask", "label": "比较能力指标"}, {"kind": "select-object", "label": "选择具体无人机补证"}],
            "evidence": [{"kind": "database", "label": "ontology_entity + current ontology_entity_revision"}],
        }

    def object_brief(self, entity_id: str) -> dict[str, Any]:
        entity, doc = self._entity_document(entity_id)
        attributes = doc.get("attributes") or {}
        expected = (doc.get("completeness") or {}).get("expectedKeys") or list(attributes)
        present = (doc.get("completeness") or {}).get("presentKeys") or [key for key, bucket in attributes.items() if (bucket.get("values") or [])]
        missing = [key for key in expected if key not in set(present)]
        jobs = self.queue.list(entity_id=entity["entityId"])
        with self.store._connect() as con:
            source_count = con.execute("SELECT COUNT(*) FROM ontology_source_reference WHERE entity_id=?", (entity["entityId"],)).fetchone()[0]
            candidate_count = con.execute("SELECT COUNT(*) FROM ontology_attribute_candidate WHERE entity_id=? AND status IN ('under-review','needs-evidence','edited')", (entity["entityId"],)).fetchone()[0]
            relation_candidate_count = con.execute("SELECT COUNT(*) FROM ontology_relation_candidate WHERE entity_id=? AND status IN ('under-review','needs-evidence')", (entity["entityId"],)).fetchone()[0]
        actions = self._recommended_object_actions(missing, jobs)
        return {
            "ok": True,
            "entity": entity,
            "summary": {
                "presentCount": len(present),
                "missingCount": len(missing),
                "sourceCount": source_count,
                "jobCount": len(jobs),
                "candidateCount": candidate_count,
                "relationCandidateCount": relation_candidate_count,
                "coverageRatio": (doc.get("completeness") or {}).get("coverageRatio"),
            },
            "missingAttributes": missing[:20],
            "jobs": jobs,
            "candidates": self._with_evidence_verification(self.list_attribute_candidates(entity["entityId"])),
            "relationCandidates": self._with_evidence_verification(self.list_relation_candidates(entity["entityId"])),
            "reviewHistory": self.list_candidate_reviews(entity["entityId"]),
            "actions": actions,
            "answer": f"{entity['preferredName']} 当前有 {len(present)} 个已登记字段、{len(missing)} 个缺口、{source_count} 条采编来源和 {len(jobs)} 个核验任务。建议优先补证：{', '.join(action['label'] for action in actions[:3]) or '暂无建议'}。",
            "trace": [_trace("object-read", detail=f"读取 {entity['preferredName']} 档案、当前属性版本、来源和核验任务")],
        }

    def attribute_plan(self, entity_id: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = payload or {}
        scope = str(payload.get("scope") or "missing").strip() or "missing"
        entity, doc = self._entity_document(entity_id)
        attributes = doc.get("attributes") or {}
        completeness = doc.get("completeness") or {}
        expected = completeness.get("expectedKeys") or list(attributes)
        present = set(completeness.get("presentKeys") or [key for key, bucket in attributes.items() if (bucket.get("values") or [])])
        missing = [key for key in expected if key not in present]
        unknown = [key for key, bucket in attributes.items() if bucket.get("state") == "unknown"]
        target_types = SCOPE_TARGETS.get(scope, SCOPE_TARGETS["missing"])
        target_specs = {row[0]: row for row in TARGETS}
        targets = []
        for target_type in target_types:
            spec = target_specs[target_type]
            keys = [key for key in spec[1] if key in attributes or key in missing or scope != "missing"]
            if scope == "missing" and spec[1]:
                keys = [key for key in spec[1] if key in missing or key in unknown]
            targets.append({
                "targetType": target_type,
                "label": self._target_label(target_type),
                "attributeKeys": keys,
                "sourceScopeIds": list(spec[2]),
                "query": f'"{entity["preferredName"]}" {entity.get("englishName") or ""} {entity.get("manufacturer") or ""} {entity.get("originCountry") or ""} {spec[3]}'.strip(),
                "maxItems": spec[4],
                "sinceDays": spec[5],
            })
        return {
            "ok": True,
            "entity": entity,
            "scope": scope,
            "missingAttributes": missing,
            "unknownAttributes": unknown,
            "targets": targets,
            "trace": [_trace("attribute-plan", detail=f"为 {entity['preferredName']} 生成 {len(targets)} 个属性检索目标")],
        }

    @staticmethod
    def _target_label(target_type: str) -> str:
        return {
            "identity": "身份信息", "performance": "性能参数", "systems": "任务系统",
            "collaboration": "自主协同", "industrial": "产业采购", "combat": "作战记录", "imagery": "公开影像",
        }.get(target_type, target_type)

    def collect_attribute_evidence(self, entity_id: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = payload or {}
        scope = str(payload.get("scope") or "missing").strip() or "missing"
        entity, _ = self._entity_document(entity_id)
        target_types = set(SCOPE_TARGETS.get(scope, SCOPE_TARGETS["missing"]))
        planned_jobs = self.queue.plan(entity["entityId"], force=bool(payload.get("force")), target_types=target_types)
        selected = [job for job in planned_jobs if job["targetType"] in target_types]
        executed = []
        for job in selected:
            executed.append(self.queue.execute(job["verificationId"]))
        retained = sum(len((job.get("result") or {}).get("items") or []) for job in executed)
        failures = [job for job in executed if job.get("status") == "failed"]
        status = "needs-review" if retained else "failed" if failures else "completed"
        return {
            "ok": True,
            "status": status,
            "entity": entity,
            "scope": scope,
            "jobs": executed,
            "retainedItems": retained,
            "answer": f"{entity['preferredName']} 的 {scope} 属性证据检索完成，保留 {retained} 条待核验材料。",
            "trace": [_trace("verification-plan", detail=f"复用/创建 {len(planned_jobs)} 个核验任务"), _trace("firecrawl-collect", status=status, detail=f"执行 {len(executed)} 个检索目标，保留 {retained} 条材料")],
        }

    def extract_attribute_candidates(self, entity_id: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = payload or {}
        scope = str(payload.get("scope") or "missing").strip() or "missing"
        entity, doc = self._entity_document(entity_id)
        jobs = self.queue.list(entity_id=entity["entityId"])
        target_types = set(SCOPE_TARGETS.get(scope, SCOPE_TARGETS["missing"]))
        with self.store._connect() as con:
            revision = con.execute('SELECT entity_revision_id FROM ontology_entity_revision WHERE entity_id=? AND revision=?',(entity['entityId'],entity['currentRevision'])).fetchone()
        jobs = [job for job in jobs if revision and job['entityRevisionId'] == revision[0] and job["targetType"] in target_types and (job.get("result") or {}).get("items")]
        materials = []
        for job in jobs:
            for index, item in enumerate((job.get("result") or {}).get("items") or []):
                body = str(item.get("body") or item.get("title") or "")
                if not body.strip():
                    continue
                materials.append({"jobId": job["verificationId"], "index": index, "item": item, "body": body[:5000]})
        if not materials:
            return {"ok": True, "status": "no-evidence", "entity": entity, "candidates": [], "answer": "尚无可供 DeepSeek 提取的检索材料。请先执行 Firecrawl 属性证据检索。", "trace": [_trace("extract-precheck", status="needs-evidence", detail="没有已保存材料")]}
        provider = getattr(self.trend_provider, "claim_provider", None)
        configured = bool(provider is not None and getattr(self.trend_provider, "configured", False))
        if not configured:
            return {"ok": True, "status": "model-unavailable", "entity": entity, "candidates": [], "materials": len(materials), "answer": "DeepSeek v4 未配置，不能执行模型属性提取。已保留检索材料，可配置模型后重试。", "trace": [_trace("extract-precheck", status="blocked", detail="DeepSeek 未配置")]}
        aliases = []
        with self.store._connect() as con:
            aliases = [str(row[0]).strip() for row in con.execute("SELECT alias FROM ontology_entity_alias WHERE entity_id=? ORDER BY confidence DESC, alias", (entity["entityId"],)).fetchall() if str(row[0]).strip()]
        allowed_keys = sorted({key for job in jobs for key in (job.get("attributeKeys") or [])}) or sorted(set(missing := (doc.get("completeness") or {}).get("unknownKeys") or []))
        offset = payload.get('materialOffset',0)
        if type(offset) is not int or offset < 0:
            raise IntelligenceValidationError('materialOffset must be a nonnegative integer')
        selected_materials=materials[offset:offset+12]
        body_parts = []
        material_ranges = []
        line_cursor = 0
        for material in selected_materials:
            item = material["item"]
            header = f"SOURCE {material['jobId']}#{material['index']} | {item.get('title') or 'untitled'} | {item.get('sourceUri') or item.get('itemUri') or ''}"
            chunk = header + "\n" + material["body"][:5000]
            chunk_lines = chunk.splitlines() or [chunk]
            if body_parts:
                line_cursor += 1  # blank separator inserted by join
            start_line = line_cursor + 1
            body_parts.append(chunk)
            line_cursor += len(chunk_lines)
            material_ranges.append((start_line, line_cursor, material))
        body = "\n\n".join(body_parts)
        document = {
            "id": f"ontology-attribute-extract:{entity['entityId']}:{scope}",
            "title": f"{entity['preferredName']} 属性候选提取",
            "body": body[:78000],
            "targetEntity": {
                "preferredName": entity.get("preferredName"),
                "englishName": entity.get("englishName"),
                "aliases": aliases[:16],
            },
            "allowedAttributeKeys": allowed_keys,
            "strictEquipmentExtraction": True,
            'modelTimeoutSeconds':payload.get('modelTimeoutSeconds'),
        }
        try:
            from capability_profile import REGISTRY
        except ModuleNotFoundError:
            from .capability_profile import REGISTRY
        document['attributeDefinitions'] = {key:REGISTRY['fields'][key] for key in allowed_keys if key in REGISTRY['fields']}
        try:
            claims = provider.extract(document)
        except Exception as exc:
            # Return a bounded error category, never raw provider text (which
            # may contain URLs, authorization headers or supplied documents).
            text=str(exc).casefold()
            code='provider-call-failed'
            if isinstance(exc,TimeoutError) or 'timed out' in text:code='provider-timeout'
            elif isinstance(exc,json.JSONDecodeError):code='invalid-json'
            elif 'token budget' in text:code='output-token-limit'
            elif 'different model' in text:code='model-mismatch'
            elif 'no usable claims' in text or 'claims must be an array' in text:code='invalid-claims'
            elif 'no message content' in text:code='empty-provider-response'
            return {'ok':False, 'status':'extraction-failed','entity':entity,'candidates':[], 'errorCode':code, 'answer':'DeepSeek 提取调用失败；已保存正文，可重试。没有将失败标记成无属性。', 'trace':[_trace('deepseek-extract',status='failed',detail=code)]}
        candidates = []
        relation_candidates = []
        rejected = []
        for claim in claims[:50]:
            key = self._attribute_key_for_claim(claim, allowed_keys=allowed_keys)
            if not key:
                continue
            quote = str(claim.get('quote') or '')
            matches = [m for m in selected_materials if quote and quote in m['body']]
            if not matches:
                rejected.append({'attributeKey':key,'reason':'引文不匹配已保存正文'})
                continue
            # Exact source text wins over unreliable generated line offsets.
            source_material = matches[0] if len(matches)==1 else None
            if source_material is None:
                rejected.append({'attributeKey':key,'reason':'同一引文存在于多个来源，归属不明确'})
                continue
            source_item = (source_material or {}).get("item") or {}
            if not self._claim_targets_entity(claim, entity, aliases, source_item=source_item):
                continue
            analysis_type = (claim.get('recommendation') or {}).get('analysisType')
            relation_target = str(claim.get('relationTarget') or '').strip()
            relation_predicate = str(claim.get('relationPredicate') or '').strip()
            if relation_target and relation_predicate and analysis_type != 'inference':
                relation_evidence = [{"quote":quote, "lineStart":source_material['body'][:source_material['body'].index(quote)].count('\n') + 1, "lineEnd":source_material['body'][:source_material['body'].index(quote)].count('\n') + 1 + quote.count('\n'), "snapshotId":source_item.get('snapshotId'), "source": "deepseek-document-window", "sourceSubject": claim.get("sourceSubject"), "sourceJobId": source_material.get("jobId"), "sourceItemIndex":source_material.get('index'), "sourceUri": source_item.get("sourceUri") or source_item.get("itemUri"), "sourceName": source_item.get("sourceName"), "sourceVersion": source_item.get("sourceVersion"), "contentHash": source_item.get("contentHash")}]
                relation_candidates.append(self._save_relation_candidate(entity, relation_target, relation_predicate, claim, relation_evidence, source_material.get('jobId')))
            if analysis_type == 'inference':
                rejected.append({'attributeKey':key,'reason':'推断不能当作来源属性'})
                continue
            raw = claim.get('rawValue',claim.get('value'))
            value = self._candidate_value(key, raw)
            if not value:
                continue
            if value.casefold() not in quote.casefold():
                rejected.append({'attributeKey':key,'reason':'属性原值不在引文中'})
                continue
            unit = claim.get('unit') if isinstance(claim.get('unit'),str) and claim['unit'] in quote else None
            conditions = claim.get('conditions') if isinstance(claim.get('conditions'),str) and claim['conditions'] in quote else None
            metadata = {k:claim.get(k) for k in ('qualifier','capabilityRole','variant','configuration')}
            for k in ('variant','configuration'):
                if not isinstance(metadata[k],str) or metadata[k] not in quote:
                    metadata[k] = None
            confidence = claim.get('extractionConfidence')
            if not isinstance(confidence,(int,float)) or isinstance(confidence,bool) or not 0 <= confidence <= 1:
                confidence = None
            current = _value(doc, key)
            source_job_id = (source_material or {}).get("jobId") or materials[0]["jobId"]
            local_start = source_material['body'][:source_material['body'].index(quote)].count('\n') + 1
            evidence = [{"quote":quote, "lineStart":local_start,"lineEnd":local_start+quote.count('\n'), "snapshotId":source_item.get('snapshotId'), "source": "deepseek-document-window", "sourceSubject": claim.get("sourceSubject"), "attributeKey": key, "sourceJobId": source_job_id, "sourceItemIndex":source_material['index'], "sourceUri": source_item.get("sourceUri") or source_item.get("itemUri"), "sourceName": source_item.get("sourceName"), "sourceVersion": source_item.get("sourceVersion"), "contentHash": source_item.get("contentHash")}]
            candidate_id = self._save_attribute_candidate(
                entity["entityId"], key, current.get("rawValue") or current.get("normalizedValue"), value,
                unit=unit, conditions=conditions, metadata=metadata, confidence=confidence, source_job_id=source_job_id,
                evidence=evidence, model_provider=getattr(provider, "provider_id", "deepseek-claim-provider"),
                model=getattr(provider, "model", None), trace=[_trace("deepseek-extract", detail="模型提出属性候选")],
            )
            candidates.append(self.get_attribute_candidate(candidate_id))
        return {
            "ok": True,
            "status": "under-review" if candidates else "no-candidates",
            "entity": entity,
            "candidates": candidates,
            "relationCandidates": relation_candidates,
            "rejectedClaims": rejected,
            "materialsUsed":len(selected_materials), "materialsRemaining":max(0,len(materials)-offset-len(selected_materials)),
            "answer": f"DeepSeek 已生成 {len(candidates)} 条属性候选、{len(relation_candidates)} 条关系候选；人工确认和发布前不会更新可信本体。",
            "trace": [_trace("deepseek-extract", detail=f"读取 {len(materials)} 条材料，生成 {len(candidates)} 条候选")],
        }

    @staticmethod
    def _attribute_key_for_claim(claim: dict[str, Any], *, allowed_keys: list[str] | None = None) -> str | None:
        declared = str(claim.get("attributeKey") or "").strip()
        if declared and (not allowed_keys or declared in allowed_keys):
            return declared
        if declared and allowed_keys:
            return None
        text = " ".join(str(claim.get(key) or "") for key in ("predicate", "statement", "subject", "value")).casefold()
        for key, aliases in ATTRIBUTE_ALIASES.items():
            if allowed_keys and key not in allowed_keys:
                continue
            if any(alias.casefold() in text for alias in aliases):
                return key
        return None

    @staticmethod
    def _candidate_value(attribute_key: str, raw_value: Any) -> str | None:
        """Keep model proposals as attribute values rather than source prose."""
        value = str(raw_value or "").strip()
        if not value:
            return None
        if attribute_key == "combat-status" and len(value) > 160:
            lowered = value.casefold()
            for phrase in ("currently operational", "operational", "combat operations", "in sustainment", "in service", "retired", "development"):
                if phrase in lowered:
                    return phrase
            return None
        if attribute_key in {"first-flight-year", "service-entry-year", "max-speed", "ceiling", "flight-range", "endurance", "max-takeoff-weight", "payload-capacity", "length", "wingspan"} and len(value) > 240:
            return None
        return value[:2000]

    @staticmethod
    def _claim_targets_entity(claim: dict[str, Any], entity: dict[str, Any], aliases: list[str], *, source_item: dict[str, Any] | None = None) -> bool:
        """Reject claims whose quoted source subject is another platform.

        Search results often contain comparison tables (for example,
        Integrator versus Shadow). DeepSeek must state which platform owns the
        extracted line; absent that declaration the claim remains too
        ambiguous for an object-level candidate.
        """
        target_terms = [str(entity.get("preferredName") or ""), str(entity.get("englishName") or ""), *aliases]
        target_terms = [term.casefold() for term in target_terms if term.strip()]
        # Legacy/test providers may not emit subject metadata at all. Keep
        # their existing behavior; the DeepSeek normalizer always supplies a
        # subject field, so live model output still goes through the gate.
        if "subject" not in claim and "sourceSubject" not in claim:
            return True
        source_subject = str(claim.get("sourceSubject") or "").casefold().strip()
        subject = str(claim.get("subject") or "").casefold().strip()
        if source_subject:
            # Family-name substrings cannot assign an ER/25M or neighboring
            # variant's figures to the base equipment model.
            normalize=lambda name:re.sub(r'[^\w]+',' ',name.casefold()).strip()
            return normalize(source_subject) in {normalize(term) for term in target_terms}
        if subject and any(term in subject or subject in term for term in target_terms):
            return True
        context = f"{claim.get('statement') or ''} {claim.get('quote') or ''}".casefold()
        if any(term in context for term in target_terms):
            return True
        # Official specifications often state the platform name only in the
        # heading, then use pronouns or bare measurements in following lines
        # (for example, "endurance is nine hours"). The collector has already
        # applied an object relevance gate to this source item, so permit such
        # continuation lines while retaining explicit sourceSubject checks for
        # comparison articles.
        provenance = source_item.get("provenance") if isinstance(source_item, dict) else None
        matched = provenance.get("matchedTargetAnchors") if isinstance(provenance, dict) else None
        if isinstance(matched, list):
            matched_terms = [str(value).casefold().strip() for value in matched if str(value).strip()]
            if any(any(term in anchor or anchor in term for term in target_terms) for anchor in matched_terms):
                return True
        return False

    def _save_attribute_candidate(self, entity_id: str, attribute_key: str, current_value: Any, proposed_value: str, *, unit: str | None, confidence: float | None, source_job_id: str | None, evidence: list[dict[str, Any]], model_provider: str, model: str | None, trace: list[dict[str, Any]], conditions=None, metadata=None) -> str:
        created = _now()
        content_hash = hashlib.sha256(_dump({"entityId": entity_id, "attributeKey": attribute_key, "proposedValue": proposed_value, "evidence": evidence, 'unit':unit,'conditions':conditions,'metadata':metadata}).encode("utf-8")).hexdigest()
        candidate_id = _id("attr-candidate", entity_id, attribute_key, content_hash)
        with self.store._lock, self.store._connect() as con:
            con.execute(
                "INSERT OR IGNORE INTO ontology_attribute_candidate(candidate_id,entity_id,attribute_key,current_value,proposed_value,unit,conditions,confidence,status,source_job_id,evidence_json,model_provider,model,trace_json,content_hash,created_at,updated_at,metadata_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (candidate_id, entity_id, attribute_key, None if current_value is None else str(current_value), proposed_value, unit, conditions, confidence, "under-review", source_job_id, _dump(evidence), model_provider, model, _dump(trace), content_hash, created, created,_dump(metadata or {})),
            )
        return candidate_id

    def _save_relation_candidate(self, entity: dict[str, Any], target_name: str, predicate: str, claim: dict[str, Any], evidence: list[dict[str, Any]], source_job_id: str | None) -> dict[str, Any]:
        """Persist a model-proposed relation without admitting it to the graph."""
        target_name = target_name[:240]
        predicate = predicate[:120]
        target_id = None
        with self.store._connect() as con:
            row = con.execute("SELECT entity_id FROM ontology_entity WHERE lower(preferred_name)=lower(?) OR lower(english_name)=lower(?) OR lower(canonical_key)=lower(?) LIMIT 1", (target_name, target_name, target_name)).fetchone()
            if row:
                target_id = row[0]
            if target_id is None:
                row = con.execute("SELECT entity_id FROM ontology_entity_alias WHERE lower(alias)=lower(?) LIMIT 1", (target_name,)).fetchone()
                if row:
                    target_id = row[0]
            evidence_ids = []
            for ref in evidence:
                match = con.execute("SELECT evidence_id FROM ontology_evidence_link WHERE content_hash=? AND quote=? ORDER BY created_at DESC LIMIT 1", (ref.get('contentHash'), ref.get('quote'))).fetchone()
                if match:
                    evidence_ids.append(match[0])
        confidence = claim.get('extractionConfidence')
        if not isinstance(confidence,(int,float)) or isinstance(confidence,bool) or not 0 <= confidence <= 1:
            confidence = None
        qualifier = {key: claim.get(key) for key in ('qualifier','variant','configuration','relationTargetType') if claim.get(key) is not None}
        document = {'entityId':entity['entityId'],'targetEntityId':target_id,'targetName':target_name,'predicate':predicate,'qualifier':qualifier,'evidence':evidence}
        content_hash = hashlib.sha256(_dump(document).encode('utf-8')).hexdigest()
        candidate_id = _id('relation-candidate', entity['entityId'], target_name, predicate, content_hash)
        created = _now()
        with self.store._lock,self.store._connect() as con:
            con.execute("INSERT OR IGNORE INTO ontology_relation_candidate(candidate_id,entity_id,target_entity_id,target_name,predicate,target_type,qualifier_json,confidence,status,source_job_id,evidence_json,model_provider,model,trace_json,content_hash,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (candidate_id,entity['entityId'],target_id,target_name,predicate,claim.get('relationTargetType'),_dump(qualifier),confidence,'under-review',source_job_id,_dump(evidence),'deepseek-claim-provider',getattr(getattr(self.trend_provider,'claim_provider',None),'model',None),_dump([_trace('deepseek-relation-extract',detail='模型提出关系/谓词候选')]),content_hash,created,created))
        return self.get_relation_candidate(candidate_id)

    @staticmethod
    def _relation_candidate(row):
        item=dict(row)
        return {'candidateId':item['candidate_id'],'entityId':item['entity_id'],'targetEntityId':item['target_entity_id'],'targetName':item['target_name'],'predicate':item['predicate'],'targetType':item['target_type'],'qualifier':_json(item['qualifier_json'],{}),'confidence':item['confidence'],'status':item['status'],'sourceJobId':item['source_job_id'],'evidence':_json(item['evidence_json'],[]),'modelProvider':item['model_provider'],'model':item['model'],'trace':_json(item['trace_json'],[]),'contentHash':item['content_hash'],'createdAt':item['created_at'],'updatedAt':item['updated_at']}

    def list_relation_candidates(self, entity_id: str | None = None) -> list[dict[str, Any]]:
        where=' WHERE entity_id=?' if entity_id else ''
        values=(entity_id,) if entity_id else ()
        with self.store._connect() as con:
            rows=con.execute('SELECT * FROM ontology_relation_candidate'+where+' ORDER BY created_at DESC',values).fetchall()
        return [self._relation_candidate(row) for row in rows]

    def get_relation_candidate(self, candidate_id: str) -> dict[str, Any]:
        with self.store._connect() as con:
            row=con.execute('SELECT * FROM ontology_relation_candidate WHERE candidate_id=?',(candidate_id,)).fetchone()
        if row is None: raise IntelligenceNotFound(f'relation candidate not found: {candidate_id}')
        return self._relation_candidate(row)

    def review_relation_candidate(self, candidate_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        action=str(payload.get('action') or '').strip()
        if action not in {'accept','reject','request-more-evidence'}: raise IntelligenceValidationError('action must be accept, reject or request-more-evidence')
        reviewer=str(payload.get('reviewer') or 'operator').strip()[:200] or 'operator'; reason=str(payload.get('reason') or '').strip()
        if not reason: raise IntelligenceValidationError('reason is required')
        status={'accept':'accepted','reject':'rejected','request-more-evidence':'needs-evidence'}[action]; created=_now(); review_id='relation-review-'+secrets.token_hex(8)
        with self.store._lock,self.store._connect() as con:
            row=con.execute('SELECT candidate_id FROM ontology_relation_candidate WHERE candidate_id=?',(candidate_id,)).fetchone()
            if row is None: raise IntelligenceNotFound(f'relation candidate not found: {candidate_id}')
            con.execute('UPDATE ontology_relation_candidate SET status=?,updated_at=? WHERE candidate_id=?',(status,created,candidate_id))
            con.execute('INSERT INTO ontology_relation_candidate_review(review_id,candidate_id,action,reviewer,reason,created_at) VALUES(?,?,?,?,?,?)',(review_id,candidate_id,action,reviewer,reason,created))
        return {'ok':True,'candidate':self.get_relation_candidate(candidate_id),'review':{'reviewId':review_id,'candidateId':candidate_id,'action':action,'reviewer':reviewer,'reason':reason,'createdAt':created}}

    def publish_relation_candidate(self, candidate_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        candidate=self.get_relation_candidate(candidate_id)
        publisher=str(payload.get('publishedBy') or '').strip(); reason=str(payload.get('reason') or '').strip()
        if candidate['status']!='accepted': raise IntelligenceConflict('relation candidate requires an accepted human review')
        if not publisher or not reason: raise IntelligenceValidationError('publishedBy and reason are required')
        if not candidate['targetEntityId']: raise IntelligenceValidationError('relation target must match an ontology entity before publication')
        evidence_ids=[]
        with self.store._lock,self.store._connect() as con:
            for ref in candidate['evidence']:
                row=con.execute('SELECT evidence_id FROM ontology_evidence_link WHERE content_hash=? AND quote=? ORDER BY created_at DESC LIMIT 1',(ref.get('contentHash'),ref.get('quote'))).fetchone()
                if row: evidence_ids.append(row[0])
            if not evidence_ids: raise IntelligenceValidationError('relation publication requires matching source evidence')
            relation_id=_id('relation',candidate['entityId'],candidate['targetEntityId'],candidate['predicate'],candidate['contentHash'])
            con.execute("INSERT OR IGNORE INTO ontology_relation_assertion(relation_id,from_entity_id,to_entity_id,predicate,qualifier,valid_time,evidence_ids,claim_kind,confidence,review_status,created_at) VALUES(?,?,?,?,?,?,?,?,?,'published',?)",(relation_id,candidate['entityId'],candidate['targetEntityId'],candidate['predicate'],_dump(candidate['qualifier']),None,_dump(evidence_ids),'observed',1.0,_now()))
            publication_id=_id('relation-publication',candidate_id,relation_id,publisher,reason)
            con.execute("INSERT OR IGNORE INTO ontology_relation_candidate_publication(publication_id,candidate_id,relation_id,published_by,reason,created_at) VALUES(?,?,?,?,?,?)",(publication_id,candidate_id,relation_id,publisher,reason,_now()))
            con.execute("UPDATE ontology_relation_candidate SET status='published',updated_at=? WHERE candidate_id=?",(_now(),candidate_id))
        return {'ok':True,'relationId':relation_id,'publicationId':publication_id,'candidateId':candidate_id,'publishedBy':publisher,'reason':reason}

    def list_attribute_candidates(self, entity_id: str | None = None) -> list[dict[str, Any]]:
        where = " WHERE entity_id=?" if entity_id else ""
        values = (entity_id,) if entity_id else ()
        with self.store._connect() as con:
            rows = con.execute("SELECT * FROM ontology_attribute_candidate" + where + " ORDER BY created_at DESC", values).fetchall()
        return [self._candidate(row) for row in rows]

    def get_attribute_candidate(self, candidate_id: str) -> dict[str, Any]:
        with self.store._connect() as con:
            row = con.execute("SELECT * FROM ontology_attribute_candidate WHERE candidate_id=?", (candidate_id,)).fetchone()
        if row is None:
            raise IntelligenceNotFound(f"attribute candidate not found: {candidate_id}")
        return self._candidate(row)

    @staticmethod
    def _same_source_uri(left: Any, right: Any) -> bool:
        if not left or not right:
            return False
        try:
            a = urlsplit(str(left)); b = urlsplit(str(right))
            return urlunsplit((a.scheme.casefold(), a.netloc.casefold(), a.path.rstrip("/") or "/", a.query, "")) == urlunsplit((b.scheme.casefold(), b.netloc.casefold(), b.path.rstrip("/") or "/", b.query, ""))
        except ValueError:
            return str(left).strip().rstrip("/") == str(right).strip().rstrip("/")

    def verify_candidate_evidence(self, candidate: dict[str, Any]) -> dict[str, Any]:
        """Run deterministic integrity checks against immutable collection snapshots.

        This establishes that the saved quote came from the saved source body. It
        cannot establish that a publisher's claim is true in the real world;
        source quality and independent corroboration remain separate gates.
        """
        refs = candidate.get("evidence") if isinstance(candidate, dict) else []
        refs = refs if isinstance(refs, list) else []
        checks = []
        domains = set()
        primary_source = False
        with self.store._connect() as con:
            for ref in refs:
                if not isinstance(ref, dict):
                    checks.append({"status": "failed", "code": "invalid-reference", "detail": "证据引用不是对象"})
                    continue
                verification_id = ref.get("sourceJobId") or candidate.get("sourceJobId")
                index = ref.get("sourceItemIndex")
                snapshot = None
                if ref.get("snapshotId") and verification_id:
                    snapshot = con.execute("SELECT item_json,content_hash FROM ontology_collected_source_snapshot WHERE snapshot_id=? AND verification_id=?", (ref.get("snapshotId"), verification_id)).fetchone()
                if snapshot is None and verification_id and index is not None:
                    snapshot = con.execute("SELECT item_json,content_hash FROM ontology_collected_source_snapshot WHERE verification_id=? AND item_index=? AND content_hash=?", (verification_id, int(index), str(ref.get("contentHash") or ""))).fetchone()
                if snapshot is None and verification_id and ref.get("contentHash"):
                    snapshot = con.execute("SELECT item_json,content_hash FROM ontology_collected_source_snapshot WHERE verification_id=? AND content_hash=? ORDER BY item_index LIMIT 1", (verification_id, str(ref.get("contentHash")))).fetchone()
                item = _json(snapshot[0], {}) if snapshot else {}
                body = str(item.get("body") or "")
                stored_hash = str(snapshot[1] if snapshot else "")
                body_hash = hashlib.sha256(body.encode("utf-8")).hexdigest() if body else ""
                item_uri = item.get("sourceUri") or item.get("itemUri")
                quote = str(ref.get("quote") or "")
                source_url = ref.get("sourceUri") or ref.get("sourceUrl")
                quote_exact = bool(quote and body and quote in body)
                hash_match = bool(ref.get("contentHash") and stored_hash and str(ref.get("contentHash")) == stored_hash and stored_hash == body_hash)
                uri_match = self._same_source_uri(source_url, item_uri)
                snapshot_found = snapshot is not None
                source_version = bool(ref.get("sourceVersion") or item.get("sourceVersion"))
                reliability = str(item.get("sourceReliability") or "").casefold()
                category = str(item.get("sourceCategory") or item.get("category") or "").casefold()
                is_primary = reliability == "primary" or category in {"defense-official", "manufacturer-disclosure", "procurement-budget", "test-evaluation"}
                if item_uri:
                    try:
                        domains.add(urlsplit(str(item_uri)).netloc.casefold())
                    except ValueError:
                        pass
                primary_source = primary_source or is_primary
                passed = snapshot_found and quote_exact and hash_match and uri_match and source_version
                checks.append({
                    "status": "verified" if passed else "failed",
                    "code": "snapshot-integrity" if passed else "snapshot-mismatch",
                    "detail": "快照、引文、内容哈希、来源 URL 和版本均一致" if passed else "快照、引文、哈希或来源 URL 未能全部匹配",
                    "quoteExact": quote_exact, "contentHashMatch": hash_match,
                    "sourceUrlMatch": uri_match, "snapshotFound": snapshot_found,
                    "sourceVersionPresent": source_version, "sourceReliability": reliability or "unknown",
                })
        verified_count = sum(1 for item in checks if item.get("status") == "verified")
        conflicts = candidate.get("conflictCount")
        if conflicts is None:
            conflicts = 0
        confidence = candidate.get("confidence")
        confidence_ok = isinstance(confidence, (int, float)) and not isinstance(confidence, bool) and confidence >= 0.9
        corroborated = len({domain for domain in domains if domain}) >= 2
        # Auto-accept is only for a review queue state. Publication remains an
        # explicit action and a human can still override this recommendation.
        eligible = bool(checks) and verified_count == len(checks) and (primary_source or corroborated) and confidence_ok and not conflicts
        reasons = []
        if not checks: reasons.append("没有证据引用")
        if verified_count != len(checks): reasons.append("来源快照或引文完整性未通过")
        if not (primary_source or corroborated): reasons.append("缺少高质量来源或独立来源交叉印证")
        if not confidence_ok: reasons.append("模型置信度低于 0.90")
        if conflicts: reasons.append("存在同属性冲突候选")
        return {
            "status": "eligible" if eligible else "review-required",
            "checks": checks,
            "verifiedCount": verified_count,
            "evidenceCount": len(checks),
            "independentSourceCount": len({domain for domain in domains if domain}),
            "primarySource": primary_source,
            "confidence": confidence,
            "conflictCount": conflicts,
            "autoAcceptEligible": eligible,
            "reasons": reasons,
        }

    def _with_evidence_verification(self, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        for candidate in candidates:
            candidate["evidenceVerification"] = self.verify_candidate_evidence(candidate)
        return candidates

    def list_candidate_reviews(self, entity_id: str) -> list[dict[str, Any]]:
        """Return immutable candidate review decisions for an entity.

        Candidate status is only the current state; the review tables are the
        decision log. Keep both the decision and the candidate evidence in the
        response so the UI can show what was accepted/rejected and why.
        """
        with self.store._connect() as con:
            attribute_rows = con.execute(
                "SELECT r.*, c.entity_id FROM ontology_attribute_candidate_review r "
                "JOIN ontology_attribute_candidate c ON c.candidate_id=r.candidate_id "
                "WHERE c.entity_id=? ORDER BY r.created_at DESC",
                (entity_id,),
            ).fetchall()
            relation_rows = con.execute(
                "SELECT r.*, c.entity_id FROM ontology_relation_candidate_review r "
                "JOIN ontology_relation_candidate c ON c.candidate_id=r.candidate_id "
                "WHERE c.entity_id=? ORDER BY r.created_at DESC",
                (entity_id,),
            ).fetchall()
        history = []
        for row in attribute_rows:
            candidate = self.get_attribute_candidate(row["candidate_id"])
            candidate["evidenceVerification"] = self.verify_candidate_evidence(candidate)
            history.append({
                "reviewId": row["review_id"], "candidateType": "attribute",
                "candidateId": row["candidate_id"], "action": row["action"],
                "status": candidate.get("status"), "reviewer": row["reviewer"],
                "reason": row["reason"], "editedValue": row["edited_value"],
                "createdAt": row["created_at"], "candidate": candidate,
            })
        for row in relation_rows:
            candidate = self.get_relation_candidate(row["candidate_id"])
            candidate["evidenceVerification"] = self.verify_candidate_evidence(candidate)
            history.append({
                "reviewId": row["review_id"], "candidateType": "relation",
                "candidateId": row["candidate_id"], "action": row["action"],
                "status": candidate.get("status"), "reviewer": row["reviewer"],
                "reason": row["reason"], "createdAt": row["created_at"],
                "candidate": candidate,
            })
        return sorted(history, key=lambda item: item.get("createdAt") or "", reverse=True)

    @staticmethod
    def _candidate(row) -> dict[str, Any]:
        item = dict(row)
        return {
            "candidateId": item["candidate_id"], "entityId": item["entity_id"], "attributeKey": item["attribute_key"],
            "currentValue": item["current_value"], "proposedValue": item["proposed_value"], "unit": item["unit"],
            "conditions": item["conditions"], "confidence": item["confidence"], "status": item["status"],
            "sourceJobId": item["source_job_id"], "evidence": _json(item["evidence_json"], []),
            'metadata':_json(item.get('metadata_json'),{}),
            "modelProvider": item["model_provider"], "model": item["model"], "trace": _json(item["trace_json"], []),
            "contentHash": item["content_hash"], "createdAt": item["created_at"], "updatedAt": item["updated_at"],
        }

    def review_attribute_candidate(self, candidate_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        action = str(payload.get("action") or "").strip()
        if action not in {"accept", "reject", "request-more-evidence", "edit"}:
            raise IntelligenceValidationError("action must be accept, reject, request-more-evidence or edit")
        reviewer = str(payload.get("reviewer") or "operator").strip()[:200] or "operator"
        reason = str(payload.get("reason") or "").strip()
        if not reason:
            raise IntelligenceValidationError("reason is required")
        edited_value = str(payload.get("editedValue") or "").strip() if payload.get("editedValue") is not None else None
        status = {"accept": "accepted", "reject": "rejected", "request-more-evidence": "needs-evidence", "edit": "edited"}[action]
        now = _now()
        review_id = f"attr-review-{secrets.token_hex(8)}"
        with self.store._lock, self.store._connect() as con:
            row = con.execute("SELECT candidate_id FROM ontology_attribute_candidate WHERE candidate_id=?", (candidate_id,)).fetchone()
            if row is None:
                raise IntelligenceNotFound(f"attribute candidate not found: {candidate_id}")
            if action == "edit" and edited_value:
                con.execute("UPDATE ontology_attribute_candidate SET status=?,proposed_value=?,updated_at=? WHERE candidate_id=?", (status, edited_value, now, candidate_id))
            else:
                con.execute("UPDATE ontology_attribute_candidate SET status=?,updated_at=? WHERE candidate_id=?", (status, now, candidate_id))
            con.execute("INSERT INTO ontology_attribute_candidate_review(review_id,candidate_id,action,reviewer,reason,edited_value,created_at) VALUES(?,?,?,?,?,?,?)", (review_id, candidate_id, action, reviewer, reason, edited_value, now))
        return {"ok": True, "candidate": self.get_attribute_candidate(candidate_id), "review": {"reviewId": review_id, "candidateId": candidate_id, "action": action, "reviewer": reviewer, "reason": reason, "createdAt": now}}

    def _entity_document(self, entity_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        with self.store._connect() as con:
            row = con.execute(
                "SELECT entity_id,canonical_key,entity_type,preferred_name,english_name,category,subcategory,lifecycle_status,"
                "origin_country,manufacturer,user_countries_json,combat_status,current_revision,review_status,updated_at "
                "FROM ontology_entity WHERE entity_id=? OR canonical_key=?",
                (entity_id, entity_id),
            ).fetchone()
            if row is None:
                raise IntelligenceNotFound(f"ontology entity not found: {entity_id}")
            revision = con.execute(
                "SELECT attributes_json FROM ontology_entity_revision WHERE entity_id=? AND revision=?",
                (row["entity_id"], row["current_revision"]),
            ).fetchone()
        entity = {
            "entityId": row["entity_id"], "canonicalKey": row["canonical_key"], "preferredName": row["preferred_name"],
            "entityType": row["entity_type"],
            "englishName": row["english_name"], "category": row["category"], "subcategory": row["subcategory"],
            "lifecycleStatus": row["lifecycle_status"], "originCountry": row["origin_country"],
            "manufacturer": row["manufacturer"], "userCountries": _json(row["user_countries_json"], []),
            "combatStatus": row["combat_status"], "currentRevision": row["current_revision"],
            "reviewStatus": row["review_status"], "updatedAt": row["updated_at"],
        }
        entity.update(ontology_taxonomy(
            row["entity_type"], row["category"], row["subcategory"],
            row["preferred_name"], row["english_name"], row["canonical_key"],
        ))
        return entity, _json(revision["attributes_json"], {}) if revision else {}

    @staticmethod
    def _recommended_object_actions(missing: list[str], jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        existing = {job["targetType"]: job for job in jobs}
        actions = [{"action": "plan", "label": "生成补证计划", "status": "ready"}]
        mapping = [
            ("combat", "搜索作战记录", {"combat-status", "combat-conflict-count", "combat-hours-or-production"}),
            ("performance", "核验性能参数", {"payload-capacity", "max-speed", "ceiling", "flight-range", "endurance"}),
            ("systems", "核验任务系统", {"sensor-payload", "data-link", "primary-weapons"}),
            ("collaboration", "核验自主协同", {"swarm-compatibility", "autonomy-mode", "human-oversight"}),
            ("imagery", "查找公开影像", set()),
        ]
        missing_set = set(missing)
        for action, label, keys in mapping:
            job = existing.get(action)
            priority = not keys or bool(missing_set & keys) or job is not None
            actions.append({"action": action, "label": label, "status": job["status"] if job else "not-planned", "recommended": priority, "jobId": job.get("verificationId") if job else None})
        return actions

    def object_action(self, entity_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        action = str(payload.get("action") or "plan").strip()
        entity, _ = self._entity_document(entity_id)
        trace = [_trace("object-read", detail=f"读取 {entity['preferredName']}")]
        if action == "plan":
            jobs = self.queue.plan(entity["entityId"])
            trace.append(_trace("verification-plan", detail=f"生成/复用 {len(jobs)} 个核验任务"))
            return {"ok": True, "entity": entity, "action": action, "jobs": jobs, "answer": f"已为 {entity['preferredName']} 生成 {len(jobs)} 个对象定向补证任务。", "trace": trace}
        jobs = self.queue.list(entity_id=entity["entityId"])
        if not jobs:
            jobs = self.queue.plan(entity["entityId"])
            trace.append(_trace("verification-plan", detail=f"先生成 {len(jobs)} 个核验任务"))
        job = next((item for item in jobs if item["targetType"] == action), None)
        if job is None:
            raise IntelligenceValidationError(f"unsupported object agent action: {action}")
        trace.append(_trace("verification-dispatch", detail=f"执行 {action} 补证任务", jobId=job["verificationId"]))
        result = self.queue.execute(job["verificationId"])
        retained = len((result.get("result") or {}).get("items") or [])
        trace.append(_trace("evidence-candidates", detail=f"保留 {retained} 条待核验证据", status="done" if retained else result.get("status", "done")))
        return {
            "ok": True,
            "entity": entity,
            "action": action,
            "job": result,
            "answer": f"{entity['preferredName']} 的 {action} 补证任务已完成，保留 {retained} 条待核验证据；结果不会自动写入可信本体。",
            "trace": trace,
        }


__all__ = ["OntologyAgent", "ontology_taxonomy"]
