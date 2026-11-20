"""Deterministic context assembly shared by model, tool and handoff paths."""
from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

from .context_contract import (build_manifest, content_hash, context_policy,
                               invocation_key, memory_policy)


class ContextBuilder:
    def __init__(self, repository=None):
        self.repository = repository

    @staticmethod
    def _node(profile, node_key):
        baseline = getattr(profile, "execution_baseline", {}) or {}
        graph = baseline.get("graph", {}) if isinstance(baseline, dict) else {}
        return next((node for node in graph.get("nodes", []) if node.get("id") == node_key), {})

    @staticmethod
    def _upstream_projection(items):
        """Project handoff artifacts before they enter a tool/model context.

        Handoff inputs retain the complete, hash-bound artifact for the
        deterministic runtime and audit APIs.  A context manifest only needs
        the identity and provenance of that artifact; embedding a whole
        ontology snapshot or report in every tool call both defeats least
        privilege and can exhaust the manifest byte budget.  Small structured
        content is retained as a convenience, while larger content remains
        available through the local artifact store and is represented by its
        immutable hash.
        """
        projected = []
        for item in items:
            if not isinstance(item, dict):
                continue
            value = {key: deepcopy(item[key]) for key in
                     ("artifactId", "contentHash", "artifactType", "runId")
                     if key in item}
            content = item.get("content")
            if content is not None:
                try:
                    encoded = json.dumps(content, ensure_ascii=False,
                                         separators=(",", ":"), default=str).encode("utf-8")
                except (TypeError, ValueError):
                    encoded = b""
                # Keep compact conclusions/contract fragments useful to a
                # model, but never copy large source bodies into each call.
                if len(encoded) <= 12000:
                    value["content"] = deepcopy(content)
                else:
                    value["contentProjection"] = {
                        "contentHash": item.get("contentHash"),
                        "omitted": True,
                        "reason": "context-size-limit",
                    }
            projected.append(value)
        return projected

    def build(self, ctx, *, node_key: str | None = None, role_key: str | None = None,
              operation_key: str | None = None, request=None, output_schema=None,
              evidence=None, upstream=None, coordination=None, memory=None,
              dependencies=None, step_run_id=None):
        node_key = node_key or getattr(ctx, "current_step_key", None) or "run"
        role_key = role_key or getattr(ctx.profile, "agent_key", "agent")
        operation_key = operation_key or f"{node_key}:{getattr(ctx, 'iteration', 0)}"
        request = request if request is not None else []
        evidence = evidence if isinstance(evidence, list) else []
        upstream = self._upstream_projection(upstream) if isinstance(upstream, list) else []
        coordination = coordination if isinstance(coordination, dict) else {}
        memory = memory if isinstance(memory, list) else []
        policy = context_policy(ctx.profile)
        configured_memory = memory_policy(ctx.profile)
        # Memory is opt-in twice: the Profile must allow historical memory and
        # its memory policy must explicitly enable published retrieval.  The
        # repository method is optional for legacy/in-memory fixtures.
        if (not memory and policy.get("allowHistoricalMemory")
                and configured_memory.get("enabled")
                and configured_memory.get("requirePublished", True)
                and self.repository and hasattr(self.repository, "published_memory")):
            entity_id = ((getattr(ctx, "input", {}) or {}).get("entityId") or
                         (getattr(ctx, "input", {}) or {}).get("uavId"))
            if isinstance(entity_id, str) and entity_id:
                stream_key = configured_memory["streamKeyTemplate"].replace("{entityId}", entity_id)
                memory = self.repository.published_memory(
                    stream_key=stream_key, limit=configured_memory["maxItems"],
                    max_age_seconds=configured_memory.get("maxAgeSeconds"))
        node = deepcopy(self._node(ctx.profile, node_key))
        task = deepcopy(getattr(ctx, "input", {}) or {})
        task.pop('taskIntent', None)
        for reserved in ('goalContract', 'localGoal', 'parentGoalId', 'goalAssessment'):
            task.pop(reserved, None)
        if getattr(ctx.profile, 'goal_contract', None):
            from .goal_contract import GoalService
            goal = GoalService(self.repository.connection_factory).context(ctx.run_id) if self.repository and hasattr(self.repository, 'connection_factory') else {}
            task['goalRef'] = {key: deepcopy(goal[key]) for key in
                ('goalId', 'objective', 'localGoal', 'parentGoalId', 'contractHash', 'contractRevision', 'exclusions') if key in goal}
        if self.repository and hasattr(self.repository, 'task_intent'):
            intent = self.repository.task_intent(ctx.run_id)
            task['taskIntentRef'] = {key: intent.get(key) for key in ('intentId','contentHash','status')}
            document = intent.get('document') or {}
            task['taskIntentRef']['constraints'] = {key:deepcopy(document[key]) for key in
                ('allowedOperations','allowedTools','budgets','expiresAt','stopConditions') if key in document}
        task["node"] = {key: node.get(key) for key in ("id", "inputContract", "outputContract", "budget") if key in node}
        # Request bodies are already redacted by the provider before persistence;
        # only their stable hash is included in the manifest.
        request_hash = content_hash(request)
        schema_hash = content_hash(output_schema or node.get("outputContract", {}))
        operation_key = invocation_key(operation_key, request, output_schema or node.get("outputContract", {}))
        manifest = build_manifest(
            run_id=ctx.run_id, root_run_id=getattr(ctx, "root_run_id", None),
            profile=ctx.profile, role_key=role_key, node_key=node_key,
            iteration=int(getattr(ctx, "iteration", 0) or 0), operation_key=operation_key,
            task=task, coordination=coordination or (getattr(ctx, "state", {}) or {}).get("coordination", {}),
            evidence=evidence, upstream=upstream, memory=memory,
            request_hash=request_hash, schema_hash=schema_hash, policy=policy)
        requested_dependencies = dependencies if isinstance(dependencies, list) else []
        dependencies = []
        for item in evidence:
            if not isinstance(item, dict):
                continue
            if item.get("sourceId"):
                dependencies.append({"dependencyId": item["sourceId"],
                                     "dependencyKind": "evidence-source",
                                     "versionRef": item.get("sourceVersion") or item.get("observedAt"),
                                     "contentHash": item.get("contentHash") or item.get("sourceContentHash"),
                                     "readMode": "read"})
            if item.get("sourceArtifactId"):
                dependencies.append({"dependencyId": item["sourceArtifactId"],
                                     "dependencyKind": "source-snapshot",
                                     "versionRef": item.get("sourceVersion"),
                                     "contentHash": item.get("sourceArtifactHash") or item.get("contentHash"),
                                     "readMode": "read"})
            if not item.get("sourceId") and not item.get("sourceArtifactId") and item.get("candidateId"):
                dependencies.append({"dependencyId": item["candidateId"],
                                     "dependencyKind": "evidence-candidate",
                                     "versionRef": item.get("observedAt"),
                                     "contentHash": item.get("contentHash"),
                                     "readMode": "read"})
        for item in upstream:
            if isinstance(item, dict) and item.get("artifactId"):
                dependencies.append({"dependencyId": item["artifactId"],
                                     "dependencyKind": "upstream-artifact",
                                     "versionRef": item.get("runId"),
                                     "contentHash": item.get("contentHash"),
                                     "readMode": "read"})
        for item in memory:
            if isinstance(item, dict) and item.get("publicationId"):
                dependencies.append({"dependencyId": item["publicationId"],
                                     "dependencyKind": "published-memory",
                                     "versionRef": str(item.get("publicationVersion", "")),
                                     "contentHash": item.get("payloadHash"),
                                     "readMode": "read"})
        # Deterministic adapters may know that a lookup read a particular
        # ontology entity/query even before an evidence candidate exists.
        # Callers can also record negative reads (absence) explicitly.
        direct = []
        for item in requested_dependencies:
            if isinstance(item, dict) and item.get("dependencyId"):
                direct.append(deepcopy(item))
        for item in ((getattr(ctx, "state", {}) or {}).get("contextDependencies", []) or []):
            if isinstance(item, dict) and item.get("dependencyId"):
                direct.append(deepcopy(item))
        # The local variable above intentionally starts empty after the
        # evidence/upstream pass; preserve those entries while deduplicating
        # direct additions by identity and kind.
        merged = []
        seen = set()
        for item in [*dependencies, *direct]:
            key = (item.get("dependencyId"), item.get("dependencyKind", "artifact"))
            if key in seen:
                continue
            seen.add(key)
            merged.append(item)
        dependencies = merged
        manifest["dependencies"] = dependencies
        manifest.pop("manifestHash", None)
        max_bytes = int(policy["maxBytes"])
        while (len(json.dumps(manifest, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) > max_bytes
               and manifest["dependencies"]):
            manifest["dependencies"].pop()
            manifest.setdefault("excluded", []).append({"path": "dependencies", "reason": "size-limit"})
        if len(json.dumps(manifest, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) > max_bytes:
            raise ValueError("context exceeds policy maxBytes after preserving task and constraints")
        manifest["manifestHash"] = content_hash(manifest)
        frozen_profile_hash = getattr(ctx, "profile_snapshot_hash", None)
        if frozen_profile_hash:
            manifest["profileHash"] = frozen_profile_hash
            manifest.pop("manifestHash", None)
            manifest["manifestHash"] = content_hash(manifest)
        if self.repository and hasattr(self.repository, "create_context_snapshot"):
            context_id = self.repository.create_context_snapshot(
                ctx.run_id, manifest, operation_key=operation_key,
                step_run_id=step_run_id or getattr(ctx, "current_step_run_id", None), role_key=role_key, node_key=node_key,
                iteration=int(getattr(ctx, "iteration", 0) or 0))
            ctx.context_id = context_id
            ctx.context_manifest_hash = manifest["manifestHash"]
        return manifest

    @staticmethod
    def tool_projection(manifest: dict[str, Any], arguments: dict[str, Any]) -> dict[str, Any]:
        return {"context": {"manifestHash": manifest.get("manifestHash"),
                             "task": deepcopy(manifest.get("task", {})),
                             "coordination": deepcopy(manifest.get("coordination", {})),
                             "evidence": deepcopy(manifest.get("evidence", [])),
                             "upstream": deepcopy(manifest.get("upstream", []))},
                "arguments": deepcopy(arguments)}


__all__ = ["ContextBuilder"]
