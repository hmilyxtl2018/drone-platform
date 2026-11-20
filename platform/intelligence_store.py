"""Persistent store for the intelligence collection and evidence governance demo.

The static ontology snapshot remains read-only.  This module owns a separate
SQLite database containing submitted source documents, extracted claims,
reviews, conflicts, published knowledge versions, and an append-only audit
stream.  It intentionally has no effectors or authorization path: publication
is a human review and knowledge-version operation only.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

try:
    from capability_analysis import validate_capabilities
    from evidence_graph import source_metadata
except ModuleNotFoundError:  # pragma: no cover
    from .capability_analysis import validate_capabilities
    from .evidence_graph import source_metadata


ROOT = Path(__file__).resolve().parent
DEFAULT_INTELLIGENCE_DB = ROOT.parent / "ontology" / "coast-raid-k" / "coast-raid-k-intelligence.sqlite"
ONTOLOGY_NS = "https://local.ontology/coast-raid-k#"
INSTANCE_NS = "https://local.ontology/coast-raid-k/intelligence#"
GRAPH_ENTITY_TYPES = {"Organization", "EquipmentModel", "Technology", "Program"}
GRAPH_EVENT_TYPES = {"TestEvent", "ProcurementEvent", "ProductionEvent"}
_GRAPH_REVISION_UNSPECIFIED = object()
# Extraction links are research proposals. Only these exact endpoint pairs
# have a mapping into the registered ontology vocabulary.
GRAPH_RELATION_TYPES = {
    "manufactures": ({"Organization"}, {"EquipmentModel"}, "manufacturedBy", True),
    "develops": ({"Organization"}, {"EquipmentModel", "Technology", "Program"}, "develops", False),
    "procures": ({"Organization"}, {"EquipmentModel"}, "procures", False),
    "supplies": ({"Organization"}, {"EquipmentModel"}, "supplies", False),
    "testsEquipment": ({"TestEvent"}, {"EquipmentModel"}, "testsEquipment", False),
    "usesTechnology": ({"EquipmentModel", "Program"}, {"Technology"}, "usesTechnology", False),
    "partOfProgram": ({"EquipmentModel", "Technology"}, {"Program"}, "participatesInProgram", False),
    "participatesIn": ({"Organization"}, GRAPH_EVENT_TYPES, "participatesInEventRecord", False),
    "announcedBy": (GRAPH_EVENT_TYPES, {"Organization"}, "announcedBy", False),
    "involvesEquipment": (GRAPH_EVENT_TYPES, {"EquipmentModel"}, "involvesEquipment", False),
    "involvesOrganization": (GRAPH_EVENT_TYPES, {"Organization"}, "involvesOrganization", False),
}


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _json(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _decode(value: Any) -> Any:
    if value is None or isinstance(value, (dict, list, int, float, bool)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return value


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _id(prefix: str) -> str:
    return f"{prefix}-{secrets.token_hex(8)}"


def _revision(value: Any) -> int:
    if type(value) is not int or value < 1:
        raise IntelligenceValidationError("expectedRevision must be a positive integer")
    return value


def _parse_statement(statement: str, fallback: dict[str, Any]) -> tuple[str, str, Any, str | None]:
    """Update structured fields when a reviewer edits the statement text."""
    parts = [part.strip() for part in statement.split("|")]
    if len(parts) >= 3:
        subject, predicate, value = parts[0], parts[1], "|".join(parts[2:]).strip()
        return subject or "unstructured", predicate or "asserts", value, f"{subject.casefold()}|{predicate.casefold()}"
    match = re.match(r"^(.{1,120}?)[：:=]\s*(.{1,500})$", statement)
    if match:
        subject, value = match.group(1).strip(), match.group(2).strip()
        return subject or "unstructured", "value", value, f"{subject.casefold()}|value"
    return str(fallback.get("subject") or "unstructured"), str(fallback.get("predicate") or "asserts"), fallback.get("value") or statement, fallback.get("matchKey")


class IntelligenceStoreError(RuntimeError):
    """Base class for errors safe to expose from the local demo API."""


class IntelligenceNotFound(IntelligenceStoreError):
    pass


class IntelligenceConflict(IntelligenceStoreError):
    pass


class IntelligenceValidationError(IntelligenceStoreError):
    pass


SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS intelligence_document (
  document_id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  source_uri TEXT NOT NULL,
  source_version TEXT NOT NULL,
  body TEXT NOT NULL,
  content_hash TEXT NOT NULL UNIQUE,
  access_basis TEXT NOT NULL CHECK (access_basis IN ('public','authorized')),
  source_reliability TEXT NOT NULL DEFAULT 'unknown',
  status TEXT NOT NULL DEFAULT 'ingested',
  immutable INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS intelligence_document_source_idx
  ON intelligence_document(source_uri, source_version);

CREATE TABLE IF NOT EXISTS intelligence_candidate (
  candidate_id TEXT PRIMARY KEY,
  document_id TEXT NOT NULL REFERENCES intelligence_document(document_id),
  claim_id TEXT NOT NULL,
  revision INTEGER NOT NULL DEFAULT 1,
  original_statement TEXT NOT NULL,
  statement TEXT NOT NULL,
  subject TEXT,
  predicate TEXT,
  value TEXT,
  match_key TEXT,
  status TEXT NOT NULL DEFAULT 'under-review',
  evidence TEXT NOT NULL,
  concepts TEXT NOT NULL DEFAULT '[]',
  uncertainty TEXT NOT NULL DEFAULT '[]',
  recommendation TEXT NOT NULL DEFAULT '{}',
  latest_review_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(document_id, claim_id, revision)
);
CREATE INDEX IF NOT EXISTS intelligence_candidate_match_idx
  ON intelligence_candidate(match_key, status);
CREATE INDEX IF NOT EXISTS intelligence_candidate_doc_idx
  ON intelligence_candidate(document_id, created_at);

CREATE TABLE IF NOT EXISTS intelligence_candidate_revision (
  candidate_id TEXT NOT NULL REFERENCES intelligence_candidate(candidate_id),
  revision INTEGER NOT NULL,
  statement TEXT NOT NULL,
  subject TEXT,
  predicate TEXT,
  value TEXT,
  evidence TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY(candidate_id, revision)
);

CREATE TABLE IF NOT EXISTS intelligence_conflict (
  conflict_id TEXT PRIMARY KEY,
  match_key TEXT NOT NULL,
  candidate_ids TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'open',
  reason TEXT NOT NULL,
  resolution TEXT,
  resolved_by TEXT,
  resolved_at TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS intelligence_review (
  review_id TEXT PRIMARY KEY,
  candidate_id TEXT NOT NULL REFERENCES intelligence_candidate(candidate_id),
  action TEXT NOT NULL CHECK (action IN ('accept','reject','request-more-evidence','edit')),
  reviewer TEXT NOT NULL,
  reason TEXT,
  edited_statement TEXT,
  expected_revision INTEGER,
  resolve_conflict TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS intelligence_review_candidate_idx
  ON intelligence_review(candidate_id, created_at);

CREATE TABLE IF NOT EXISTS intelligence_version (
  version_id TEXT PRIMARY KEY,
  version TEXT NOT NULL UNIQUE,
  content_hash TEXT NOT NULL UNIQUE,
  candidate_ids TEXT NOT NULL,
  snapshot TEXT NOT NULL,
  published_by TEXT NOT NULL,
  title TEXT,
  status TEXT NOT NULL DEFAULT 'published',
  published_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS intelligence_event (
  event_id TEXT PRIMARY KEY,
  seq INTEGER NOT NULL UNIQUE,
  event_type TEXT NOT NULL,
  subject_id TEXT,
  payload TEXT NOT NULL,
  payload_sha256 TEXT NOT NULL,
  actor TEXT,
  occurred_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS intelligence_event_subject_idx
  ON intelligence_event(subject_id, seq);

-- Research assessments are deliberately separate from KnowledgeCandidate and
-- PublishedKnowledgeVersion.  An assessment is a human-reviewable research
-- report; publishing it does not promote claims into the trusted ontology.
CREATE TABLE IF NOT EXISTS intelligence_assessment (
  assessment_id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL,
  title TEXT NOT NULL,
  question TEXT NOT NULL,
  horizon_years INTEGER NOT NULL,
  revision INTEGER NOT NULL DEFAULT 1,
  status TEXT NOT NULL DEFAULT 'under-review',
  provider TEXT NOT NULL,
  model TEXT,
  analysis_type TEXT NOT NULL,
  summary TEXT NOT NULL,
  evidence_refs TEXT NOT NULL,
  source_snapshot TEXT NOT NULL,
  uncertainty TEXT NOT NULL DEFAULT '[]',
  warnings TEXT NOT NULL DEFAULT '[]',
  content_hash TEXT NOT NULL UNIQUE,
  latest_review_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  published_at TEXT,
  published_by TEXT
);
CREATE INDEX IF NOT EXISTS intelligence_assessment_task_idx
  ON intelligence_assessment(task_id, created_at);

CREATE TABLE IF NOT EXISTS intelligence_assessment_review (
  review_id TEXT PRIMARY KEY,
  assessment_id TEXT NOT NULL REFERENCES intelligence_assessment(assessment_id),
  action TEXT NOT NULL CHECK (action IN ('accept','reject','request-more-evidence')),
  reviewer TEXT NOT NULL,
  reason TEXT NOT NULL,
  expected_revision INTEGER NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS intelligence_assessment_review_idx
  ON intelligence_assessment_review(assessment_id, created_at);

-- Validation rounds are immutable verifier observations.  They are kept
-- separate from human assessment reviews so a model or deterministic check
-- can never masquerade as an operator decision.
CREATE TABLE IF NOT EXISTS intelligence_validation_round (
  validation_round_id TEXT PRIMARY KEY,
  assessment_id TEXT NOT NULL REFERENCES intelligence_assessment(assessment_id),
  round_no INTEGER NOT NULL,
  kind TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('passed','needs-evidence','failed')),
  checks TEXT NOT NULL DEFAULT '[]',
  confidence_factors TEXT NOT NULL DEFAULT '{}',
  issues TEXT NOT NULL DEFAULT '[]',
  source_snapshot_hash TEXT NOT NULL,
  provider TEXT NOT NULL,
  model TEXT,
  actor TEXT,
  created_at TEXT NOT NULL,
  UNIQUE(assessment_id, round_no)
);
CREATE INDEX IF NOT EXISTS intelligence_validation_round_idx
  ON intelligence_validation_round(assessment_id, round_no);

-- Graph revisions are immutable snapshots.  Storing the complete node/edge
-- projection in one row keeps the demo transactional while retaining a
-- stable revision boundary for the UI and later graph database adapters.
CREATE TABLE IF NOT EXISTS intelligence_graph_revision (
  graph_revision_id TEXT PRIMARY KEY,
  assessment_id TEXT NOT NULL REFERENCES intelligence_assessment(assessment_id),
  revision INTEGER NOT NULL,
  nodes TEXT NOT NULL DEFAULT '[]',
  edges TEXT NOT NULL DEFAULT '[]',
  content_hash TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL,
  UNIQUE(assessment_id, revision)
);
CREATE INDEX IF NOT EXISTS intelligence_graph_revision_idx
  ON intelligence_graph_revision(assessment_id, revision);

-- An OntologyAdmission is a proposed atomic assertion.  It has a dedicated
-- human review and publication gate; publishing an assessment never creates
-- one implicitly.
CREATE TABLE IF NOT EXISTS intelligence_ontology_admission (
  admission_id TEXT PRIMARY KEY,
  assessment_id TEXT NOT NULL REFERENCES intelligence_assessment(assessment_id),
  metric_id TEXT NOT NULL,
  revision INTEGER NOT NULL DEFAULT 1,
  status TEXT NOT NULL DEFAULT 'under-review' CHECK (status IN ('under-review','accepted','rejected','needs-evidence','published','withdrawn')),
  candidate_iri TEXT NOT NULL,
  ontology_class TEXT NOT NULL,
  mapping TEXT NOT NULL,
  assertion TEXT NOT NULL,
  source_snapshot_hash TEXT NOT NULL,
  content_hash TEXT NOT NULL UNIQUE,
  latest_review_id TEXT,
  publication TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(assessment_id, metric_id)
);
CREATE INDEX IF NOT EXISTS intelligence_ontology_admission_idx
  ON intelligence_ontology_admission(assessment_id, status);

CREATE TABLE IF NOT EXISTS intelligence_ontology_admission_review (
  review_id TEXT PRIMARY KEY,
  admission_id TEXT NOT NULL REFERENCES intelligence_ontology_admission(admission_id),
  action TEXT NOT NULL CHECK (action IN ('accept','reject','request-more-evidence')),
  reviewer TEXT NOT NULL,
  reason TEXT NOT NULL,
  expected_revision INTEGER NOT NULL,
  verified_confidence TEXT CHECK (verified_confidence IN ('low','medium','high')),
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS intelligence_ontology_admission_review_idx
  ON intelligence_ontology_admission_review(admission_id, created_at);

-- Immutable, repeatable audits of the published ontology projection.  An
-- audit is a read-only governance record: it never promotes or withdraws a
-- fact.  Keeping the result in the same append-only event stream makes the
-- periodic audit visible when an operator reads the publication history.
CREATE TABLE IF NOT EXISTS intelligence_ontology_audit (
  audit_id TEXT PRIMARY KEY,
  assessment_id TEXT,
  scope TEXT NOT NULL DEFAULT 'published-ontology',
  status TEXT NOT NULL CHECK (status IN ('passed','failed','needs-review')),
  checks TEXT NOT NULL DEFAULT '[]',
  summary TEXT NOT NULL DEFAULT '{}',
  content_hash TEXT NOT NULL UNIQUE,
  actor TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS intelligence_ontology_audit_idx
  ON intelligence_ontology_audit(assessment_id, created_at);

-- A correction creates another immutable assertion; no fact row is rewritten.
CREATE TABLE IF NOT EXISTS intelligence_ontology_attribute_amendment (
  previous_admission_id TEXT PRIMARY KEY REFERENCES intelligence_ontology_admission(admission_id),
  replacement_admission_id TEXT NOT NULL REFERENCES intelligence_ontology_admission(admission_id),
  reviewer TEXT NOT NULL, reason TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS intelligence_attribute_amendment_no_update
BEFORE UPDATE ON intelligence_ontology_attribute_amendment BEGIN
  SELECT RAISE(ABORT, 'property amendments are immutable');
END;
CREATE TRIGGER IF NOT EXISTS intelligence_attribute_amendment_no_delete
BEFORE DELETE ON intelligence_ontology_attribute_amendment BEGIN
  SELECT RAISE(ABORT, 'property amendments are retained');
END;

CREATE TRIGGER IF NOT EXISTS intelligence_document_no_update
BEFORE UPDATE ON intelligence_document BEGIN
  SELECT RAISE(ABORT, 'source documents are immutable');
END;
CREATE TRIGGER IF NOT EXISTS intelligence_document_no_delete
BEFORE DELETE ON intelligence_document BEGIN
  SELECT RAISE(ABORT, 'source documents are immutable');
END;
CREATE TRIGGER IF NOT EXISTS intelligence_event_no_update
BEFORE UPDATE ON intelligence_event BEGIN
  SELECT RAISE(ABORT, 'intelligence events are append-only');
END;
CREATE TRIGGER IF NOT EXISTS intelligence_event_no_delete
BEFORE DELETE ON intelligence_event BEGIN
  SELECT RAISE(ABORT, 'intelligence events are append-only');
END;
CREATE TRIGGER IF NOT EXISTS intelligence_review_no_update
BEFORE UPDATE ON intelligence_review BEGIN
  SELECT RAISE(ABORT, 'ingestion reviews are append-only');
END;
CREATE TRIGGER IF NOT EXISTS intelligence_review_no_delete
BEFORE DELETE ON intelligence_review BEGIN
  SELECT RAISE(ABORT, 'ingestion reviews are append-only');
END;
CREATE TRIGGER IF NOT EXISTS intelligence_revision_no_update
BEFORE UPDATE ON intelligence_candidate_revision BEGIN
  SELECT RAISE(ABORT, 'candidate revisions are append-only');
END;
CREATE TRIGGER IF NOT EXISTS intelligence_revision_no_delete
BEFORE DELETE ON intelligence_candidate_revision BEGIN
  SELECT RAISE(ABORT, 'candidate revisions are append-only');
END;
CREATE TRIGGER IF NOT EXISTS intelligence_version_no_update
BEFORE UPDATE ON intelligence_version BEGIN
  SELECT RAISE(ABORT, 'published knowledge versions are immutable');
END;
CREATE TRIGGER IF NOT EXISTS intelligence_version_no_delete
BEFORE DELETE ON intelligence_version BEGIN
  SELECT RAISE(ABORT, 'published knowledge versions are immutable');
END;
CREATE TRIGGER IF NOT EXISTS intelligence_assessment_published_no_update
BEFORE UPDATE ON intelligence_assessment WHEN OLD.status='published' BEGIN
  SELECT RAISE(ABORT, 'published assessments are immutable');
END;
CREATE TRIGGER IF NOT EXISTS intelligence_assessment_no_delete
BEFORE DELETE ON intelligence_assessment BEGIN
  SELECT RAISE(ABORT, 'assessments are retained');
END;
CREATE TRIGGER IF NOT EXISTS intelligence_assessment_review_no_update
BEFORE UPDATE ON intelligence_assessment_review BEGIN
  SELECT RAISE(ABORT, 'assessment reviews are immutable');
END;
CREATE TRIGGER IF NOT EXISTS intelligence_assessment_review_no_delete
BEFORE DELETE ON intelligence_assessment_review BEGIN
  SELECT RAISE(ABORT, 'assessment reviews are retained');
END;
CREATE TRIGGER IF NOT EXISTS intelligence_validation_round_no_update
BEFORE UPDATE ON intelligence_validation_round BEGIN
  SELECT RAISE(ABORT, 'validation rounds are immutable');
END;
CREATE TRIGGER IF NOT EXISTS intelligence_validation_round_no_delete
BEFORE DELETE ON intelligence_validation_round BEGIN
  SELECT RAISE(ABORT, 'validation rounds are retained');
END;
CREATE TRIGGER IF NOT EXISTS intelligence_graph_revision_no_update
BEFORE UPDATE ON intelligence_graph_revision BEGIN
  SELECT RAISE(ABORT, 'graph revisions are immutable');
END;
CREATE TRIGGER IF NOT EXISTS intelligence_graph_revision_no_delete
BEFORE DELETE ON intelligence_graph_revision BEGIN
  SELECT RAISE(ABORT, 'graph revisions are retained');
END;
CREATE TRIGGER IF NOT EXISTS intelligence_ontology_admission_review_no_update
BEFORE UPDATE ON intelligence_ontology_admission_review BEGIN
  SELECT RAISE(ABORT, 'ontology admission reviews are immutable');
END;
CREATE TRIGGER IF NOT EXISTS intelligence_ontology_admission_review_no_delete
BEFORE DELETE ON intelligence_ontology_admission_review BEGIN
  SELECT RAISE(ABORT, 'ontology admission reviews are retained');
END;
CREATE TRIGGER IF NOT EXISTS intelligence_ontology_audit_no_update
BEFORE UPDATE ON intelligence_ontology_audit BEGIN
  SELECT RAISE(ABORT, 'ontology audits are immutable');
END;
CREATE TRIGGER IF NOT EXISTS intelligence_ontology_audit_no_delete
BEFORE DELETE ON intelligence_ontology_audit BEGIN
  SELECT RAISE(ABORT, 'ontology audits are retained');
END;
CREATE TRIGGER IF NOT EXISTS intelligence_ontology_admission_assertion_no_update
BEFORE UPDATE ON intelligence_ontology_admission
WHEN OLD.assessment_id IS NOT NEW.assessment_id OR OLD.metric_id IS NOT NEW.metric_id
  OR OLD.candidate_iri IS NOT NEW.candidate_iri OR OLD.ontology_class IS NOT NEW.ontology_class
  OR OLD.mapping IS NOT NEW.mapping OR OLD.assertion IS NOT NEW.assertion
  OR OLD.source_snapshot_hash IS NOT NEW.source_snapshot_hash OR OLD.content_hash IS NOT NEW.content_hash
BEGIN
  SELECT RAISE(ABORT, 'ontology admission assertion is immutable');
END;
CREATE TRIGGER IF NOT EXISTS intelligence_ontology_admission_published_no_update
BEFORE UPDATE ON intelligence_ontology_admission WHEN OLD.status='published' BEGIN
  SELECT RAISE(ABORT, 'published ontology admissions are immutable');
END;
CREATE TRIGGER IF NOT EXISTS intelligence_ontology_admission_no_delete
BEFORE DELETE ON intelligence_ontology_admission BEGIN
  SELECT RAISE(ABORT, 'ontology admissions are retained');
END;
"""


class _Connection(sqlite3.Connection):
    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    value = dict(row)
    for key in ("evidence", "concepts", "uncertainty", "recommendation", "candidate_ids", "snapshot", "payload", "summary", "evidence_refs", "source_snapshot", "warnings", "checks", "confidence_factors", "issues", "nodes", "edges", "mapping", "assertion", "publication"):
        if key in value:
            value[key] = _decode(value[key])
    mapping = {
        "document_id": "documentId", "source_uri": "sourceUri", "source_version": "sourceVersion",
        "content_hash": "contentHash", "access_basis": "accessBasis", "source_reliability": "sourceReliability",
        "created_at": "createdAt", "candidate_id": "candidateId", "claim_id": "claimId",
        "original_statement": "originalStatement", "match_key": "matchKey", "latest_review_id": "latestReviewId",
        "updated_at": "updatedAt", "conflict_id": "conflictId", "resolved_by": "resolvedBy",
        "resolved_at": "resolvedAt", "review_id": "reviewId", "edited_statement": "editedStatement",
        "expected_revision": "expectedRevision", "resolve_conflict": "resolveConflict", "version_id": "versionId",
        "content_hash": "contentHash", "candidate_ids": "candidateIds", "published_by": "publishedBy",
        "published_at": "publishedAt", "event_id": "eventId", "event_type": "eventType",
        "subject_id": "subjectId", "payload_sha256": "payloadSha256", "occurred_at": "occurredAt",
        "assessment_id": "assessmentId", "task_id": "taskId", "horizon_years": "horizonYears",
        "analysis_type": "analysisType", "evidence_refs": "evidenceRefs", "source_snapshot": "sourceSnapshot",
        "content_hash": "contentHash", "latest_review_id": "latestReviewId", "published_at": "publishedAt",
        "published_by": "publishedBy", "review_id": "reviewId", "expected_revision": "expectedRevision",
        "validation_round_id": "validationRoundId", "round_no": "roundNo", "confidence_factors": "confidenceFactors",
        "source_snapshot_hash": "sourceSnapshotHash", "graph_revision_id": "graphRevisionId", "candidate_iri": "candidateIri",
        "ontology_class": "ontologyClass", "admission_id": "admissionId", "metric_id": "metricId", "latest_review_id": "latestReviewId",
        "checks": "checks", "issues": "issues", "nodes": "nodes", "edges": "edges", "created_at": "createdAt", "updated_at": "updatedAt",
        "audit_id": "auditId", "scope": "scope", "status": "status", "actor": "actor",
        "run_no": "runNo", "trigger": "trigger", "assessment_revision": "assessmentRevision", "completed_at": "completedAt",
        "run_no": "runNo", "trigger": "trigger", "assessment_revision": "assessmentRevision",
        "completed_at": "completedAt", "finding_id": "findingId", "audit_review_id": "auditReviewId",
        "target_type": "targetType", "target_id": "targetId", "code": "code", "severity": "severity",
        "state": "state", "message": "message", "latest_review_id": "latestReviewId",
        "revision": "revision",
    }
    for source, target in mapping.items():
        if source in value:
            value[target] = value.pop(source)
    # Prefer the object's own identity over foreign keys such as a candidate's
    # documentId or a review's candidateId.
    id_key = next((k for k in ("eventId", "versionId", "reviewId", "auditReviewId", "validationRoundId", "graphRevisionId", "admissionId", "findingId", "auditId", "assessmentId", "conflictId", "candidateId", "documentId") if k in value), None)
    if id_key:
        # ``id`` is the canonical contract field.  Domain aliases remain for
        # ergonomic workbench lookups and backwards compatibility.
        value["id"] = value[id_key]
    value["type"] = {
        "documentId": "SourceDocument", "candidateId": "KnowledgeCandidate", "reviewId": "IngestionReview",
        "conflictId": "ConflictSet", "versionId": "PublishedKnowledgeVersion", "eventId": "IntelligenceAuditEvent",
        "assessmentId": "IntelligenceAssessment",
        "validationRoundId": "ValidationRound", "graphRevisionId": "EvidenceGraphRevision", "admissionId": "OntologyAdmission", "auditId": "OntologyAudit", "findingId": "AuditFinding", "auditReviewId": "AuditFindingReview",
    }.get(id_key or "", value.get("type"))
    if id_key == "reviewId" and "assessmentId" in value:
        value["type"] = "AssessmentReview"
    if "immutable" in value:
        value["immutable"] = bool(value["immutable"])
    if "contentHash" in value and id_key == "versionId":
        # The contract calls this field ``hash`` on a published version while
        # documents and evidence use the more explicit contentHash spelling.
        value.setdefault("hash", value["contentHash"])
    # Nullable fields are explicit in the intelligence contract only where
    # they represent an intentional absence.  Omit SQL NULLs for optional
    # string/date properties whose JSON schemas do not permit null.
    nullable = {"latestReviewId", "resolution", "editedStatement", "resolveConflict", "model", "publishedAt", "publishedBy", "actor", "publication"}
    for key in list(value):
        if value[key] is None and key not in nullable:
            value.pop(key)
    if id_key == "admissionId":
        admission_mapping = value.get("mapping") or {}
        value["kind"] = admission_mapping.get("kind", "capability-metric")
        if value["kind"] in {"graph-node", "graph-relation", "graph-property"}:
            for key in ("graphRevisionId", "graphElementId", "proposedConfidence"):
                value[key] = admission_mapping[key]
            value["staleGraph"] = False
    return value


class IntelligenceStore:
    """SQLite implementation for source, claim, review and publication state."""

    def __init__(self, path: str | Path | None = None):
        raw = path or os.environ.get("CRK_INTELLIGENCE_SQLITE_PATH") or DEFAULT_INTELLIGENCE_DB
        self.path = Path(raw).expanduser()
        if not self.path.is_absolute():
            self.path = (ROOT.parent / self.path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(str(self.path), timeout=20, isolation_level=None, factory=_Connection)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys = ON")
        con.execute("PRAGMA busy_timeout = 20000")
        return con

    def _initialize(self) -> None:
        with self._connect() as con:
            con.executescript(SCHEMA)
            # Versioned structured-equipment migrations are kept outside the
            # legacy intelligence schema.  The migration is additive and
            # idempotent, so existing assessments, graph snapshots and
            # admissions remain readable and immutable.
            # The version table is created by the first migration itself. Create
            # it before checking versions so a brand-new database can be
            # initialized safely. Each migration is additive and idempotent by
            # version, while immutable legacy intelligence tables stay intact.
            con.execute(
                "CREATE TABLE IF NOT EXISTS schema_migration ("
                "version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
            )
            migrations = (
                ("001_equipment_ontology", ROOT / "migrations" / "001_equipment_ontology.sql"),
                ("002_equipment_entity_context", ROOT / "migrations" / "002_equipment_entity_context.sql"),
                ("003_equipment_verification_queue", ROOT / "migrations" / "003_equipment_verification_queue.sql"),
                ("004_equipment_workbook_sources", ROOT / "migrations" / "004_equipment_workbook_sources.sql"),
                ("005_equipment_lineage", ROOT / "migrations" / "005_equipment_lineage.sql"),
                ("006_ontology_attribute_candidates", ROOT / "migrations" / "006_ontology_attribute_candidates.sql"),
                ("007_equipment_capability_profiles", ROOT / "migrations" / "007_equipment_capability_profiles.sql"),
                ("008_capability_collection", ROOT / "migrations" / "008_capability_collection.sql"),
                ("009_capability_conflict_resolution", ROOT / "migrations" / "009_capability_conflict_resolution.sql"),
                ("010_periodic_audit_review", ROOT / "migrations" / "010_periodic_audit_review.sql"),
                ("011_entity_enrichment_batches", ROOT / "migrations" / "011_entity_enrichment_batches.sql"),
                ("012_relation_candidates", ROOT / "migrations" / "012_relation_candidates.sql"),
                ("013_relation_publication_audit", ROOT / "migrations" / "013_relation_publication_audit.sql"),
                ("014_enrichment_response_budget", ROOT / "migrations" / "014_enrichment_response_budget.sql"),
                ("015_entity_audit_runs", ROOT / "migrations" / "015_entity_audit_runs.sql"),
                ("016_audit_publication_binding", ROOT / "migrations" / "016_audit_publication_binding.sql"),
                ("017_verification_recovery", ROOT / "migrations" / "017_verification_recovery.sql"),
                ("018_signal_evidence_chain", ROOT / "migrations" / "018_signal_evidence_chain.sql"),
                ("019_agent_orchestration", ROOT / "migrations" / "019_agent_orchestration.sql"),
                ("020_agent_artifact_immutability", ROOT / "migrations" / "020_agent_artifact_immutability.sql"),
                ("021_conversation_sessions", ROOT / "migrations" / "021_conversation_sessions.sql"),
                ("022_conversation_display_cursor", ROOT / "migrations" / "022_conversation_display_cursor.sql"),
                 ("023_agent_task_leases", ROOT / "migrations" / "023_agent_task_leases.sql"),
                ("024_agent_tool_registry", ROOT / "migrations" / "024_agent_tool_registry.sql"),
                ("025_manufacturing_cost_records", ROOT / "migrations" / "025_manufacturing_cost_records.sql"),
                ("026_manufacturing_candidates", ROOT / "migrations" / "026_manufacturing_candidates.sql"),
                ("027_manufacturing_domain_records", ROOT / "migrations" / "027_manufacturing_domain_records.sql"),
                ("028_manufacturing_candidate_categories", ROOT / "migrations" / "028_manufacturing_candidate_categories.sql"),
                ("029_evidence_factor_graph", ROOT / "migrations" / "029_evidence_factor_graph.sql"),
            )
            for migration_version, migration_path in migrations:
                if not migration_path.exists():
                    continue
                applied = con.execute(
                    "SELECT 1 FROM schema_migration WHERE version=?",
                    (migration_version,),
                ).fetchone()
                if applied is None:
                    con.executescript(migration_path.read_text(encoding="utf-8"))
                    con.execute(
                        "INSERT OR IGNORE INTO schema_migration(version, applied_at) VALUES(?,?)",
                        (migration_version, _now()),
                    )
            # Additive migration preserves existing capability admissions and
            # immutable review records when opening an older demo database.
            columns = {row[1] for row in con.execute("PRAGMA table_info(intelligence_ontology_admission_review)")}
            if "verified_confidence" not in columns:
                con.execute("ALTER TABLE intelligence_ontology_admission_review ADD COLUMN verified_confidence TEXT CHECK (verified_confidence IN ('low','medium','high'))")

    @staticmethod
    def _document(row: sqlite3.Row | None) -> dict[str, Any] | None:
        return _row(row)

    def _event_tx(self, con: sqlite3.Connection, event_type: str, subject_id: str | None, payload: dict[str, Any], actor: str | None = None) -> dict[str, Any]:
        seq = int(con.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM intelligence_event").fetchone()[0])
        event_id = _id("intel-event")
        occurred = _now()
        encoded = _json(payload)
        digest = _sha(encoded)
        con.execute("INSERT INTO intelligence_event(event_id,seq,event_type,subject_id,payload,payload_sha256,actor,occurred_at) VALUES(?,?,?,?,?,?,?,?)", (event_id, seq, event_type, subject_id, encoded, digest, actor, occurred))
        return {"eventId": event_id, "seq": seq, "eventType": event_type, "subjectId": subject_id, "payload": payload, "payloadSha256": digest, "actor": actor, "occurredAt": occurred}

    @staticmethod
    def validate_document(payload: dict[str, Any]) -> None:
        """Run before model calls as well as writes: invalid input stays local."""
        if not isinstance(payload, dict):
            raise IntelligenceValidationError("document payload must be an object")
        title = str(payload.get("title") or "").strip()
        source_uri = str(payload.get("sourceUri") or "").strip()
        source_version = str(payload.get("sourceVersion") or "").strip()
        body = payload.get("body")
        if not title or not source_uri or not source_version or not isinstance(body, str) or not body.strip():
            raise IntelligenceValidationError("title, sourceUri, sourceVersion and non-empty body are required")
        access_basis = str(payload.get("accessBasis") or "")
        if access_basis not in {"public", "authorized"}:
            raise IntelligenceValidationError("accessBasis is required and must be public or authorized")
        reliability = str(payload.get("sourceReliability") or "unknown")
        if reliability not in {"unknown", "primary", "secondary"}:
            raise IntelligenceValidationError("sourceReliability must be unknown, primary or secondary")

    def submit_document(self, payload: dict[str, Any], *, extracted_candidates: Iterable[dict[str, Any]] = ()) -> dict[str, Any]:
        self.validate_document(payload)
        title = str(payload["title"]).strip()
        source_uri = str(payload["sourceUri"]).strip()
        source_version = str(payload["sourceVersion"]).strip()
        body = payload["body"]
        access_basis = str(payload["accessBasis"])
        reliability = str(payload.get("sourceReliability") or "unknown")
        content_hash = _sha(body)
        with self._lock, self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            duplicate = con.execute("SELECT * FROM intelligence_document WHERE content_hash = ?", (content_hash,)).fetchone()
            if duplicate:
                self._event_tx(con, "SourceDocumentDeduplicated", duplicate["document_id"], {"entityId": duplicate["document_id"], "entityType": "SourceDocument", "status": duplicate["status"], "contentHashOrClaimHash": content_hash, "contentHash": content_hash, "sourceUri": source_uri, "sourceVersion": source_version}, payload.get("submittedBy"))
                result = {"document": self._document(duplicate), "candidates": [_row(row) for row in con.execute("SELECT * FROM intelligence_candidate WHERE document_id = ? ORDER BY created_at, candidate_id", (duplicate["document_id"],))], "duplicate": True}
                con.commit()
                return result
            document_id = str(payload.get("documentId") or _id("source"))
            created = _now()
            try:
                con.execute("INSERT INTO intelligence_document(document_id,title,source_uri,source_version,body,content_hash,access_basis,source_reliability,created_at) VALUES(?,?,?,?,?,?,?,?,?)", (document_id, title, source_uri, source_version, body, content_hash, access_basis, reliability, created))
            except sqlite3.IntegrityError as exc:
                raise IntelligenceConflict(f"document already exists: {document_id}") from exc
            self._event_tx(con, "SourceDocumentIngested", document_id, {"entityId": document_id, "entityType": "SourceDocument", "status": "ingested", "contentHashOrClaimHash": content_hash, "documentId": document_id, "contentHash": content_hash, "sourceUri": source_uri, "sourceVersion": source_version}, payload.get("submittedBy"))
            candidate_rows = []
            for item in extracted_candidates:
                candidate_rows.append(self._insert_candidate_tx(con, document_id, item, source_uri, source_version, body, content_hash))
            result = {"document": self._document(con.execute("SELECT * FROM intelligence_document WHERE document_id = ?", (document_id,)).fetchone()), "candidates": candidate_rows, "duplicate": False}
            con.commit()
            return result

    def _insert_candidate_tx(self, con: sqlite3.Connection, document_id: str, item: dict[str, Any], source_uri: str, source_version: str, body: str, content_hash: str) -> dict[str, Any]:
        statement = str(item.get("statement") or "").strip()
        if not statement:
            raise IntelligenceValidationError("candidate statement cannot be empty")
        line = max(1, int(item.get("lineStart") or 1))
        lines = body.splitlines() or [body]
        quote = str(item.get("quote") or (lines[line - 1] if line <= len(lines) else statement))
        evidence = item.get("evidence") or {"sourceUri": source_uri, "sourceVersion": source_version, "documentId": document_id, "lineStart": line, "lineEnd": max(line, int(item.get("lineEnd") or line)), "quote": quote, "contentHash": content_hash}
        candidate_id = str(item.get("candidateId") or _id("candidate"))
        claim_id = str(item.get("claimId") or f"{document_id}:claim:{line}")
        created = _now()
        subject = str(item.get("subject") or "unstructured")
        predicate = str(item.get("predicate") or "asserts")
        value = item.get("value") if item.get("value") is not None else statement
        uncertainty = item.get("uncertainty") or {}
        if isinstance(uncertainty, list):
            uncertainty = {"flags": uncertainty}
        con.execute("INSERT INTO intelligence_candidate(candidate_id,document_id,claim_id,revision,original_statement,statement,subject,predicate,value,match_key,status,evidence,concepts,uncertainty,recommendation,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (candidate_id, document_id, claim_id, int(item.get("revision") or 1), str(item.get("originalStatement") or statement), statement, subject, predicate, value, item.get("matchKey"), str(item.get("status") or "under-review"), _json(evidence), _json(item.get("concepts") or []), _json(uncertainty), _json(item.get("recommendation") or {}), created, created))
        con.execute("INSERT INTO intelligence_candidate_revision(candidate_id,revision,statement,subject,predicate,value,evidence,created_at) VALUES(?,?,?,?,?,?,?,?)", (candidate_id, int(item.get("revision") or 1), statement, subject, predicate, value, _json(evidence), created))
        candidate = _row(con.execute("SELECT * FROM intelligence_candidate WHERE candidate_id = ?", (candidate_id,)).fetchone())
        self._event_tx(con, "KnowledgeCandidateCreated", candidate_id, {"entityId": candidate_id, "entityType": "KnowledgeCandidate", "status": "under-review", "contentHashOrClaimHash": _sha(statement), "candidateId": candidate_id, "documentId": document_id, "claimId": claim_id, "statement": statement}, "intelligence-agent")
        return candidate  # type: ignore[return-value]

    def get_document(self, document_id: str) -> dict[str, Any] | None:
        with self._connect() as con:
            return self._document(con.execute("SELECT * FROM intelligence_document WHERE document_id = ?", (str(document_id),)).fetchone())

    def document_for_body(self, body: str) -> dict[str, Any] | None:
        """Avoid paid model extraction for an already stored immutable body."""
        with self._connect() as con:
            return self._document(con.execute("SELECT * FROM intelligence_document WHERE content_hash = ?", (_sha(body),)).fetchone())

    def list_documents(self) -> list[dict[str, Any]]:
        with self._connect() as con:
            return [self._document(row) for row in con.execute("SELECT * FROM intelligence_document ORDER BY created_at DESC")]

    def get_candidate(self, candidate_id: str) -> dict[str, Any] | None:
        with self._connect() as con:
            return _row(con.execute("SELECT * FROM intelligence_candidate WHERE candidate_id = ?", (str(candidate_id),)).fetchone())

    def list_candidates(self, document_id: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM intelligence_candidate"
        args: list[Any] = []
        if document_id:
            query += " WHERE document_id = ?"
            args.append(str(document_id))
        query += " ORDER BY created_at, candidate_id"
        with self._connect() as con:
            return [_row(row) for row in con.execute(query, args)]

    def list_conflicts(self) -> list[dict[str, Any]]:
        with self._connect() as con:
            return [_row(row) for row in con.execute("SELECT * FROM intelligence_conflict ORDER BY created_at")]

    def list_reviews(self, candidate_id: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM intelligence_review"
        args: list[Any] = []
        if candidate_id:
            query += " WHERE candidate_id = ?"
            args.append(str(candidate_id))
        query += " ORDER BY created_at"
        with self._connect() as con:
            return [_row(row) for row in con.execute(query, args)]

    def list_candidate_revisions(self, candidate_id: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM intelligence_candidate_revision"
        args: list[Any] = []
        if candidate_id:
            query += " WHERE candidate_id = ?"
            args.append(str(candidate_id))
        query += " ORDER BY candidate_id, revision"
        with self._connect() as con:
            rows = con.execute(query, args).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["candidateId"] = item.pop("candidate_id")
            item["revision"] = int(item["revision"])
            item["evidence"] = _decode(item["evidence"])
            item["createdAt"] = item.pop("created_at")
            item["id"] = f"{item['candidateId']}@r{item['revision']}"
            item["type"] = "KnowledgeCandidateRevision"
            result.append(item)
        return result

    def list_versions(self) -> list[dict[str, Any]]:
        with self._connect() as con:
            return [_row(row) for row in con.execute("SELECT * FROM intelligence_version ORDER BY published_at DESC")]

    def list_events(self) -> list[dict[str, Any]]:
        with self._connect() as con:
            return [_row(row) for row in con.execute("SELECT * FROM intelligence_event ORDER BY seq")]

    # ---- Research assessment projections ---------------------------------
    # Assessments are intentionally separate from knowledge candidates.  They
    # preserve the immutable source snapshot used for an analyst's conclusion
    # and have their own review/publication gate.
    def create_assessment(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise IntelligenceValidationError("assessment payload must be an object")
        task_id = str(payload.get("taskId") or "").strip()
        title = str(payload.get("title") or "").strip()
        question = str(payload.get("question") or "").strip()
        if not task_id or not title or not question:
            raise IntelligenceValidationError("taskId, title and question are required")
        try:
            horizon = int(payload.get("horizonYears", 5))
        except (TypeError, ValueError) as exc:
            raise IntelligenceValidationError("horizonYears must be an integer") from exc
        if horizon < 1 or horizon > 30:
            raise IntelligenceValidationError("horizonYears must be between 1 and 30")
        source_snapshot = payload.get("sourceSnapshot")
        if not isinstance(source_snapshot, list) or not source_snapshot:
            raise IntelligenceValidationError("sourceSnapshot must contain at least one immutable source")
        # The source snapshot is supplied by OSINTTaskStore after it looked up
        # the task's immutable items; require identity and hash fields here as
        # a second guard against model/client supplied citations.
        for source in source_snapshot:
            if not isinstance(source, dict) or not all(str(source.get(k) or "").strip() for k in ("itemId", "sourceUri", "contentHash")):
                raise IntelligenceValidationError("each sourceSnapshot item requires itemId, sourceUri and contentHash")
        summary = payload.get("summary")
        if not isinstance(summary, dict):
            raise IntelligenceValidationError("summary must be an object")
        if "capabilityAssessment" in summary:
            try:
                validate_capabilities(summary["capabilityAssessment"], source_snapshot)
            except ValueError as exc:
                raise IntelligenceValidationError(str(exc)) from exc
        evidence_refs = payload.get("evidenceRefs")
        if not isinstance(evidence_refs, list) or not evidence_refs:
            raise IntelligenceValidationError("evidenceRefs must be a non-empty array")
        source_by_id = {source["itemId"]: source for source in source_snapshot}
        if len(source_by_id) != len(source_snapshot):
            raise IntelligenceValidationError("sourceSnapshot item IDs must be unique")
        findings = summary.get("findings")
        if not isinstance(findings, list):
            raise IntelligenceValidationError("summary findings must be an array")
        finding_ids = set()
        evidence_ids = {e.get("itemId") for e in evidence_refs if isinstance(e, dict)}
        for finding in findings:
            if not isinstance(finding, dict) or not isinstance(finding.get("id"), str) or not finding["id"].strip() or finding["id"] in finding_ids:
                raise IntelligenceValidationError("finding IDs must be non-empty and unique")
            finding_ids.add(finding["id"])
            refs = finding.get("evidenceIds")
            if (not isinstance(refs, list) or not refs or any(not isinstance(ref, str) for ref in refs)
                    or len(refs) != len(set(refs)) or not set(refs).issubset(source_by_id)
                    or not set(refs).issubset(evidence_ids)):
                raise IntelligenceValidationError("finding evidence IDs must refer to cited sourceSnapshot items")
        for evidence in evidence_refs:
            if not isinstance(evidence, dict) or evidence.get("itemId") not in source_by_id:
                raise IntelligenceValidationError("assessment evidence refers to an unknown source item")
            source = source_by_id[evidence["itemId"]]
            if (any(evidence.get(key) != source.get(key) for key in ("sourceUri", "contentHash"))
                    or evidence.get("sourceVersion") != str(source.get("sourceVersion") or source.get("contentHash") or "")):
                raise IntelligenceValidationError("assessment evidence metadata does not match the source snapshot")
            start, end = evidence.get("lineStart"), evidence.get("lineEnd")
            lines = str(source.get("body") or "").splitlines() or [str(source.get("body") or "")]
            if type(start) is not int or type(end) is not int or not (1 <= start <= end <= len(lines)) or evidence.get("quote") != "\n".join(lines[start - 1:end]):
                raise IntelligenceValidationError("assessment quote and line range do not match the source snapshot")
        provider = str(payload.get("provider") or "deterministic-trend-provider")[:120]
        model = str(payload.get("model") or "").strip() or None
        analysis_type = str(payload.get("analysisType") or "signal-summary")[:80]
        uncertainty = payload.get("uncertainty") or []
        warnings = payload.get("warnings") or []
        canonical = _json({
            "taskId": task_id, "title": title, "question": question,
            "horizonYears": horizon, "summary": summary,
            "evidenceRefs": evidence_refs, "sourceSnapshot": source_snapshot,
            "provider": provider, "model": model, "analysisType": analysis_type,
        })
        content_hash = _sha(canonical)
        assessment_id = str(payload.get("assessmentId") or _id("assessment"))
        created = _now()
        with self._lock, self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            parent_id = (summary.get("capabilityAssessment") or {}).get("basedOnAssessmentId")
            if parent_id:
                parent = self._assessment(con, con.execute("SELECT * FROM intelligence_assessment WHERE assessment_id=?", (parent_id,)).fetchone())
                if parent is None or parent["taskId"] != task_id:
                    raise IntelligenceValidationError("capability parent assessment must exist in the same task")
                if parent["status"] == "withdrawn":
                    raise IntelligenceConflict("withdrawn assessment cannot be used for a new draft")
            if con.execute("SELECT 1 FROM intelligence_assessment WHERE content_hash=?", (content_hash,)).fetchone():
                row = con.execute("SELECT * FROM intelligence_assessment WHERE content_hash=?", (content_hash,)).fetchone()
                con.commit()
                return {"assessment": self._assessment(con, row), "duplicate": True}
            try:
                con.execute("""INSERT INTO intelligence_assessment
                    (assessment_id,task_id,title,question,horizon_years,revision,status,provider,model,analysis_type,summary,evidence_refs,source_snapshot,uncertainty,warnings,content_hash,created_at,updated_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                    assessment_id, task_id, title, question, horizon, 1, "under-review", provider, model,
                    analysis_type, _json(summary), _json(evidence_refs), _json(source_snapshot),
                    _json(uncertainty), _json(warnings), content_hash, created, created,
                ))
            except sqlite3.IntegrityError as exc:
                raise IntelligenceConflict(f"assessment already exists: {assessment_id}") from exc
            self._event_tx(con, "TrendAssessmentDraftCreated", assessment_id, {
                "entityId": assessment_id, "entityType": "IntelligenceAssessment", "status": "under-review",
                "assessmentId": assessment_id, "taskId": task_id, "contentHashOrClaimHash": content_hash,
                "provider": provider, "analysisType": analysis_type,
            }, "intelligence-agent")
            row = con.execute("SELECT * FROM intelligence_assessment WHERE assessment_id=?", (assessment_id,)).fetchone()
            con.commit()
            return {"assessment": _row(row), "duplicate": False}

    @staticmethod
    def _assessment(con: sqlite3.Connection, row: sqlite3.Row | None) -> dict[str, Any] | None:
        result = _row(row)
        if result is None:
            return None
        # Withdrawal is a new audit fact, never a rewrite of the published
        # report or its review history. All public projections use this view.
        withdrawal = con.execute(
            "SELECT event_id,payload,actor,occurred_at FROM intelligence_event "
            "WHERE subject_id=? AND event_type='TrendAssessmentPublicationWithdrawn' ORDER BY seq DESC LIMIT 1",
            (result["assessmentId"],),
        ).fetchone()
        if withdrawal:
            result["status"] = "withdrawn"
            result["publicationWithdrawal"] = {
                "eventId": withdrawal["event_id"], "actor": withdrawal["actor"],
                "createdAt": withdrawal["occurred_at"], "reason": _decode(withdrawal["payload"])["reason"],
            }
        return result

    def withdraw_assessment_publication(self, assessment_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Record an administrative correction without erasing audit history."""
        actor = str(payload.get("actor") or "").strip()
        reason = str(payload.get("reason") or "").strip()
        if not actor or not reason:
            raise IntelligenceValidationError("actor and reason are required for publication withdrawal")
        with self._lock, self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT * FROM intelligence_assessment WHERE assessment_id=?", (str(assessment_id),)).fetchone()
            if row is None:
                raise IntelligenceNotFound(f"assessment not found: {assessment_id}")
            current = self._assessment(con, row)
            if current["status"] == "withdrawn":
                con.commit()
                return current
            if row["status"] != "published":
                raise IntelligenceConflict("only a published assessment can be withdrawn")
            self._event_tx(con, "TrendAssessmentPublicationWithdrawn", assessment_id, {
                "entityId": assessment_id, "entityType": "IntelligenceAssessment", "status": "withdrawn",
                "assessmentId": assessment_id, "taskId": row["task_id"], "reason": reason,
                "invalidatedReviewId": row["latest_review_id"], "contentHashOrClaimHash": row["content_hash"],
            }, actor)
            con.commit()
            return self._assessment(con, row)

    def get_assessment(self, assessment_id: str) -> dict[str, Any] | None:
        with self._connect() as con:
            return self._assessment(con, con.execute("SELECT * FROM intelligence_assessment WHERE assessment_id=?", (str(assessment_id),)).fetchone())

    def list_assessments(self, task_id: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM intelligence_assessment"
        args: list[Any] = []
        if task_id:
            query += " WHERE task_id=?"
            args.append(str(task_id))
        query += " ORDER BY created_at DESC, assessment_id"
        with self._connect() as con:
            return [self._assessment(con, row) for row in con.execute(query, args)]

    def list_assessment_reviews(self, assessment_id: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM intelligence_assessment_review"
        args: list[Any] = []
        if assessment_id:
            query += " WHERE assessment_id=?"
            args.append(str(assessment_id))
        query += " ORDER BY created_at"
        with self._connect() as con:
            return [_row(row) for row in con.execute(query, args)]

    # ---- Periodic audit and item-level human review ---------------------
    # A validation round records a verifier observation.  The audit ledger
    # below is the governance boundary around that observation: every issue is
    # retained, tied to the immutable source snapshot and must receive an
    # explicit operator decision before it is considered closed.
    @staticmethod
    def _audit_evidence_refs(refs: Any, sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if refs is None:
            return []
        if not isinstance(refs, list):
            raise IntelligenceValidationError("audit evidenceRefs must be an array")
        if not refs:
            return []
        evidence_ids = [str(ref.get("itemId")) for ref in refs if isinstance(ref, dict) and ref.get("itemId")]
        if len(evidence_ids) != len(refs) or len(set(evidence_ids)) != len(evidence_ids):
            raise IntelligenceValidationError("audit evidenceRefs require unique source item IDs")
        # Reuse the graph provenance validator.  It checks source URI/version/
        # hash as well as exact line/character quote boundaries.
        element = {"evidenceIds": evidence_ids, "payload": {"evidenceRefs": refs}}
        return IntelligenceStore._graph_evidence(element, sources)

    @staticmethod
    def _audit_state_from_findings(findings: list[dict[str, Any]]) -> str:
        if any(item.get("state") in {"open", "needs-evidence"} for item in findings):
            return "needs-review"
        return "passed"

    def list_audits(self, assessment_id: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM intelligence_ontology_audit"
        args: list[Any] = []
        if assessment_id:
            query += " WHERE assessment_id=?"
            args.append(str(assessment_id))
        query += " ORDER BY created_at DESC, audit_id"
        with self._connect() as con:
            audits = []
            for row in con.execute(query, args):
                item = _row(row)
                findings = [_row(f) for f in con.execute(
                    "SELECT * FROM intelligence_audit_finding WHERE audit_id=? ORDER BY severity DESC, created_at, finding_id",
                    (row["audit_id"],),
                )]
                item["findings"] = findings
                item["findingCount"] = len(findings)
                item["openFindingCount"] = sum(f.get("state") in {"open", "needs-evidence"} for f in findings)
                item["currentStatus"] = self._audit_state_from_findings(findings)
                audits.append(item)
            return audits

    # Alias used by clients that call the object a periodic audit run.
    list_audit_runs = list_audits

    def get_audit(self, audit_id: str) -> dict[str, Any] | None:
        return next((item for item in self.list_audits() if item.get("auditId") == str(audit_id)), None)

    def list_audit_findings(self, assessment_id: str | None = None, audit_id: str | None = None,
                            state: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM intelligence_audit_finding WHERE 1=1"
        args: list[Any] = []
        if assessment_id:
            query += " AND assessment_id=?"
            args.append(str(assessment_id))
        if audit_id:
            query += " AND audit_id=?"
            args.append(str(audit_id))
        if state:
            query += " AND state=?"
            args.append(str(state))
        query += " ORDER BY CASE severity WHEN 'critical' THEN 0 WHEN 'high' THEN 1 WHEN 'medium' THEN 2 WHEN 'low' THEN 3 ELSE 4 END, created_at, finding_id"
        with self._connect() as con:
            result = []
            for row in con.execute(query, args):
                item = _row(row)
                reviews = [_row(review) for review in con.execute(
                    "SELECT * FROM intelligence_audit_finding_review WHERE finding_id=? ORDER BY created_at",
                    (row["finding_id"],),
                )]
                item["reviews"] = reviews
                result.append(item)
            return result

    def get_audit_finding(self, finding_id: str) -> dict[str, Any] | None:
        return next((item for item in self.list_audit_findings() if item.get("findingId") == str(finding_id)), None)

    def run_periodic_audit(self, assessment_id: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        """Run deterministic integrity checks against an assessment snapshot.

        The method only creates an immutable audit and finding records.  It
        never changes an admission, graph revision or published projection.
        Findings are deliberately conservative: model confidence is evidence
        for review, never a human decision.
        """
        payload = payload if isinstance(payload, dict) else {}
        actor = str(payload.get("actor") or "periodic-audit").strip() or "periodic-audit"
        trigger = str(payload.get("trigger") or "periodic").strip()
        if trigger not in {"periodic", "manual", "publication-gate"}:
            raise IntelligenceValidationError("audit trigger must be periodic, manual or publication-gate")
        with self._lock, self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            assessment_row = con.execute("SELECT * FROM intelligence_assessment WHERE assessment_id=?", (str(assessment_id),)).fetchone()
            if assessment_row is None:
                raise IntelligenceNotFound(f"assessment not found: {assessment_id}")
            assessment = self._assessment(con, assessment_row)
            if assessment.get("status") == "withdrawn":
                raise IntelligenceConflict("withdrawn assessment cannot be audited")
            source_snapshot = _decode(assessment_row["source_snapshot"]) or []
            source_hash = self._snapshot_hash(source_snapshot)
            graph_row = con.execute("SELECT * FROM intelligence_graph_revision WHERE assessment_id=? ORDER BY revision DESC LIMIT 1", (str(assessment_id),)).fetchone()
            graph_id = graph_row["graph_revision_id"] if graph_row else None
            findings: list[dict[str, Any]] = []
            checks: list[dict[str, Any]] = []

            def add_check(code: str, status: str, detail: str, target_id: str | None = None) -> None:
                checks.append({"code": code, "status": status, "detail": detail, **({"targetId": target_id} if target_id else {})})

            if not source_snapshot:
                add_check("source-snapshot-present", "failed", "assessment has no immutable source snapshot")
                findings.append({"targetType": "assessment", "targetId": str(assessment_id), "code": "source-snapshot-missing", "severity": "critical", "state": "open", "message": "研判没有可核验的来源快照。", "evidenceRefs": []})
            else:
                add_check("source-snapshot-present", "passed", f"{len(source_snapshot)} immutable source items")

            if graph_row is None:
                add_check("evidence-graph-present", "needs-review", "assessment has no stored graph")
                findings.append({"targetType": "assessment", "targetId": str(assessment_id), "code": "graph-missing", "severity": "high", "state": "open", "message": "尚未形成可审计的对象图谱。", "evidenceRefs": []})
            else:
                add_check("evidence-graph-present", "passed", f"graph revision {graph_row['revision']}", graph_id)

            admission_rows = list(con.execute("SELECT * FROM intelligence_ontology_admission WHERE assessment_id=? ORDER BY created_at, admission_id", (str(assessment_id),)))
            for admission in admission_rows:
                mapping = _decode(admission["mapping"]) or {}
                assertion = _decode(admission["assertion"]) or {}
                target = admission["admission_id"]
                refs = assertion.get("evidenceRefs") if isinstance(assertion, dict) else []
                # Capability admissions use the legacy singular ``evidence``
                # field; audit it through the same exact-locator validator as
                # graph nodes, properties, relations and events.
                if not refs and isinstance(assertion, dict) and isinstance(assertion.get("evidence"), dict):
                    refs = [assertion["evidence"]]
                try:
                    canonical_refs = self._audit_evidence_refs(refs, source_snapshot)
                except IntelligenceStoreError as exc:
                    canonical_refs = []
                    findings.append({"targetType": "admission", "targetId": target, "code": "evidence-invalid", "severity": "critical", "state": "open", "message": str(exc), "evidenceRefs": []})
                if not canonical_refs:
                    findings.append({"targetType": "admission", "targetId": target, "code": "evidence-missing", "severity": "high", "state": "open", "message": "本体候选缺少可定位的来源证据。", "evidenceRefs": []})
                if admission["source_snapshot_hash"] != source_hash:
                    findings.append({"targetType": "admission", "targetId": target, "code": "source-snapshot-stale", "severity": "high", "state": "open", "message": "候选引用的来源快照已变化，必须重新提取并审核。", "evidenceRefs": canonical_refs})
                if mapping.get("kind") in {"graph-node", "graph-relation", "graph-property"} and (not graph_row or mapping.get("graphRevisionId") != graph_id):
                    findings.append({"targetType": "admission", "targetId": target, "code": "graph-revision-stale", "severity": "high", "state": "open", "message": "候选不再对应当前图谱版本。", "evidenceRefs": canonical_refs})
                if admission["status"] in {"under-review", "needs-evidence"}:
                    findings.append({"targetType": "admission", "targetId": target, "code": "human-review-pending", "severity": "medium", "state": "open", "message": "模型候选尚未完成逐项人工审核；模型置信度不能替代确认。", "evidenceRefs": canonical_refs})
                elif admission["status"] == "accepted":
                    review = con.execute("SELECT verified_confidence FROM intelligence_ontology_admission_review WHERE admission_id=? AND review_id=? AND action='accept'", (target, admission["latest_review_id"])).fetchone()
                    if not review or review["verified_confidence"] != "high":
                        findings.append({"targetType": "admission", "targetId": target, "code": "human-confidence-missing", "severity": "high", "state": "open", "message": "接受记录缺少人工高置信度确认。", "evidenceRefs": canonical_refs})
                add_check("admission-evidence", "passed" if canonical_refs else "failed", "source quote locator validated" if canonical_refs else "missing or invalid source quote", target)

            status = "needs-review" if findings else "passed"
            run_no = int(con.execute("SELECT COALESCE(MAX(run_no),0)+1 FROM intelligence_ontology_audit WHERE assessment_id=?", (str(assessment_id),)).fetchone()[0])
            audit_id, created = _id("ontology-audit"), _now()
            summary = {"findingCount": len(findings), "openFindingCount": len(findings), "admissionCount": len(admission_rows), "sourceCount": len(source_snapshot), "modelCanProposeOnly": True}
            content_hash = _sha(_json({"assessmentId": assessment_id, "runNo": run_no, "sourceSnapshotHash": source_hash, "checks": checks, "findings": findings}))
            con.execute("INSERT INTO intelligence_ontology_audit(audit_id,assessment_id,scope,status,checks,summary,content_hash,actor,created_at,run_no,trigger,assessment_revision,graph_revision_id,completed_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (audit_id, assessment_id, "assessment-ontology", status, _json(checks), _json(summary), content_hash, actor, created, run_no, trigger, int(assessment_row["revision"]), graph_id, created))
            for issue in findings:
                finding_id = _id("audit-finding")
                con.execute("INSERT INTO intelligence_audit_finding(finding_id,audit_id,assessment_id,target_type,target_id,code,severity,state,message,evidence_refs,expected_revision,revision,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (finding_id, audit_id, assessment_id, issue["targetType"], issue["targetId"], issue["code"], issue["severity"], issue["state"], issue["message"], _json(issue.get("evidenceRefs") or []), int(assessment_row["revision"]), 1, created, created))
            self._event_tx(con, "OntologyPeriodicAuditCompleted", audit_id, {"entityId": audit_id, "entityType": "OntologyAudit", "assessmentId": assessment_id, "auditId": audit_id, "runNo": run_no, "status": status, "findingCount": len(findings), "sourceSnapshotHash": source_hash, "modelCanProposeOnly": True}, actor)
            con.commit()
            return self.get_audit(audit_id)  # type: ignore[return-value]

    def review_audit_finding(self, finding_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise IntelligenceValidationError("audit finding review payload must be an object")
        action = str(payload.get("action") or "").strip()
        action = {"accept": "confirm", "resolve": "confirm", "reject": "dismiss"}.get(action, action)
        if action not in {"confirm", "dismiss", "request-more-evidence", "reopen"}:
            raise IntelligenceValidationError("audit finding action must be confirm, dismiss, request-more-evidence or reopen")
        reviewer = str(payload.get("reviewer") or "").strip()
        reason = str(payload.get("reason") or "").strip()
        if not reviewer or not reason:
            raise IntelligenceValidationError("reviewer and reason are required")
        if "expectedRevision" not in payload:
            raise IntelligenceValidationError("expectedRevision is required for audit finding review")
        expected = _revision(payload["expectedRevision"])
        with self._lock, self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT * FROM intelligence_audit_finding WHERE finding_id=?", (str(finding_id),)).fetchone()
            if row is None:
                raise IntelligenceNotFound(f"audit finding not found: {finding_id}")
            if expected != int(row["revision"]):
                raise IntelligenceConflict("audit finding changed; reload before reviewing")
            assessment_row = con.execute("SELECT * FROM intelligence_assessment WHERE assessment_id=?", (row["assessment_id"],)).fetchone()
            if assessment_row is None:
                raise IntelligenceNotFound(f"assessment not found: {row['assessment_id']}")
            sources = _decode(assessment_row["source_snapshot"]) or []
            evidence_input = payload.get("evidenceRefs", [])
            evidence = self._audit_evidence_refs(evidence_input, sources)
            state = {"confirm": "confirmed", "dismiss": "dismissed", "request-more-evidence": "needs-evidence", "reopen": "open"}[action]
            review_id, created = _id("audit-review"), _now()
            con.execute("INSERT INTO intelligence_audit_finding_review(audit_review_id,finding_id,action,reviewer,reason,evidence_refs,expected_revision,created_at) VALUES(?,?,?,?,?,?,?,?)", (review_id, finding_id, action, reviewer, reason, _json(evidence), expected, created))
            con.execute("UPDATE intelligence_audit_finding SET state=?,revision=revision+1,latest_review_id=?,updated_at=? WHERE finding_id=?", (state, review_id, created, finding_id))
            self._event_tx(con, "OntologyAuditFindingReviewed", finding_id, {"entityId": finding_id, "entityType": "AuditFinding", "findingId": finding_id, "auditId": row["audit_id"], "assessmentId": row["assessment_id"], "action": action, "state": state, "reviewId": review_id, "revision": expected + 1, "evidenceRefs": evidence}, reviewer)
            con.commit()
            return self.get_audit_finding(finding_id)  # type: ignore[return-value]

    # ---- Evidence graph, validation rounds and ontology admission ---------
    @staticmethod
    def _snapshot_hash(source_snapshot: list[dict[str, Any]]) -> str:
        return _sha(_json(source_snapshot))

    def get_graph(self, assessment_id: str) -> dict[str, Any] | None:
        with self._connect() as con:
            assessment = self._assessment(con, con.execute("SELECT * FROM intelligence_assessment WHERE assessment_id=?", (str(assessment_id),)).fetchone())
            if assessment is None:
                return None
            if assessment.get("status") == "withdrawn":
                return None
            row = con.execute("SELECT * FROM intelligence_graph_revision WHERE assessment_id=? ORDER BY revision DESC LIMIT 1", (str(assessment_id),)).fetchone()
            return _row(row)

    def list_graph_revisions(self, assessment_id: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM intelligence_graph_revision"
        args: list[Any] = []
        if assessment_id:
            query += " WHERE assessment_id=?"
            args.append(str(assessment_id))
        query += " ORDER BY assessment_id, revision"
        with self._connect() as con:
            return [_row(row) for row in con.execute(query, args)]

    def save_graph(self, assessment_id: str, graph: dict[str, Any], *, expected_revision: int | None = None,
                   expected_graph_revision: Any = _GRAPH_REVISION_UNSPECIFIED) -> dict[str, Any]:
        if not isinstance(graph, dict) or not isinstance(graph.get("nodes"), list) or not isinstance(graph.get("edges"), list):
            raise IntelligenceValidationError("graph nodes and edges must be arrays")
        with self._lock, self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT * FROM intelligence_assessment WHERE assessment_id=?", (str(assessment_id),)).fetchone()
            if row is None:
                raise IntelligenceNotFound(f"assessment not found: {assessment_id}")
            if self._assessment(con, row).get("status") == "withdrawn":
                raise IntelligenceConflict("withdrawn assessment cannot create evidence graph")
            if expected_revision is not None and _revision(expected_revision) != int(row["revision"]):
                raise IntelligenceConflict("assessment revision changed; reload before building graph")
            if expected_graph_revision is not _GRAPH_REVISION_UNSPECIFIED:
                if expected_graph_revision is not None and (not isinstance(expected_graph_revision, str) or not expected_graph_revision):
                    raise IntelligenceValidationError("expected_graph_revision must be an existing graph ID or None")
                latest = con.execute("SELECT graph_revision_id FROM intelligence_graph_revision WHERE assessment_id=? ORDER BY revision DESC LIMIT 1", (str(assessment_id),)).fetchone()
                current_graph_id = latest["graph_revision_id"] if latest else None
                if current_graph_id != expected_graph_revision:
                    raise IntelligenceConflict("graph revision changed during extraction; reload before saving graph")
            source_snapshot = _decode(row["source_snapshot"]) or []
            allowed_sources = {str(source.get("itemId")) for source in source_snapshot if isinstance(source, dict)}
            source_by_id = {str(source["itemId"]): source for source in source_snapshot}
            def validate_payload_evidence(element: dict[str, Any]) -> None:
                payload = element.get("payload") or {}
                if not isinstance(payload, dict):
                    raise IntelligenceValidationError("graph payload must be an object")
                refs = payload.get("evidenceRefs", [])
                if not isinstance(refs, list):
                    raise IntelligenceValidationError("graph evidenceRefs must be an array")
                for evidence in refs:
                    if not isinstance(evidence, dict) or evidence.get("itemId") not in element["evidenceIds"]:
                        raise IntelligenceValidationError("graph evidence locator is not in its source references")
                    source = source_by_id[evidence["itemId"]]
                    if (any(evidence.get(key) != source.get(key) for key in ("sourceUri", "contentHash"))
                            or evidence.get("sourceVersion") != str(source.get("sourceVersion") or source.get("contentHash") or "")):
                        raise IntelligenceValidationError("graph evidence provenance does not match the source snapshot")
                    quote = evidence.get("quote")
                    body = str(source.get("body") or "")
                    if "startChar" in evidence or "endChar" in evidence:
                        start, end = evidence.get("startChar"), evidence.get("endChar")
                        if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(body) or body[start:end] != quote:
                            raise IntelligenceValidationError("graph quote character range does not match the source snapshot")
                    else:
                        lines = body.splitlines() or [body]
                        start, end = evidence.get("lineStart"), evidence.get("lineEnd")
                        if type(start) is not int or type(end) is not int or not 1 <= start <= end <= len(lines) or "\n".join(lines[start - 1:end]) != quote:
                            raise IntelligenceValidationError("graph quote line range does not match the source snapshot")
            if len(graph["nodes"]) > 1000 or len(graph["edges"]) > 4000:
                raise IntelligenceValidationError("graph exceeds the bounded projection size")
            for node in graph["nodes"]:
                if not isinstance(node, dict) or not str(node.get("id") or "").strip() or not str(node.get("type") or "").strip():
                    raise IntelligenceValidationError("graph node id and type are required")
                if not isinstance(node.get("evidenceIds"), list) or not node["evidenceIds"] or not set(str(x) for x in node["evidenceIds"]).issubset(allowed_sources):
                    raise IntelligenceValidationError("graph node evidence refers to an unknown source item")
                validate_payload_evidence(node)
            node_ids = {str(node["id"]) for node in graph["nodes"]}
            if len(node_ids) != len(graph["nodes"]):
                raise IntelligenceValidationError("graph node IDs must be unique")
            edge_ids = set()
            for edge in graph["edges"]:
                if not isinstance(edge, dict) or str(edge.get("from")) not in node_ids or str(edge.get("to")) not in node_ids or not str(edge.get("relation") or "").strip():
                    raise IntelligenceValidationError("graph edge endpoints and relation are required")
                if not isinstance(edge.get("id"), str) or not edge["id"] or edge["id"] in edge_ids:
                    raise IntelligenceValidationError("graph edge IDs must be non-empty and unique")
                edge_ids.add(edge["id"])
                if not isinstance(edge.get("evidenceIds"), list) or not edge["evidenceIds"] or not set(str(x) for x in edge["evidenceIds"]).issubset(allowed_sources):
                    raise IntelligenceValidationError("graph edge evidence refers to an unknown source item")
                validate_payload_evidence(edge)
            content = _json({"assessmentId": assessment_id, "nodes": graph["nodes"], "edges": graph["edges"]})
            digest = _sha(content)
            old = con.execute("SELECT * FROM intelligence_graph_revision WHERE content_hash=?", (digest,)).fetchone()
            if old:
                con.commit()
                return _row(old)  # type: ignore[return-value]
            revision = int(con.execute("SELECT COALESCE(MAX(revision),0)+1 FROM intelligence_graph_revision WHERE assessment_id=?", (str(assessment_id),)).fetchone()[0])
            graph_id = _id("graph-revision")
            created = _now()
            con.execute("INSERT INTO intelligence_graph_revision(graph_revision_id,assessment_id,revision,nodes,edges,content_hash,created_at) VALUES(?,?,?,?,?,?,?)", (graph_id, assessment_id, revision, _json(graph["nodes"]), _json(graph["edges"]), digest, created))
            self._event_tx(con, "EvidenceGraphRevisionCreated", graph_id, {"entityId": graph_id, "entityType": "EvidenceGraphRevision", "assessmentId": assessment_id, "graphRevisionId": graph_id, "revision": revision, "contentHashOrClaimHash": digest}, "intelligence-agent")
            result = _row(con.execute("SELECT * FROM intelligence_graph_revision WHERE graph_revision_id=?", (graph_id,)).fetchone())
            con.commit()
            return result  # type: ignore[return-value]

    def list_validation_rounds(self, assessment_id: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM intelligence_validation_round"
        args: list[Any] = []
        if assessment_id:
            query += " WHERE assessment_id=?"
            args.append(str(assessment_id))
        query += " ORDER BY assessment_id, round_no"
        with self._connect() as con:
            return [_row(row) for row in con.execute(query, args)]

    def list_ontology_audits(self, assessment_id: str | None = None) -> list[dict[str, Any]]:
        """Return immutable publication/periodic audit records."""
        return self.list_audits(assessment_id)

    @staticmethod
    def _audit_evidence(assertion: dict[str, Any], sources: list[dict[str, Any]]) -> tuple[bool, str]:
        """Check every published assertion's locator against its frozen source.

        Graph admissions carry ``evidenceRefs``; legacy capability admissions
        carry one ``evidence`` object.  The audit deliberately checks the
        locator and quote again at publication time so a later source snapshot
        or malformed legacy row cannot silently look publishable.
        """
        if not isinstance(assertion, dict):
            return False, "assertion is not an object"
        refs = assertion.get("evidenceRefs")
        if refs is None and isinstance(assertion.get("evidence"), dict):
            refs = [assertion["evidence"]]
        if not isinstance(refs, list) or not refs:
            return False, "no exact evidence references"
        by_id = {str(item.get("itemId")): item for item in sources if isinstance(item, dict) and item.get("itemId")}
        for ref in refs:
            if not isinstance(ref, dict) or str(ref.get("itemId")) not in by_id:
                return False, "evidence item is absent from the source snapshot"
            source = by_id[str(ref["itemId"])]
            if ref.get("sourceUri") != source.get("sourceUri") or ref.get("contentHash") != source.get("contentHash"):
                return False, "evidence provenance does not match the source snapshot"
            expected_version = str(source.get("sourceVersion") or source.get("contentHash") or "")
            if ref.get("sourceVersion") != expected_version:
                return False, "evidence source version does not match the source snapshot"
            quote = ref.get("quote")
            body = str(source.get("body") or "")
            if not isinstance(quote, str) or not quote.strip():
                return False, "evidence quote is empty"
            if "startChar" in ref or "endChar" in ref:
                start, end = ref.get("startChar"), ref.get("endChar")
                valid = type(start) is int and type(end) is int and 0 <= start < end <= len(body) and body[start:end] == quote
            else:
                lines = body.splitlines() or [body]
                start, end = ref.get("lineStart"), ref.get("lineEnd")
                valid = type(start) is int and type(end) is int and 1 <= start <= end <= len(lines) and "\n".join(lines[start - 1:end]) == quote
            if not valid:
                return False, "evidence quote locator does not match the source snapshot"
        return True, f"{len(refs)} exact source locator(s) verified"

    def audit_ontology_publication(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        """Run a read-only audit over published ontology admissions.

        This is the periodic governance checkpoint between the entity dossier
        and a later human review.  It never changes admission status and never
        publishes or withdraws facts; it records an immutable result that can
        be read back from the audit API.
        """
        payload = payload if isinstance(payload, dict) else {}
        actor = str(payload.get("actor") or "ontology-auditor").strip()
        if not actor:
            raise IntelligenceValidationError("audit actor is required")
        assessment_filter = payload.get("assessmentId")
        if assessment_filter is not None and not str(assessment_filter).strip():
            raise IntelligenceValidationError("assessmentId must be a non-empty string when provided")
        with self._lock, self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            query = "SELECT * FROM intelligence_ontology_admission WHERE status='published'"
            args: list[Any] = []
            if assessment_filter:
                query += " AND assessment_id=?"
                args.append(str(assessment_filter))
            query += " ORDER BY assessment_id, admission_id"
            rows = con.execute(query, args).fetchall()
            checks: list[dict[str, Any]] = []
            for row in rows:
                admission_id = row["admission_id"]
                assertion = _decode(row["assertion"]) or {}
                mapping = _decode(row["mapping"]) or {}
                source_row = con.execute("SELECT source_snapshot FROM intelligence_assessment WHERE assessment_id=?", (row["assessment_id"],)).fetchone()
                sources = _decode(source_row["source_snapshot"]) if source_row else []
                # Every published record must still have an explicit human
                # acceptance with high verified confidence.
                review = con.execute("SELECT * FROM intelligence_ontology_admission_review WHERE review_id=? AND admission_id=?", (row["latest_review_id"], admission_id)).fetchone()
                if not review or review["action"] != "accept" or review["verified_confidence"] != "high":
                    checks.append({"admissionId": admission_id, "code": "human-review-high-confidence", "status": "failed", "detail": "published admission has no current high-confidence human acceptance"})
                else:
                    checks.append({"admissionId": admission_id, "code": "human-review-high-confidence", "status": "passed", "detail": "current acceptance review is high confidence"})
                # Source snapshot hash and exact quote are independently
                # checked even though proposal-time checks already ran.
                expected_hash = self._snapshot_hash(sources or [])
                if row["source_snapshot_hash"] != expected_hash:
                    checks.append({"admissionId": admission_id, "code": "source-snapshot-version", "status": "failed", "detail": "published assertion points at a changed source snapshot"})
                else:
                    checks.append({"admissionId": admission_id, "code": "source-snapshot-version", "status": "passed", "detail": "source snapshot hash matches"})
                evidence_ok, evidence_detail = self._audit_evidence(assertion, sources or [])
                checks.append({"admissionId": admission_id, "code": "evidence-completeness", "status": "passed" if evidence_ok else "failed", "detail": evidence_detail})
                # Graph records must still refer to the current immutable
                # graph revision.  A changed graph is a review blocker even
                # if the old candidate itself remains append-only.
                if mapping.get("kind") in {"graph-node", "graph-relation", "graph-property"}:
                    try:
                        self._check_graph_admission_current(con, row)
                        checks.append({"admissionId": admission_id, "code": "graph-revision-current", "status": "passed", "detail": "published assertion is bound to the current graph revision"})
                    except IntelligenceConflict as exc:
                        checks.append({"admissionId": admission_id, "code": "graph-revision-current", "status": "failed", "detail": str(exc)})
                if mapping.get("kind") in {"graph-relation", "graph-property"}:
                    required = set(mapping.get("requiredCandidateIris") or [])
                    published_endpoints = {candidate["candidate_iri"] for candidate in con.execute("SELECT candidate_iri FROM intelligence_ontology_admission WHERE assessment_id=? AND status='published'", (row["assessment_id"],))}
                    endpoint_ok = required.issubset(published_endpoints)
                    checks.append({"admissionId": admission_id, "code": "published-endpoints", "status": "passed" if endpoint_ok else "failed", "detail": "all endpoint admissions are published" if endpoint_ok else "one or more endpoint admissions are not published"})
            failed = sum(check["status"] == "failed" for check in checks)
            summary = {"publishedAdmissions": len(rows), "checks": len(checks), "passed": len(checks) - failed, "failed": failed}
            status = "failed" if failed else "passed"
            created = _now()
            # Migration 010 makes audit runs append-only per assessment.  A
            # publication projection audit uses the same ledger, therefore it
            # must claim its own run number instead of relying on the default
            # (which would collide on the second audit for an assessment).
            run_no = int(con.execute("SELECT COALESCE(MAX(run_no),0)+1 FROM intelligence_ontology_audit WHERE assessment_id IS ?", (str(assessment_filter) if assessment_filter else None,)).fetchone()[0])
            canonical = {"assessmentId": str(assessment_filter) if assessment_filter else None, "scope": "published-ontology", "status": status, "checks": checks, "summary": summary, "runNo": run_no, "createdAt": created}
            digest = _sha(_json(canonical))
            audit_id = _id("ontology-audit")
            con.execute("INSERT INTO intelligence_ontology_audit(audit_id,assessment_id,scope,status,checks,summary,content_hash,actor,created_at,run_no,trigger,completed_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (audit_id, str(assessment_filter) if assessment_filter else None, "published-ontology", status, _json(checks), _json(summary), digest, actor, created, run_no, "publication-gate", created))
            self._event_tx(con, "OntologyPublicationAuditRecorded", audit_id, {"entityId": audit_id, "entityType": "OntologyAudit", "auditId": audit_id, "assessmentId": str(assessment_filter) if assessment_filter else None, "scope": "published-ontology", "status": status, "contentHashOrClaimHash": digest}, actor)
            result = _row(con.execute("SELECT * FROM intelligence_ontology_audit WHERE audit_id=?", (audit_id,)).fetchone())
            con.commit()
            return result  # type: ignore[return-value]

    def save_validation_round(self, assessment_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise IntelligenceValidationError("validation payload must be an object")
        actor = str(payload.get("actor") or "intelligence-verifier").strip()
        kind = str(payload.get("kind") or "deterministic").strip()
        if kind not in {"deterministic", "model-critique"}:
            raise IntelligenceValidationError("validation kind must be deterministic or model-critique")
        with self._lock, self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT * FROM intelligence_assessment WHERE assessment_id=?", (str(assessment_id),)).fetchone()
            if row is None:
                raise IntelligenceNotFound(f"assessment not found: {assessment_id}")
            if self._assessment(con, row).get("status") == "withdrawn":
                raise IntelligenceConflict("withdrawn assessment cannot record validation rounds")
            expected = payload.get("expectedRevision")
            if expected is not None and _revision(expected) != int(row["revision"]):
                raise IntelligenceConflict("assessment revision changed; reload before validating")
            round_no = int(con.execute("SELECT COALESCE(MAX(round_no),0)+1 FROM intelligence_validation_round WHERE assessment_id=?", (str(assessment_id),)).fetchone()[0])
            source_snapshot = _decode(row["source_snapshot"]) or []
            snapshot_hash = self._snapshot_hash(source_snapshot)
            checks = payload.get("checks") if isinstance(payload.get("checks"), list) else []
            factors = payload.get("confidenceFactors") if isinstance(payload.get("confidenceFactors"), dict) else {}
            issues = payload.get("issues") if isinstance(payload.get("issues"), list) else []
            status = str(payload.get("status") or ("needs-evidence" if issues else "passed"))
            if status not in {"passed", "needs-evidence", "failed"}:
                raise IntelligenceValidationError("validation status is invalid")
            rid = _id("validation-round")
            created = _now()
            provider = str(payload.get("provider") or "deterministic-evidence-verifier")[:120]
            model = str(payload.get("model") or "").strip() or None
            con.execute("INSERT INTO intelligence_validation_round(validation_round_id,assessment_id,round_no,kind,status,checks,confidence_factors,issues,source_snapshot_hash,provider,model,actor,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (rid, assessment_id, round_no, kind, status, _json(checks), _json(factors), _json(issues), snapshot_hash, provider, model, actor, created))
            self._event_tx(con, "AssessmentValidationRoundRecorded", rid, {"entityId": rid, "entityType": "ValidationRound", "assessmentId": assessment_id, "validationRoundId": rid, "roundNo": round_no, "status": status, "kind": kind, "sourceSnapshotHash": snapshot_hash}, actor)
            result = _row(con.execute("SELECT * FROM intelligence_validation_round WHERE validation_round_id=?", (rid,)).fetchone())
            con.commit()
            return result  # type: ignore[return-value]

    def list_admissions(self, assessment_id: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM intelligence_ontology_admission"
        args: list[Any] = []
        if assessment_id:
            query += " WHERE assessment_id=?"
            args.append(str(assessment_id))
        query += " ORDER BY assessment_id, metric_id"
        with self._connect() as con:
            result = []
            for row in con.execute(query, args):
                item = _row(row)
                # Withdrawal invalidates the projection without rewriting its
                # immutable admission record.
                if item and self._assessment(con, con.execute("SELECT * FROM intelligence_assessment WHERE assessment_id=?", (row["assessment_id"],)).fetchone())["status"] == "withdrawn":
                    item["status"] = "withdrawn"
                if item:
                    review = con.execute("SELECT * FROM intelligence_ontology_admission_review WHERE review_id=?", (row["latest_review_id"],)).fetchone()
                    if review:
                        item["review"] = {"action": review["action"], "reviewer": review["reviewer"], "reason": review["reason"], "expectedRevision": int(review["expected_revision"]), "createdAt": review["created_at"]}
                        if review["verified_confidence"]:
                            item["review"]["verifiedConfidence"] = review["verified_confidence"]
                    amendment = con.execute("SELECT replacement_admission_id FROM intelligence_ontology_attribute_amendment WHERE previous_admission_id=?", (row["admission_id"],)).fetchone()
                    if amendment:
                        item["supersededByAdmissionId"] = amendment["replacement_admission_id"]
                        if item.get("review"):
                            item["review"]["action"] = "modify"
                    if item["kind"] in {"graph-node", "graph-relation", "graph-property"}:
                        latest = con.execute("SELECT graph_revision_id FROM intelligence_graph_revision WHERE assessment_id=? ORDER BY revision DESC LIMIT 1", (row["assessment_id"],)).fetchone()
                        item["staleGraph"] = latest is None or latest["graph_revision_id"] != item["graphRevisionId"]
                result.append(item)
            return result

    @staticmethod
    def _graph_node_type(node: dict[str, Any]) -> str:
        kind = node.get("type")
        if not isinstance(kind, str):
            raise IntelligenceValidationError("graph ontology node type must be a declared class name")
        if kind in GRAPH_ENTITY_TYPES:
            return kind
        if kind == "EventRecord" and (node.get("payload") or {}).get("eventKind") in GRAPH_EVENT_TYPES:
            return node["payload"]["eventKind"]
        raise IntelligenceValidationError("only extracted entities and public test/procurement/production events can enter ontology review")

    @staticmethod
    def _graph_evidence(element: dict[str, Any], sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
        refs = (element.get("payload") or {}).get("evidenceRefs")
        if not isinstance(refs, list) or not refs:
            raise IntelligenceValidationError("ontology graph candidates require exact source quotes")
        source_by_id = {source["itemId"]: source for source in sources}
        canonical_refs = []
        for ref in refs:
            if not isinstance(ref, dict) or ref.get("itemId") not in element.get("evidenceIds", []) or ref.get("itemId") not in source_by_id:
                raise IntelligenceValidationError("ontology graph evidence refers to an unknown source")
            source = source_by_id[ref["itemId"]]
            if any(ref.get(key) != source.get(key) for key in ("sourceUri", "contentHash")) or ref.get("sourceVersion") != str(source.get("sourceVersion") or source.get("contentHash") or ""):
                raise IntelligenceValidationError("ontology graph provenance does not match the immutable snapshot")
            quote, body = ref.get("quote"), str(source.get("body") or "")
            if not isinstance(quote, str) or not quote.strip():
                raise IntelligenceValidationError("ontology graph evidence quote is missing")
            if "startChar" in ref or "endChar" in ref:
                start, end = ref.get("startChar"), ref.get("endChar")
                valid = type(start) is int and type(end) is int and 0 <= start < end <= len(body) and body[start:end] == quote
            else:
                lines = body.splitlines() or [body]
                start, end = ref.get("lineStart"), ref.get("lineEnd")
                valid = type(start) is int and type(end) is int and 1 <= start <= end <= len(lines) and "\n".join(lines[start - 1:end]) == quote
            if not valid:
                raise IntelligenceValidationError("ontology graph quote locator does not match the source snapshot")
            locator = {key: ref[key] for key in ("itemId", "sourceUri", "sourceVersion", "contentHash", "quote", "startChar", "endChar", "lineStart", "lineEnd") if key in ref}
            if source.get("publishedAt"):
                locator["publishedAt"] = source["publishedAt"]
            # Source classifications belong to the immutable source snapshot,
            # not model-written graph metadata such as 'official'/'verified'.
            locator.update(source_metadata(source))
            canonical_refs.append(locator)
        # JSON round-trip detaches the proposal from caller-owned nested data.
        return json.loads(_json(canonical_refs))

    @staticmethod
    def _graph_candidate_iri(assessment_id: str, graph_revision_id: str, node_id: str) -> str:
        # Revision-qualified identity keeps a later extraction/classification
        # from silently overwriting an already reviewed endpoint record.
        return INSTANCE_NS + "graph-object-" + _sha(_json([assessment_id, graph_revision_id, node_id]))[:20]

    def propose_graph_admissions(self, assessment_id: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
        """Derive immutable candidates from a stored graph, never client facts."""
        if not isinstance(payload, dict) or set(payload) - {"graphRevisionId", "graphNodeIds", "graphEdgeIds", "graphProperties"}:
            raise IntelligenceValidationError("graph admission input accepts only graph revision and selected IDs")
        revision_id = payload.get("graphRevisionId")
        nodes_selected, edges_selected = payload.get("graphNodeIds", []), payload.get("graphEdgeIds", [])
        properties_selected = payload.get("graphProperties", [])
        if not isinstance(revision_id, str) or not revision_id.strip():
            raise IntelligenceValidationError("graphRevisionId is required")
        if any(not isinstance(ids, list) or any(not isinstance(x, str) or not x.strip() for x in ids) for ids in (nodes_selected, edges_selected)):
            raise IntelligenceValidationError("graphNodeIds and graphEdgeIds require at least one selected ID")
        if (not isinstance(properties_selected, list) or any(not isinstance(prop, dict) or set(prop) - {"graphNodeId", "propertyPath", "ontologyPredicate"}
                or not isinstance(prop.get("graphNodeId"), str) or not prop.get("graphNodeId") or not isinstance(prop.get("propertyPath"), str)
                for prop in properties_selected) or not nodes_selected + edges_selected + properties_selected):
            raise IntelligenceValidationError("graphProperties accepts only a selected node, propertyPath and declared predicate")
        if len(nodes_selected) + len(edges_selected) + len(properties_selected) > 200:
            raise IntelligenceValidationError("graph admission selection exceeds 200 records")
        with self._lock, self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            parent = con.execute("SELECT * FROM intelligence_assessment WHERE assessment_id=?", (str(assessment_id),)).fetchone()
            if parent is None:
                raise IntelligenceNotFound(f"assessment not found: {assessment_id}")
            if self._assessment(con, parent)["status"] == "withdrawn":
                raise IntelligenceConflict("withdrawn assessment cannot create ontology admissions")
            graph_row = con.execute("SELECT * FROM intelligence_graph_revision WHERE assessment_id=? ORDER BY revision DESC LIMIT 1", (assessment_id,)).fetchone()
            if graph_row is None or graph_row["graph_revision_id"] != revision_id:
                raise IntelligenceConflict("graph revision changed; reload before proposing ontology candidates")
            nodes = {node["id"]: node for node in _decode(graph_row["nodes"])}
            edges = {edge["id"]: edge for edge in _decode(graph_row["edges"])}
            if not set(nodes_selected).issubset(nodes) or not set(edges_selected).issubset(edges) or any(prop["graphNodeId"] not in nodes for prop in properties_selected):
                raise IntelligenceValidationError("selected graph IDs are not in the stored revision")
            sources = _decode(parent["source_snapshot"]) or []
            source_hash = self._snapshot_hash(sources)
            proposals = []
            for node_id in dict.fromkeys(nodes_selected):
                node = nodes[node_id]
                kind = self._graph_node_type(node)
                refs = self._graph_evidence(node, sources)
                label = str(node.get("label") or "").strip()
                if not label or (kind in GRAPH_ENTITY_TYPES and not any(label.casefold() in ref["quote"].casefold() for ref in refs)):
                    raise IntelligenceValidationError("graph entity label is not supported by its source quote")
                attributes = {"eventKind": kind} if kind in GRAPH_EVENT_TYPES else {}
                assertion = {"type": node["type"], "label": label, "attributes": attributes, "evidenceRefs": refs}
                mapping = {"kind": "graph-node", "graphRevisionId": revision_id, "graphElementId": node_id, "proposedConfidence": "medium" if (node.get("payload") or {}).get("confidence") in ("medium", "high") else "low"}
                proposals.append(("node:" + node_id, self._graph_candidate_iri(assessment_id, revision_id, node_id), ONTOLOGY_NS + kind, mapping, assertion))
            for edge_id in dict.fromkeys(edges_selected):
                edge = edges[edge_id]
                relation = edge["relation"]
                if relation not in GRAPH_RELATION_TYPES:
                    raise IntelligenceValidationError("graph context relation has no admitted ontology mapping")
                source_kind, target_kind = self._graph_node_type(nodes[edge["from"]]), self._graph_node_type(nodes[edge["to"]])
                domains, ranges, predicate, inverse = GRAPH_RELATION_TYPES[relation]
                if source_kind not in domains or target_kind not in ranges:
                    raise IntelligenceValidationError("graph relation endpoint types do not match the declared ontology predicate")
                refs = self._graph_evidence(edge, sources)
                # Endpoints need source-backed identity even before their own
                # separate human review. A relation never admits their fields.
                for endpoint in (nodes[edge["from"]], nodes[edge["to"]]):
                    self._graph_evidence(endpoint, sources)
                from_id, to_id = (edge["to"], edge["from"]) if inverse else (edge["from"], edge["to"])
                assertion = {"from": self._graph_candidate_iri(assessment_id, revision_id, from_id), "to": self._graph_candidate_iri(assessment_id, revision_id, to_id), "relation": predicate, "evidenceRefs": refs}
                mapping = {"kind": "graph-relation", "graphRevisionId": revision_id, "graphElementId": edge_id, "proposedConfidence": "medium" if (edge.get("payload") or {}).get("confidence") in ("medium", "high") else "low", "predicate": ONTOLOGY_NS + predicate, "fromGraphNodeId": from_id, "toGraphNodeId": to_id, "requiredCandidateIris": [assertion["from"], assertion["to"]]}
                # This class describes the triple's subject; no fictitious
                # relation individual is inserted into the ontology layer.
                proposals.append(("edge:" + edge_id, INSTANCE_NS + "graph-relation-" + _sha(_json([assessment_id, revision_id, edge_id]))[:20], ONTOLOGY_NS + (target_kind if inverse else source_kind), mapping, assertion))
            try:
                from ontology_attribute_admission import AttributeAdmissionError, build_property_assertion
            except ModuleNotFoundError:
                from .ontology_attribute_admission import AttributeAdmissionError, build_property_assertion
            for selection in properties_selected:
                node_id = selection["graphNodeId"]
                node, path = nodes[node_id], selection["propertyPath"]
                kind = self._graph_node_type(node)
                subject = self._graph_candidate_iri(assessment_id, revision_id, node_id)
                try:
                    predicate, assertion = build_property_assertion(node, selection, sources, kind, subject, self._graph_evidence)
                except AttributeAdmissionError as exc:
                    raise IntelligenceValidationError(str(exc)) from None
                mapping = {"kind": "graph-property", "graphRevisionId": revision_id, "graphElementId": node_id,
                           "propertyPath": path, "predicate": ONTOLOGY_NS + predicate, "subjectIri": subject,
                           "subjectClass": ONTOLOGY_NS + kind, "requiredCandidateIris": [subject], "proposedConfidence": "low"}
                iri = INSTANCE_NS + "graph-property-" + _sha(_json([assessment_id, revision_id, node_id, path, predicate]))[:20]
                proposals.append(("property:" + _sha(_json([node_id, path, predicate]))[:20], iri, ONTOLOGY_NS + "AttributeObservation", mapping, assertion))
            results = []
            for element_key, iri, ontology_class, mapping, assertion in proposals:
                metric_id = "graph:" + revision_id + ":" + element_key
                existing = con.execute("SELECT * FROM intelligence_ontology_admission WHERE assessment_id=? AND metric_id=?", (assessment_id, metric_id)).fetchone()
                if existing:
                    results.append(_row(existing)); continue
                digest = _sha(_json({"assessmentId": assessment_id, "metricId": metric_id, "candidateIri": iri, "ontologyClass": ontology_class, "mapping": mapping, "assertion": assertion, "sourceSnapshotHash": source_hash}))
                admission_id, created = _id("ontology-admission"), _now()
                con.execute("INSERT INTO intelligence_ontology_admission(admission_id,assessment_id,metric_id,revision,status,candidate_iri,ontology_class,mapping,assertion,source_snapshot_hash,content_hash,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (admission_id, assessment_id, metric_id, 1, "under-review", iri, ontology_class, _json(mapping), _json(assertion), source_hash, digest, created, created))
                self._event_tx(con, "OntologyAdmissionProposed", admission_id, {"entityId": admission_id, "entityType": "OntologyAdmission", "assessmentId": assessment_id, "admissionId": admission_id, "kind": mapping["kind"], "graphRevisionId": revision_id, "status": "under-review", "contentHashOrClaimHash": digest}, "intelligence-agent")
                results.append(_row(con.execute("SELECT * FROM intelligence_ontology_admission WHERE admission_id=?", (admission_id,)).fetchone()))
            con.commit()
            return results

    def _check_graph_admission_current(self, con: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
        mapping = _decode(row["mapping"])
        if mapping.get("kind") in {"graph-node", "graph-relation", "graph-property"}:
            if con.execute("SELECT 1 FROM intelligence_ontology_attribute_amendment WHERE previous_admission_id=?", (row["admission_id"],)).fetchone():
                raise IntelligenceConflict("property proposal was superseded; review its replacement candidate")
            latest = con.execute("SELECT graph_revision_id FROM intelligence_graph_revision WHERE assessment_id=? ORDER BY revision DESC LIMIT 1", (row["assessment_id"],)).fetchone()
            if latest is None or latest["graph_revision_id"] != mapping["graphRevisionId"]:
                raise IntelligenceConflict("graph revision changed; create and review a candidate from the latest graph")
            parent = con.execute("SELECT source_snapshot FROM intelligence_assessment WHERE assessment_id=?", (row["assessment_id"],)).fetchone()
            if row["source_snapshot_hash"] != self._snapshot_hash(_decode(parent["source_snapshot"])):
                raise IntelligenceConflict("ontology admission source snapshot changed")
        return mapping

    def create_admissions(self, assessment_id: str, proposals: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not isinstance(proposals, list) or not proposals:
            raise IntelligenceValidationError("admission proposals must be a non-empty array")
        with self._lock, self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT * FROM intelligence_assessment WHERE assessment_id=?", (str(assessment_id),)).fetchone()
            if row is None:
                raise IntelligenceNotFound(f"assessment not found: {assessment_id}")
            assessment = self._assessment(con, row)
            if assessment["status"] == "withdrawn":
                raise IntelligenceConflict("withdrawn assessment cannot create ontology admissions")
            sources = _decode(row["source_snapshot"]) or []
            source_hash = self._snapshot_hash(sources)
            metric_by_id = {
                str(metric.get("id")): metric
                for metric in ((_decode(row["summary"]) or {}).get("capabilityAssessment") or {}).get("metrics") or []
                if isinstance(metric, dict) and str(metric.get("id") or "").strip()
            }
            results = []
            for proposal in proposals:
                if not isinstance(proposal, dict) or not str(proposal.get("metricId") or "").strip():
                    raise IntelligenceValidationError("each admission requires metricId")
                metric_id = str(proposal["metricId"])
                if metric_id not in metric_by_id:
                    raise IntelligenceValidationError("admission metricId is not present in the assessment capability ledger")
                assertion = proposal.get("assertion")
                mapping = proposal.get("mapping")
                if not isinstance(assertion, dict) or not isinstance(mapping, dict):
                    raise IntelligenceValidationError("admission assertion and mapping must be objects")
                evidence = assertion.get("evidence") if isinstance(assertion.get("evidence"), dict) else {}
                if evidence.get("itemId") not in {s.get("itemId") for s in sources}:
                    raise IntelligenceValidationError("admission evidence refers to an unknown source item")
                metric = metric_by_id[metric_id]
                expected_assertion = {key: metric.get(key) for key in ("equipment", "dimension", "metric", "value", "unit", "qualifier", "basis", "conditions", "evidence")}
                if assertion != expected_assertion:
                    raise IntelligenceValidationError("admission assertion must exactly match the stored capability observation")
                try:
                    validate_capabilities((_decode(row["summary"]) or {})["capabilityAssessment"], sources)
                except (KeyError, ValueError) as exc:
                    raise IntelligenceValidationError("admission capability provenance is invalid") from exc
                expected_mapping = {
                    "equipmentIri": f"https://local.ontology/coast-raid-k/intelligence#equipment-{_sha(str(metric['equipment']))[:16]}",
                    "metricName": metric["metric"],
                    "predicate": "https://local.ontology/coast-raid-k#hasCapabilityObservation",
                }
                if mapping != expected_mapping:
                    raise IntelligenceValidationError("admission mapping must use the registered capability vocabulary")
                candidate_iri = str(proposal.get("candidateIri") or f"https://local.ontology/coast-raid-k/intelligence#capability-observation-{_sha(assessment_id + ':' + metric_id)[:16]}")
                ontology_class = str(proposal.get("ontologyClass") or "https://local.ontology/coast-raid-k#CapabilityObservation")
                if (candidate_iri != f"https://local.ontology/coast-raid-k/intelligence#capability-observation-{_sha(assessment_id + ':' + metric_id)[:16]}"
                        or ontology_class != "https://local.ontology/coast-raid-k#CapabilityObservation"):
                    raise IntelligenceValidationError("admission IRI or ontology class does not match its metric mapping")
                canonical = _json({"assessmentId": assessment_id, "metricId": metric_id, "candidateIri": candidate_iri, "ontologyClass": ontology_class, "mapping": mapping, "assertion": assertion, "sourceSnapshotHash": source_hash})
                digest = _sha(canonical)
                existing = con.execute("SELECT * FROM intelligence_ontology_admission WHERE assessment_id=? AND metric_id=?", (assessment_id, metric_id)).fetchone()
                if existing:
                    results.append(_row(existing)); continue
                admission_id = _id("ontology-admission")
                created = _now()
                con.execute("INSERT INTO intelligence_ontology_admission(admission_id,assessment_id,metric_id,revision,status,candidate_iri,ontology_class,mapping,assertion,source_snapshot_hash,content_hash,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (admission_id, assessment_id, metric_id, 1, "under-review", candidate_iri, ontology_class, _json(mapping), _json(assertion), source_hash, digest, created, created))
                self._event_tx(con, "OntologyAdmissionProposed", admission_id, {"entityId": admission_id, "entityType": "OntologyAdmission", "assessmentId": assessment_id, "admissionId": admission_id, "metricId": metric_id, "status": "under-review", "contentHashOrClaimHash": digest}, "intelligence-agent")
                results.append(_row(con.execute("SELECT * FROM intelligence_ontology_admission WHERE admission_id=?", (admission_id,)).fetchone()))
            con.commit()
            return results

    def review_admission(self, admission_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        action = str(payload.get("action") or "")
        reviewer = str(payload.get("reviewer") or "").strip()
        reason = str(payload.get("reason") or "").strip()
        if action not in {"accept", "reject", "request-more-evidence", "modify"} or not reviewer or not reason:
            raise IntelligenceValidationError("admission action, reviewer and reason are required")
        if "expectedRevision" not in payload:
            raise IntelligenceValidationError("expectedRevision is required to prevent stale admission reviews")
        with self._lock, self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT * FROM intelligence_ontology_admission WHERE admission_id=?", (str(admission_id),)).fetchone()
            if row is None: raise IntelligenceNotFound(f"admission not found: {admission_id}")
            parent = self._assessment(con, con.execute("SELECT * FROM intelligence_assessment WHERE assessment_id=?", (row["assessment_id"],)).fetchone())
            if parent and parent.get("status") == "withdrawn": raise IntelligenceConflict("withdrawn assessment admissions are invalidated")
            if row["status"] in {"published", "withdrawn"}: raise IntelligenceConflict("published or withdrawn admissions are immutable")
            expected = _revision(payload["expectedRevision"])
            if expected != int(row["revision"]): raise IntelligenceConflict("admission revision changed; reload before reviewing")
            mapping = self._check_graph_admission_current(con, row)
            if action == "modify":
                return self._amend_property_tx(con, row, mapping, parent, payload, reviewer, reason, expected)
            if "editedProperty" in payload:
                raise IntelligenceValidationError("editedProperty requires action=modify and a new independent review")
            confidence = payload.get("verifiedConfidence")
            if confidence is not None and (not isinstance(confidence, str) or confidence not in {"low", "medium", "high"}):
                raise IntelligenceValidationError("verifiedConfidence must be low, medium or high")
            if mapping.get("kind") in {"graph-node", "graph-relation", "graph-property"} and action == "accept" and confidence != "high":
                raise IntelligenceValidationError("graph ontology acceptance requires explicit human verifiedConfidence=high")
            if action != "accept" and confidence == "high":
                raise IntelligenceValidationError("high verified confidence requires acceptance")
            status = {"accept":"accepted", "reject":"rejected", "request-more-evidence":"needs-evidence"}[action]
            rid, created = _id("admission-review"), _now()
            con.execute("INSERT INTO intelligence_ontology_admission_review(review_id,admission_id,action,reviewer,reason,expected_revision,verified_confidence,created_at) VALUES(?,?,?,?,?,?,?,?)", (rid, admission_id, action, reviewer, reason, expected, confidence, created))
            con.execute("UPDATE intelligence_ontology_admission SET status=?,revision=revision+1,latest_review_id=?,updated_at=? WHERE admission_id=?", (status, rid, created, admission_id))
            self._event_tx(con, "OntologyAdmissionReviewRecorded", admission_id, {"entityId": admission_id, "entityType": "OntologyAdmission", "admissionId": admission_id, "reviewId": rid, "action": action, "status": status, "revision": expected + 1}, reviewer)
            con.commit()
            return next(x for x in self.list_admissions() if x["admissionId"] == admission_id)

    def _amend_property_tx(self, con: sqlite3.Connection, row: sqlite3.Row, mapping: dict,
                           assessment: dict, payload: dict, reviewer: str, reason: str,
                           expected: int) -> dict:
        """Retire a proposal and append a corrected, independently reviewable one."""
        if mapping.get("kind") != "graph-property":
            raise IntelligenceValidationError("modify is supported only for independently reviewed properties")
        if payload.get("verifiedConfidence") is not None:
            raise IntelligenceValidationError("modification requires a separate acceptance review")
        edited = payload.get("editedProperty")
        if not isinstance(edited, dict) or not edited:
            raise IntelligenceValidationError("editedProperty is required for modification")
        try:
            from ontology_attribute_admission import AttributeAdmissionError, build_property_assertion
        except ModuleNotFoundError:
            from .ontology_attribute_admission import AttributeAdmissionError, build_property_assertion
        graph = con.execute("SELECT nodes FROM intelligence_graph_revision WHERE graph_revision_id=?", (mapping["graphRevisionId"],)).fetchone()
        node = next(n for n in _decode(graph["nodes"]) if n["id"] == mapping["graphElementId"])
        node = json.loads(_json(node))
        original = _decode(row["assertion"])
        corrections = {"rawValue": original["rawValue"], "evidenceRefs": original["evidenceRefs"],
                       **{key: original[key] for key in ("normalizedValue", "unit", "conditions", "validTime") if key in original},
                       **edited}
        if "rawValue" in edited and "normalizedValue" not in edited:
            corrections["normalizedValue"] = None
        # An amendment may cite another source already in the report snapshot.
        refs = corrections.get("evidenceRefs")
        if isinstance(refs, list):
            node["evidenceIds"] = list(dict.fromkeys(node["evidenceIds"] + [r.get("itemId") for r in refs if isinstance(r, dict)]))
        sources = assessment.get("sourceSnapshot") or []
        selection = {"propertyPath": mapping["propertyPath"], "ontologyPredicate": mapping["predicate"]}
        try:
            predicate, assertion = build_property_assertion(node, selection, sources, self._graph_node_type(node), mapping["subjectIri"], self._graph_evidence, edited=corrections)
        except AttributeAdmissionError as exc:
            raise IntelligenceValidationError(str(exc)) from None
        if "normalizedValue" in assertion and "normalizedValue" not in edited:
            assertion["normalizationBasis"] = original.get("normalizationBasis", "model-translation")
        admission_id, created = _id("ontology-admission"), _now()
        revised_mapping = {**mapping, "predicate": ONTOLOGY_NS + predicate, "supersedesAdmissionId": row["admission_id"]}
        metric_id = "amended-property:" + admission_id
        iri = INSTANCE_NS + "graph-property-" + _sha(_json([row["candidate_iri"], admission_id]))[:20]
        digest = _sha(_json({"assessmentId": row["assessment_id"], "metricId": metric_id, "candidateIri": iri,
                            "ontologyClass": row["ontology_class"], "mapping": revised_mapping, "assertion": assertion,
                            "sourceSnapshotHash": row["source_snapshot_hash"]}))
        con.execute("INSERT INTO intelligence_ontology_admission(admission_id,assessment_id,metric_id,revision,status,candidate_iri,ontology_class,mapping,assertion,source_snapshot_hash,content_hash,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (admission_id, row["assessment_id"], metric_id, 1, "under-review", iri, row["ontology_class"], _json(revised_mapping), _json(assertion), row["source_snapshot_hash"], digest, created, created))
        review_id = _id("admission-review")
        con.execute("INSERT INTO intelligence_ontology_admission_review(review_id,admission_id,action,reviewer,reason,expected_revision,created_at) VALUES(?,?,?,?,?,?,?)", (review_id, row["admission_id"], "reject", reviewer, reason, expected, created))
        con.execute("UPDATE intelligence_ontology_admission SET status='rejected',revision=revision+1,latest_review_id=?,updated_at=? WHERE admission_id=?", (review_id, created, row["admission_id"]))
        con.execute("INSERT INTO intelligence_ontology_attribute_amendment VALUES(?,?,?,?,?)", (row["admission_id"], admission_id, reviewer, reason, created))
        self._event_tx(con, "OntologyPropertyAmended", row["admission_id"], {"entityId": row["admission_id"], "entityType": "OntologyAdmission", "previousAdmissionId": row["admission_id"], "replacementAdmissionId": admission_id, "graphRevisionId": mapping["graphRevisionId"], "propertyPath": mapping["propertyPath"], "previousAssertion": original, "replacementAssertion": assertion, "reason": reason}, reviewer)
        con.commit()
        return next(item for item in self.list_admissions(row["assessment_id"]) if item["admissionId"] == admission_id)

    def publish_admission(self, admission_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        publisher = str(payload.get("reviewer") or payload.get("publishedBy") or "").strip()
        if not publisher or "expectedRevision" not in payload:
            raise IntelligenceValidationError("reviewer and expectedRevision are required to publish admission")
        with self._lock, self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT * FROM intelligence_ontology_admission WHERE admission_id=?", (str(admission_id),)).fetchone()
            if row is None: raise IntelligenceNotFound(f"admission not found: {admission_id}")
            parent = self._assessment(con, con.execute("SELECT * FROM intelligence_assessment WHERE assessment_id=?", (row["assessment_id"],)).fetchone())
            if parent and parent.get("status") == "withdrawn": raise IntelligenceConflict("withdrawn assessment admissions are invalidated")
            if _revision(payload["expectedRevision"]) != int(row["revision"]): raise IntelligenceConflict("admission revision changed; reload before publishing")
            if row["status"] != "accepted": raise IntelligenceConflict("ontology admission publication requires human acceptance")
            accepted = con.execute("SELECT * FROM intelligence_ontology_admission_review WHERE review_id=? AND admission_id=? AND action='accept'", (row["latest_review_id"], admission_id)).fetchone()
            if not accepted or int(accepted["expected_revision"]) + 1 != int(row["revision"]):
                raise IntelligenceConflict("admission acceptance review is missing or stale")
            mapping = self._check_graph_admission_current(con, row)
            if mapping.get("kind") in {"graph-node", "graph-relation", "graph-property"} and accepted["verified_confidence"] != "high":
                raise IntelligenceConflict("graph ontology publication requires human-verified high confidence")
            # Re-run the exact source-locator check at the publication
            # boundary.  Proposal-time validation is not enough: a source
            # snapshot can be replaced or a legacy assertion can lack a
            # locator while it waits in the review queue.
            parent_source_row = con.execute("SELECT source_snapshot FROM intelligence_assessment WHERE assessment_id=?", (row["assessment_id"],)).fetchone()
            source_items = _decode(parent_source_row["source_snapshot"]) if parent_source_row else []
            evidence_ok, evidence_detail = self._audit_evidence(_decode(row["assertion"]) or {}, source_items or [])
            if not evidence_ok:
                raise IntelligenceConflict("ontology admission publication requires complete source evidence: " + evidence_detail)
            if mapping.get("kind") in {"graph-relation", "graph-property"}:
                published_nodes = {
                    candidate["candidate_iri"]
                    for candidate in con.execute("SELECT * FROM intelligence_ontology_admission WHERE assessment_id=? AND status='published'", (row["assessment_id"],))
                    if (_decode(candidate["mapping"])).get("kind") == "graph-node" and (_decode(candidate["mapping"])).get("graphRevisionId") == mapping["graphRevisionId"]
                }
                if not set(mapping["requiredCandidateIris"]).issubset(published_nodes):
                    raise IntelligenceConflict("publish independently reviewed subject/endpoint objects before publishing their property or relation")
            published = _now()
            projection = {"versionId": admission_id, "publisher": publisher, "createdAt": published}
            con.execute("UPDATE intelligence_ontology_admission SET status='published',publication=?,updated_at=? WHERE admission_id=?", (_json(projection), published, admission_id))
            self._event_tx(con, "OntologyAdmissionPublished", admission_id, {"entityId": admission_id, "entityType": "OntologyAdmission", "admissionId": admission_id, "status": "published", "publishedBy": publisher, "candidateIri": row["candidate_iri"]}, publisher)
            con.commit()
            result = self.list_admissions()
            return next(x for x in result if x["admissionId"] == admission_id)

    def published_ontology(self, *, detailed: bool = False) -> dict[str, Any]:
        """Read the human-admitted fact layer for the ontology browser.

        The base ontology package stays versioned and untouched.  The live
        layer is an explicit extension containing source-qualified atomic
        observations, never whole trend reports or unlabeled model claims.
        """
        admitted = [admission for admission in self.list_admissions() if admission["status"] == "published"]
        # Subjects are projected before their independently admitted properties.
        admitted.sort(key=lambda admission: admission["kind"] == "graph-property")
        objects: dict[str, dict[str, Any]] = {}
        relations: list[dict[str, Any]] = []
        provenance: list[dict[str, Any]] = []
        for admission in admitted:
            assertion, mapping = admission["assertion"], admission["mapping"]
            if admission["kind"] == "graph-property":
                subject = assertion["subject"]
                if subject not in objects:
                    raise IntelligenceValidationError("published property has no published subject")
                iri = admission["candidateIri"]
                observation = {"id": iri, "iri": iri, "type": "AttributeObservation", "ontologyClass": ONTOLOGY_NS + "AttributeObservation",
                               "label": assertion["subjectLabel"] + " · " + assertion["propertyName"],
                               "assertion": {**assertion, "normalizedValueVerified": "normalizedValue" in assertion},
                               "admissionId": admission["admissionId"], "assessmentId": admission["assessmentId"],
                               "publication": admission["publication"], "verifiedConfidence": "high"}
                objects[iri] = observation
                property_record = {"admissionId": admission["admissionId"], "observationIri": iri, "propertyPath": mapping["propertyPath"],
                                   "predicate": mapping["predicate"], **assertion, "verifiedConfidence": "high",
                                   "normalizedValueVerified": "normalizedValue" in assertion}
                objects[subject].setdefault("admittedProperties", []).append(property_record)
                # Multiple sources may disagree. Keep source-qualified values
                # as lists rather than silently overwriting the subject value.
                name = mapping["predicate"].split("#")[-1]
                objects[subject].setdefault("attributes", {}).setdefault(name, []).append(assertion["rawValue"])
                relations.append({"id": admission["admissionId"] + ":attribute", "from": subject, "to": iri,
                                  "relation": "hasAttributeObservation", "predicate": ONTOLOGY_NS + "hasAttributeObservation",
                                  "admissionId": admission["admissionId"], "verifiedConfidence": "high"})
                provenance.append({"admissionId": admission["admissionId"], "assessmentId": admission["assessmentId"],
                                   "graphRevisionId": mapping["graphRevisionId"], "graphElementId": mapping["graphElementId"],
                                   "propertyPath": mapping["propertyPath"], "sourceSnapshotHash": admission["sourceSnapshotHash"],
                                   "evidenceRefs": assertion["evidenceRefs"], "identityEvidenceRefs": assertion["identityEvidenceRefs"],
                                   "review": admission.get("review"), "publication": admission["publication"]})
                continue
            if admission["kind"] in {"graph-node", "graph-relation"}:
                if admission["kind"] == "graph-node":
                    iri = admission["candidateIri"]
                    objects[iri] = {"id": iri, "iri": iri, "type": admission["ontologyClass"].split("#")[-1], "ontologyClass": admission["ontologyClass"], "label": assertion["label"], "attributes": assertion["attributes"], "assertion": assertion, "admissionId": admission["admissionId"], "assessmentId": admission["assessmentId"], "publication": admission["publication"], "verifiedConfidence": admission["review"]["verifiedConfidence"]}
                else:
                    relations.append({"id": admission["candidateIri"], "from": assertion["from"], "to": assertion["to"], "relation": assertion["relation"], "predicate": mapping["predicate"], "admissionId": admission["admissionId"], "verifiedConfidence": admission["review"]["verifiedConfidence"]})
                provenance.append({"admissionId": admission["admissionId"], "assessmentId": admission["assessmentId"], "graphRevisionId": mapping["graphRevisionId"], "sourceSnapshotHash": admission["sourceSnapshotHash"], "evidenceRefs": assertion["evidenceRefs"], "review": admission.get("review"), "publication": admission["publication"]})
                continue
            equipment_iri, observation_iri = mapping["equipmentIri"], admission["candidateIri"]
            objects.setdefault(equipment_iri, {"id": equipment_iri, "iri": equipment_iri, "type": "EquipmentModel", "ontologyClass": "https://local.ontology/coast-raid-k#EquipmentModel", "label": assertion["equipment"], "admissionIds": []})["admissionIds"].append(admission["admissionId"])
            objects[observation_iri] = {
                "id": observation_iri, "iri": observation_iri, "type": "CapabilityObservation",
                "ontologyClass": admission["ontologyClass"],
                "label": f"{assertion['equipment']} · {assertion['metric']} {assertion['value']} {assertion['unit']}",
                "assertion": assertion, "admissionId": admission["admissionId"],
                "assessmentId": admission["assessmentId"], "publication": admission["publication"],
            }
            relations.append({"id": f"{admission['admissionId']}:capability", "from": equipment_iri, "to": observation_iri, "relation": "hasCapabilityObservation", "predicate": mapping["predicate"], "admissionId": admission["admissionId"]})
            provenance.append({"admissionId": admission["admissionId"], "assessmentId": admission["assessmentId"], "sourceSnapshotHash": admission["sourceSnapshotHash"], "evidence": assertion["evidence"], "review": admission.get("review"), "publication": admission["publication"]})
        try:
            from capability_profile import published_capability_ontology
        except ModuleNotFoundError:
            from .capability_profile import published_capability_ontology
        capability_layer=published_capability_ontology(self, detailed=detailed)
        for record in capability_layer['objects']:
            objects.setdefault(record['iri'],record)
        return {"namespace": "https://local.ontology/coast-raid-k/intelligence#", "objects": list(objects.values()), "relations": relations+capability_layer['relations'], "provenance": provenance+capability_layer['provenance'], "admissionCount": len(admitted)+capability_layer['admissionCount'], "detailLevel": capability_layer.get('detailLevel', 'compatibility')}

    def review_assessment(self, assessment_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        action = str(payload.get("action") or "")
        reviewer = str(payload.get("reviewer") or "").strip()
        reason = str(payload.get("reason") or "").strip()
        if action not in {"accept", "reject", "request-more-evidence"}:
            raise IntelligenceValidationError("assessment action must be accept, reject or request-more-evidence")
        if not reviewer or not reason:
            raise IntelligenceValidationError("reviewer and reason are required")
        if "expectedRevision" not in payload:
            raise IntelligenceValidationError("expectedRevision is required to prevent stale reviews")
        with self._lock, self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT * FROM intelligence_assessment WHERE assessment_id=?", (str(assessment_id),)).fetchone()
            if row is None:
                raise IntelligenceNotFound(f"assessment not found: {assessment_id}")
            if row["status"] == "published":
                raise IntelligenceConflict("published assessments are immutable")
            try:
                expected = int(payload.get("expectedRevision"))
            except (TypeError, ValueError) as exc:
                raise IntelligenceValidationError("expectedRevision must be an integer") from exc
            if expected != int(row["revision"]):
                raise IntelligenceConflict("assessment revision changed; reload before reviewing")
            status = {"accept": "accepted", "reject": "rejected", "request-more-evidence": "needs-evidence"}[action]
            review_id = _id("assessment-review")
            created = _now()
            con.execute("INSERT INTO intelligence_assessment_review(review_id,assessment_id,action,reviewer,reason,expected_revision,created_at) VALUES(?,?,?,?,?,?,?)", (review_id, assessment_id, action, reviewer, reason, expected, created))
            con.execute("UPDATE intelligence_assessment SET status=?,latest_review_id=?,updated_at=?,revision=revision+1 WHERE assessment_id=?", (status, review_id, created, assessment_id))
            self._event_tx(con, "TrendAssessmentReviewRecorded", assessment_id, {
                "entityId": assessment_id, "entityType": "IntelligenceAssessment", "status": status,
                "assessmentId": assessment_id, "reviewId": review_id, "action": action,
                "revision": expected + 1,
            }, reviewer)
            con.commit()
            return self.get_assessment(assessment_id)  # type: ignore[return-value]

    def publish_assessment(self, assessment_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        publisher = str(payload.get("reviewer") or payload.get("publishedBy") or "").strip()
        if not publisher:
            raise IntelligenceValidationError("reviewer is required to publish an assessment")
        if "expectedRevision" not in payload:
            raise IntelligenceValidationError("expectedRevision is required to prevent stale publication")
        with self._lock, self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT * FROM intelligence_assessment WHERE assessment_id=?", (str(assessment_id),)).fetchone()
            if row is None:
                raise IntelligenceNotFound(f"assessment not found: {assessment_id}")
            try:
                expected = int(payload.get("expectedRevision"))
            except (TypeError, ValueError) as exc:
                raise IntelligenceValidationError("expectedRevision must be an integer") from exc
            if expected != int(row["revision"]):
                raise IntelligenceConflict("assessment revision changed; reload before publishing")
            if row["status"] != "accepted":
                raise IntelligenceConflict("assessment publication blocked: human acceptance is required")
            accepted = con.execute("SELECT 1 FROM intelligence_assessment_review WHERE assessment_id=? AND action='accept' ORDER BY created_at DESC LIMIT 1", (assessment_id,)).fetchone()
            if not accepted:
                raise IntelligenceConflict("assessment publication blocked: acceptance review is missing")
            published = _now()
            con.execute("UPDATE intelligence_assessment SET status='published',published_at=?,published_by=?,updated_at=? WHERE assessment_id=?", (published, publisher, published, assessment_id))
            self._event_tx(con, "TrendAssessmentPublished", assessment_id, {
                "entityId": assessment_id, "entityType": "IntelligenceAssessment", "status": "published",
                "assessmentId": assessment_id, "taskId": row["task_id"], "publishedBy": publisher,
            }, publisher)
            con.commit()
            return self.get_assessment(assessment_id)  # type: ignore[return-value]

    def review_candidate(self, candidate_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        action = str(payload.get("action") or "")
        reviewer = str(payload.get("reviewer") or "").strip()
        if action not in {"accept", "reject", "request-more-evidence", "edit"}:
            raise IntelligenceValidationError("action must be accept, reject, request-more-evidence or edit")
        if not reviewer:
            raise IntelligenceValidationError("reviewer is required")
        with self._lock, self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT * FROM intelligence_candidate WHERE candidate_id = ?", (str(candidate_id),)).fetchone()
            if row is None:
                raise IntelligenceNotFound(f"candidate not found: {candidate_id}")
            candidate = _row(row)
            if candidate["status"] in {"published", "superseded"}:
                raise IntelligenceConflict(f"candidate is immutable after publication/supersession: {candidate_id}")
            if "expectedRevision" not in payload:
                raise IntelligenceValidationError("expectedRevision is required to prevent stale reviews")
            expected = payload.get("expectedRevision")
            if expected is None or int(expected) != int(candidate["revision"]):
                raise IntelligenceConflict("candidate revision changed; reload before reviewing")
            reason = str(payload.get("reason") or "").strip()
            if not reason:
                raise IntelligenceValidationError("reason is required for an ingestion review")
            edited = payload.get("editedStatement")
            if action == "edit" and not isinstance(edited, str) or action == "edit" and not edited.strip():
                raise IntelligenceValidationError("editedStatement is required for edit")
            new_statement = str(edited).strip() if action == "edit" else candidate["statement"]
            # Editing creates a new pending revision; a second explicit
            # acceptance is required before publication.
            status = {"accept": "accepted", "reject": "rejected", "request-more-evidence": "needs-evidence", "edit": "under-review"}[action]
            revision = int(candidate["revision"]) + (1 if action == "edit" else 0)
            subject, predicate, value, match_key = _parse_statement(new_statement, candidate)
            review_id = _id("review")
            created = _now()
            resolve = payload.get("resolveConflict")
            if resolve is not None and (resolve not in {"replace-existing", "keep-both", "dismiss"} or action != "accept"):
                raise IntelligenceValidationError("conflict resolution requires accept and a supported resolution")
            con.execute("INSERT INTO intelligence_review(review_id,candidate_id,action,reviewer,reason,edited_statement,expected_revision,resolve_conflict,created_at) VALUES(?,?,?,?,?,?,?,?,?)", (review_id, candidate_id, action, reviewer, reason, edited if action == "edit" else None, expected, resolve, created))
            con.execute("UPDATE intelligence_candidate SET statement = ?, revision = ?, subject = ?, predicate = ?, value = ?, match_key = ?, status = ?, latest_review_id = ?, updated_at = ? WHERE candidate_id = ?", (new_statement, revision, subject, predicate, value, match_key, status, review_id, created, candidate_id))
            if action == "edit":
                con.execute("INSERT INTO intelligence_candidate_revision(candidate_id,revision,statement,subject,predicate,value,evidence,created_at) VALUES(?,?,?,?,?,?,?,?)", (candidate_id, revision, new_statement, subject, predicate, value, _json(candidate.get("evidence") or {}), created))
            if resolve and resolve in {"replace-existing", "keep-both", "dismiss"}:
                active_conflicts = [c for c in self._conflicts_for_candidate_tx(con, candidate_id) if c["status"] in {"open", "unresolved"}]
                conflict_ids = [c["conflictId"] for c in active_conflicts]
                if conflict_ids:
                    conflict_status = "dismissed" if resolve == "dismiss" else "resolved"
                    con.executemany("UPDATE intelligence_conflict SET status=?, resolution=?, resolved_by=?, resolved_at=? WHERE conflict_id=?", [(conflict_status, str(resolve), reviewer, created, conflict_id) for conflict_id in conflict_ids])
                    if resolve == "replace-existing":
                        for conflict in active_conflicts:
                            for other in conflict.get("candidateIds") or []:
                                if other != candidate_id:
                                        con.execute("UPDATE intelligence_candidate SET status='superseded', updated_at=? WHERE candidate_id=? AND status IN ('accepted','edited','under-review','pending-review','needs-evidence','new')", (created, other))
            self._event_tx(con, "IngestionReviewRecorded", candidate_id, {"entityId": candidate_id, "entityType": "KnowledgeCandidate", "status": status, "contentHashOrClaimHash": _sha(new_statement), "reviewId": review_id, "candidateId": candidate_id, "action": action, "revision": revision, "resolveConflict": resolve}, reviewer)
            con.commit()
            return self.get_candidate(candidate_id)  # type: ignore[return-value]

    def _conflicts_for_candidate_tx(self, con: sqlite3.Connection, candidate_id: str) -> list[dict[str, Any]]:
        rows = con.execute("SELECT * FROM intelligence_conflict WHERE candidate_ids LIKE ?", (f'%"{candidate_id}"%',)).fetchall()
        return [_row(row) for row in rows]

    def unresolved_conflicts_for(self, candidate_id: str) -> list[dict[str, Any]]:
        with self._connect() as con:
            return [_row(row) for row in con.execute("SELECT * FROM intelligence_conflict WHERE status IN ('open','unresolved') AND candidate_ids LIKE ?", (f'%"{candidate_id}"%',))]

    def add_conflict(self, match_key: str, candidate_ids: list[str], reason: str) -> dict[str, Any]:
        unique = sorted(set(str(x) for x in candidate_ids))
        if len(unique) < 2:
            raise IntelligenceValidationError("a conflict needs at least two candidates")
        with self._lock, self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            existing = con.execute("SELECT * FROM intelligence_conflict WHERE match_key=? AND status IN ('open','unresolved')", (match_key,)).fetchone()
            if existing:
                old_ids = set(_decode(existing["candidate_ids"]) or [])
                if set(unique).issubset(old_ids):
                    con.commit()
                    return _row(existing)  # type: ignore[return-value]
            conflict_id = _id("conflict")
            created = _now()
            con.execute("INSERT INTO intelligence_conflict(conflict_id,match_key,candidate_ids,status,reason,created_at) VALUES(?,?,?,?,?,?)", (conflict_id, match_key, _json(unique), "open", reason, created))
            self._event_tx(con, "CandidateConflictDetected", conflict_id, {"entityId": conflict_id, "entityType": "ConflictSet", "status": "open", "contentHashOrClaimHash": _sha(_json(unique)), "conflictId": conflict_id, "matchKey": match_key, "candidateIds": unique, "reason": reason}, "intelligence-agent")
            result = _row(con.execute("SELECT * FROM intelligence_conflict WHERE conflict_id=?", (conflict_id,)).fetchone())
            con.commit()
            return result  # type: ignore[return-value]

    def publish(self, payload: dict[str, Any]) -> dict[str, Any]:
        publisher = str(payload.get("reviewer") or payload.get("publishedBy") or "").strip()
        if not publisher:
            raise IntelligenceValidationError("reviewer is required to publish")
        requested = payload.get("candidateIds")
        with self._lock, self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            if requested is None:
                rows = con.execute("SELECT * FROM intelligence_candidate WHERE status IN ('accepted','edited') ORDER BY candidate_id").fetchall()
            else:
                if not isinstance(requested, list) or not requested:
                    raise IntelligenceValidationError("candidateIds must be a non-empty array")
                marks = ",".join("?" for _ in requested)
                rows = con.execute(f"SELECT * FROM intelligence_candidate WHERE candidate_id IN ({marks}) ORDER BY candidate_id", [str(x) for x in requested]).fetchall()
            if not rows:
                raise IntelligenceValidationError("no reviewed candidates are ready for publication")
            selected_ids = [row["candidate_id"] for row in rows]
            missing = set(str(x) for x in (requested or [])) - set(selected_ids)
            if missing:
                raise IntelligenceNotFound(f"candidate(s) not found: {sorted(missing)}")
            blocked = []
            for row in rows:
                if row["status"] not in {"accepted"}:
                    blocked.append(f"{row['candidate_id']}:status={row['status']}")
                if not con.execute("SELECT 1 FROM intelligence_review WHERE candidate_id=? AND action IN ('accept','edit') ORDER BY created_at DESC LIMIT 1", (row["candidate_id"],)).fetchone():
                    blocked.append(f"{row['candidate_id']}:missing-review")
                if con.execute("SELECT 1 FROM intelligence_conflict WHERE status IN ('open','unresolved') AND candidate_ids LIKE ?", (f'%"{row["candidate_id"]}"%',)).fetchone():
                    blocked.append(f"{row['candidate_id']}:unresolved-conflict")
            if blocked:
                raise IntelligenceConflict("publication blocked: " + ", ".join(blocked))
            snapshot = []
            for row in rows:
                candidate = _row(row)
                snapshot.append(candidate)
            snapshot_json = _json({"candidates": snapshot})
            digest = _sha(snapshot_json)
            prior = con.execute("SELECT COUNT(*) FROM intelligence_version").fetchone()[0]
            version = f"v{int(prior) + 1:04d}"
            version_id = _id("knowledge-version")
            published_at = _now()
            con.execute("INSERT INTO intelligence_version(version_id,version,content_hash,candidate_ids,snapshot,published_by,title,published_at) VALUES(?,?,?,?,?,?,?,?)", (version_id, version, digest, _json(selected_ids), snapshot_json, publisher, payload.get("title"), published_at))
            con.executemany("UPDATE intelligence_candidate SET status='published', updated_at=? WHERE candidate_id=?", [(published_at, candidate_id) for candidate_id in selected_ids])
            self._event_tx(con, "KnowledgeVersionPublished", version_id, {"entityId": version_id, "entityType": "PublishedKnowledgeVersion", "status": "published", "contentHashOrClaimHash": digest, "versionId": version_id, "version": version, "contentHash": digest, "candidateIds": selected_ids}, publisher)
            con.commit()
            return self.get_version(version_id)  # type: ignore[return-value]

    def get_version(self, version_id: str) -> dict[str, Any] | None:
        with self._connect() as con:
            return _row(con.execute("SELECT * FROM intelligence_version WHERE version_id=?", (str(version_id),)).fetchone())

    def state(self) -> dict[str, Any]:
        docs = self.list_documents()
        candidates = self.list_candidates()
        conflicts = self.list_conflicts()
        reviews = self.list_reviews()
        versions = self.list_versions()
        events = self.list_events()
        assessments = self.list_assessments()
        assessment_reviews = self.list_assessment_reviews()
        graphs = self.list_graph_revisions()
        validation_rounds = self.list_validation_rounds()
        admissions = self.list_admissions()
        return {"documents": docs, "candidates": candidates, "claims": [self._candidate_claim(c) for c in candidates], "conflicts": conflicts, "reviews": reviews, "revisions": self.list_candidate_revisions(), "versions": versions, "assessments": assessments, "assessmentReviews": assessment_reviews, "graphs": graphs, "validationRounds": validation_rounds, "admissions": admissions, "events": events, "summary": {"documents": len(docs), "candidates": len(candidates), "pendingReview": sum(c["status"] in {"pending-review", "under-review", "new"} for c in candidates), "unresolvedConflicts": sum(c["status"] in {"open", "unresolved"} for c in conflicts), "publishedVersions": len(versions), "assessments": len(assessments), "assessmentsPendingReview": sum(a["status"] == "under-review" for a in assessments), "graphRevisions": len(graphs), "validationRounds": len(validation_rounds), "ontologyAdmissions": len(admissions)}}

    @staticmethod
    def _candidate_claim(candidate: dict[str, Any]) -> dict[str, Any]:
        """Expose each extracted candidate as an EvidenceClaim projection."""
        status = candidate.get("status")
        if status in {"under-review", "new", "pending-review", "needs-evidence", "edited"}:
            status = "under-review"
        elif status == "published":
            status = "accepted"
        recommendation = candidate.get("recommendation") or {}
        if not isinstance(recommendation, dict):
            recommendation = {}
        is_llm = recommendation.get("provider") == "deepseek-claim-provider"
        model = recommendation.get("model")
        return {
            "id": candidate["claimId"], "type": "EvidenceClaim", "documentId": candidate["documentId"],
            "statement": candidate["statement"], "subject": candidate.get("subject") or "unstructured",
            "predicate": candidate.get("predicate") or "asserts", "value": candidate.get("value"),
            "evidence": candidate.get("evidence"), "claimHash": _sha(_json({"statement": candidate["statement"], "evidence": candidate.get("evidence")})),
            "extractionMethod": "llm" if is_llm else "deterministic",
            "extractorAgent": "agent.intelligence.deepseek" if is_llm else "agent.intelligence.deterministic",
            **({"modelId": model} if is_llm and isinstance(model, str) and model else {}),
            "confidence": 0.6 if candidate.get("uncertainty") else 0.8, "status": status,
            "createdAt": candidate["createdAt"], "provenance": [candidate["documentId"]],
        }


__all__ = ["IntelligenceStore", "IntelligenceStoreError", "IntelligenceNotFound", "IntelligenceConflict", "IntelligenceValidationError", "DEFAULT_INTELLIGENCE_DB"]
