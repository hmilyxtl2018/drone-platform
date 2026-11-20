BEGIN;
CREATE TABLE IF NOT EXISTS agent_goal_contract_revision (
    contract_id text PRIMARY KEY,
    agent_key text NOT NULL REFERENCES agent_definition(agent_key),
    revision integer NOT NULL CHECK (revision > 0),
    document_json jsonb NOT NULL CHECK (jsonb_typeof(document_json)='object'),
    content_hash text NOT NULL CHECK (length(content_hash)=64),
    created_by text NOT NULL,
    reason text NOT NULL,
    idempotency_key text NOT NULL,
    request_hash text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(agent_key,revision), UNIQUE(agent_key,idempotency_key)
);
CREATE TABLE IF NOT EXISTS agent_run_goal (
    run_id text PRIMARY KEY REFERENCES agent_run(run_id),
    goal_id text UNIQUE NOT NULL,
    parent_goal_id text REFERENCES agent_run_goal(goal_id),
    contract_id text NOT NULL REFERENCES agent_goal_contract_revision(contract_id),
    document_json jsonb NOT NULL CHECK (jsonb_typeof(document_json)='object'),
    content_hash text NOT NULL CHECK (length(content_hash)=64),
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS agent_run_goal_parent_idx ON agent_run_goal(parent_goal_id);
CREATE TABLE IF NOT EXISTS agent_goal_assessment (
    assessment_id text PRIMARY KEY,
    run_id text NOT NULL REFERENCES agent_run_goal(run_id),
    goal_id text NOT NULL REFERENCES agent_run_goal(goal_id),
    goal_hash text NOT NULL CHECK (length(goal_hash)=64),
    output_hash text NOT NULL CHECK (length(output_hash)=64),
    execution_status text NOT NULL,
    evaluator_version text NOT NULL,
    status text NOT NULL CHECK (status IN ('met','partially-met','unmet','not-evaluable')),
    document_json jsonb NOT NULL CHECK (jsonb_typeof(document_json)='object'),
    content_hash text NOT NULL CHECK (length(content_hash)=64),
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS agent_goal_assessment_run_idx ON agent_goal_assessment(run_id,created_at DESC);
DO $$
DECLARE name text;
BEGIN
    FOREACH name IN ARRAY ARRAY['agent_goal_contract_revision','agent_run_goal','agent_goal_assessment'] LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS goal_history_immutable ON %I', name);
        EXECUTE format('CREATE TRIGGER goal_history_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON %I FOR EACH STATEMENT EXECUTE FUNCTION agent_research_history_immutable()', name);
    END LOOP;
END $$;
DROP TRIGGER IF EXISTS goal_assessment_worker_fence ON agent_goal_assessment;
CREATE TRIGGER goal_assessment_worker_fence BEFORE INSERT ON agent_goal_assessment
FOR EACH ROW EXECUTE FUNCTION agent_worker_fence_guard();
DROP TRIGGER IF EXISTS goal_snapshot_worker_fence ON agent_run_goal;
CREATE TRIGGER goal_snapshot_worker_fence BEFORE INSERT ON agent_run_goal
FOR EACH ROW EXECUTE FUNCTION agent_worker_fence_guard();
COMMIT;
