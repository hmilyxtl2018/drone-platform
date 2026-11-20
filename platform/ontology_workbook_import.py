"""Import the equipment workbook into the reviewable ontology candidate layer.

The workbook is an input snapshot, never a published ontology.  Every source
cell becomes a reusable evidence record and every imported assertion remains
pending until Firecrawl/analyst review accepts it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import unicodedata
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from openpyxl import load_workbook
    from openpyxl.utils import get_column_letter
except ImportError:  # pragma: no cover - desktop system Python fallback
    bundled_site = Path.home() / ".cache" / "codex-runtimes" / "codex-primary-runtime" / "dependencies" / "python" / "Lib" / "site-packages"
    if bundled_site.is_dir():
        sys.path.insert(0, str(bundled_site))
    try:
        from openpyxl import load_workbook
        from openpyxl.utils import get_column_letter
    except ImportError as exc:
        raise RuntimeError("openpyxl is required to import an xlsx workbook") from exc

try:
    from attributes_jsonb import canonical_attributes_hash, validate_attributes_jsonb
    from intelligence_store import IntelligenceStore
except ModuleNotFoundError:  # pragma: no cover
    # The bundled workbook runtime includes openpyxl but may not include the
    # optional jsonschema package. Keep the importer runnable there and retain
    # the same canonical hash plus the invariant checks needed before insert.
    try:
        from .intelligence_store import IntelligenceStore
    except ImportError:  # direct ``python platform/ontology_workbook_import.py``
        from intelligence_store import IntelligenceStore

    def canonical_attributes_hash(document: dict[str, Any]) -> str:
        copy = json.loads(json.dumps(document, ensure_ascii=False))
        copy.get("provenance", {}).pop("contentHash", None)
        payload = json.dumps(copy, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def validate_attributes_jsonb(document: dict[str, Any], *, attribute_registry: set[str] | None = None) -> list[str]:
        errors: list[str] = []
        if document.get("schemaVersion") != SCHEMA_VERSION or document.get("objectType") != "EquipmentModel":
            errors.append("invalid equipment attributes envelope")
        fields = document.get("attributes", {})
        completeness = document.get("completeness", {})
        expected = set(completeness.get("expectedKeys", []))
        if attribute_registry and expected - attribute_registry:
            errors.append(f"unregistered expected attributes: {sorted(expected - attribute_registry)}")
        present = set(completeness.get("presentKeys", []))
        unknown = set(completeness.get("unknownKeys", []))
        if present & unknown or present | unknown != expected:
            errors.append("attribute completeness sets do not partition expected keys")
        expected_ratio = len(present) / len(expected) if expected else 1.0
        if abs(float(completeness.get("coverageRatio", -1)) - expected_ratio) > 1e-9:
            errors.append("coverageRatio mismatch")
        for key, bucket in fields.items():
            if bucket.get("state") in {"reported", "verified", "inferred"}:
                for value in bucket.get("values", []):
                    if not value.get("evidenceRefs"):
                        errors.append(f"{key}: evidenceRefs is empty")
        return errors


ROOT = Path(__file__).resolve().parent
REGISTRY_PATH = ROOT.parent / "ontology" / "coast-raid-k" / "equipment-attribute-registry.json"
SCHEMA_VERSION = "equipment-attributes.v1"
PARSER_VERSION = "equipment-workbook-import.v2-qualified-values"
SOURCE_PARSER_VERSION = "equipment-workbook-sources.v2"
MISSING = {"", "—", "-", "n/a", "na", "n.a.", "n/a (公开来源不足)", "未公开", "不详"}

FIELD_MAP = {
    "类别": "category", "子类别": "subcategory", "首飞年": "first-flight-year",
    "服役年": "service-entry-year", "状态": "lifecycle-status", "机长 m": "length",
    "翼展 m": "wingspan", "最大起飞重量 kg": "max-takeoff-weight", "有效载荷 kg": "payload-capacity",
    "最大速度 km/h": "max-speed", "升限 m": "ceiling", "最大航程 km": "flight-range",
    "续航 h": "endurance", "动力类型": "propulsion-type", "主要武器": "primary-weapons",
    "隐身特性": "stealth-feature", "蜂群兼容": "swarm-compatibility", "实战记录": "combat-status",
    "参考单价 (百万USD)": "reference-unit-cost", "技术亮点": "technical-highlights",
    "实战冲突数": "combat-conflict-count", "综合评分": "composite-score",
    "传感器载荷(具体型号)": "sensor-payload", "数据链类型": "data-link", "RCS等级": "rcs",
    "实战累计小时/生产数": "combat-hours-or-production",
}

CONTEXT_FIELDS = {"原产国", "制造商", "主要使用国"}


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def stable_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:20]
    return f"{prefix}-{digest}"


def text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def is_missing(value: Any) -> bool:
    return text(value).casefold() in MISSING


def normalized_bool(value: Any) -> bool | None:
    value = text(value).casefold()
    if value in {"是", "yes", "true", "1"} or value.startswith("是("):
        return True
    if value in {"否", "no", "false", "0"}:
        return False
    return None


def number(value: Any) -> int | float | None:
    if is_missing(value):
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return value
    match = re.search(r"[-+]?\d+(?:\.\d+)?", text(value).replace(",", ""))
    if not match:
        return None
    parsed = float(match.group(0))
    return int(parsed) if parsed.is_integer() else parsed


def value_type(key: str, raw: Any, registry: dict[str, Any]) -> tuple[str, Any, str | None]:
    spec = registry.get("fields", {}).get(key, {})
    expected = spec.get("valueType")
    if expected == "boolean":
        parsed = normalized_bool(raw)
        return ("boolean", parsed, None) if parsed is not None else ("text", text(raw), None)
    if expected in {"number", "quantity"}:
        try:
            from capability_profile import normalize_measurement
        except ModuleNotFoundError:
            from .capability_profile import normalize_measurement
        normalized,unit,qualifier,lower,upper=normalize_measurement(text(raw),None,spec.get('unit'),spec)
        if qualifier=='range':return 'range',{'lower':lower,'upper':upper},unit
        if isinstance(normalized,(int,float)) and not isinstance(normalized,bool):return expected,normalized,unit
        parsed = number(raw)
        if parsed is not None and re.fullmatch(r'\s*[-+]?\d+(?:\.\d+)?\s*',text(raw)):
            return (expected, parsed, spec.get("unit"))
        return ("text", text(raw), None)
    return (expected or "text", text(raw), spec.get("unit"))


def workbook_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def split_aliases(value: Any) -> list[str]:
    raw = text(value)
    if is_missing(raw):
        return []
    return [part.strip() for part in re.split(r"\s*[↔/;,]\s*", raw) if part.strip()]


def canonical_key(model: str, english: str = "") -> str:
    source = model.strip() or english.strip()
    source = re.sub(r"[^a-zA-Z0-9]+", "-", source.casefold()).strip("-")
    return source or "unknown-equipment"


def normalized_name(value: Any) -> str:
    value = unicodedata.normalize("NFKC", text(value)).casefold().replace("ı", "i")
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", value)


def _entity_name_index(con: Any) -> tuple[dict[str, str], list[tuple[str, str]]]:
    index: dict[str, str] = {}
    names: list[tuple[str, str]] = []
    for row in con.execute("SELECT entity_id,preferred_name,english_name,canonical_key FROM ontology_entity"):
        for candidate in (row[1], row[2], row[3]):
            key = normalized_name(candidate)
            if key:
                index.setdefault(key, row[0])
                names.append((key, row[0]))
    for row in con.execute("SELECT entity_id,alias FROM ontology_entity_alias"):
        key = normalized_name(row[1])
        if key:
            index.setdefault(key, row[0])
            names.append((key, row[0]))
    return index, names


def _match_entity(name: Any, index: dict[str, str], names: list[tuple[str, str]]) -> str | None:
    key = normalized_name(name)
    if not key:
        return None
    exact = index.get(key)
    if exact:
        return exact
    candidates = {entity_id for candidate, entity_id in names if len(key) >= 5 and (key in candidate or candidate in key)}
    return next(iter(candidates)) if len(candidates) == 1 else None


def _lineage_candidates(value: Any) -> list[str]:
    raw = text(value)
    raw = re.sub(r"\s*→\s*", "→", raw)
    candidates: list[str] = []
    for part in raw.split("→"):
        cleaned = re.sub(r"\s*\([^)]*\)", "", part).strip(" ·")
        if not cleaned:
            continue
        candidates.append(cleaned)
    return candidates


def _match_lineage_name(value: str, index: dict[str, str], names: list[tuple[str, str]]) -> tuple[str | None, str]:
    options = [value, *[part.strip() for part in re.split(r"\s*/\s*", value) if part.strip()]]
    for option in options:
        entity_id = _match_entity(option, index, names)
        if entity_id:
            return entity_id, option
    return None, value


def _source_reliability(source_name: str, citation: str) -> str:
    combined = f"{source_name} {citation}".casefold()
    if any(token in combined for token in ("官网", "官方", "国防部", "usaf", "irgc")):
        return "primary"
    if any(token in combined for token in ("army recognition", "defensenews", "aviation week", "oryx", "rusi", "crs")):
        return "secondary"
    return "unknown"


def import_workbook_sources(workbook: str | Path, database: str | Path | None = None, *, snapshot_id: str | None = None) -> dict[str, Any]:
    """Backfill cited sources without changing immutable attribute revisions."""
    path = Path(workbook).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(path)
    file_hash = workbook_hash(path)
    store = IntelligenceStore(database)
    created_at = now()
    with store._lock, store._connect() as con:
        snapshot = con.execute("SELECT snapshot_id FROM ontology_import_snapshot WHERE file_hash=?", (file_hash,)).fetchone()
        if snapshot is None:
            raise ValueError("workbook must be imported before its cited sources")
        snapshot_id = snapshot_id or snapshot[0]
        source_import_id = stable_id("source-import", snapshot_id, SOURCE_PARSER_VERSION)
        existing = con.execute("SELECT source_count,matched_count,unmatched_count,status FROM ontology_source_import_run WHERE source_import_id=?", (source_import_id,)).fetchone()
        if existing and existing[3] == "completed":
            return {"status": "duplicate", "sourceImportId": source_import_id, "sourceCount": existing[0], "matchedCount": existing[1], "unmatchedCount": existing[2]}
        con.execute(
            "INSERT OR REPLACE INTO ontology_source_import_run(source_import_id,snapshot_id,parser_version,source_count,matched_count,unmatched_count,status,error_message,created_at,completed_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (source_import_id, snapshot_id, SOURCE_PARSER_VERSION, 0, 0, 0, "running", None, created_at, None),
        )

    workbook_obj = load_workbook(path, read_only=False, data_only=False)
    records: list[dict[str, Any]] = []

    def add(scope: str, subject: Any, source_name: Any, url: Any, citation: Any, sheet: str, row: int, cell_range: str, details: dict[str, Any]) -> None:
        citation_value = text(citation)
        url_value = text(url)
        if not citation_value and not url_value:
            return
        records.append({"scope": scope, "subject": text(subject), "sourceName": text(source_name) or "工作簿引用", "url": url_value or None, "citation": citation_value or url_value, "sheet": sheet, "row": row, "cellRange": cell_range, "details": {key: value for key, value in details.items() if not is_missing(value)}})

    if "主表_全部机型" in workbook_obj.sheetnames:
        sheet = workbook_obj["主表_全部机型"]
        headers = {text(cell.value): cell.column for cell in sheet[3] if cell.value is not None}
        for row_number in range(4, sheet.max_row + 1):
            model = sheet.cell(row_number, headers.get("型号", 4)).value
            qid_cell = sheet.cell(row_number, headers.get("Wikidata Q ID", 29))
            qid = text(qid_cell.value)
            url = qid_cell.hyperlink.target if qid_cell.hyperlink else (f"https://www.wikidata.org/wiki/{qid}" if qid else "")
            if qid:
                add("identity", model, "Wikidata", url, qid, sheet.title, row_number, qid_cell.coordinate, {"referenceType": "model-identity"})

    if "实战记录明细" in workbook_obj.sheetnames:
        sheet = workbook_obj["实战记录明细"]
        headers = {text(cell.value): cell.column for cell in sheet[3] if cell.value is not None}
        for row_number in range(4, sheet.max_row + 1):
            values = {key: sheet.cell(row_number, column).value for key, column in headers.items()}
            add("combat-event", values.get("型号"), "实战记录参考来源", None, values.get("参考来源"), sheet.title, row_number, f"A{row_number}:H{row_number}", {"conflict": values.get("冲突/战区"), "time": values.get("时间"), "mission": values.get("作战任务"), "result": values.get("典型战果 / 关键事件"), "loss": values.get("损失情况"), "declaredCredibility": values.get("来源可信度")})

    if "战损数据" in workbook_obj.sheetnames:
        sheet = workbook_obj["战损数据"]
        headers = {text(cell.value): cell.column for cell in sheet[1] if cell.value is not None}
        for row_number in range(2, sheet.max_row + 1):
            values = {key: sheet.cell(row_number, column).value for key, column in headers.items()}
            add("loss-record", values.get("机型"), "战损数据参考来源", None, values.get("来源"), sheet.title, row_number, f"A{row_number}:E{row_number}", {"lossOrSorties": values.get("累计损失/出击数"), "defeatMechanism": values.get("被何种防空系统击落/未遇型"), "theater": values.get("主要战区")})

    if "图库索引" in workbook_obj.sheetnames:
        sheet = workbook_obj["图库索引"]
        headers = {text(cell.value): cell.column for cell in sheet[3] if cell.value is not None}
        for row_number in range(4, sheet.max_row + 1):
            values = {key: sheet.cell(row_number, column).value for key, column in headers.items()}
            url_cell = sheet.cell(row_number, headers.get("来源链接", 5))
            url = url_cell.hyperlink.target if url_cell.hyperlink else values.get("来源链接")
            add("image", values.get("名称"), values.get("图像来源"), url, values.get("备注") or url, sheet.title, row_number, f"B{row_number}:F{row_number}", {"mediaType": values.get("类别"), "note": values.get("备注")})

    if "数据说明" in workbook_obj.sheetnames:
        sheet = workbook_obj["数据说明"]
        for row_number in range(3, min(sheet.max_row, 5) + 1):
            cell = sheet.cell(row_number, 2)
            if not is_missing(cell.value):
                add("workbook-general", None, "工作簿数据来源声明", None, cell.value, sheet.title, row_number, cell.coordinate, {"referenceType": "workbook-level"})

    lineage_rows: list[dict[str, Any]] = []
    if "改型血缘" in workbook_obj.sheetnames:
        sheet = workbook_obj["改型血缘"]
        for row_number in range(2, sheet.max_row + 1):
            family = text(sheet.cell(row_number, 1).value)
            chain = text(sheet.cell(row_number, 2).value)
            if family and chain:
                lineage_rows.append({"family": family, "chain": chain, "row": row_number})
    workbook_obj.close()

    matched = 0
    unmatched = 0
    with store._lock, store._connect() as con:
        index, names = _entity_name_index(con)
        for record in records:
            entity_id = _match_entity(record["subject"], index, names) if record["subject"] else None
            if record["scope"] == "workbook-general":
                match_status = "workbook-level"
            elif entity_id:
                match_status = "matched"
                matched += 1
            else:
                match_status = "identity-review"
                unmatched += 1
            source_record = con.execute("SELECT raw_record_id FROM ontology_raw_record WHERE snapshot_id=? AND canonical_model_id=? ORDER BY row_number LIMIT 1", (snapshot_id, entity_id)).fetchone() if entity_id else None
            reference_id = stable_id("source", snapshot_id, record["sheet"], str(record["row"]), record["scope"], record["citation"], record["url"] or "")
            con.execute(
                "INSERT OR IGNORE INTO ontology_source_reference(source_reference_id,snapshot_id,entity_id,source_record_id,source_scope,subject_name,source_name,source_url,citation_text,sheet_name,row_number,cell_range,details_json,source_reliability,verification_status,review_status,match_status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (reference_id, snapshot_id, entity_id, source_record[0] if source_record else None, record["scope"], record["subject"] or None, record["sourceName"], record["url"], record["citation"], record["sheet"], record["row"], record["cellRange"], json.dumps(record["details"], ensure_ascii=False, default=str), _source_reliability(record["sourceName"], record["citation"]), "unverified", "pending", match_status, created_at),
            )
        lineage_edges = 0
        for lineage in lineage_rows:
            chain_items = _lineage_candidates(lineage["chain"])
            evidence_id = stable_id("evidence", snapshot_id, "改型血缘", str(lineage["row"]))
            quote = f"{lineage['family']}：{lineage['chain']}"
            con.execute(
                "INSERT OR IGNORE INTO ontology_evidence_link(evidence_id,source_type,source_ref,source_version,quote,content_hash,locator_json,source_reliability,verification_status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (evidence_id, "workbook", path.name, f"sha256:{file_hash}", quote, hashlib.sha256(quote.encode("utf-8")).hexdigest(), json.dumps({"sheetName": "改型血缘", "rowNumber": lineage["row"], "cellRange": f"A{lineage['row']}:B{lineage['row']}"}, ensure_ascii=False), "unknown", "unverified", created_at),
            )
            for sequence, (predecessor_raw, successor_raw) in enumerate(zip(chain_items, chain_items[1:]), start=1):
                predecessor_id, predecessor_name = _match_lineage_name(predecessor_raw, index, names)
                successor_id, successor_name = _match_lineage_name(successor_raw, index, names)
                if predecessor_id is None or successor_id is None or predecessor_id == successor_id:
                    continue
                relation_id = stable_id("relation", snapshot_id, lineage["family"], str(sequence), predecessor_id, successor_id)
                qualifier = {"family": lineage["family"], "sequence": sequence, "predecessorRaw": predecessor_raw, "successorRaw": successor_raw, "relationType": "model-family-lineage"}
                con.execute(
                    "INSERT OR IGNORE INTO ontology_relation_assertion(relation_id,from_entity_id,to_entity_id,predicate,qualifier,evidence_ids,claim_kind,confidence,review_status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (relation_id, successor_id, predecessor_id, "derived-from", json.dumps(qualifier, ensure_ascii=False), json.dumps([evidence_id]), "workbook-lineage", 0.7, "pending", created_at),
                )
                edge_id = stable_id("lineage", snapshot_id, lineage["family"], str(sequence), predecessor_raw, successor_raw)
                con.execute(
                    "INSERT OR IGNORE INTO ontology_lineage_edge(lineage_edge_id,snapshot_id,evidence_id,family_name,sequence_no,predecessor_entity_id,successor_entity_id,predecessor_name,successor_name,raw_chain,qualifier_json,match_status,review_status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (edge_id, snapshot_id, evidence_id, lineage["family"], sequence, predecessor_id, successor_id, predecessor_name, successor_name, lineage["chain"], json.dumps(qualifier, ensure_ascii=False), "matched", "pending", created_at),
                )
                lineage_edges += 1
        completed = now()
        con.execute("UPDATE ontology_source_import_run SET source_count=?,matched_count=?,unmatched_count=?,status='completed',completed_at=? WHERE source_import_id=?", (len(records), matched, unmatched, completed, source_import_id))
    return {"status": "completed", "sourceImportId": source_import_id, "sourceCount": len(records), "matchedCount": matched, "unmatchedCount": unmatched, "lineageEdgeCount": lineage_edges}


def make_evidence(*, evidence_id: str, source_name: str, file_hash: str, quote: str, sheet: str, row: int, cell: str) -> dict[str, Any]:
    return {
        "evidenceId": evidence_id, "sourceType": "workbook", "sourceRef": source_name,
        "sourceVersion": f"sha256:{file_hash}", "quote": quote,
        "contentHash": hashlib.sha256(quote.encode("utf-8")).hexdigest(),
        "locator": {"sheetName": sheet, "rowNumber": row, "cellRange": cell},
        "sourceReliability": "unknown",
    }


def import_workbook(workbook: str | Path, database: str | Path | None = None) -> dict[str, Any]:
    path = Path(workbook).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(path)
    file_hash = workbook_hash(path)
    store = IntelligenceStore(database)
    source_name = path.name
    with store._connect() as con:
        existing = con.execute("SELECT snapshot_id, status FROM ontology_import_snapshot WHERE file_hash=?", (file_hash,)).fetchone()
        if existing:
            source_result = import_workbook_sources(path, database, snapshot_id=existing[0])
            return {"status": "duplicate", "snapshotId": existing[0], "fileHash": file_hash, "rowCount": 0, "sourceImport": source_result}

    registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    workbook_obj = load_workbook(path, read_only=True, data_only=True)
    sheet_names = list(workbook_obj.sheetnames)
    if "主表_全部机型" not in workbook_obj.sheetnames:
        raise ValueError("workbook is missing 主表_全部机型")
    worksheet = workbook_obj["主表_全部机型"]
    rows = list(worksheet.iter_rows(values_only=True))
    if len(rows) < 4:
        raise ValueError("主表_全部机型 does not contain a header and data row")
    headers = [text(value) for value in rows[2]]
    snapshot_id = stable_id("import", file_hash)
    imported_at = now()
    created_records = 0
    created_entities = 0
    created_assertions = 0

    with store._lock, store._connect() as con:
        con.execute(
            "INSERT INTO ontology_import_snapshot(snapshot_id,file_name,file_hash,source_version,workbook_schema_version,parser_version,sheet_names,row_count,status,imported_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (snapshot_id, source_name, file_hash, f"sha256:{file_hash}", "workbook.v4", PARSER_VERSION, json.dumps(sheet_names, ensure_ascii=False), 0, "running", imported_at),
        )
        for row_number, row_values in enumerate(rows[3:], start=4):
            row = {headers[i]: row_values[i] if i < len(row_values) else None for i in range(len(headers)) if headers[i]}
            model = text(row.get("型号"))
            english = text(row.get("英文名"))
            if not model and not english:
                continue
            canonical = canonical_key(model, english)
            entity_id = stable_id("equipment", canonical)
            raw_record_id = stable_id("raw", snapshot_id, str(row_number))
            raw_payload = {key: value for key, value in row.items() if not is_missing(value)}
            raw_hash = hashlib.sha256(json.dumps(raw_payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")).hexdigest()
            con.execute(
                "INSERT INTO ontology_raw_record(raw_record_id,snapshot_id,sheet_name,row_number,cell_range,raw_payload,raw_row_hash,canonical_model_id,normalization_status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (raw_record_id, snapshot_id, "主表_全部机型", row_number, f"A{row_number}:{get_column_letter(len(headers))}{row_number}", json.dumps(raw_payload, ensure_ascii=False, default=str), raw_hash, entity_id, "normalized", imported_at),
            )
            context = {
                "origin_country": None if is_missing(row.get("原产国")) else text(row.get("原产国")),
                "manufacturer": None if is_missing(row.get("制造商")) else text(row.get("制造商")),
                "user_countries_json": json.dumps(split_aliases(row.get("主要使用国")), ensure_ascii=False),
                "combat_status": "yes" if normalized_bool(row.get("实战记录")) is True else "no" if normalized_bool(row.get("实战记录")) is False else "unknown",
            }
            entity = con.execute("SELECT entity_id,current_revision,source_snapshot_ids FROM ontology_entity WHERE canonical_key=?", (canonical,)).fetchone()
            prior_revision = int(entity[1]) if entity else 0
            prior_snapshots = json.loads(entity[2]) if entity and entity[2] else []
            if entity is None:
                con.execute(
                    "INSERT INTO ontology_entity(entity_id,canonical_iri,canonical_key,entity_type,preferred_name,english_name,category,subcategory,lifecycle_status,wikidata_id,current_revision,review_status,source_snapshot_ids,origin_country,manufacturer,user_countries_json,combat_status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (entity_id, f"https://local.ontology/coast-raid-k#equipment-{canonical}", canonical, "EquipmentModel", model or english, english or None, text(row.get("类别")) or None, text(row.get("子类别")) or None, text(row.get("状态")) or None, None if is_missing(row.get("Wikidata Q ID")) else text(row.get("Wikidata Q ID")), 0, "pending", json.dumps([snapshot_id]), context["origin_country"], context["manufacturer"], context["user_countries_json"], context["combat_status"], imported_at, imported_at),
                )
                created_entities += 1
            else:
                entity_id = entity[0]
                # Keep the first non-empty identity context while retaining
                # the source trail across workbook refreshes.
                con.execute(
                    "UPDATE ontology_entity SET origin_country=COALESCE(origin_country,?), manufacturer=COALESCE(manufacturer,?), user_countries_json=CASE WHEN user_countries_json='[]' THEN ? ELSE user_countries_json END, combat_status=CASE WHEN combat_status='unknown' THEN ? ELSE combat_status END, updated_at=? WHERE entity_id=?",
                    (context["origin_country"], context["manufacturer"], context["user_countries_json"], context["combat_status"], imported_at, entity_id),
                )
            for alias in sorted(set([english, *split_aliases(row.get("型号"))])):
                if alias and alias.casefold() != model.casefold():
                    con.execute(
                        "INSERT OR IGNORE INTO ontology_entity_alias(alias_id,entity_id,alias,alias_type,language,source_snapshot_id,source_record_id,confidence,review_status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (stable_id("alias", entity_id, alias.casefold()), entity_id, alias, "workbook-name", "en" if re.search(r"[A-Za-z]", alias) else "und", snapshot_id, raw_record_id, 0.8, "pending", imported_at),
                    )

            attributes: dict[str, dict[str, Any]] = {}
            present: list[str] = []
            unknown: list[str] = []
            evidence_by_key: dict[str, dict[str, Any]] = {}
            for header, key in FIELD_MAP.items():
                raw = row.get(header)
                if is_missing(raw):
                    attributes[key] = {"state": "unknown", "values": []}
                    unknown.append(key)
                    continue
                cell_index = headers.index(header) + 1
                cell = f"{get_column_letter(cell_index)}{row_number}"
                evidence_id = stable_id("evidence", snapshot_id, str(row_number), cell, key)
                evidence = make_evidence(evidence_id=evidence_id, source_name=source_name, file_hash=file_hash, quote=text(raw), sheet="主表_全部机型", row=row_number, cell=cell)
                con.execute(
                    "INSERT OR IGNORE INTO ontology_evidence_link(evidence_id,source_type,source_ref,source_version,quote,content_hash,locator_json,source_reliability,verification_status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (evidence_id, "workbook", source_name, f"sha256:{file_hash}", evidence["quote"], evidence["contentHash"], json.dumps(evidence["locator"], ensure_ascii=False), "unknown", "unverified", imported_at),
                )
                kind, parsed, unit = value_type(key, raw, registry)
                qualifier='exact'
                if kind in {'quantity','number','range'}:
                    try:
                        from capability_profile import normalize_measurement
                    except ModuleNotFoundError:
                        from .capability_profile import normalize_measurement
                    _,_,q,_,_=normalize_measurement(text(raw),None,unit,registry['fields'].get(key,{}))
                    qualifier={'approximate':'approximately','range':'unknown','unspecified':'unknown'}.get(q,q)
                claim_kind = "imported"
                conditions = None  # no inferred theater or operating condition
                value = {"assertionId": stable_id("assertion", entity_id, key, evidence_id), "rawValue": text(raw), "valueType": kind, "value": parsed, "normalizedValue": parsed, "unit": unit, "qualifier": qualifier, "conditions": conditions, "validTime": None, "observedAt": None, "claimKind": claim_kind, "basis": "workbook", "evidenceRefs": [evidence], "confidence": 0.8, "reviewStatus": "pending", "truthVerified": False}
                attributes[key] = {"state": "reported", "values": [value]}
                present.append(key)
                evidence_by_key[key] = evidence
                con.execute(
                    "INSERT INTO ontology_assertion(assertion_id,entity_id,attribute_key,raw_value,normalized_value,value_type,unit,qualifier,conditions,claim_kind,basis,evidence_ids,confidence,review_status,truth_verified,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (value["assertionId"], entity_id, key, value["rawValue"], json.dumps(parsed, ensure_ascii=False), kind, unit, qualifier, conditions, claim_kind, "workbook", json.dumps([evidence_id]), 0.8, "pending", 0, imported_at),
                )
                created_assertions += 1
            expected = list(registry["templates"]["EquipmentModel"]["identity"] + registry["templates"]["EquipmentModel"]["dimensions"] + registry["templates"]["EquipmentModel"]["performance"] + registry["templates"]["EquipmentModel"]["propulsion"] + registry["templates"]["EquipmentModel"]["mission"] + registry["templates"]["EquipmentModel"]["c4isr"] + registry["templates"]["EquipmentModel"]["survivability"] + registry["templates"]["EquipmentModel"]["autonomy"] + registry["templates"]["EquipmentModel"]["manufacturing"] + registry["templates"]["EquipmentModel"]["research"] + ["combat-status", "combat-conflict-count", "combat-hours-or-production"])
            expected = list(dict.fromkeys(expected))
            present = sorted(set(present)); unknown = sorted(set(expected) - set(present))
            attributes = {key: attributes.get(key, {"state": "unknown", "values": []}) for key in expected}
            created = imported_at
            snapshot = {"schemaVersion": SCHEMA_VERSION, "objectId": entity_id, "objectType": "EquipmentModel", "objectRevision": 1, "attributes": attributes, "completeness": {"mode": "template", "templateId": "EquipmentModel", "expectedKeys": expected, "presentKeys": present, "unknownKeys": unknown, "notApplicableKeys": [], "conflictKeys": [], "missingRequiredKeys": [], "coverageRatio": len(present) / len(expected) if expected else 1.0, "status": "valid" if not unknown else "incomplete", "checkedAt": created, "validatorVersion": "attributes-jsonb.v1"}, "provenance": {"sourceSnapshotIds": [snapshot_id], "sourceRecordIds": [raw_record_id], "generatedBy": PARSER_VERSION, "createdAt": created, "updatedAt": created, "contentHash": "0" * 64}}
            snapshot["provenance"]["contentHash"] = canonical_attributes_hash(snapshot)
            errors = validate_attributes_jsonb(snapshot, attribute_registry=set(registry["fields"]))
            if errors:
                raise ValueError(f"{model}: invalid attributes JSONB: {'; '.join(errors)}")
            revision = prior_revision + 1
            revision_id = stable_id("entity-revision", entity_id, snapshot["provenance"]["contentHash"], str(revision))
            con.execute("INSERT OR IGNORE INTO ontology_entity_revision(entity_revision_id,entity_id,revision,attributes_json,attributes_schema_version,attributes_sha256,source_snapshot_id,source_record_id,review_status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)", (revision_id, entity_id, revision, json.dumps(snapshot, ensure_ascii=False, sort_keys=True), SCHEMA_VERSION, snapshot["provenance"]["contentHash"], snapshot_id, raw_record_id, "pending", created))
            all_snapshots = list(dict.fromkeys([*prior_snapshots, snapshot_id]))
            con.execute("UPDATE ontology_entity SET current_revision=?, source_snapshot_ids=json(?), updated_at=? WHERE entity_id=?", (revision, json.dumps(all_snapshots), created, entity_id))
            created_records += 1
        con.execute("UPDATE ontology_import_snapshot SET row_count=?,status='completed',completed_at=? WHERE snapshot_id=?", (created_records, now(), snapshot_id))
    workbook_obj.close()
    source_result = import_workbook_sources(path, database, snapshot_id=snapshot_id)
    return {"status": "completed", "snapshotId": snapshot_id, "fileHash": file_hash, "rowCount": created_records, "entityCount": created_entities, "assertionCount": created_assertions, "sourceImport": source_result}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workbook", required=True)
    parser.add_argument("--database")
    parser.add_argument("--sources-only", action="store_true")
    args = parser.parse_args(argv)
    result = import_workbook_sources(args.workbook, args.database) if args.sources_only else import_workbook(args.workbook, args.database)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
