"""Append-only evidence persistence; review and publication are separate services.

Source bodies must already exist in an immutable artifact owned by the Run.
Candidates refer to that exact artifact hash, text body hash and Unicode offsets.
No caller can assign trusted/approved state through this API. Later review modules
must append decisions keyed by candidate_id + fingerprint, without mutating rows.
"""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy

from psycopg2.extras import Json

from agent_tools.evidence import content_hash, detect_conflicts, normalize_candidate, source_groups
from .phase3_repository import Phase3Repository
from .repository import new_id
from .reasoning_repository import ReasoningRepository


def artifact_json_hash(content):
    # Match PostgresRepository.add_artifact's existing canonicalization exactly.
    return hashlib.sha256(json.dumps(content, sort_keys=True, default=str).encode()).hexdigest()


def artifact_source(artifact, run_id, evidence_index=None):
    """Validate ownership and content before preparing an indexed source view."""
    if not artifact or artifact.get("run_id") != run_id:
        raise ValueError("source artifact belongs to a different run or is absent")
    content = artifact.get("content_json", {})
    if isinstance(content, str):
        content = json.loads(content)
    if not isinstance(content, dict) or artifact_json_hash(content) != artifact.get("content_hash"):
        raise ValueError("source artifact content hash mismatch")
    kind = artifact.get("artifact_type")
    if kind == "web-source-snapshot":
        if evidence_index is not None or content.get("contentKind") != "markdown":
            raise ValueError("web source requires a full markdown snapshot")
        text = content.get("markdown")
        locator = {"path": ["markdown"], "offsetUnit": "unicode-codepoint"}
        metadata = {key: deepcopy(content.get(key)) for key in (
            "sourceUri", "retrievedAt", "publishedAt", "observedAt", "sourceVersion", "provider", "title", "originSourceUri")}
        content_kind = "markdown"
        if not isinstance(text, str) or content.get("contentHash") != content_hash(text):
            raise ValueError("source markdown hash mismatch")
    elif kind == 'document-source-snapshot':
        if evidence_index is not None:
            raise ValueError('document source uses full text offsets')
        text = content.get('text')
        if not isinstance(text, str) or content.get('contentHash') != content_hash(text):
            raise ValueError('document source text hash mismatch')
        locator = {'path': ['text'], 'offsetUnit': 'unicode-codepoint'}
        metadata = {key: deepcopy(content.get(key)) for key in (
            'sourceUri', 'sourceKey', 'sourceVersion', 'fileHash', 'parserVersion', 'retrievedAt', 'segments')}
        content_kind = 'document-text'
    elif kind == "ontology-source-snapshot":
        evidence = content.get("sourceEvidence", [])
        if not isinstance(evidence_index, int) or isinstance(evidence_index, bool) or not 0 <= evidence_index < len(evidence):
            raise ValueError("ontology source requires a valid evidence index")
        item = evidence[evidence_index]
        text = item.get("quote")
        locator = {"path": ["sourceEvidence", evidence_index, "quote"], "evidenceIndex": evidence_index,
                   "offsetUnit": "unicode-codepoint"}
        metadata = {key: deepcopy(item.get(key)) for key in (
            "sourceUri", "sourceRef", "sourceVersion", "retrievedAt", "publishedAt", "observedAt", "recordId", "locator", "provenance")}
        metadata["ontologyRevision"] = deepcopy(content.get("sourceSnapshot"))
        metadata["originalSourceVerification"] = item.get("verificationStatus", "unknown")
        metadata["originalSourceContentHash"] = item.get("contentHash")
        metadata["citationScope"] = "stored-ontology-excerpt; original full source not available"
        content_kind = "ontology-quote"
    else:
        raise ValueError("artifact is not an eligible persisted source snapshot")
    if not isinstance(text, str) or not text or not isinstance(metadata.get("sourceUri"), str) or not metadata["sourceUri"]:
        raise ValueError("source body and source URI are required")
    return {"artifactId": artifact["artifact_id"], "artifactHash": artifact["content_hash"],
            "contentHash": content_hash(text), "contentKind": content_kind, "sourceUri": metadata["sourceUri"],
            "markdown": text, "locator": locator, "metadata": metadata,
            "retrievedAt": metadata.get("retrievedAt"), "publishedAt": metadata.get("publishedAt"),
            "observedAt": metadata.get("observedAt"), "originSourceUri": metadata.get("originSourceUri"),
            "verificationStatus": "unverified", "reviewStatus": "pending", "truthVerified": False}


def constrained_candidate(candidate, source, scope, allowed_entities=None, allowed_metrics=None):
    """Allow explicit server narrowing only; a model cannot widen frozen scope."""
    frozen_entities = scope.get("allowedEntities")
    frozen_metrics = scope.get("allowedMetrics")
    for name, value in (("allowedEntities", frozen_entities), ("allowedMetrics", frozen_metrics)):
        if not isinstance(value, (list, tuple)) or not value or len(value) > 500 or any(not isinstance(x, str) or not x for x in value):
            raise ValueError(f"candidate persistence requires frozen {name}")
    permitted = []
    for supplied, frozen in ((allowed_entities, frozen_entities), (allowed_metrics, frozen_metrics)):
        if supplied is None:
            supplied = frozen
        if not isinstance(supplied, (list, tuple, set, frozenset)) or not supplied or not set(supplied).issubset(frozen):
            raise PermissionError("candidate scope cannot exceed the frozen run scope")
        permitted.append(set(supplied))
    return normalize_candidate(candidate, source, allowed_entities=permitted[0], allowed_metrics=permitted[1],
                               allow_ontology_quote=source["contentKind"] == "ontology-quote")


class EvidenceRepository(ReasoningRepository, Phase3Repository):
    def add_future_evidence_reference(self, run_id, evidence):
        """Persist a Future Battlefield citation against its source artifact.

        The database trigger checks ownership, artifact hash, locator shape and
        the quoted text.  This method is deliberately append-only and returns
        the stable evidence ID used by structured findings.
        """
        required = ('evidenceId', 'sourceArtifactId', 'sourceArtifactHash',
                    'contentHash', 'locator', 'quote', 'sourceUri', 'truthStatus')
        if not isinstance(evidence, dict) or any(not evidence.get(key) for key in required):
            raise ValueError('Future evidence reference is incomplete')
        if evidence.get('truthStatus') not in {'unverified', 'human-reviewed-unverified', 'verified'}:
            raise ValueError('invalid Future evidence truth status')
        with self.connection_factory() as con:
            with con.cursor() as cursor:
                cursor.execute("""INSERT INTO future_battlefield_evidence_ref(
                    evidence_id,run_id,source_artifact_id,source_artifact_hash,content_hash,
                    quote,source_uri,locator_json,truth_status,claim_id,published_at,observed_at,
                    effective_at,origin_source_uri,source_relation,independence_status,start_offset,end_offset)
                    VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT(run_id,evidence_id) DO NOTHING""",
                    (str(evidence['evidenceId']), run_id, evidence['sourceArtifactId'],
                     evidence['sourceArtifactHash'], evidence['contentHash'], evidence['quote'],
                     evidence['sourceUri'], Json(evidence['locator']), evidence['truthStatus'],
                     evidence.get('claimId'), evidence.get('publishedAt'), evidence.get('observedAt'),
                     evidence.get('effectiveAt'), evidence.get('originSourceUri'),
                     evidence.get('sourceRelation', 'unknown'), evidence.get('independenceStatus', 'unknown'),
                     evidence.get('startOffset'), evidence.get('endOffset'))) 
                self.event(cursor, run_id, 'evidence.reference-persisted', None,
                           {'evidenceId': str(evidence['evidenceId']),
                            'sourceArtifactId': evidence['sourceArtifactId'],
                            'sourceArtifactHash': evidence['sourceArtifactHash'],
                            'contentHash': evidence['contentHash']})
        return str(evidence['evidenceId'])

    def list_future_evidence_references(self, run_id):
        with self.connection_factory() as con:
            with con.cursor() as cursor:
                cursor.execute("SELECT * FROM future_battlefield_evidence_ref WHERE run_id=%s ORDER BY created_at,evidence_id", (run_id,))
                return [dict(row) for row in cursor.fetchall()]

    @staticmethod
    def _artifact(cursor, run_id, artifact_id):
        cursor.execute("SELECT * FROM agent_run_artifact WHERE artifact_id=%s", (artifact_id,))
        artifact = cursor.fetchone()
        if not artifact or artifact["run_id"] != run_id:
            raise ValueError("source artifact belongs to a different run or is absent")
        return dict(artifact)

    def register_source_snapshot(self, run_id, artifact_id, evidence_index=None):
        with self.connection_factory() as con:
            with con.cursor() as cursor:
                cursor.execute("SELECT run_id FROM agent_run WHERE run_id=%s FOR UPDATE", (run_id,))
                if not cursor.fetchone():
                    raise ValueError("run not found")
                source = artifact_source(self._artifact(cursor, run_id, artifact_id), run_id, evidence_index)
                cursor.execute("SELECT * FROM agent_source_snapshot WHERE run_id=%s AND artifact_id=%s AND locator_json=%s",
                               (run_id, artifact_id, Json(source["locator"])))
                existing = cursor.fetchone()
                if existing:
                    return self._source(cursor, run_id, existing["source_id"])
                source_id = new_id("source")
                cursor.execute("""INSERT INTO agent_source_snapshot(source_id,run_id,artifact_id,artifact_hash,
                    content_hash,source_uri,content_kind,locator_json,metadata_json)
                    VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (source_id, run_id, artifact_id, source["artifactHash"], source["contentHash"],
                     source["sourceUri"], source["contentKind"], Json(source["locator"]), Json(source["metadata"])))
                self.event(cursor, run_id, "evidence.source-registered", None,
                           {"sourceId": source_id, "artifactId": artifact_id, "contentHash": source["contentHash"],
                            "contentKind": source["contentKind"]})
                return {**source, "sourceId": source_id}

    def _source(self, cursor, run_id, source_id):
        cursor.execute("SELECT * FROM agent_source_snapshot WHERE source_id=%s AND run_id=%s", (source_id, run_id))
        row = cursor.fetchone()
        if not row:
            raise ValueError("source snapshot belongs to a different run or is absent")
        source = artifact_source(self._artifact(cursor, run_id, row["artifact_id"]), run_id,
                                 row["locator_json"].get("evidenceIndex"))
        if (source["artifactHash"] != row["artifact_hash"] or source["contentHash"] != row["content_hash"]
                or source["locator"] != row["locator_json"]):
            raise ValueError("persisted source reference hash mismatch")
        return {**source, "sourceId": source_id}

    def get_source_snapshot(self, run_id, source_id):
        with self.connection_factory() as con:
            with con.cursor() as cursor:
                return self._source(cursor, run_id, source_id)

    def list_source_snapshots(self, run_id):
        with self.connection_factory() as con:
            with con.cursor() as cursor:
                cursor.execute("SELECT source_id FROM agent_source_snapshot WHERE run_id=%s ORDER BY created_at,source_id", (run_id,))
                ids = [row["source_id"] for row in cursor.fetchall()]
                return [self._source(cursor, run_id, source_id) for source_id in ids]

    def store_candidate(self, run_id, source_id, candidate, *, allowed_entities=None, allowed_metrics=None):
        # Only immutable Profile scope authorizes entity and metric selection.
        profile = self.load_profile_snapshot(run_id)
        with self.connection_factory() as con:
            with con.cursor() as cursor:
                cursor.execute("SELECT run_id FROM agent_run WHERE run_id=%s FOR UPDATE", (run_id,))
                if not cursor.fetchone():
                    raise ValueError("run not found")
                source = self._source(cursor, run_id, source_id)
                result = constrained_candidate(candidate, source, profile.scope_policy, allowed_entities, allowed_metrics)
                cursor.execute("SELECT candidate_json FROM agent_evidence_candidate WHERE run_id=%s AND fingerprint=%s",
                               (run_id, result["fingerprint"]))
                previous = cursor.fetchone()
                if previous:
                    return dict(previous["candidate_json"])
                cursor.execute("""INSERT INTO agent_evidence_candidate(candidate_id,run_id,source_id,fingerprint,
                    entity_id,metric,candidate_json,quote,start_offset,end_offset,citation_validated)
                    VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,true)""",
                    (result["candidateId"], run_id, source_id, result["fingerprint"], result["entityId"],
                     result["metric"], Json(result), result["quote"], result["startOffset"], result["endOffset"]))
                self.event(cursor, run_id, "evidence.candidate-created", None,
                           {"candidateId": result["candidateId"], "fingerprint": result["fingerprint"],
                            "sourceId": source_id, "reviewStatus": "pending"})
                return result

    def list_candidates(self, run_id):
        with self.connection_factory() as con:
            with con.cursor() as cursor:
                cursor.execute("SELECT candidate_json FROM agent_evidence_candidate WHERE run_id=%s ORDER BY created_at,candidate_id", (run_id,))
                return [dict(row["candidate_json"]) for row in cursor.fetchall()]

    def evidence_bundle(self, run_id):
        sources, candidates = self.list_source_snapshots(run_id), self.list_candidates(run_id)
        return {"runId": run_id, "sources": sources, "candidates": candidates,
                "futureEvidence": self.list_future_evidence_references(run_id),
                "sourceGroups": source_groups(sources), "conflicts": detect_conflicts(candidates),
                "reviewStatus": "pending", "verificationStatus": "unverified",
                "citationValidationMeaning": "source text location only; no truth certification"}



