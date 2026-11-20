-- Independent, explicitly requested deterministic calculations; no model queue.
CREATE TABLE IF NOT EXISTS agent_engineering_run (
    run_id text PRIMARY KEY REFERENCES agent_run(run_id),
    parent_run_id text NOT NULL REFERENCES agent_run(run_id),
    principal_id text NOT NULL REFERENCES agent_admin_identity(principal_id),
    idempotency_key text NOT NULL,
    request_hash text NOT NULL CHECK(request_hash ~ '^[0-9a-f]{64}$'),
    tool_key text NOT NULL CHECK(tool_key IN ('engineering.evaluate_requirement','engineering.mass_budget','engineering.endurance_budget')),
    frozen_input_json jsonb NOT NULL CHECK(jsonb_typeof(frozen_input_json)='object'),
    frozen_input_hash text NOT NULL CHECK(frozen_input_hash ~ '^[0-9a-f]{64}$'),
    projected_payload_json jsonb NOT NULL CHECK(jsonb_typeof(projected_payload_json)='object'),
    candidate_projection_json jsonb NOT NULL CHECK(jsonb_typeof(candidate_projection_json)='object'),
    projection_hash text NOT NULL CHECK(projection_hash ~ '^[0-9a-f]{64}$'),
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK(run_id<>parent_run_id),
    UNIQUE(parent_run_id,idempotency_key)
);
CREATE INDEX IF NOT EXISTS agent_engineering_parent_idx ON agent_engineering_run(parent_run_id,created_at);
CREATE OR REPLACE FUNCTION agent_engineering_run_integrity() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE data jsonb;
BEGIN
    SELECT input_json INTO data FROM agent_run WHERE run_id=NEW.run_id;
    IF data->>'executionMode' IS DISTINCT FROM 'engineering-manual-v1'
       OR data->>'engineeringParentRunId' IS DISTINCT FROM NEW.parent_run_id
       OR data->>'engineeringInputHash' IS DISTINCT FROM NEW.frozen_input_hash THEN
        RAISE EXCEPTION 'manual engineering input binding mismatch' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END; $$;
DROP TRIGGER IF EXISTS agent_engineering_run_integrity ON agent_engineering_run;
CREATE TRIGGER agent_engineering_run_integrity BEFORE INSERT ON agent_engineering_run
FOR EACH ROW EXECUTE FUNCTION agent_engineering_run_integrity();
DROP TRIGGER IF EXISTS agent_engineering_run_immutable ON agent_engineering_run;
CREATE TRIGGER agent_engineering_run_immutable BEFORE UPDATE OR DELETE ON agent_engineering_run
FOR EACH ROW EXECUTE FUNCTION agent_engine_reject_mutation();
DROP TRIGGER IF EXISTS agent_engineering_run_no_truncate ON agent_engineering_run;
CREATE TRIGGER agent_engineering_run_no_truncate BEFORE TRUNCATE ON agent_engineering_run
FOR EACH STATEMENT EXECUTE FUNCTION agent_engine_reject_mutation();
DROP TRIGGER IF EXISTS agent_engineering_run_fence ON agent_engineering_run;
CREATE TRIGGER agent_engineering_run_fence BEFORE INSERT ON agent_engineering_run
FOR EACH ROW EXECUTE FUNCTION agent_worker_fence_guard();
