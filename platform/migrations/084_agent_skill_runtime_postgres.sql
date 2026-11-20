BEGIN;
CREATE TABLE IF NOT EXISTS agent_skill (
    skill_key text PRIMARY KEY,
    name text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS agent_skill_revision (
    skill_key text NOT NULL REFERENCES agent_skill(skill_key),
    version text NOT NULL,
    content_hash text NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
    package_json jsonb NOT NULL CHECK (jsonb_typeof(package_json)='object'),
    created_by text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(skill_key,version), UNIQUE(skill_key,version,content_hash)
);
CREATE TABLE IF NOT EXISTS agent_step_skill_binding (
    agent_key text NOT NULL REFERENCES agent_definition(agent_key),
    profile_version text NOT NULL,
    step_key text NOT NULL,
    stage text NOT NULL,
    skill_key text NOT NULL,
    skill_version text NOT NULL,
    content_hash text NOT NULL,
    created_by text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(agent_key,profile_version,step_key,stage),
    FOREIGN KEY(skill_key,skill_version,content_hash)
        REFERENCES agent_skill_revision(skill_key,version,content_hash)
);
CREATE TABLE IF NOT EXISTS agent_run_skill_invocation (
    invocation_id text PRIMARY KEY,
    run_id text NOT NULL REFERENCES agent_run(run_id),
    step_run_id text,
    step_key text NOT NULL,
    stage text NOT NULL,
    operation_key text NOT NULL,
    skill_key text NOT NULL,
    skill_version text NOT NULL,
    content_hash text NOT NULL,
    input_hash text NOT NULL,
    schema_hash text NOT NULL,
    context_id text,
    manifest_hash text,
    status text NOT NULL CHECK (status IN ('running','succeeded','failed','cancelled','unknown')),
    model_call_ids jsonb NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(model_call_ids)='array'),
    output_hash text,
    error_code text,
    created_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz,
    UNIQUE(run_id,operation_key),
    FOREIGN KEY(skill_key,skill_version,content_hash)
        REFERENCES agent_skill_revision(skill_key,version,content_hash)
);
CREATE INDEX IF NOT EXISTS agent_skill_invocation_run_idx ON agent_run_skill_invocation(run_id,created_at);
DO $$
DECLARE name text;
BEGIN
    FOREACH name IN ARRAY ARRAY['agent_skill_revision','agent_step_skill_binding'] LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS skill_definition_immutable ON %I',name);
        EXECUTE format('CREATE TRIGGER skill_definition_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON %I FOR EACH STATEMENT EXECUTE FUNCTION agent_research_history_immutable()',name);
    END LOOP;
END $$;
DROP TRIGGER IF EXISTS skill_invocation_worker_fence ON agent_run_skill_invocation;
CREATE TRIGGER skill_invocation_worker_fence BEFORE INSERT OR UPDATE OR DELETE ON agent_run_skill_invocation
FOR EACH ROW EXECUTE FUNCTION agent_worker_fence_guard();
DROP TRIGGER IF EXISTS skill_invocation_no_delete ON agent_run_skill_invocation;
CREATE TRIGGER skill_invocation_no_delete BEFORE DELETE OR TRUNCATE ON agent_run_skill_invocation
FOR EACH STATEMENT EXECUTE FUNCTION agent_research_history_immutable();
COMMIT;
