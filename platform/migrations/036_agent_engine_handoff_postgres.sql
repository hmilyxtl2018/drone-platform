-- Immutable handoffs between the three configured research agents.
-- Apply after 033. No existing configuration or runtime data is changed.
CREATE TABLE IF NOT EXISTS agent_run_handoff (
    handoff_id text PRIMARY KEY,
    run_id text NOT NULL REFERENCES agent_run(run_id), -- parent / fencing owner
    child_run_id text NOT NULL UNIQUE REFERENCES agent_run(run_id),
    root_run_id text NOT NULL REFERENCES agent_run_budget(root_run_id),
    target_agent_key text NOT NULL REFERENCES agent_definition(agent_key),
    idempotency_key text NOT NULL,
    artifacts_json jsonb NOT NULL CHECK (jsonb_typeof(artifacts_json)='array'),
    artifacts_hash text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (run_id<>child_run_id),
    UNIQUE (run_id,idempotency_key),
    UNIQUE (run_id,target_agent_key,artifacts_hash)
);
CREATE INDEX IF NOT EXISTS agent_handoff_root_idx ON agent_run_handoff(root_run_id,created_at);
DROP TRIGGER IF EXISTS agent_handoff_immutable ON agent_run_handoff;
CREATE TRIGGER agent_handoff_immutable BEFORE UPDATE OR DELETE ON agent_run_handoff
FOR EACH ROW EXECUTE FUNCTION agent_engine_reject_mutation();
DROP TRIGGER IF EXISTS agent_handoff_fence ON agent_run_handoff;
CREATE TRIGGER agent_handoff_fence BEFORE INSERT ON agent_run_handoff
FOR EACH ROW EXECUTE FUNCTION agent_worker_fence_guard();
