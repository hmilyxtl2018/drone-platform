-- Additive capability JSON documents; archived attributes are never rewritten.
CREATE TABLE IF NOT EXISTS ontology_capability_profile (
  profile_id TEXT PRIMARY KEY,
  entity_id TEXT NOT NULL REFERENCES ontology_entity(entity_id),
  object_revision INTEGER NOT NULL CHECK(object_revision >= 1),
  schema_version TEXT NOT NULL,
  content_hash TEXT NOT NULL UNIQUE,
  profile_json TEXT NOT NULL CHECK(json_valid(profile_json) AND json_type(profile_json)='object'),
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ontology_capability_profile_entity_idx
  ON ontology_capability_profile(entity_id,object_revision,created_at);
CREATE TRIGGER IF NOT EXISTS ontology_capability_profile_no_update
BEFORE UPDATE ON ontology_capability_profile BEGIN
  SELECT RAISE(ABORT,'capability profiles are immutable');
END;
CREATE TRIGGER IF NOT EXISTS ontology_capability_profile_no_delete
BEFORE DELETE ON ontology_capability_profile BEGIN
  SELECT RAISE(ABORT,'capability profiles are retained');
END;
CREATE TABLE IF NOT EXISTS ontology_capability_review (
  review_id INTEGER PRIMARY KEY AUTOINCREMENT,
  profile_id TEXT NOT NULL REFERENCES ontology_capability_profile(profile_id),
  record_id TEXT NOT NULL,
  action TEXT NOT NULL CHECK(action IN ('accept','reject','request-more-evidence')),
  reviewer TEXT NOT NULL,
  reason TEXT NOT NULL,
  verified_confidence TEXT CHECK(verified_confidence IN ('low','medium','high')),
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ontology_capability_review_target_idx
  ON ontology_capability_review(profile_id,record_id,review_id);
CREATE TRIGGER IF NOT EXISTS ontology_capability_review_no_update
BEFORE UPDATE ON ontology_capability_review BEGIN
  SELECT RAISE(ABORT,'capability reviews are immutable');
END;
CREATE TRIGGER IF NOT EXISTS ontology_capability_review_no_delete
BEFORE DELETE ON ontology_capability_review BEGIN
  SELECT RAISE(ABORT,'capability reviews are retained');
END;
CREATE TABLE IF NOT EXISTS ontology_capability_publication (
  publication_id TEXT PRIMARY KEY,
  profile_id TEXT NOT NULL REFERENCES ontology_capability_profile(profile_id),
  entity_id TEXT NOT NULL REFERENCES ontology_entity(entity_id),
  selected_record_ids TEXT NOT NULL CHECK(json_valid(selected_record_ids)),
  publication_json TEXT NOT NULL CHECK(json_valid(publication_json)),
  published_by TEXT NOT NULL,
  reason TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ontology_capability_publication_entity_idx
  ON ontology_capability_publication(entity_id,created_at);
CREATE TRIGGER IF NOT EXISTS ontology_capability_publication_no_update
BEFORE UPDATE ON ontology_capability_publication BEGIN
  SELECT RAISE(ABORT,'capability publications are immutable');
END;
CREATE TRIGGER IF NOT EXISTS ontology_capability_publication_no_delete
BEFORE DELETE ON ontology_capability_publication BEGIN
  SELECT RAISE(ABORT,'capability publications are retained');
END;
