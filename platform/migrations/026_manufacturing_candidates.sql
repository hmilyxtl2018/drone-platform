-- Imported production/cost facts remain candidates until a human review and
-- explicit publication creates an immutable-looking published record.
CREATE TABLE IF NOT EXISTS ontology_manufacturing_candidate (
  candidate_id TEXT PRIMARY KEY,
  entity_id TEXT NOT NULL,
  candidate_type TEXT NOT NULL CHECK(candidate_type IN ('production','cost')),
  payload_json TEXT NOT NULL CHECK(json_valid(payload_json) AND json_type(payload_json)='object'),
  evidence_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(evidence_json)),
  source_snapshot_ids_json TEXT NOT NULL DEFAULT '[]' CHECK(json_valid(source_snapshot_ids_json)),
  content_hash TEXT NOT NULL UNIQUE,
  status TEXT NOT NULL DEFAULT 'under-review' CHECK(status IN ('under-review','accepted','rejected','needs-evidence','published')),
  source_import_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ontology_manufacturing_candidate_entity_idx
  ON ontology_manufacturing_candidate(entity_id, candidate_type, status, created_at DESC);

CREATE TABLE IF NOT EXISTS ontology_manufacturing_candidate_review (
  review_id TEXT PRIMARY KEY,
  candidate_id TEXT NOT NULL REFERENCES ontology_manufacturing_candidate(candidate_id),
  action TEXT NOT NULL CHECK(action IN ('accept','reject','request-more-evidence','edit')),
  reviewer TEXT NOT NULL,
  reason TEXT NOT NULL,
  edited_payload_json TEXT CHECK(edited_payload_json IS NULL OR json_valid(edited_payload_json)),
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ontology_manufacturing_candidate_review_idx
  ON ontology_manufacturing_candidate_review(candidate_id, created_at DESC);
