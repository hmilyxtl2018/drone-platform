"""Role-specific, explicitly provisional deliverables from cited run material."""
from __future__ import annotations

from copy import deepcopy


ENGINEERING_INPUTS = (
    "mission profile and operating environment", "airframe configuration and mass breakdown",
    "propulsion and energy performance curves", "payload interfaces and duty cycle",
    "materials, drawings, tolerances and manufacturing process", "simulation and test measurements",
)
ROLE_DELIVERABLES = {
    'uav-capability-analyst': ('Assessment','EvidenceBundle','GapList','Assumptions'),
    'uav-design-engineer': ('DesignBrief','ConstraintMatrix','DataRequestList','VerificationPlan','EvidenceBundle'),
    'uav-evidence-reviewer': ('ReviewPackage','BlockingIssues','TraceabilityMatrix','EvidenceBundle'),
}


def validate_deliverables(agent_key, deliverables):
    if set(deliverables) != set(ROLE_DELIVERABLES.get(agent_key, ())):
        raise ValueError('role deliverable contract mismatch')
    for value in deliverables.values():
        if value.get('truthVerified') is not False or value.get('reviewStatus') != 'pending':
            raise ValueError('role deliverables cannot self-approve')
    if agent_key == 'uav-design-engineer' and deliverables['DesignBrief'].get('detailedDesignCompleted') is not False:
        raise ValueError('engineering completion is unproven')


def build_deliverables(agent_key, output, bundle, requirements):
    common = {"entityId": output["entityId"], "truthVerified": False,
              "reviewStatus": "pending", "telemetry": "unknown",
              "sourceIndependence": deepcopy(output.get("sourceIndependence", {"status": "unknown"})),
              "reportValidationIssues": deepcopy(output.get("reportValidationIssues", [])),
              "sourceSnapshot": deepcopy(output["sourceSnapshot"])}
    candidates = bundle["candidates"]
    refs = [{"candidateId": c["candidateId"], "sourceId": c["sourceId"],
             "sourceArtifactId": c["sourceArtifactId"], "sourceArtifactHash": c["sourceArtifactHash"],
             "contentHash": c["contentHash"], "quote": c["quote"],
             "startOffset": c["startOffset"], "endOffset": c["endOffset"]} for c in candidates]
    evidence = {**common, "sources": deepcopy(bundle["sources"]),
                "sourceGroups": deepcopy(bundle.get("sourceGroups", [])),
                "candidates": deepcopy(candidates), "conflicts": deepcopy(bundle["conflicts"])}
    gaps = [{"metric": metric, "reason": "candidate evidence still requires review",
             "candidateIds": [c["candidateId"] for c in candidates if c["metric"] == metric]}
            for metric in requirements]
    if agent_key == "uav-capability-analyst":
        return {"Assessment": {**common, "summary": output["summary"], "findings": output["findings"],
                                "assessment": deepcopy(output.get("assessment", {})),
                                "requirements": deepcopy(requirements)},
                "EvidenceBundle": evidence, "GapList": {**common, "items": gaps,
                    "additionalRequests": output["unresolvedQuestions"]},
                "Assumptions": {**common, "items": output["assumptions"]}}
    if agent_key == "uav-design-engineer":
        requests = list(dict.fromkeys([*output["unresolvedQuestions"], *ENGINEERING_INPUTS]))
        return {"DesignBrief": {**common, "maturity": "concept-only", "summary": output["summary"],
                                 "findings": output["findings"], "requirements": deepcopy(requirements),
                                 "detailedDesignCompleted": False, "manufacturingValidated": False},
                "ConstraintMatrix": {**common, "items": [{"metric": metric, "requirement": value,
                    "candidateIds": [c["candidateId"] for c in candidates if c["metric"] == metric],
                    "satisfaction": "unknown", "conditionsMustMatch": True}
                    for metric, value in requirements.items()]},
                "DataRequestList": {**common, "items": requests},
                "VerificationPlan": {**common, "status": "proposed", "steps": [
                    {"step": "validate-inputs", "requiredInputs": requests, "passCriterion": "reviewed traceable inputs"},
                    {"step": "calculate-and-simulate", "passCriterion": "reproducible calculation under declared conditions",
                     "execution": "requires engineering tools and configuration data"},
                    {"step": "bench-and-flight-test", "passCriterion": "approved test criteria and recorded measurements",
                     "execution": "requires independent test authority"}]}, "EvidenceBundle": evidence}
    if agent_key != "uav-evidence-reviewer":
        raise ValueError("unconfigured research Agent role")
    issues = []
    for candidate in candidates:
        checks = {"exactCitation": candidate.get("citationValidated") is True,
                  "conditionsDeclared": bool(candidate.get("conditions")),
                  "variantDeclared": bool(candidate.get("variant")),
                  "observedAtDeclared": bool(candidate.get("observedAt")),
                  "fullOriginalSource": candidate.get("contentKind") == "markdown"}
        issues.append({"candidateId": candidate["candidateId"], "checks": checks,
                       "blockingChecks": [key for key, passed in checks.items() if not passed],
                       "humanDecisionRequired": True})
    return {"ReviewPackage": {**common, "summary": output["summary"], "findings": output["findings"],
                "checks": issues, "conflicts": deepcopy(bundle["conflicts"]), "humanApprovalGranted": False},
            "BlockingIssues": {**common, "items": [i for i in issues if i["blockingChecks"]],
                "unresolvedConflicts": deepcopy(bundle["conflicts"]), "missingInputs": output["unresolvedQuestions"]},
            "TraceabilityMatrix": {**common, "rows": refs}, "EvidenceBundle": evidence}
