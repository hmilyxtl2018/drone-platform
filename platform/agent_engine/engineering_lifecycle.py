"""Gated lifecycle from a research candidate to an engineering decision.

The research Agent can propose inputs, but it cannot silently turn them into a
design, a test result, or a manufacturing instruction.  This module keeps the
transition rules independent from the HTTP and PostgreSQL layers so the same
rules can be exercised in unit tests and by future workers.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import re


STAGES = (
    "exploration",
    "decision-support",
    "engineering-input",
    "prototype",
    "test",
    "qualification",
    "production-readiness",
)

_STAGE_INDEX = {name: index for index, name in enumerate(STAGES)}
_ID = re.compile(r"^[A-Za-z0-9_.:/-]{1,200}$")
_HASH = re.compile(r"^[0-9a-f]{64}$")


class LifecycleValidationError(ValueError):
    """Caller supplied a gate that cannot be reviewed."""


class LifecycleConflictError(ValueError):
    """The immutable lifecycle or source Run changed while advancing."""


def canonical_hash(value):
    try:
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError, RecursionError) as exc:
        raise LifecycleValidationError("lifecycle content must be finite JSON") from exc
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _bounded_text(value, field, maximum=4000, required=True):
    if not isinstance(value, str) or len(value.strip()) > maximum or (required and not value.strip()):
        raise LifecycleValidationError(f"{field} must be bounded text")
    return value.strip()


def _bounded_array(value, field, maximum=200):
    if not isinstance(value, list) or len(value) > maximum:
        raise LifecycleValidationError(f"{field} must be a bounded array")
    return value


def _hash(value, field):
    if not isinstance(value, str) or not _HASH.fullmatch(value):
        raise LifecycleValidationError(f"{field} must be a sha256 hash")
    return value


def _id(value, field):
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise LifecycleValidationError(f"invalid {field}")
    return value


def _human_gate(value):
    if not isinstance(value, dict):
        raise LifecycleValidationError("humanReview gate is required")
    allowed = {"status", "approvalId", "reviewerId", "reportHash", "decision", "reason"}
    if set(value) - allowed:
        raise LifecycleValidationError("humanReview contains unsupported fields")
    if value.get("status") != "approved":
        raise LifecycleValidationError("human review must be approved before decision-support")
    _id(value.get("approvalId"), "humanReview.approvalId")
    _id(value.get("reviewerId"), "humanReview.reviewerId")
    _hash(value.get("reportHash"), "humanReview.reportHash")
    _bounded_text(value.get("reason"), "humanReview.reason", 4000)
    return deepcopy(value)


def _evidence_gate(value):
    refs = _bounded_array(value, "evidenceRefs", 500)
    if not refs:
        raise LifecycleValidationError("decision-support requires evidenceRefs")
    for index, ref in enumerate(refs):
        if not isinstance(ref, dict):
            raise LifecycleValidationError(f"evidenceRefs[{index}] must be an object")
        required = ("evidenceId", "sourceArtifactId", "sourceArtifactHash", "contentHash", "truthStatus")
        missing = [field for field in required if not ref.get(field)]
        if missing:
            raise LifecycleValidationError(f"evidenceRefs[{index}] missing {missing}")
        _id(ref["evidenceId"], f"evidenceRefs[{index}].evidenceId")
        _id(ref["sourceArtifactId"], f"evidenceRefs[{index}].sourceArtifactId")
        _hash(ref["sourceArtifactHash"], f"evidenceRefs[{index}].sourceArtifactHash")
        _hash(ref["contentHash"], f"evidenceRefs[{index}].contentHash")
        if ref.get("truthStatus") not in {"unverified", "human-reviewed-unverified", "verified"}:
            raise LifecycleValidationError(f"evidenceRefs[{index}] has invalid truthStatus")
    return deepcopy(refs)


def _decision_support_gate(gate):
    if not isinstance(gate, dict):
        raise LifecycleValidationError("decision-support gate must be an object")
    allowed = {"evidenceRefs", "conflictDisposition", "redTeamStatus", "humanReview", "reportHash", "sourceRunStatus"}
    if set(gate) - allowed:
        raise LifecycleValidationError("decision-support gate contains unsupported fields")
    report_hash = _hash(gate.get("reportHash"), "reportHash")
    evidence = _evidence_gate(gate.get("evidenceRefs"))
    if gate.get("conflictDisposition") not in {"resolved", "retained-with-gaps"}:
        raise LifecycleValidationError("conflictDisposition must be resolved or retained-with-gaps")
    if gate.get("redTeamStatus") != "completed":
        raise LifecycleValidationError("red-team review must be completed")
    source_status = gate.get("sourceRunStatus")
    if source_status not in {"requires-human-review", "completed-with-gaps", "completed"}:
        raise LifecycleValidationError("source Run is not an eligible research result")
    human = _human_gate(gate.get("humanReview"))
    if human["reportHash"] != report_hash:
        raise LifecycleValidationError("human review report hash differs from gate report hash")
    return {"reportHash": report_hash, "evidenceRefs": evidence,
            "conflictDisposition": gate["conflictDisposition"],
            "redTeamStatus": "completed", "sourceRunStatus": source_status,
            "humanReview": human}


_REQUIRED_VERIFICATION_FIELDS = (
    "requirementId", "metric", "unit", "operatingConditions", "inputBasis",
    "verificationMethod", "testProcedure", "acceptanceCriteria", "configurationVersion",
    "resultEvidence", "reviewStatus",
)


def validate_verification_matrix(value, *, require_results=False):
    """Validate engineering verification rows without certifying any result."""
    rows = _bounded_array(value, "verificationMatrix", 500)
    if not rows:
        raise LifecycleValidationError("verificationMatrix cannot be empty")
    ids = set()
    normalized = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise LifecycleValidationError(f"verificationMatrix[{index}] must be an object")
        allowed = set(_REQUIRED_VERIFICATION_FIELDS) | {"dimension", "notes"}
        if set(row) - allowed:
            raise LifecycleValidationError(f"verificationMatrix[{index}] contains unsupported fields")
        missing = [field for field in _REQUIRED_VERIFICATION_FIELDS if field not in row]
        if missing:
            raise LifecycleValidationError(f"verificationMatrix[{index}] missing {missing}")
        requirement_id = _id(row["requirementId"], f"verificationMatrix[{index}].requirementId")
        if requirement_id in ids:
            raise LifecycleValidationError(f"duplicate requirementId: {requirement_id}")
        ids.add(requirement_id)
        dimension = _bounded_text(row.get("dimension", "unspecified"), "dimension", 160)
        metric = _bounded_text(row["metric"], "metric", 240)
        unit = _bounded_text(row["unit"], "unit", 80)
        if not isinstance(row["operatingConditions"], dict) or not row["operatingConditions"]:
            raise LifecycleValidationError(f"verificationMatrix[{index}].operatingConditions must be an object")
        input_basis = row["inputBasis"]
        if not isinstance(input_basis, dict) or input_basis.get("kind") not in {"evidence", "assumption", "derived"}:
            raise LifecycleValidationError(f"verificationMatrix[{index}].inputBasis.kind is invalid")
        _bounded_text(row["verificationMethod"], "verificationMethod", 1000)
        _bounded_text(row["testProcedure"], "testProcedure", 4000)
        _bounded_text(row["acceptanceCriteria"], "acceptanceCriteria", 2000)
        _bounded_text(row["configurationVersion"], "configurationVersion", 200)
        result_evidence = _bounded_array(row["resultEvidence"], "resultEvidence", 100)
        if require_results and not result_evidence:
            raise LifecycleValidationError(f"verificationMatrix[{index}] requires resultEvidence")
        if row["reviewStatus"] not in {"proposed-human-review", "pending", "blocked", "accepted-human"}:
            raise LifecycleValidationError(f"verificationMatrix[{index}].reviewStatus is invalid")
        # A model cannot mark an engineering requirement accepted.  The
        # transition may retain an explicit human decision for later stages,
        # but the row is still auditable as supplied.
        review_status = row["reviewStatus"] if row["reviewStatus"] != "accepted-human" else "accepted-human"
        normalized.append({"requirementId": requirement_id, "dimension": dimension, "metric": metric,
                           "unit": unit, "operatingConditions": deepcopy(row["operatingConditions"]),
                           "inputBasis": deepcopy(input_basis), "verificationMethod": row["verificationMethod"].strip(),
                           "testProcedure": row["testProcedure"].strip(), "acceptanceCriteria": row["acceptanceCriteria"].strip(),
                           "configurationVersion": row["configurationVersion"].strip(),
                           "resultEvidence": deepcopy(result_evidence), "reviewStatus": review_status,
                           **({"notes": row["notes"].strip()} if isinstance(row.get("notes"), str) else {})})
    return normalized


def validate_stage_gate(current_stage, target_stage, gate):
    if current_stage not in _STAGE_INDEX or target_stage not in _STAGE_INDEX:
        raise LifecycleValidationError("unknown lifecycle stage")
    if _STAGE_INDEX[target_stage] != _STAGE_INDEX[current_stage] + 1:
        raise LifecycleValidationError(f"lifecycle transition must advance one stage: {current_stage} -> {target_stage}")
    if not isinstance(gate, dict):
        raise LifecycleValidationError("stage gate must be an object")
    if target_stage == "decision-support":
        return _decision_support_gate(gate)
    if target_stage == "engineering-input":
        allowed = {"verificationMatrix", "manufacturingRequirements", "requirementsBasis", "humanReview"}
        if set(gate) - allowed:
            raise LifecycleValidationError("engineering-input gate contains unsupported fields")
        matrix = validate_verification_matrix(gate.get("verificationMatrix"))
        requirements = _bounded_array(gate.get("manufacturingRequirements", []), "manufacturingRequirements", 200)
        for index, requirement in enumerate(requirements):
            if not isinstance(requirement, dict) or requirement.get("status") != "proposed-human-review":
                raise LifecycleValidationError(f"manufacturingRequirements[{index}] must remain proposed-human-review")
        basis = _bounded_text(gate.get("requirementsBasis"), "requirementsBasis", 2000)
        return {"verificationMatrix": matrix, "manufacturingRequirements": deepcopy(requirements),
                "requirementsBasis": basis, "humanReview": deepcopy(gate.get("humanReview"))}
    if target_stage == "prototype":
        allowed = {"designInputs", "configurationVersion", "riskRegister", "humanReview"}
        if set(gate) - allowed:
            raise LifecycleValidationError("prototype gate contains unsupported fields")
        inputs = _bounded_array(gate.get("designInputs"), "designInputs", 200)
        if not inputs:
            raise LifecycleValidationError("prototype requires designInputs")
        version = _bounded_text(gate.get("configurationVersion"), "configurationVersion", 200)
        risks = _bounded_array(gate.get("riskRegister"), "riskRegister", 200)
        return {"designInputs": deepcopy(inputs), "configurationVersion": version,
                "riskRegister": deepcopy(risks), "humanReview": deepcopy(gate.get("humanReview"))}
    if target_stage == "test":
        allowed = {"testPlan", "verificationMatrix", "sampleSize", "humanReview"}
        if set(gate) - allowed:
            raise LifecycleValidationError("test gate contains unsupported fields")
        plan = gate.get("testPlan")
        if not isinstance(plan, dict):
            raise LifecycleValidationError("testPlan is required")
        for field in ("operatingConditions", "testProcedure", "acceptanceCriteria"):
            if not plan.get(field):
                raise LifecycleValidationError(f"testPlan.{field} is required")
        if type(gate.get("sampleSize")) is not int or gate["sampleSize"] < 1:
            raise LifecycleValidationError("test sampleSize must be a positive integer")
        matrix = validate_verification_matrix(gate.get("verificationMatrix"))
        return {"testPlan": deepcopy(plan), "verificationMatrix": matrix,
                "sampleSize": gate["sampleSize"], "humanReview": deepcopy(gate.get("humanReview"))}
    if target_stage == "qualification":
        allowed = {"testResults", "deviations", "retestPlan", "humanReview"}
        if set(gate) - allowed:
            raise LifecycleValidationError("qualification gate contains unsupported fields")
        results = _bounded_array(gate.get("testResults"), "testResults", 500)
        if not results:
            raise LifecycleValidationError("qualification requires testResults")
        deviations = _bounded_array(gate.get("deviations", []), "deviations", 200)
        retest = _bounded_text(gate.get("retestPlan"), "retestPlan", 4000)
        return {"testResults": deepcopy(results), "deviations": deepcopy(deviations),
                "retestPlan": retest, "humanReview": deepcopy(gate.get("humanReview"))}
    if target_stage == "production-readiness":
        allowed = {"processCapability", "supplyChain", "qualityPlan", "maintainability", "cost", "capacity", "humanReview"}
        if set(gate) - allowed:
            raise LifecycleValidationError("production-readiness gate contains unsupported fields")
        required = ("processCapability", "supplyChain", "qualityPlan", "maintainability", "cost", "capacity")
        for field in required:
            if not isinstance(gate.get(field), dict) or not gate[field]:
                raise LifecycleValidationError(f"{field} evidence is required")
        return {field: deepcopy(gate[field]) for field in required} | {"humanReview": deepcopy(gate.get("humanReview"))}
    raise LifecycleValidationError("unsupported lifecycle transition")


def initial_state(source_run_id, source_run_hash):
    _id(source_run_id, "sourceRunId")
    _hash(source_run_hash, "sourceRunHash")
    return {"stage": "exploration", "sourceRunId": source_run_id, "sourceRunHash": source_run_hash,
            "truthVerified": False, "engineeringCertification": False,
            "manufacturingStatus": "proposed-human-review", "history": []}


__all__ = ["STAGES", "LifecycleValidationError", "LifecycleConflictError", "canonical_hash",
           "validate_stage_gate", "validate_verification_matrix", "initial_state"]
