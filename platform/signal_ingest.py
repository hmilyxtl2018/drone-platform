"""Adapt Firecrawl/DeepSeek UAV enrichment into the signal evidence chain.

The enrichment agents already emit useful, source-bound candidates, but their
JSON shape predates the ``SignalObservation``/``CapabilityClaim`` contracts.
This adapter is the boundary between those untrusted artifacts and the
append-only evidence tables introduced by migration 018.

The adapter deliberately never reviews or publishes a claim.  Every imported
claim is ``under-review`` with ``truthVerified`` false, even when an upstream
artifact accidentally contains an accepted/published status.  Firecrawl
snapshots are retained by reference in the evidence locator and bundle
validity payload; exact quotes and content hashes are required before a record
is created.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.parse import urlsplit

try:
    from signal_contracts import validate_records, with_content_hash
except ModuleNotFoundError:  # pragma: no cover - package import
    from .signal_contracts import validate_records, with_content_hash


class SignalIngestError(ValueError):
    """Raised when a Firecrawl/DeepSeek artifact cannot be safely adapted."""


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _string(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value).strip()


def _entity_id(candidate: Mapping[str, Any], entity_map: Mapping[str, Any] | None = None) -> str:
    value = _string(candidate.get("entityId"))
    if value:
        return value
    subject = _string(candidate.get("entityName") or candidate.get("subject"))
    if entity_map and subject:
        for key, item in entity_map.items():
            if isinstance(item, Mapping):
                names = [item.get("preferredName"), item.get("englishName"), *(item.get("aliases") or [])]
            else:
                names = [item]
            if any(subject.casefold() == _string(name).casefold() for name in names if name):
                return _string(key)
    raise SignalIngestError("candidate is missing entityId and cannot be resolved from entityName")


def _source_type(source: Mapping[str, Any]) -> str:
    role = _string(source.get("publisherRole") or source.get("sourceRole")).casefold()
    if "military" in role or "official" in role or "government" in role:
        return "official-publication"
    if "manufacturer" in role or "industry" in role:
        return "manufacturer-disclosure"
    if "test" in role or "evaluation" in role:
        return "evaluation-report"
    if "image" in role or "imagery" in role:
        return "public-imagery"
    if "research" in role:
        return "research-analysis"
    return "defense-media" if role else "web-page"


def _domain(uri: str) -> str:
    try:
        return urlsplit(uri).netloc.casefold()
    except Exception:
        return ""


def _value_type(candidate: Mapping[str, Any], raw: str) -> str:
    qualifier = _string(candidate.get("qualifier")).casefold()
    if qualifier == "range" or re.search(r"\d\s*(?:-|–|—|~|至|to)\s*\d", raw, re.I):
        return "range"
    if isinstance(candidate.get("proposedValue"), bool):
        return "boolean"
    if candidate.get("unit"):
        return "quantity"
    if re.fullmatch(r"\s*(?:[+-]?\d+(?:\.\d+)?|[+-]?\.\d+)\s*", raw):
        return "number"
    return "text"


def _normalized_value(candidate: Mapping[str, Any], raw: str, value_type: str) -> Any:
    # Preserve the source value as the canonical raw string.  Numeric parsing
    # is conservative so a compound value never becomes a false scalar.
    if value_type == "number":
        try:
            return float(raw) if "." in raw else int(raw)
        except ValueError:
            return raw
    if value_type == "range":
        match = re.search(r"([+-]?\d+(?:\.\d+)?)\s*(?:-|–|—|~|至|to)\s*([+-]?\d+(?:\.\d+)?)", raw, re.I)
        if match:
            return {"lower": float(match.group(1)), "upper": float(match.group(2))}
    # DeepSeek may already return a normalized scalar/object.  Do not trust a
    # generated value over the source string, but retain it for reviewer use.
    supplied = candidate.get("normalizedValue")
    if supplied is not None:
        return deepcopy(supplied)
    return raw


def _conditions(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return deepcopy(dict(value))
    text = _string(value)
    return {"text": text} if text else {}


def _qualifier(value: Any) -> str:
    allowed = {"exact", "approximately", "at-least", "at-most", "greater-than", "less-than", "unknown"}
    value = _string(value).casefold()
    return value if value in allowed else "unknown"


def _group_domain(attribute_key: str, candidate: Mapping[str, Any], registry: Mapping[str, Any] | None = None) -> str:
    role = _string(candidate.get("capabilityRole"))
    if role:
        return role
    if registry:
        for group in registry.get("groups", []):
            if attribute_key in (group.get("keys") or []):
                return _string(group.get("label") or group.get("id"), "装备能力")
    prefixes = {
        "sensor": "侦察与感知", "detection": "侦察与感知", "tracking": "侦察与感知",
        "communication": "通信与信息共享", "data-link": "通信与信息共享", "link-": "通信与信息共享",
        "autonomy": "自主与协同", "swarm": "自主与协同", "collaborative": "自主与协同",
        "weapon": "任务与载荷集成", "payload": "任务与载荷集成", "stealth": "生存与环境适应",
        "jamming": "生存与环境适应", "environment": "生存与环境适应", "maintenance": "保障与部署",
        "propulsion": "产业与技术依赖", "production": "产业与技术依赖", "manufacturing": "产业与技术依赖",
    }
    return next((label for prefix, label in prefixes.items() if attribute_key.startswith(prefix) or prefix in attribute_key), "飞行与任务持续")


def _source_ref(evidence: Mapping[str, Any], source: Mapping[str, Any] | None = None, *, fallback_snapshot: str | None = None) -> dict[str, Any]:
    evidence = evidence or {}
    source = source or {}
    uri = _string(evidence.get("sourceUri") or source.get("sourceUri") or source.get("url"))
    quote = _string(evidence.get("quote"))
    content_hash = _string(evidence.get("contentHash") or source.get("contentHash"))
    if not uri or not quote or not re.fullmatch(r"[a-fA-F0-9]{64}", content_hash):
        raise SignalIngestError("each candidate evidence requires sourceUri, non-empty quote and SHA-256 contentHash")
    source_version = _string(evidence.get("sourceVersion") or source.get("scrapeId") or source.get("sourceVersion")) or None
    snapshot_path = _string(evidence.get("snapshotPath") or source.get("snapshotPath") or fallback_snapshot) or None
    snapshot_position = evidence.get("snapshotPosition", source.get("snapshotPosition"))
    locator: dict[str, Any] = {}
    for key in ("lineStart", "lineEnd", "sourceItemIndex", "snapshotPosition"):
        value = evidence.get(key, snapshot_position if key == "snapshotPosition" else None)
        if value is not None:
            locator[key] = value
    if snapshot_path:
        locator["snapshotPath"] = snapshot_path
    if evidence.get("snapshotMatch") is not None:
        locator["snapshotMatch"] = bool(evidence.get("snapshotMatch"))
    return {
        "evidenceId": _string(evidence.get("evidenceId")) or "evidence-" + _hash({"uri": uri, "version": source_version, "hash": content_hash, "quote": quote})[:24],
        "sourceUri": uri,
        "sourceVersion": source_version,
        "contentHash": content_hash.lower(),
        "quote": quote,
        "locator": locator,
        "sourceType": _source_type(source),
        "independenceKey": _string(source.get("sourceId") or source.get("firecrawlSearchId") or _domain(uri)) or None,
    }


def _source_index(payload: Mapping[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    indexed: dict[tuple[str, str], dict[str, Any]] = {}
    for source in payload.get("sources") or []:
        if not isinstance(source, Mapping):
            continue
        uri = _string(source.get("sourceUri") or source.get("url"))
        indexed[(uri, _string(source.get("contentHash")))] = dict(source)
        if uri:
            indexed.setdefault((uri, ""), dict(source))
    return indexed


def adapt_enrichment(payload: Mapping[str, Any], *, entity_map: Mapping[str, Any] | None = None, audit_run_id: str | None = None, registry: Mapping[str, Any] | None = None, collected_at: str | None = None) -> dict[str, Any]:
    """Convert one ``uav-capability-enrichment-agent-*.json`` artifact.

    Returns validated contract records grouped as observations, claims and
    evidence bundles.  No database is touched by this function.
    """
    if not isinstance(payload, Mapping):
        raise SignalIngestError("enrichment payload must be an object")
    source_lookup = _source_index(payload)
    default_time = collected_at or _string(payload.get("createdAt")) or _now()
    candidates = payload.get("candidates") or []
    if not isinstance(candidates, list):
        raise SignalIngestError("enrichment candidates must be an array")
    observations: dict[str, dict[str, Any]] = {}
    claims: list[dict[str, Any]] = []
    bundles: list[dict[str, Any]] = []
    for index, candidate in enumerate(candidates):
        if not isinstance(candidate, Mapping):
            continue
        entity_id = _entity_id(candidate, entity_map)
        attribute_key = _string(candidate.get("attributeKey") or candidate.get("predicate"))
        raw = _string(candidate.get("proposedValue") or candidate.get("rawValue") or candidate.get("value"))
        if not attribute_key or not raw:
            continue
        evidence_items = candidate.get("evidence") or []
        if not isinstance(evidence_items, list) or not evidence_items:
            # A model claim with no quote is not admissible to the signal
            # chain. Keep it in the legacy review queue instead.
            continue
        refs: list[dict[str, Any]] = []
        for item in evidence_items:
            if not isinstance(item, Mapping):
                continue
            uri = _string(item.get("sourceUri"))
            content_hash = _string(item.get("contentHash"))
            source = source_lookup.get((uri, content_hash)) or source_lookup.get((uri, "")) or {}
            refs.append(_source_ref(item, source, fallback_snapshot=_string(candidate.get("snapshotPath")) or None))
        if not refs:
            continue
        source_keys = []
        for ref in refs:
            source_key = _hash({"entityId": entity_id, "sourceUri": ref["sourceUri"], "sourceVersion": ref.get("sourceVersion"), "contentHash": ref["contentHash"], "quote": ref.get("quote")})[:24]
            source_keys.append(source_key)
            obs_id = "signal-observation-" + source_key
            if obs_id not in observations:
                source_meta = source_lookup.get((ref["sourceUri"], ref["contentHash"])) or source_lookup.get((ref["sourceUri"], "")) or {}
                obs = {
                    "recordType": "SignalObservation", "observationId": obs_id, "entityId": entity_id,
                    "observationType": "IMAGERY" if ref.get("sourceType") == "public-imagery" else "OSINT",
                    "collectedAt": _string(source_meta.get("retrievedAt") or source_meta.get("publishedAt")) or default_time,
                    "collectionLocation": {"accessBasis": "public", "collector": "firecrawl", "sourceSnapshot": ref.get("locator", {}).get("snapshotPath")},
                    "conditions": _conditions(candidate.get("conditions")),
                    "features": {"claims": [], "sourceRole": source_meta.get("publisherRole") or source_meta.get("sourceRole"), "sourceUri": ref["sourceUri"]},
                    "sourceRefs": [ref], "claimState": "reported", "confidence": None,
                    "verificationStatus": "under-review", "evidenceLevel": "E1", "createdAt": default_time,
                }
                if audit_run_id:
                    obs["auditRunId"] = audit_run_id
                observations[obs_id] = with_content_hash(obs)
        value_type = _value_type(candidate, raw)
        claim_id = "capability-claim-" + _hash({"candidateId": _string(candidate.get("candidateId")), "entityId": entity_id, "attributeKey": attribute_key, "raw": raw, "refs": refs})[:24]
        observation_ids = ["signal-observation-" + key for key in source_keys]
        evidence_ids = [ref["evidenceId"] for ref in refs]
        source_meta = source_lookup.get((refs[0]["sourceUri"], refs[0]["contentHash"])) or source_lookup.get((refs[0]["sourceUri"], "")) or {}
        claim = {
            "recordType": "CapabilityClaim", "claimId": claim_id, "entityId": entity_id,
            "capabilityDomain": _group_domain(attribute_key, candidate, registry), "metric": attribute_key,
            "rawValue": raw, "valueType": value_type, "normalizedValue": _normalized_value(candidate, raw, value_type),
            "unit": _string(candidate.get("unit")) or None, "qualifier": _qualifier(candidate.get("qualifier")),
            "conditions": _conditions(candidate.get("conditions")), "variant": _string(candidate.get("variant")) or None,
            "configuration": _string(candidate.get("configuration")) or None,
            "operationalEffect": _string(candidate.get("capabilityRole")) or None,
            "sourceObservationIds": list(dict.fromkeys(observation_ids)), "evidenceBundleIds": [],
            "claimState": "reported", "verificationStatus": "under-review", "evidenceLevel": "E1",
            "confidence": candidate.get("extractionConfidence") if isinstance(candidate.get("extractionConfidence"), (int, float)) else None,
            "reviewStatus": "under-review", "truthVerified": False, "createdAt": default_time,
        }
        entity_revision_id = _string(candidate.get("entityRevisionId"))
        if entity_revision_id:
            claim["entityRevisionId"] = entity_revision_id
        if audit_run_id:
            claim["auditRunId"] = audit_run_id
        bundle_id = "evidence-bundle-" + _hash({"claimId": claim_id, "evidenceIds": evidence_ids})[:24]
        claim["evidenceBundleIds"] = [bundle_id]
        bundle = {
            "recordType": "EvidenceBundle", "evidenceBundleId": bundle_id, "entityId": entity_id,
            "claimIds": [claim_id], "observationIds": list(dict.fromkeys(observation_ids)), "evidenceIds": evidence_ids,
            "sourceIndependence": {"status": "unknown", "independentSourceCount": len(set(evidence_ids)), "method": "pending-human-verification"},
            "validity": {"sourceSnapshots": [ref.get("locator", {}) for ref in refs], "modelMatch": {"variant": claim["variant"], "configuration": claim["configuration"]}, "sourceType": source_meta.get("publisherRole") or source_meta.get("sourceRole")},
            "counterEvidenceIds": [], "evidenceLevel": "E1", "verificationStatus": "under-review", "createdAt": default_time,
        }
        if audit_run_id:
            bundle["auditRunId"] = audit_run_id
        bundles.append(with_content_hash(bundle))
        claims.append(with_content_hash(claim))
        for obs_id in observation_ids:
            features = observations[obs_id]["features"]
            features["claims"].append({"claimId": claim_id, "metric": attribute_key, "rawValue": raw})
            observations[obs_id]["features"] = features
            observations[obs_id] = with_content_hash(observations[obs_id])
    records = [*observations.values(), *claims, *bundles]
    return {"runId": payload.get("runId"), "observations": list(observations.values()), "claims": claims, "evidenceBundles": bundles, "records": validate_records(records)}


def adapt_enrichment_file(path: str | Path, **kwargs: Any) -> dict[str, Any]:
    path = Path(path)
    return adapt_enrichment(json.loads(path.read_text(encoding="utf-8")), **kwargs)


def _db_json(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def persist_signal_records(store: Any, adapted: Mapping[str, Any], *, strict_entities: bool = True) -> dict[str, int]:
    """Persist adapted records and source links into an IntelligenceStore.

    All rows use ``INSERT OR IGNORE`` and the new signal tables are append-only.
    The existing ``ontology_evidence_link`` table receives the exact quote and
    content hash so legacy review screens can open the same source evidence.
    """
    records = adapted.get("records") or []
    observations = adapted.get("observations") or []
    claims = adapted.get("claims") or []
    bundles = adapted.get("evidenceBundles") or []
    with store._lock, store._connect() as con:
        con.execute("BEGIN IMMEDIATE")
        for obs in observations:
            if strict_entities and obs.get("entityId") and not con.execute("SELECT 1 FROM ontology_entity WHERE entity_id=?", (obs["entityId"],)).fetchone():
                raise SignalIngestError(f"unknown entityId: {obs['entityId']}")
            for ref in obs.get("sourceRefs") or []:
                locator = ref.get("locator") or {}
                con.execute("INSERT OR IGNORE INTO ontology_evidence_link(evidence_id,source_type,source_ref,source_version,quote,content_hash,locator_json,published_at,retrieved_at,source_reliability,verification_status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (ref["evidenceId"], "web-page", ref["sourceUri"], ref.get("sourceVersion") or "unknown", ref.get("quote") or "", ref["contentHash"], _db_json(locator), None, None, "unknown", "under-review", obs.get("createdAt") or _now()))
            con.execute("INSERT OR IGNORE INTO ontology_signal_observation(observation_id,entity_id,audit_run_id,observation_type,collected_at,collection_location,conditions_json,features_json,emitter_signature_id,source_refs,claim_state,confidence,verification_status,evidence_level,content_hash,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (obs["observationId"], obs.get("entityId"), obs.get("auditRunId"), obs["observationType"], obs["collectedAt"], _db_json(obs.get("collectionLocation")), _db_json(obs.get("conditions")), _db_json(obs.get("features")), obs.get("emitterSignatureId"), _db_json(obs.get("sourceRefs")), obs.get("claimState", "reported"), obs.get("confidence"), "under-review", "E1", obs["contentHash"], obs.get("createdAt") or _now()))
        for claim in claims:
            if strict_entities and not con.execute("SELECT 1 FROM ontology_entity WHERE entity_id=?", (claim["entityId"],)).fetchone():
                raise SignalIngestError(f"unknown entityId: {claim['entityId']}")
            con.execute("INSERT OR IGNORE INTO ontology_capability_claim(claim_id,entity_id,entity_revision_id,audit_run_id,capability_domain,metric,raw_value,value_type,normalized_value,unit,qualifier,conditions_json,variant,configuration,operational_effect,source_observation_ids,evidence_bundle_ids,claim_state,verification_status,evidence_level,confidence,review_status,truth_verified,content_hash,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (claim["claimId"], claim["entityId"], claim.get("entityRevisionId"), claim.get("auditRunId"), claim["capabilityDomain"], claim["metric"], claim["rawValue"], claim["valueType"], _db_json(claim.get("normalizedValue")), claim.get("unit"), claim.get("qualifier", "unknown"), _db_json(claim.get("conditions")), claim.get("variant"), claim.get("configuration"), claim.get("operationalEffect"), _db_json(claim.get("sourceObservationIds")), _db_json(claim.get("evidenceBundleIds")), "reported", "under-review", "E1", claim.get("confidence"), "under-review", 0, claim["contentHash"], claim.get("createdAt") or _now()))
        for bundle in bundles:
            con.execute("INSERT OR IGNORE INTO ontology_evidence_bundle(evidence_bundle_id,entity_id,audit_run_id,claim_ids,observation_ids,evidence_ids,source_independence_json,validity_json,counter_evidence_ids,evidence_level,verification_status,content_hash,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (bundle["evidenceBundleId"], bundle.get("entityId"), bundle.get("auditRunId"), _db_json(bundle.get("claimIds")), _db_json(bundle.get("observationIds")), _db_json(bundle.get("evidenceIds")), _db_json(bundle.get("sourceIndependence")), _db_json(bundle.get("validity")), _db_json(bundle.get("counterEvidenceIds")), "E1", "under-review", bundle["contentHash"], bundle.get("createdAt") or _now()))
    return {"records": len(records), "observations": len(observations), "claims": len(claims), "evidenceBundles": len(bundles)}


__all__ = ["SignalIngestError", "adapt_enrichment", "adapt_enrichment_file", "persist_signal_records"]
