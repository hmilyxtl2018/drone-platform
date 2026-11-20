-- Formal entity audit runs. Capability runs remain the provider execution
-- implementation; this layer owns the user-visible lifecycle, trigger and
-- publication boundary.
CREATE TABLE IF NOT EXISTS ontology_audit_run (
  audit_run_id TEXT PRIMARY KEY,
  entity_id TEXT NOT NULL REFERENCES ontology_entity(entity_id),
  object_revision INTEGER NOT NULL CHECK(object_revision >= 1),
  trigger_type TEXT NOT NULL CHECK(trigger_type IN ('manual','batch','periodic','conversation')),
  status TEXT NOT NULL CHECK(status IN ('queued','planning','collecting','extracting','reconciling','awaiting-review','completed','completed-with-gaps','failed','cancelled')),
  request_json TEXT NOT NULL CHECK(json_valid(request_json) AND json_type(request_json)='object'),
  result_json TEXT CHECK(result_json IS NULL OR json_valid(result_json)),
  capability_run_id TEXT REFERENCES ontology_capability_run(run_id),
  retry_of TEXT REFERENCES ontology_audit_run(audit_run_id),
  stop_reason TEXT,
  error_message TEXT,
  created_at TEXT NOT NULL,
  started_at TEXT,
  updated_at TEXT NOT NULL,
  completed_at TEXT
);
CREATE INDEX IF NOT EXISTS ontology_audit_run_entity_idx ON ontology_audit_run(entity_id,created_at DESC);
CREATE INDEX IF NOT EXISTS ontology_audit_run_status_idx ON ontology_audit_run(status,created_at DESC);

CREATE TABLE IF NOT EXISTS ontology_audit_event (
  event_id INTEGER PRIMARY KEY AUTOINCREMENT,
  audit_run_id TEXT NOT NULL REFERENCES ontology_audit_run(audit_run_id),
  stage TEXT NOT NULL,
  status TEXT NOT NULL,
  detail_json TEXT NOT NULL CHECK(json_valid(detail_json)),
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ontology_audit_event_run_idx ON ontology_audit_event(audit_run_id,event_id);
CREATE TRIGGER IF NOT EXISTS ontology_audit_event_no_update BEFORE UPDATE ON ontology_audit_event BEGIN SELECT RAISE(ABORT,'audit events are immutable'); END;
CREATE TRIGGER IF NOT EXISTS ontology_audit_event_no_delete BEFORE DELETE ON ontology_audit_event BEGIN SELECT RAISE(ABORT,'audit events are retained'); END;

CREATE TABLE IF NOT EXISTS ontology_audit_candidate (
  audit_run_id TEXT NOT NULL REFERENCES ontology_audit_run(audit_run_id),
  candidate_type TEXT NOT NULL CHECK(candidate_type IN ('attribute','relation')),
  candidate_id TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY(audit_run_id,candidate_type,candidate_id)
);
CREATE INDEX IF NOT EXISTS ontology_audit_candidate_id_idx ON ontology_audit_candidate(candidate_type,candidate_id);

CREATE TABLE IF NOT EXISTS ontology_audit_review (
  audit_review_id TEXT PRIMARY KEY,
  audit_run_id TEXT NOT NULL REFERENCES ontology_audit_run(audit_run_id),
  candidate_type TEXT NOT NULL CHECK(candidate_type IN ('attribute','relation')),
  candidate_id TEXT NOT NULL,
  action TEXT NOT NULL CHECK(action IN ('accept','reject','request-more-evidence','edit','mark-conflict')),
  reviewer TEXT NOT NULL,
  reason TEXT NOT NULL,
  decision_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(decision_json)),
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ontology_audit_review_run_idx ON ontology_audit_review(audit_run_id,created_at);
CREATE TRIGGER IF NOT EXISTS ontology_audit_review_no_update BEFORE UPDATE ON ontology_audit_review BEGIN SELECT RAISE(ABORT,'audit reviews are immutable'); END;
CREATE TRIGGER IF NOT EXISTS ontology_audit_review_no_delete BEFORE DELETE ON ontology_audit_review BEGIN SELECT RAISE(ABORT,'audit reviews are retained'); END;

CREATE TABLE IF NOT EXISTS ontology_audit_schedule (
  schedule_id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)),
  interval_seconds INTEGER NOT NULL CHECK(interval_seconds >= 300),
  policy_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(policy_json)),
  last_run_at TEXT,
  next_run_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
