"""Dependency-based invalidation for context-bound outputs."""
from __future__ import annotations

from typing import Any


def affected_contexts(contexts: list[dict[str, Any]], changed: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return contexts whose recorded read-set intersects changed references.

    The result is deliberately context-level.  It does not claim sentence or
    claim-level causality; callers mark dependent artifacts for review.
    """
    changed_keys = {(item.get("dependencyKind", "artifact"), item.get("dependencyId"), item.get("contentHash"))
                    for item in changed if isinstance(item, dict)}
    changed_ids = {(item.get("dependencyKind", "artifact"), item.get("dependencyId"))
                   for item in changed if isinstance(item, dict)}
    affected = []
    for context in contexts:
        manifest = context.get("manifest_json") or {}
        dependencies = manifest.get("dependencies", []) if isinstance(manifest, dict) else []
        hit = False
        for dependency in dependencies:
            key = (dependency.get("dependencyKind", "artifact"), dependency.get("dependencyId"),
                   dependency.get("contentHash"))
            if key in changed_keys or (key[0], key[1]) in changed_ids:
                hit = True
                break
        if hit:
            affected.append({"contextId": context.get("context_id"),
                             "operationKey": context.get("operation_key"),
                             "status": "review-needed",
                             "reason": "dependency-changed"})
    return affected


__all__ = ["affected_contexts"]
