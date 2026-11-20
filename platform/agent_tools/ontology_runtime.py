"""Read-only ontology tools for the runtime; source paths are server configured."""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import sqlite3
from contextlib import closing
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit
from urllib.request import url2pathname

from .ontology import DEFAULT_SOURCE, METRICS


ENVELOPE_SCHEMA = {
    "type": "object", "required": ["input", "state"],
    "properties": {"input": {"type": "object"}, "state": {"type": "object"},
                   "arguments": {"type": "object"}}, "additionalProperties": False,
}
SOURCE_SCHEMAS = {
    "ontology.resolve_entity": {"type": "object", "required": ["matches", "ambiguous", "sourceMode"]},
    "ontology.get_entity": {"type": "object", "required": ["uavId", "uavData", "sourceSnapshot", "sourceClaims", "sourceEvidence", "sourceGaps"]},
    "ontology.query_evidence": {"type": "object", "required": ["entityId", "sourceSnapshot", "evidence", "sourceGaps"]},
}


def arguments(payload):
    value = payload.get("arguments", {})
    if not isinstance(value, dict):
        raise ValueError("tool arguments must be an object")
    return value


def _extract_revision(source_path, entity_id, requirements, revision=None):
    """Read any attribute at one revision in one read-only SQLite transaction."""
    with closing(sqlite3.connect(source_path.as_uri() + "?mode=ro", uri=True)) as con:
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA query_only=ON")
        con.execute("BEGIN")
        entity_row = con.execute("SELECT * FROM ontology_entity WHERE entity_id=?", (entity_id,)).fetchone()
        if entity_row is None:
            raise ValueError(f"entity has no current revision: {entity_id}")
        entity = dict(entity_row)
        selected = revision if revision is not None else entity["current_revision"]
        row = con.execute("SELECT * FROM ontology_entity_revision WHERE entity_id=? AND revision=?",
                          (entity_id, selected)).fetchone()
        if row is None:
            raise ValueError("requested historical revision is unavailable" if revision is not None
                             else f"entity has no current revision: {entity_id}")
        row = dict(row)
        raw_json = row["attributes_json"]
        if len(raw_json.encode("utf-8")) > 16 * 1024 * 1024:
            raise ValueError("ontology revision exceeds the adapter size limit")
        document = json.loads(raw_json)
        attributes = document.get("attributes", {})
        if not isinstance(attributes, dict):
            raise ValueError("ontology revision attributes must be an object")
        gaps, evidence, claims = [], [], {}
        uav = {"uavId": entity_id, "name": entity.get("preferred_name")}
        for attribute, group in attributes.items():
            metric, units = METRICS.get(attribute, (attribute, {}))
            claims[metric] = deepcopy(group)
            if not isinstance(group, dict):
                gaps.append(f"{attribute}: attribute structure is unknown")
                continue
            values = group.get("values", [])
            if not isinstance(values, list):
                gaps.append(f"{attribute}: attribute values are unknown")
                continue
            exact = group.get("state") == "reported" and len(values) == 1 and isinstance(values[0], dict) and values[0].get("qualifier") == "exact"
            if not exact:
                gaps.append(f"{metric}: missing, conflicting or non-exact value")
            for claim in values:
                if not isinstance(claim, dict):
                    gaps.append(f"{attribute}: malformed claim retained in attributes")
                    continue
                raw = claim.get("normalizedValue", claim.get("value"))
                unit = claim.get("unit")
                valid_number = isinstance(raw, (int, float)) and not isinstance(raw, bool) and math.isfinite(raw) and raw >= 0
                if attribute in METRICS and exact:
                    if valid_number and unit in units:
                        uav[metric] = raw * units[unit]
                    else:
                        gaps.append(f"{metric}: unsupported unit or non-numeric value")
                if claim.get("truthVerified") is not True:
                    gaps.append(f"{metric}: source claim is unverified")
                if claim.get("reviewStatus") != "approved":
                    gaps.append(f"{metric}: source review is not approved")
                if not claim.get("conditions"):
                    gaps.append(f"{metric}: measurement conditions are missing")
                if not claim.get("observedAt"):
                    gaps.append(f"{metric}: observation timestamp is missing")
                refs = claim.get("evidenceRefs", [])
                if not isinstance(refs, list):
                    refs = []
                    gaps.append(f"{metric}: invalid evidence reference structure")
                if not refs:
                    gaps.append(f"{metric}: source evidence is missing")
                for ref in refs:
                    if not isinstance(ref, dict):
                        gaps.append(f"{metric}: invalid evidence reference retained in attributes")
                        continue
                    eid = ref.get("evidenceId")
                    linked = con.execute("SELECT * FROM ontology_evidence_link WHERE evidence_id=?", (eid,)).fetchone()
                    source = dict(linked) if linked else {}
                    if not linked:
                        gaps.append(f"{metric}: evidence link is missing: {eid}")
                    locator = ref.get("locator")
                    if source.get("locator_json"):
                        try:
                            locator = json.loads(source["locator_json"])
                        except (ValueError, TypeError):
                            locator = {"rawLocator": source["locator_json"]}
                            gaps.append(f"{metric}: evidence locator JSON is malformed")
                    item = {
                        "source": "ontology-attribute", "recordId": eid, "metric": metric, "attribute": attribute,
                        "sourceUri": source_path.as_uri() + "#evidence=" + quote(str(eid)),
                        "dataTimestamp": claim.get("observedAt"), "observedAt": claim.get("observedAt"),
                        "sourceRef": source.get("source_ref", ref.get("sourceRef")),
                        "sourceVersion": source.get("source_version", ref.get("sourceVersion")),
                        "contentHash": source.get("content_hash", ref.get("contentHash")),
                        "quote": source.get("quote", ref.get("quote")), "locator": locator,
                        "verificationStatus": source.get("verification_status", "unknown"),
                        "truthVerified": claim.get("truthVerified"), "reviewStatus": claim.get("reviewStatus"),
                        "conditions": claim.get("conditions"), "qualifier": claim.get("qualifier"),
                        "publishedAt": source.get("published_at"), "retrievedAt": source.get("retrieved_at"),
                        "importedAt": source.get("created_at"),
                        "provenance": {"mode": "ontology-sqlite", "entityId": entity_id, "revision": selected,
                                       "assertionId": claim.get("assertionId")},
                    }
                    if item["verificationStatus"] != "verified":
                        gaps.append(f"{metric}: evidence verification is incomplete")
                    if not item["sourceVersion"] or not item["contentHash"] or not item["locator"]:
                        gaps.append(f"{metric}: evidence provenance is incomplete")
                    evidence.append(item)
        for attribute, (metric, _) in METRICS.items():
            if attribute not in attributes:
                claims.setdefault(metric, {})
                gaps.append(f"{metric}: missing, conflicting or non-exact value")
        snapshot = {
            "mode": "ontology-sqlite", "sourceUri": source_path.as_uri(), "entityId": entity_id,
            "revision": selected, "entityRevisionId": row["entity_revision_id"],
            "selection": "current" if revision is None else "explicit-revision",
            "attributesSha256": row.get("attributes_sha256"), "serializedAttributesHash": hashlib.sha256(raw_json.encode("utf-8")).hexdigest(),
            "sourceSnapshotId": row.get("source_snapshot_id"), "reviewStatus": row.get("review_status"),
            "importedAt": row.get("created_at"), "attributesSchemaVersion": row.get("attributes_schema_version"),
            "extractedAt": datetime.now(timezone.utc).isoformat(),
        }
    gaps.append("flight/mission/energy/battery telemetry is not available from this ontology adapter")
    if snapshot["reviewStatus"] != "approved":
        gaps.append("entity revision review is not approved")
    frozen = {
        "uavId": entity_id, "entityId": entity_id, "requirements": requirements, "uavData": uav,
        "entity": entity, "identityMetadataScope": "current-entity-record; attributes belong to selected revision",
        "attributes": attributes, "revisionDocument": document,
        "sourceSnapshot": snapshot, "sourceClaims": claims, "sourceEvidence": evidence,
        "sourceGaps": list(dict.fromkeys(gaps)), "telemetry": {"status": "unknown", "records": []},
        "assumptions": ["Ontology-reported values support provisional comparison only; this is not an engineering certification."],
    }
    snapshot["extractionHash"] = hashlib.sha256(json.dumps(frozen, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()
    return frozen


class OntologyRuntimeTools:
    """The first successful extraction is reused for the remainder of a Run.

    The engine checkpoints the returned snapshot and the run-local cache. A
    source update never silently replaces an extracted revision during recovery.
    Explicit revision selection reads a distinct immutable source snapshot.
    """

    def __init__(self, repository, source_path=None):
        self.repository = repository
        self.source_path = Path(source_path).resolve() if source_path is not None else None

    def _source_path(self, ctx):
        if self.source_path is not None:
            return self.source_path.resolve(strict=True)
        policy = getattr(ctx.profile, "source_policy", {}) or {}
        source = policy.get("ontologySource", {})
        if not source:
            return DEFAULT_SOURCE.resolve(strict=True)
        if not isinstance(source, dict) or source.get("read_only") is not True or source.get("enabled") is not True:
            raise PermissionError("frozen ontology source must be enabled and read-only")
        if source.get("connector") not in {"sqlite", "sqlite-readonly", "ontology-sqlite"}:
            raise ValueError("ontology source connector must be read-only SQLite")
        root = source.get("root_uri")
        if not isinstance(root, str) or not root:
            raise ValueError("frozen ontology root_uri is missing")
        if root.startswith("file:"):
            parsed = urlsplit(root)
            if parsed.netloc or parsed.query or parsed.fragment:
                raise ValueError("ontology source requires a local file URI without query or fragment")
            path = Path(url2pathname(unquote(parsed.path)))
        else:
            path = Path(root)
        if not path.is_absolute():
            raise ValueError("ontology source path must be absolute")
        resolved = path.resolve(strict=True)
        if not resolved.is_file():
            raise ValueError("ontology source path must be an existing file")
        return resolved

    def _resolve(self, args, source_path):
        entity_id = args.get("entityId")
        query = args.get("query")
        limit = args.get("limit", 10)
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 20:
            raise ValueError("entity query limit must be 1..20")
        if not entity_id and (not isinstance(query, str) or not 1 <= len(query.strip()) <= 200):
            raise ValueError("entityId or bounded query is required")
        if entity_id and (not isinstance(entity_id, str) or len(entity_id) > 200):
            raise ValueError("entityId must be a bounded string")
        with closing(sqlite3.connect(source_path.as_uri() + "?mode=ro", uri=True)) as con:
            con.row_factory = sqlite3.Row
            con.execute("PRAGMA query_only=ON")
            if entity_id:
                rows = con.execute("SELECT * FROM ontology_entity WHERE entity_id=?", (entity_id,)).fetchall()
            else:
                term = query.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                rows = con.execute("""SELECT * FROM ontology_entity
                    WHERE preferred_name LIKE ? ESCAPE '\\' OR english_name LIKE ? ESCAPE '\\'
                    ORDER BY entity_id LIMIT ?""", ("%" + term + "%", "%" + term + "%", limit + 1)).fetchall()
        keys = ("entity_id", "preferred_name", "english_name", "entity_type", "current_revision", "review_status")
        matches = [{key: row[key] for key in keys if key in row.keys()} for row in rows[:limit]]
        return {"matches": matches, "ambiguous": len(rows) > 1, "truncated": len(rows) > limit,
                "sourceMode": "ontology-sqlite", "sourceUri": source_path.as_uri()}

    async def resolve_entity(self, payload, ctx):
        args = arguments(payload)
        if not args:
            args = {"entityId": ctx.input.get("entityId") or ctx.input.get("uavId")}
        return await asyncio.to_thread(self._resolve, args, self._source_path(ctx))

    async def get_entity(self, payload, ctx):
        args = arguments(payload)
        entity_id = args.get("entityId") or ctx.input.get("entityId") or ctx.input.get("uavId")
        if not isinstance(entity_id, str) or not entity_id or len(entity_id) > 200:
            raise ValueError("entityId is required")
        revision = args.get("revision")
        pin = ctx.input.get("entitySelection")
        if isinstance(pin, dict) and pin.get("entityId") == entity_id:
            if revision is not None and revision != pin.get("revision"):
                raise ValueError("requested revision differs from the selected task baseline")
            from .ontology_catalog import source_version_hash
            if pin.get("sourceVersionHash") != source_version_hash(ctx.profile, self._source_path(ctx)):
                raise ValueError("selected ontology source configuration changed")
            revision = pin.get("revision")
        if revision is not None and (not isinstance(revision, int) or isinstance(revision, bool) or revision < 1):
            raise ValueError("revision must be a positive integer")
        cache_key = entity_id if revision is None else f"{entity_id}::revision={revision}"
        def matches(result):
            snapshot = result.get("sourceSnapshot", {})
            return result.get("uavId") == entity_id and bool(snapshot) and (
                snapshot.get("revision") == revision if revision is not None else snapshot.get("selection", "current") == "current")
        cache = ctx.state.setdefault("ontologySnapshots", {})
        frozen = cache.get(cache_key)
        if frozen is None and revision is not None:
            frozen = next((item for item in cache.values() if matches(item)), None)
        if frozen is None and hasattr(self.repository, "list_artifacts"):
            # An artifact commit can precede the step-state checkpoint. Recover
            # that first extraction before reading a potentially newer source.
            for artifact in self.repository.list_artifacts(ctx.run_id):
                if artifact.get("artifact_type") != "ontology-source-snapshot":
                    continue
                content = artifact.get("content_json", {})
                if isinstance(content, str):
                    content = json.loads(content)
                if matches(content):
                    frozen = {**deepcopy(content), "artifactId": artifact["artifact_id"]}
                    break
        if frozen is None:
            # Completed step results can be restored even if an earlier engine
            # version checkpointed only the return value, without this cache.
            for step in ctx.state.get("steps", {}).values():
                result = step.get("toolResults", {}).get("ontology.get_entity", {})
                if matches(result):
                    frozen = deepcopy(result)
                    break
        if frozen is None:
            frozen = await asyncio.to_thread(_extract_revision, self._source_path(ctx), entity_id, deepcopy(ctx.input.get("requirements", {})), revision)
            if isinstance(pin, dict) and pin.get("entityId") == entity_id and (
                    frozen["sourceSnapshot"]["serializedAttributesHash"] != pin.get("revisionHash")
                    or frozen["sourceSnapshot"]["entityRevisionId"] != pin.get("entityRevisionId")):
                raise ValueError("selected ontology revision content changed")
            frozen_source = (getattr(ctx.profile, "source_policy", {}) or {}).get("ontologySource", {})
            if frozen_source:
                frozen["sourceSnapshot"]["sourcePolicyVersion"] = frozen_source.get("policy_version")
                frozen["sourceSnapshot"]["sourceKey"] = frozen_source.get("source_key")
            artifact_id = self.repository.add_artifact(ctx.run_id, "ontology-source-snapshot", frozen)
            frozen = {**frozen, "artifactId": artifact_id}
        if isinstance(pin, dict) and pin.get("entityId") == entity_id and (
                frozen["sourceSnapshot"]["serializedAttributesHash"] != pin.get("revisionHash")
                or frozen["sourceSnapshot"]["entityRevisionId"] != pin.get("entityRevisionId")):
            raise ValueError("selected ontology revision content changed")
        cache[cache_key] = deepcopy(frozen)
        return deepcopy(frozen)

    async def query_evidence(self, payload, ctx):
        args = arguments(payload)
        frozen = await self.get_entity(payload, ctx)
        evidence = frozen["sourceEvidence"]
        for argument_key, item_key in (("metrics", "metric"), ("attributes", "attribute"), ("evidenceIds", "recordId")):
            selected = args.get(argument_key)
            if selected is not None:
                if not isinstance(selected, list) or len(selected) > 100 or any(not isinstance(v, str) for v in selected):
                    raise ValueError(f"{argument_key} must be a bounded string array")
                evidence = [item for item in evidence if item.get(item_key) in selected]
        return {"entityId": frozen["uavId"], "sourceSnapshot": frozen["sourceSnapshot"],
                "artifactId": frozen["artifactId"], "evidence": evidence, "sourceGaps": frozen["sourceGaps"]}


def register_source_tools(engine, source_path=None, web_adapter=None):
    """Register implementations; bindings and versioned policy still authorize use."""
    from .web import FirecrawlRuntimeTools, WEB_SCHEMAS
    ontology = OntologyRuntimeTools(engine.repo, source_path)
    web = web_adapter or FirecrawlRuntimeTools(engine.repo)
    handlers = {
        "ontology.resolve_entity": ontology.resolve_entity, "ontology.get_entity": ontology.get_entity,
        "ontology.query_evidence": ontology.query_evidence, "web.search": web.search, "web.scrape": web.scrape,
    }
    for key, handler in handlers.items():
        engine.register_tool(key, handler, ENVELOPE_SCHEMA, {**SOURCE_SCHEMAS, **WEB_SCHEMAS}[key])
    return {"ontology": ontology, "web": web}
