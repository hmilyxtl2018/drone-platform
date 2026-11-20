-- Attribute candidates proposed from object-directed public evidence.
-- These rows are review work items, not trusted ontology facts.

CREATE TABLE IF NOT EXISTS ontology_attribute_candidate (
  candidate_id TEXT PRIMARY KEY,
  entity_id TEXT NOT NULL REFERENCES ontology_entity(entity_id),
  attribute_key TEXT NOT NULL,
  current_value TEXT,
  proposed_value TEXT NOT NULL,
  unit TEXT,
  conditions TEXT,
  confidence REAL CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
  status TEXT NOT NULL DEFAULT 'under-review'
    CHECK (status IN ('under-review','accepted','rejected','needs-evidence','edited','withdrawn')),
  source_job_id TEXT REFERENCES ontology_verification_job(verification_id),
  evidence_json TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(evidence_json)),
  model_provider TEXT NOT NULL,
  model TEXT,
  trace_json TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(trace_json)),
  content_hash TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ontology_attribute_candidate_entity_idx
  ON ontology_attribute_candidate(entity_id, status, attribute_key);

CREATE TABLE IF NOT EXISTS ontology_attribute_candidate_review (
  review_id TEXT PRIMARY KEY,
  candidate_id TEXT NOT NULL REFERENCES ontology_attribute_candidate(candidate_id),
  action TEXT NOT NULL CHECK (action IN ('accept','reject','request-more-evidence','edit')),
  reviewer TEXT NOT NULL,
  reason TEXT NOT NULL,
  edited_value TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ontology_attribute_candidate_review_idx
  ON ontology_attribute_candidate_review(candidate_id, created_at);
