-- Structured equipment ontology candidate layer.
-- This is deliberately separate from the immutable static ontology package.
-- SQLite stores JSON as canonical TEXT; the PostgreSQL projection uses JSONB.

CREATE TABLE IF NOT EXISTS schema_migration (
  version TEXT PRIMARY KEY,
  applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ontology_import_snapshot (
  snapshot_id TEXT PRIMARY KEY,
  file_name TEXT NOT NULL,
  file_hash TEXT NOT NULL UNIQUE,
  source_version TEXT NOT NULL,
  workbook_schema_version TEXT NOT NULL,
  parser_version TEXT NOT NULL,
  sheet_names TEXT NOT NULL CHECK (json_valid(sheet_names)),
  row_count INTEGER NOT NULL DEFAULT 0 CHECK (row_count >= 0),
  status TEXT NOT NULL CHECK (status IN ('running','completed','partial','failed','duplicate')),
  error_message TEXT,
  imported_at TEXT NOT NULL,
  completed_at TEXT
);
CREATE INDEX IF NOT EXISTS ontology_import_snapshot_status_idx
  ON ontology_import_snapshot(status, imported_at DESC);

CREATE TABLE IF NOT EXISTS ontology_raw_record (
  raw_record_id TEXT PRIMARY KEY,
  snapshot_id TEXT NOT NULL REFERENCES ontology_import_snapshot(snapshot_id),
  sheet_name TEXT NOT NULL,
  row_number INTEGER NOT NULL CHECK (row_number >= 1),
  cell_range TEXT NOT NULL,
  raw_payload TEXT NOT NULL CHECK (json_valid(raw_payload)),
  raw_row_hash TEXT NOT NULL,
  canonical_model_id TEXT,
  normalization_status TEXT NOT NULL DEFAULT 'pending'
    CHECK (normalization_status IN ('pending','normalized','identity-review','rejected','failed')),
  error_reason TEXT,
  created_at TEXT NOT NULL,
  UNIQUE(snapshot_id, sheet_name, row_number, raw_row_hash)
);
CREATE INDEX IF NOT EXISTS ontology_raw_record_snapshot_idx
  ON ontology_raw_record(snapshot_id, sheet_name, row_number);
CREATE INDEX IF NOT EXISTS ontology_raw_record_model_idx
  ON ontology_raw_record(canonical_model_id, normalization_status);

CREATE TABLE IF NOT EXISTS ontology_entity (
  entity_id TEXT PRIMARY KEY,
  canonical_iri TEXT NOT NULL UNIQUE,
  canonical_key TEXT NOT NULL UNIQUE,
  entity_type TEXT NOT NULL,
  preferred_name TEXT NOT NULL,
  english_name TEXT,
  category TEXT,
  subcategory TEXT,
  lifecycle_status TEXT,
  wikidata_id TEXT,
  current_revision INTEGER NOT NULL DEFAULT 0 CHECK (current_revision >= 0),
  review_status TEXT NOT NULL DEFAULT 'pending'
    CHECK (review_status IN ('pending','under-review','accepted','rejected','published','withdrawn')),
  source_snapshot_ids TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(source_snapshot_ids)),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ontology_entity_type_idx
  ON ontology_entity(entity_type, category, preferred_name);

CREATE TABLE IF NOT EXISTS ontology_entity_alias (
  alias_id TEXT PRIMARY KEY,
  entity_id TEXT NOT NULL REFERENCES ontology_entity(entity_id),
  alias TEXT NOT NULL,
  alias_type TEXT NOT NULL,
  language TEXT,
  source_snapshot_id TEXT REFERENCES ontology_import_snapshot(snapshot_id),
  source_record_id TEXT REFERENCES ontology_raw_record(raw_record_id),
  confidence REAL CHECK (confidence >= 0 AND confidence <= 1),
  review_status TEXT NOT NULL DEFAULT 'pending'
    CHECK (review_status IN ('pending','under-review','accepted','rejected','published','withdrawn')),
  created_at TEXT NOT NULL,
  UNIQUE(entity_id, alias)
);
CREATE INDEX IF NOT EXISTS ontology_entity_alias_lookup_idx
  ON ontology_entity_alias(alias, review_status);

CREATE TABLE IF NOT EXISTS ontology_entity_revision (
  entity_revision_id TEXT PRIMARY KEY,
  entity_id TEXT NOT NULL REFERENCES ontology_entity(entity_id),
  revision INTEGER NOT NULL CHECK (revision >= 1),
  attributes_json TEXT NOT NULL DEFAULT '{}'
    CHECK (json_valid(attributes_json) AND json_type(attributes_json) = 'object'),
  attributes_schema_version TEXT NOT NULL,
  attributes_sha256 TEXT NOT NULL,
  source_snapshot_id TEXT NOT NULL REFERENCES ontology_import_snapshot(snapshot_id),
  source_record_id TEXT REFERENCES ontology_raw_record(raw_record_id),
  review_status TEXT NOT NULL DEFAULT 'pending'
    CHECK (review_status IN ('pending','under-review','accepted','rejected','published','withdrawn')),
  created_at TEXT NOT NULL,
  UNIQUE(entity_id, revision),
  UNIQUE(attributes_sha256)
);
CREATE INDEX IF NOT EXISTS ontology_entity_revision_entity_idx
  ON ontology_entity_revision(entity_id, revision DESC);
CREATE TRIGGER IF NOT EXISTS ontology_entity_revision_no_update
BEFORE UPDATE ON ontology_entity_revision BEGIN
  SELECT RAISE(ABORT, 'ontology entity revisions are immutable');
END;
CREATE TRIGGER IF NOT EXISTS ontology_entity_revision_no_delete
BEFORE DELETE ON ontology_entity_revision BEGIN
  SELECT RAISE(ABORT, 'ontology entity revisions are retained');
END;

CREATE TABLE IF NOT EXISTS ontology_evidence_link (
  evidence_id TEXT PRIMARY KEY,
  source_type TEXT NOT NULL CHECK (source_type IN ('workbook','osint-item','source-document','web-page','media')),
  source_ref TEXT NOT NULL,
  source_version TEXT NOT NULL,
  quote TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  locator_json TEXT NOT NULL CHECK (json_valid(locator_json)),
  published_at TEXT,
  retrieved_at TEXT,
  source_reliability TEXT CHECK (source_reliability IN ('unknown','primary','secondary')),
  verification_status TEXT NOT NULL DEFAULT 'unverified',
  created_at TEXT NOT NULL,
  UNIQUE(source_ref, source_version, content_hash, locator_json, quote)
);
CREATE INDEX IF NOT EXISTS ontology_evidence_link_source_idx
  ON ontology_evidence_link(source_type, source_ref, source_version);

CREATE TABLE IF NOT EXISTS ontology_assertion (
  assertion_id TEXT PRIMARY KEY,
  entity_id TEXT NOT NULL REFERENCES ontology_entity(entity_id),
  entity_revision_id TEXT REFERENCES ontology_entity_revision(entity_revision_id),
  attribute_key TEXT NOT NULL,
  predicate TEXT,
  raw_value TEXT NOT NULL,
  normalized_value TEXT,
  value_type TEXT NOT NULL,
  unit TEXT,
  qualifier TEXT,
  conditions TEXT,
  valid_time TEXT,
  observed_at TEXT,
  claim_kind TEXT NOT NULL,
  basis TEXT NOT NULL,
  evidence_ids TEXT NOT NULL CHECK (json_valid(evidence_ids)),
  confidence REAL CHECK (confidence >= 0 AND confidence <= 1),
  review_status TEXT NOT NULL DEFAULT 'pending'
    CHECK (review_status IN ('pending','under-review','accepted','rejected','withdrawn','superseded')),
  truth_verified INTEGER NOT NULL DEFAULT 0 CHECK (truth_verified IN (0,1)),
  created_at TEXT NOT NULL,
  UNIQUE(entity_id, attribute_key, assertion_id)
);
CREATE INDEX IF NOT EXISTS ontology_assertion_entity_idx
  ON ontology_assertion(entity_id, attribute_key, review_status);
CREATE INDEX IF NOT EXISTS ontology_assertion_predicate_idx
  ON ontology_assertion(predicate, value_type, unit);

CREATE TABLE IF NOT EXISTS ontology_relation_assertion (
  relation_id TEXT PRIMARY KEY,
  from_entity_id TEXT NOT NULL REFERENCES ontology_entity(entity_id),
  to_entity_id TEXT NOT NULL REFERENCES ontology_entity(entity_id),
  predicate TEXT NOT NULL,
  qualifier TEXT,
  valid_time TEXT,
  evidence_ids TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(evidence_ids)),
  claim_kind TEXT NOT NULL,
  confidence REAL CHECK (confidence >= 0 AND confidence <= 1),
  review_status TEXT NOT NULL DEFAULT 'pending'
    CHECK (review_status IN ('pending','under-review','accepted','rejected','withdrawn','published')),
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ontology_relation_from_idx
  ON ontology_relation_assertion(from_entity_id, predicate, review_status);
CREATE INDEX IF NOT EXISTS ontology_relation_to_idx
  ON ontology_relation_assertion(to_entity_id, predicate, review_status);

CREATE TABLE IF NOT EXISTS ontology_event (
  event_id TEXT PRIMARY KEY,
  event_type TEXT NOT NULL,
  title TEXT NOT NULL,
  start_time TEXT,
  end_time TEXT,
  date_precision TEXT,
  theater TEXT,
  description TEXT,
  participant_entity_ids TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(participant_entity_ids)),
  subject_entity_ids TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(subject_entity_ids)),
  conditions TEXT,
  evidence_ids TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(evidence_ids)),
  confidence REAL CHECK (confidence >= 0 AND confidence <= 1),
  review_status TEXT NOT NULL DEFAULT 'pending'
    CHECK (review_status IN ('pending','under-review','accepted','rejected','withdrawn','published')),
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ontology_event_type_time_idx
  ON ontology_event(event_type, start_time, review_status);

CREATE TABLE IF NOT EXISTS ontology_event_observation (
  observation_id TEXT PRIMARY KEY,
  event_id TEXT NOT NULL REFERENCES ontology_event(event_id),
  metric_type TEXT NOT NULL,
  value_low REAL,
  value_high REAL,
  value_exact REAL,
  unit TEXT,
  denominator TEXT,
  quantity_type TEXT,
  scope TEXT,
  as_of_date TEXT,
  conditions TEXT,
  evidence_ids TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(evidence_ids)),
  uncertainty TEXT,
  review_status TEXT NOT NULL DEFAULT 'pending'
    CHECK (review_status IN ('pending','under-review','accepted','rejected','withdrawn','published')),
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ontology_event_observation_metric_idx
  ON ontology_event_observation(metric_type, unit, review_status);

CREATE TABLE IF NOT EXISTS ontology_capability_assessment (
  capability_assessment_id TEXT PRIMARY KEY,
  entity_id TEXT NOT NULL REFERENCES ontology_entity(entity_id),
  dimension TEXT NOT NULL,
  score REAL,
  scale_min REAL,
  scale_max REAL,
  weight REAL,
  method TEXT NOT NULL,
  assessor_type TEXT NOT NULL,
  cutoff_date TEXT,
  supporting_evidence_ids TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(supporting_evidence_ids)),
  counter_evidence_ids TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(counter_evidence_ids)),
  confidence REAL CHECK (confidence >= 0 AND confidence <= 1),
  review_status TEXT NOT NULL DEFAULT 'pending'
    CHECK (review_status IN ('pending','under-review','accepted','rejected','withdrawn','published')),
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ontology_capability_assessment_entity_idx
  ON ontology_capability_assessment(entity_id, dimension, review_status);
