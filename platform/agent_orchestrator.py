"""Durable multi-agent orchestration for ontology analysis.

This module deliberately owns execution state, not ontology truth.  Agents
may produce candidate artifacts and hand them to downstream agents, but no
method in this module publishes a fact.  Publication remains behind the
existing audit/review APIs.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import threading
from datetime import datetime, timezone, timedelta
from typing import Any, Iterable

try:
    from intelligence_store import IntelligenceConflict, IntelligenceNotFound, IntelligenceValidationError
    from conversation_store import ConversationStore
except ModuleNotFoundError:  # pragma: no cover
    from .intelligence_store import IntelligenceConflict, IntelligenceNotFound, IntelligenceValidationError
    from .conversation_store import ConversationStore


RUN_STATUSES = {"queued", "planning", "running", "paused", "completed", "completed-with-gaps", "failed", "cancelled"}
TASK_STATUSES = {"queued", "ready", "running", "succeeded", "failed", "blocked", "cancelled"}
ARTIFACT_STATUSES = {"candidate", "under-review", "rejected", "superseded"}
HANDOFF_STATUSES = {"pending", "accepted", "rejected", "cancelled"}

# The order defines the default DAG. Extractor and signal analysis are
# intentionally independent so the orchestrator can dispatch them together.
AGENT_ROLES: tuple[dict[str, Any], ...] = (
    {"role": "ontology-planner", "displayName": "Ontology Planner", "description": "读取实体档案和缺口，生成研究计划", "inputContract": "entity-profile.v1", "outputContract": "research-plan.v1", "dependencies": ()},
    {"role": "intelligence-collector", "displayName": "Intelligence Collector", "description": "按研究计划采集公开来源和影像证据", "inputContract": "research-plan.v1", "outputContract": "source-bundle.v1", "dependencies": ("ontology-planner",)},
    {"role": "attribute-relation-extractor", "displayName": "Attribute & Relation Extractor", "description": "从来源正文提取属性、关系、条件和引文", "inputContract": "source-bundle.v1", "outputContract": "ontology-candidates.v1", "dependencies": ("intelligence-collector",)},
    {"role": "signal-imagery-analyst", "displayName": "Signal / Imagery Analyst", "description": "关联公开信号、遥测、影像与装备配置", "inputContract": "source-bundle.v1", "outputContract": "observation-bundle.v1", "dependencies": ("intelligence-collector",)},
    {"role": "evidence-fusion", "displayName": "Evidence Fusion Analyst", "description": "去重、判断来源独立性并标记冲突", "inputContract": "ontology-candidates.v1+observation-bundle.v1", "outputContract": "evidence-bundle.v1", "dependencies": ("attribute-relation-extractor", "signal-imagery-analyst")},
    {"role": "capability-quantification", "displayName": "Capability Quantification Analyst", "description": "把技术属性转换为带条件的作战能力指标", "inputContract": "evidence-bundle.v1", "outputContract": "capability-assessment.v1", "dependencies": ("evidence-fusion",)},
    {"role": "red-team-governance-reviewer", "displayName": "Red Team & Governance Reviewer", "description": "寻找反证、过度推断和发布风险", "inputContract": "capability-assessment.v1", "outputContract": "review-package.v1", "dependencies": ("capability-quantification",)},
)
ROLE_MAP = {item["role"]: item for item in AGENT_ROLES}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _dump(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _decode(value: Any, default: Any = None) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _hash(value: Any) -> str:
    return hashlib.sha256(_dump(value).encode("utf-8")).hexdigest()


class AgentContractError(IntelligenceValidationError):
    """Raised when an agent input/output violates a declared contract."""


class MissionOrchestrator:
    """Persistent DAG scheduler used by the analysis workbench.

    The scheduler does not call an external model itself.  A provider worker
    claims ready tasks, writes candidate artifacts with :meth:`add_artifact`,
    and marks the task complete.  This keeps provider failures and retries
    visible and permits Firecrawl/DeepSeek workers to evolve independently.
    """

    DEFAULT_BUDGET = {
        "maxTasks": 7,
        "maxConcurrentTasks": 2,
        "maxAttempts": 2,
        "maxElapsedSeconds": 1800,
        "maxModelCalls": 50,
        "maxSourceCalls": 50,
        "maxArtifacts": 500,
    }

    def __init__(self, agent_or_store):
        self.agent = agent_or_store if hasattr(agent_or_store, "store") else None
        self.store = getattr(agent_or_store, "store", agent_or_store)
        self._lock = getattr(self.store, "_lock", threading.RLock())

    @staticmethod
    def roles() -> list[dict[str, Any]]:
        return [{**item, "dependencies": list(item["dependencies"])} for item in AGENT_ROLES]

    def _event_tx(self, con, run_id: str, event_type: str, detail: dict[str, Any] | None = None, task_id: str | None = None):
        # Keep stage/status in the immutable event payload so the existing
        # schema remains compatible while session/workbench consumers can
        # render a normalized event stream.  Provider workers may override
        # these values explicitly when they emit a richer event.
        payload = dict(detail or {})
        stage = payload.setdefault("stage", self._event_stage(event_type))
        payload.setdefault("status", self._event_status(event_type))
        con.execute(
            "INSERT INTO ontology_analysis_event(analysis_run_id,event_type,agent_task_id,detail_json,created_at) VALUES(?,?,?,?,?)",
            (run_id, event_type, task_id, _dump(payload), _now()),
        )

    @staticmethod
    def _event_stage(event_type: str) -> str:
        """Map durable event names to the workbench's stable stages."""
        name = str(event_type or "").casefold()
        if name.startswith("run-") or name.startswith("task-"):
            return "orchestration"
        if "artifact" in name or "handoff" in name:
            return "evidence-chain"
        if "budget" in name:
            return "orchestration"
        return "orchestration"

    @staticmethod
    def _event_status(event_type: str) -> str:
        name = str(event_type or "").casefold()
        if any(token in name for token in ("failed", "blocked", "budget-exceeded")):
            return "failed"
        if any(token in name for token in ("started", "created", "ready", "requested", "recovered")):
            return "running"
        if any(token in name for token in ("completed", "succeeded", "accepted", "updated")):
            return "completed"
        if "cancel" in name:
            return "cancelled"
        if "retry" in name:
            return "retrying"
        return "recorded"

    def _entity_id(self, value: str | None) -> str | None:
        if not value:
            return None
        if self.agent is None or not hasattr(self.agent, "_entity_document"):
            return str(value)
        entity, _ = self.agent._entity_document(str(value))
        return entity["entityId"]

    def _row_run(self, row, include_children: bool = True) -> dict[str, Any]:
        if row is None:
            raise IntelligenceNotFound("analysis run not found")
        item = dict(row)
        result = {
            "analysisRunId": item["analysis_run_id"], "entityId": item["entity_id"],
            "status": item["status"], "request": _decode(item["request_json"], {}),
            "budget": _decode(item["budget_json"], {}), "result": _decode(item["result_json"], {}) or {},
            "conversationSessionId": item.get("conversation_session_id"),
            "prompt": item.get("prompt_text"), "intent": _decode(item.get("intent_json"), {}),
            "retryOf": item["retry_of"], "stopReason": item["stop_reason"], "error": item["error_message"],
            "createdAt": item["created_at"], "startedAt": item["started_at"],
            "updatedAt": item["updated_at"], "completedAt": item["completed_at"],
        }
        if not include_children:
            return result
        with self.store._connect() as con:
            result["tasks"] = [self._task_row(r) for r in con.execute("SELECT * FROM ontology_agent_task WHERE analysis_run_id=? ORDER BY created_at", (item["analysis_run_id"],))]
            active = next((task for task in result["tasks"] if task["status"] in {"running", "ready"}), None)
            result["currentStage"] = active["role"] if active else ("completed" if result["status"] in {"completed", "completed-with-gaps"} else result["status"])
            result["completedStages"] = [task["role"] for task in result["tasks"] if task["status"] == "succeeded"]
            result["artifacts"] = [self._artifact_row(r) for r in con.execute("SELECT * FROM ontology_agent_artifact WHERE analysis_run_id=? ORDER BY created_at", (item["analysis_run_id"],))]
            result["handoffs"] = [self._handoff_row(r) for r in con.execute("SELECT * FROM ontology_agent_handoff WHERE analysis_run_id=? ORDER BY created_at", (item["analysis_run_id"],))]
            result["events"] = [{"eventId": r["event_id"], "eventType": r["event_type"], "agentTaskId": r["agent_task_id"], "detail": _decode(r["detail_json"], {}), "createdAt": r["created_at"]} for r in con.execute("SELECT * FROM ontology_analysis_event WHERE analysis_run_id=? ORDER BY event_id", (item["analysis_run_id"],))]
        return result

    @staticmethod
    def _task_row(row) -> dict[str, Any]:
        item = dict(row)
        return {"agentTaskId": item["agent_task_id"], "analysisRunId": item["analysis_run_id"], "role": item["role"], "status": item["status"], "dependencies": _decode(item["dependency_ids"], []), "input": _decode(item["input_json"], {}), "output": _decode(item["output_json"], {}) if item["output_json"] else {}, "budget": _decode(item["budget_json"], {}), "attempt": item["attempt"], "maxAttempts": item["max_attempts"], "workerId": item["worker_id"], "leaseExpiresAt": item.get("lease_expires_at"), "error": item["error_message"], "createdAt": item["created_at"], "startedAt": item["started_at"], "updatedAt": item["updated_at"], "completedAt": item["completed_at"]}

    @staticmethod
    def _artifact_row(row) -> dict[str, Any]:
        item = dict(row)
        return {"artifactId": item["artifact_id"], "analysisRunId": item["analysis_run_id"], "agentTaskId": item["agent_task_id"], "artifactType": item["artifact_type"], "status": item["status"], "content": _decode(item["content_json"], {}), "contentHash": item["content_hash"], "createdAt": item["created_at"]}

    @staticmethod
    def _handoff_row(row) -> dict[str, Any]:
        item = dict(row)
        return {"handoffId": item["handoff_id"], "analysisRunId": item["analysis_run_id"], "fromTaskId": item["from_task_id"], "toTaskId": item["to_task_id"], "artifactId": item["artifact_id"], "status": item["status"], "payload": _decode(item["payload_json"], {}), "createdAt": item["created_at"], "updatedAt": item["updated_at"]}

    def create(self, request: dict[str, Any] | None = None, *, entity_id: str | None = None, background: bool = False) -> dict[str, Any]:
        request = dict(request or {})
        entity = self._entity_id(entity_id or request.get("entityId"))
        budget = dict(self.DEFAULT_BUDGET)
        budget.update(request.get("budget") or {})
        for key in ("maxTasks", "maxConcurrentTasks", "maxAttempts", "maxElapsedSeconds", "maxModelCalls", "maxSourceCalls", "maxArtifacts"):
            try:
                budget[key] = max(1, int(budget[key]))
            except (TypeError, ValueError):
                raise AgentContractError(f"budget.{key} must be a positive integer")
        if budget["maxTasks"] < len(AGENT_ROLES):
            raise AgentContractError(f"budget.maxTasks must be at least {len(AGENT_ROLES)}")
        run_id, stamp = "analysis-" + secrets.token_hex(10), _now()
        session_id = str(request.get("conversationSessionId") or request.get("sessionId") or "").strip() or None
        prompt = request.get("prompt", request.get("question"))
        prompt = str(prompt).strip() if prompt is not None else None
        intent = request.get("intent") if isinstance(request.get("intent"), dict) else {}
        if session_id:
            session = ConversationStore(self.agent or self.store).get_session(session_id)
            if session.get("status") != "active":
                raise IntelligenceConflict("conversation session is closed or archived")
            if session.get("entityId") and entity and session["entityId"] != entity:
                raise IntelligenceConflict("conversation session does not belong to entity")
            if not entity and session.get("entityId"):
                entity = session["entityId"]
        task_ids = {item["role"]: "agent-task-" + secrets.token_hex(9) for item in AGENT_ROLES}
        with self._lock, self.store._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            con.execute("INSERT INTO ontology_analysis_run(analysis_run_id,entity_id,status,request_json,budget_json,retry_of,conversation_session_id,prompt_text,intent_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)", (run_id, entity, "queued", _dump(request), _dump(budget), request.get("retryOf"), session_id, prompt, _dump(intent), stamp, stamp))
            for item in AGENT_ROLES:
                dependencies = [task_ids[role] for role in item["dependencies"]]
                status = "ready" if not dependencies else "queued"
                task_request = {"entityId": entity, "analysisRunId": run_id, "role": item["role"], "inputContract": item["inputContract"]}
                con.execute("INSERT INTO ontology_agent_task(agent_task_id,analysis_run_id,role,status,dependency_ids,input_json,budget_json,max_attempts,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)", (task_ids[item["role"]], run_id, item["role"], status, _dump(dependencies), _dump(task_request), _dump(budget), budget["maxAttempts"], stamp, stamp))
            self._event_tx(con, run_id, "run-created", {"entityId": entity, "taskCount": len(AGENT_ROLES), "budget": budget})
            self._event_tx(con, run_id, "tasks-planned", {"roles": [item["role"] for item in AGENT_ROLES]})
        return self.get(run_id)

    # Alias used by HTTP handlers and callers that prefer run terminology.
    create_run = create
    create_analysis_run = create

    def get(self, run_id: str, *, include_children: bool = True) -> dict[str, Any]:
        with self.store._connect() as con:
            row = con.execute("SELECT * FROM ontology_analysis_run WHERE analysis_run_id=?", (str(run_id),)).fetchone()
        return self._row_run(row, include_children=include_children)

    get_run = get

    def list(self, *, entity_id: str | None = None, status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 200))
        clauses, values = [], []
        if entity_id:
            clauses.append("entity_id=?"); values.append(self._entity_id(entity_id))
        if status:
            if status not in RUN_STATUSES: raise AgentContractError("unsupported analysis run status")
            clauses.append("status=?"); values.append(status)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self.store._connect() as con:
            rows = con.execute("SELECT * FROM ontology_analysis_run" + where + " ORDER BY created_at DESC LIMIT ?", (*values, limit)).fetchall()
        return [self._row_run(row, include_children=False) for row in rows]

    list_runs = list

    def _refresh_ready_tx(self, con, run_id: str) -> list[str]:
        rows = con.execute("SELECT * FROM ontology_agent_task WHERE analysis_run_id=? AND status='queued'", (run_id,)).fetchall()
        promoted = []
        for row in rows:
            deps = _decode(row["dependency_ids"], [])
            if deps and any(con.execute("SELECT status FROM ontology_agent_task WHERE agent_task_id=?", (dep,)).fetchone() is None for dep in deps):
                continue
            states = [con.execute("SELECT status FROM ontology_agent_task WHERE agent_task_id=?", (dep,)).fetchone()[0] for dep in deps]
            if any(state in {"failed", "blocked", "cancelled"} for state in states):
                con.execute("UPDATE ontology_agent_task SET status='blocked',error_message=?,updated_at=? WHERE agent_task_id=?", ("dependency failed or cancelled", _now(), row["agent_task_id"]))
                self._event_tx(con, run_id, "task-blocked", {"reason": "dependency failed or cancelled"}, row["agent_task_id"])
            elif all(state == "succeeded" for state in states):
                con.execute("UPDATE ontology_agent_task SET status='ready',updated_at=? WHERE agent_task_id=?", (_now(), row["agent_task_id"]))
                self._event_tx(con, run_id, "task-ready", {"dependencies": deps}, row["agent_task_id"])
                promoted.append(row["agent_task_id"])
        return promoted

    def start(self, run_id: str) -> dict[str, Any]:
        with self._lock, self.store._connect() as con:
            row = con.execute("SELECT status FROM ontology_analysis_run WHERE analysis_run_id=?", (run_id,)).fetchone()
            if row is None: raise IntelligenceNotFound("analysis run not found")
            if row[0] in {"cancelled", "completed", "completed-with-gaps"}: raise IntelligenceConflict("analysis run is terminal")
            stamp = _now()
            con.execute("UPDATE ontology_analysis_run SET status='running',started_at=COALESCE(started_at,?),updated_at=? WHERE analysis_run_id=?", (stamp, stamp, run_id))
            self._event_tx(con, run_id, "run-started", {})
            self._refresh_ready_tx(con, run_id)
        return self.get(run_id)

    resume = start
    start_run = start
    resume_run = start

    def pause(self, run_id: str, reason: str = "operator paused") -> dict[str, Any]:
        with self._lock, self.store._connect() as con:
            row = con.execute("SELECT status FROM ontology_analysis_run WHERE analysis_run_id=?", (run_id,)).fetchone()
            if row is None: raise IntelligenceNotFound("analysis run not found")
            if row[0] not in {"queued", "planning", "running"}: raise IntelligenceConflict("analysis run cannot be paused")
            con.execute("UPDATE ontology_analysis_run SET status='paused',stop_reason=?,updated_at=? WHERE analysis_run_id=?", (reason, _now(), run_id))
            self._event_tx(con, run_id, "run-paused", {"reason": reason})
        return self.get(run_id)

    pause_run = pause

    def cancel(self, run_id: str, reason: str = "operator cancelled") -> dict[str, Any]:
        with self._lock, self.store._connect() as con:
            row = con.execute("SELECT status FROM ontology_analysis_run WHERE analysis_run_id=?", (run_id,)).fetchone()
            if row is None: raise IntelligenceNotFound("analysis run not found")
            if row[0] in {"completed", "completed-with-gaps", "failed", "cancelled"}: raise IntelligenceConflict("analysis run is terminal")
            stamp = _now()
            con.execute("UPDATE ontology_analysis_run SET status='cancelled',stop_reason=?,updated_at=?,completed_at=? WHERE analysis_run_id=?", (reason, stamp, stamp, run_id))
            con.execute("UPDATE ontology_agent_task SET status='cancelled',worker_id=NULL,lease_expires_at=NULL,updated_at=?,completed_at=? WHERE analysis_run_id=? AND status IN ('queued','ready','running')", (stamp, stamp, run_id))
            self._event_tx(con, run_id, "run-cancelled", {"reason": reason})
        return self.get(run_id)

    cancel_run = cancel

    def claim_ready_tasks(self, run_id: str, *, limit: int | None = None, worker_id: str = "orchestrator") -> list[dict[str, Any]]:
        with self._lock, self.store._connect() as con:
            run = con.execute("SELECT status,budget_json,started_at FROM ontology_analysis_run WHERE analysis_run_id=?", (run_id,)).fetchone()
            if run is None: raise IntelligenceNotFound("analysis run not found")
            if run[0] != "running": raise IntelligenceConflict("analysis run is not running")
            budget = _decode(run[1], self.DEFAULT_BUDGET)
            if run[2]:
                try:
                    elapsed = (datetime.now(timezone.utc) - datetime.fromisoformat(str(run[2]).replace("Z", "+00:00"))).total_seconds()
                    if elapsed > int(budget.get("maxElapsedSeconds", self.DEFAULT_BUDGET["maxElapsedSeconds"])):
                        stamp = _now(); con.execute("UPDATE ontology_analysis_run SET status='failed',stop_reason=?,error_message=?,updated_at=?,completed_at=? WHERE analysis_run_id=?", ("budget-exceeded", "analysis run elapsed-time budget exceeded", stamp, stamp, run_id)); self._event_tx(con, run_id, "run-budget-exceeded", {"budget": budget, "elapsedSeconds": elapsed})
                        raise IntelligenceConflict("analysis run elapsed-time budget exceeded")
                except ValueError:
                    pass
            # Reclaim expired worker leases before enforcing concurrency.  A
            # provider timeout or process crash must leave the task retryable
            # without requiring a full service restart.
            lease_now = _now()
            expired = con.execute(
                "SELECT agent_task_id FROM ontology_agent_task WHERE analysis_run_id=? AND status='running' AND lease_expires_at IS NOT NULL AND lease_expires_at<?",
                (run_id, lease_now),
            ).fetchall()
            for expired_row in expired:
                con.execute(
                    "UPDATE ontology_agent_task SET status='ready',worker_id=NULL,lease_expires_at=NULL,error_message=?,updated_at=?,completed_at=NULL WHERE agent_task_id=?",
                    ("worker lease expired; task requeued", lease_now, expired_row[0]),
                )
                self._event_tx(con, run_id, "task-reclaimed", {"reason": "lease-expired", "retryable": True}, expired_row[0])
            active = con.execute("SELECT COUNT(*) FROM ontology_agent_task WHERE analysis_run_id=? AND status='running'", (run_id,)).fetchone()[0]
            allowed = max(0, int(budget.get("maxConcurrentTasks", 2)) - active)
            if limit is not None: allowed = min(allowed, max(0, int(limit)))
            if allowed <= 0: return []
            self._refresh_ready_tx(con, run_id)
            rows = con.execute("SELECT * FROM ontology_agent_task WHERE analysis_run_id=? AND status='ready' ORDER BY created_at LIMIT ?", (run_id, allowed)).fetchall()
            stamp = _now(); claimed = []
            lease_seconds = max(30, min(300, int(budget.get("maxElapsedSeconds", self.DEFAULT_BUDGET["maxElapsedSeconds"]))))
            lease_expires = (datetime.now(timezone.utc) + timedelta(seconds=lease_seconds)).isoformat()
            for row in rows:
                con.execute("UPDATE ontology_agent_task SET status='running',attempt=attempt+1,worker_id=?,lease_expires_at=?,started_at=COALESCE(started_at,?),updated_at=? WHERE agent_task_id=?", (worker_id, lease_expires, stamp, stamp, row["agent_task_id"]))
                self._event_tx(con, run_id, "task-started", {"workerId": worker_id, "attempt": row["attempt"] + 1, "leaseExpiresAt": lease_expires}, row["agent_task_id"])
                claimed.append(row["agent_task_id"])
        return [self.task(task_id) for task_id in claimed]

    def task(self, task_id: str) -> dict[str, Any]:
        with self.store._connect() as con:
            row = con.execute("SELECT * FROM ontology_agent_task WHERE agent_task_id=?", (task_id,)).fetchone()
        if row is None: raise IntelligenceNotFound("agent task not found")
        return self._task_row(row)

    get_task = task

    def _validate_task_output(self, task: dict[str, Any], output: dict[str, Any] | None) -> dict[str, Any]:
        if output is None: return {}
        if not isinstance(output, dict): raise AgentContractError("agent task output must be an object")
        contract = ROLE_MAP.get(task["role"], {}).get("outputContract")
        value = dict(output)
        supplied = value.get("schemaVersion")
        if supplied is not None and supplied != contract:
            raise AgentContractError(f"agent task output schema must be {contract}")
        if value.get("modelCanProposeOnly") is False:
            raise AgentContractError("agent output cannot disable modelCanProposeOnly")
        value.setdefault("schemaVersion", contract); value["modelCanProposeOnly"] = True
        return value

    def complete_task(self, task_id: str, output: dict[str, Any] | None = None, *, worker_id: str | None = None) -> dict[str, Any]:
        with self._lock, self.store._connect() as con:
            row = con.execute("SELECT * FROM ontology_agent_task WHERE agent_task_id=?", (task_id,)).fetchone()
            if row is None: raise IntelligenceNotFound("agent task not found")
            if row["status"] != "running": raise IntelligenceConflict("agent task is not running")
            if worker_id and row["worker_id"] != worker_id: raise IntelligenceConflict("agent task is owned by another worker")
            value = self._validate_task_output(dict(row), output)
            stamp = _now(); con.execute("UPDATE ontology_agent_task SET status='succeeded',output_json=?,worker_id=NULL,lease_expires_at=NULL,updated_at=?,completed_at=? WHERE agent_task_id=?", (_dump(value), stamp, stamp, task_id))
            self._event_tx(con, row["analysis_run_id"], "task-succeeded", {"outputKeys": sorted(value.keys())}, task_id)
            self._refresh_ready_tx(con, row["analysis_run_id"])
            self._maybe_finish_tx(con, row["analysis_run_id"])
        return self.task(task_id)

    complete = complete_task

    def fail_task(self, task_id: str, error: str, *, retry: bool = True, worker_id: str | None = None) -> dict[str, Any]:
        with self._lock, self.store._connect() as con:
            row = con.execute("SELECT * FROM ontology_agent_task WHERE agent_task_id=?", (task_id,)).fetchone()
            if row is None: raise IntelligenceNotFound("agent task not found")
            if row["status"] != "running": raise IntelligenceConflict("agent task is not running")
            if worker_id and row["worker_id"] != worker_id: raise IntelligenceConflict("agent task is owned by another worker")
            next_status = "ready" if retry and row["attempt"] < row["max_attempts"] else "failed"
            stamp = _now(); con.execute("UPDATE ontology_agent_task SET status=?,error_message=?,worker_id=NULL,lease_expires_at=NULL,updated_at=?,completed_at=? WHERE agent_task_id=?", (next_status, str(error)[:1000], stamp, None if next_status == "ready" else stamp, task_id))
            self._event_tx(con, row["analysis_run_id"], "task-retry" if next_status == "ready" else "task-failed", {"error": str(error)[:500], "retryable": next_status == "ready", "attempt": row["attempt"], "maxAttempts": row["max_attempts"]}, task_id)
            if next_status == "failed": self._maybe_finish_tx(con, row["analysis_run_id"])
        return self.task(task_id)

    fail = fail_task

    def _maybe_finish_tx(self, con, run_id: str) -> None:
        run = con.execute("SELECT status FROM ontology_analysis_run WHERE analysis_run_id=?", (run_id,)).fetchone()
        if not run or run[0] in {"cancelled", "completed", "completed-with-gaps", "failed"}: return
        rows = con.execute("SELECT status FROM ontology_agent_task WHERE analysis_run_id=?", (run_id,)).fetchall()
        statuses = [row[0] for row in rows]
        stamp = _now()
        if any(status == "failed" for status in statuses):
            con.execute("UPDATE ontology_analysis_run SET status='failed',error_message=?,updated_at=?,completed_at=? WHERE analysis_run_id=?", ("one or more agent tasks failed", stamp, stamp, run_id)); self._event_tx(con, run_id, "run-failed", {"reason": "task-failed"})
        elif rows and all(status in {"succeeded", "blocked", "cancelled"} for status in statuses):
            blocked = sum(status != "succeeded" for status in statuses)
            final = "completed-with-gaps" if blocked else "completed"
            con.execute("UPDATE ontology_analysis_run SET status=?,result_json=?,updated_at=?,completed_at=? WHERE analysis_run_id=?", (final, _dump({"taskCount": len(rows), "succeeded": statuses.count("succeeded"), "blocked": statuses.count("blocked"), "artifacts": con.execute("SELECT COUNT(*) FROM ontology_agent_artifact WHERE analysis_run_id=?", (run_id,)).fetchone()[0], "publication": "requires-explicit-human-review"}), stamp, stamp, run_id)); self._event_tx(con, run_id, "run-completed", {"status": final, "publication": "requires-explicit-human-review"})

    def add_artifact(self, task_id: str, artifact_type: str, content: dict[str, Any], *, status: str = "under-review") -> dict[str, Any]:
        if status not in ARTIFACT_STATUSES or status not in {"candidate", "under-review"}:
            raise AgentContractError("agent artifacts can only enter candidate or under-review state")
        if not artifact_type or not isinstance(content, dict): raise AgentContractError("artifactType and object content are required")
        with self._lock, self.store._connect() as con:
            task = con.execute("SELECT * FROM ontology_agent_task WHERE agent_task_id=?", (task_id,)).fetchone()
            if task is None: raise IntelligenceNotFound("agent task not found")
            budget = _decode(con.execute("SELECT budget_json FROM ontology_analysis_run WHERE analysis_run_id=?", (task["analysis_run_id"],)).fetchone()[0], self.DEFAULT_BUDGET)
            current_artifacts = con.execute("SELECT COUNT(*) FROM ontology_agent_artifact WHERE analysis_run_id=?", (task["analysis_run_id"],)).fetchone()[0]
            if current_artifacts >= int(budget.get("maxArtifacts", self.DEFAULT_BUDGET["maxArtifacts"])):
                raise IntelligenceConflict("analysis run artifact budget exceeded")
            value = dict(content)
            contract = ROLE_MAP.get(task["role"], {}).get("outputContract")
            supplied = value.get("schemaVersion")
            if supplied is not None and supplied != contract:
                raise AgentContractError(f"artifact schema must be {contract}")
            if value.get("modelCanProposeOnly") is False:
                raise AgentContractError("agent artifact cannot disable modelCanProposeOnly")
            value.setdefault("modelCanProposeOnly", True); value.setdefault("schemaVersion", contract)
            aid, stamp, digest = "artifact-" + secrets.token_hex(10), _now(), _hash(value)
            con.execute("INSERT OR IGNORE INTO ontology_agent_artifact(artifact_id,analysis_run_id,agent_task_id,artifact_type,status,content_json,content_hash,created_at) VALUES(?,?,?,?,?,?,?,?)", (aid, task["analysis_run_id"], task_id, str(artifact_type), status, _dump(value), digest, stamp))
            row = con.execute("SELECT * FROM ontology_agent_artifact WHERE agent_task_id=? AND content_hash=?", (task_id, digest)).fetchone()
            self._event_tx(con, task["analysis_run_id"], "artifact-created", {"artifactId": row["artifact_id"], "artifactType": artifact_type, "status": status}, task_id)
        return self._artifact_row(row)

    register_artifact = add_artifact
    create_artifact = add_artifact

    def handoff(self, from_task_id: str, to_task_id: str, *, artifact_id: str | None = None, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        with self._lock, self.store._connect() as con:
            source = con.execute("SELECT * FROM ontology_agent_task WHERE agent_task_id=?", (from_task_id,)).fetchone()
            target = con.execute("SELECT * FROM ontology_agent_task WHERE agent_task_id=?", (to_task_id,)).fetchone()
            if source is None or target is None: raise IntelligenceNotFound("handoff task not found")
            if source["analysis_run_id"] != target["analysis_run_id"]: raise IntelligenceConflict("handoff tasks must belong to one run")
            if artifact_id:
                artifact = con.execute("SELECT artifact_id FROM ontology_agent_artifact WHERE artifact_id=? AND agent_task_id=?", (artifact_id, from_task_id)).fetchone()
                if artifact is None: raise IntelligenceConflict("artifact does not belong to source task")
            hid, stamp = "handoff-" + secrets.token_hex(9), _now()
            con.execute("INSERT INTO ontology_agent_handoff(handoff_id,analysis_run_id,from_task_id,to_task_id,artifact_id,status,payload_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)", (hid, source["analysis_run_id"], from_task_id, to_task_id, artifact_id, "pending", _dump(payload or {}), stamp, stamp))
            self._event_tx(con, source["analysis_run_id"], "agent-handoff-created", {"handoffId": hid, "fromTaskId": from_task_id, "toTaskId": to_task_id, "artifactId": artifact_id}, from_task_id)
            row = con.execute("SELECT * FROM ontology_agent_handoff WHERE handoff_id=?", (hid,)).fetchone()
        return self._handoff_row(row)

    create_handoff = handoff

    def update_handoff(self, handoff_id: str, status: str) -> dict[str, Any]:
        if status not in HANDOFF_STATUSES: raise AgentContractError("unsupported handoff status")
        with self._lock, self.store._connect() as con:
            row = con.execute("SELECT * FROM ontology_agent_handoff WHERE handoff_id=?", (handoff_id,)).fetchone()
            if row is None: raise IntelligenceNotFound("handoff not found")
            stamp = _now(); con.execute("UPDATE ontology_agent_handoff SET status=?,updated_at=? WHERE handoff_id=?", (status, stamp, handoff_id)); self._event_tx(con, row["analysis_run_id"], "agent-handoff-updated", {"handoffId": handoff_id, "status": status})
            row = con.execute("SELECT * FROM ontology_agent_handoff WHERE handoff_id=?", (handoff_id,)).fetchone()
        return self._handoff_row(row)

    def retry(self, run_id: str, *, task_id: str | None = None, request: dict[str, Any] | None = None) -> dict[str, Any]:
        run = self.get(run_id, include_children=False)
        if task_id:
            row = self.task(task_id)
            if row["analysisRunId"] != run_id: raise IntelligenceConflict("task does not belong to run")
            if row["status"] not in {"failed", "blocked"}: raise IntelligenceConflict("only failed or blocked tasks can retry")
            if int(row["attempt"]) >= int(row["maxAttempts"]): raise IntelligenceConflict("agent task retry budget exhausted")
            with self._lock, self.store._connect() as con:
                stamp = _now()
                con.execute("UPDATE ontology_agent_task SET status='ready',error_message=NULL,worker_id=NULL,lease_expires_at=NULL,completed_at=NULL,updated_at=? WHERE agent_task_id=?", (stamp, task_id))
                con.execute("UPDATE ontology_analysis_run SET status='running',error_message=NULL,stop_reason=NULL,completed_at=NULL,updated_at=? WHERE analysis_run_id=? AND status IN ('failed','paused')", (stamp, run_id))
                self._event_tx(con, run_id, "task-retry-requested", {}, task_id)
            return self.get(run_id)
        if run["status"] not in {"failed", "cancelled", "completed-with-gaps"}: raise IntelligenceConflict("only terminal runs with gaps or failure can retry")
        payload = {**run["request"], **(request or {}), "retryOf": run_id}
        return self.create(payload, entity_id=run["entityId"])

    retry_run = retry

    def recover(self) -> int:
        """Mark in-flight runs paused after a service restart; workers can resume."""
        with self._lock, self.store._connect() as con:
            rows = con.execute("SELECT analysis_run_id FROM ontology_analysis_run WHERE status IN ('planning','running')").fetchall()
            for row in rows:
                con.execute("UPDATE ontology_analysis_run SET status='paused',stop_reason=?,updated_at=? WHERE analysis_run_id=?", ("service-restarted", _now(), row[0])); self._event_tx(con, row[0], "run-recovered", {"retryable": True})
                # Workers are process-local.  Requeue their leases so a new
                # worker can claim the tasks after the operator resumes.
                con.execute("UPDATE ontology_agent_task SET status='ready',worker_id=NULL,lease_expires_at=NULL,error_message=?,updated_at=? WHERE analysis_run_id=? AND status='running'", ("service-restarted; task requeued", _now(), row[0]))
            return len(rows)


__all__ = ["MissionOrchestrator", "AgentContractError", "AGENT_ROLES", "RUN_STATUSES", "TASK_STATUSES"]
