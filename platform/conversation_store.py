"""Persistent conversation sessions/messages for ontology analysis.

Conversation is an interaction boundary around an analysis run.  It is not a
publication mechanism: messages and bindings are retained for traceability,
while facts still require the existing candidate review and release gates.
"""
from __future__ import annotations

import hashlib
import json
import secrets
from datetime import datetime, timezone
from typing import Any

try:
    from intelligence_store import IntelligenceConflict, IntelligenceNotFound, IntelligenceValidationError
except ModuleNotFoundError:  # pragma: no cover
    from .intelligence_store import IntelligenceConflict, IntelligenceNotFound, IntelligenceValidationError


SESSION_STATUSES = {"active", "closed", "archived"}
MESSAGE_ROLES = {"user", "assistant", "system", "tool"}


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


class ConversationStore:
    def __init__(self, agent_or_store):
        self.agent = agent_or_store if hasattr(agent_or_store, "store") else None
        self.store = getattr(agent_or_store, "store", agent_or_store)
        self._lock = self.store._lock

    def _entity_id(self, value: str | None) -> str | None:
        if not value:
            return None
        if self.agent is not None and hasattr(self.agent, "_entity_document"):
            entity, _ = self.agent._entity_document(str(value))
            return entity["entityId"]
        return str(value)

    @staticmethod
    def _session_row(row) -> dict[str, Any]:
        item = dict(row)
        return {
            "conversationSessionId": item["conversation_session_id"],
            "sessionId": item["conversation_session_id"],
            "entityId": item["entity_id"], "title": item["title"], "status": item["status"],
            "actorId": item["actor_id"], "context": _decode(item["context_json"], {}),
            "createdAt": item["created_at"], "updatedAt": item["updated_at"],
            "closedAt": item["closed_at"], "lastMessageAt": item["last_message_at"],
            "displayCursor": item.get("display_cursor", 0),
            "displayClearedAt": item.get("display_cleared_at"),
        }

    @staticmethod
    def _message_row(row) -> dict[str, Any]:
        item = dict(row)
        return {
            "conversationMessageId": item["conversation_message_id"],
            "messageId": item["conversation_message_id"],
            "conversationSessionId": item["conversation_session_id"],
            "analysisRunId": item["analysis_run_id"], "sequence": item["sequence_no"],
            "role": item["role"], "content": item["content_text"],
            "contentJson": _decode(item["content_json"], {}),
            "intent": _decode(item["intent_json"], {}), "contentHash": item["content_hash"],
            "createdAt": item["created_at"],
        }

    def create_session(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = dict(payload or {})
        entity_id = self._entity_id(payload.get("entityId"))
        title = str(payload.get("title") or payload.get("topic") or "本体分析会话").strip()[:240]
        actor_id = str(payload.get("actorId") or payload.get("actor") or "operator").strip()[:200] or "operator"
        context = payload.get("context") if isinstance(payload.get("context"), dict) else {}
        session_id, stamp = "conversation-" + secrets.token_hex(10), _now()
        with self._lock, self.store._connect() as con:
            con.execute("INSERT INTO ontology_conversation_session(conversation_session_id,entity_id,title,status,actor_id,context_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)", (session_id, entity_id, title, "active", actor_id, _dump(context), stamp, stamp))
            row = con.execute("SELECT * FROM ontology_conversation_session WHERE conversation_session_id=?", (session_id,)).fetchone()
        return self._session_row(row)

    create = create_session

    def get_session(self, session_id: str, *, include_messages: bool = False, message_limit: int = 100) -> dict[str, Any]:
        with self.store._connect() as con:
            row = con.execute("SELECT * FROM ontology_conversation_session WHERE conversation_session_id=?", (str(session_id),)).fetchone()
        if row is None:
            raise IntelligenceNotFound("conversation session not found")
        value = self._session_row(row)
        if include_messages:
            value["messages"] = self.list_messages(session_id, limit=message_limit)
        return value

    get = get_session

    def list_sessions(self, *, entity_id: str | None = None, status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 200))
        clauses, values = [], []
        if entity_id:
            clauses.append("entity_id=?"); values.append(self._entity_id(entity_id))
        if status:
            if status not in SESSION_STATUSES: raise IntelligenceValidationError("unsupported conversation session status")
            clauses.append("status=?"); values.append(status)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self.store._connect() as con:
            rows = con.execute("SELECT * FROM ontology_conversation_session" + where + " ORDER BY updated_at DESC LIMIT ?", (*values, limit)).fetchall()
        return [self._session_row(row) for row in rows]

    list = list_sessions

    def list_messages(self, session_id: str, *, limit: int = 200, before_sequence: int | None = None, after_sequence: int | None = None) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 500))
        clauses = ["conversation_session_id=?"]; values: list[Any] = [str(session_id)]
        if before_sequence is not None:
            clauses.append("sequence_no<?"); values.append(int(before_sequence))
        if after_sequence is not None:
            clauses.append("sequence_no>?"); values.append(int(after_sequence))
        with self.store._connect() as con:
            exists = con.execute("SELECT 1 FROM ontology_conversation_session WHERE conversation_session_id=?", (str(session_id),)).fetchone()
            if exists is None: raise IntelligenceNotFound("conversation session not found")
            rows = con.execute("SELECT * FROM ontology_conversation_message WHERE " + " AND ".join(clauses) + " ORDER BY sequence_no DESC LIMIT ?", (*values, limit)).fetchall()
        # Return chronological order even when the SQL query uses a bounded
        # reverse index for recent-message pagination.
        return [self._message_row(row) for row in reversed(rows)]

    def add_message(self, session_id: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = dict(payload or {})
        role = str(payload.get("role") or "user").strip().lower()
        if role not in MESSAGE_ROLES: raise IntelligenceValidationError("message role must be user, assistant, system or tool")
        content = payload.get("content", payload.get("text", payload.get("message")))
        if isinstance(content, (dict, list)):
            content_json = content if isinstance(content, dict) else {"items": content}
            content_text = json.dumps(content, ensure_ascii=False)
        else:
            content_text = str(content or "").strip()
            content_json = payload.get("contentJson") if isinstance(payload.get("contentJson"), dict) else {}
        if not content_text: raise IntelligenceValidationError("message content is required")
        if len(content_text) > 200000: raise IntelligenceValidationError("message content exceeds 200000 characters")
        intent = payload.get("intent") if isinstance(payload.get("intent"), dict) else {}
        analysis_id = str(payload.get("analysisRunId") or "").strip() or None
        with self._lock, self.store._connect() as con:
            session = con.execute("SELECT * FROM ontology_conversation_session WHERE conversation_session_id=?", (str(session_id),)).fetchone()
            if session is None: raise IntelligenceNotFound("conversation session not found")
            if session["status"] != "active": raise IntelligenceConflict("conversation session is closed or archived")
            if analysis_id:
                run = con.execute("SELECT entity_id FROM ontology_analysis_run WHERE analysis_run_id=?", (analysis_id,)).fetchone()
                if run is None: raise IntelligenceNotFound("analysis run not found")
                if run["entity_id"] and session["entity_id"] and run["entity_id"] != session["entity_id"]:
                    raise IntelligenceConflict("analysis run does not belong to conversation entity")
            sequence = int(con.execute("SELECT COALESCE(MAX(sequence_no),0)+1 FROM ontology_conversation_message WHERE conversation_session_id=?", (str(session_id),)).fetchone()[0])
            message_id, stamp = "message-" + secrets.token_hex(10), _now()
            content_hash = _hash({"role": role, "content": content_text, "contentJson": content_json, "intent": intent})
            con.execute("INSERT INTO ontology_conversation_message(conversation_message_id,conversation_session_id,analysis_run_id,sequence_no,role,content_text,content_json,intent_json,content_hash,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)", (message_id, str(session_id), analysis_id, sequence, role, content_text, _dump(content_json), _dump(intent), content_hash, stamp))
            con.execute("UPDATE ontology_conversation_session SET updated_at=?,last_message_at=? WHERE conversation_session_id=?", (stamp, stamp, str(session_id)))
            row = con.execute("SELECT * FROM ontology_conversation_message WHERE conversation_message_id=?", (message_id,)).fetchone()
        return self._message_row(row)

    append_message = add_message

    def submit_prompt(self, session_id: str, payload: dict[str, Any] | None, orchestrator) -> dict[str, Any]:
        """Create a user message and its bound AnalysisRun atomically at the API boundary.

        The run is created before the message is appended so a failed run
        request cannot leave a misleading user prompt with no execution link.
        The two durable records use the same session and intent; ontology
        publication remains a later human-reviewed action.
        """
        payload = dict(payload or {})
        session = self.get_session(session_id)
        if session["status"] != "active": raise IntelligenceConflict("conversation session is closed or archived")
        prompt = str(payload.get("prompt") or payload.get("content") or payload.get("text") or "").strip()
        if not prompt: raise IntelligenceValidationError("prompt is required")
        intent = payload.get("intent") if isinstance(payload.get("intent"), dict) else {}
        request = {**payload, "conversationSessionId": session_id, "prompt": prompt, "intent": intent, "trigger": "conversation", "autoPublish": False}
        run = orchestrator.create(request, entity_id=session.get("entityId"))
        try:
            message = self.add_message(session_id, {"role": "user", "content": prompt, "intent": intent, "analysisRunId": run["analysisRunId"]})
        except Exception:
            # Keep the run auditable while avoiding an orphaned active run when
            # a session is closed between the preflight and message append.
            try:
                orchestrator.cancel(run["analysisRunId"], "conversation message append failed")
            except Exception:
                pass
            raise
        return {"session": self.get_session(session_id), "analysisRun": run, "message": message}

    def bind_run(self, session_id: str, run_id: str, *, prompt: str | None = None, intent: dict[str, Any] | None = None) -> dict[str, Any]:
        with self._lock, self.store._connect() as con:
            session = con.execute("SELECT entity_id FROM ontology_conversation_session WHERE conversation_session_id=?", (str(session_id),)).fetchone()
            if session is None: raise IntelligenceNotFound("conversation session not found")
            run = con.execute("SELECT entity_id FROM ontology_analysis_run WHERE analysis_run_id=?", (str(run_id),)).fetchone()
            if run is None: raise IntelligenceNotFound("analysis run not found")
            if session["entity_id"] and run["entity_id"] and session["entity_id"] != run["entity_id"]:
                raise IntelligenceConflict("analysis run does not belong to conversation entity")
            con.execute("UPDATE ontology_analysis_run SET conversation_session_id=?,prompt_text=COALESCE(?,prompt_text),intent_json=COALESCE(?,intent_json),updated_at=? WHERE analysis_run_id=?", (str(session_id), prompt, _dump(intent or {}) if intent is not None else None, _now(), str(run_id)))
        return self.get_session(session_id)

    def close_session(self, session_id: str, *, status: str = "closed") -> dict[str, Any]:
        if status not in {"closed", "archived"}: raise IntelligenceValidationError("session can only close as closed or archived")
        with self._lock, self.store._connect() as con:
            row = con.execute("SELECT * FROM ontology_conversation_session WHERE conversation_session_id=?", (str(session_id),)).fetchone()
            if row is None: raise IntelligenceNotFound("conversation session not found")
            stamp = _now(); con.execute("UPDATE ontology_conversation_session SET status=?,closed_at=COALESCE(closed_at,?),updated_at=? WHERE conversation_session_id=?", (status, stamp, stamp, str(session_id)))
        return self.get_session(session_id)

    def restore_session(self, session_id: str) -> dict[str, Any]:
        with self._lock, self.store._connect() as con:
            row = con.execute("SELECT * FROM ontology_conversation_session WHERE conversation_session_id=?", (str(session_id),)).fetchone()
            if row is None: raise IntelligenceNotFound("conversation session not found")
            stamp = _now(); con.execute("UPDATE ontology_conversation_session SET status='active',closed_at=NULL,updated_at=? WHERE conversation_session_id=?", (stamp, str(session_id)))
        return self.get_session(session_id)

    reopen_session = restore_session

    def archive_session(self, session_id: str) -> dict[str, Any]:
        return self.close_session(session_id, status="archived")

    def clear_display(self, session_id: str) -> dict[str, Any]:
        """Advance the UI cursor while retaining every message row."""
        with self._lock, self.store._connect() as con:
            row = con.execute("SELECT * FROM ontology_conversation_session WHERE conversation_session_id=?", (str(session_id),)).fetchone()
            if row is None: raise IntelligenceNotFound("conversation session not found")
            cursor = int(con.execute("SELECT COALESCE(MAX(sequence_no),0) FROM ontology_conversation_message WHERE conversation_session_id=?", (str(session_id),)).fetchone()[0])
            stamp = _now(); con.execute("UPDATE ontology_conversation_session SET display_cursor=?,display_cleared_at=?,updated_at=? WHERE conversation_session_id=?", (cursor, stamp, stamp, str(session_id)))
        return self.get_session(session_id)


__all__ = ["ConversationStore", "SESSION_STATUSES", "MESSAGE_ROLES"]
