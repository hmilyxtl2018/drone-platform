-- Entity-directed enrichment batches. Each item is independently resumable;
-- a failed object never blocks the rest of the batch.
CREATE TABLE IF NOT EXISTS ontology_capability_batch (
  batch_id TEXT PRIMARY KEY,
  request_json TEXT NOT NULL CHECK(json_valid(request_json) AND json_type(request_json)='object'),
  status TEXT NOT NULL CHECK(status IN ('queued','running','completed','partial','failed','interrupted')),
  result_json TEXT CHECK(result_json IS NULL OR json_valid(result_json)),
  created_at TEXT NOT NULL,
  started_at TEXT,
  completed_at TEXT
);

CREATE TABLE IF NOT EXISTS ontology_capability_batch_item (
  batch_id TEXT NOT NULL REFERENCES ontology_capability_batch(batch_id),
  entity_id TEXT NOT NULL REFERENCES ontology_entity(entity_id),
  run_id TEXT REFERENCES ontology_capability_run(run_id),
  status TEXT NOT NULL CHECK(status IN ('queued','running','completed','awaiting-human-review','completed-with-gaps','failed','interrupted')),
  current_scope TEXT,
  round INTEGER NOT NULL DEFAULT 0 CHECK(round >= 0),
  max_items INTEGER NOT NULL DEFAULT 0 CHECK(max_items >= 0),
  max_model_calls INTEGER NOT NULL DEFAULT 0 CHECK(max_model_calls >= 0),
  max_elapsed_seconds INTEGER NOT NULL DEFAULT 0 CHECK(max_elapsed_seconds >= 0),
  stop_reason TEXT,
  candidate_count INTEGER NOT NULL DEFAULT 0 CHECK(candidate_count >= 0),
  conflict_count INTEGER NOT NULL DEFAULT 0 CHECK(conflict_count >= 0),
  error_message TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY(batch_id, entity_id)
);
CREATE INDEX IF NOT EXISTS ontology_capability_batch_item_entity_idx
  ON ontology_capability_batch_item(entity_id, created_at);

CREATE TABLE IF NOT EXISTS ontology_capability_batch_event (
  event_id INTEGER PRIMARY KEY AUTOINCREMENT,
  batch_id TEXT NOT NULL REFERENCES ontology_capability_batch(batch_id),
  entity_id TEXT,
  stage TEXT NOT NULL,
  status TEXT NOT NULL,
  detail_json TEXT NOT NULL CHECK(json_valid(detail_json)),
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ontology_capability_batch_event_idx
  ON ontology_capability_batch_event(batch_id, event_id);
CREATE TRIGGER IF NOT EXISTS ontology_capability_batch_event_no_update
BEFORE UPDATE ON ontology_capability_batch_event BEGIN
  SELECT RAISE(ABORT,'batch events are immutable');
END;
CREATE TRIGGER IF NOT EXISTS ontology_capability_batch_event_no_delete
BEFORE DELETE ON ontology_capability_batch_event BEGIN
  SELECT RAISE(ABORT,'batch events are retained');
END;

-- Keep the planning distinction between all registered fields and current
-- archive gaps. Existing jobs remain valid and gain an empty default.
ALTER TABLE ontology_verification_job ADD COLUMN missing_keys_json TEXT NOT NULL DEFAULT '[]' CHECK(json_valid(missing_keys_json));
