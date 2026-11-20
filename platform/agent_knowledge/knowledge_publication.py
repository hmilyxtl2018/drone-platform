"""P4-05 knowledge governance over immutable Run evidence.

The service creates a reviewable projection from current human review decisions.
An ``accept`` decision is a disposition of the saved material; it is never
converted into source truth, engineering certification, or ``truthVerified``.
Change sets, approvals, publications and withdrawals are append-only records.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
import secrets

from psycopg2.extras import Json

from agent_admin.credentials import Principal, token_digest
from agent_engine.evidence_repository import artifact_json_hash
from agent_tools.evidence import candidate_fingerprint, detect_conflicts


class KnowledgeNotFoundError(LookupError):
    pass


class KnowledgeConflictError(ValueError):
    pass


class KnowledgeValidationError(ValueError):
    pass


_ID = re.compile(r"^[A-Za-z0-9_.:/-]{1,240}$")
_KEY = re.compile(r"^[A-Za-z0-9_.:/-]{1,160}$")


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     allow_nan=False, separators=(",", ":")).encode("utf-8")).hexdigest()


def _text(value, name, maximum=4000):
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= maximum:
        raise KnowledgeValidationError(f"{name} is required")
    if any(ord(c) < 32 and c not in "\r\n\t" for c in value):
        raise KnowledgeValidationError(f"{name} contains a control character")
    return value.strip()


def _id(value, name, pattern=_ID):
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise KnowledgeValidationError(f"invalid {name}")
    return value


class KnowledgePublicationService:
    """Authenticated proposal, approval, publication and withdrawal operations.

    ``connection_factory`` must return a fresh PostgreSQL connection. The
    service deliberately has no SQLite or provider client dependency.
    """

    def __init__(self, connection_factory):
        self.connection_factory = connection_factory

    @staticmethod
    def _authorize(cursor, actor, actor_token, role="reviewer"):
        if not isinstance(actor, Principal) or actor.kind != "human":
            raise PermissionError("authenticated human identity required")
        digest = token_digest(actor_token)
        if digest is None:
            raise PermissionError("current human token required")
        cursor.execute("""SELECT i.principal_id,i.display_name,i.kind,i.enabled,i.roles_json
            FROM agent_admin_identity i JOIN agent_admin_token t USING(principal_id)
            WHERE i.principal_id=%s AND t.token_hash=%s AND t.revoked_at IS NULL
              AND t.expires_at>clock_timestamp()""", (actor.principal_id, digest))
        row = cursor.fetchone()
        roles = set(row["roles_json"]) if row else set()
        if (not row or row["kind"] != "human" or not row["enabled"]
                or role not in roles and "admin" not in roles
                or set(actor.roles) != roles):
            raise PermissionError("current human role is required")
        return row

    @staticmethod
    def _validate_expiry(expires_at):
        if expires_at is None:
            return datetime.now(timezone.utc) + timedelta(days=7)
        if isinstance(expires_at, str):
            try:
                expires_at = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
            except ValueError as error:
                raise KnowledgeValidationError("expiresAt must be an ISO timestamp") from error
        if not isinstance(expires_at, datetime):
            raise KnowledgeValidationError("expiresAt must be a timestamp")
        if expires_at.tzinfo is None:
            raise KnowledgeValidationError("expiresAt must include a timezone")
        expires_at = expires_at.astimezone(timezone.utc)
        now = datetime.now(timezone.utc)
        if expires_at <= now or expires_at > now + timedelta(days=90):
            raise KnowledgeValidationError("expiresAt must be within 90 days")
        return expires_at

    @staticmethod
    def _snapshot(cursor, run_id, lock=False):
        cursor.execute("SELECT run_id,agent_key,agent_version FROM agent_run WHERE run_id=%s" +
                       (" FOR UPDATE" if lock else ""), (run_id,))
        run = cursor.fetchone()
        if not run:
            raise KnowledgeNotFoundError("run not found")
        cursor.execute("SELECT profile_hash FROM agent_run_profile_snapshot WHERE run_id=%s", (run_id,))
        profile = cursor.fetchone()
        if not profile:
            raise KnowledgeConflictError("immutable profile snapshot is missing")
        cursor.execute("SELECT agent_review_evidence_hash(%s) AS evidence_hash", (run_id,))
        evidence = cursor.fetchone()["evidence_hash"]
        return {"run": dict(run), "profileHash": profile["profile_hash"], "evidenceHash": evidence}

    @staticmethod
    def _latest_reviews(cursor, run_id, snapshot):
        cursor.execute("""SELECT DISTINCT ON (subject_type,subject_id) review_id,subject_type,subject_id,
                   subject_hash,artifact_id,artifact_hash,decision,reviewer_id,run_version,profile_hash,
                   evidence_set_hash,created_at,conflict_id
            FROM agent_human_review WHERE run_id=%s
            ORDER BY subject_type,subject_id,review_seq DESC""", (run_id,))
        rows = [dict(row) for row in cursor.fetchall()]
        for row in rows:
            if (row["run_version"] != snapshot["run"]["agent_version"]
                    or row["profile_hash"] != snapshot["profileHash"]
                    or row["evidence_set_hash"] != snapshot["evidenceHash"]):
                row["effective"] = False
            else:
                row["effective"] = row["decision"] in {"accept", "resolve-conflict"}
        return rows

    @staticmethod
    def _candidate(cursor, run_id, candidate_id):
        cursor.execute("""SELECT c.candidate_json,s.source_id,s.artifact_id,s.artifact_hash,s.content_hash,
                   s.source_uri,a.content_json,a.content_hash AS stored_artifact_content_hash
            FROM agent_evidence_candidate c JOIN agent_source_snapshot s ON s.source_id=c.source_id
            JOIN agent_run_artifact a ON a.artifact_id=s.artifact_id
            WHERE c.run_id=%s AND c.candidate_id=%s""", (run_id, candidate_id))
        row = cursor.fetchone()
        if not row:
            raise KnowledgeValidationError("candidate is absent from the Run")
        candidate = dict(row["candidate_json"])
        if (candidate_fingerprint(candidate) != row["candidate_json"].get("fingerprint")
                or candidate.get("candidateId") != candidate_id
                or artifact_json_hash(row["content_json"]) != row["stored_artifact_content_hash"]
                or row["artifact_hash"] != row["stored_artifact_content_hash"]):
            raise KnowledgeConflictError("candidate or source artifact hash changed")
        return {"candidate": candidate, "source": dict(row)}

    @classmethod
    def _revalidate_payload(cls, cursor, row, snapshot):
        """Re-check every review and source referenced by an immutable proposal."""
        payload = row["payload_json"]
        if _hash(payload) != row["payload_hash"]:
            raise KnowledgeConflictError("change set payload hash changed")
        records = payload.get("records") if isinstance(payload, dict) else None
        if not isinstance(records, list) or not records:
            raise KnowledgeConflictError("change set records are invalid")
        reviews = {item["subject_id"]: item for item in cls._latest_reviews(cursor, row["run_id"], snapshot)}
        cursor.execute("SELECT candidate_json FROM agent_evidence_candidate WHERE run_id=%s ORDER BY candidate_id", (row["run_id"],))
        all_candidates = [dict(item["candidate_json"]) for item in cursor.fetchall()]
        conflict_candidates = {candidate for issue in detect_conflicts(all_candidates)
                              for candidate in issue["candidateIds"]}
        for record in records:
            candidate_id = record.get("candidateId")
            review = reviews.get(candidate_id)
            if (not review or not review["effective"] or review["decision"] not in {"accept", "resolve-conflict"}
                    or review["review_id"] != record.get("reviewId")
                    or (candidate_id in conflict_candidates and review["decision"] != "resolve-conflict")):
                raise KnowledgeConflictError("a referenced human review is stale or superseded")
            current = cls._candidate(cursor, row["run_id"], candidate_id)
            source = record.get("source") or {}
            for key, actual in (("sourceId", current["source"]["source_id"]),
                                ("artifactId", current["source"]["artifact_id"]),
                                ("artifactHash", current["source"]["artifact_hash"]),
                                ("contentHash", current["source"]["content_hash"])):
                if source.get(key) != actual:
                    raise KnowledgeConflictError("a referenced source snapshot changed")

    @staticmethod
    def _state(cursor, change_set_id):
        cursor.execute("SELECT event_type,event_id,actor_id,reason,created_at FROM agent_knowledge_change_event "
                       "WHERE change_set_id=%s ORDER BY created_at,event_id", (change_set_id,))
        rows = [dict(r) for r in cursor.fetchall()]
        if not rows:
            raise KnowledgeNotFoundError("change set history is absent")
        cursor.execute("SELECT expires_at FROM agent_knowledge_change_set WHERE change_set_id=%s", (change_set_id,))
        expiry = cursor.fetchone()["expires_at"]
        current = rows[-1]["event_type"]
        if current in {"proposed", "approved"} and expiry <= datetime.now(timezone.utc):
            current = "expired"
        return current, rows

    @staticmethod
    def _public_change(row, state=None):
        result = {"changeSetId": row["change_set_id"], "streamKey": row["stream_key"],
                  "runId": row["run_id"], "proposedBy": row["proposed_by"],
                  "idempotencyKey": row["idempotency_key"], "payload": row["payload_json"],
                  "payloadHash": row["payload_hash"], "evidenceSetHash": row["evidence_set_hash"],
                  "baseVersion": row["base_version"], "expiresAt": row["expires_at"],
                  "createdAt": row["created_at"]}
        if state is not None:
            result["status"] = state
        return result

    def propose_change_set(self, actor, run_id, *, stream_key, candidate_ids, actor_token,
                           reason, idempotency_key, expires_at=None):
        _id(run_id, "run ID")
        _id(stream_key, "stream key")
        _text(reason, "reason")
        _id(idempotency_key, "idempotency key", _KEY)
        if (not isinstance(candidate_ids, (list, tuple)) or not candidate_ids
                or len(candidate_ids) > 100 or len(set(candidate_ids)) != len(candidate_ids)):
            raise KnowledgeValidationError("one to one hundred unique candidates are required")
        candidate_ids = [_id(item, "candidate ID") for item in candidate_ids]
        expiry = self._validate_expiry(expires_at)
        with self.connection_factory() as con:
            with con.cursor() as cursor:
                self._authorize(cursor, actor, actor_token)
                snapshot = self._snapshot(cursor, run_id, lock=True)
                cursor.execute("SELECT * FROM agent_knowledge_change_set WHERE proposed_by=%s AND idempotency_key=%s",
                               (actor.principal_id, idempotency_key))
                previous = cursor.fetchone()
                if previous:
                    previous_ids = [item.get("candidateId") for item in (previous["payload_json"] or {}).get("records", [])]
                    if (previous["run_id"] != run_id or previous["stream_key"] != stream_key
                            or previous_ids != candidate_ids):
                        raise KnowledgeConflictError("idempotency key belongs to another proposal")
                    state, _ = self._state(cursor, previous["change_set_id"])
                    return self._public_change(dict(previous), state)
                reviews = {row["subject_id"]: row for row in self._latest_reviews(cursor, run_id, snapshot)}
                cursor.execute("SELECT candidate_json FROM agent_evidence_candidate WHERE run_id=%s ORDER BY candidate_id", (run_id,))
                all_candidates = [dict(item["candidate_json"]) for item in cursor.fetchall()]
                conflicts = detect_conflicts(all_candidates)
                conflict_candidates = {candidate for item in conflicts for candidate in item["candidateIds"]}
                records = []
                for candidate_id in candidate_ids:
                    review = reviews.get(candidate_id)
                    if not review or not review["effective"] or review["decision"] not in {"accept", "resolve-conflict"}:
                        raise KnowledgeConflictError("every candidate needs a current human acceptance")
                    if candidate_id in conflict_candidates and review["decision"] != "resolve-conflict":
                        raise KnowledgeConflictError("unresolved candidate conflict blocks proposal")
                    item = self._candidate(cursor, run_id, candidate_id)
                    candidate = item["candidate"]
                    records.append({"candidateId": candidate_id, "candidate": candidate,
                        "reviewId": review["review_id"], "reviewerId": review["reviewer_id"],
                        "reviewDecision": review["decision"], "source": {
                            "sourceId": item["source"]["source_id"], "sourceUri": item["source"]["source_uri"],
                            "artifactId": item["source"]["artifact_id"], "artifactHash": item["source"]["artifact_hash"],
                            "contentHash": item["source"]["content_hash"]},
                        "truthVerified": False, "verificationStatus": "human-reviewed-unverified",
                        "engineeringCertification": False})
                payload = {"schemaVersion": "knowledge-change-set.v1", "streamKey": stream_key,
                           "runId": run_id, "records": records, "truthVerified": False,
                           "verificationStatus": "human-reviewed-unverified",
                           "sourceTruthMeaning": "human disposition of immutable material; independent verification remains required"}
                payload_hash, evidence_hash = _hash(payload), snapshot["evidenceHash"]
                change_set_id = "knowledge-change-" + secrets.token_hex(12)
                cursor.execute("SELECT pg_advisory_xact_lock(hashtextextended('knowledge-proposal:'||%s,0))", (stream_key,))
                cursor.execute("""INSERT INTO agent_knowledge_change_set(change_set_id,stream_key,run_id,
                    proposed_by,idempotency_key,payload_json,payload_hash,evidence_set_hash,base_version,expires_at)
                    VALUES(%s,%s,%s,%s,%s,%s,%s,%s,COALESCE((SELECT max(version) FROM agent_knowledge_publication WHERE stream_key=%s),0),%s)""",
                    (change_set_id, stream_key, run_id, actor.principal_id, idempotency_key, Json(payload),
                     payload_hash, evidence_hash, stream_key, expiry))
                cursor.execute("""INSERT INTO agent_knowledge_change_event(event_id,change_set_id,event_type,
                    actor_id,expected_hash,reason,idempotency_key) VALUES(%s,%s,'proposed',%s,%s,%s,%s)""",
                    ("knowledge-event-" + secrets.token_hex(12), change_set_id, actor.principal_id,
                     payload_hash, reason, idempotency_key))
                return self._public_change({"change_set_id": change_set_id, "stream_key": stream_key,
                    "run_id": run_id, "proposed_by": actor.principal_id, "idempotency_key": idempotency_key,
                    "payload_json": payload, "payload_hash": payload_hash, "evidence_set_hash": evidence_hash,
                    "base_version": 0, "expires_at": expiry, "created_at": datetime.now(timezone.utc)}, "proposed")

    def approve_change_set(self, actor, change_set_id, *, actor_token, expected_hash, reason, idempotency_key):
        _id(change_set_id, "change set ID")
        _text(reason, "reason")
        _id(idempotency_key, "idempotency key", _KEY)
        if not isinstance(expected_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_hash):
            raise KnowledgeValidationError("current change set hash is required")
        with self.connection_factory() as con:
            with con.cursor() as cursor:
                self._authorize(cursor, actor, actor_token)
                cursor.execute("SELECT * FROM agent_knowledge_change_set WHERE change_set_id=%s FOR UPDATE", (change_set_id,))
                row = cursor.fetchone()
                if not row:
                    raise KnowledgeNotFoundError("change set not found")
                row = dict(row)
                if row["payload_hash"] != expected_hash:
                    raise KnowledgeConflictError("change set hash changed")
                if row["proposed_by"] == actor.principal_id:
                    raise PermissionError("proposal and approval require independent humans")
                cursor.execute("SELECT event_id,actor_id,reason,created_at FROM agent_knowledge_change_event WHERE change_set_id=%s AND event_type='approved' AND idempotency_key=%s",
                               (change_set_id, idempotency_key))
                prior = cursor.fetchone()
                if prior:
                    if prior["actor_id"] != actor.principal_id:
                        raise KnowledgeConflictError("approval idempotency key belongs to another actor")
                    return {"changeSetId": change_set_id, "status": "approved", "approvalEventId": prior["event_id"],
                            "approvedBy": actor.principal_id, "approvedAt": prior["created_at"]}
                state, _ = self._state(cursor, change_set_id)
                if state != "proposed":
                    raise KnowledgeConflictError("change set is no longer awaiting approval")
                if row["expires_at"] <= datetime.now(timezone.utc):
                    raise KnowledgeConflictError("change set evidence has expired")
                snapshot = self._snapshot(cursor, row["run_id"])
                if snapshot["evidenceHash"] != row["evidence_set_hash"]:
                    raise KnowledgeConflictError("source or review evidence changed; propose again")
                self._revalidate_payload(cursor, row, snapshot)
                event_id = "knowledge-event-" + secrets.token_hex(12)
                cursor.execute("""INSERT INTO agent_knowledge_change_event(event_id,change_set_id,event_type,
                    actor_id,expected_hash,reason,idempotency_key) VALUES(%s,%s,'approved',%s,%s,%s,%s)""",
                    (event_id, change_set_id, actor.principal_id, expected_hash, reason, idempotency_key))
                return {"changeSetId": change_set_id, "status": "approved", "approvalEventId": event_id,
                        "approvedBy": actor.principal_id}

    def reject_change_set(self, actor, change_set_id, *, actor_token, expected_hash, reason, idempotency_key):
        """Record an independent human rejection; the proposal remains immutable."""
        _id(change_set_id, "change set ID")
        _text(reason, "reason")
        _id(idempotency_key, "idempotency key", _KEY)
        if not isinstance(expected_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_hash):
            raise KnowledgeValidationError("current change set hash is required")
        with self.connection_factory() as con:
            with con.cursor() as cursor:
                self._authorize(cursor, actor, actor_token)
                cursor.execute("SELECT * FROM agent_knowledge_change_set WHERE change_set_id=%s FOR UPDATE", (change_set_id,))
                row = cursor.fetchone()
                if not row:
                    raise KnowledgeNotFoundError("change set not found")
                row = dict(row)
                if row["payload_hash"] != expected_hash:
                    raise KnowledgeConflictError("change set hash changed")
                if row["proposed_by"] == actor.principal_id:
                    raise PermissionError("proposal and rejection require an independent human")
                cursor.execute("SELECT event_id,actor_id,created_at FROM agent_knowledge_change_event WHERE change_set_id=%s AND event_type='rejected' AND idempotency_key=%s",
                               (change_set_id, idempotency_key))
                prior = cursor.fetchone()
                if prior:
                    if prior["actor_id"] != actor.principal_id:
                        raise KnowledgeConflictError("rejection idempotency key belongs to another actor")
                    return {"changeSetId": change_set_id, "status": "rejected", "rejectionEventId": prior["event_id"],
                            "rejectedBy": actor.principal_id, "rejectedAt": prior["created_at"]}
                state, _ = self._state(cursor, change_set_id)
                if state != "proposed":
                    raise KnowledgeConflictError("change set is no longer awaiting a decision")
                event_id = "knowledge-event-" + secrets.token_hex(12)
                cursor.execute("""INSERT INTO agent_knowledge_change_event(event_id,change_set_id,event_type,
                    actor_id,expected_hash,reason,idempotency_key) VALUES(%s,%s,'rejected',%s,%s,%s,%s)""",
                    (event_id, change_set_id, actor.principal_id, expected_hash, reason, idempotency_key))
                return {"changeSetId": change_set_id, "status": "rejected", "rejectionEventId": event_id,
                        "rejectedBy": actor.principal_id}

    def publish_change_set(self, actor, change_set_id, *, actor_token, expected_hash, reason, idempotency_key):
        _id(change_set_id, "change set ID")
        _text(reason, "reason")
        _id(idempotency_key, "idempotency key", _KEY)
        with self.connection_factory() as con:
            with con.cursor() as cursor:
                self._authorize(cursor, actor, actor_token)
                cursor.execute("SELECT * FROM agent_knowledge_change_set WHERE change_set_id=%s FOR UPDATE", (change_set_id,))
                row = cursor.fetchone()
                if not row:
                    raise KnowledgeNotFoundError("change set not found")
                row = dict(row)
                if row["payload_hash"] != expected_hash:
                    raise KnowledgeConflictError("change set hash changed")
                if row["proposed_by"] == actor.principal_id:
                    raise PermissionError("proposal and publication require an independent human")
                cursor.execute("SELECT * FROM agent_knowledge_publication WHERE stream_key=%s AND idempotency_key=%s",
                               (row["stream_key"], idempotency_key))
                previous = cursor.fetchone()
                if previous:
                    if previous["published_by"] != actor.principal_id or previous["change_set_id"] != change_set_id:
                        raise KnowledgeConflictError("publication idempotency key belongs to another request")
                    return self._public_publication(dict(previous), self._publication_state(cursor, previous["publication_id"]))
                state, events = self._state(cursor, change_set_id)
                if state != "approved":
                    raise KnowledgeConflictError("an independent approval is required")
                if row["expires_at"] <= datetime.now(timezone.utc):
                    raise KnowledgeConflictError("approved evidence has expired")
                snapshot = self._snapshot(cursor, row["run_id"])
                if snapshot["evidenceHash"] != row["evidence_set_hash"]:
                    raise KnowledgeConflictError("source or review evidence changed; publish is stale")
                self._revalidate_payload(cursor, row, snapshot)
                cursor.execute("SELECT pg_advisory_xact_lock(hashtextextended('knowledge-publication:'||%s,0))", (row["stream_key"],))
                cursor.execute("SELECT COALESCE(max(version),0) AS current_version FROM agent_knowledge_publication WHERE stream_key=%s", (row["stream_key"],))
                current_version = cursor.fetchone()["current_version"]
                if current_version != row["base_version"]:
                    raise KnowledgeConflictError("knowledge stream advanced; rebase and approve a fresh change set")
                cursor.execute("SELECT COALESCE(max(version),0)+1 AS next_version FROM agent_knowledge_publication WHERE stream_key=%s", (row["stream_key"],))
                version = cursor.fetchone()["next_version"]
                publication_id = "knowledge-publication-" + secrets.token_hex(12)
                cursor.execute("""INSERT INTO agent_knowledge_publication(publication_id,stream_key,version,
                    change_set_id,payload_json,payload_hash,evidence_set_hash,published_by,idempotency_key)
                    VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (publication_id, row["stream_key"], version, change_set_id, Json(row["payload_json"]),
                     row["payload_hash"], row["evidence_set_hash"], actor.principal_id, idempotency_key))
                cursor.execute("""INSERT INTO agent_knowledge_publication_event(event_id,publication_id,event_type,
                    actor_id,reason,idempotency_key) VALUES(%s,%s,'published',%s,%s,%s)""",
                    ("knowledge-event-" + secrets.token_hex(12), publication_id, actor.principal_id, reason, idempotency_key))
                cursor.execute("SELECT * FROM agent_knowledge_publication WHERE publication_id=%s", (publication_id,))
                return self._public_publication(dict(cursor.fetchone()), "published")

    @staticmethod
    def _publication_state(cursor, publication_id):
        cursor.execute("SELECT event_type FROM agent_knowledge_publication_event WHERE publication_id=%s ORDER BY created_at DESC,event_id DESC LIMIT 1", (publication_id,))
        row = cursor.fetchone()
        return row["event_type"] if row else "published"

    @staticmethod
    def _public_publication(row, state):
        return {"publicationId": row["publication_id"], "streamKey": row["stream_key"], "version": row["version"],
                "changeSetId": row["change_set_id"], "payload": row["payload_json"], "payloadHash": row["payload_hash"],
                "evidenceSetHash": row["evidence_set_hash"], "publishedBy": row["published_by"],
                "publishedAt": row["published_at"], "idempotencyKey": row["idempotency_key"], "status": state}

    def withdraw_publication(self, actor, publication_id, *, actor_token, reason, idempotency_key):
        _id(publication_id, "publication ID")
        _text(reason, "reason")
        _id(idempotency_key, "idempotency key", _KEY)
        with self.connection_factory() as con:
            with con.cursor() as cursor:
                self._authorize(cursor, actor, actor_token)
                cursor.execute("SELECT * FROM agent_knowledge_publication WHERE publication_id=%s FOR UPDATE", (publication_id,))
                row = cursor.fetchone()
                if not row:
                    raise KnowledgeNotFoundError("publication not found")
                state = self._publication_state(cursor, publication_id)
                if state == "withdrawn":
                    cursor.execute("SELECT event_id FROM agent_knowledge_publication_event WHERE publication_id=%s AND event_type='withdrawn' AND idempotency_key=%s", (publication_id, idempotency_key))
                    prior = cursor.fetchone()
                    if prior:
                        cursor.execute("SELECT actor_id FROM agent_knowledge_publication_event WHERE event_id=%s", (prior["event_id"],))
                        if cursor.fetchone()["actor_id"] != actor.principal_id:
                            raise KnowledgeConflictError("withdrawal idempotency key belongs to another actor")
                        return {"publicationId": publication_id, "status": "withdrawn", "withdrawalEventId": prior["event_id"]}
                    raise KnowledgeConflictError("publication is already withdrawn")
                event_id = "knowledge-event-" + secrets.token_hex(12)
                cursor.execute("""INSERT INTO agent_knowledge_publication_event(event_id,publication_id,event_type,
                    actor_id,reason,idempotency_key,replaces_publication_id) VALUES(%s,%s,'withdrawn',%s,%s,%s,NULL)""",
                    (event_id, publication_id, actor.principal_id, reason, idempotency_key))
                return {"publicationId": publication_id, "status": "withdrawn", "withdrawalEventId": event_id,
                        "withdrawnBy": actor.principal_id}

    def list_change_sets(self, actor, stream_key, *, actor_token, limit=100):
        _id(stream_key, "stream key")
        if type(limit) is not int or not 1 <= limit <= 500:
            raise KnowledgeValidationError("limit must be between 1 and 500")
        with self.connection_factory() as con:
            with con.cursor() as cursor:
                self._authorize(cursor, actor, actor_token)
                cursor.execute("SELECT * FROM agent_knowledge_change_set WHERE stream_key=%s ORDER BY created_at DESC,change_set_id DESC LIMIT %s", (stream_key, limit))
                result = []
                for row in cursor.fetchall():
                    state, _ = self._state(cursor, row["change_set_id"])
                    result.append(self._public_change(dict(row), state))
                return result

    def get_change_set(self, actor, change_set_id, *, actor_token):
        _id(change_set_id, "change set ID")
        with self.connection_factory() as con:
            with con.cursor() as cursor:
                self._authorize(cursor, actor, actor_token)
                cursor.execute("SELECT * FROM agent_knowledge_change_set WHERE change_set_id=%s", (change_set_id,))
                row = cursor.fetchone()
                if not row:
                    raise KnowledgeNotFoundError("change set not found")
                state, events = self._state(cursor, change_set_id)
                result = self._public_change(dict(row), state)
                result["events"] = [dict(event) for event in events]
                return result

    def list_publications(self, actor, stream_key, *, actor_token, limit=100):
        _id(stream_key, "stream key")
        if type(limit) is not int or not 1 <= limit <= 500:
            raise KnowledgeValidationError("limit must be between 1 and 500")
        with self.connection_factory() as con:
            with con.cursor() as cursor:
                self._authorize(cursor, actor, actor_token)
                cursor.execute("SELECT * FROM agent_knowledge_publication WHERE stream_key=%s ORDER BY version DESC LIMIT %s", (stream_key, limit))
                return [self._public_publication(dict(row), self._publication_state(cursor, row["publication_id"])) for row in cursor.fetchall()]

    def get_publication(self, actor, publication_id, *, actor_token):
        _id(publication_id, "publication ID")
        with self.connection_factory() as con:
            with con.cursor() as cursor:
                self._authorize(cursor, actor, actor_token)
                cursor.execute("SELECT * FROM agent_knowledge_publication WHERE publication_id=%s", (publication_id,))
                row = cursor.fetchone()
                if not row:
                    raise KnowledgeNotFoundError("publication not found")
                result = self._public_publication(dict(row), self._publication_state(cursor, publication_id))
                cursor.execute("SELECT event_id,event_type,actor_id,reason,idempotency_key,replaces_publication_id,created_at "
                               "FROM agent_knowledge_publication_event WHERE publication_id=%s ORDER BY created_at,event_id", (publication_id,))
                result["events"] = [dict(event) for event in cursor.fetchall()]
                return result
