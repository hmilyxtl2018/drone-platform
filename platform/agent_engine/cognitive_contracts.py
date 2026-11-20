"""Runtime guards for the proposed ontology-cognitive Agent suite.

This module is deliberately conservative.  JSON Schema validates shape while
the functions below validate the parts that shape alone cannot prove: frozen
versions, direct-parent artifact authority, hashes, graph endpoints and the
distinction between an unknown check and a passed check.  It never writes the
ontology and never turns a model explanation into a factual verification.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Iterable

from jsonschema import Draft202012Validator, FormatChecker


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "contracts" / "ontology-cognitive.v1.schema.json"
SCHEMA = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
FORMAT_CHECKER = FormatChecker()
AGENTS = frozenset({
    "ontology-cognitive-analyst", "situation-assessment-action",
    "rule-applicability-action", "tactics-research-action",
    "ontology-consistency-action",
})
PRODUCTS = frozenset({
    "SituationAssessment", "ConstraintAssessment", "ResearchOptionComparison",
    "ConsistencyReport", "CognitiveResearchReport", "OntologyChangeSetCandidate",
})


def digest(value: Any) -> str:
    """Use the same canonical JSON digest convention as Repository.add_artifact."""
    raw = json.dumps(value, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def schema(definition: str | None = None) -> dict[str, Any]:
    """Return the frozen suite schema or a schema that references one definition."""
    if definition is None:
        return deepcopy(SCHEMA)
    if definition not in SCHEMA.get("$defs", {}):
        raise ValueError(f"unknown ontology-cognitive contract: {definition}")
    return {"$schema": SCHEMA["$schema"], "$defs": SCHEMA["$defs"],
            "$ref": f"#/$defs/{definition}"}


def validate_schema(definition: str, value: Any) -> None:
    """Validate only structure; raise ValueError without echoing sensitive data."""
    validator = Draft202012Validator(schema(definition), format_checker=FORMAT_CHECKER)
    errors = sorted(validator.iter_errors(value), key=lambda error: list(error.absolute_path))
    if errors:
        error = errors[0]
        path = "/" + "/".join(str(part).replace("~", "~0").replace("/", "~1")
                                  for part in error.absolute_path)
        raise ValueError(f"cognitive-contract-invalid at {path or '/'}: {error.validator}")


def _parse_time(value: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError("timestamp must be ISO-8601")
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError("timestamp must be ISO-8601") from exc
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include timezone")
    return parsed.astimezone(timezone.utc)


def _ref_key(ref: dict[str, Any]) -> tuple[str, str, str, str]:
    return (str(ref.get("runId")), str(ref.get("artifactId")),
            str(ref.get("artifactType")), str(ref.get("contentHash")))


def validate_artifact_ref(ref: Any, allowed_refs: Iterable[dict[str, Any]] | None = None, *,
                         owner_run_id: str | None = None) -> dict[str, Any]:
    """Validate a reference and ensure it is in the server-provided allowlist."""
    validate_schema("ArtifactRef", ref)
    # None is an explicit schema-only call.  An empty list is an explicit
    # server authority manifest containing no allowed artifacts and therefore
    # denies every reference.
    if allowed_refs is not None:
        expected = {_ref_key(item) for item in allowed_refs if isinstance(item, dict)}
        if _ref_key(ref) not in expected:
            raise PermissionError("artifact reference is outside the delegated input allowlist")
    if owner_run_id is not None and ref["runId"] != owner_run_id:
        raise PermissionError("artifact reference is not owned by the delegated run")
    return deepcopy(ref)


def validate_context(context: Any, expected_context: dict[str, Any] | None = None) -> None:
    validate_schema("Context", context)
    scope = context["timeScope"]
    start, as_of, end = (_parse_time(scope[field]) for field in ("validFrom", "asOf", "validTo"))
    if not start <= as_of <= end:
        raise ValueError("context timeScope must satisfy validFrom <= asOf <= validTo")
    for key in ("ontologySnapshotRef", "ontologySchemaRef", "sourcePolicyRef"):
        if not context[key].get("contentHash"):
            raise ValueError(f"context {key} must be content-hash pinned")
    rule = context["ruleSetRef"]
    if rule.get("selection") == "selected" and not rule.get("snapshot", {}).get("contentHash"):
        raise ValueError("selected ruleSetRef must be hash pinned")
    if expected_context is not None:
        for key in ("researchPurpose", "scenarioRef", "ontologySnapshotRef", "ontologySchemaRef",
                    "ruleSetRef", "timeScope", "sourcePolicyRef", "inputArtifactRefs"):
            if context.get(key) != expected_context.get(key):
                raise ValueError(f"frozen context changed: {key}")


def _evidence_ids(product: dict[str, Any]) -> set[str]:
    return {item.get("evidenceId") for claim in product.get("claims", [])
            for item in claim.get("evidence", []) if isinstance(item, dict)}


def _check_graph(product: dict[str, Any]) -> None:
    view = product.get("ontologyView")
    if not view:
        return
    entities = {item["entityId"] for item in view.get("entities", [])}
    if len(entities) != len(view.get("entities", [])):
        raise ValueError("ontology entity identities must be unique")
    claims = {item["claimId"] for item in product.get("claims", [])}
    relation_ids = set()
    relations = view.get("relations", [])
    for relation in relations:
        if relation["relationId"] in relation_ids:
            raise ValueError("ontology relation identities must be unique")
        relation_ids.add(relation["relationId"])
        if relation["subjectId"] not in entities or relation["objectId"] not in entities:
            raise ValueError("ontology relation endpoint is not in the report graph")
        relation_claim_ids = set(relation.get("claimIds", []))
        if not relation_claim_ids <= claims:
            raise ValueError("ontology relation references an unknown claim")
        if not set(relation.get("conditionClaimIds", [])) <= claims:
            raise ValueError("ontology relation references an unknown condition claim")
        if relation.get("support") == "supported" and not relation_claim_ids:
            raise ValueError("supported ontology relation requires a supporting claim")
        for claim_id in relation_claim_ids:
            claim = next(item for item in product.get("claims", []) if item["claimId"] == claim_id)
            if claim.get("support") != relation.get("support"):
                raise ValueError("ontology relation support does not match its claim")
            if relation.get("support") == "supported" and any(
                    claim.get(key) != relation.get(key) for key in ("subjectId", "predicateId", "objectId")):
                raise ValueError("ontology relation does not match the bound supporting claim")
    for claim in product.get("claims", []):
        if claim.get("support") == "supported":
            if not claim.get("evidence"):
                raise ValueError("supported claim requires direct evidence")
            if not all(claim.get(key) for key in ("subjectId", "predicateId", "objectId")):
                raise ValueError("supported claim requires an ontology binding")
            if not any(relation.get("support") == "supported"
                       and claim["claimId"] in relation.get("claimIds", [])
                       and all(claim.get(key) == relation.get(key)
                               for key in ("subjectId", "predicateId", "objectId"))
                       for relation in relations):
                raise ValueError("supported claim is absent from its exact ontology relation")
    if any(claim.get("support") == "supported" for claim in product.get("claims", [])):
        if not view.get("entities") or not view.get("relations"):
            raise ValueError("supported claims require a nonempty ontology view")


def load_verified_artifact(ref: dict[str, Any], allowed_refs: Iterable[dict[str, Any]],
                           repository: Any) -> dict[str, Any]:
    """Resolve an explicitly authorized artifact and verify its stored bytes."""
    validate_artifact_ref(ref, allowed_refs)
    row = repository.get_artifact(ref["runId"], ref["artifactId"])
    if (not row or row.get("run_id") != ref["runId"] or row.get("artifact_id") != ref["artifactId"]
            or row.get("artifact_type") != ref["artifactType"] or row.get("content_hash") != ref["contentHash"]):
        raise PermissionError("artifact identity, hash or ownership does not match its reference")
    content = row.get("content_json")
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except ValueError:
            raise ValueError("artifact content is not valid JSON") from None
    if not isinstance(content, dict) or digest(content) != ref["contentHash"]:
        raise ValueError("artifact bytes do not match the immutable artifact fingerprint")
    return {**row, "content_json": content}


def _json_pointer(document: Any, pointer: str) -> Any:
    if not pointer.startswith("/"):
        raise ValueError("source locator must be a JSON pointer beginning with slash")
    current = document
    for token in pointer[1:].split("/"):
        token = token.replace("~1", "/").replace("~0", "~")
        if isinstance(current, list) and token.isdigit() and int(token) < len(current):
            current = current[int(token)]
        elif isinstance(current, dict) and token in current:
            current = current[token]
        else:
            raise ValueError("source locator does not resolve inside the artifact")
    return current


def verify_evidence(evidence: dict[str, Any], allowed_refs: Iterable[dict[str, Any]],
                    repository: Any) -> dict[str, Any]:
    """Check exact quotation and location against an eligible saved source."""
    validate_schema("Evidence", evidence)
    row = load_verified_artifact(evidence["sourceArtifactRef"], allowed_refs, repository)
    content = row["content_json"]
    kind = row["artifact_type"]
    if kind not in {"web-source-snapshot", "document-source-snapshot", "ontology-source-snapshot"}:
        raise ValueError("evidence requires an eligible immutable source snapshot")
    locator = evidence["locator"]
    if locator["kind"] == "json-pointer":
        permitted_paths = {"/markdown"} if kind == "web-source-snapshot" else {"/text"}
        if kind == "ontology-source-snapshot":
            if not re.fullmatch(r"/sourceEvidence/[0-9]+/quote", locator["value"]):
                raise ValueError("ontology source locator must identify an exact stored excerpt")
        elif locator["value"] not in permitted_paths:
            raise ValueError("source locator does not identify the saved source body")
        text = _json_pointer(content, locator["value"])
    else:
        body = content.get("markdown") if kind == "web-source-snapshot" else content.get("text")
        if not isinstance(body, str):
            raise ValueError("source has no full body for that locator type")
        if locator["kind"] == "fragment" and re.fullmatch(r"[0-9]+:[0-9]+", locator["value"]):
            start, end = map(int, locator["value"].split(":"))
            if not 0 <= start < end <= len(body):
                raise ValueError("source fragment offsets exceed the saved body")
            text = body[start:end]
        elif locator["kind"] == "line" and re.fullmatch(r"[1-9][0-9]*(?:-[1-9][0-9]*)?", locator["value"]):
            limits = list(map(int, locator["value"].split("-")))
            start, end = limits[0], limits[-1]
            lines = body.splitlines()
            if not 1 <= start <= end <= len(lines):
                raise ValueError("source line locator exceeds the saved body")
            text = "\n".join(lines[start - 1:end])
        else:
            raise ValueError("source locator kind is not supported by the installed resolver")
    if not isinstance(text, str) or evidence["quote"] not in text:
        raise ValueError("evidence quotation is absent from its exact source location")
    if content.get("retrievedAt") and evidence["retrievedAt"] != content["retrievedAt"]:
        raise ValueError("evidence retrieval time differs from the saved source")
    return deepcopy(evidence)


def _check_evidence(product: dict[str, Any], allowed_refs: Iterable[dict[str, Any]] | None = None,
                    repository: Any = None) -> None:
    allowed = list(allowed_refs) if allowed_refs is not None else None
    evidences = []
    for claim in product.get("claims", []):
        evidences.extend(claim.get("evidence", []))
    for conflict in product.get("conflicts", []):
        evidences.extend(conflict.get("evidence", []))
    for check in product.get("checks", []):
        evidences.extend(check.get("evidence", []))
    for finding in product.get("ruleFindings", []):
        evidences.extend(finding.get("evidence", []))
    for evidence in evidences:
        if not isinstance(evidence, dict):
            raise ValueError("evidence entries must be objects")
        validate_artifact_ref(evidence["sourceArtifactRef"], allowed)
        if not evidence.get("quote", "").strip() or not evidence.get("locator", {}).get("value"):
            raise ValueError("evidence must contain an exact quote and locator")
        if repository is not None:
            # Passing a repository means accepting the source semantically,
            # which requires an explicit allowlist, including when it is empty.
            if allowed is None:
                raise PermissionError("source acceptance requires an explicit artifact allowlist")
            verify_evidence(evidence, allowed, repository)
    for conflict in product.get("conflicts", []):
        if conflict.get("state") != "reconciled" and not conflict.get("evidence"):
            raise ValueError("unresolved conflict requires evidence")


def validate_product(product: Any, *, expected_context: dict[str, Any] | None = None,
                     allowed_refs: Iterable[dict[str, Any]] | None = None,
                     repository: Any = None) -> dict[str, Any]:
    """Validate a proposed typed payload and return a sanitized copy."""
    if not isinstance(product, dict):
        raise ValueError("cognitive product must be an object")
    product_type = product.get("productType")
    if product_type not in PRODUCTS:
        raise ValueError("unknown cognitive product type")
    validate_schema(product_type, product)
    validate_context(product["context"], expected_context)
    provenance = product["provenance"]
    if provenance.get("agentKey") not in AGENTS:
        raise ValueError("product producer is outside the cognitive suite")
    if provenance.get("budgetRef", {}).get("goalLineageId") != provenance.get("goalLineageId"):
        raise ValueError("product budget and provenance goal lineage differ")
    if provenance.get("budgetRef", {}).get("ownerId") != provenance.get("ownerId"):
        raise ValueError("product budget and provenance ownership differ")
    if provenance["agentKey"] != "ontology-cognitive-analyst" and provenance.get("parentGoalId") is None:
        raise ValueError("delegated cognitive Action requires its direct parent goal")
    for name, field in (("claims", "claimId"), ("checks", "checkId"), ("gaps", "gapId"), ("conflicts", "conflictId")):
        identities = [item[field] for item in product.get(name, [])]
        if len(identities) != len(set(identities)):
            raise ValueError(f"{name} identities must be unique")
    claim_ids = {item["claimId"] for item in product["claims"]}
    check_ids = {item["checkId"] for item in product["checks"]}
    scope = product["context"]["timeScope"]
    for claim in product["claims"]:
        if claim.get("validTime") is not None:
            period = claim["validTime"]
            start, as_of, end = (_parse_time(period[key]) for key in ("validFrom", "asOf", "validTo"))
            if not (_parse_time(scope["validFrom"]) <= start <= as_of <= end <= _parse_time(scope["validTo"])):
                raise ValueError("claim validity is outside the frozen task time scope")
    for entry in product.get("conflicts", []) + product.get("ruleFindings", []):
        if not set(entry.get("claimIds", [])) <= claim_ids or not set(entry.get("checkIds", [])) <= check_ids:
            raise ValueError("professional finding contains a dangling claim or check reference")
    _check_evidence(product, allowed_refs, repository)
    _check_graph(product)
    # A model explanation is never a passing deterministic check.
    for check in product.get("checks", []):
        if check.get("kind") == "model-explanation" and check.get("status") in {"pass", "fail"}:
            raise ValueError("model explanation cannot pass or fail a deterministic check")
        if check.get("applicability") == "unknown" and check.get("status") != "unknown":
            raise ValueError("unknown check applicability must remain unknown")
    return deepcopy(product)


def build_explanation_graph(report: dict[str, Any]) -> dict[str, Any]:
    """Project already supplied claim tuples into a report graph, without facts.

    New endpoints use their identifier as the label and an explicit unclassified
    type.  This projection never infers a domain entity type, merges identities,
    invents relations or upgrades support.  Caller must validate the resulting
    typed report and source evidence before accepting it.
    """
    view = deepcopy(report.get("ontologyView") or {"entities": [], "relations": []})
    entities = {item["entityId"]: item for item in view.get("entities", [])}
    relations = list(view.get("relations", []))
    for claim in report.get("claims", []):
        if not all(isinstance(claim.get(key), str) and claim[key]
                   for key in ("claimId", "subjectId", "predicateId", "objectId")):
            continue
        for entity in (claim["subjectId"], claim["objectId"]):
            if entity not in entities:
                entities[entity] = {"entityId": entity, "label": entity,
                                    "typeId": "ontology:UnclassifiedResearchEntity", "claimIds": [claim["claimId"]]}
            elif claim["claimId"] not in entities[entity].get("claimIds", []):
                entities[entity].setdefault("claimIds", []).append(claim["claimId"])
        matching = next((relation for relation in relations
                         if all(relation.get(field) == claim.get(field)
                                for field in ("subjectId", "predicateId", "objectId", "support"))), None)
        if matching is None:
            relations.append({"relationId": "relation-" + digest({key: claim[key] for key in
                              ("claimId", "subjectId", "predicateId", "objectId")})[:16],
                              "subjectId": claim["subjectId"], "predicateId": claim["predicateId"],
                              "objectId": claim["objectId"], "claimIds": [claim["claimId"]],
                              "support": claim.get("support", "unknown"), "conditionClaimIds": []})
        elif claim["claimId"] not in matching.get("claimIds", []):
            matching.setdefault("claimIds", []).append(claim["claimId"])
    return {"entities": list(entities.values()), "relations": relations}


def validate_task_input(value: Any) -> None:
    validate_schema("TaskInput", value)
    validate_context(value["context"])
    for ref in value.get("sourceArtifactRefs", []) + value.get("capabilityArtifactRefs", []):
        validate_artifact_ref(ref, None)


def product_base(product_type: str, context: dict[str, Any], provenance: dict[str, Any]) -> dict[str, Any]:
    if product_type not in PRODUCTS:
        raise ValueError("unknown cognitive product type")
    validate_context(context)
    return {"schemaVersion": "ontology-cognitive.v1", "state": "proposed",
            "context": deepcopy(context), "provenance": deepcopy(provenance),
            "claims": [], "gaps": [], "conflicts": [], "checks": [],
            "productType": product_type}


def partial_gap(kind: str, description: str, impact: str, next_step: str,
                affected_claim_ids: Iterable[str] = ()) -> dict[str, Any]:
    return {"gapId": "gap-" + hashlib.sha256(description.encode()).hexdigest()[:16],
            "kind": kind, "description": description, "impact": impact,
            "affectedClaimIds": sorted(set(affected_claim_ids)), "suggestedNextStep": next_step}


def authorize_artifacts(ctx: Any, refs: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Use only an authority manifest placed by the server, never user input."""
    authority = (getattr(ctx, "state", {}) or {}).get("_cognitiveAuthority", {})
    allowed = authority.get("artifactRefs", []) if isinstance(authority, dict) else []
    allowed_keys = {_ref_key(ref) for ref in allowed if isinstance(ref, dict)}
    result = []
    for ref in refs:
        validate_artifact_ref(ref, allowed)
        if _ref_key(ref) not in allowed_keys:
            raise PermissionError("artifact reference is not server-authorized")
        result.append(deepcopy(ref))
    return result

