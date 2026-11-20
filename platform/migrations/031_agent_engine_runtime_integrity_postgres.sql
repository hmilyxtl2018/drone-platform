-- PostgreSQL audit/snapshot retention: the SQLite triggers in migration 028
-- do not apply to the PostgreSQL runtime.
CREATE OR REPLACE FUNCTION agent_engine_reject_mutation() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'agent runtime record is immutable: %', TG_TABLE_NAME
        USING ERRCODE = '55000';
END;
$$;

DROP TRIGGER IF EXISTS agent_run_event_immutable ON agent_run_event;
CREATE TRIGGER agent_run_event_immutable BEFORE UPDATE OR DELETE ON agent_run_event
FOR EACH ROW EXECUTE FUNCTION agent_engine_reject_mutation();
DROP TRIGGER IF EXISTS agent_tool_call_immutable ON agent_tool_call;
CREATE TRIGGER agent_tool_call_immutable BEFORE UPDATE OR DELETE ON agent_tool_call
FOR EACH ROW EXECUTE FUNCTION agent_engine_reject_mutation();
DROP TRIGGER IF EXISTS agent_profile_snapshot_immutable ON agent_run_profile_snapshot;
CREATE TRIGGER agent_profile_snapshot_immutable BEFORE UPDATE OR DELETE ON agent_run_profile_snapshot
FOR EACH ROW EXECUTE FUNCTION agent_engine_reject_mutation();
DROP TRIGGER IF EXISTS agent_artifact_immutable ON agent_run_artifact;
CREATE TRIGGER agent_artifact_immutable BEFORE UPDATE OR DELETE ON agent_run_artifact
FOR EACH ROW EXECUTE FUNCTION agent_engine_reject_mutation();
