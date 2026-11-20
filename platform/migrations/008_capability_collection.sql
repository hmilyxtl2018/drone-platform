-- Keep collection evidence after retries; candidates reference immutable snapshots.
ALTER TABLE ontology_attribute_candidate ADD COLUMN metadata_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(metadata_json));
CREATE TABLE ontology_collected_source_snapshot (
  snapshot_id TEXT PRIMARY KEY,
  verification_id TEXT NOT NULL REFERENCES ontology_verification_job(verification_id),
  entity_revision_id TEXT NOT NULL,
  item_index INTEGER NOT NULL,
  content_hash TEXT NOT NULL,
  item_json TEXT NOT NULL CHECK(json_valid(item_json)),
  created_at TEXT NOT NULL
);
CREATE INDEX ontology_collected_snapshot_job ON ontology_collected_source_snapshot(verification_id,content_hash);
CREATE TRIGGER ontology_collected_snapshot_no_update BEFORE UPDATE ON ontology_collected_source_snapshot BEGIN SELECT RAISE(ABORT,'source snapshots are immutable'); END;
CREATE TRIGGER ontology_collected_snapshot_no_delete BEFORE DELETE ON ontology_collected_source_snapshot BEGIN SELECT RAISE(ABORT,'source snapshots are retained'); END;
CREATE TABLE ontology_capability_run (
  run_id TEXT PRIMARY KEY,
  entity_id TEXT NOT NULL REFERENCES ontology_entity(entity_id),
  object_revision INTEGER NOT NULL,
  status TEXT NOT NULL,
  request_json TEXT NOT NULL CHECK(json_valid(request_json)),
  result_json TEXT CHECK(result_json IS NULL OR json_valid(result_json)),
  created_at TEXT NOT NULL,
  completed_at TEXT
);
CREATE TABLE ontology_capability_run_event (
  event_id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL REFERENCES ontology_capability_run(run_id),
  stage TEXT NOT NULL,
  status TEXT NOT NULL,
  detail_json TEXT NOT NULL CHECK(json_valid(detail_json)),
  created_at TEXT NOT NULL
);
CREATE TRIGGER ontology_capability_run_event_no_update BEFORE UPDATE ON ontology_capability_run_event BEGIN SELECT RAISE(ABORT,'run events are immutable'); END;
CREATE TRIGGER ontology_capability_run_event_no_delete BEFORE DELETE ON ontology_capability_run_event BEGIN SELECT RAISE(ABORT,'run events are retained'); END;
