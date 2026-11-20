-- Recoverable leases for object-directed verification jobs.
-- A provider process can disappear without completing its row.  These fields
-- let the service distinguish a live worker from a stale lease while keeping
-- the original job, query and collected evidence available for retry.
ALTER TABLE ontology_verification_job ADD COLUMN heartbeat_at TEXT;
ALTER TABLE ontology_verification_job ADD COLUMN stop_reason TEXT;
ALTER TABLE ontology_verification_job ADD COLUMN recovered_at TEXT;
ALTER TABLE ontology_verification_job ADD COLUMN worker_token TEXT;
CREATE INDEX IF NOT EXISTS ontology_verification_job_heartbeat_idx
  ON ontology_verification_job(status, heartbeat_at, started_at);

CREATE TABLE IF NOT EXISTS ontology_verification_job_event (
  event_id INTEGER PRIMARY KEY AUTOINCREMENT,
  verification_id TEXT NOT NULL REFERENCES ontology_verification_job(verification_id),
  event_type TEXT NOT NULL,
  detail_json TEXT NOT NULL CHECK(json_valid(detail_json)),
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ontology_verification_job_event_idx
  ON ontology_verification_job_event(verification_id, event_id);
CREATE TRIGGER IF NOT EXISTS ontology_verification_job_event_no_update
BEFORE UPDATE ON ontology_verification_job_event BEGIN
  SELECT RAISE(ABORT, 'verification job events are immutable');
END;
CREATE TRIGGER IF NOT EXISTS ontology_verification_job_event_no_delete
BEFORE DELETE ON ontology_verification_job_event BEGIN
  SELECT RAISE(ABORT, 'verification job events are retained');
END;
