-- External references cited by an imported workbook. These references are
-- review candidates: they do not verify an ontology assertion by themselves.
CREATE TABLE IF NOT EXISTS ontology_source_import_run (
  source_import_id TEXT PRIMARY KEY,
  snapshot_id TEXT NOT NULL REFERENCES ontology_import_snapshot(snapshot_id),
  parser_version TEXT NOT NULL,
  source_count INTEGER NOT NULL DEFAULT 0 CHECK (source_count >= 0),
  matched_count INTEGER NOT NULL DEFAULT 0 CHECK (matched_count >= 0),
  unmatched_count INTEGER NOT NULL DEFAULT 0 CHECK (unmatched_count >= 0),
  status TEXT NOT NULL CHECK (status IN ('running','completed','partial','failed')),
  error_message TEXT,
  created_at TEXT NOT NULL,
  completed_at TEXT,
  UNIQUE(snapshot_id, parser_version)
);

CREATE TABLE IF NOT EXISTS ontology_source_reference (
  source_reference_id TEXT PRIMARY KEY,
  snapshot_id TEXT NOT NULL REFERENCES ontology_import_snapshot(snapshot_id),
  entity_id TEXT REFERENCES ontology_entity(entity_id),
  source_record_id TEXT REFERENCES ontology_raw_record(raw_record_id),
  source_scope TEXT NOT NULL CHECK (source_scope IN ('identity','combat-event','loss-record','image','workbook-general')),
  subject_name TEXT,
  source_name TEXT NOT NULL,
  source_url TEXT,
  citation_text TEXT NOT NULL,
  sheet_name TEXT NOT NULL,
  row_number INTEGER NOT NULL CHECK (row_number >= 1),
  cell_range TEXT NOT NULL,
  details_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(details_json)),
  source_reliability TEXT NOT NULL DEFAULT 'unknown' CHECK (source_reliability IN ('unknown','primary','secondary')),
  verification_status TEXT NOT NULL DEFAULT 'unverified',
  review_status TEXT NOT NULL DEFAULT 'pending' CHECK (review_status IN ('pending','under-review','accepted','rejected','published','withdrawn')),
  match_status TEXT NOT NULL DEFAULT 'matched' CHECK (match_status IN ('matched','workbook-level','identity-review')),
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ontology_source_reference_entity_idx
  ON ontology_source_reference(entity_id, source_scope, review_status);
CREATE INDEX IF NOT EXISTS ontology_source_reference_snapshot_idx
  ON ontology_source_reference(snapshot_id, sheet_name, row_number);
