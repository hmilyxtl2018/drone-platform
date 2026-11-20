"""Append-only runtime store for the Agent workbench.

The ontology package SQLite file is a generated, read-only knowledge snapshot.
This module deliberately keeps run state in a second SQLite database (or in a
database path supplied by ``CRK_RUNTIME_SQLITE_PATH``).  Runtime events are
immutable and carry a content hash, causal/correlation links, and enough
provenance for a run to be replayed without consulting the UI.

The store is intentionally small for the first demo.  The table names mirror
``ontology/coast-raid-k/ddl-runtime.sql`` so the same API shape can later be
backed by PostgreSQL without changing the workbench endpoints.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote


ROOT = Path(__file__).resolve().parent
DEFAULT_RUNTIME_DB = ROOT.parent / "ontology" / "coast-raid-k" / "coast-raid-k-runtime.sqlite"
RUNTIME_NS = "https://local.ontology/coast-raid-k/run/"


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _json(value: Any) -> str:
    """Canonical JSON used for payload hashes and SQLite TEXT columns."""

    return json.dumps(value if value is not None else {}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _decode(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (dict, list, int, float, bool)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return value


def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    item = dict(row)
    for key in ("metadata", "payload", "provenance", "detail"):
        if key in item:
            item[key] = _decode(item[key])
    return item


def _run_row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    """Convert the SQL run projection to the API's JSON contract."""

    item = _row(row)
    if item is None:
        return None
    mapping = {
        "run_id": "runId", "package_id": "packageId", "package_version": "packageVersion",
        "model_id": "modelId", "model_revision": "modelRevision", "started_at": "startedAt",
        "ended_at": "endedAt", "manifest_hash": "manifestHash", "created_at": "createdAt",
        "updated_at": "updatedAt",
    }
    for snake, camel in mapping.items():
        item[camel] = item.pop(snake)
    return item


SQLITE_SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS crk_run (
  run_id          TEXT PRIMARY KEY,
  package_id      TEXT,
  package_version TEXT,
  model_id        TEXT,
  model_revision  TEXT,
  scenario        TEXT,
  status          TEXT NOT NULL DEFAULT 'created',
  started_at      TEXT NOT NULL,
  ended_at        TEXT,
  manifest_hash   TEXT,
  metadata        TEXT NOT NULL DEFAULT '{}',
  created_at      TEXT NOT NULL,
  updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS crk_runtime_event (
  event_id        TEXT PRIMARY KEY,
  run_id          TEXT NOT NULL REFERENCES crk_run(run_id) ON DELETE CASCADE,
  seq             INTEGER NOT NULL,
  event_type      TEXT NOT NULL,
  event_iri       TEXT NOT NULL,
  actor_iri       TEXT,
  subject_iri     TEXT,
  causation_id    TEXT,
  correlation_id  TEXT,
  occurred_at     TEXT NOT NULL,
  payload         TEXT NOT NULL,
  payload_sha256  TEXT NOT NULL,
  provenance      TEXT NOT NULL DEFAULT '{}',
  visibility      TEXT NOT NULL DEFAULT 'run',
  created_at      TEXT NOT NULL,
  UNIQUE(run_id, seq),
  UNIQUE(run_id, event_iri)
);

CREATE INDEX IF NOT EXISTS crk_runtime_event_run_seq_idx
  ON crk_runtime_event(run_id, seq);
CREATE INDEX IF NOT EXISTS crk_runtime_event_type_idx
  ON crk_runtime_event(run_id, event_type);
CREATE INDEX IF NOT EXISTS crk_runtime_event_causation_idx
  ON crk_runtime_event(run_id, causation_id);
CREATE INDEX IF NOT EXISTS crk_runtime_event_correlation_idx
  ON crk_runtime_event(run_id, correlation_id);

CREATE TABLE IF NOT EXISTS crk_runtime_evidence (
  evidence_id    TEXT PRIMARY KEY,
  run_id         TEXT NOT NULL REFERENCES crk_run(run_id) ON DELETE CASCADE,
  event_id       TEXT REFERENCES crk_runtime_event(event_id) ON DELETE CASCADE,
  subject_iri    TEXT,
  relation       TEXT NOT NULL,
  source_iri     TEXT,
  source_version TEXT,
  content_hash   TEXT,
  detail         TEXT NOT NULL DEFAULT '{}',
  created_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS crk_runtime_evidence_run_idx
  ON crk_runtime_evidence(run_id, created_at);
CREATE INDEX IF NOT EXISTS crk_runtime_evidence_event_idx
  ON crk_runtime_evidence(event_id);

CREATE TABLE IF NOT EXISTS crk_runtime_entity (
  run_id          TEXT NOT NULL REFERENCES crk_run(run_id) ON DELETE CASCADE,
  entity_iri      TEXT NOT NULL,
  entity_type     TEXT,
  status          TEXT,
  payload         TEXT NOT NULL DEFAULT '{}',
  latest_event_id TEXT NOT NULL REFERENCES crk_runtime_event(event_id),
  updated_at      TEXT NOT NULL,
  PRIMARY KEY(run_id, entity_iri)
);

CREATE TABLE IF NOT EXISTS crk_trigger (
  trigger_id       TEXT PRIMARY KEY,
  package_id       TEXT,
  package_version  TEXT,
  event_type       TEXT NOT NULL,
  condition        TEXT NOT NULL DEFAULT '{}',
  target_agent     TEXT,
  cooldown_seconds INTEGER NOT NULL DEFAULT 0,
  dedupe_key       TEXT,
  max_concurrency  INTEGER NOT NULL DEFAULT 1,
  requires_approval INTEGER NOT NULL DEFAULT 0,
  on_failure       TEXT NOT NULL DEFAULT 'audit',
  enabled          INTEGER NOT NULL DEFAULT 1,
  created_at       TEXT NOT NULL,
  updated_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS crk_human_review (
  review_id            TEXT PRIMARY KEY,
  run_id               TEXT NOT NULL REFERENCES crk_run(run_id) ON DELETE CASCADE,
  action_request_iri   TEXT NOT NULL,
  recommendation_iri   TEXT,
  requested_by         TEXT,
  status               TEXT NOT NULL DEFAULT 'pending',
  decision             TEXT,
  reason               TEXT,
  requested_at         TEXT NOT NULL,
  expires_at           TEXT,
  decided_at           TEXT,
  decided_by           TEXT,
  request_event_id     TEXT REFERENCES crk_runtime_event(event_id),
  decision_event_id    TEXT REFERENCES crk_runtime_event(event_id),
  payload              TEXT NOT NULL DEFAULT '{}',
  UNIQUE(run_id, action_request_iri, status)
);
CREATE INDEX IF NOT EXISTS crk_human_review_queue_idx
  ON crk_human_review(run_id, status, requested_at);

-- Runtime events and decisions are append-only.  State transitions happen
-- through new events; they never update or delete an existing event.
CREATE TRIGGER IF NOT EXISTS crk_runtime_event_no_update
BEFORE UPDATE ON crk_runtime_event
BEGIN
  SELECT RAISE(ABORT, 'crk_runtime_event is append-only');
END;
CREATE TRIGGER IF NOT EXISTS crk_runtime_event_no_delete
BEFORE DELETE ON crk_runtime_event
BEGIN
  SELECT RAISE(ABORT, 'crk_runtime_event is append-only');
END;
"""


class _ClosingConnection(sqlite3.Connection):
    """sqlite3's context manager commits but does not close the handle.

    Closing on ``with`` exit matters on Windows, where an open SQLite handle
    prevents a temporary runtime database from being replaced by a test or a
    local demo restart.
    """

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


class RuntimeStoreError(RuntimeError):
    """Base class for errors safe to return from the HTTP API."""


class NotFound(RuntimeStoreError):
    pass


class Conflict(RuntimeStoreError):
    pass


class ValidationError(RuntimeStoreError):
    pass


class RuntimeStore:
    """Small SQLite runtime ledger with atomic event sequencing."""

    def __init__(self, path: str | Path | None = None):
        raw = path or os.environ.get("CRK_RUNTIME_SQLITE_PATH") or DEFAULT_RUNTIME_DB
        self.path = Path(raw).expanduser()
        if not self.path.is_absolute():
            self.path = (ROOT.parent / self.path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(str(self.path), timeout=20, isolation_level=None, factory=_ClosingConnection)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys = ON")
        con.execute("PRAGMA busy_timeout = 20000")
        return con

    def _initialize(self) -> None:
        with self._connect() as con:
            con.executescript(SQLITE_SCHEMA)

    @staticmethod
    def _run_iri(run_id: str) -> str:
        return f"{RUNTIME_NS}{quote(str(run_id), safe='') }"

    @staticmethod
    def _event_iri(run_id: str, event_id: str) -> str:
        return f"{RUNTIME_NS}{quote(str(run_id), safe='')}/event/{quote(str(event_id), safe='')}"

    @staticmethod
    def _new_id(prefix: str) -> str:
        return f"{prefix}-{secrets.token_hex(8)}"

    def create_run(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValidationError("run payload must be an object")
        run_id = str(payload.get("runId") or payload.get("id") or self._new_id("run"))
        started_at = str(payload.get("startedAt") or _utc_now())
        now = _utc_now()
        metadata = payload.get("metadata") or {}
        with self._lock, self._connect() as con:
            try:
                con.execute(
                    """INSERT INTO crk_run
                    (run_id, package_id, package_version, model_id, model_revision,
                     scenario, status, started_at, ended_at, manifest_hash,
                     metadata, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        run_id,
                        payload.get("packageId"),
                        payload.get("packageVersion"),
                        payload.get("modelId"),
                        payload.get("modelRevision"),
                        payload.get("scenario"),
                        str(payload.get("status") or "created"),
                        started_at,
                        payload.get("endedAt"),
                        payload.get("manifestHash"),
                        _json(metadata),
                        now,
                        now,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise Conflict(f"run already exists: {run_id}") from exc
        return self.get_run(run_id)  # type: ignore[return-value]

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self._connect() as con:
            return _run_row(con.execute("SELECT * FROM crk_run WHERE run_id = ?", (str(run_id),)).fetchone())

    def list_runs(self, limit: int = 100) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit or 100), 500))
        with self._connect() as con:
            return [_run_row(row) for row in con.execute("SELECT * FROM crk_run ORDER BY created_at DESC LIMIT ?", (limit,))]

    def update_run(self, run_id: str, *, status: str | None = None, ended_at: str | None = None) -> dict[str, Any]:
        if self.get_run(run_id) is None:
            raise NotFound(f"run not found: {run_id}")
        fields: list[str] = []
        values: list[Any] = []
        if status is not None:
            fields.append("status = ?")
            values.append(str(status))
        if ended_at is not None:
            fields.append("ended_at = ?")
            values.append(ended_at)
        if fields:
            fields.append("updated_at = ?")
            values.append(_utc_now())
            values.append(str(run_id))
            with self._connect() as con:
                con.execute(f"UPDATE crk_run SET {', '.join(fields)} WHERE run_id = ?", values)
        return self.get_run(run_id)  # type: ignore[return-value]

    def _append_event_tx(self, con: sqlite3.Connection, run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        run_id = str(run_id)
        if con.execute("SELECT 1 FROM crk_run WHERE run_id = ?", (run_id,)).fetchone() is None:
            raise NotFound(f"run not found: {run_id}")
        event_type = str(payload.get("eventType") or payload.get("type") or "RuntimeEvent")
        event_payload = payload.get("payload") if "payload" in payload else payload.get("data", {})
        if event_payload is None:
            event_payload = {}
        if not isinstance(event_payload, (dict, list)):
            raise ValidationError("event payload must be an object or array")
        event_id = str(payload.get("eventId") or self._new_id("evt"))
        event_iri = str(payload.get("eventIri") or self._event_iri(run_id, event_id))
        occurred_at = str(payload.get("occurredAt") or _utc_now())
        created_at = _utc_now()
        payload_json = _json(event_payload)
        payload_hash = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
        row = con.execute("SELECT COALESCE(MAX(seq), 0) + 1 AS next_seq FROM crk_runtime_event WHERE run_id = ?", (run_id,)).fetchone()
        seq = int(row["next_seq"])
        try:
            con.execute(
                """INSERT INTO crk_runtime_event
                (event_id, run_id, seq, event_type, event_iri, actor_iri,
                 subject_iri, causation_id, correlation_id, occurred_at,
                 payload, payload_sha256, provenance, visibility, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    event_id,
                    run_id,
                    seq,
                    event_type,
                    event_iri,
                    payload.get("actorIri"),
                    payload.get("subjectIri"),
                    payload.get("causationId"),
                    payload.get("correlationId") or run_id,
                    occurred_at,
                    payload_json,
                    payload_hash,
                    _json(payload.get("provenance") or {}),
                    str(payload.get("visibility") or "run"),
                    created_at,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise Conflict(f"event already exists or sequence conflict: {event_id}") from exc
        event = {
            "eventId": event_id,
            "runId": run_id,
            "seq": seq,
            "eventType": event_type,
            "eventIri": event_iri,
            "actorIri": payload.get("actorIri"),
            "subjectIri": payload.get("subjectIri"),
            "causationId": payload.get("causationId"),
            "correlationId": payload.get("correlationId") or run_id,
            "occurredAt": occurred_at,
            "payload": event_payload,
            "payloadSha256": payload_hash,
            "provenance": payload.get("provenance") or {},
            "visibility": str(payload.get("visibility") or "run"),
            "createdAt": created_at,
        }
        self._project_entity_tx(con, event)
        self._insert_evidence_tx(con, run_id, event_id, payload.get("evidence") or [])
        return event

    def append_event(self, run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValidationError("event payload must be an object")
        with self._lock, self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            event = self._append_event_tx(con, str(run_id), payload)
            con.commit()
            return event

    def _project_entity_tx(self, con: sqlite3.Connection, event: dict[str, Any]) -> None:
        payload = event.get("payload")
        if not isinstance(payload, dict):
            return
        entity_iri = payload.get("entityIri") or payload.get("iri") or payload.get("subjectIri")
        if not entity_iri:
            return
        entity_iri = str(entity_iri)
        entity_type = payload.get("entityType") or payload.get("type") or event.get("eventType")
        status = payload.get("status") or payload.get("authorizationStatus")
        con.execute(
            """INSERT INTO crk_runtime_entity
            (run_id, entity_iri, entity_type, status, payload, latest_event_id, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id, entity_iri) DO UPDATE SET
              entity_type=excluded.entity_type, status=excluded.status,
              payload=excluded.payload, latest_event_id=excluded.latest_event_id,
              updated_at=excluded.updated_at""",
            (event["runId"], entity_iri, entity_type, status, _json(payload), event["eventId"], event["createdAt"]),
        )

    def _insert_evidence_tx(self, con: sqlite3.Connection, run_id: str, event_id: str, evidence: Iterable[Any]) -> None:
        for item in evidence:
            if isinstance(item, str):
                item = {"sourceIri": item, "relation": "supports"}
            if not isinstance(item, dict):
                continue
            con.execute(
                """INSERT INTO crk_runtime_evidence
                (evidence_id, run_id, event_id, subject_iri, relation,
                 source_iri, source_version, content_hash, detail, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    str(item.get("evidenceId") or self._new_id("evidence")),
                    run_id,
                    event_id,
                    item.get("subjectIri"),
                    str(item.get("relation") or "supports"),
                    item.get("sourceIri") or item.get("uri"),
                    item.get("sourceVersion"),
                    item.get("contentHash") or item.get("hash"),
                    _json(item.get("detail") or {}),
                    _utc_now(),
                ),
            )

    def list_events(self, run_id: str, *, after_seq: int = 0, limit: int = 500) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit or 500), 5000))
        with self._connect() as con:
            rows = con.execute(
                "SELECT * FROM crk_runtime_event WHERE run_id = ? AND seq > ? ORDER BY seq LIMIT ?",
                (str(run_id), max(0, int(after_seq or 0)), limit),
            ).fetchall()
        events: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item.update({"eventId": item.pop("event_id"), "runId": item.pop("run_id"), "seq": item["seq"]})
            item["eventType"] = item.pop("event_type")
            item["eventIri"] = item.pop("event_iri")
            item["actorIri"] = item.pop("actor_iri")
            item["subjectIri"] = item.pop("subject_iri")
            item["causationId"] = item.pop("causation_id")
            item["correlationId"] = item.pop("correlation_id")
            item["occurredAt"] = item.pop("occurred_at")
            item["payload"] = _decode(item.pop("payload"))
            item["payloadSha256"] = item.pop("payload_sha256")
            item["provenance"] = _decode(item.pop("provenance"))
            item["createdAt"] = item.pop("created_at")
            events.append(item)
        return events

    def list_evidence(self, run_id: str) -> list[dict[str, Any]]:
        with self._connect() as con:
            rows = con.execute("SELECT * FROM crk_runtime_evidence WHERE run_id = ? ORDER BY created_at", (str(run_id),)).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            for snake, camel in (("evidence_id", "evidenceId"), ("run_id", "runId"), ("event_id", "eventId"), ("subject_iri", "subjectIri"), ("source_iri", "sourceIri"), ("source_version", "sourceVersion"), ("content_hash", "contentHash"), ("created_at", "createdAt")):
                item[camel] = item.pop(snake)
            item["detail"] = _decode(item["detail"])
            result.append(item)
        return result

    def list_entities(self, run_id: str) -> list[dict[str, Any]]:
        with self._connect() as con:
            rows = con.execute("SELECT * FROM crk_runtime_entity WHERE run_id = ? ORDER BY updated_at", (str(run_id),)).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["runId"] = item.pop("run_id")
            item["entityIri"] = item.pop("entity_iri")
            item["entityType"] = item.pop("entity_type")
            item["latestEventId"] = item.pop("latest_event_id")
            item["payload"] = _decode(item["payload"])
            item["updatedAt"] = item.pop("updated_at")
            result.append(item)
        return result

    def trace(self, run_id: str) -> dict[str, Any]:
        run = self.get_run(run_id)
        if run is None:
            raise NotFound(f"run not found: {run_id}")
        return {
            "run": run,
            "events": self.list_events(run_id, limit=5000),
            "evidence": self.list_evidence(run_id),
            "entities": self.list_entities(run_id),
            "humanReviews": self.list_reviews(run_id),
        }

    def create_review(self, run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        if self.get_run(run_id) is None:
            raise NotFound(f"run not found: {run_id}")
        action_request = str(payload.get("actionRequestIri") or "")
        if not action_request:
            raise ValidationError("actionRequestIri is required")
        review_id = str(payload.get("reviewId") or self._new_id("review"))
        requested_at = str(payload.get("requestedAt") or _utc_now())
        review = {
            "reviewId": review_id,
            "runId": str(run_id),
            "actionRequestIri": action_request,
            "recommendationIri": payload.get("recommendationIri"),
            "requestedBy": payload.get("requestedBy"),
            "status": "pending",
            "decision": None,
            "reason": payload.get("reason"),
            "requestedAt": requested_at,
            "expiresAt": payload.get("expiresAt"),
            "decidedAt": None,
            "decidedBy": None,
            "payload": payload.get("payload") or {},
        }
        with self._lock, self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            try:
                event = self._append_event_tx(
                    con,
                    str(run_id),
                    {
                        "eventType": "ApprovalRequest",
                        "actorIri": payload.get("requestedBy"),
                        "subjectIri": action_request,
                        "correlationId": payload.get("correlationId") or str(run_id),
                        "occurredAt": requested_at,
                        "payload": {**review, "type": "ApprovalRequest"},
                        "evidence": payload.get("evidence") or [],
                    },
                )
                con.execute(
                    """INSERT INTO crk_human_review
                    (review_id, run_id, action_request_iri, recommendation_iri,
                     requested_by, status, decision, reason, requested_at,
                     expires_at, decided_at, decided_by, request_event_id,
                     decision_event_id, payload)
                    VALUES (?, ?, ?, ?, ?, 'pending', NULL, ?, ?, ?, NULL, NULL, ?, NULL, ?)""",
                    (review_id, str(run_id), action_request, payload.get("recommendationIri"), payload.get("requestedBy"), payload.get("reason"), requested_at, payload.get("expiresAt"), event["eventId"], _json(payload.get("payload") or {})),
                )
            except sqlite3.IntegrityError as exc:
                raise Conflict("a pending review already exists for this action request") from exc
            con.commit()
        return self.get_review(review_id)  # type: ignore[return-value]

    def get_review(self, review_id: str) -> dict[str, Any] | None:
        with self._connect() as con:
            row = con.execute("SELECT * FROM crk_human_review WHERE review_id = ?", (str(review_id),)).fetchone()
        return self._review_row(row)

    @staticmethod
    def _review_row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        item = dict(row)
        mapping = {
            "review_id": "reviewId", "run_id": "runId", "action_request_iri": "actionRequestIri",
            "recommendation_iri": "recommendationIri", "requested_by": "requestedBy",
            "requested_at": "requestedAt", "expires_at": "expiresAt", "decided_at": "decidedAt",
            "decided_by": "decidedBy", "request_event_id": "requestEventId", "decision_event_id": "decisionEventId",
        }
        for snake, camel in mapping.items():
            item[camel] = item.pop(snake)
        item["payload"] = _decode(item["payload"])
        return item

    def list_reviews(self, run_id: str, status: str | None = None) -> list[dict[str, Any]]:
        with self._connect() as con:
            if status:
                rows = con.execute("SELECT * FROM crk_human_review WHERE run_id = ? AND status = ? ORDER BY requested_at", (str(run_id), status)).fetchall()
            else:
                rows = con.execute("SELECT * FROM crk_human_review WHERE run_id = ? ORDER BY requested_at", (str(run_id),)).fetchall()
        return [self._review_row(row) for row in rows]  # type: ignore[list-item]

    def decide_review(self, review_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        decision = str(payload.get("decision") or "").lower()
        if decision not in {"approved", "rejected", "deferred", "escalated", "request-more-evidence"}:
            raise ValidationError("decision must be approved, rejected, deferred, escalated, or request-more-evidence")
        decided_by = str(payload.get("decidedBy") or "")
        if not decided_by:
            raise ValidationError("decidedBy is required")
        with self._lock, self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT * FROM crk_human_review WHERE review_id = ?", (str(review_id),)).fetchone()
            if row is None:
                raise NotFound(f"human review not found: {review_id}")
            if row["status"] != "pending":
                raise Conflict(f"human review is already {row['status']}")
            decided_at = str(payload.get("decidedAt") or _utc_now())
            review_payload = {
                "type": "HumanDecision",
                "reviewId": str(review_id),
                "runId": row["run_id"],
                "actionRequestIri": row["action_request_iri"],
                "recommendationIri": row["recommendation_iri"],
                "decision": decision,
                "reason": payload.get("reason"),
                "decidedBy": decided_by,
                "decidedAt": decided_at,
                "evidenceVersion": payload.get("evidenceVersion"),
            }
            event = self._append_event_tx(
                con,
                row["run_id"],
                {
                    "eventType": "HumanDecision",
                    "actorIri": decided_by,
                    "subjectIri": row["action_request_iri"],
                    "correlationId": payload.get("correlationId") or row["run_id"],
                    "occurredAt": decided_at,
                    "payload": review_payload,
                    "evidence": payload.get("evidence") or [],
                },
            )
            con.execute(
                """UPDATE crk_human_review
                SET status = ?, decision = ?, reason = ?, decided_at = ?,
                    decided_by = ?, decision_event_id = ?, payload = ?
                WHERE review_id = ?""",
                (decision, decision, payload.get("reason"), decided_at, decided_by, event["eventId"], _json(payload.get("payload") or {}), str(review_id)),
            )
            con.commit()
        return self.get_review(review_id)  # type: ignore[return-value]


__all__ = ["RuntimeStore", "RuntimeStoreError", "NotFound", "Conflict", "ValidationError", "DEFAULT_RUNTIME_DB"]
