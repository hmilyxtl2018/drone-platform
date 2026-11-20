"""Versioned contracts for assembling least-privilege Agent contexts.

The contract is deliberately independent of a provider or a prompt format.  It
describes what a node may see; renderers decide how that projection is encoded
for a model or a tool.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, is_dataclass
from copy import deepcopy
from typing import Any


CONTEXT_POLICY_VERSION = "context-policy.v1"
CONTEXT_MANIFEST_VERSION = "context-manifest.v1"


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False, default=str)


def content_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def invocation_key(base: str, request: Any, schema: Any) -> str:
    """Bind a logical operation to its exact request/schema projection."""
    if not isinstance(base, str) or not base:
        raise ValueError("invocation base key is required")
    suffix = content_hash({'request': request, 'schema': schema})[:24]
    if base.endswith(':' + suffix):
        return base
    return f"{base}:{suffix}"


def default_context_policy() -> dict[str, Any]:
    projection = {
        "taskFields": ["entityId", "uavId", "question", "requirements", "analysisPurpose",
                        "analysisContext", "commonSettings", "specificSettings", "scope", "perspectives",
                        "asOf", "selectedObject", "selectionReason", "objectClass", "category",
                        "budgetLimits", "executionMode", "automationMode", "collaborate", "includeDesign", "previousRunId",
                        "workspaceId", "workspaceRevision", "workspaceRevisionHash", "supplementalMaterial", "followupMode", "followupTarget",
                        "entitySelection", "uavData", "missions", "flightLogs", "batteryHistory",
                        "fixtureMode", "sourceSnapshot", "sourceEvidence", "node", "taskIntentRef"],
        "coordinationFields": ["status", "assignments", "gaps", "conflicts", "budget", "iteration",
                                "currentStepKey", "lastEvent", "revision"],
        "evidenceFields": ["sourceId", "sourceUri", "contentHash", "contentKind", "quote", "metric",
                            "value", "unit", "conditions", "variant", "assertionId", "reviewStatus",
                            "verified", "observedAt"],
        "upstreamFields": ["artifactId", "contentHash", "artifactType", "runId", "content"],
        "memoryFields": ["memoryId", "streamKey", "publicationId", "publicationVersion",
                          "payloadHash", "evidenceSetHash", "publishedAt", "candidateId",
                          "candidate", "source", "memoryStatus"],
    }
    return {
        "schemaVersion": CONTEXT_POLICY_VERSION,
        "maxBytes": 120000,
        "maxEvidenceItems": 32,
        "maxMemoryItems": 8,
        "allowPrivateState": False,
        "toolProjection": False,
        "allowHistoricalMemory": False,
        "freshness": "pinned",
        "roleProjections": {"default": projection},
    }


def context_policy(profile) -> dict[str, Any]:
    config = getattr(profile, "model_policy", {}) or {}
    config_json = config.get("config_json") or {}
    configured = config_json.get("contextPolicy") or {}
    if not isinstance(configured, dict):
        raise ValueError("contextPolicy must be an object")
    value = {**default_context_policy(), **deepcopy(configured)}
    if value.get("schemaVersion") not in (None, CONTEXT_POLICY_VERSION):
        raise ValueError("unsupported context policy version")
    value["schemaVersion"] = CONTEXT_POLICY_VERSION
    if type(value.get("maxBytes")) is not int or not 4096 <= value["maxBytes"] <= 2_000_000:
        raise ValueError("contextPolicy.maxBytes must be between 4096 and 2000000")
    for key in ("allowPrivateState", "allowHistoricalMemory", "toolProjection"):
        if type(value.get(key)) is not bool:
            raise ValueError(f"contextPolicy.{key} must be boolean")
    if value.get("freshness") not in {"pinned", "run", "live"}:
        raise ValueError("contextPolicy.freshness must be pinned, run or live")
    for key in ("maxEvidenceItems", "maxMemoryItems"):
        if type(value.get(key)) is not int or not 0 <= value[key] <= 512:
            raise ValueError(f"contextPolicy.{key} must be between 0 and 512")
    return value


def memory_policy(profile) -> dict[str, Any]:
    """Return the explicitly published-memory policy for a Profile.

    Historical material is denied by default.  When a Profile opts in, the
    runtime still queries only the immutable PostgreSQL publication stream;
    source candidates and private Run state never become memory implicitly.
    """
    config = getattr(profile, "model_policy", {}) or {}
    config_json = config.get("config_json") or {}
    configured = config_json.get("memoryPolicy")
    if configured is None:
        configured = (getattr(profile, "harness_contract", {}) or {}).get("memoryPolicy", {})
    if configured is None:
        configured = {}
    if not isinstance(configured, dict):
        raise ValueError("memoryPolicy must be an object")
    value = {"enabled": False, "streamKeyTemplate": "uav:{entityId}",
             "maxItems": 8, "maxAgeSeconds": None, "requirePublished": True}
    value.update(deepcopy(configured))
    if type(value.get("enabled")) is not bool or type(value.get("requirePublished")) is not bool:
        raise ValueError("memoryPolicy.enabled and requirePublished must be boolean")
    if not isinstance(value.get("streamKeyTemplate"), str) or not value["streamKeyTemplate"]:
        raise ValueError("memoryPolicy.streamKeyTemplate must be a non-empty string")
    if type(value.get("maxItems")) is not int or not 0 <= value["maxItems"] <= 512:
        raise ValueError("memoryPolicy.maxItems must be between 0 and 512")
    age = value.get("maxAgeSeconds")
    if age is not None and (isinstance(age, bool) or not isinstance(age, int) or not 0 <= age <= 31_536_000):
        raise ValueError("memoryPolicy.maxAgeSeconds must be null or a bounded integer")
    return value


def _safe_fields(value: Any, allowed: set[str] | None) -> Any:
    """Project mappings without interpreting untrusted values as instructions."""
    if allowed is None or not isinstance(value, dict):
        return deepcopy(value)
    return {key: deepcopy(child) for key, child in value.items() if key in allowed}


def role_projection(policy: dict[str, Any], role_key: str) -> dict[str, Any]:
    projections = policy.get("roleProjections") or {}
    projection = projections.get(role_key) or projections.get("default") or {}
    if not isinstance(projection, dict):
        raise ValueError("role projection must be an object")
    return projection


def build_manifest(*, run_id: str, root_run_id: str | None, profile, role_key: str,
                   node_key: str, iteration: int, operation_key: str,
                   task: dict[str, Any], coordination: dict[str, Any],
                   evidence: list[dict[str, Any]], upstream: list[dict[str, Any]],
                   memory: list[dict[str, Any]], request_hash: str,
                   schema_hash: str, policy: dict[str, Any]) -> dict[str, Any]:
    projection = role_projection(policy, role_key)
    allowed_task = set(projection.get("taskFields", [])) or None
    allowed_coordination = set(projection.get("coordinationFields", [])) or None
    allowed_evidence = set(projection.get("evidenceFields", [])) or None
    allowed_upstream = set(projection.get("upstreamFields", [])) or None
    # Historical memory is more sensitive than current evidence: a role must
    # explicitly list its memory fields or it receives an empty projection.
    allowed_memory = set(projection.get("memoryFields", []))
    excluded: list[dict[str, str]] = []
    task_value = _safe_fields(task, allowed_task)
    coordination_value = _safe_fields(coordination, allowed_coordination)
    evidence_value = [_safe_fields(item, allowed_evidence) for item in evidence[:int(policy["maxEvidenceItems"])]]
    upstream_value = [_safe_fields(item, allowed_upstream) for item in upstream]
    memory_value = [_safe_fields(item, allowed_memory) for item in memory[:int(policy["maxMemoryItems"])] \
                    ] if policy.get("allowHistoricalMemory") else []
    if not policy.get("allowPrivateState"):
        excluded.append({"path": "privateState", "reason": "policy-denied"})
    if memory and not policy.get("allowHistoricalMemory"):
        excluded.append({"path": "memory", "reason": "policy-denied"})
    profile_snapshot_hash = getattr(profile, "snapshot_hash", None)
    if not profile_snapshot_hash and is_dataclass(profile):
        profile_data = asdict(profile)
        if not profile_data.get('skill_manifest'):
            profile_data.pop('skill_manifest',None)
        profile_snapshot_hash = content_hash(profile_data)
    manifest = {
        "schemaVersion": CONTEXT_MANIFEST_VERSION,
        "runId": run_id,
        "rootRunId": root_run_id or run_id,
        "roleKey": role_key,
        "nodeKey": node_key,
        "iteration": iteration,
        "operationKey": operation_key,
        "profileHash": profile_snapshot_hash or content_hash({"agentKey": getattr(profile, "agent_key", "legacy"),
                                                               "version": getattr(profile, "version", "legacy"),
                                                               "systemPrompt": getattr(profile, "system_prompt", "")}),
        "baselineHash": (getattr(profile, "execution_baseline", {}) or {}).get("graphHash"),
        "policy": deepcopy(policy),
        "task": task_value,
        "coordination": coordination_value,
        "evidence": evidence_value,
        "upstream": upstream_value,
        "memory": memory_value,
        "excluded": excluded,
        "requestHash": request_hash,
        "schemaHash": schema_hash,
    }
    raw = canonical_json(manifest)
    if len(raw.encode("utf-8")) > int(policy["maxBytes"]):
        # Keep the invariant-bearing fields and reduce evidence before failing.
        while manifest["evidence"] and len(canonical_json(manifest).encode("utf-8")) > int(policy["maxBytes"]):
            manifest["evidence"].pop()
        if len(canonical_json(manifest).encode("utf-8")) > int(policy["maxBytes"]):
            raise ValueError("context exceeds policy maxBytes after preserving task and constraints")
        manifest["excluded"].append({"path": "evidence", "reason": "size-limit"})
    manifest["manifestHash"] = content_hash(manifest)
    return manifest


__all__ = ["CONTEXT_POLICY_VERSION", "CONTEXT_MANIFEST_VERSION", "canonical_json",
           "content_hash", "invocation_key", "context_policy", "memory_policy", "build_manifest"]
