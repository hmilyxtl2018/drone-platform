-- Change observations refer to stored source artifacts; they do not establish
-- factual truth or grant authority to schedule a new mission.
CREATE TABLE IF NOT EXISTS connector_source_event (
 event_id text PRIMARY KEY,
 connector_ref text NOT NULL,
 source_uri text NOT NULL,
 before_artifact_id text NOT NULL REFERENCES agent_run_artifact(artifact_id),
 after_artifact_id text NOT NULL REFERENCES agent_run_artifact(artifact_id),
 before_hash text NOT NULL CHECK(length(before_hash)=64),
 after_hash text NOT NULL CHECK(length(after_hash)=64),
 dedup_key text NOT NULL UNIQUE,
 observed_by text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 CHECK(before_hash<>after_hash)
);
CREATE INDEX IF NOT EXISTS connector_source_event_uri_idx ON connector_source_event(source_uri,created_at DESC);
DROP TRIGGER IF EXISTS connector_history_immutable ON connector_source_event;
CREATE TRIGGER connector_history_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON connector_source_event
 FOR EACH STATEMENT EXECUTE FUNCTION connector_history_immutable();
