"""Compatibility execution bridge for the multi-agent analysis run.

The project already has a durable entity audit implementation
(:mod:`audit_run`) and the bounded capability workflow
(:mod:`capability_workflow`).  This module adapts those outputs to the
``AnalysisRun``/``AgentTask`` contract without duplicating provider calls.
It is intentionally conservative: every artifact is persisted as
``under-review`` and this bridge never calls an ontology publication method.

``MissionOrchestrator`` is kept as a small protocol rather than imported at
module import time.  That allows old deployments and isolated tests to load
the bridge while the orchestration migration is being applied.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol


AGENT_ROLES = (
    "ontology-planner",
    "intelligence-collector",
    "attribute-relation-extractor",
    "signal-imagery-analyst",
    "evidence-fusion",
    "capability-quantification",
    "red-team-governance-reviewer",
)


class OrchestratorProtocol(Protocol):
    """The subset of MissionOrchestrator used by this adapter."""

    def create(self, request: dict[str, Any], *, entity_id: str | None = None) -> Mapping[str, Any]: ...
    def start(self, run_id: str) -> Mapping[str, Any]: ...
    def claim_ready_tasks(self, run_id: str, *, limit: int | None = None,
                          worker_id: str = "orchestrator") -> list[Mapping[str, Any]]: ...
    def add_artifact(self, task_id: str, artifact_type: str, content: dict[str, Any],
                     *, status: str = "under-review") -> Mapping[str, Any]: ...
    def complete_task(self, task_id: str, output: dict[str, Any] | None = None,
                      *, worker_id: str | None = None) -> Mapping[str, Any]: ...
    def handoff(self, from_task_id: str, to_task_id: str, *, artifact_id: str | None = None,
                payload: dict[str, Any] | None = None) -> Mapping[str, Any]: ...
    def update_handoff(self, handoff_id: str, status: str) -> Mapping[str, Any]: ...


def _first(value: Any, *keys: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        for key in keys:
            if key in value:
                return value[key]
    return default


def _task_id(task: Any) -> str | None:
    return _first(task, "taskId", "agentTaskId", "id")


def _run_id(run: Any) -> str:
    run_id = _first(run, "runId", "analysisRunId", "id")
    if not run_id:
        raise ValueError("orchestrator response did not contain runId")
    return str(run_id)


def _tasks(run: Any) -> dict[str, Any]:
    rows = _first(run, "tasks", "agentTasks", default=[])
    return {str(_first(row, "role", default="")): row for row in rows if _task_id(row)}


def _events(audit: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    rows = _first(audit, "events", default=[])
    return [row for row in rows if isinstance(row, Mapping)]


def _audit_stage(audit: Mapping[str, Any], stage: str) -> list[Mapping[str, Any]]:
    return [event for event in _events(audit) if str(event.get("stage") or "") == stage]


def _provider_calls(audit: Mapping[str, Any], stage: str | None = None) -> list[dict[str, Any]]:
    """Project provider call metadata from durable audit events.

    The call payload itself remains in the provider trace store; artifacts
    only carry stable identifiers and bounded metadata needed to audit which
    Firecrawl/DeepSeek stage produced a candidate.
    """
    calls: list[dict[str, Any]] = []
    for event in (_audit_stage(audit, stage) if stage else _events(audit)):
        detail = event.get("detail") if isinstance(event, Mapping) else None
        if not isinstance(detail, Mapping):
            continue
        provider = detail.get("provider") or detail.get("collector")
        operation = detail.get("operation") or detail.get("stage")
        if not provider and not operation:
            continue
        calls.append({
            "eventId": event.get("eventId"), "provider": provider,
            "operation": operation, "status": event.get("status") or detail.get("status"),
            "model": detail.get("model"), "attempt": detail.get("attempt"),
        })
    return calls[:200]


def _audit_signal(audit: Mapping[str, Any], key: str) -> list[Any]:
    signal = _first(audit, "signal", default={})
    return list(_first(signal, key, default=[]) or []) if isinstance(signal, Mapping) else []


def _source_refs(audit: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Collect immutable source references without inventing a source record."""
    refs: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for row in list(_first(audit, "candidates", default=[]) or []):
        candidate = _candidate_value(row)
        for evidence in candidate.get("evidence") or []:
            if not isinstance(evidence, Mapping):
                continue
            uri = str(evidence.get("sourceUri") or evidence.get("sourceRef") or "")
            quote = str(evidence.get("quote") or "")
            digest = str(evidence.get("contentHash") or "")
            key = (uri, quote, digest)
            if key in seen or not any(key):
                continue
            seen.add(key)
            refs.append({
                "sourceUri": uri,
                "sourceVersion": evidence.get("sourceVersion"),
                "contentHash": digest,
                "quote": quote,
                "locator": {"lineStart": evidence.get("lineStart"), "lineEnd": evidence.get("lineEnd")},
                "snapshotMatched": evidence.get("snapshotMatch") is True,
            })
    # Signal observations can be collected before a model candidate exists;
    # retain their source references in the collector/evidence artifacts too.
    for observation in _audit_signal(audit, "observations"):
        if not isinstance(observation, Mapping):
            continue
        for evidence in observation.get("sourceRefs") or []:
            if not isinstance(evidence, Mapping):
                continue
            uri = str(evidence.get("sourceUri") or evidence.get("sourceRef") or "")
            quote = str(evidence.get("quote") or "")
            digest = str(evidence.get("contentHash") or "")
            key = (uri, quote, digest)
            if key in seen or not any(key):
                continue
            seen.add(key)
            refs.append({
                "sourceUri": uri, "sourceVersion": evidence.get("sourceVersion"),
                "contentHash": digest, "quote": quote,
                "locator": evidence.get("locator") or {},
                "snapshotMatched": evidence.get("snapshotMatched") is True,
            })
    return refs[:500]


def _candidate_value(row: Any) -> Mapping[str, Any]:
    """Return the legacy attribute candidate from either API row shape."""
    if not isinstance(row, Mapping):
        return {}
    value = row.get("candidate")
    return value if isinstance(value, Mapping) else row


def _candidate_contract(row: Any) -> dict[str, Any]:
    """Expose explicit context fields even when the source did not provide them.

    ``unknown`` is intentional: it prevents a missing condition, variant or
    configuration from disappearing in downstream artifacts and keeps the
    resulting candidate reviewable without turning absence into a fact.
    """
    candidate = dict(_candidate_value(row))
    metadata = candidate.get("metadata") if isinstance(candidate.get("metadata"), Mapping) else {}
    conditions = candidate.get("conditions")
    variant = candidate.get("variant", metadata.get("variant"))
    configuration = candidate.get("configuration", metadata.get("configuration"))
    gaps = []
    if conditions in (None, "", {}):
        gaps.append("conditions")
        conditions = {"status": "unknown", "reason": "source did not state applicability conditions"}
    if variant in (None, ""):
        gaps.append("variant")
        variant = "unknown"
    if configuration in (None, ""):
        gaps.append("configuration")
        configuration = "unknown"
    return {
        "candidateId": _first(candidate, "candidateId", "candidate_id"),
        "entityId": _first(candidate, "entityId", "entity_id"),
        "attributeKey": _first(candidate, "attributeKey", "attribute_key"),
        "proposedValue": _first(candidate, "proposedValue", "proposed_value"),
        "unit": candidate.get("unit"),
        "conditions": conditions,
        "variant": variant,
        "configuration": configuration,
        "confidence": candidate.get("confidence"),
        "reviewStatus": candidate.get("status") or candidate.get("reviewStatus") or "under-review",
        "evidence": candidate.get("evidence") or [],
        # Keep the source snapshots and exact quote attached to the
        # candidate.  Downstream agents may normalize this list, but they
        # cannot replace it with model-generated evidence.
        "evidenceRefs": candidate.get("evidence") or [],
        "contextGaps": gaps,
    }


def _artifact_content(audit: Mapping[str, Any], role: str) -> dict[str, Any]:
    """Select a bounded, evidence-preserving artifact for ``role``."""
    if role == "ontology-planner":
        request = _first(audit, "request", default={})
        return {"schemaVersion": "research-plan.v1", "request": request,
                "entityId": _first(audit, "entityId"),
                "objectRevision": _first(audit, "objectRevision")}
    if role == "intelligence-collector":
        return {"schemaVersion": "source-bundle.v1", "events": _audit_stage(audit, "collecting"),
                "sourceRefs": _source_refs(audit),
                "sourceCount": len(_source_refs(audit)),
                "observationCount": len(_audit_signal(audit, "observations")),
                "providerCalls": _provider_calls(audit, "collecting")}
    if role == "attribute-relation-extractor":
        candidates = list(_first(audit, "candidates", default=[]) or [])
        claims = _audit_signal(audit, "claims")
        return {"schemaVersion": "ontology-candidates.v1", "candidates": candidates,
                "candidateFacts": [_candidate_contract(row) for row in candidates],
                "capabilityClaims": claims, "events": _audit_stage(audit, "extracting"),
                "providerCalls": _provider_calls(audit, "extracting"),
                "reviewStatus": "under-review"}
    if role == "signal-imagery-analyst":
        return {"schemaVersion": "observation-bundle.v1",
                "observations": _audit_signal(audit, "observations"),
                "emitterSignatures": _audit_signal(audit, "emitterSignatures")}
    if role == "evidence-fusion":
        result = _first(audit, "result", default={}) or {}
        quality = result.get("quality", {}) if isinstance(result, Mapping) else {}
        return {"schemaVersion": "evidence-bundle.v1",
                "evidenceBundles": _audit_signal(audit, "evidenceBundles"),
                "verificationRuns": _audit_signal(audit, "verificationRuns"),
                "conflictCount": int(quality.get("conflictCount") or 0) if isinstance(quality, Mapping) else 0,
                "gaps": (quality.get("gaps") or quality.get("unknownFields") or []) if isinstance(quality, Mapping) else [],
                "sourceRefs": _source_refs(audit),
                "sourceIndependence": "requires-review"}
    if role == "capability-quantification":
        result = _first(audit, "result", default={}) or {}
        return {"schemaVersion": "capability-assessment.v1",
                "profile": result.get("profile") if isinstance(result, Mapping) else result,
                "result": result, "events": _audit_stage(audit, "validation"),
                "providerCalls": _provider_calls(audit, "validation"),
                "reviewStatus": "under-review"}
    if role == "red-team-governance-reviewer":
        result = _first(audit, "result", default={}) or {}
        claims = _audit_signal(audit, "claims")
        candidates = list(_first(audit, "candidates", default=[]) or [])
        findings: list[dict[str, Any]] = []
        quality = result.get("quality", {}) if isinstance(result, Mapping) else {}
        if isinstance(quality, Mapping) and int(quality.get("conflictCount") or 0):
            findings.append({"code": "source-conflict", "severity": "high", "status": "open",
                             "detail": "至少存在一个跨来源冲突；不得自动选择数值。"})
        for claim in claims:
            refs = claim.get("evidenceBundleIds") or claim.get("sourceObservationIds") or [] if isinstance(claim, Mapping) else []
            if not refs:
                findings.append({"code": "claim-without-evidence", "severity": "high", "status": "open",
                                 "claimId": _first(claim, "claimId", default="unknown"),
                                 "detail": "能力断言没有绑定观测或证据包。"})
            status = str(_first(claim, "reviewStatus", "review_status", default="under-review"))
            if status not in {"under-review", "pending", "candidate"}:
                findings.append({"code": "review-gate-state", "severity": "medium", "status": "open",
                                 "claimId": _first(claim, "claimId", default="unknown"),
                                 "detail": f"候选状态为 {status}，需要人工确认其准入边界。"})
        for row in candidates:
            candidate = _candidate_contract(row)
            if candidate["contextGaps"]:
                findings.append({
                    "code": "candidate-context-missing",
                    "severity": "medium",
                    "status": "open",
                    "candidateId": candidate.get("candidateId") or "unknown",
                    "detail": "属性候选缺少适用条件、改型或配置；已标记为 unknown，不能据此外推作战能力。",
                    "missing": candidate["contextGaps"],
                })
        return {"schemaVersion": "review-package.v1",
                "quality": result.get("quality", {}) if isinstance(result, Mapping) else {},
                "claimCount": len(claims),
                "attributeCandidateCount": len(candidates),
                "findings": findings,
                "publication": "requires-explicit-human-review",
                "allCandidatesUnderReview": all(
                    str(_first(item, "reviewStatus", "review_status", default="under-review")) == "under-review"
                    for item in [*claims, *[_candidate_value(row) for row in candidates]]
                )}
    raise ValueError(f"unknown agent role: {role}")


class EntityAuditExecutionBridge:
    """Run the existing entity audit under an ``AnalysisRun`` envelope.

    The existing audit worker remains the source of truth for Firecrawl,
    DeepSeek, signal ingestion, conflict handling and capability quantification.
    This wrapper records each stage as an artifact and a handoff so the new
    multi-agent workbench can observe the complete chain immediately.
    """

    def __init__(self, orchestrator: OrchestratorProtocol, audit_runs: Any,
                 conversation_store: Any | None = None):
        self.orchestrator = orchestrator
        self.audit_runs = audit_runs
        self.conversation_store = conversation_store

    def execute(self, entity_id: str, request: dict[str, Any] | None = None) -> dict[str, Any]:
        request = dict(request or {})
        request["entityId"] = entity_id
        # EntityAuditRuns accepts the durable user trigger vocabulary.  The
        # orchestration layer is recorded in the request metadata rather than
        # inventing a trigger that older audit readers reject.
        if request.get("trigger") not in {"manual", "batch", "periodic", "conversation"}:
            request["trigger"] = "manual"
        request.setdefault("orchestration", "multi-agent")
        # A publication gate is deliberately impossible to bypass through the
        # adapter.  Review and publication are separate user operations.
        request["autoPublish"] = False
        envelope = self.orchestrator.create(request, entity_id=entity_id)
        analysis_id = _run_id(envelope)
        return self.execute_existing(analysis_id, entity_id, request, envelope=envelope)

    def execute_existing(self, analysis_id: str, entity_id: str,
                         request: dict[str, Any] | None = None,
                         *, envelope: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Execute an already-created AnalysisRun.

        This is used by the HTTP worker endpoint: ``POST /analysis-runs/{id}/execute``
        lets the UI create/inspect a run before dispatching providers.
        """
        request = dict(request or {})
        request["entityId"] = entity_id
        if request.get("trigger") not in {"manual", "batch", "periodic", "conversation"}:
            request["trigger"] = "manual"
        request.setdefault("orchestration", "multi-agent")
        request["autoPublish"] = False
        envelope = envelope or self.orchestrator.get(analysis_id)
        # Conversation binding is deliberately additive.  Legacy/manual
        # runs continue to work without a session; conversation runs carry
        # prompt and intent through the same durable AnalysisRun boundary.
        session_id = request.get("conversationSessionId") or request.get("sessionId")
        if session_id and self.conversation_store is not None:
            binder = getattr(self.conversation_store, "bind_run", None)
            if not callable(binder):
                raise RuntimeError("conversation store does not support AnalysisRun binding")
            binder(
                str(session_id), analysis_id,
                prompt=str(request.get("prompt") or request.get("promptText") or "") or None,
                intent=request.get("intent") if isinstance(request.get("intent"), Mapping) else None,
            )
        self.orchestrator.start(analysis_id)

        # Planning is a local contract step.  Complete it before entering the
        # synchronous legacy audit worker so a long Firecrawl/DeepSeek call
        # cannot make the run look stuck at ``ontology-planner``.  Downstream
        # tasks stay queued until the audit produces their input artifacts.
        artifacts: list[Mapping[str, Any]] = []
        handoffs: list[Mapping[str, Any]] = []
        role_tasks: dict[str, str] = {}
        role_artifacts: dict[str, str] = {}
        planner = self.orchestrator.claim_ready_tasks(analysis_id, limit=1, worker_id="audit-bridge")
        planner_task = next((row for row in planner if _first(row, "role") == "ontology-planner"), None)
        if planner_task is None:
            raise RuntimeError("agent task is not ready for role ontology-planner")
        planner_task_id = _task_id(planner_task)
        if not planner_task_id:
            raise RuntimeError("orchestrator task has no id for role ontology-planner")
        planner_audit = {"request": request, "entityId": entity_id,
                         "objectRevision": _first(envelope, "objectRevision")}
        def artifact_content(source_audit: Mapping[str, Any], role: str) -> dict[str, Any]:
            value = _artifact_content(source_audit, role)
            audit_id = _first(source_audit, "auditRunId", "auditId")
            if audit_id:
                value["auditRunId"] = audit_id
            return value
        planner_artifact = self.orchestrator.add_artifact(
            planner_task_id, "ontology-planner", artifact_content(planner_audit, "ontology-planner"),
            status="under-review"
        )
        artifacts.append(planner_artifact)
        self.orchestrator.complete_task(
            planner_task_id, artifact_content(planner_audit, "ontology-planner"),
            worker_id="audit-bridge"
        )
        planner_artifact_id = _first(planner_artifact, "artifactId", "id")
        role_tasks["ontology-planner"] = planner_task_id
        if planner_artifact_id:
            role_artifacts["ontology-planner"] = str(planner_artifact_id)

        # Reuse a reviewable audit for the same immutable object revision.
        # Repeated workbench runs must not create duplicate provider jobs or
        # fail merely because the previous result is awaiting human review.
        audit = None
        audit_agent = getattr(self.audit_runs, "agent", None)
        current_revision = None
        if audit_agent is not None and hasattr(audit_agent, "_entity_document"):
            try:
                entity, _ = audit_agent._entity_document(entity_id)
                current_revision = int(entity.get("currentRevision") or 0)
            except Exception:
                current_revision = None
        try:
            existing_runs = self.audit_runs.list(entity_id=entity_id, limit=20)
        except Exception:
            existing_runs = []
        for existing in existing_runs:
            if existing.get("status") not in {"awaiting-review", "completed", "completed-with-gaps"}:
                continue
            if current_revision is not None and int(existing.get("objectRevision") or 0) != current_revision:
                continue
            audit = existing
            break
        if audit is None:
            audit = self.audit_runs.submit(request, entity_id, background=False)
        if str(_first(audit, "status", default="")).lower() in {"failed", "cancelled"}:
            raise RuntimeError(f"entity audit did not produce a usable result: {_first(audit, 'status')}")
        for role in AGENT_ROLES[1:]:
            # Claim only a ready task. Completing a task refreshes the DAG and
            # makes its dependent task ready; this also preserves the
            # orchestrator's retry and concurrency accounting.
            claimed = self.orchestrator.claim_ready_tasks(analysis_id, limit=1, worker_id="audit-bridge")
            task = next((row for row in claimed if _first(row, "role") == role), None)
            if task is None:
                # The graph may have been claimed by an external worker. Keep
                # this run observable and fail loudly instead of writing an
                # artifact to a task that is not owned by this bridge.
                raise RuntimeError(f"agent task is not ready for role {role}")
            task_id = _task_id(task)
            if not task_id:
                raise RuntimeError(f"orchestrator task has no id for role {role}")
            artifact = self.orchestrator.add_artifact(
                task_id, role, artifact_content(audit, role), status="under-review"
            )
            artifacts.append(artifact)
            self.orchestrator.complete_task(task_id, artifact_content(audit, role), worker_id="audit-bridge")
            artifact_id = _first(artifact, "artifactId", "id")
            dependencies = next((item["dependencies"] for item in _ROLE_DEPS if item["role"] == role), ())
            for dependency in dependencies:
                source_id = role_tasks.get(dependency)
                source_artifact_id = role_artifacts.get(dependency)
                if not source_id or not source_artifact_id:
                    continue
                handoff = self.orchestrator.handoff(
                    source_id, task_id, artifact_id=source_artifact_id,
                    payload={"contract": f"{role}.v1", "status": "under-review"}
                )
                handoffs.append(self.orchestrator.update_handoff(_first(handoff, "handoffId", "id"), "accepted"))
            role_tasks[role] = task_id
            if artifact_id:
                role_artifacts[role] = str(artifact_id)
        return {
            "analysisRunId": analysis_id,
            "auditRun": audit,
            "artifacts": artifacts,
            "handoffs": handoffs,
            "publication": "requires-explicit-human-review",
            "autoPublished": False,
        }


# Kept local so this adapter remains usable with an orchestrator exposing the
# same role names without importing its implementation.  The task ids are
# populated per execute() call; a lock is unnecessary because a bridge owns a
# single synchronous run at a time.
_ROLE_DEPS = (
    {"role": "ontology-planner", "dependencies": ()},
    {"role": "intelligence-collector", "dependencies": ("ontology-planner",)},
    {"role": "attribute-relation-extractor", "dependencies": ("intelligence-collector",)},
    {"role": "signal-imagery-analyst", "dependencies": ("intelligence-collector",)},
    {"role": "evidence-fusion", "dependencies": ("attribute-relation-extractor", "signal-imagery-analyst")},
    {"role": "capability-quantification", "dependencies": ("evidence-fusion",)},
    {"role": "red-team-governance-reviewer", "dependencies": ("capability-quantification",)},
)


__all__ = ["AGENT_ROLES", "EntityAuditExecutionBridge", "OrchestratorProtocol"]
