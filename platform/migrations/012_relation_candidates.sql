-- Model-proposed entity relations remain separate from ontology assertions.
CREATE TABLE IF NOT EXISTS ontology_relation_candidate (
  candidate_id TEXT PRIMARY KEY,
  entity_id TEXT NOT NULL REFERENCES ontology_entity(entity_id),
  target_entity_id TEXT REFERENCES ontology_entity(entity_id),
  target_name TEXT NOT NULL,
  predicate TEXT NOT NULL,
  target_type TEXT,
  qualifier_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(qualifier_json)),
  confidence REAL CHECK(confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
  status TEXT NOT NULL DEFAULT 'under-review' CHECK(status IN ('under-review','accepted','rejected','needs-evidence','withdrawn','published')),
  source_job_id TEXT REFERENCES ontology_verification_job(verification_id),
  evidence_json TEXT NOT NULL DEFAULT '[]' CHECK(json_valid(evidence_json)),
  model_provider TEXT NOT NULL,
  model TEXT,
  trace_json TEXT NOT NULL DEFAULT '[]' CHECK(json_valid(trace_json)),
  content_hash TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ontology_relation_candidate_entity_idx
  ON ontology_relation_candidate(entity_id,status,predicate);
CREATE TABLE IF NOT EXISTS ontology_relation_candidate_review (
  review_id TEXT PRIMARY KEY,
  candidate_id TEXT NOT NULL REFERENCES ontology_relation_candidate(candidate_id),
  action TEXT NOT NULL CHECK(action IN ('accept','reject','request-more-evidence')),
  reviewer TEXT NOT NULL,
  reason TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ontology_relation_candidate_review_idx
  ON ontology_relation_candidate_review(candidate_id,created_at);
