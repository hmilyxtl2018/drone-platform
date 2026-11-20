"""Credential-safe execution events for actual research operations.

The caller binds a sink explicitly in its own request/background thread.
No global provider traffic is attributed to a task, and old records are never
backfilled as if their tool calls had been observed.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
import os
import re
import secrets
import threading
from typing import Any, Callable
from urllib.error import HTTPError, URLError


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _name(value: Any, default: str = "unknown") -> str:
    text = str(value or "")
    return text if re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", text) else default


_COUNTS = {
    "inputCharacters", "outputCharacters", "inputTokens", "outputTokens",
    "outputTokenLimit", "responseBytes", "timeoutSeconds", "requestedLimit",
    "sourceCount", "rawResultCount", "retainedItemCount", "removedDuplicateCount",
    "nodeCount", "edgeCount", "findingCount", "candidateCount", "errorCount",
    "sinceDays", "horizonYears", "validationCount", "entityCount", "eventCount",
    "proposedAttributeCount", "retainedAttributeCount", "rejectedAttributeCount",
    "retryCount",
    "retryLimit", "requestBudgetSeconds",
}


def safe_details(value: Any) -> dict[str, Any]:
    """Allow operational metadata only; never serialize requests or responses."""
    if not isinstance(value, dict):
        return {}
    result = {}
    for key, item in value.items():
        if key in _COUNTS and type(item) in (int, float) and 0 <= item <= 1_000_000_000:
            result[key] = item
        elif key in {"sourceId", "objectId", "method", "resultMode", "requestedModel", "actualModel"}:
            result[key] = _name(item)
        elif key == "modelConfirmed" and type(item) is bool:
            result[key] = item
        elif key == "finishReason" and isinstance(item, str) and item in {"stop", "length", "tool_calls", "content_filter", "insufficient_system_resource"}:
            result[key] = item
        elif key == "thinkingMode" and isinstance(item, str) and item in {"enabled-low", "disabled"}:
            result[key] = item
        elif key == "sourceScopes" and isinstance(item, (list, tuple)):
            result[key] = [_name(part) for part in item[:20]]
        elif key == "domains" and isinstance(item, (list, tuple)):
            result[key] = [part for part in item[:30] if isinstance(part, str) and re.fullmatch(r"[A-Za-z0-9.-]{1,253}", part)]
        elif key == "excludedCounts" and isinstance(item, dict):
            result[key] = {name: count for name, count in item.items() if name in {"outsideScope", "invalidUrl", "outsideTimeWindow", "emptyBody", "unrelatedSubject", "genericSummary", "invalidImageUrl"} and type(count) is int and 0 <= count <= 1_000_000}
    return result


def safe_error(exc: BaseException) -> dict[str, Any]:
    """Exception text can contain keys, URLs or source text, so never store it."""
    if isinstance(exc, HTTPError):
        return {"code": "provider-http-error", "httpStatus": int(exc.code)}
    if isinstance(exc, (URLError, TimeoutError, OSError)):
        return {"code": "connection-or-timeout"}
    if isinstance(exc, (ValueError, TypeError, UnicodeError)):
        return {"code": "invalid-request-or-response"}
    return {"code": "operation-failed"}


@dataclass(frozen=True)
class TraceContext:
    sink: Callable[[dict[str, Any]], None]
    task_id: str | None
    assessment_id: str | None
    stage: str
    operation: str
    run_id: str


_CURRENT: ContextVar[TraceContext | None] = ContextVar("research_execution_context", default=None)
_STAGE_OVERRIDE: ContextVar[str | None] = ContextVar("research_execution_stage_override", default=None)
_ACTIVE_RUN_LOCK = threading.Lock()
_ACTIVE_RUNS: dict[str, int] = {}


def active_execution_runs() -> frozenset[str]:
    """Return live run leases in this process without persisting a fake event.

    ContextVars describe one thread only. The reaper can run in a separate
    HTTP thread, so it needs a synchronized process-wide view. A restart
    naturally drops these leases and lets historical abandoned calls expire.
    """
    with _ACTIVE_RUN_LOCK:
        return frozenset(_ACTIVE_RUNS)


@contextmanager
def execution_context(sink, *, task_id=None, assessment_id=None, stage="collection", operation="research", run_id=None):
    context = TraceContext(sink, str(task_id) if task_id else None, str(assessment_id) if assessment_id else None, _name(stage), _name(operation), run_id or f"execution-{secrets.token_hex(12)}")
    token = _CURRENT.set(context)
    stage_token = _STAGE_OVERRIDE.set(None)
    with _ACTIVE_RUN_LOCK:
        _ACTIVE_RUNS[context.run_id] = _ACTIVE_RUNS.get(context.run_id, 0) + 1
    try:
        yield context
    finally:
        with _ACTIVE_RUN_LOCK:
            remaining = _ACTIVE_RUNS.get(context.run_id, 1) - 1
            if remaining:
                _ACTIVE_RUNS[context.run_id] = remaining
            else:
                _ACTIVE_RUNS.pop(context.run_id, None)
        _STAGE_OVERRIDE.reset(stage_token)
        _CURRENT.reset(token)


@contextmanager
def trace_call(*, provider: str, model: str | None = None, operation: str | None = None, stage: str | None = None, details: dict | None = None):
    """Observe a real operation, including its error, without changing fallback.

    A completed transport event means its response was parsed, not that an
    analytical conclusion was independently verified. The surrounding stage
    must record normalization/fallback separately when appropriate.
    """
    context = _CURRENT.get()
    stats = safe_details(details)
    if provider == "deepseek" and operation == "chat-completions" and stats.get("timeoutSeconds"):
        # The transport retries network failures inside one logical call.
        # Persist its complete retry budget at start so a restarted process
        # cannot reap the call after only the first request's timeout.
        try:
            retry_limit = max(0, min(3, int(os.environ.get("DEEPSEEK_RETRY_COUNT", "1"))))
        except (TypeError, ValueError):
            retry_limit = 1
        try:
            backoff = max(0.0, min(5.0, float(os.environ.get("DEEPSEEK_RETRY_BACKOFF_SECONDS", "0.5"))))
        except (TypeError, ValueError):
            backoff = 0.5
        stats["retryLimit"] = retry_limit
        stats["requestBudgetSeconds"] = stats["timeoutSeconds"] * (retry_limit + 1) + sum(min(5.0, backoff * (2 ** index)) for index in range(retry_limit))
    if context is None:
        yield stats
        return
    # Propagate a more precise stage to nested provider transports.
    effective_stage = _name(stage or _STAGE_OVERRIDE.get() or context.stage)
    stage_token = _STAGE_OVERRIDE.set(effective_stage) if stage else None
    base = {
        "callId": f"call-{secrets.token_hex(12)}", "runId": context.run_id,
        "taskId": context.task_id, "assessmentId": context.assessment_id,
        "stage": effective_stage, "provider": _name(provider),
        "model": _name(model) if model else None, "operation": _name(operation or context.operation),
    }
    try:
        # Keep start emission inside the guarded block so a failing sink also
        # restores the stage override before the exception leaves this scope.
        context.sink({**base, "status": "started", "timestamp": _now(), "details": stats, "error": None})
        yield stats
    except BaseException as exc:
        try:
            context.sink({**base, "status": "failed", "timestamp": _now(), "details": safe_details(stats), "error": safe_error(exc)})
        except Exception:
            # Keep the original provider/application error as the failure.
            pass
        raise
    else:
        context.sink({**base, "status": "succeeded", "timestamp": _now(), "details": safe_details(stats), "error": None})
    finally:
        if stage_token is not None:
            _STAGE_OVERRIDE.reset(stage_token)


__all__ = ["execution_context", "trace_call", "safe_details", "safe_error", "TraceContext", "active_execution_runs"]
