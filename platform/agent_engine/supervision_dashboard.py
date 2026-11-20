"""Owner-scoped, read-only quantitative Agent supervision.

Counts, cohorts and quality evidence stay separate. Unknown telemetry is never
substituted with zero, and every metric has a matching Run drilldown.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import math

from .supervision import (
    ACTIVE, TERMINAL, KNOWN_STATUSES, GlobalSupervisionService,
    SupervisionUnavailable, SupervisionValidationError, _columns, _table_set,
    _dt, _iso, _json, _gap_count, _has_report, _call_summary,
    _encode_cursor, _decode_cursor, _cursor_after,
)

VERSION = "supervision-dashboard.v1"
METRICS = frozenset({"live.active", "live.roots", "live.children", "cohort.total", "cohort.completed",
    "cohort.active", "cohort.waiting-human", "cohort.unknown",
    "cohort.failed", "cohort.blocked", "cohort.gaps", "cohort.cancelled", "cohort.clean-completion",
    "delivery.reports", "delivery.accepted", "delivery.evaluated", "delivery.unassessed",
    "latency.completed", "calls.models", "calls.tools", "calls.failed", "calls.cancelled",
    "calls.unknown", "calls.retried", "calls.first-pass", "budget.uncertain", "issues",
    "period.closed", "period.failed"} | {
        "calls." + kind + "." + suffix for kind in ("models", "tools")
        for suffix in ("failed", "retried", "first-pass")})


def metric(numerator, denominator, *, unknown=0, sample=None, key=""):
    sample = denominator if sample is None else sample
    return {"value": numerator / denominator if denominator else None,
            "numerator": numerator, "denominator": denominator,
            "unknownCount": unknown, "sampleSize": sample,
            "coverage": sample / denominator if denominator else None,
            "metricKey": key, "unit": "ratio"}


def counts(rows):
    status = Counter(row.get("status") for row in rows)
    return {"total": len(rows), "active": sum(r.get("status") in ACTIVE for r in rows),
        "executing": sum(r.get("status") in ACTIVE - {"waiting-human"} for r in rows),
        "completed": status["completed"], "withGaps": status["completed-with-gaps"],
        "failed": status["failed"], "blocked": status["blocked"], "cancelled": status["cancelled"],
        "waitingHuman": status["waiting-human"] + status["requires-human-review"],
        "unknown": sum(r.get("status") not in KNOWN_STATUSES for r in rows)}


def percentiles(values, total, unit):
    values = sorted(v for v in values if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and v >= 0)
    # Nearest rank makes the estimator explicit, including small samples.
    def p(q):
        return values[max(0, math.ceil(q * len(values)) - 1)] if values else None
    return {"p50" + unit: p(.5), "p95" + unit: p(.95), "sampleSize": len(values),
            "unknownCount": total - len(values), "coverage": len(values) / total if total else None,
            "estimator": "nearest-rank"}


def call_metrics(rows, kind=None):
    if rows is None:
        return {name: None for name in ("total", "succeeded", "failed", "cancelled", "unknown", "active",
                "retryAttempts", "retriedOperations", "operationCoverage", "retryRate", "firstPass", "latency")}
    values = list({r["call_id"]: r for r in rows if r.get("call_id")}.values())
    states = Counter(r.get("status") for r in values)
    operations = defaultdict(list)
    for row in values:
        if row.get("operation_key"):
            operations[(row["run_id"], row["operation_key"])].append(row)
    known_calls = sum(bool(r.get("operation_key")) for r in values)
    known_operations = len(operations)
    # A window can contain a retry while the original attempt happened before
    # the window.  Such an operation cannot establish first-pass behaviour;
    # only groups carrying the zero-based attempt are eligible for that metric.
    closed_operations = []
    incomplete_operations = 0
    known_retry_operations = 0
    retried = 0
    for group in operations.values():
        ordinals_known = all(type(row.get("retry_count")) is int and row["retry_count"] >= 0 for row in group)
        has_retry = any(type(row.get("retry_count")) is int and row["retry_count"] > 0 for row in group)
        if has_retry or ordinals_known:
            known_retry_operations += 1
            retried += has_retry
        if not all(row.get("status") in {"succeeded", "completed", "failed", "error", "timeout", "cancelled"} for row in group):
            incomplete_operations += 1
            continue
        if not ordinals_known or not any(row["retry_count"] == 0 for row in group):
            incomplete_operations += 1
            continue
        closed_operations.append(group)
    first_pass = sum(any(type(row.get("retry_count")) is int and row["retry_count"] == 0
                         and row.get("status") in {"succeeded", "completed"} for row in group) for group in closed_operations)
    # Zero is the legacy default, so it cannot prove a measured duration.
    durations = [r["latency_ms"] for r in values if isinstance(r.get("latency_ms"), (int, float)) and r["latency_ms"] > 0]
    result = {"total": len(values), "succeeded": states["succeeded"] + states["completed"],
        "failed": states["failed"] + states["timeout"] + states["error"], "cancelled": states["cancelled"],
        "active": states["running"] + states["queued"],
        "unknown": sum(r.get("status") not in {"succeeded", "completed", "failed", "timeout", "error", "cancelled", "running", "queued"} for r in values),
        "retryAttempts": sum(isinstance(r.get("retry_count"), int) and r["retry_count"] > 0 for r in values),
        "retriedOperations": retried if known_operations else None,
        # This is physical-call coverage; retryRate and firstPass are ratios
        # over logical operation groups and expose their own unknown count.
        "operationCoverage": known_calls / len(values) if values else None,
        "retryRate": metric(retried, known_retry_operations, unknown=known_operations - known_retry_operations,
                             sample=known_retry_operations, key="calls." + (kind + "." if kind else "") + "retried"),
        "firstPass": metric(first_pass, len(closed_operations),
                             unknown=incomplete_operations,
                             sample=len(closed_operations), key="calls." + (kind + "." if kind else "") + "first-pass"),
        "latency": percentiles(durations, len(values), "Ms")}
    result.update(logicalOperations=known_operations, unkeyedAttempts=len(values) - known_calls,
                  incompleteOperations=incomplete_operations)
    result["retryRate"].update(population="observed-keyed-operations",
                               coverage=known_retry_operations / known_operations if known_operations else None,
                               unknownUnit="logical-operations-with-unknown-retry-ordinal")
    result["firstPass"].update(population="closed-operations-with-initial-attempt",
                               coverage=len(closed_operations) / known_operations if known_operations else None,
                               unknownUnit="incomplete-logical-operations")
    return result


class DashboardService(GlobalSupervisionService):
    """Reuse the common read-only transaction and visibility authority."""

    @staticmethod
    def _validate(agent_key, layer, version):
        GlobalSupervisionService._validate_agent_key(agent_key)
        if layer is not None and layer not in {"application", "action"}:
            raise SupervisionValidationError("layer must be application or action")
        if version is not None and (not isinstance(version, str) or not version or len(version) > 100):
            raise SupervisionValidationError("invalid version")

    @staticmethod
    def _root(row, origins):
        return (origins.get(row["run_id"]) or {}).get("lineageRootRunId") or row["run_id"]

    @staticmethod
    def _in_window(value, start, end):
        at = _dt(value)
        return at is not None and start <= at <= end

    @staticmethod
    def _select(cursor, table, ids, fields, columns):
        if not ids:
            return []
        available = columns.get(table, set())
        projected = [field if field in available else "NULL AS " + field for field in fields]
        cursor.execute("SELECT " + ",".join(projected) + " FROM " + table + " WHERE run_id=ANY(%s)", (list(ids),))
        return [dict(row) for row in cursor.fetchall()]

    def _snapshot(self, cursor, origins, hours, agent_key, layer, version):
        now = datetime.now(timezone.utc); start = now - timedelta(hours=hours)
        cursor.execute("SELECT table_name,column_name FROM information_schema.columns WHERE table_schema=current_schema()")
        columns = defaultdict(set)
        for row in cursor.fetchall():
            columns[row["table_name"]].add(row["column_name"])
        tables = set(columns)
        if not {"agent_run", "agent_definition"} <= tables:
            raise SupervisionUnavailable("supervision catalog or Run telemetry unavailable")
        directory_fields = ["agent_key", "display_name", "status", "current_version"]
        directory_fields += [name for name in ("agent_layer", "runtime_kind") if name in columns["agent_definition"]]
        cursor.execute("SELECT " + ",".join(directory_fields) + " FROM agent_definition ORDER BY agent_key")
        directory = [dict(row) for row in cursor.fetchall()]
        directory_by_key = {row["agent_key"]: row for row in directory}
        history = self._select(cursor, "agent_run", origins,
            ("run_id", "agent_key", "agent_version", "agent_layer", "status", "current_step_key", "created_at", "started_at", "updated_at", "completed_at"), columns)
        for row in history:
            row["root"] = self._root(row, origins)
            row["layer"] = row.get("agent_layer") or directory_by_key.get(row["agent_key"], {}).get("agent_layer")
        scoped = [row for row in history if (not agent_key or row["agent_key"] == agent_key)
                  and (not layer or row.get("layer") == layer)
                  and (not version or row.get("agent_version") == version)]
        ids = {row["run_id"] for row in scoped}
        calls = {}
        for kind, table in (("models", "agent_model_call"), ("tools", "agent_tool_call")):
            calls[kind] = self._select(cursor, table, ids,
                ("call_id", "run_id", "status", "retry_count", "operation_key", "latency_ms", "created_at"), columns) if table in tables else None
        # Load only compact output metadata for historical rows. Full outputs
        # are fetched solely for report rows carrying a goal assessment.
        report_meta = {}
        if ids:
            cursor.execute("""WITH q AS (SELECT run_id,CASE WHEN jsonb_typeof(output_json::jsonb->'judgmentReport')='object'
                THEN output_json::jsonb->'judgmentReport' ELSE output_json::jsonb END AS report FROM agent_run WHERE run_id=ANY(%s))
                SELECT run_id,EXISTS(SELECT 1 FROM jsonb_each(CASE WHEN jsonb_typeof(report)='object' THEN report ELSE '{}'::jsonb END) f
                  WHERE f.key IN ('findings','evidence','ontology','ontologyGraph','summary')
                    AND f.value NOT IN ('null'::jsonb,'[]'::jsonb,'{}'::jsonb,'false'::jsonb,'0'::jsonb,'""'::jsonb)) AS has_report,
                CASE WHEN EXISTS (SELECT 1 FROM jsonb_each(CASE WHEN jsonb_typeof(report)='object' THEN report ELSE '{}'::jsonb END) f
                  WHERE f.key IN ('gaps','evidenceGaps','capabilityGaps','unresolvedQuestions') AND jsonb_typeof(f.value)<>'array') THEN NULL
                WHEN report ?| ARRAY['gaps','evidenceGaps','capabilityGaps','unresolvedQuestions'] THEN
                  (CASE WHEN jsonb_typeof(report->'gaps')='array' THEN jsonb_array_length(report->'gaps') ELSE 0 END)+
                  (CASE WHEN jsonb_typeof(report->'evidenceGaps')='array' THEN jsonb_array_length(report->'evidenceGaps') ELSE 0 END)+
                  (CASE WHEN jsonb_typeof(report->'capabilityGaps')='array' THEN jsonb_array_length(report->'capabilityGaps') ELSE 0 END)+
                  (CASE WHEN jsonb_typeof(report->'unresolvedQuestions')='array' THEN jsonb_array_length(report->'unresolvedQuestions') ELSE 0 END)
                ELSE NULL END AS gap_count,
                CASE WHEN EXISTS (SELECT 1 FROM jsonb_each(CASE WHEN jsonb_typeof(report)='object' THEN report ELSE '{}'::jsonb END) f
                  WHERE f.key IN ('gaps','evidenceGaps','capabilityGaps','unresolvedQuestions') AND jsonb_typeof(f.value)<>'array')
                  THEN TRUE ELSE FALSE END AS malformed_gap,
                CASE WHEN jsonb_typeof(report #> '{quality,gapCount}')='number'
                  AND (report #>> '{quality,gapCount}') ~ '^[0-9]{1,9}$' THEN (report #>> '{quality,gapCount}')::integer
                  ELSE NULL END AS quality_gap_count FROM q""", (list(ids),))
            report_meta = {row["run_id"]: dict(row) for row in cursor.fetchall()}
            for meta in report_meta.values():
                if meta.get("malformed_gap"):
                    # An explicit quality.gapCount cannot repair a malformed
                    # gap collection; retain unknown rather than fabricating 0.
                    meta["gap_count"] = None
                elif meta.get("quality_gap_count") is not None:
                    meta["gap_count"] = max(meta.get("gap_count") or 0, meta["quality_gap_count"])
        cohort = [row for row in scoped if self._in_window(row.get("created_at"), start, now)]
        live = [row for row in scoped if row.get("status") in ACTIVE]
        period_closed = [row for row in scoped if row.get("status") in TERMINAL and self._in_window(row.get("completed_at"), start, now)]
        relevant = {row["run_id"] for row in cohort + live + period_closed}
        assessments = {}
        report_ids = {rid for rid in relevant if report_meta.get(rid, {}).get("has_report")}
        if "agent_goal_assessment" in tables and report_ids:
            cursor.execute("SELECT DISTINCT run_id FROM agent_goal_assessment WHERE run_id=ANY(%s)", (list(report_ids),))
            proof_ids = [row["run_id"] for row in cursor.fetchall()]
            proof_rows = self._select(cursor, "agent_run", proof_ids, ("run_id", "status", "output_json"), columns)
            assessments = self._assessments(cursor, tables, proof_ids, proof_rows)
        window_calls = {kind: None if rows is None else [row for row in rows if self._in_window(row.get("created_at"), start, now)] for kind, rows in calls.items()}
        return {"now": now, "start": start, "hours": hours, "tables": tables, "columns": columns, "origins": origins,
                "directory": directory, "history": history, "scoped": scoped, "cohort": cohort, "live": live,
                "closed": period_closed, "calls": calls, "windowCalls": window_calls,
                "reports": report_meta, "assessments": assessments, "filters": {"agentKey": agent_key, "layer": layer, "version": version}}

    @staticmethod
    def _delivery(snapshot, rows):
        reports = [row for row in rows if snapshot["reports"].get(row["run_id"], {}).get("has_report")]
        valid = [row for row in reports if snapshot["assessments"].get(row["run_id"], {}).get("_valid")]
        accepted = [row for row in valid if row.get("status") in {"completed", "completed-with-gaps"}
                    and (_json(snapshot["assessments"][row["run_id"]].get("document_json"))).get("deliveryStatus") == "met"]
        available = "agent_goal_assessment" in snapshot["tables"]
        return {"reports": len(reports), "evaluated": len(valid) if available else None,
                "accepted": len(accepted) if available else None,
                "unassessed": len(reports) - len(valid) if available else None,
                "genericDeliveries": None,
                "acceptance": metric(len(accepted), len(valid), unknown=len(reports) - len(valid), key="delivery.accepted"),
                "coverage": metric(len(valid), len(reports), unknown=len(reports) - len(valid), sample=len(valid), key="delivery.evaluated")}

    @staticmethod
    def _clean(snapshot, root):
        if root.get("status") != "completed":
            return False
        members = [row for row in snapshot["history"] if row["root"] == root["run_id"]]
        if any(row.get("status") != "completed" for row in members):
            return False
        member_meta = [snapshot["reports"].get(row["run_id"], {}) for row in members]
        if any(meta.get("malformed_gap") for meta in member_meta):
            return None
        member_gaps = [meta.get("gap_count") for meta in member_meta]
        if any(gap is not None and gap > 0 for gap in member_gaps):
            return False
        if any(gap is None for gap in member_gaps):
            return None
        # Absence of a gap field or delivery proof cannot establish a clean
        # task. Only the current, hash-bound basic delivery evaluation can.
        gap = snapshot["reports"].get(root["run_id"], {}).get("gap_count")
        assessment = snapshot["assessments"].get(root["run_id"], {})
        if gap is None or not assessment.get("_valid"):
            return None
        if gap > 0:
            return False
        document = _json(assessment.get("document_json"))
        if document.get("deliveryStatus") != "met":
            return False if document.get("deliveryStatus") == "unmet" else None
        checks = document.get("checks")
        delegation = next((item for item in (checks or []) if isinstance(item, dict) and item.get("id") == "delegation-return"), None)
        if members and len(members) > 1 and (not delegation or delegation.get("status") != "pass"):
            return None
        return True

    def _issues(self, snapshot):
        selected = {row["run_id"]: row for row in snapshot["cohort"] + snapshot["live"]}
        groups = {}
        kinds = {"failed": (0, "high", "运行失败"), "blocked": (1, "high", "运行受阻"),
                 "invalid-assessment": (0, "high", "目标评估与当前产物不匹配"),
                 "gaps": (2, "medium", "分析或证据存在缺口"), "waiting-human": (3, "medium", "存在人工处理等待"),
                 "unassessed": (4, "low", "报告尚无有效目标评估")}
        for row in selected.values():
            rid = row["run_id"]; status = row.get("status"); proof = snapshot["assessments"].get(rid)
            if proof and not proof.get("_valid"):
                kind = "invalid-assessment"
            elif status in {"failed", "blocked"}:
                kind = status
            elif status == "completed-with-gaps" or (snapshot["reports"].get(rid, {}).get("gap_count") or 0) > 0:
                kind = "gaps"
            elif status in {"waiting-human", "requires-human-review"}:
                kind = "waiting-human"
            elif snapshot["reports"].get(rid, {}).get("has_report") and status in TERMINAL and not proof:
                kind = "unassessed"
            else:
                continue
            root = row["root"]
            root_row = next((entry for entry in snapshot["history"] if entry["run_id"] == root), {})
            item = groups.setdefault(root, {"issueKey": "root:" + root, "rootRunId": root,
                "runId": root, "kind": kind, "severity": kinds[kind][1], "title": kinds[kind][2],
                "agentKey": root_row.get("agent_key"), "status": root_row.get("status"), "createdAt": _iso(root_row.get("created_at")),
                "constituentRunIds": [], "kinds": [], "objectRef": {"kind": "run", "id": root}})
            if kinds[kind][0] < kinds[item["kind"]][0]:
                item.update(kind=kind, severity=kinds[kind][1], title=kinds[kind][2])
            item["constituentRunIds"].append(rid)
            if kind not in item["kinds"]:
                item["kinds"].append(kind)
        result = list(groups.values())
        result.sort(key=lambda row: (kinds[row["kind"]][0], -(_dt(row["createdAt"]).timestamp() if _dt(row["createdAt"]) else 0), row["rootRunId"]))
        return result

    @staticmethod
    def _period_events(cursor, snapshot):
        if "periodEvents" in snapshot:
            return snapshot["periodEvents"]
        available = {"event_type", "detail_json", "created_at", "run_id"} <= snapshot["columns"].get("agent_run_event", set())
        roots = {row["run_id"] for row in snapshot["scoped"] if row["root"] == row["run_id"]}
        result = {"available": available, "closed": [], "failed": []}
        if available and roots:
            # Count each root at its latest closure in the window. A recovered
            # failure remains a failure observation in a separate root series.
            for kind, statuses in (("closed", TERMINAL), ("failed", {"failed"})):
                cursor.execute("""SELECT DISTINCT ON (run_id) run_id,created_at,detail_json->>'status' AS status FROM agent_run_event
                    WHERE run_id=ANY(%s) AND event_type='run.status' AND created_at >= %s AND created_at <= %s
                      AND detail_json->>'status'=ANY(%s) ORDER BY run_id,created_at DESC""",
                    (list(roots), snapshot["start"], snapshot["now"], list(statuses)))
                result[kind] = [dict(row) for row in cursor.fetchall()]
        snapshot["periodEvents"] = result
        return result

    def _timeline(self, cursor, snapshot):
        hours = snapshot["hours"]; step = timedelta(hours=1 if hours == 24 else 24)
        start, now = snapshot["start"], snapshot["now"]
        series = [{"from": _iso(start + i * step), "to": _iso(min(now, start + (i + 1) * step)),
                   "createdRoots": 0, "createdChildren": 0, "closedRoots": 0, "failedRoots": 0}
                  for i in range(int(timedelta(hours=hours) / step))]
        def add(at, key):
            at = _dt(at)
            if at is not None and start <= at <= now:
                index = min(len(series) - 1, int((at - start) / step))
                series[index][key] += 1
        for row in snapshot["scoped"]:
            add(row.get("created_at"), "createdRoots" if row["root"] == row["run_id"] else "createdChildren")
        events = self._period_events(cursor, snapshot)
        event_available = events["available"]
        if event_available:
            for row in events["closed"]:
                add(row.get("created_at"), "closedRoots")
            for row in events["failed"]:
                add(row.get("created_at"), "failedRoots")
        else:
            # Missing event history remains unknown; current terminal state
            # cannot reconstruct failures which recovered during this window.
            for bucket in series:
                bucket["closedRoots"] = bucket["failedRoots"] = None
        return series, event_available

    def _decisions(self, cursor, snapshot):
        needed = {"agent_autopilot_decision", "agent_autopilot_decision_event", "agent_autopilot_run_binding", "agent_autopilot_mission"}
        if not needed <= snapshot["tables"]:
            return None
        ids = [row["run_id"] for row in snapshot["scoped"]]
        if not ids:
            return []
        owners = {row.get("ownerPrincipalId") for row in snapshot["origins"].values() if row.get("ownerPrincipalId")}
        owner = next(iter(owners)) if len(owners) == 1 else None
        cursor.execute("""SELECT DISTINCT ON (d.decision_id) d.decision_id,d.mission_id,d.kind,d.created_at,b.run_id
            FROM agent_autopilot_decision d JOIN agent_autopilot_run_binding b USING(mission_id)
            JOIN agent_autopilot_mission m USING(mission_id)
            WHERE b.run_id=ANY(%s) AND m.principal_id=%s AND NOT EXISTS (
                SELECT 1 FROM agent_autopilot_decision_event e WHERE e.decision_id=d.decision_id AND e.action<>'defer')
            ORDER BY d.decision_id,b.run_id""", (ids, owner))
        return [{"decisionId": row["decision_id"], "missionId": row["mission_id"], "kind": row["kind"],
                 "status": "pending", "createdAt": _iso(row.get("created_at")), "runId": row["run_id"]}
                for row in cursor.fetchall()]

    def _memberships(self, cursor, snapshot, issues):
        cohort, live = snapshot["cohort"], snapshot["live"]
        roots = [row for row in cohort if row["root"] == row["run_id"]]
        report_rows = [row for row in cohort if snapshot["reports"].get(row["run_id"], {}).get("has_report")]
        valid = {row["run_id"] for row in report_rows if snapshot["assessments"].get(row["run_id"], {}).get("_valid")}
        successful = {row["run_id"] for row in report_rows if row.get("status") in {"completed", "completed-with-gaps"}}
        accepted = {rid for rid in valid & successful if _json(snapshot["assessments"][rid].get("document_json")).get("deliveryStatus") == "met"}
        result = {"live.active": {row["run_id"] for row in live},
            "live.roots": {row["run_id"] for row in live if row["root"] == row["run_id"]},
            "live.children": {row["run_id"] for row in live if row["root"] != row["run_id"]},
            "cohort.total": {row["run_id"] for row in cohort},
            "cohort.active": {row["run_id"] for row in roots if row.get("status") in ACTIVE - {"waiting-human"}},
            "cohort.waiting-human": {row["run_id"] for row in roots if row.get("status") in {"waiting-human", "requires-human-review"}},
            "cohort.unknown": {row["run_id"] for row in roots if row.get("status") not in KNOWN_STATUSES},
            "cohort.clean-completion": {row["run_id"] for row in roots if self._clean(snapshot, row) is True},
            "delivery.reports": {row["run_id"] for row in report_rows}, "delivery.accepted": accepted,
            "delivery.evaluated": valid, "delivery.unassessed": {row["run_id"] for row in report_rows} - valid,
            "latency.completed": {row["run_id"] for row in snapshot["closed"] if row["root"] == row["run_id"]
                and _dt(row.get("started_at")) and _dt(row.get("completed_at"))
                and _dt(row.get("completed_at")) >= _dt(row.get("started_at"))},
            "issues": {rid for issue in issues for rid in issue["constituentRunIds"]}}
        for suffix, status in (("completed", "completed"), ("failed", "failed"), ("blocked", "blocked"),
                               ("gaps", "completed-with-gaps"), ("cancelled", "cancelled")):
            result["cohort." + suffix] = {row["run_id"] for row in roots if row.get("status") == status}
        for kind in ("models", "tools"):
            rows = snapshot["windowCalls"][kind] or []
            result["calls." + kind] = {row["run_id"] for row in rows}
            result["calls." + kind + ".failed"] = {row["run_id"] for row in rows if row.get("status") in {"failed", "timeout", "error"}}
            result["calls." + kind + ".retried"] = {row["run_id"] for row in rows if (row.get("retry_count") or 0) > 0}
            groups = defaultdict(list)
            for row in rows:
                if row.get("operation_key"):
                    groups[(row["run_id"], row["operation_key"])].append(row)
            result["calls." + kind + ".first-pass"] = {group[0]["run_id"] for group in groups.values()
                if all(row.get("status") in {"succeeded", "completed", "failed", "error", "timeout", "cancelled"}
                       and type(row.get("retry_count")) is int and row["retry_count"] >= 0 for row in group)
                and any(type(row.get("retry_count")) is int and row["retry_count"] == 0
                        and row.get("status") in {"succeeded", "completed"} for row in group)}
        all_calls = (snapshot["windowCalls"]["models"] or []) + (snapshot["windowCalls"]["tools"] or [])
        result["calls.failed"] = {row["run_id"] for row in all_calls if row.get("status") in {"failed", "timeout", "error"}}
        result["calls.cancelled"] = {row["run_id"] for row in all_calls if row.get("status") == "cancelled"}
        result["calls.unknown"] = {row["run_id"] for row in all_calls if row.get("status") not in {"succeeded", "completed", "failed", "timeout", "error", "cancelled", "running", "queued"}}
        result["calls.retried"] = {row["run_id"] for row in all_calls if (row.get("retry_count") or 0) > 0}
        result["calls.first-pass"] = result["calls.models.first-pass"] | result["calls.tools.first-pass"]
        events = self._period_events(cursor, snapshot)
        result["period.closed"] = {row["run_id"] for row in events["closed"]}
        result["period.failed"] = {row["run_id"] for row in events["failed"]}
        result["budget.uncertain"] = set()
        required = {"agent_budget_reservation", "agent_budget_settlement", "agent_run_budget_member"}
        ids = {row["run_id"] for row in cohort + live}
        if required <= snapshot["tables"] and ids:
            cursor.execute("""SELECT r.run_id,r.root_run_id,r.charges_json,s.known_json FROM agent_budget_reservation r
                LEFT JOIN LATERAL (SELECT known_json FROM agent_budget_settlement s WHERE s.reservation_id=r.reservation_id
                  ORDER BY revision DESC,created_at DESC,settlement_id DESC LIMIT 1) s ON true
                WHERE r.run_id=ANY(%s) AND r.root_run_id=ANY(%s)""", (list(ids), list(snapshot["origins"])))
            result["budget.uncertain"] = {row["run_id"] for row in cursor.fetchall()
                if set(_json(row.get("charges_json"))) - set(_json(row.get("known_json")))}
        return result

    def _project(self, cursor, snapshot):
        roots = lambda rows: [row for row in rows if row["root"] == row["run_id"]]
        children = lambda rows: [row for row in rows if row["root"] != row["run_id"]]
        cohort_roots = roots(snapshot["cohort"])
        closed_roots = [row for row in cohort_roots if row.get("status") in TERMINAL]
        clean_states = [self._clean(snapshot, row) for row in closed_roots]
        issues = self._issues(snapshot)
        memberships = self._memberships(cursor, snapshot, issues)
        agents = []
        for definition in snapshot["directory"]:
            key = definition["agent_key"]
            history = [row for row in snapshot["history"] if row["agent_key"] == key]
            window = [row for row in snapshot["cohort"] if row["agent_key"] == key]
            live = [row for row in snapshot["live"] if row["agent_key"] == key]
            closed = [row for row in snapshot["closed"] if row["agent_key"] == key]
            elapsed = [(_dt(row["completed_at"]) - _dt(row["started_at"])).total_seconds() for row in closed if _dt(row.get("started_at")) and _dt(row.get("completed_at"))]
            version_scope = [row for row in snapshot["scoped"] if row["agent_key"] == key]
            version_ids = {row["run_id"] for row in version_scope}
            resource_scope = list({row["run_id"]: row for row in window + live}.values())
            row_origins = snapshot["origins"]
            row_resources = self._budget_metrics(cursor, snapshot["tables"], resource_scope, row_origins) if resource_scope else {"sharedBudgetRoots": 0, "counters": {}, "accounting": "no-selected-task-roots"}
            agents.append({"agentKey": key, "displayName": definition.get("display_name") or key,
                "layer": definition.get("agent_layer"), "runtimeKind": definition.get("runtime_kind"),
                "status": definition.get("status"), "version": definition.get("current_version"),
                "versions": sorted({row.get("agent_version") for row in history if row.get("agent_version")} | {definition.get("current_version") or "unknown"}),
                "readiness": {**self._readiness(cursor, snapshot["tables"], definition),
                              "scope": "current-registered-version", "version": definition.get("current_version")},
                "history": {"rootTasks": counts(roots(history)), "subRuns": counts(children(history)), "scope": "all-authorized-history"},
                "windowMetrics": {"rootTasks": counts(roots(window)), "subRuns": counts(children(window)),
                    "live": {"rootTasks": counts(roots(live)), "subRuns": counts(children(live))},
                    "calls": {kind: call_metrics(None if rows is None else [row for row in rows if row["run_id"] in version_ids], kind) for kind, rows in snapshot["windowCalls"].items()},
                    "deliveries": self._delivery(snapshot, window),
                    "latency": {**percentiles(elapsed, len(closed), "Seconds"),
                                "scope": "agent-closed-runs-wall-clock"}, "resources": row_resources,
                    "cost": {"amount": None, "currency": None, "status": "unmeasured"}}})
        period_roots = roots(snapshot["closed"])
        durations = [(end - start).total_seconds() for row in period_roots
            if (start := _dt(row.get("started_at"))) is not None and (end := _dt(row.get("completed_at"))) is not None and end >= start]
        latency = percentiles(durations, len(period_roots), "Seconds")
        latency.update(metricKey="latency.completed", scope="closed-in-window-root-wall-clock")
        timeline, event_available = self._timeline(cursor, snapshot)
        resource_rows = list({row["run_id"]: row for row in snapshot["cohort"] + snapshot["live"]}.values())
        # Filters narrow usage rows, while a visible parent budget root still
        # supplies the shared limit. Parent/sibling usage is not included.
        selected_origins = snapshot["origins"]
        resources = self._budget_metrics(cursor, snapshot["tables"], resource_rows, selected_origins)
        resources.update(scope="selected-created-cohort-plus-active-runs", metricKey="budget.uncertain")
        decisions = self._decisions(cursor, snapshot)
        calls = {kind: call_metrics(rows, kind) for kind, rows in snapshot["windowCalls"].items()}
        quality = self._professional_quality(cursor, snapshot)
        coverage = {"registeredAgents": len(agents), "visibleAgents": len(agents),
            "visibleRuns": len(snapshot["scoped"]), "cohortRuns": len(snapshot["cohort"]),
            "activeRuns": len(snapshot["live"]), "runTimeUnknown": sum(_dt(row.get("created_at")) is None for row in snapshot["scoped"]),
            "attribution": {state: sum(snapshot["origins"][row["run_id"]].get("attributionStatus") == state for row in snapshot["scoped"]) for state in ("recorded", "derived")},
            "telemetry": {"models": snapshot["windowCalls"]["models"] is not None, "tools": snapshot["windowCalls"]["tools"] is not None,
                          "goalAssessments": "agent_goal_assessment" in snapshot["tables"], "statusEvents": event_available},
            "limitations": ["仅显示当前用户有权查看的运行；未知或冲突归属不授权访问。", "目录历史与窗口创建批次分开计量。",
                "延迟为墙钟时间，未扣除等待；默认零延迟不计为已测样本。", "报告验收仅涵盖基础交付与追溯，通用非报告产物暂未计量。"]}
        clean_metric = metric(sum(state is True for state in clean_states), len(clean_states),
            unknown=sum(state is None for state in clean_states), sample=sum(state is not None for state in clean_states), key="cohort.clean-completion")
        return {"schemaVersion": VERSION, "queriedAt": snapshot["now"].isoformat(),
            "scope": {"kind": "authorized-platform", "visibility": "owner-and-verified-lineage", **snapshot["filters"]},
            "window": {"hours": snapshot["hours"], "from": snapshot["start"].isoformat(), "to": snapshot["now"].isoformat(), "includesActive": True},
            "agents": agents, "catalog": agents,
            "live": {"rootTasks": counts(roots(snapshot["live"])), "subRuns": counts(children(snapshot["live"]))},
            "cohort": {"rootTasks": counts(cohort_roots), "subRuns": counts(children(snapshot["cohort"])), "cleanCompletion": clean_metric},
            "deliveries": self._delivery(snapshot, snapshot["cohort"]), "latency": latency, "calls": calls, "resources": resources,
            "quality": quality, "pendingDecisions": len(decisions) if decisions is not None else None, "decisions": decisions,
            "attention": {"value": len(issues), "metricKey": "issues"}, "issues": issues[:100], "issuesTotal": len(issues),
            "timeline": timeline, "coverage": coverage,
            "definitions": {"cohort": "按 createdAt 落入窗口统计；未关闭项保留，不能作已完成率分子。",
                "live": "当前所有在途 Run，包含窗口外创建的任务。", "calls": "按调用 createdAt 落入窗口、真实 call_id 去重，取消与失败分别统计。",
                "retryAttempts": "retry_count>0 的唯一物理调用行数，不能累加 retry_count 序号。",
                "retryRate": "窗口内具有operation_key的逻辑操作组中出现重试的比例；operationCoverage另示物理调用的键覆盖率。",
                "firstPass": "仅对窗口内有初始尝试且全部调用已关闭的逻辑操作计首轮成功；缺初始尝试为未知。",
                "timeline": "每根任务取窗口内最后一次关闭事件；失败系列另取最后失败事件，因此恢复成功仍保留失败观察。",
                "cleanCompletion": "已关闭根任务及实际可见子运行均 completed，根有显式零缺口与有效基础验收；缺证明为未知。",
                "accepted": "当前产物、状态和评估哈希匹配且 deliveryStatus=met；不代表专业正确性。",
                "budget": "用量仅来自所选Run；同一可见共享预算根限额只计一次。各Agent行的共享根限额不可相加；known、settled(effective)和uncertain视图重叠不能相加。"},
            "sources": ["agent_run", "agent_run_origin", "agent_model_call", "agent_tool_call", "agent_goal_assessment", "agent_budget_reservation", "agent_budget_settlement"]}, memberships

    def _professional_quality(self, cursor, snapshot):
        report_ids = [row["run_id"] for row in snapshot["cohort"] if row["root"] == row["run_id"]
                      and snapshot["reports"].get(row["run_id"], {}).get("has_report")]
        result = {"status": "unknown", "truthVerified": False, "supportedFindings": None, "reviewedRelations": None,
                  "reports": len(report_ids), "reportsWithRecordedProof": 0, "unknownReports": len(report_ids),
                  "reason": "仅统计绑定不可变生成与复核产物的引文支持证明；不证明事实真实性或审查独立性。"}
        required = {"call_id", "run_id", "stage", "status", "input_artifact_id", "output_artifact_id",
                    "actual_model", "requested_model", "created_at"}
        action_required = {"run_id", "child_run_id", "parent_step_key", "status", "output_artifact_id", "output_artifact_hash"}
        artifact_required = {"artifact_id", "run_id", "artifact_type", "content_json", "content_hash"}
        if (not report_ids or not required <= snapshot["columns"].get("agent_model_call", set())
                or not action_required <= snapshot["columns"].get("agent_action_invocation", set())
                or not artifact_required <= snapshot["columns"].get("agent_run_artifact", set())):
            return result
        cursor.execute("SELECT run_id,output_json FROM agent_run WHERE run_id=ANY(%s)", (report_ids,))
        saved = [dict(row) for row in cursor.fetchall()]
        from .reasoning_repository import ReasoningRepository
        repository = ReasoningRepository()
        finding_total = relation_total = reviewed_reports = 0
        for row in saved:
            output = _json(row.get("output_json")); report = _json(output.get("judgmentReport")) or output
            findings = [item for item in report.get("findings", []) if isinstance(item, dict)] if isinstance(report.get("findings"), list) else []
            eligible_findings = [item for item in findings
                                 if isinstance(item.get("supportReview"), dict)
                                 and _json(item.get("supportReview")).get("reviewCallId")]
            cursor.execute("SELECT child_run_id FROM agent_action_invocation WHERE run_id=%s", (row["run_id"],))
            if any(child["child_run_id"] not in snapshot["origins"] for child in cursor.fetchall()):
                continue
            try:
                finding_proofs = repository.validate_finding_reviews(row["run_id"], eligible_findings, cursor=cursor)
                relations = [{**relation, "perspectiveKey": item.get("perspectiveKey"),
                              **({"findingId": item["findingId"]} if item.get("findingId") else {})}
                             for item in findings
                             for relation in (item.get("relations", []) if isinstance(item.get("relations", []), list) else [])
                             if isinstance(relation, dict)]
                relation_proofs = repository.validate_support_reviews(row["run_id"], relations, cursor=cursor)
            except (ValueError, TypeError, KeyError, AttributeError):
                continue
            # One report may repeat a finding/relation in multiple display
            # perspectives. Count each immutable proof once for stable KPIs.
            finding_proofs = list({(p.get("findingId"), p.get("perspectiveKey"), p.get("statement")): p
                                   for p in finding_proofs}.values())
            relation_proofs = list({p.get("bindingHash"): p for p in relation_proofs if p.get("bindingHash")}.values())
            if finding_proofs or relation_proofs:
                reviewed_reports += 1
                finding_total += len(finding_proofs); relation_total += len(relation_proofs)
        if reviewed_reports:
            result.update(status="recorded", supportedFindings=finding_total, reviewedRelations=relation_total,
                          reportsWithRecordedProof=reviewed_reports, unknownReports=len(report_ids) - reviewed_reports)
        return result

    def dashboard(self, actor, window_hours=24, agent_key=None, layer=None, version=None):
        hours = self._validate_window(window_hours)
        self._validate(agent_key, layer, version)
        def build(cursor, origins):
            snapshot = self._snapshot(cursor, origins, hours, agent_key, layer, version)
            return self._project(cursor, snapshot)[0]
        return self._read(actor, build)

    def runs(self, actor, window_hours=24, agent_key=None, layer=None, version=None,
             metric=None, status=None, cursor=None, limit=30):
        hours = self._validate_window(window_hours)
        self._validate(agent_key, layer, version)
        if metric is not None and metric not in METRICS:
            raise SupervisionValidationError("invalid metric")
        if status is not None and status not in KNOWN_STATUSES:
            raise SupervisionValidationError("invalid status")
        if type(limit) is not int or isinstance(limit, bool) or not 1 <= limit <= 100:
            raise SupervisionValidationError("limit must be between 1 and 100")
        cursor_at, cursor_id = _decode_cursor(cursor)
        def build(c, origins):
            snapshot = self._snapshot(c, origins, hours, agent_key, layer, version)
            # A Run page does not display catalog readiness, professional proof
            # totals or per-Agent resource matrices. Reuse the exact membership
            # selector without paying to build the whole Dashboard again.
            memberships = self._memberships(c, snapshot, self._issues(snapshot))
            selected_ids = memberships[metric] if metric else {row["run_id"] for row in snapshot["cohort"] + snapshot["live"]}
            rows = [row for row in snapshot["scoped"] if row["run_id"] in selected_ids and (not status or row.get("status") == status)]
            rows.sort(key=lambda row: (_dt(row.get("created_at")) or datetime.min.replace(tzinfo=timezone.utc), row["run_id"]), reverse=True)
            remaining = [row for row in rows if _cursor_after(row, cursor_at, cursor_id)]
            page = remaining[:limit]
            names = {row["agent_key"]: row.get("display_name") or row["agent_key"] for row in snapshot["directory"]}
            models, tools = snapshot["calls"]["models"], snapshot["calls"]["tools"]
            items = []
            for row in page:
                origin = origins[row["run_id"]]
                assessment = snapshot["assessments"].get(row["run_id"], {})
                item = self._run_item(row, assessment, snapshot["now"], models, tools, names)
                item.update(rootRunId=row["root"], parentRunId=origin.get("parentRunId"),
                    sourceKind=origin.get("sourceKind"), attributionStatus=origin.get("attributionStatus"), layer=row.get("layer"))
                # _run_item intentionally has no raw output. Use the compact
                # recorded metadata for quality instead of inferring zero.
                item["quality"] = {"gapCount": snapshot["reports"].get(row["run_id"], {}).get("gap_count"),
                    "assessmentStatus": "evaluated" if assessment.get("_valid") else "unassessed",
                    "status": "gaps" if (snapshot["reports"].get(row["run_id"], {}).get("gap_count") or 0) > 0 or row.get("status") == "completed-with-gaps" else "unknown"}
                items.append(item)
            call_count = None
            if metric and metric.startswith("calls."):
                parts = metric.split(".")
                kind = parts[1] if parts[1] in {"models", "tools"} else None
                selector = parts[-1] if parts[-1] not in {"models", "tools"} else None
                selected_rows = {row["run_id"] for row in rows}
                matching = [(call_kind, row) for call_kind, call_rows in snapshot["windowCalls"].items()
                            for row in call_rows or [] if row["run_id"] in selected_rows and (not kind or kind == call_kind)]
                allowed = {"succeeded", "completed", "failed", "timeout", "error", "cancelled", "running", "queued"}
                matching = [(call_kind, row) for call_kind, row in matching if
                    (selector is None or selector == "failed" and row.get("status") in {"failed", "timeout", "error"}
                     or selector == "cancelled" and row.get("status") == "cancelled"
                     or selector == "unknown" and row.get("status") not in allowed
                     or selector == "retried" and (row.get("retry_count") or 0) > 0
                     or selector == "first-pass" and type(row.get("retry_count")) is int
                         and row["retry_count"] == 0 and row.get("status") in {"succeeded", "completed"})]
                call_count = len({(call_kind, row["call_id"]) for call_kind, row in matching if row.get("call_id")})
            return {"schemaVersion": VERSION, "queriedAt": snapshot["now"].isoformat(),
                    "scope": {"kind": "authorized-platform", "visibility": "owner-and-verified-lineage", **snapshot["filters"]},
                    "window": {"hours": hours, "from": snapshot["start"].isoformat(), "to": snapshot["now"].isoformat(), "includesActive": True},
                    "metric": metric, "total": len(rows), "totalRootTasks": len({row["root"] for row in rows}),
                    "unit": "affected-runs" if metric == "issues" or metric and metric.startswith("calls.") else "runs",
                    "callCount": call_count, "items": items,
                    "nextCursor": _encode_cursor(page[-1].get("created_at"), page[-1]["run_id"]) if page and len(remaining) > len(page) else None}
        return self._read(actor, build)



