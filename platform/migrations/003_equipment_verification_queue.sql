-- Bounded, object-directed public evidence collection jobs. Results remain
-- review candidates and never mutate an accepted/published assertion.
CREATE TABLE IF NOT EXISTS ontology_verification_job (
  verification_id TEXT PRIMARY KEY,
  entity_id TEXT NOT NULL REFERENCES ontology_entity(entity_id),
  entity_revision_id TEXT NOT NULL REFERENCES ontology_entity_revision(entity_revision_id),
  target_type TEXT NOT NULL CHECK (target_type IN ('identity','performance','systems','collaboration','industrial','combat','imagery')),
  attribute_keys_json TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(attribute_keys_json)),
  query_text TEXT NOT NULL,
  source_scope_ids TEXT NOT NULL CHECK (json_valid(source_scope_ids)),
  max_items INTEGER NOT NULL CHECK (max_items BETWEEN 1 AND 100),
  since_days INTEGER NOT NULL CHECK (since_days BETWEEN 0 AND 3650),
  status TEXT NOT NULL DEFAULT 'planned'
    CHECK (status IN ('planned','running','completed','needs-review','failed','cancelled')),
  result_json TEXT CHECK (result_json IS NULL OR json_valid(result_json)),
  error_message TEXT,
  created_at TEXT NOT NULL,
  started_at TEXT,
  completed_at TEXT,
  UNIQUE(entity_revision_id, target_type)
);
CREATE INDEX IF NOT EXISTS ontology_verification_job_status_idx
  ON ontology_verification_job(status, target_type, created_at);
CREATE INDEX IF NOT EXISTS ontology_verification_job_entity_idx
  ON ontology_verification_job(entity_id, entity_revision_id, target_type);
