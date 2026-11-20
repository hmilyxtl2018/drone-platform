"""Build and persist explainable evidence factor graphs for UAV candidates.

The graph is a review projection. It records what supports a candidate and
which constraints remain unresolved; it never changes candidate or ontology
publication state.
"""
from __future__ import annotations

import hashlib
import json
import secrets
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

try:
    from factor_graph_contract import assert_valid, content_hash, with_content_hash
except ModuleNotFoundError:  # pragma: no cover
    from .factor_graph_contract import assert_valid, content_hash, with_content_hash


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _json(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _decode(value: Any) -> Any:
    if value is None or isinstance(value, (dict, list, int, float, bool)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return value


def _stable_id(prefix: str, value: Any) -> str:
    digest = hashlib.sha256(_json(value).encode("utf-8")).hexdigest()[:20]
    return f"{prefix}-{digest}"


def _candidate_value(candidate: dict[str, Any]) -> tuple[str, str, Any]:
    kind = str(candidate.get("candidate_type") or "")
    payload = candidate.get("payload") or {}
    if kind == "composition":
        key = f"composition:{payload.get('partName') or 'unknown'}"
        value = {key: payload.get(key) for key in ("partName", "relation", "quantity") if key in payload}
        return key, "composition", value
    if kind == "process":
        key = f"process:{payload.get('processName') or 'unknown'}"
        value = {key: payload.get(key) for key in ("processName", "processType", "sequenceNo") if key in payload}
        return key, "process", value
    if kind == "organization":
        key = f"organization:{payload.get('role') or 'unknown'}"
        value = {key: payload.get(key) for key in ("organizationName", "organizationId", "role") if key in payload}
        return key, "organization", value
    if kind == "production":
        key = f"production:{payload.get('recordType') or 'unknown'}"
        value = {key: payload.get(key) for key in ("recordType", "quantity", "quantityUnit", "startDate", "endDate") if key in payload}
        return key, "production", value
    if kind == "cost":
        key = f"cost:{payload.get('costType') or 'unknown'}"
        value = {key: payload.get(key) for key in ("costType", "amount", "currency", "priceBasisYear", "quantity", "quantityUnit") if key in payload}
        return key, "cost", value
    return f"unknown:{kind or 'candidate'}", "identity", payload


def _value_signature(value: Any) -> str:
    return _json(value)


def build_manufacturing_graph(entity: dict[str, Any], candidates: list[dict[str, Any]], *, entity_revision_id: str | None = None, graph_run_id: str | None = None) -> dict[str, Any]:
    """Create a deterministic review graph from existing manufacturing candidates."""
    if not isinstance(entity, dict) or not entity.get("entityId") or not entity.get("preferredName"):
        raise ValueError("entityId and preferredName are required")
    candidates = [item for item in candidates if isinstance(item, dict)]
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for candidate in candidates:
        key, _, _ = _candidate_value(candidate)
        grouped[key].append(candidate)

    variables: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    factors: list[dict[str, Any]] = []
    constraints: list[dict[str, Any]] = []
    gaps: list[str] = []
    conflicts: list[str] = []

    variable_by_key: dict[str, dict[str, Any]] = {}
    for key, rows in sorted(grouped.items()):
        _, variable_type, _ = _candidate_value(rows[0])
        signatures = {_value_signature(_candidate_value(row)[2]) for row in rows}
        state = "conflict" if len(signatures) > 1 else "proposed"
        if state == "conflict":
            conflicts.append(f"{key} has {len(signatures)} different candidate values")
        variable = {
            "variableId": _stable_id("variable", [entity["entityId"], key]),
            "variableKey": key,
            "variableType": variable_type,
            "state": state,
            "value": [_candidate_value(row)[2] for row in rows] if len(signatures) > 1 else _candidate_value(rows[0])[2],
            "unit": None,
            "conditions": {"configurations": sorted({str((row.get("payload") or {}).get("configuration")) for row in rows if (row.get("payload") or {}).get("configuration")})},
            "candidateIds": [str(row.get("candidate_id")) for row in rows if row.get("candidate_id")],
        }
        variable_by_key[key] = variable
        variables.append(variable)

    observation_by_candidate: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        candidate_id = str(candidate.get("candidate_id") or "")
        key, _, _ = _candidate_value(candidate)
        payload = candidate.get("payload") or {}
        evidence = candidate.get("evidence") or payload.get("evidence") or {}
        snapshot_ids = candidate.get("sourceSnapshotIds") or payload.get("sourceSnapshotIds") or []
        snapshot_id = str((snapshot_ids[0] if snapshot_ids else evidence.get("snapshotId")) or "")
        quote = str(evidence.get("quote") or "")
        source_uri = str(evidence.get("sourceUri") or "")
        if not snapshot_id or not quote or not source_uri:
            gaps.append(f"candidate {candidate_id or '<unknown>'} lacks a complete immutable source reference")
            continue
        evidence_hash = str(evidence.get("contentHash") or "")
        if len(evidence_hash) != 64 or any(char not in "0123456789abcdef" for char in evidence_hash.lower()):
            evidence_hash = hashlib.sha256(quote.encode("utf-8")).hexdigest()
        observation = {
            "observationId": _stable_id("observation", [candidate_id, snapshot_id, evidence.get("contentHash"), quote]),
            "candidateId": candidate_id or None,
            "sourceSnapshotId": snapshot_id,
            "sourceUri": source_uri,
            "quote": quote[:1200],
            "sourceContentHash": evidence_hash,
            "contentHash": evidence_hash,
            "claimState": "rejected" if candidate.get("status") == "rejected" else "reported",
            "variableIds": [variable_by_key[key]["variableId"]],
            "locator": evidence.get("locator"),
        }
        observations.append(observation)
        observation_by_candidate[candidate_id] = observation

        variable_id = variable_by_key[key]["variableId"]
        factors.append({
            "factorId": _stable_id("factor", ["source-support", candidate_id, observation["observationId"]]),
            "factorType": "source-support",
            "status": "supporting",
            "variableIds": [variable_id],
            "observationIds": [observation["observationId"]],
            "score": None,
            "details": {"candidateId": candidate_id, "candidateStatus": candidate.get("status", "under-review")},
        })
        identity_ok = str(evidence.get("sourceSubject") or "").strip().casefold() == str(entity["preferredName"]).strip().casefold() or str(entity["preferredName"]).casefold() in quote.casefold()
        factors.append({
            "factorId": _stable_id("factor", ["identity-match", candidate_id, identity_ok]),
            "factorType": "identity-match",
            "status": "satisfied" if identity_ok else "unresolved",
            "variableIds": [variable_id],
            "observationIds": [observation["observationId"]],
            "score": 1 if identity_ok else None,
            "details": {"entityName": entity["preferredName"], "sourceSubject": evidence.get("sourceSubject")},
        })
        if not identity_ok:
            gaps.append(f"candidate {candidate_id} does not have an explicit identity match in the saved evidence")

    for key, variable in sorted(variable_by_key.items()):
        rows = grouped[key]
        variable_id = variable["variableId"]
        obs = [observation_by_candidate[str(row.get("candidate_id"))] for row in rows if str(row.get("candidate_id")) in observation_by_candidate]
        configs = sorted({str((row.get("payload") or {}).get("configuration")) for row in rows if (row.get("payload") or {}).get("configuration")})
        review_open = any(row.get("status") in {"under-review", "needs-evidence"} for row in rows)
        factors.append({
            "factorId": _stable_id("factor", ["source-independence", variable_id]),
            "factorType": "source-independence",
            "status": "unresolved",
            "variableIds": [variable_id],
            "observationIds": [item["observationId"] for item in obs],
            "score": None,
            "details": {"sourceCount": len({item["sourceSnapshotId"] for item in obs}), "independenceStatus": "unknown"},
        })
        if len(configs) > 1:
            conflicts.append(f"{key} has multiple configurations: {', '.join(configs)}")
        constraints.append({
            "constraintId": _stable_id("constraint", ["configuration", variable_id]),
            "constraintType": "configuration",
            "status": "failed" if len(configs) > 1 else ("satisfied" if configs else "unresolved"),
            "variableIds": [variable_id],
            "details": {"configurations": configs},
        })
        constraints.append({
            "constraintId": _stable_id("constraint", ["source", variable_id]),
            "constraintType": "source",
            "status": "satisfied" if len(obs) == len(rows) and obs else "unresolved",
            "variableIds": [variable_id],
            "details": {"observationCount": len(obs), "candidateCount": len(rows)},
        })
        constraints.append({
            "constraintId": _stable_id("constraint", ["review", variable_id]),
            "constraintType": "review",
            "status": "unresolved" if review_open else "satisfied",
            "variableIds": [variable_id],
            "details": {"reviewRequired": review_open},
        })

    if conflicts:
        inference_status = "conflict"
    elif gaps:
        inference_status = "needs-evidence"
    else:
        inference_status = "pending-review"
    graph = {
        "schemaVersion": "evidence-factor-graph.v1",
        "graphRunId": graph_run_id or _stable_id("fg-run", [entity["entityId"], entity_revision_id, [row.get("candidate_id") for row in candidates]]),
        "entity": {"entityId": entity["entityId"], "entityRevisionId": entity_revision_id, "name": entity["preferredName"], "variant": entity.get("variant")},
        "variables": variables,
        "observations": observations,
        "factors": factors,
        "constraints": constraints,
        "inference": {"status": inference_status, "conflicts": conflicts, "gaps": gaps, "summary": {"candidateCount": len(candidates), "variableCount": len(variables), "observationCount": len(observations), "factorCount": len(factors), "constraintCount": len(constraints)}},
    }
    return with_content_hash(graph)


class FactorGraphStore:
    def __init__(self, store):
        self.store = getattr(store, "store", store)

    def create(self, graph: dict[str, Any]) -> dict[str, Any]:
        assert_valid(graph)
        graph_id = graph["graphRunId"]
        with self.store._lock, self.store._connect() as con:
            existing = con.execute("SELECT graph_json FROM ontology_factor_graph_run WHERE graph_run_id=?", (graph_id,)).fetchone()
            if existing:
                saved = _decode(existing[0])
                if saved.get("contentHash") != graph["contentHash"]:
                    raise ValueError("graphRunId already exists with a different content hash")
                return saved
            now = _now()
            con.execute("INSERT INTO ontology_factor_graph_run(graph_run_id,entity_id,entity_revision_id,schema_version,status,graph_json,content_hash,created_at) VALUES(?,?,?,?,?,?,?,?)", (graph_id, graph["entity"]["entityId"], graph["entity"].get("entityRevisionId"), graph["schemaVersion"], graph["inference"]["status"], _json(graph), graph["contentHash"], now))
            for row in graph["variables"]:
                con.execute("INSERT INTO ontology_factor_graph_variable(variable_id,graph_run_id,entity_id,variable_key,variable_type,state,value_json,unit,conditions_json,candidate_ids_json,content_hash,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (row["variableId"], graph_id, graph["entity"]["entityId"], row["variableKey"], row["variableType"], row["state"], _json(row.get("value")) if row.get("value") is not None else None, row.get("unit"), _json(row.get("conditions") or {}), _json(row.get("candidateIds") or []), row["contentHash"], now))
            for row in graph["observations"]:
                con.execute("INSERT INTO ontology_factor_graph_observation(observation_id,graph_run_id,entity_id,candidate_id,source_snapshot_id,source_uri,quote,claim_state,variable_ids_json,locator_json,content_hash,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (row["observationId"], graph_id, graph["entity"]["entityId"], row.get("candidateId"), row["sourceSnapshotId"], row["sourceUri"], row["quote"], row["claimState"], _json(row["variableIds"]), _json(row["locator"]) if row.get("locator") is not None else None, row["contentHash"], now))
            for row in graph["factors"]:
                con.execute("INSERT INTO ontology_factor_graph_factor(factor_id,graph_run_id,factor_type,status,variable_ids_json,observation_ids_json,score,details_json,content_hash,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)", (row["factorId"], graph_id, row["factorType"], row["status"], _json(row.get("variableIds") or []), _json(row.get("observationIds") or []), row.get("score"), _json(row.get("details") or {}), row["contentHash"], now))
            for row in graph["constraints"]:
                con.execute("INSERT INTO ontology_factor_graph_constraint(constraint_id,graph_run_id,constraint_type,status,variable_ids_json,details_json,content_hash,created_at) VALUES(?,?,?,?,?,?,?,?)", (row["constraintId"], graph_id, row["constraintType"], row["status"], _json(row.get("variableIds") or []), _json(row.get("details") or {}), row["contentHash"], now))
        return graph

    def build_from_candidates(self, entity: dict[str, Any], candidates: list[dict[str, Any]], *, entity_revision_id: str | None = None) -> dict[str, Any]:
        return self.create(build_manufacturing_graph(entity, candidates, entity_revision_id=entity_revision_id))

    def get(self, graph_run_id: str) -> dict[str, Any] | None:
        with self.store._connect() as con:
            row = con.execute("SELECT graph_json FROM ontology_factor_graph_run WHERE graph_run_id=?", (graph_run_id,)).fetchone()
        return _decode(row[0]) if row else None

    def list_runs(self, entity_id: str) -> list[dict[str, Any]]:
        with self.store._connect() as con:
            rows = con.execute("SELECT graph_run_id,status,content_hash,created_at,graph_json FROM ontology_factor_graph_run WHERE entity_id=? ORDER BY created_at DESC", (entity_id,)).fetchall()
        result = []
        for row in rows:
            graph = _decode(row["graph_json"])
            result.append({"graphRunId": row["graph_run_id"], "status": row["status"], "contentHash": row["content_hash"], "createdAt": row["created_at"], "summary": graph.get("inference", {}).get("summary", {}), "conflicts": graph.get("inference", {}).get("conflicts", []), "gaps": graph.get("inference", {}).get("gaps", [])})
        return result


__all__ = ["FactorGraphStore", "build_manufacturing_graph"]
