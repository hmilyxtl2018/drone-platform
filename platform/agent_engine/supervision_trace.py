"""Read-only, evidence-bound execution trace for the global supervision UI.

This module intentionally keeps the trace smaller than the report/explorer
payload.  It projects durable Run, step, call, event and review records only;
request inputs, prompts, provider endpoints and raw errors never cross this
boundary.  Authorization is delegated to ``visible_runs`` through the same
repeatable-read, read-only transaction used by global supervision.
"""
from __future__ import annotations

from collections import defaultdict, deque
from datetime import datetime, timezone
import re
from typing import Any

from .supervision import (
    TERMINAL, GlobalSupervisionService, SupervisionNotFound,
    SupervisionValidationError, _columns,
    _dt, _elapsed, _has_graph, _has_report, _iso, _json, _now, _table_set,
)


SCHEMA_VERSION = "global-supervision-trace.v1"
_SAFE_CODE = re.compile(r"^[A-Za-z0-9_.:-]{1,96}$")
_CALL_STATUS = {"queued", "running", "succeeded", "completed", "failed",
                "error", "timeout", "cancelled", "unknown"}
_INTERVAL_STATUSES = {"created", "queued", "planning", "running", "waiting-tool",
                      "waiting-human", "retrying", "looping", "validating"}


def _safe_code(value: Any) -> str | None:
    value = str(value) if value is not None else ""
    return value if _SAFE_CODE.fullmatch(value) else None


def _safe_status(value: Any) -> str:
    value = str(value or "unknown")
    return value if value in _CALL_STATUS else "unknown"


def _duration_ms(start: Any, end: Any) -> int | None:
    left, right = _dt(start), _dt(end)
    if not left or not right:
        return None
    duration = round((right - left).total_seconds() * 1000)
    return duration if duration > 0 else None


def _latency(row: dict[str, Any]) -> int | float | None:
    value = row.get("latency_ms")
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
        return value
    return _duration_ms(row.get("started_at"), row.get("finished_at"))


def _fetch(cursor, table: str, ids: list[str], fields: tuple[str, ...]) -> list[dict[str, Any]]:
    """Select only fields present in this deployment's schema."""
    if not ids:
        return []
    available = _columns(cursor, table)
    selected = [field for field in fields if field in available]
    if "run_id" not in available or not selected:
        return []
    cursor.execute("SELECT " + ",".join(selected) + " FROM " + table
                   + " WHERE run_id=ANY(%s)", (ids,))
    return [dict(row) for row in cursor.fetchall()]


def _calls(cursor, tables: set[str], ids: list[str]) -> tuple[list[dict[str, Any]] | None, list[dict[str, Any]] | None]:
    models = None
    tools = None
    if "agent_model_call" in tables:
        models = _fetch(cursor, "agent_model_call", ids,
                        ("call_id", "run_id", "step_run_id", "provider_key",
                         "requested_model", "actual_model", "status", "stage",
                         "operation_key",
                         "retry_count", "latency_ms", "created_at", "started_at",
                         "finished_at", "error_code"))
    if "agent_tool_call" in tables:
        tools = _fetch(cursor, "agent_tool_call", ids,
                       ("call_id", "run_id", "step_run_id", "tool_key",
                        "tool_version", "operation_key", "status", "retry_count",
                        "latency_ms", "created_at", "started_at", "finished_at",
                        "error_code"))
    return models, tools


def _safe_model(row: dict[str, Any]) -> dict[str, Any]:
    return {"callId": row.get("call_id"), "stepRunId": row.get("step_run_id"),
            "modelId": row.get("actual_model") or row.get("requested_model"),
            "requestedModel": row.get("requested_model"),
            "actualModel": row.get("actual_model"),
            "operation": _safe_code(row.get("operation_key") or row.get("stage")),
            "providerKey": _safe_code(row.get("provider_key")),
            "status": _safe_status(row.get("status")), "stage": _safe_code(row.get("stage")),
            "retryCount": row.get("retry_count"), "latencyMs": _latency(row),
            "createdAt": _iso(row.get("created_at")), "startedAt": _iso(row.get("started_at")),
            "finishedAt": _iso(row.get("finished_at")), "errorCode": _safe_code(row.get("error_code"))}


def _safe_tool(row: dict[str, Any]) -> dict[str, Any]:
    return {"callId": row.get("call_id"), "stepRunId": row.get("step_run_id"),
            "toolKey": _safe_code(row.get("tool_key")),
            "toolVersion": _safe_code(row.get("tool_version")),
            "operation": _safe_code(row.get("operation_key") or row.get("tool_key")),
            "status": _safe_status(row.get("status")), "retryCount": row.get("retry_count"),
            "latencyMs": _latency(row), "createdAt": _iso(row.get("created_at")),
            "startedAt": _iso(row.get("started_at")), "finishedAt": _iso(row.get("finished_at")),
            "errorCode": _safe_code(row.get("error_code"))}


def _step_rows(cursor, tables: set[str], ids: list[str]) -> list[dict[str, Any]] | None:
    if "agent_step_run" not in tables:
        return None
    fields = ("step_run_id", "run_id", "step_key", "status", "attempt",
              "started_at", "finished_at", "created_at")
    rows = _fetch(cursor, "agent_step_run", ids, fields)
    return sorted(rows, key=lambda row: (_dt(row.get("created_at")) or datetime.min.replace(tzinfo=timezone.utc),
                                         str(row.get("step_key") or ""), int(row.get("attempt") or 0)))


def _relation_edges(cursor, tables: set[str], visible: set[str]) -> list[dict[str, Any]]:
    """Read actual collaboration edges; task followups remain separate roots."""
    result: list[dict[str, Any]] = []
    if "agent_action_invocation" in tables:
        cols = _columns(cursor, "agent_action_invocation")
        fields = [x for x in ("run_id", "child_run_id", "parent_step_run_id", "parent_step_key", "status", "created_at") if x in cols]
        if {"run_id", "child_run_id"} <= cols:
            cursor.execute("SELECT " + ",".join(fields) + " FROM agent_action_invocation WHERE run_id=ANY(%s)", (list(visible),))
            for row in cursor.fetchall():
                item = dict(row)
                if item.get("child_run_id") in visible and item.get("run_id") in visible:
                    result.append({"fromRunId": item["run_id"], "toRunId": item["child_run_id"],
                                   "kind": "action", "stepRunId": item.get("parent_step_run_id"),
                                   "stepKey": item.get("parent_step_key"), "status": item.get("status"),
                                   "createdAt": _iso(item.get("created_at"))})
    if "agent_run_handoff" in tables:
        cols = _columns(cursor, "agent_run_handoff")
        if {"run_id", "child_run_id"} <= cols:
            fields = [field for field in ("run_id", "child_run_id", "created_at") if field in cols]
            cursor.execute("SELECT " + ",".join(fields) + " FROM agent_run_handoff WHERE run_id=ANY(%s)", (list(visible),))
            for row in cursor.fetchall():
                item = dict(row)
                if item.get("child_run_id") in visible:
                    result.append({"fromRunId": item["run_id"], "toRunId": item["child_run_id"],
                                   "kind": "handoff", "stepRunId": None, "stepKey": None,
                                   "createdAt": _iso(item.get("created_at"))})
    if "agent_run_budget_member" in tables:
        cols = _columns(cursor, "agent_run_budget_member")
        if {"run_id", "parent_run_id"} <= cols:
            fields = [field for field in ("run_id", "parent_run_id", "created_at") if field in cols]
            cursor.execute("SELECT " + ",".join(fields) + " FROM agent_run_budget_member WHERE run_id=ANY(%s)", (list(visible),))
            for row in cursor.fetchall():
                item = dict(row)
                if item.get("parent_run_id") in visible and item.get("parent_run_id") != item.get("run_id"):
                    result.append({"fromRunId": item["parent_run_id"], "toRunId": item["run_id"],
                                   "kind": "budget-lineage", "stepRunId": None, "stepKey": None,
                                   "createdAt": _iso(item.get("created_at"))})
    # Stable de-duplication keeps action + budget records distinct while
    # preventing duplicate rows from repeated historical projections.
    unique = {}
    for edge in result:
        key = (edge["fromRunId"], edge["toRunId"], edge["kind"], edge.get("stepRunId"), edge.get("stepKey"))
        unique[key] = edge
    return sorted(unique.values(), key=lambda edge: (edge["fromRunId"], edge["toRunId"], edge["kind"]))


def _selected_ids(root_id: str, edges: list[dict[str, Any]]) -> set[str]:
    children: dict[str, list[str]] = defaultdict(list)
    for edge in edges:
        children[edge["fromRunId"]].append(edge["toRunId"])
    selected = {root_id}
    queue = deque([root_id])
    while queue:
        parent = queue.popleft()
        for child in children.get(parent, []):
            if child not in selected:
                selected.add(child)
                queue.append(child)
    return selected


def _run_intervals(run: dict[str, Any], events: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str]:
    parsed = []
    ordered_events = sorted(events, key=lambda event: (_dt(event.get("created_at")) or datetime.max.replace(tzinfo=timezone.utc),
                                                       str(event.get("event_id") or "")))
    for event in ordered_events:
        if event.get("event_type") != "run.status":
            continue
        detail = _json(event.get("detail_json"))
        status = detail.get("status") if isinstance(detail.get("status"), str) else None
        if status in _INTERVAL_STATUSES | TERMINAL:
            parsed.append((status, _dt(event.get("created_at"))))
    parsed = [(status, when) for status, when in parsed if when]
    intervals = []
    for index, (status, started) in enumerate(parsed):
        if status not in _INTERVAL_STATUSES:
            continue
        ended = parsed[index + 1][1] if index + 1 < len(parsed) else None
        if ended is None and run.get("status") in TERMINAL:
            ended = _dt(run.get("completed_at"))
        if ended is not None and ended <= started:
            ended = None
        intervals.append({"kind": status, "startedAt": _iso(started), "endedAt": _iso(ended),
                          "source": "run.status-event", "coverage": "observed" if ended else "observed-open"})
    # created_at/started_at are real persisted timestamps, but they do not
    # prove every queue transition; expose this as a partial observation.
    if not intervals and _duration_ms(run.get("created_at"), run.get("started_at")) is not None:
        intervals.append({"kind": "pre-start", "startedAt": _iso(run.get("created_at")),
                          "endedAt": _iso(run.get("started_at")), "source": "run-timestamps",
                          "coverage": "partial"})
        return intervals, "partial"
    return intervals, "observed" if intervals else "unknown"


def _evidence_links(output: dict[str, Any]) -> list[dict[str, str]]:
    report = output.get("judgmentReport") if isinstance(output.get("judgmentReport"), dict) else output
    links: list[dict[str, str]] = []
    graph = report.get("ontologyGraph") or report.get("ontology")
    if isinstance(graph, dict):
        for kind, key in (("ontology-node", "nodes"), ("ontology-edge", "edges"), ("ontology-assertion", "assertions")):
            for item in graph.get(key) or []:
                if isinstance(item, dict) and item.get("id") is not None:
                    links.append({"kind": kind, "id": str(item["id"])})
    for item in report.get("evidence") or []:
        if isinstance(item, dict):
            identifier = item.get("evidenceId") or item.get("id") or item.get("sourceId")
            if identifier is not None:
                links.append({"kind": "evidence", "id": str(identifier)})
    unique = {(item["kind"], item["id"]): item for item in links}
    return list(unique.values())[:300]


def _quality(cursor, tables: set[str], run: dict[str, Any], output: dict[str, Any], assessment: dict[str, Any] | None) -> dict[str, Any]:
    review_proofs = []
    if "agent_report_review_snapshot" in tables:
        cols = _columns(cursor, "agent_report_review_snapshot")
        selected = [x for x in ("snapshot_id", "run_id", "report_hash", "source_graph_hash", "created_at") if x in cols]
        if {"snapshot_id", "run_id"} <= cols:
            order = " ORDER BY created_at DESC" if "created_at" in cols else ""
            cursor.execute("SELECT " + ",".join(selected) + " FROM agent_report_review_snapshot WHERE run_id=%s" + order, (run["run_id"],))
            for row in cursor.fetchall():
                item = dict(row)
                event_count = None
                if "agent_report_review_event" in tables and "snapshot_id" in _columns(cursor, "agent_report_review_event"):
                    cursor.execute("SELECT count(*) AS n FROM agent_report_review_event WHERE snapshot_id=%s", (item["snapshot_id"],))
                    event_count = int(cursor.fetchone()["n"])
                review_proofs.append({"snapshotId": item.get("snapshot_id"), "reportHash": item.get("report_hash"),
                                      "sourceGraphHash": item.get("source_graph_hash"), "eventCount": event_count,
                                      "status": "recorded-proof"})
    proof_status = "verified" if assessment and assessment.get("_valid") else "recorded" if review_proofs else "unassessed"
    return {"assessment": {"status": assessment.get("status") if assessment else None,
                            "executionStatus": assessment.get("execution_status") if assessment else None,
                            "valid": bool(assessment and assessment.get("_valid", False)),
                            "evaluatorVersion": assessment.get("evaluator_version") if assessment else None},
            "reviewProofs": review_proofs, "proofStatus": proof_status,
            "ontology": {"available": _has_graph(output), "links": _evidence_links(output)}}


def _budget(cursor, tables: set[str], ids: set[str], origins: dict[str, dict[str, Any]]) -> dict[str, Any]:
    required = {"agent_run_budget", "agent_run_budget_member", "agent_budget_reservation", "agent_budget_settlement"}
    if not required <= tables:
        return {"roots": [], "coverage": "unknown"}
    columns = _columns(cursor, "agent_run_budget_member")
    fields = [field for field in ("run_id", "root_run_id", "parent_run_id") if field in columns]
    if not {"run_id", "root_run_id"} <= columns:
        return {"roots": [], "coverage": "unknown"}
    cursor.execute("SELECT " + ",".join(fields) + " FROM agent_run_budget_member WHERE run_id=ANY(%s)", (list(ids),))
    members = [dict(row) for row in cursor.fetchall()]
    roots = sorted({row["root_run_id"] for row in members if row.get("root_run_id") in origins})
    if not roots:
        return {"roots": [], "coverage": "observed"}
    cursor.execute("SELECT root_run_id,limits_json FROM agent_run_budget WHERE root_run_id=ANY(%s)", (roots,))
    limits = {row["root_run_id"]: (row.get("limits_json") or {}) for row in cursor.fetchall()}
    cursor.execute("SELECT reservation_id,root_run_id,run_id,charges_json FROM agent_budget_reservation WHERE root_run_id=ANY(%s) AND run_id=ANY(%s)", (roots, list(ids)))
    reservations = [dict(row) for row in cursor.fetchall()]
    settlements = {}
    if reservations:
        reservation_ids = [row["reservation_id"] for row in reservations]
        cursor.execute("""SELECT DISTINCT ON (reservation_id) reservation_id,known_json,effective_json
                          FROM agent_budget_settlement WHERE reservation_id=ANY(%s)
                          ORDER BY reservation_id,revision DESC,created_at DESC""", (reservation_ids,))
        settlements = {row["reservation_id"]: dict(row) for row in cursor.fetchall()}
    def add(target, value):
        if isinstance(value, dict):
            for key, number in value.items():
                if isinstance(number, (int, float)) and not isinstance(number, bool):
                    target[key] = target.get(key, 0) + number
    result = []
    for root in roots:
        relevant = [item for item in reservations if item["root_run_id"] == root]
        settled, known, uncertain = {}, {}, {}
        for item in relevant:
            settlement = settlements.get(item["reservation_id"])
            if settlement:
                add(settled, settlement.get("effective_json")); add(known, settlement.get("known_json"))
                reported = settlement.get("known_json") or {}
                add(uncertain, {key: value for key, value in (item.get("charges_json") or {}).items() if key not in reported})
            else:
                add(uncertain, item.get("charges_json"))
        result.append({"rootRunId": root, "limits": limits.get(root, {}),
                       "counters": {"reservations": len(relevant), "settled": settled,
                                     "known": known, "uncertain": uncertain}})
    return {"roots": result, "coverage": "observed"}


def _budget_root_map(cursor, tables: set[str], ids: set[str], origins: dict[str, dict[str, Any]]) -> dict[str, str]:
    """Map each selected Run to its recorded budget root without inference."""
    if "agent_run_budget_member" not in tables:
        return {}
    columns = _columns(cursor, "agent_run_budget_member")
    if not {"run_id", "root_run_id"} <= columns:
        return {}
    cursor.execute("SELECT run_id,root_run_id FROM agent_run_budget_member WHERE run_id=ANY(%s)", (list(ids),))
    return {row["run_id"]: row["root_run_id"] for row in cursor.fetchall()
            if row.get("root_run_id") in origins}


class SupervisionTraceService:
    """Owner-scoped, read-only execution trace projection."""

    def __init__(self, factory, engine=None):
        self.factory = factory
        self.engine = engine
        self._supervision = GlobalSupervisionService(factory, engine)

    def trace(self, actor, run_id: str) -> dict[str, Any]:
        if not isinstance(run_id, str) or not run_id or len(run_id) > 256:
            raise SupervisionValidationError("invalid run id")

        def build(cursor, origins):
            if run_id not in origins:
                raise SupervisionNotFound("run not found")
            tables = _table_set(cursor)
            edge_candidates = _relation_edges(cursor, tables, set(origins))
            selected_ids = _selected_ids(run_id, edge_candidates)
            # Never include a parent merely to provide a label. A selected
            # child exposes parentId only when that parent is in this tree.
            selected_edges = [edge for edge in edge_candidates
                              if edge["fromRunId"] in selected_ids and edge["toRunId"] in selected_ids]
            rows = _fetch(cursor, "agent_run", sorted(selected_ids),
                          ("run_id", "agent_key", "agent_version", "status", "current_step_key",
                           "created_at", "started_at", "updated_at", "completed_at", "output_json"))
            by_id = {row["run_id"]: row for row in rows}
            if run_id not in by_id:
                raise SupervisionNotFound("run not found")
            names = self._supervision._display_names(cursor, tables, {str(row.get("agent_key")) for row in rows})
            steps = _step_rows(cursor, tables, sorted(selected_ids))
            model_rows, tool_rows = _calls(cursor, tables, sorted(selected_ids))
            events = _fetch(cursor, "agent_run_event", sorted(selected_ids), ("event_id", "run_id", "event_type", "step_key", "detail_json", "created_at")) if "agent_run_event" in tables else None
            events_by_run: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for event in events or []:
                events_by_run[event["run_id"]].append(event)
            steps_by_run: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for step in steps or []:
                steps_by_run[step["run_id"]].append(step)
            models_by_step: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
            tools_by_step: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
            model_safe = {row.get("call_id"): _safe_model(row) for row in (model_rows or [])}
            tool_safe = {row.get("call_id"): _safe_tool(row) for row in (tool_rows or [])}
            for row in model_rows or []:
                if row.get("step_run_id"):
                    models_by_step[(row["run_id"], row["step_run_id"])].append(row)
            for row in tool_rows or []:
                if row.get("step_run_id"):
                    tools_by_step[(row["run_id"], row["step_run_id"])].append(row)
            relation_by_step: dict[tuple[str, str], list[str]] = defaultdict(list)
            for edge in selected_edges:
                if edge["kind"] == "action":
                    if edge.get("stepRunId"):
                        relation_by_step[(edge["fromRunId"], edge["stepRunId"])].append(edge["toRunId"])
            assessments = self._assessments(cursor, tables, sorted(selected_ids), rows)
            budget_root_by_run = _budget_root_map(cursor, tables, selected_ids, origins)
            nodes = []
            for rid in sorted(selected_ids, key=lambda value: (0 if value == run_id else 1, value)):
                row = by_id.get(rid)
                if not row:
                    continue
                output = _json(row.get("output_json"))
                assessment = assessments.get(rid)
                node_steps = []
                for step in steps_by_run.get(rid, []):
                    sm = models_by_step[(rid, step.get("step_run_id"))]
                    st = tools_by_step[(rid, step.get("step_run_id"))]
                    children = relation_by_step[(rid, step.get("step_run_id"))]
                    node_steps.append({"stepRunId": step.get("step_run_id"), "stepKey": step.get("step_key"),
                                       "attempt": step.get("attempt"), "status": step.get("status"),
                                       "createdAt": _iso(step.get("created_at")), "startedAt": _iso(step.get("started_at")),
                                       "finishedAt": _iso(step.get("finished_at")),
                                       "elapsedSeconds": _elapsed({"started_at": step.get("started_at"), "completed_at": step.get("finished_at"), "status": step.get("status")}, _now()),
                                       "models": [model_safe.get(item.get("call_id")) for item in sm if item.get("call_id") in model_safe],
                                       "tools": [tool_safe.get(item.get("call_id")) for item in st if item.get("call_id") in tool_safe],
                                       "subAgentRunIds": sorted(set(children)),
                                       "modelCallIds": [item.get("call_id") for item in sm if item.get("call_id")],
                                       "toolCallIds": [item.get("call_id") for item in st if item.get("call_id")]})
                associated_models = {item.get("callId") for step in node_steps for item in step.get("models", [])}
                associated_tools = {item.get("callId") for step in node_steps for item in step.get("tools", [])}
                actual_step_ids = {step.get("stepRunId") for step in node_steps}
                unattached_subagents = [{"runId": edge["toRunId"], "stepKey": edge.get("stepKey"),
                                        "attempt": None,
                                        "association": "step-key-only" if not edge.get("stepRunId") and edge.get("stepKey") else "unknown"}
                                       for edge in selected_edges if edge["kind"] == "action"
                                       and edge["fromRunId"] == rid and edge.get("stepRunId") not in actual_step_ids]
                run_models = [row for row in (model_rows or []) if row.get("run_id") == rid]
                run_tools = [row for row in (tool_rows or []) if row.get("run_id") == rid]
                intervals, interval_coverage = _run_intervals(row, events_by_run.get(rid, []))
                parent_edges = [edge for edge in selected_edges if edge["toRunId"] == rid]
                parent_id = parent_edges[0]["fromRunId"] if parent_edges else None
                quality = _quality(cursor, tables, row, output, assessment)
                node = {"runId": rid, "parentRunId": parent_id, "agentKey": row.get("agent_key"),
                        "displayName": names.get(row.get("agent_key"), row.get("agent_key")),
                        "version": row.get("agent_version"), "status": row.get("status"),
                        "sourceKind": origins.get(rid, {}).get("sourceKind"),
                        "attributionStatus": origins.get(rid, {}).get("attributionStatus"),
                        "lineageRootRunId": origins.get(rid, {}).get("lineageRootRunId") if origins.get(rid, {}).get("lineageRootRunId") in selected_ids else None,
                        "budgetRootId": budget_root_by_run.get(rid), "steps": node_steps,
                        "unattachedSubAgents": unattached_subagents,
                        "unattachedCalls": {"modelCallIds": sorted({item.get("call_id") for item in run_models if item.get("call_id")} - associated_models),
                                             "toolCallIds": sorted({item.get("call_id") for item in run_tools if item.get("call_id")} - associated_tools),
                                             "models": [model_safe[item.get("call_id")] for item in run_models
                                                        if item.get("call_id") not in associated_models and item.get("call_id") in model_safe],
                                             "tools": [tool_safe[item.get("call_id")] for item in run_tools
                                                       if item.get("call_id") not in associated_tools and item.get("call_id") in tool_safe]},
                        "observedIntervals": intervals,
                        "quality": quality,
                        "links": {"report": {"runId": rid, "available": _has_report(output)},
                                  "graph": {"runId": rid, "available": _has_graph(output)},
                                  "evidence": quality["ontology"]["links"]},
                        "coverage": {"steps": "observed" if steps is not None else "unknown",
                                     "calls": "observed" if model_rows is not None and tool_rows is not None else "unknown",
                                     "events": "observed" if events is not None else "unknown",
                                     "intervals": interval_coverage,
                                     "qualityProof": "observed" if assessment or quality["reviewProofs"] else "unknown"}}
                nodes.append(node)
            return {"schemaVersion": SCHEMA_VERSION, "queriedAt": _iso(_now()),
                    "scope": {"kind": "authorized-run", "visibility": "owner-and-verified-lineage"},
                    "rootRunId": run_id, "nodes": nodes, "edges": selected_edges,
                    "coverage": {"lineage": "observed", "steps": "observed" if steps is not None else "unknown",
                                 "calls": "observed" if model_rows is not None and tool_rows is not None else "unknown",
                                 "events": "observed" if events is not None else "unknown",
                                 "qualityProof": "observed" if any(node["coverage"]["qualityProof"] == "observed" for node in nodes) else "unknown"},
                    "budget": _budget(cursor, tables, selected_ids, origins)}
        return self._supervision._read(actor, build)

    @staticmethod
    def _assessments(cursor, tables, ids, rows):
        return GlobalSupervisionService._assessments(cursor, tables, ids, rows)

