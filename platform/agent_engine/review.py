"""Authenticated, append-only human decisions over immutable Run evidence.

Accept means a human accepted this specific candidate/artifact for this Run. It
does not certify source truth or publish to the ontology. New materials invalidate
earlier decisions until reviewed again; immutable historical rows remain visible.
"""
from __future__ import annotations

import hashlib
import json
import re

from agent_admin.credentials import Principal
from agent_tools.evidence import detect_conflicts
from agent_tools.evidence import candidate_fingerprint
from .evidence_repository import artifact_json_hash
from .repository import new_id
from .worker import control_context


DECISIONS = frozenset({"accept", "reject", "request-evidence", "resolve-conflict"})


def conflict_identity(conflict):
    value = {key: conflict.get(key) for key in ("entityId", "metric", "status")}
    value["candidateIds"] = sorted(conflict.get("candidateIds", []))
    return "conflict-" + hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def validate_review_request(principal, subject_type, subject_id, expected_hash, decision, reason, conflict_id=None):
    # Reject browser dicts and model-made actor objects before touching storage.
    if not isinstance(principal, Principal) or principal.kind != "human" or not principal.permits("reviewer"):
        raise PermissionError("an authenticated human reviewer is required")
    if subject_type not in {"candidate", "artifact"} or not isinstance(subject_id, str) or not 1 <= len(subject_id) <= 200:
        raise ValueError("invalid review subject")
    if not isinstance(expected_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_hash):
        raise ValueError("expected immutable subject hash is required")
    if decision not in DECISIONS or not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 4000:
        raise ValueError("review decision and a bounded reason are required")
    if decision == "resolve-conflict":
        if subject_type != "candidate" or not isinstance(conflict_id, str) or not re.fullmatch(r"conflict-[0-9a-f]{64}", conflict_id):
            raise ValueError("conflict resolution requires a candidate and the current conflict identity")
    elif conflict_id is not None:
        raise ValueError("conflict identity is only valid for resolve-conflict decisions")


def classify_reviews(rows, *, run_version, profile_hash, evidence_hash):
    latest = {}
    for row in rows:
        latest[(row["subject_type"], row["subject_id"])] = row["review_id"]
    result = []
    for row in rows:
        item = dict(row)
        stale_reasons = []
        if row.get("run_version") != run_version:
            stale_reasons.append("run-version-changed")
        if row.get("profile_hash") != profile_hash:
            stale_reasons.append("profile-snapshot-changed")
        if row.get("evidence_set_hash") != evidence_hash:
            stale_reasons.append("materials-or-conclusions-changed")
        superseded = latest[(row["subject_type"], row["subject_id"])] != row["review_id"]
        item.update({"stale": bool(stale_reasons), "staleReasons": stale_reasons, "superseded": superseded,
                     "effective": not superseded and not stale_reasons,
                     "status": "superseded" if superseded else "stale" if stale_reasons else "current"})
        result.append(item)
    return result


class ReviewService:
    def __init__(self, repository):
        self.repository = repository

    @staticmethod
    def _identity(cursor, principal):
        cursor.execute("SELECT * FROM agent_admin_identity WHERE principal_id=%s FOR SHARE", (principal.principal_id,))
        row = cursor.fetchone()
        if not row or not row["enabled"] or row["kind"] != "human" or not ({"reviewer", "admin"} & set(row["roles_json"])):
            raise PermissionError("human reviewer identity is disabled or lacks review permission")
        # Server object and live identity must agree; a stale/reconstructed actor
        # object cannot add privileges by changing roles or its display name.
        if set(principal.roles) != set(row["roles_json"]):
            raise PermissionError("authenticated reviewer roles changed; authenticate again")

    @staticmethod
    def _snapshot(cursor, run_id, lock=False):
        cursor.execute("SELECT run_id,agent_version FROM agent_run WHERE run_id=%s" + (" FOR UPDATE" if lock else ""), (run_id,))
        run = cursor.fetchone()
        if not run:
            raise ValueError("run not found")
        cursor.execute("SELECT profile_json,profile_hash FROM agent_run_profile_snapshot WHERE run_id=%s", (run_id,))
        profile = cursor.fetchone()
        if not profile or artifact_json_hash(profile["profile_json"]) != profile["profile_hash"]:
            raise ValueError("immutable profile snapshot is absent or its hash changed")
        cursor.execute("SELECT agent_review_evidence_hash(%s) AS evidence_hash", (run_id,))
        return {"run_version": run["agent_version"], "profile_hash": profile["profile_hash"],
                "evidence_hash": cursor.fetchone()["evidence_hash"]}

    @staticmethod
    def _subject(cursor, run_id, subject_type, subject_id, expected_hash):
        if subject_type == "candidate":
            cursor.execute("""SELECT c.run_id,c.fingerprint,c.candidate_json,s.artifact_id,s.artifact_hash
                FROM agent_evidence_candidate c JOIN agent_source_snapshot s ON s.source_id=c.source_id
                WHERE c.candidate_id=%s""", (subject_id,))
            candidate = cursor.fetchone()
            if not candidate or candidate["run_id"] != run_id:
                raise ValueError("candidate is absent or belongs to a different run")
            if candidate_fingerprint(candidate["candidate_json"]) != candidate["fingerprint"]:
                raise ValueError("candidate fingerprint changed")
            artifact_id, expected_artifact = candidate["artifact_id"], candidate["artifact_hash"]
            subject_hash = candidate["fingerprint"]
        else:
            artifact_id, expected_artifact = subject_id, expected_hash
            subject_hash = expected_hash
        cursor.execute("SELECT * FROM agent_run_artifact WHERE artifact_id=%s", (artifact_id,))
        artifact = cursor.fetchone()
        if not artifact or artifact["run_id"] != run_id:
            raise ValueError("artifact is absent or belongs to a different run")
        if artifact_json_hash(artifact["content_json"]) != artifact["content_hash"] or expected_artifact != artifact["content_hash"]:
            raise ValueError("review source artifact hash changed")
        if subject_hash != expected_hash:
            raise ValueError("review subject hash changed; reload before reviewing")
        return {"subject_hash": subject_hash, "artifact_id": artifact_id, "artifact_hash": artifact["content_hash"]}

    @staticmethod
    def _conflicts(cursor, run_id):
        cursor.execute("SELECT candidate_json FROM agent_evidence_candidate WHERE run_id=%s ORDER BY candidate_id", (run_id,))
        conflicts = detect_conflicts([dict(row["candidate_json"]) for row in cursor.fetchall()])
        return [{**item, "conflictId": conflict_identity(item)} for item in conflicts]

    def conflicts(self, run_id):
        with self.repository.connection_factory() as con:
            with con.cursor() as cursor:
                return self._conflicts(cursor, run_id)

    def decide(self, run_id, principal, *, subject_type, subject_id, expected_hash, decision, reason, conflict_id=None):
        validate_review_request(principal, subject_type, subject_id, expected_hash, decision, reason, conflict_id)
        with control_context(run_id), self.repository.connection_factory() as con:
            with con.cursor() as cursor:
                self._identity(cursor, principal)
                snapshot = self._snapshot(cursor, run_id, lock=True)
                subject = self._subject(cursor, run_id, subject_type, subject_id, expected_hash)
                if conflict_id is not None:
                    conflicts = self._conflicts(cursor, run_id)
                    if not any(item["conflictId"] == conflict_id and subject_id in item["candidateIds"] for item in conflicts):
                        raise ValueError("conflict is absent, changed, or does not include the reviewed candidate")
                cursor.execute("SELECT review_id FROM agent_human_review WHERE run_id=%s AND subject_type=%s AND subject_id=%s ORDER BY review_seq DESC LIMIT 1",
                               (run_id, subject_type, subject_id))
                prior = cursor.fetchone()
                review_id = new_id("review")
                cursor.execute("""INSERT INTO agent_human_review(review_id,run_id,subject_type,subject_id,subject_hash,
                    artifact_id,artifact_hash,decision,reason,conflict_id,reviewer_id,supersedes_review_id,
                    run_version,profile_hash,evidence_set_hash)
                    VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                    (review_id, run_id, subject_type, subject_id, subject["subject_hash"], subject["artifact_id"],
                     subject["artifact_hash"], decision, reason.strip(), conflict_id, principal.principal_id,
                     prior["review_id"] if prior else None, snapshot["run_version"], snapshot["profile_hash"], snapshot["evidence_hash"]))
                row = dict(cursor.fetchone())
                self.repository.event(cursor, run_id, "review.decided", None,
                                      {"reviewId": review_id, "subjectType": subject_type, "subjectId": subject_id,
                                       "decision": decision, "reviewerId": principal.principal_id, "subjectHash": expected_hash})
                return classify_reviews([row], **snapshot)[0]

    def list_reviews(self, run_id):
        with self.repository.connection_factory() as con:
            with con.cursor() as cursor:
                snapshot = self._snapshot(cursor, run_id)
                cursor.execute("SELECT * FROM agent_human_review WHERE run_id=%s ORDER BY review_seq", (run_id,))
                return classify_reviews([dict(row) for row in cursor.fetchall()], **snapshot)

    def trusted_review_states(self, run_id):
        states = {}
        candidates = {item["candidateId"]: item for item in self.repository.list_candidates(run_id)}
        for row in self.list_reviews(run_id):
            if not row["effective"]:
                continue
            approved = row["decision"] in {"accept", "resolve-conflict"}
            states[row["subject_id"]] = {"reviewStatus": "approved" if approved else "rejected" if row["decision"] == "reject" else "pending",
                "verificationStatus": "unverified", "truthVerified": False, "reviewId": row["review_id"],
                "fingerprint": row["subject_hash"], "decision": row["decision"], "reviewerId": row["reviewer_id"]}
            if row["subject_type"] == "candidate" and row["subject_id"] in candidates:
                candidate = candidates[row["subject_id"]]
                states[row["subject_id"]]["contentHash"] = candidate.get("contentHash")
                states[row["subject_id"]]["candidateHash"] = hashlib.sha256(json.dumps(
                    candidate, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()).hexdigest()
            if row["decision"] == "resolve-conflict":
                states["conflict:" + row["conflict_id"]] = {"status": "resolved", "reviewStatus": "approved", "reviewId": row["review_id"]}
        return states
