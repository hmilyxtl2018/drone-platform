"""Deterministic, read-only research tools for the ontology cognitive suite."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from typing import Any

from jsonschema import validate

from agent_engine.cognitive_contracts import (
    AGENTS, digest, partial_gap, product_base, validate_product, validate_schema,
    authorize_artifacts, load_verified_artifact, verify_evidence, build_explanation_graph,
)

VERSION = "1.0.0"
TOOL_KEYS = (
    "ontology.map_candidates", "artifact.read_capability",
    "analysis.explanation_graph", "analysis.report_contract_validate",
    "analysis.temporal_align", "analysis.rule_effectivity",
    "analysis.constraint_evaluate", "analysis.comparison_validate",
    "analysis.temporal_validate", "ontology.shape_validate",
    "ontology.relation_validate",
)
TEXT = {"type": "string", "minLength": 1, "maxLength": 12000}
REF = {"type": "object", "required": ["runId", "artifactId", "artifactType", "contentHash"],
       "properties": {"runId": TEXT, "artifactId": TEXT, "artifactType": TEXT,
                      "contentHash": {"type": "string", "pattern": "^[a-f0-9]{64}$"}},
       "additionalProperties": False}
ARRAY = {"type": "array", "maxItems": 500, "items": {"type": "object"}}
GRAPH = {"type": "object"}
ARGUMENT_SCHEMAS = {
    "ontology.map_candidates": {"type": "object", "required": ["claims"], "properties": {"claims": ARRAY}, "additionalProperties": False},
    "artifact.read_capability": {"type": "object", "required": ["artifactRefs"], "properties": {
        "artifactRefs": {"type": "array", "maxItems": 100, "items": REF}}, "additionalProperties": False},
    "analysis.explanation_graph": {"type": "object", "required": ["report"], "properties": {"report": GRAPH}, "additionalProperties": False},
    "analysis.report_contract_validate": {"type": "object", "required": ["report"], "properties": {"report": GRAPH}, "additionalProperties": False},
    "analysis.temporal_align": {"type": "object", "required": ["observations"], "properties": {"observations": ARRAY}, "additionalProperties": False},
    "analysis.rule_effectivity": {"type": "object", "required": ["rules"], "properties": {"rules": ARRAY,
        "asOf": {"type": "string", "format": "date-time"}}, "additionalProperties": False},
    "analysis.constraint_evaluate": {"type": "object", "required": ["checks"], "properties": {"checks": ARRAY}, "additionalProperties": False},
    "analysis.comparison_validate": {"type": "object", "required": ["criteria", "options"], "properties": {"criteria": ARRAY,"options": ARRAY}, "additionalProperties": False},
    "analysis.temporal_validate": {"type": "object", "required": ["records"], "properties": {"records": ARRAY}, "additionalProperties": False},
    "ontology.shape_validate": {"type": "object", "required": ["graph"], "properties": {"graph": GRAPH}, "additionalProperties": False},
    "ontology.relation_validate": {"type": "object", "required": ["graph"], "properties": {"graph": GRAPH}, "additionalProperties": False},
}
GENERIC_OUTPUT = {"type": "object", "required": ["toolKey", "version", "deterministic", "truthVerified", "gaps"],
                  "properties": {"toolKey": TEXT, "version": TEXT, "deterministic": {"const": True},
                                 "truthVerified": {"const": False}, "gaps": {"type": "array"}},
                  "additionalProperties": True}
TOOL_SCHEMAS = {key: ({"oneOf": [
    {"type": "object", "required": ["arguments"], "properties": {"arguments": arguments}, "additionalProperties": False},
    {"type": "object", "required": ["input", "state"], "properties": {"input": arguments, "state": {"type": "object"}}, "additionalProperties": False}
    ]}, deepcopy(GENERIC_OUTPUT))
    for key, arguments in ARGUMENT_SCHEMAS.items()}


def tool_schema(key):
    if key not in TOOL_SCHEMAS:
        raise ValueError("unknown cognitive tool")
    return deepcopy(TOOL_SCHEMAS[key])


def _args(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("cognitive tool input must be an object")
    value = payload.get("arguments")
    if value is None:
        value = payload.get("input", payload)
    if not isinstance(value, dict):
        raise ValueError("cognitive tool arguments must be an object")
    return deepcopy(value)


def _out(key: str, **fields: Any) -> dict[str, Any]:
    return {"toolKey": key, "version": VERSION, "deterministic": True,
            "truthVerified": False, "gaps": [], **fields}


def _refs(args: dict[str, Any], ctx: Any) -> list[dict[str, Any]]:
    refs = args.get("artifactRefs", args.get("inputArtifactRefs", []))
    if not isinstance(refs, list):
        raise ValueError("artifactRefs must be an array")
    return authorize_artifacts(ctx, refs)


def _temporal(value: Any) -> tuple[datetime, datetime, datetime] | None:
    if not isinstance(value, dict):
        return None
    try:
        parsed = []
        for key in ("validFrom", "asOf", "validTo"):
            item = value.get(key)
            if not isinstance(item, str):
                return None
            item = item[:-1] + "+00:00" if item.endswith("Z") else item
            date = datetime.fromisoformat(item)
            if date.tzinfo is None:
                return None
            parsed.append(date)
        if not parsed[0] <= parsed[1] <= parsed[2]:
            return None
        return tuple(parsed)  # type: ignore[return-value]
    except (TypeError, ValueError):
        return None


def evaluate(key: str, args: dict[str, Any], *, ctx: Any = None, repository: Any = None) -> dict[str, Any]:
    if key not in TOOL_KEYS:
        raise ValueError("unknown cognitive tool")
    args = deepcopy(args or {})
    validate(args, ARGUMENT_SCHEMAS[key])
    gaps: list[dict[str, Any]] = []
    if key == "ontology.map_candidates":
        claims = args.get("claims", [])
        if not isinstance(claims, list):
            raise ValueError("claims must be an array")
        changes = []
        for claim in claims:
            if not isinstance(claim, dict):
                gaps.append(partial_gap("unsupported-relation", "Malformed claim retained as mapping gap.",
                                        "No ontology relation was inferred.", "Review the source claim."))
                continue
            subject, predicate, obj = claim.get("subjectId"), claim.get("predicateId"), claim.get("objectId")
            if not all(isinstance(x, str) and x for x in (subject, predicate, obj)):
                gaps.append(partial_gap("unsupported-relation", "Claim has no complete entity-relation-object tuple.",
                                        "Candidate mapping remains unresolved.", "Provide explicit ontology identifiers."))
                continue
            changes.append({"changeId": "map-" + hashlib.sha256(json.dumps(claim, sort_keys=True).encode()).hexdigest()[:16],
                            "operation": "propose-relation", "reason": "Evidence-linked candidate mapping only.",
                            "payload": {"relationId": "relation-" + hashlib.sha256(json.dumps(claim, sort_keys=True).encode()).hexdigest()[:16],
                                        "subjectId": subject, "predicateId": predicate, "objectId": obj,
                                        "claimIds": [claim.get("claimId")] if claim.get("claimId") else [],
                                        "support": claim.get("support", "unknown"), "conditionClaimIds": []}})
        if not claims:
            gaps.append(partial_gap("missing-source", "No claims were supplied for ontology mapping.",
                                    "No candidate relation can be proposed.", "Collect authorized source claims."))
        return _out(key, changes=changes, publicationState="not-published", gaps=gaps)
    if key == "artifact.read_capability":
        if ctx is None:
            raise PermissionError("capability artifact reads require a server context")
        refs = _refs(args, ctx)
        artifacts = []
        for ref in refs:
            if repository is None or not hasattr(repository, "get_artifact"):
                gaps.append(partial_gap("missing-capability-artifact", "Capability artifact store is unavailable.",
                                        "No capability evidence was read.", "Provide an authorized artifact store."))
                continue
            artifact = load_verified_artifact(ref, refs, repository)
            content = artifact.get("content_json")
            if not isinstance(content, dict):
                gaps.append(partial_gap("missing-capability-artifact", "Capability artifact payload is unreadable.",
                                        "Capability evidence remains unknown.", "Review the producing run artifact."))
                continue
            # Reuse is purpose/configuration/time-scoped.  An authorized blob
            # cannot silently become capability evidence for an unrelated task.
            expected = (getattr(ctx, "state", {}) or {}).get("_cognitiveContext", {})
            capability_scope = content.get("context", {})
            scope_match = isinstance(capability_scope, dict) and all(
                capability_scope.get(field) == expected.get(field)
                for field in ("researchPurpose", "scenarioRef", "timeScope"))
            if not scope_match:
                gaps.append(partial_gap("missing-capability-artifact", "Capability artifact purpose, scenario or time does not match.",
                                        "The artifact was not admitted as evidence for this task.", "Provide a purpose-compatible capability assessment."))
                continue
            artifacts.append({"artifactRef": ref, "content": content, "truthVerified": False,
                              "sourceStatus": "candidate-artifact"})
        if not artifacts and not gaps:
            gaps.append(partial_gap("missing-capability-artifact", "No authorized capability artifacts were supplied.",
                                    "Capability branch remains unknown.", "Run or provide an authorized capability assessment."))
        return _out(key, capabilityArtifacts=artifacts, gaps=gaps)
    if key == "analysis.temporal_align":
        observations = args.get("observations", [])
        if not isinstance(observations, list): raise ValueError("observations must be an array")
        aligned, unknown = [], 0
        for observation in observations:
            if not isinstance(observation, dict):
                unknown += 1; continue
            period = observation.get("validTime") or observation.get("timeScope")
            if _temporal(period): aligned.append(deepcopy(observation))
            else:
                unknown += 1
                item = deepcopy(observation); item["state"] = "unknown"; aligned.append(item)
        if unknown:
            gaps.append(partial_gap("insufficient-coverage", "Some observations have no valid time scope.",
                                    "Those observations cannot support a time-bounded comparison.", "Supply effective and observation times."))
        return _out(key, observations=aligned, unknownCount=unknown, gaps=gaps)
    if key == "analysis.rule_effectivity":
        rules = args.get("rules", [])
        if not isinstance(rules, list): raise ValueError("rules must be an array")
        findings=[]
        for rule in rules:
            period = rule.get("effectivePeriod") if isinstance(rule, dict) else None
            # Verified source authority is a server validation result; a model
            # writing the word in its arguments never establishes authority.
            authorities = ((getattr(ctx, "state", {}) or {}).get("_cognitiveAuthority", {}).get("ruleAuthorities", {})
                           if ctx is not None else {})
            authority = authorities.get(rule.get("ruleId"), {}).get("status", "unknown") if isinstance(rule, dict) else "unknown"
            version = rule.get("ruleVersion") if isinstance(rule, dict) else None
            if not _temporal(period) or authority != "verified-authoritative" or not version:
                findings.append({"rule": deepcopy(rule), "applicability": "unknown", "reason": "authority-version-or-period-unknown"})
                gaps.append(partial_gap("unknown-rule-applicability", "Rule authority, version or effective period is unverified.",
                                        "Rule cannot be treated as applicable.", "Verify the published rule metadata."))
            else: findings.append({"rule": deepcopy(rule), "applicability": "applicable"})
        return _out(key, ruleFindings=findings, gaps=gaps)
    if key == "analysis.constraint_evaluate":
        checks=[]
        for check in args["checks"]:
            executor = check.get("executor")
            record = check.get("record", {})
            result, kind = None, "rule-applicability"
            # Closed metadata check registry: it validates document metadata,
            # never the permissibility of a tactical or operational action.
            if executor == "cognitive.rule.metadata-completeness.v1":
                kind = "schema"
                result = isinstance(record, dict) and all(record.get(field) for field in ("ruleId", "ruleVersion", "sourceArtifactRef"))
            elif executor == "cognitive.rule.effective-window.v1":
                kind = "temporal-validity"
                period = _temporal(record.get("effectivePeriod")) if isinstance(record, dict) else None
                if period is not None:
                    result = period[0] <= period[1] <= period[2]
            elif executor == "cognitive.rule.source-ref-integrity.v1":
                kind = "reference-integrity"
                if ctx is not None and repository is not None and isinstance(record, dict) and record.get("sourceArtifactRef"):
                    authorized = authorize_artifacts(ctx, [record["sourceArtifactRef"]])
                    load_verified_artifact(record["sourceArtifactRef"], authorized, repository)
                    result = True
            status = "pass" if result is True else "fail" if result is False else "unknown"
            item = {"checkId": check.get("checkId", "check-unknown"), "kind": kind,
                    "applicability": "applicable" if result is not None else "unknown", "status": status,
                    "method": {"kind": "deterministic" if result is not None else "not-executed",
                               "executor": executor or "unregistered", "version": VERSION},
                    "explanation": "Installed document-metadata checker result; no authority to act." if result is not None else "Checker or required metadata is unavailable; result remains unknown.",
                    "evidence": deepcopy(check.get("evidence", []))}
            checks.append(item)
        if not checks:
            gaps.append(partial_gap("unknown-rule-applicability", "No installed deterministic rule checker was supplied.",
                                    "Rule checks remain unknown.", "Install and authorize a versioned checker."))
        return _out(key, checks=checks, gaps=gaps)
    if key == "analysis.comparison_validate":
        criteria = args.get("criteria", [])
        options = args.get("options", [])
        if not isinstance(criteria, list) or not isinstance(options, list): raise ValueError("criteria/options must be arrays")
        if len(options) < 2: gaps.append(partial_gap("insufficient-coverage", "Fewer than two research options are available.", "No comparative conclusion is justified.", "Provide a second evidence-bounded option."))
        missing = [item.get("optionId", "unknown") for item in options if not item.get("assessments")]
        if missing: gaps.append(partial_gap("insufficient-coverage", "Options lack complete criterion assessments.", "Options cannot be ranked.", "Supply evidence for each common criterion.", missing))
        return _out(key, comparisonState="evidence-bounded" if len(options)>=2 and not missing else "insufficient-evidence", criteria=deepcopy(criteria), options=deepcopy(options), gaps=gaps)
    if key in {"analysis.temporal_validate", "ontology.shape_validate", "ontology.relation_validate"}:
        records = args.get("records", args.get("graph", {}))
        failures, performed = [], 0
        if key == "analysis.temporal_validate":
            for item in records:
                if item.get("validTime") is None:
                    continue
                performed += 1
                if not _temporal(item.get("validTime")):
                    failures.append({"reason":"invalid-time-scope"})
        elif key == "ontology.shape_validate":
            try:
                validate_schema("OntologyView", records)
                performed = len(records.get("entities", [])) + len(records.get("relations", []))
            except ValueError:
                failures.append({"reason":"ontology-view-schema-invalid"})
                performed = 1
        else:
            entities={e.get("entityId") for e in records.get("entities",[]) if isinstance(e,dict)} if isinstance(records,dict) else set()
            for relation in records.get("relations",[]) if isinstance(records,dict) else []:
                performed += 1
                if relation.get("subjectId") not in entities or relation.get("objectId") not in entities:
                    failures.append({"reason":"relation-endpoint-missing"})
        status = "fail" if failures else "pass" if performed else "unknown"
        if not performed and not failures:
            gaps.append(partial_gap("insufficient-coverage", "No compatible records were available for this installed check.",
                                    "This check did not establish consistency.", "Provide time-scoped records or a typed nonempty graph."))
        return _out(key, checks=[{"status": status, "failures": failures, "performedCount": performed}], gaps=gaps)
    if key == "analysis.explanation_graph":
        report=deepcopy(args.get("report", {}))
        if not isinstance(report,dict): raise ValueError("report must be an object")
        return _out(key, ontologyView=build_explanation_graph(report), claims=deepcopy(report.get("claims",[])), gaps=gaps)
    if key == "analysis.report_contract_validate":
        report=args.get("report")
        if not isinstance(report,dict): raise ValueError("report must be an object")
        validate_product(report, expected_context=(getattr(ctx,"state",{}) or {}).get("_cognitiveContext") if ctx else None,
                         allowed_refs=(getattr(ctx,"state",{}) or {}).get("_cognitiveAuthority",{}).get("artifactRefs",[]) if ctx else [],
                         repository=repository)
        return _out(key, valid=True, gaps=[])
    raise ValueError("unhandled cognitive tool")


class CognitiveTools:
    def __init__(self, repository): self.repository = repository
    async def invoke(self, key: str, payload: dict[str, Any], ctx: Any) -> dict[str, Any]:
        result = evaluate(key, _args(payload), ctx=ctx, repository=self.repository)
        artifact = self.repository.add_artifact(ctx.run_id, "cognitive-tool-result", result)
        return {**result, "artifactId": artifact, "artifactHash": digest(result)}


def register_cognitive_tools(engine):
    runtime = CognitiveTools(engine.repo)
    for key, (input_schema, output_schema) in TOOL_SCHEMAS.items():
        async def handler(payload, ctx, key=key):
            return await runtime.invoke(key, payload, ctx)
        engine.register_tool(key, handler, deepcopy(input_schema), deepcopy(output_schema))
    return runtime

