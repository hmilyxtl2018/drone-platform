"""Read-only, authenticated views of persisted review material.

No source URL or file is opened. Every body is obtained from a Run-owned source
artifact and checked before presentation. Human decisions remain exclusively in
ReviewService; model review artifacts are always untrusted recommendations.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
import os
import re

from agent_admin.credentials import Principal, token_digest
from agent_engine.evidence_repository import EvidenceRepository, artifact_json_hash
from agent_engine.review import ReviewService, classify_reviews, conflict_identity
from agent_tools.evidence import candidate_fingerprint, content_hash, detect_conflicts, source_groups


_ID = re.compile(r"[A-Za-z0-9_.:-]{1,200}")
_SECRET_NAME = re.compile(r"(?:api[_-]?key|authorization|access[_-]?token|refresh[_-]?token|password|passwd|secret|credential|token_hash)$", re.I)
_SECRET_TEXT = (
    re.compile(r"\baat_[A-Za-z0-9_-]{20,}"),
    re.compile(r"\b(?:sk|fc)-[A-Za-z0-9_-]{8,}"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]+", re.I),
    re.compile(r"\b(?:[A-Z0-9_]*API[_-]?KEY|access[_-]?token|password|secret)\s*[:=]\s*[\"']?[^\s,;\"']+", re.I),
    re.compile(r"(?<=[?&])(?:token|api[_-]?key|key|signature|access[_-]?token)=[^&#\s]+", re.I),
    re.compile(r"(?<=://)[^/@\s]+:[^/@\s]+@"),
)
_MAX_ITEMS = 2000
_MAX_ARTIFACT_BYTES = 8_000_000


def _id(value, name):
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ValueError("invalid " + name)
    return value


def _masked_text(text, credentials):
    """Mask without changing Unicode code-point offsets in a source body."""
    ranges = []
    for secret in credentials:
        if isinstance(secret, str) and secret:
            start = text.find(secret)
            while start >= 0:
                ranges.append((start, start + len(secret)))
                start = text.find(secret, start + len(secret))
    for pattern in _SECRET_TEXT:
        ranges.extend(match.span() for match in pattern.finditer(text))
    if not ranges:
        return text
    characters = list(text)
    for start, end in ranges:
        for index in range(start, end):
            # Keep stored line boundaries as well as code-point positions.
            if characters[index] not in "\r\n":
                characters[index] = "█"
    return "".join(characters)


def _safe(value, credentials):
    if isinstance(value, str):
        return _masked_text(value, credentials)
    if isinstance(value, dict):
        return {_masked_text(str(key), credentials): "[REDACTED]" if _SECRET_NAME.search(str(key))
                else _safe(item, credentials) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe(item, credentials) for item in value]
    return value


class ReviewWorkbenchService:
    """Snapshot-consistent review queries over fresh repository connections.

    The supplied repository factory must return a fresh, unshared connection,
    as required by the Engine repositories. Factory setup (search_path/fencing)
    is committed before starting a read-only REPEATABLE READ view. No mutation,
    network request or credential issuance takes place in this service.
    """
    def __init__(self, repository: EvidenceRepository, review_service=None):
        self.repository = repository
        self.review_service = review_service or ReviewService(repository)

    @staticmethod
    def _authorize(cursor, actor, actor_token):
        if not isinstance(actor, Principal) or actor.kind != "human":
            raise PermissionError("authenticated human reviewer required")
        digest = token_digest(actor_token)
        if digest is None:
            raise PermissionError("current reviewer token required")
        cursor.execute("""SELECT i.principal_id,i.display_name,i.kind,i.enabled,i.roles_json
            FROM agent_admin_identity i JOIN agent_admin_token t USING(principal_id)
            WHERE i.principal_id=%s AND t.token_hash=%s AND t.revoked_at IS NULL
              AND t.expires_at>clock_timestamp()""", (actor.principal_id, digest))
        row = cursor.fetchone()
        if (not row or not row["enabled"] or row["kind"] != "human"
                or not {"admin", "reviewer"}.intersection(row["roles_json"])
                or set(actor.roles) != set(row["roles_json"])):
            raise PermissionError("current human reviewer permission required")
        return {"principalId": row["principal_id"], "displayName": row["display_name"], "roles": row["roles_json"]}

    @contextmanager
    def _view(self, actor, run_id, actor_token):
        _id(run_id, "run ID")
        with self.repository.connection_factory() as con:
            # A fresh factory may already have SET search_path / set_config in
            # a setup transaction. Keep session setup, discard local fencing.
            con.commit()
            con.set_session(isolation_level="REPEATABLE READ", readonly=True)
            with con.cursor() as cursor:
                identity = self._authorize(cursor, actor, actor_token)
                cursor.execute("SELECT run_id,agent_key,agent_version,status FROM agent_run WHERE run_id=%s", (run_id,))
                row = cursor.fetchone()
                if not row:
                    raise ValueError("run not found")
                run = dict(row)
                snapshot = self.review_service._snapshot(cursor, run_id)
                cursor.execute("SELECT profile_json FROM agent_run_profile_snapshot WHERE run_id=%s", (run_id,))
                profile = cursor.fetchone()["profile_json"]
                credentials = [actor_token]
                model = profile.get("modelPolicy", {}).get("providerSnapshot", {})
                web = profile.get("sourcePolicy", {}).get("firecrawlProvider", {})
                for reference in (model.get("credential_ref"), web.get("credentialRef")):
                    if isinstance(reference, str):
                        variable = reference[4:] if reference.startswith("env:") else reference
                        if re.fullmatch(r"[A-Z_][A-Z0-9_]{0,127}", variable):
                            secret = os.environ.get(variable)
                            if secret:
                                credentials.append(secret)
                yield cursor, run, snapshot, identity, tuple(credentials)

    @staticmethod
    def _artifact(cursor, run_id, artifact_id):
        cursor.execute("SELECT artifact_id,run_id,artifact_type,content_json,content_hash FROM agent_run_artifact "
                       "WHERE artifact_id=%s AND run_id=%s", (artifact_id, run_id))
        row = cursor.fetchone()
        if not row:
            raise ValueError("artifact is absent or belongs to a different run")
        artifact = dict(row)
        if artifact_json_hash(artifact["content_json"]) != artifact["content_hash"]:
            raise ValueError("artifact content hash mismatch")
        if len(str(artifact["content_json"]).encode("utf-8")) > _MAX_ARTIFACT_BYTES:
            raise ValueError("artifact exceeds review view limit")
        return artifact

    def _material(self, cursor, run_id):
        cursor.execute("SELECT source_id FROM agent_source_snapshot WHERE run_id=%s ORDER BY created_at,source_id LIMIT %s",
                       (run_id, _MAX_ITEMS + 1))
        ids = [row["source_id"] for row in cursor.fetchall()]
        if len(ids) > _MAX_ITEMS:
            raise ValueError("source set exceeds review view limit")
        sources = [self.repository._source(cursor, run_id, source_id) for source_id in ids]
        if sum(len(source["markdown"].encode("utf-8")) for source in sources) > _MAX_ARTIFACT_BYTES:
            raise ValueError("source text set exceeds review view limit")
        cursor.execute("SELECT candidate_id,source_id,fingerprint,candidate_json FROM agent_evidence_candidate "
                       "WHERE run_id=%s ORDER BY created_at,candidate_id LIMIT %s", (run_id, _MAX_ITEMS + 1))
        rows = cursor.fetchall()
        if len(rows) > _MAX_ITEMS:
            raise ValueError("candidate set exceeds review view limit")
        by_source = {source["sourceId"]: source for source in sources}
        candidates = []
        for row in rows:
            candidate = dict(row["candidate_json"])
            source = by_source.get(row["source_id"])
            start, end = candidate.get("startOffset"), candidate.get("endOffset")
            if (candidate_fingerprint(candidate) != row["fingerprint"] or candidate.get("candidateId") != row["candidate_id"]
                    or candidate.get("fingerprint") != row["fingerprint"] or not source
                    or candidate.get("sourceId") != source["sourceId"]
                    or candidate.get("sourceArtifactId") != source["artifactId"]
                    or candidate.get("sourceArtifactHash") != source["artifactHash"]
                    or candidate.get("contentHash") != source["contentHash"]
                    or candidate.get("offsetUnit") != "unicode-codepoint"
                    or type(start) is not int or type(end) is not int
                    or not 0 <= start < end <= len(source["markdown"])
                    or source["markdown"][start:end] != candidate.get("quote")):
                raise ValueError("candidate source citation or fingerprint mismatch")
            candidates.append(candidate)
        return sources, candidates

    @staticmethod
    def _reviews(cursor, run_id, snapshot):
        cursor.execute("SELECT * FROM agent_human_review WHERE run_id=%s ORDER BY review_seq LIMIT %s", (run_id, _MAX_ITEMS + 1))
        rows = cursor.fetchall()
        if len(rows) > _MAX_ITEMS:
            raise ValueError("review history exceeds review view limit")
        return classify_reviews([dict(row) for row in rows], **snapshot)

    @staticmethod
    def _related(cursor, run_id):
        cursor.execute("SELECT to_regclass('agent_run_handoff') AS handoff,to_regclass('agent_run_followup') AS followup,"
                       "to_regclass('agent_workspace_run') AS workspace")
        tables = cursor.fetchone()
        handoffs, followups, workspaces = [], [], []
        if tables["handoff"]:
            cursor.execute("""WITH RECURSIVE related(run_id) AS (
                SELECT %s::text UNION SELECT h.child_run_id FROM agent_run_handoff h JOIN related r ON h.run_id=r.run_id)
                SELECT h.handoff_id,h.run_id,h.child_run_id,h.artifacts_json,h.artifacts_hash
                FROM agent_run_handoff h JOIN related r ON h.run_id=r.run_id ORDER BY h.handoff_id LIMIT %s""",
                (run_id, _MAX_ITEMS + 1))
            handoffs = [dict(row) for row in cursor.fetchall()]
        if tables["followup"]:
            cursor.execute("SELECT previous_run_id,run_id,previous_artifacts_json FROM agent_run_followup "
                           "WHERE previous_run_id=%s OR run_id=%s LIMIT %s", (run_id, run_id, _MAX_ITEMS + 1))
            followups = [dict(row) for row in cursor.fetchall()]
        if tables["workspace"]:
            cursor.execute("SELECT workspace_id,revision,run_id,revision_hash FROM agent_workspace_run WHERE run_id=%s LIMIT %s",
                           (run_id, _MAX_ITEMS + 1))
            workspaces = [dict(row) for row in cursor.fetchall()]
        if any(len(rows) > _MAX_ITEMS for rows in (handoffs, followups, workspaces)):
            raise ValueError("related set exceeds review view limit")
        return {"handoffs": handoffs, "followups": followups, "workspaces": workspaces}

    @staticmethod
    def _impact(cursor, run_id, candidates, reviews, related):
        cursor.execute("SELECT artifact_id,artifact_type,content_hash FROM agent_run_artifact "
                       "WHERE run_id=%s ORDER BY artifact_id LIMIT %s", (run_id, _MAX_ITEMS + 1))
        artifacts = [dict(row) for row in cursor.fetchall()]
        if len(artifacts) > _MAX_ITEMS:
            raise ValueError("artifact set exceeds review view limit")
        return {"runId": run_id, "invalidationScope": "whole-run-evidence-set",
                "candidateIds": [item["candidateId"] for item in candidates],
                "currentReviewIds": [item["review_id"] for item in reviews if item["effective"]],
                "staleReviewIds": [item["review_id"] for item in reviews if item["stale"]],
                "artifacts": artifacts, "related": related,
                "relatedRunMeaning": "potential downstream impact; no downstream approval is inherited or automatically revoked",
                "publicationChanged": False, "truthCertified": False}

    @staticmethod
    def _source_summary(source):
        return {key: deepcopy(source.get(key)) for key in (
            "sourceId", "sourceUri", "artifactId", "artifactHash", "contentHash", "contentKind", "locator", "metadata",
            "retrievedAt", "publishedAt", "observedAt", "verificationStatus", "reviewStatus", "truthVerified")}

    @staticmethod
    def _candidate_view(candidate, reviews, conflicts):
        return {**deepcopy(candidate), "citationChecked": True,
                "humanReviews": [deepcopy(row) for row in reviews if row["subject_type"] == "candidate"
                                 and row["subject_id"] == candidate["candidateId"]],
                "conflictIds": [item["conflictId"] for item in conflicts if candidate["candidateId"] in item["candidateIds"]],
                "sourceIndependence": "unknown", "truthVerified": False, "engineeringCertification": False}

    @staticmethod
    def _conflicts(candidates, reviews):
        conflicts = [{**item, "conflictId": conflict_identity(item)} for item in detect_conflicts(candidates)]
        for conflict in conflicts:
            resolutions = [row["review_id"] for row in reviews if row["effective"]
                           and row["decision"] == "resolve-conflict" and row["conflict_id"] == conflict["conflictId"]]
            conflict.update(humanResolutionReviewIds=resolutions, humanResolutionCurrent=bool(resolutions),
                            truthVerified=False, automaticWinner=None)
        return conflicts

    def overview(self, actor, run_id, *, actor_token):
        with self._view(actor, run_id, actor_token) as (cursor, run, snapshot, identity, secrets):
            sources, candidates = self._material(cursor, run_id)
            reviews = self._reviews(cursor, run_id, snapshot)
            conflicts = self._conflicts(candidates, reviews)
            related = self._related(cursor, run_id)
            result = {"contractVersion": "review-workbench.v1", "run": run, "reviewer": identity,
                "snapshot": snapshot, "sources": [self._source_summary(source) for source in sources],
                "candidates": [self._candidate_view(item, reviews, conflicts) for item in candidates],
                "sourceGroups": source_groups(sources), "independentSourceCount": None,
                "independenceStatus": "unknown", "conflicts": conflicts, "reviews": reviews,
                "impact": self._impact(cursor, run_id, candidates, reviews, related),
                "reviewMeaning": "human disposition of immutable material; no automatic source truth or publication",
                "modelReviewMeaning": "recommendation only; not a human decision", "truthVerified": False}
            return _safe(result, secrets)

    def source(self, actor, run_id, source_id, *, actor_token):
        _id(source_id, "source ID")
        with self._view(actor, run_id, actor_token) as (cursor, run, snapshot, identity, secrets):
            sources, candidates = self._material(cursor, run_id)
            source = next((item for item in sources if item["sourceId"] == source_id), None)
            if source is None:
                raise ValueError("source snapshot is absent or belongs to a different run")
            body = source["markdown"]
            displayed = _masked_text(body, secrets)
            citations = [{"candidateId": item["candidateId"], "quote": item["quote"],
                          "startOffset": item["startOffset"], "endOffset": item["endOffset"],
                          "offsetUnit": "unicode-codepoint", "matchesStoredBody": True}
                         for item in candidates if item["sourceId"] == source_id]
            result = {**self._source_summary(source), "runId": run_id, "text": displayed,
                "textLength": len(body), "offsetUnit": "unicode-codepoint", "hashVerified": True,
                "displayRedacted": displayed != body, "displayContentHash": content_hash(displayed),
                "citationScope": "stored-full-markdown" if source["contentKind"] == "markdown" else "stored-ontology-excerpt-only",
                "originalFullSourceAvailable": source["contentKind"] == "markdown", "citations": citations,
                "snapshot": snapshot, "truthVerified": False}
            return _safe(result, secrets)

    def subject(self, actor, run_id, subject_type, subject_id, *, actor_token):
        if subject_type not in {"candidate", "artifact"}:
            raise ValueError("invalid review subject type")
        _id(subject_id, "subject ID")
        with self._view(actor, run_id, actor_token) as (cursor, run, snapshot, identity, secrets):
            sources, candidates = self._material(cursor, run_id)
            reviews = self._reviews(cursor, run_id, snapshot)
            conflicts = self._conflicts(candidates, reviews)
            if subject_type == "candidate":
                candidate = next((item for item in candidates if item["candidateId"] == subject_id), None)
                if candidate is None:
                    raise ValueError("candidate is absent or belongs to a different run")
                expected = candidate["fingerprint"]
                content = self._candidate_view(candidate, reviews, conflicts)
            else:
                artifact = self._artifact(cursor, run_id, subject_id)
                expected = artifact["content_hash"]
                content = {"artifactId": subject_id, "artifactType": artifact["artifact_type"],
                           "contentHash": expected, "content": deepcopy(artifact["content_json"]),
                           "authority": "stored-artifact; not a human decision", "hashVerified": True}
            self.review_service._subject(cursor, run_id, subject_type, subject_id, expected)
            result = {"runId": run_id, "subjectType": subject_type, "subjectId": subject_id, "expectedHash": expected,
                "subject": content, "snapshot": snapshot,
                "reviews": [row for row in reviews if row["subject_type"] == subject_type and row["subject_id"] == subject_id],
                "impact": self._impact(cursor, run_id, candidates, reviews, self._related(cursor, run_id)),
                "truthVerified": False, "engineeringCertification": False}
            return _safe(result, secrets)
