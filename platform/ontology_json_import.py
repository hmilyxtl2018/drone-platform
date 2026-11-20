"""Import an agent-result UAV ontology JSON into the reviewable candidate DB.

The JSON is treated as an input snapshot.  It never publishes trusted facts:
entities, attribute assertions and relations are inserted with ``pending``
review status and every mapped value keeps a source URL plus the original raw
JSON record.  Re-importing the same file is idempotent by SHA-256.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from attributes_jsonb import canonical_attributes_hash, validate_attributes_jsonb
    from intelligence_store import IntelligenceStore
except ModuleNotFoundError:  # pragma: no cover - module launch fallback
    from .attributes_jsonb import canonical_attributes_hash, validate_attributes_jsonb
    from .intelligence_store import IntelligenceStore


ROOT = Path(__file__).resolve().parent
REGISTRY_PATH = ROOT.parent / "ontology" / "coast-raid-k" / "equipment-attribute-registry.json"
SCHEMA_VERSION = "equipment-attributes.v1"
PARSER_VERSION = "uav-agent-json-import.v1"


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def stable_id(prefix: str, *parts: str) -> str:
    return f"{prefix}-{hashlib.sha256("|".join(str(p) for p in parts).encode('utf-8')).hexdigest()[:20]}"


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value).strip()


def canonical_key(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9\u4e00-\u9fff]+", "-", value.casefold()).strip("-")
    return slug or f"unknown-{hashlib.sha256(value.encode('utf-8')).hexdigest()[:12]}"


def norm_name(value: Any) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", text(value).casefold())


def quote_value(value: Any) -> str:
    raw = text(value)
    return raw[:4000] or "unknown"


def citation_for(record: dict[str, Any], key: str) -> str:
    return text(record.get(f"{key}_citation")) or text(record.get("name_zh_citation")) or text(record.get("name_citation"))


def reliability(url: str) -> str:
    host = re.sub(r"^www\.", "", url.split("/")[2].casefold()) if url.startswith("http") and "/" in url else ""
    official = (".mil" in host or ".gov" in host or host.endswith("army.mod.uk") or
                any(name in host for name in ("baykartech.com", "elbitsystems.com", "insitu.com", "avinc.com", "ga-asi.com", "safran-group.com", "rheinmetall.com")))
    return "primary" if official else "secondary" if host else "unknown"


def numeric_value(raw: str, expected_unit: str | None) -> tuple[Any, str | None, str]:
    """Parse one source number conservatively while retaining the raw value."""
    if expected_unit is None:
        return raw, None, "text"
    match = re.search(r"[-+]?(?:\d+(?:\.\d+)?|\.\d+)", raw.replace(",", ""))
    if not match:
        return raw, None, "text"
    number = float(match.group(0))
    unit_match = re.search(r"(?:\d[\d,.]*\s*)(km/h|mph|ktas|kts|kt|nmi|mi|km|m|ft|lb|lbs|kg|h)", raw.casefold())
    source_unit = unit_match.group(1) if unit_match else expected_unit
    conversions = {
        ("ft", "m"): 0.3048, ("lb", "kg"): 0.45359237, ("lbs", "kg"): 0.45359237,
        ("km", "m"): 1000.0, ("mi", "km"): 1.609344, ("nmi", "km"): 1.852,
        ("kt", "km/h"): 1.852, ("kts", "km/h"): 1.852, ("ktas", "km/h"): 1.852,
        ("mph", "km/h"): 1.609344,
    }
    factor = conversions.get((source_unit, expected_unit), 1.0)
    if source_unit != expected_unit and (source_unit, expected_unit) not in conversions:
        return raw, None, "text"
    converted = number * factor
    if converted.is_integer():
        converted = int(converted)
    return converted, expected_unit, "quantity" if expected_unit not in {"year", "conflicts"} else "number"


FIELD_MAP = {
    "category": "category",
    "wingspan": "wingspan",
    "length": "length",
    "payload_capacity": "payload-capacity",
    "max_payload": "payload-capacity",
    "gross_takeoff_weight": "max-takeoff-weight",
    "max_takeoff_weight": "max-takeoff-weight",
    "takeoff_weight": "max-takeoff-weight",
    "endurance": "endurance",
    "operational_altitude": "ceiling",
    "altitude": "ceiling",
    "ceiling": "ceiling",
    "maximum_altitude": "ceiling",
    "range": "flight-range",
    "operational_range": "flight-range",
    "range_from_ground_station": "flight-range",
    "max_speed": "max-speed",
    "cruise_speed": "max-speed",
    "telemetry_range": "data-link",
    "data_link_range": "data-link",
    "payloads": "sensor-payload",
    "sensors": "sensor-payload",
    "sensor_options": "sensor-payload",
    "payload_features": "technical-highlights",
    "missions": "technical-highlights",
    "autonomy_features": "autonomy-mode",
    "navigation_feature": "autonomy-mode",
    "system_features": "technical-highlights",
    "system_components": "technical-highlights",
    "system_composition": "technical-highlights",
    "launch_recovery": "technical-highlights",
    "launch_method": "technical-highlights",
    "environmental_features": "technical-highlights",
    "airframe": "technical-highlights",
    "coverage": "technical-highlights",
}


RELATION_PREDICATES = {
    "developed_by": "developedBy", "operated_by": "operatedBy", "in_service_with": "inServiceWith",
    "procured_under": "procuredUnder", "derived_from_or_program_basis": "derivedFromOrProgramBasis",
    "procured_by": "procuredBy", "received_by": "receivedBy", "formerly_operated_by": "formerlyOperatedBy",
}


def _evidence(con: Any, *, evidence_id: str, url: str, version: str, quote: str, locator: dict[str, Any], created: str) -> dict[str, Any]:
    digest = hashlib.sha256(quote.encode("utf-8")).hexdigest()
    ref = {"evidenceId": evidence_id, "sourceType": "web-page", "sourceRef": url or "agent-result.json", "sourceVersion": version, "quote": quote, "contentHash": digest, "locator": locator, "sourceReliability": reliability(url)}
    con.execute(
        "INSERT OR IGNORE INTO ontology_evidence_link(evidence_id,source_type,source_ref,source_version,quote,content_hash,locator_json,source_reliability,verification_status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (evidence_id, "web-page" if url else "source-document", ref["sourceRef"], version, quote, digest, json.dumps(locator, ensure_ascii=False), ref["sourceReliability"], "unverified", created),
    )
    return ref


def import_agent_result(source: str | Path, database: str | Path | None = None) -> dict[str, Any]:
    path = Path(source).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(path)
    digest = file_hash(path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("platforms"), list):
        raise ValueError("agent-result JSON must contain a platforms array")
    store = IntelligenceStore(database)
    with store._connect() as con:
        existing = con.execute("SELECT snapshot_id,status FROM ontology_import_snapshot WHERE file_hash=?", (digest,)).fetchone()
        if existing:
            counts = {"entities": con.execute("SELECT COUNT(*) FROM ontology_entity WHERE json_extract(source_snapshot_ids,'$[0]')=?", (existing[0],)).fetchone()[0]}
            return {"status": "duplicate", "snapshotId": existing[0], "fileHash": digest, **counts}

    registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    fields = registry["fields"]
    expected = list(dict.fromkeys(sum(registry["templates"]["EquipmentModel"].values(), []) + ["combat-status", "combat-conflict-count", "combat-hours-or-production"]))
    snapshot_id = stable_id("import", digest)
    created = now()
    entities: dict[str, str] = {}
    aliases: dict[str, str] = {}
    relation_rows: list[tuple[dict[str, Any], str, str]] = []
    created_entities = created_assertions = created_relations = created_manufacturing_candidates = 0
    with store._lock, store._connect() as con:
        con.execute("INSERT INTO ontology_import_snapshot(snapshot_id,file_name,file_hash,source_version,workbook_schema_version,parser_version,sheet_names,row_count,status,imported_at) VALUES(?,?,?,?,?,?,?,?,?,?)", (snapshot_id, path.name, digest, f"sha256:{digest}", "agent-result.v1", PARSER_VERSION, json.dumps(list(payload.keys()), ensure_ascii=False), len(payload["platforms"]), "running", created))

        def ensure_entity(name: str, entity_type: str, *, preferred: str | None = None, source_record_id: str | None = None) -> str:
            nonlocal created_entities
            slug = canonical_key(name)
            # Equipment keys intentionally match the workbook importer so a
            # JSON refresh enriches an existing candidate instead of creating
            # a second Bayraktar/MQ-9/etc. entity.  Non-equipment entities are
            # namespaced to avoid colliding with an equipment model.
            key = slug if entity_type == "EquipmentModel" else f"{entity_type.casefold()}-{slug}"
            existing = con.execute("SELECT entity_id FROM ontology_entity WHERE canonical_key=?", (key,)).fetchone()
            if existing:
                entities[name] = existing[0]
                aliases[norm_name(name)] = existing[0]
                con.execute("UPDATE ontology_entity SET source_snapshot_ids=json_insert(source_snapshot_ids,'$[#]',?),updated_at=? WHERE entity_id=? AND NOT EXISTS (SELECT 1 FROM json_each(source_snapshot_ids) WHERE value=?)", (snapshot_id, created, existing[0], snapshot_id))
                return existing[0]
            entity_id = stable_id("equipment" if entity_type == "EquipmentModel" else "entity", key)
            con.execute("INSERT INTO ontology_entity(entity_id,canonical_iri,canonical_key,entity_type,preferred_name,english_name,category,subcategory,lifecycle_status,current_revision,review_status,source_snapshot_ids,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (entity_id, f"https://local.ontology/coast-raid-k#json-{canonical_key(name)}", key, entity_type, preferred or name, name if preferred and preferred != name else None, None, None, None, 0, "pending", json.dumps([snapshot_id]), created, created))
            entities[name] = entity_id; aliases[norm_name(name)] = entity_id; created_entities += 1
            return entity_id

        for index, platform in enumerate(payload["platforms"], start=1):
            if not isinstance(platform, dict) or not text(platform.get("name")):
                continue
            name, name_zh = text(platform["name"]), text(platform.get("name_zh"))
            entity_id = ensure_entity(name, "EquipmentModel", preferred=name_zh or name)
            aliases[norm_name(name_zh)] = entity_id if name_zh else entity_id
            raw_id = stable_id("raw", snapshot_id, "platforms", str(index))
            raw_payload = json.dumps(platform, ensure_ascii=False, sort_keys=True)
            con.execute("INSERT INTO ontology_raw_record(raw_record_id,snapshot_id,sheet_name,row_number,cell_range,raw_payload,raw_row_hash,canonical_model_id,normalization_status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)", (raw_id, snapshot_id, "platforms", index, f"platforms[{index-1}]", raw_payload, hashlib.sha256(raw_payload.encode("utf-8")).hexdigest(), entity_id, "normalized", created))
            if entity_id.startswith("equipment-"):
                con.execute("UPDATE ontology_entity SET category=COALESCE(category,?),origin_country=COALESCE(origin_country,?),manufacturer=COALESCE(manufacturer,?),updated_at=? WHERE entity_id=?", (text(platform.get("category")) or None, text(platform.get("country")) or None, text(platform.get("developer_manufacturer")) or None, created, entity_id))
            for alias in sorted({name, name_zh} - {""}):
                con.execute("INSERT OR IGNORE INTO ontology_entity_alias(alias_id,entity_id,alias,alias_type,language,source_snapshot_id,source_record_id,confidence,review_status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)", (stable_id("alias", entity_id, alias.casefold()), entity_id, alias, "agent-json-name", "zh" if re.search(r"[\u4e00-\u9fff]", alias) else "en", snapshot_id, raw_id, 0.9, "pending", created))
            attributes: dict[str, dict[str, Any]] = {key: {"state": "unknown", "values": []} for key in expected}
            present: list[str] = []
            for key, value in platform.items():
                mapped = FIELD_MAP.get(key)
                if not mapped or mapped not in fields or value in (None, "", [], {}):
                    continue
                url = citation_for(platform, key) or citation_for(platform, "category")
                quote = quote_value(value)
                evidence_id = stable_id("evidence", snapshot_id, str(index), key)
                evidence = _evidence(con, evidence_id=evidence_id, url=url, version=f"sha256:{digest}", quote=quote, locator={"lineStart": index, "lineEnd": index}, created=created)
                spec = fields[mapped]
                raw = text(value)
                if isinstance(value, list):
                    parsed, unit, value_type = value, None, "list"
                elif spec.get("valueType") in {"quantity", "number"}:
                    parsed, unit, value_type = numeric_value(raw, spec.get("unit"))
                else:
                    parsed, unit, value_type = raw, None, spec.get("valueType", "text")
                assertion_id = stable_id("assertion", entity_id, mapped, evidence_id)
                item = {"assertionId": assertion_id, "rawValue": raw, "valueType": value_type, "value": parsed, "normalizedValue": parsed, "unit": unit, "qualifier": "exact", "conditions": None, "validTime": None, "observedAt": None, "claimKind": "reported-observation", "basis": "source-document", "evidenceRefs": [evidence], "confidence": 0.7, "reviewStatus": "pending", "truthVerified": False}
                attributes[mapped] = {"state": "reported", "values": [item]}; present.append(mapped)
                con.execute("INSERT INTO ontology_assertion(assertion_id,entity_id,attribute_key,entity_revision_id,raw_value,normalized_value,value_type,unit,qualifier,conditions,claim_kind,basis,evidence_ids,confidence,review_status,truth_verified,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (assertion_id, entity_id, mapped, None, raw, json.dumps(parsed, ensure_ascii=False), value_type, unit, "exact", None, "reported-observation", "source-document", json.dumps([evidence_id]), 0.7, "pending", 0, created))
                created_assertions += 1
            unknown = sorted(set(expected) - set(present))
            revision_doc = {"schemaVersion": SCHEMA_VERSION, "objectId": entity_id, "objectType": "EquipmentModel", "objectRevision": int(con.execute("SELECT current_revision FROM ontology_entity WHERE entity_id=?", (entity_id,)).fetchone()[0]) + 1, "attributes": attributes, "completeness": {"mode": "template", "templateId": "EquipmentModel", "expectedKeys": expected, "presentKeys": sorted(set(present)), "unknownKeys": unknown, "notApplicableKeys": [], "conflictKeys": [], "missingRequiredKeys": [], "coverageRatio": len(set(present)) / len(expected) if expected else 1.0, "status": "valid" if not unknown else "incomplete", "checkedAt": created, "validatorVersion": "attributes-jsonb.v1"}, "provenance": {"sourceSnapshotIds": [snapshot_id], "sourceRecordIds": [raw_id], "generatedBy": PARSER_VERSION, "createdAt": created, "updatedAt": created, "contentHash": "0" * 64}}
            revision_doc["provenance"]["contentHash"] = canonical_attributes_hash(revision_doc)
            errors = validate_attributes_jsonb(revision_doc, attribute_registry=set(fields))
            if errors:
                raise ValueError(f"{name}: invalid attributes JSONB: {'; '.join(errors)}")
            revision = revision_doc["objectRevision"]
            con.execute("INSERT INTO ontology_entity_revision(entity_revision_id,entity_id,revision,attributes_json,attributes_schema_version,attributes_sha256,source_snapshot_id,source_record_id,review_status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)", (stable_id("entity-revision", entity_id, revision_doc["provenance"]["contentHash"], str(revision)), entity_id, revision, json.dumps(revision_doc, ensure_ascii=False, sort_keys=True), SCHEMA_VERSION, revision_doc["provenance"]["contentHash"], snapshot_id, raw_id, "pending", created))
            con.execute("UPDATE ontology_entity SET current_revision=?,updated_at=? WHERE entity_id=?", (revision, created, entity_id))

        # Resolve and persist relations after all platform aliases are known.
        for index, relation in enumerate(payload.get("relations") or [], start=1):
            if not isinstance(relation, dict) or not text(relation.get("subject")) or not text(relation.get("object")):
                continue
            subject_name, object_name = text(relation["subject"]), text(relation["object"])
            subject_id = aliases.get(norm_name(subject_name)) or ensure_entity(subject_name, "EquipmentModel", preferred=subject_name)
            relation_type = text(relation.get("predicate"))
            object_is_equipment = relation_type == "derived_from_or_program_basis" or norm_name(object_name) in aliases
            object_type = "EquipmentModel" if object_is_equipment else "Program" if relation_type == "procured_under" or any(token in object_name for token in ("合同", "项目", "FMS")) else "Organization"
            object_id = aliases.get(norm_name(object_name)) or ensure_entity(object_name, object_type, preferred=object_name)
            url = text(relation.get("citation")); quote = quote_value(relation)
            evidence_id = stable_id("evidence", snapshot_id, "relation", str(index))
            _evidence(con, evidence_id=evidence_id, url=url, version=f"sha256:{digest}", quote=quote, locator={"lineStart": index, "lineEnd": index}, created=created)
            qualifier = {key: value for key, value in relation.items() if key not in {"subject", "predicate", "object", "citation"}}
            con.execute("INSERT OR IGNORE INTO ontology_relation_assertion(relation_id,from_entity_id,to_entity_id,predicate,qualifier,evidence_ids,claim_kind,confidence,review_status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)", (stable_id("relation", snapshot_id, str(index)), subject_id, object_id, RELATION_PREDICATES.get(relation_type, relation_type), json.dumps(qualifier, ensure_ascii=False), json.dumps([evidence_id]), "agent-result-json", 0.7, "pending", created))
            created_relations += 1

        # Production and cost facts enter the same human review boundary as
        # other imported assertions. They are deliberately not materialized
        # into production/cost records at import time.
        def manufacturing_candidates(items, candidate_type):
            nonlocal created_manufacturing_candidates
            for index, item in enumerate(items or [], start=1):
                if not isinstance(item, dict):
                    continue
                subject = text(item.get("entity") or item.get("model") or item.get("platform") or item.get("name"))
                entity_id = aliases.get(norm_name(subject))
                if not entity_id or not subject:
                    continue
                candidate_payload = dict(item)
                candidate_payload.pop("entity", None); candidate_payload.pop("model", None); candidate_payload.pop("platform", None)
                citation = text(item.get("citation") or item.get("sourceUrl"))
                quote = quote_value(item.get("quote") or item.get("evidence") or item)
                candidate_payload["evidence"] = {"sourceUri": citation, "quote": quote, "sourceVersion": f"sha256:{digest}", "sourceSnapshotId": snapshot_id}
                candidate_payload["sourceSnapshotIds"] = list(dict.fromkeys([*(item.get("sourceSnapshotIds") or []), snapshot_id]))
                canonical = json.dumps({"entityId": entity_id, "candidateType": candidate_type, "payload": candidate_payload}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                content_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
                candidate_id = "mfg-candidate-" + content_hash[:20]
                con.execute("INSERT OR IGNORE INTO ontology_manufacturing_candidate(candidate_id,entity_id,candidate_type,payload_json,evidence_json,source_snapshot_ids_json,content_hash,status,source_import_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)", (candidate_id, entity_id, candidate_type, json.dumps(candidate_payload, ensure_ascii=False, sort_keys=True), json.dumps(candidate_payload["evidence"], ensure_ascii=False), json.dumps(candidate_payload["sourceSnapshotIds"], ensure_ascii=False), content_hash, "under-review", snapshot_id, created, created))
                created_manufacturing_candidates += 1

        manufacturing_candidates(payload.get("productionRecords") or payload.get("production_records"), "production")
        manufacturing_candidates(payload.get("costRecords") or payload.get("cost_records"), "cost")

        # Preserve non-platform sections verbatim for later review/audit.
        for index, (key, value) in enumerate(payload.items(), start=1):
            if key in {"platforms", "productionRecords", "production_records", "costRecords", "cost_records"}:
                continue
            raw_id = stable_id("raw", snapshot_id, "root", key)
            raw_payload = json.dumps(value, ensure_ascii=False, sort_keys=True)
            con.execute("INSERT OR IGNORE INTO ontology_raw_record(raw_record_id,snapshot_id,sheet_name,row_number,cell_range,raw_payload,raw_row_hash,canonical_model_id,normalization_status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)", (raw_id, snapshot_id, "root", index, key, raw_payload, hashlib.sha256(raw_payload.encode("utf-8")).hexdigest(), None, "normalized", created))
        con.execute("UPDATE ontology_import_snapshot SET row_count=?,status='completed',completed_at=? WHERE snapshot_id=?", (len(payload["platforms"]), now(), snapshot_id))
    return {"status": "completed", "snapshotId": snapshot_id, "fileHash": digest, "platformCount": len(payload["platforms"]), "entityCount": created_entities, "assertionCount": created_assertions, "relationCount": created_relations, "manufacturingCandidateCount": created_manufacturing_candidates}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True)
    parser.add_argument("--database")
    args = parser.parse_args()
    print(json.dumps(import_agent_result(args.source, args.database), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
