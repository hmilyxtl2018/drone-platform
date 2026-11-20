"""Read-only, owner-scoped global supervision projections.

The service deliberately lives beside the DSH adapter instead of the admin
repository.  Every projection is built from one repeatable-read transaction
and starts with :func:`run_origin.visible_runs`; a client supplied tenant or
lineage hint is never used as an authorization signal.
"""
from __future__ import annotations

import base64
import binascii
import json
import threading
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from .repository import as_json
from .run_origin import visible_runs


SCHEMA_VERSION = "global-supervision.v1"
ALLOWED_WINDOWS = (24, 168, 720)
TERMINAL = {"completed", "completed-with-gaps", "requires-human-review",
            "blocked", "failed", "cancelled"}
ACTIVE = {"created", "queued", "planning", "running", "waiting-tool",
          "waiting-human", "retrying", "looping", "validating"}
QUALITY_STATUSES = {"passed", "gaps", "unknown", "not-applicable"}
KNOWN_STATUSES = ACTIVE | TERMINAL


class SupervisionValidationError(ValueError):
    """A bounded, client-fixable query error."""


class SupervisionNotFound(LookupError):
    """A Run is absent or is outside the caller's visible scope."""


class SupervisionUnavailable(RuntimeError):
    """The projection could not be read without fabricating a result."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()
    return str(value)


def _dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _json(value: Any) -> dict[str, Any]:
    parsed = as_json(value)
    return parsed if isinstance(parsed, dict) else {}


def _table_set(cursor) -> set[str]:
    cursor.execute("SELECT table_name FROM information_schema.tables WHERE table_schema=current_schema()")
    return {str(row["table_name"]) for row in cursor.fetchall()}


def _columns(cursor, table: str) -> set[str]:
    cursor.execute("""SELECT column_name FROM information_schema.columns
                     WHERE table_schema=current_schema() AND table_name=%s""", (table,))
    return {str(row["column_name"]) for row in cursor.fetchall()}


def _safe_count(rows: list[dict[str, Any]], statuses: set[str] | None = None) -> int | None:
    if rows is None:
        return None
    if statuses is None:
        return len(rows)
    return sum(1 for row in rows if row.get("status") in statuses)


def _call_summary(rows: list[dict[str, Any]] | None) -> dict[str, int | None]:
    if rows is None:
        return {"total": None, "succeeded": None, "failed": None, "retries": None}
    # A provider can retry the same logical attempt.  Call IDs are the durable
    # identity and are therefore the only unit counted by this projection.
    unique: dict[str, dict[str, Any]] = {}
    for row in rows:
        call_id = row.get("call_id") or row.get("callId")
        if call_id is not None:
            unique[str(call_id)] = row
    values = list(unique.values())
    failed = {"failed", "error", "timeout", "cancelled"}
    return {
        "total": len(values),
        "succeeded": sum(1 for row in values if row.get("status") in {"succeeded", "completed"}),
        "failed": sum(1 for row in values if row.get("status") in failed),
        # Gateways persist one row for every physical attempt and retry_count
        # is the attempt's zero-based ordinal. Summing 0+1+2 overcounts two
        # retries as three; count the actual retry rows instead.
        "retries": sum(1 for row in values if int(row.get("retry_count") or 0) > 0),
    }


def _gap_count(output: dict[str, Any]) -> int | None:
    if not output:
        return None
    report = output.get("judgmentReport") if isinstance(output.get("judgmentReport"), dict) else output
    found = False
    total = 0
    for key in ("gaps", "evidenceGaps", "capabilityGaps", "unresolvedQuestions"):
        value = report.get(key)
        if isinstance(value, list):
            found = True
            total += len(value)
    # A nested report may carry a quality section with an explicit gap count.
    quality = report.get("quality")
    if isinstance(quality, dict) and isinstance(quality.get("gapCount"), int):
        found = True
        total = max(total, quality["gapCount"])
    return total if found else None


def _has_report(output: dict[str, Any]) -> bool:
    if not output:
        return False
    report = output.get("judgmentReport")
    if isinstance(report, dict):
        return bool(report.get("findings") or report.get("evidence") or report.get("ontology")
                    or report.get("ontologyGraph") or report.get("summary"))
    return bool(output.get("findings") or output.get("evidence") or output.get("ontology")
                or output.get("ontologyGraph") or output.get("summary"))


def _has_graph(output: dict[str, Any]) -> bool:
    if not output:
        return False
    report = output.get("judgmentReport") if isinstance(output.get("judgmentReport"), dict) else output
    graph = report.get("ontologyGraph") or report.get("ontology")
    if not isinstance(graph, dict):
        return False
    return any(isinstance(graph.get(key), list) and graph.get(key) for key in ("nodes", "edges", "assertions"))


def _elapsed(run: dict[str, Any], now: datetime) -> int | None:
    start = _dt(run.get("started_at")) or _dt(run.get("created_at"))
    end = _dt(run.get("completed_at"))
    if end is None and run.get("status") in ACTIVE:
        end = now
    if start is None or end is None:
        return None
    return max(0, round((end - start).total_seconds()))


def _encode_cursor(created_at: Any, run_id: str) -> str:
    payload = {"createdAt": _iso(created_at), "runId": str(run_id)}
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_cursor(value: str | None) -> tuple[datetime | None, str | None]:
    if value is None or value == "":
        return None, None
    if not isinstance(value, str) or len(value) > 1024:
        raise SupervisionValidationError("invalid cursor")
    try:
        padded = value + "=" * (-len(value) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
    except (ValueError, UnicodeError, json.JSONDecodeError, binascii.Error, TypeError):
        raise SupervisionValidationError("invalid cursor") from None
    if not isinstance(data, dict) or not isinstance(data.get("runId"), str) or not data["runId"]:
        raise SupervisionValidationError("invalid cursor")
    created = _dt(data.get("createdAt"))
    if data.get("createdAt") is not None and created is None:
        raise SupervisionValidationError("invalid cursor")
    return created, data["runId"]


def _cursor_after(run: dict[str, Any], cursor_created: datetime | None, cursor_id: str | None) -> bool:
    if cursor_id is None:
        return True
    created = _dt(run.get("created_at"))
    if created is None or cursor_created is None:
        return str(run.get("run_id")) < cursor_id
    return created < cursor_created or (created == cursor_created and str(run.get("run_id")) < cursor_id)


def _status_quality(run: dict[str, Any], gap_count: int | None, assessment: dict[str, Any] | None) -> dict[str, Any]:
    valid_assessment = bool(assessment and assessment.get("_valid", False))
    assessment_status = "evaluated" if valid_assessment else "unassessed"
    if gap_count is None:
        status = "unknown"
    elif gap_count > 0 or run.get("status") == "completed-with-gaps":
        status = "gaps"
    elif (valid_assessment and assessment.get("status") == "met"
          and run.get("status") == "completed"):
        status = "passed"
    elif run.get("status") in {"failed", "blocked", "cancelled"}:
        status = "unknown"
    else:
        status = "unknown"
    return {"status": status, "gapCount": gap_count, "assessmentStatus": assessment_status}


class GlobalSupervisionService:
    """Owner-scoped projection used by the global DSH supervision workspace."""

    def __init__(self, factory, engine=None):
        self.factory = factory
        self.engine = engine
        self._lock = threading.RLock()

    def _read(self, actor, callback):
        try:
            with self._lock:
                with self.factory() as connection:
                    with connection.cursor() as cursor:
                        # No write is allowed on this connection.  Keep every
                        # selector and aggregation in this repeatable snapshot.
                        cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
                        origins = visible_runs(cursor, actor)
                        return callback(cursor, origins)
        except (SupervisionValidationError, SupervisionNotFound, SupervisionUnavailable):
            raise
        except Exception as exc:
            raise SupervisionUnavailable("supervision data unavailable") from exc

    def _catalog(self, cursor, tables: set[str], agent_key: str | None, visible: dict[str, dict[str, Any]], run_rows: list[dict[str, Any]], model_rows: list[dict[str, Any]] | None, tool_rows: list[dict[str, Any]] | None):
        if "agent_definition" not in tables:
            raise SupervisionUnavailable("agent catalog unavailable")
        cols = _columns(cursor, "agent_definition")
        selected = ["agent_key", "display_name", "status", "current_version"]
        for name in ("agent_layer", "runtime_kind"):
            if name in cols:
                selected.append(name)
        cursor.execute("SELECT " + ",".join(selected) + " FROM agent_definition ORDER BY agent_key")
        records = [dict(row) for row in cursor.fetchall()]
        # The directory remains complete. Its never-run and execution counters
        # use the caller's full visible history, while overview summary metrics
        # remain bounded by the selected time window.
        history = run_rows
        by_agent: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in history:
            by_agent[str(row["agent_key"])].append(row)
        readiness = {str(row["agent_key"]): self._readiness(cursor, tables, row) for row in records}
        result = []
        for row in records:
            key = str(row["agent_key"])
            runs = by_agent.get(key, [])
            state = readiness.get(key) or {"status": "unknown", "reasons": ["readiness-unavailable"]}
            result.append({
                "agentKey": key,
                "displayName": row.get("display_name") or key,
                "layer": row.get("agent_layer") if row.get("agent_layer") in {"application", "action"} else None,
                "runtimeKind": row.get("runtime_kind") if row.get("runtime_kind") else None,
                "status": row.get("status") or "unknown",
                "version": row.get("current_version"),
                "readiness": state,
                "execution": {"total": len(runs), "active": sum(r.get("status") in ACTIVE for r in runs),
                               "neverRun": not bool(runs), "lastRunAt": _iso(max((_dt(r.get("created_at")) for r in runs if _dt(r.get("created_at"))), default=None))},
                "runs": {"completed": sum(r.get("status") == "completed" for r in runs),
                         "withGaps": sum(r.get("status") == "completed-with-gaps" for r in runs),
                         "failed": sum(r.get("status") == "failed" for r in runs),
                         "blocked": sum(r.get("status") == "blocked" for r in runs)},
                "calls": self._agent_calls(key, runs, model_rows, tool_rows),
                "deliveries": self._agent_deliveries(runs),
            })
        return result

    def _readiness(self, cursor, tables: set[str], row: dict[str, Any]) -> dict[str, Any]:
        status = row.get("status")
        if status in {"disabled", "inactive", "retired", "deprecated"}:
            return {"status": "disabled", "reasons": ["agent-disabled"]}
        # Readiness is a static contract check.  It deliberately performs no
        # provider, network, Firecrawl or model probe.
        reasons: list[str] = []
        version = row.get("current_version")
        if "agent_harness_contract" in tables:
            cursor.execute("""SELECT status FROM agent_harness_contract
                              WHERE agent_key=%s AND profile_version=%s
                                AND status IN ('testing','published')
                              ORDER BY CASE WHEN status='published' THEN 0 ELSE 1 END
                              LIMIT 1""", (row["agent_key"], version))
            if not cursor.fetchone():
                reasons.append("harness-contract-missing")
        else:
            reasons.append("harness-contract-telemetry-unavailable")
        if "agent_execution_baseline" in tables:
            cursor.execute("""SELECT 1 FROM agent_execution_baseline
                              WHERE agent_key=%s AND profile_version=%s LIMIT 1""", (row["agent_key"], version))
            if not cursor.fetchone():
                reasons.append("execution-baseline-missing")
        else:
            reasons.append("execution-baseline-telemetry-unavailable")
        if {"agent_tool_binding", "tool_registry"} <= tables:
            registry_columns = _columns(cursor, "tool_registry")
            agent_binding_columns = _columns(cursor, "agent_tool_binding")
            version_columns = _columns(cursor, "tool_version") if "tool_version" in tables else set()
            detailed = ({"protocol", "endpoint"} <= registry_columns
                        and {"tool_version", "config_json"} <= agent_binding_columns
                        and {"version", "status", "endpoint"} <= version_columns)
            if detailed:
                version_fields = "v.version AS bound_version,v.status AS version_status,v.endpoint AS version_endpoint"
                if "execution_transport" in version_columns:
                    version_fields += ",v.execution_transport"
                cursor.execute("""SELECT b.tool_key,b.tool_version,b.enabled,b.config_json,
                                          t.status AS registry_status,t.protocol,t.endpoint,
                                          """ + version_fields + """
                                  FROM agent_tool_binding b LEFT JOIN tool_registry t USING(tool_key)
                                  LEFT JOIN tool_version v ON v.tool_key=b.tool_key AND v.version=b.tool_version
                                  WHERE b.agent_key=%s""", (row["agent_key"],))
            else:
                reasons.append("tool-binding-details-unavailable")
                cursor.execute("""SELECT b.tool_key,b.enabled,t.status AS registry_status
                                  FROM agent_tool_binding b LEFT JOIN tool_registry t USING(tool_key)
                                  WHERE b.agent_key=%s""", (row["agent_key"],))
            bindings = [dict(item) for item in cursor.fetchall()]
            invalid = [item for item in bindings if bool(item.get("enabled"))
                       and (item.get("registry_status") not in {"testing", "approved", "published"}
                            or (detailed and (not item.get("bound_version")
                                or item.get("version_status") not in {"testing", "approved", "published"}))
                            or ((item.get("execution_transport") or item.get("protocol")) in {"http", "https"}
                                and not (item.get("version_endpoint") or item.get("endpoint"))))]
            if invalid:
                reasons.append("tool-binding-invalid")
        elif "agent_tool_binding" in tables or "tool_registry" in tables:
            reasons.append("tool-binding-telemetry-unavailable")
        if {"agent_model_policy", "provider_registry"} <= tables:
            model_columns = _columns(cursor, "agent_model_policy")
            selected_model = "model_provider,model_name" if "model_name" in model_columns else "model_provider"
            cursor.execute("SELECT " + selected_model + " FROM agent_model_policy WHERE agent_key=%s", (row["agent_key"],))
            policy = cursor.fetchone()
            provider = (policy or {}).get("model_provider")
            if (not policy or not provider or provider == "configured"
                    or ("model_name" in model_columns and not policy.get("model_name"))):
                reasons.append("model-provider-unconfigured")
            else:
                cursor.execute("""SELECT enabled,provider_type FROM provider_registry
                                  WHERE provider_key=%s""", (provider,))
                provider_row = cursor.fetchone()
                if not provider_row or not provider_row.get("enabled") or provider_row.get("provider_type") != "model":
                    reasons.append("model-provider-unavailable")
        elif "agent_model_policy" in tables or "provider_registry" in tables:
            reasons.append("model-provider-telemetry-unavailable")
        if not reasons:
            return {"status": "ready", "reasons": []}
        if any(reason.endswith(("missing", "invalid", "unconfigured")) or reason == "model-provider-unavailable" for reason in reasons):
            return {"status": "unavailable", "reasons": reasons}
        return {"status": "unknown", "reasons": reasons}

    def _agent_calls(self, key: str, runs: list[dict[str, Any]], model_rows: list[dict[str, Any]] | None,
                     tool_rows: list[dict[str, Any]] | None) -> dict[str, Any]:
        run_ids = {r["run_id"] for r in runs}
        models = [row for row in (model_rows or []) if row.get("run_id") in run_ids]
        tools = [row for row in (tool_rows or []) if row.get("run_id") in run_ids]
        return {"models": _call_summary(None if model_rows is None else models),
                "tools": _call_summary(None if tool_rows is None else tools)}

    def _agent_deliveries(self, runs: list[dict[str, Any]]) -> dict[str, Any]:
        deliveries = [r for r in runs if _has_report(_json(r.get("output_json")))]
        # Missing goal proof is explicitly unassessed.  A report-shaped output
        # alone cannot be counted as evaluated.
        evaluated = [r for r in deliveries if r.get("_assessment")
                     and r.get("_assessment", {}).get("_valid")
                     and r.get("_assessment", {}).get("status") in {"met", "partially-met", "unmet", "not-evaluable"}]
        return {"total": len(deliveries), "evaluated": len(evaluated),
                "withGaps": sum(((_gap_count(_json(r.get("output_json"))) or 0) > 0) for r in deliveries),
                "unassessed": len(deliveries) - len(evaluated)}

    def _load(self, cursor, origins: dict[str, dict[str, Any]], window_hours: int | None,
              agent_key: str | None, *, all_history: bool = False, include_output: bool = True):
        tables = _table_set(cursor)
        if "agent_run" not in tables:
            raise SupervisionUnavailable("Run telemetry unavailable")
        now = _now()
        from_time = now - timedelta(hours=window_hours) if window_hours is not None else None
        output_column = "output_json" if include_output else "NULL::jsonb AS output_json"
        cursor.execute("""SELECT run_id,agent_key,agent_version,status,current_step_key,
                    created_at,started_at,updated_at,completed_at,""" + output_column + """
                    FROM agent_run WHERE run_id=ANY(%s)""", (list(origins),))
        rows = [dict(row) for row in cursor.fetchall()]
        selected: list[dict[str, Any]] = []
        for row in rows:
            if agent_key and row.get("agent_key") != agent_key:
                continue
            origin = origins.get(row["run_id"])
            if not origin:
                continue
            row["_origin"] = origin
            active = row.get("status") in ACTIVE
            created = _dt(row.get("created_at"))
            if all_history or active or created is None or (from_time is not None and created >= from_time):
                selected.append(row)
        ids = [r["run_id"] for r in selected]
        models = self._fetch_calls(cursor, "agent_model_call", ids, ("call_id", "run_id", "step_run_id", "provider_key", "requested_model", "actual_model", "status", "stage", "retry_count", "created_at", "error_code")) if "agent_model_call" in tables else None
        tools = self._fetch_calls(cursor, "agent_tool_call", ids, ("call_id", "run_id", "step_run_id", "tool_key", "tool_version", "status", "retry_count", "created_at")) if "agent_tool_call" in tables else None
        # Overview/list do not display step attempts. Detail reads only the
        # selected Run's steps, so avoid loading every historical step here.
        steps = None
        assessments = self._assessments(cursor, tables, ids, selected) if include_output else {}
        for row in selected:
            row["_assessment"] = assessments.get(row["run_id"])
        return tables, selected, assessments, models, tools, steps

    @staticmethod
    def _fetch_calls(cursor, table: str, ids: list[str], fields: tuple[str, ...]) -> list[dict[str, Any]]:
        if not ids:
            return []
        cursor.execute("SELECT " + ",".join(fields) + " FROM " + table + " WHERE run_id=ANY(%s)", (ids,))
        return [dict(row) for row in cursor.fetchall()]

    @staticmethod
    def _display_names(cursor, tables: set[str], agent_keys: set[str]) -> dict[str, str]:
        if "agent_definition" not in tables or not agent_keys:
            return {}
        cursor.execute("SELECT agent_key,display_name FROM agent_definition WHERE agent_key=ANY(%s)", (list(agent_keys),))
        return {row["agent_key"]: row.get("display_name") or row["agent_key"] for row in cursor.fetchall()}

    @staticmethod
    def _assessments(cursor, tables: set[str], ids: list[str], runs: list[dict[str, Any]] | None = None) -> dict[str, dict[str, Any]]:
        if "agent_goal_assessment" not in tables or not ids:
            return {}
        cursor.execute("""SELECT DISTINCT ON (run_id) run_id,status,execution_status,
                    evaluator_version,created_at,document_json,content_hash,output_hash FROM agent_goal_assessment
                    WHERE run_id=ANY(%s) ORDER BY run_id,created_at DESC,assessment_id DESC""", (ids,))
        output_by_id = {row["run_id"]: _json(row.get("output_json")) for row in (runs or [])}
        status_by_id = {row["run_id"]: row.get("status") for row in (runs or [])}
        result = {}
        from .goal_contract import digest
        for raw in cursor.fetchall():
            row = dict(raw)
            output = output_by_id.get(row["run_id"], {})
            valid = True
            try:
                valid = (digest(row.get("document_json")) == row.get("content_hash")
                         and digest(output) == row.get("output_hash")
                         and row.get("execution_status") == status_by_id.get(row["run_id"]))
            except Exception:
                valid = False
            row["_valid"] = bool(valid)
            result[row["run_id"]] = row
        return result

    def _scope(self, agent_key: str | None, window_hours: int, now: datetime) -> dict[str, Any]:
        return {"kind": "authorized-platform", "agentKey": agent_key,
                "visibility": "owner-and-verified-lineage"}

    def _issue_rows(self, rows: list[dict[str, Any]], assessments: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
        result = []
        for row in rows:
            status = row.get("status")
            quality = _status_quality(row, _gap_count(_json(row.get("output_json"))), assessments.get(row["run_id"]))
            origin = row.get("_origin") or {}
            kind = None
            severity = "info"
            title = None
            if row.get("_assessment") and not row["_assessment"].get("_valid", False): kind, severity, title = "invalid-assessment", "high", "目标评估与当前不可变产物不匹配"
            elif status == "failed": kind, severity, title = "failed", "high", "运行失败"
            elif status == "blocked": kind, severity, title = "blocked", "high", "运行受阻"
            elif status == "waiting-human" or status == "requires-human-review": kind, severity, title = "waiting-human", "medium", "运行等待人工处理"
            elif quality["status"] == "gaps": kind, severity, title = "completed-with-gaps", "medium", "已记录分析或证据缺口"
            elif status in TERMINAL and quality["assessmentStatus"] == "unassessed" and _has_report(_json(row.get("output_json"))): kind, severity, title = "missing-assessment", "low", "交付产物尚无有效目标评估"
            if kind:
                result.append({"issueKey": f"{kind}:{row['run_id']}", "kind": kind, "severity": severity,
                               "severityLabel": {"high": "高", "medium": "中", "low": "低", "info": "提示"}.get(severity, "未知"),
                               "title": title, "agentKey": row.get("agent_key"), "runId": row["run_id"],
                               "rootRunId": origin.get("lineageRootRunId"), "stepKey": row.get("current_step_key"),
                               "status": status, "createdAt": _iso(row.get("created_at")),
                               "objectRef": {"kind": "run", "id": row["run_id"]}})
        rank = {"high": 0, "medium": 1, "low": 2, "info": 3}
        result.sort(key=lambda issue: (rank.get(issue["severity"], 99),
                    -(_dt(issue.get("createdAt")).timestamp() if _dt(issue.get("createdAt")) else 0),
                    issue["issueKey"]))
        return result

    def _base_window(self, window_hours: int, now: datetime) -> dict[str, Any]:
        return {"hours": window_hours, "from": (now - timedelta(hours=window_hours)).isoformat(),
                "to": now.isoformat(), "includesActive": True}

    def overview(self, actor, window_hours: int = 24, agent_key: str | None = None) -> dict[str, Any]:
        window_hours = self._validate_window(window_hours)
        self._validate_agent_key(agent_key)

        def build(cursor, origins):
            now = _now()
            tables, all_rows, all_assessments, all_model_rows, all_tool_rows, _ = self._load(cursor, origins, None, None, all_history=True)
            from_time = now - timedelta(hours=window_hours)
            rows = [r for r in all_rows if (not agent_key or r.get("agent_key") == agent_key)
                    and (r.get("status") in ACTIVE or _dt(r.get("created_at")) is None or _dt(r.get("created_at")) >= from_time)]
            assessments = {r["run_id"]: all_assessments[r["run_id"]] for r in rows if r["run_id"] in all_assessments}
            model_rows = [r for r in (all_model_rows or []) if r.get("run_id") in {run["run_id"] for run in rows}] if all_model_rows is not None else None
            tool_rows = [r for r in (all_tool_rows or []) if r.get("run_id") in {run["run_id"] for run in rows}] if all_tool_rows is not None else None
            catalog = self._catalog(cursor, tables, agent_key, origins, all_rows, all_model_rows, all_tool_rows)
            runs = self._run_metrics(rows)
            model_calls = model_rows
            tool_calls = tool_rows
            issues = self._issue_rows(rows, assessments)
            return {"schemaVersion": SCHEMA_VERSION, "queriedAt": now.isoformat(),
                "scope": self._scope(agent_key, window_hours, now), "window": self._base_window(window_hours, now),
                "coverage": {"registeredAgents": len(catalog), "visibleAgents": len(catalog),
                             "agentsWithVisibleRuns": len({r.get('agent_key') for r in rows}),
                             "visibleRuns": len(rows), "attribution": {"recorded": sum((r.get('_origin') or {}).get('attributionStatus') == 'recorded' for r in rows),
                                                                          "derived": sum((r.get('_origin') or {}).get('attributionStatus') == 'derived' for r in rows)}, "limitations": []},
                "summary": {"agents": self._agent_metrics(catalog, rows), "runs": runs,
                            "calls": {"models": _call_summary(model_calls), "tools": _call_summary(tool_calls)},
                            "deliveries": self._delivery_metrics(rows), "pendingDecisions": self._pending_decisions(cursor, tables, rows),
                            "resources": self._budget_metrics(cursor, tables, rows, origins)},
                "agents": catalog, "issues": issues[:100], "issuesTotal": len(issues)}
        return self._read(actor, build)

    def _pending_decisions(self, cursor, tables: set[str], rows: list[dict[str, Any]]) -> int | None:
        if "agent_autopilot_decision" not in tables:
            return None
        ids = [r["run_id"] for r in rows]
        if not ids:
            return 0
        cursor.execute("""SELECT count(DISTINCT d.decision_id) AS n FROM agent_autopilot_decision d
            JOIN agent_autopilot_run_binding b ON b.mission_id=d.mission_id
            WHERE b.run_id=ANY(%s) AND NOT EXISTS (
                SELECT 1 FROM agent_autopilot_decision_event e
                WHERE e.decision_id=d.decision_id AND e.action <> 'defer')""", (ids,))
        return int(cursor.fetchone()["n"])

    def _run_metrics(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        root = [r for r in rows if (r.get("_origin") or {}).get("lineageRootRunId") in {None, r["run_id"]}]
        return {"total": len(rows), "rootRuns": len(root), "childRuns": len(rows) - len(root),
                "active": sum(r.get("status") in ACTIVE for r in rows), "completed": sum(r.get("status") == "completed" for r in rows),
                "withGaps": sum(r.get("status") == "completed-with-gaps" for r in rows), "failed": sum(r.get("status") == "failed" for r in rows),
                "blocked": sum(r.get("status") == "blocked" for r in rows), "cancelled": sum(r.get("status") == "cancelled" for r in rows),
                "waitingHuman": sum(r.get("status") in {'waiting-human','requires-human-review'} for r in rows)}

    def _agent_metrics(self, catalog: list[dict[str, Any]], rows: list[dict[str, Any]]) -> dict[str, Any]:
        readiness = Counter(a.get("readiness", {}).get("status") for a in catalog)
        return {"total": len(catalog), "ready": readiness.get("ready", 0), "unavailable": readiness.get("unavailable", 0),
                "disabled": readiness.get("disabled", 0), "unknown": readiness.get("unknown", 0),
                "neverRun": sum(a.get("execution", {}).get("neverRun", False) for a in catalog),
                "running": len({r.get("agent_key") for r in rows if r.get("status") in ACTIVE})}

    def _delivery_metrics(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        deliveries = [r for r in rows if _has_report(_json(r.get("output_json")))]
        # Preserve the distinction between a recorded evaluation and a missing
        # evaluation; defaulting to valid would erase an important quality gap.
        evaluated = [r for r in deliveries if r.get("_assessment")
                     and r.get("_assessment", {}).get("_valid")]
        return {"total": len(deliveries), "evaluated": len(evaluated),
                "withGaps": sum((_gap_count(_json(r.get("output_json"))) or 0) > 0 for r in deliveries),
                "unassessed": len(deliveries) - len(evaluated)}

    def _budget_metrics(self, cursor, tables: set[str], rows: list[dict[str, Any]], origins: dict[str, dict[str, Any]]) -> dict[str, Any]:
        required = {"agent_run_budget", "agent_run_budget_member", "agent_budget_reservation", "agent_budget_settlement"}
        if not required <= tables:
            return {"sharedBudgetRoots": None, "counters": {}, "accounting": "unknown"}
        ids = [r["run_id"] for r in rows]
        if not ids:
            return {"sharedBudgetRoots": 0, "counters": {"reservations": 0, "settled": {}, "known": {}, "uncertain": {}}, "accounting": "visible-root-scoped"}
        cursor.execute("""SELECT DISTINCT b.root_run_id FROM agent_run_budget_member b
                          WHERE b.run_id=ANY(%s)""", (ids,))
        # Do not infer a budget root from lineage.  The root must itself be a
        # visible, verified Run or its limits/counters remain undisclosed.
        roots = [r["root_run_id"] for r in cursor.fetchall() if r["root_run_id"] in origins]
        if not roots:
            return {"sharedBudgetRoots": 0, "counters": {"reservations": 0, "settled": {}, "known": {}, "uncertain": {}}, "accounting": "visible-root-scoped"}
        cursor.execute("""SELECT reservation_id,root_run_id,run_id,charges_json
                          FROM agent_budget_reservation
                          WHERE root_run_id=ANY(%s) AND run_id=ANY(%s)""", (roots, ids))
        reservations = [dict(row) for row in cursor.fetchall()]
        reservation_ids = [row["reservation_id"] for row in reservations]
        settlements = {}
        if reservation_ids:
            cursor.execute("""SELECT DISTINCT ON (reservation_id) reservation_id,known_json,effective_json,overrun_json
                              FROM agent_budget_settlement WHERE reservation_id=ANY(%s)
                              ORDER BY reservation_id,revision DESC,created_at DESC,settlement_id DESC""", (reservation_ids,))
            settlements = {row["reservation_id"]: dict(row) for row in cursor.fetchall()}
        def add(target, value):
            if not isinstance(value, dict):
                return
            for key, number in value.items():
                if isinstance(number, (int, float)) and not isinstance(number, bool):
                    target[key] = target.get(key, 0) + number
        settled, known, uncertain = {}, {}, {}
        for reservation in reservations:
            settlement = settlements.get(reservation["reservation_id"])
            if settlement:
                add(settled, settlement.get("effective_json"))
                add(known, settlement.get("known_json"))
                # Settlement "known" is per resource dimension. Dimensions
                # not yet reported remain charged at their reservation, while
                # overrun_json is a cumulative root snapshot, never uncertainty.
                reported = settlement.get("known_json") or {}
                add(uncertain, {key: value for key, value in (reservation.get("charges_json") or {}).items()
                                if key not in reported})
            else:
                add(uncertain, reservation.get("charges_json"))
        limits = {}
        cursor.execute("SELECT limits_json FROM agent_run_budget WHERE root_run_id=ANY(%s)", (roots,))
        for row in cursor.fetchall():
            add(limits, row.get("limits_json"))
        return {"sharedBudgetRoots": len(roots),
                "counters": {"reservations": len(reservations), "limits": limits, "settled": settled, "known": known, "uncertain": uncertain},
                "accounting": "latest-settlement-per-reservation;settled-is-effective-charge;uncertain-is-unreported-reservation-dimensions;visible-root-scoped"}

    def runs(self, actor, window_hours: int = 24, agent_key: str | None = None,
             status: str | None = None, cursor: str | None = None, limit: int = 30) -> dict[str, Any]:
        window_hours = self._validate_window(window_hours)
        self._validate_agent_key(agent_key)
        if status is not None and status not in KNOWN_STATUSES:
            raise SupervisionValidationError("invalid status")
        if type(limit) is not int or isinstance(limit, bool) or not 1 <= limit <= 100:
            raise SupervisionValidationError("limit must be between 1 and 100")
        cursor_created, cursor_id = _decode_cursor(cursor)

        def build(c, origins):
            now = _now()
            tables, rows, assessments, model_rows, tool_rows, _ = self._load(c, origins, window_hours, agent_key, include_output=False)
            if status:
                rows[:] = [row for row in rows if row.get("status") == status]
            # Full selection and all quality/call aggregation happen before
            # slicing the page, so pagination can never change totals.
            rows.sort(key=lambda row: ((_dt(row.get("created_at")) or datetime.min.replace(tzinfo=timezone.utc)), str(row["run_id"])), reverse=True)
            total = len(rows)
            remaining = [r for r in rows if _cursor_after(r, cursor_created, cursor_id)]
            page = remaining[:limit]
            # Selection, total and call aggregation cover the complete visible
            # range. Only page quality needs a full saved output/hash; loading
            # historical source bundles for off-page Runs adds no information.
            if page:
                page_ids = [row["run_id"] for row in page]
                c.execute("SELECT run_id,output_json FROM agent_run WHERE run_id=ANY(%s)", (page_ids,))
                outputs = {row["run_id"]: row.get("output_json") for row in c.fetchall()}
                for row in page:
                    row["output_json"] = outputs.get(row["run_id"])
                assessments = self._assessments(c, tables, page_ids, page)
            names = self._display_names(c, tables, {str(r.get("agent_key")) for r in page})
            items = [self._run_item(r, assessments.get(r["run_id"]), now, model_rows, tool_rows, names) for r in page]
            next_cursor = _encode_cursor(page[-1].get("created_at"), page[-1]["run_id"]) if page and len(remaining) > len(page) else None
            return {"schemaVersion": SCHEMA_VERSION, "queriedAt": now.isoformat(), "scope": self._scope(agent_key, window_hours, now),
                    "window": self._base_window(window_hours, now), "total": total, "items": items, "nextCursor": next_cursor}
        return self._read(actor, build)

    def _run_item(self, row: dict[str, Any], assessment: dict[str, Any] | None, now: datetime,
                  model_rows: list[dict[str, Any]] | None = None,
                  tool_rows: list[dict[str, Any]] | None = None,
                  display_names: dict[str, str] | None = None) -> dict[str, Any]:
        origin = row.get("_origin") or {}
        quality = _status_quality(row, _gap_count(_json(row.get("output_json"))), assessment)
        run_id = row["run_id"]
        models = [r for r in (model_rows or []) if r.get("run_id") == run_id] if model_rows is not None else None
        tools = [r for r in (tool_rows or []) if r.get("run_id") == run_id] if tool_rows is not None else None
        return {"runId": run_id, "agentKey": row.get("agent_key"), "displayName": (display_names or {}).get(row.get("agent_key"), row.get("agent_key")),
                "version": row.get("agent_version"), "status": row.get("status"), "sourceKind": origin.get("sourceKind"),
                "attributionStatus": origin.get("attributionStatus"), "parentRunId": origin.get("parentRunId"),
                "rootRunId": origin.get("lineageRootRunId"), "createdAt": _iso(row.get("created_at")),
                "updatedAt": _iso(row.get("updated_at")), "startedAt": _iso(row.get("started_at")),
                "completedAt": _iso(row.get("completed_at")), "currentStepKey": row.get("current_step_key"),
                "elapsedSeconds": _elapsed(row, now), "calls": {"models": _call_summary(models), "tools": _call_summary(tools)},
                "quality": quality}

    def detail(self, actor, run_id: str) -> dict[str, Any]:
        if not isinstance(run_id, str) or not run_id or len(run_id) > 256:
            raise SupervisionValidationError("invalid run id")

        def build(c, origins):
            if run_id not in origins:
                raise SupervisionNotFound("run not found")
            tables = _table_set(c)
            # Detail is not window restricted. Read the selected Run's calls
            # directly so an old authorized Run remains drillable outside the overview window.
            c.execute("""SELECT run_id,agent_key,agent_version,status,current_step_key,
                        created_at,started_at,updated_at,completed_at,output_json
                        FROM agent_run WHERE run_id=%s""", (run_id,))
            item = c.fetchone()
            if not item:
                raise SupervisionNotFound("run not found")
            row = dict(item); row["_origin"] = origins[run_id]
            now = _now()
            assessments = self._assessments(c, tables, [run_id], [row])
            assessment = assessments.get(run_id)
            models = self._fetch_calls(c, "agent_model_call", [run_id], ("call_id", "run_id", "step_run_id", "provider_key", "requested_model", "actual_model", "status", "stage", "retry_count", "created_at", "error_code")) if "agent_model_call" in tables else None
            tools = self._fetch_calls(c, "agent_tool_call", [run_id], ("call_id", "run_id", "step_run_id", "tool_key", "tool_version", "status", "retry_count", "created_at")) if "agent_tool_call" in tables else None
            step_rows = self._fetch_calls(c, "agent_step_run", [run_id], ("step_run_id", "run_id", "step_key", "status", "attempt", "started_at", "finished_at", "created_at")) if "agent_step_run" in tables else []
            steps = []
            for step in step_rows:
                sm = [call for call in (models or []) if call.get("step_run_id") == step.get("step_run_id")]
                st = [call for call in (tools or []) if call.get("step_run_id") == step.get("step_run_id")]
                steps.append({"stepKey": step.get("step_key"), "status": step.get("status"),
                              "attempts": step.get("attempt", 0) + 1, "elapsedSeconds": _elapsed({"started_at": step.get("started_at"), "completed_at": step.get("finished_at"), "status": step.get("status")}, now),
                              "models": _call_summary(sm if models is not None else None), "tools": _call_summary(st if tools is not None else None)})
            output = _json(row.get("output_json"))
            report = _has_report(output); graph = _has_graph(output)
            names = self._display_names(c, tables, {str(row.get("agent_key"))})
            links = {"report": {"runId": run_id, "available": True} if report else None,
                     "graph": {"runId": run_id, "available": True} if graph else None,
                     "workspace": {"runId": run_id, "available": True} if report else None}
            return {"schemaVersion": SCHEMA_VERSION, "queriedAt": now.isoformat(),
                    "scope": {"kind": "authorized-run", "visibility": "owner-and-verified-lineage"},
                    "run": self._run_item(row, assessment, now, models, tools, names), "steps": steps,
                    "calls": {"models": _call_summary(models), "tools": _call_summary(tools),
                              "modelItems": [self._safe_model_call(r) for r in (models or [])],
                              "toolItems": [self._safe_tool_call(r) for r in (tools or [])]},
                    "quality": _status_quality(row, _gap_count(output), assessment), "links": links}
        return self._read(actor, build)

    @staticmethod
    def _safe_model_call(row: dict[str, Any]) -> dict[str, Any]:
        return {"callId": row.get("call_id"), "runId": row.get("run_id"), "stepRunId": row.get("step_run_id"),
                "modelId": row.get("actual_model") or row.get("requested_model"), "requestedModel": row.get("requested_model"),
                "actualModel": row.get("actual_model"), "providerKey": row.get("provider_key"), "status": row.get("status") or "unknown",
                "stage": row.get("stage"), "errorCode": row.get("error_code"), "createdAt": _iso(row.get("created_at")),
                "retryCount": row.get("retry_count")}

    @staticmethod
    def _safe_tool_call(row: dict[str, Any]) -> dict[str, Any]:
        return {"callId": row.get("call_id"), "runId": row.get("run_id"), "stepRunId": row.get("step_run_id"),
                "toolKey": row.get("tool_key"), "toolVersion": row.get("tool_version"), "status": row.get("status") or "unknown",
                "createdAt": _iso(row.get("created_at")), "retryCount": row.get("retry_count")}

    @staticmethod
    def _validate_window(value: int) -> int:
        if type(value) is not int or value not in ALLOWED_WINDOWS:
            raise SupervisionValidationError("windowHours must be one of 24, 168 or 720")
        return value

    @staticmethod
    def _validate_agent_key(value: str | None):
        if value is not None and (not isinstance(value, str) or not value or len(value) > 200):
            raise SupervisionValidationError("invalid agentKey")

