-- Historical legacy AnalysisRun associations. Engine owns only its new Run.
CREATE TABLE IF NOT EXISTS agent_legacy_run_link (
    analysis_run_id text PRIMARY KEY,
    run_id text NOT NULL UNIQUE REFERENCES agent_run(run_id),
    source_uri text NOT NULL,
    legacy_status text NOT NULL CHECK(legacy_status IN ('completed','completed-with-gaps','requires-review','failed','cancelled')),
    snapshot_hash text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
DROP TRIGGER IF EXISTS agent_legacy_link_immutable ON agent_legacy_run_link;
CREATE TRIGGER agent_legacy_link_immutable BEFORE UPDATE OR DELETE ON agent_legacy_run_link
FOR EACH ROW EXECUTE FUNCTION agent_engine_reject_mutation();
