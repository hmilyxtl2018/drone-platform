-- Context manifests, invocation slots and controlled collaboration state.
-- Raw prompts/source bodies remain in existing artifacts; these tables store
-- immutable references and hashes only.

CREATE TABLE IF NOT EXISTS agent_context_snapshot (
    context_id text PRIMARY KEY,
    run_id text NOT NULL REFERENCES agent_run(run_id),
    root_run_id text NOT NULL REFERENCES agent_run(run_id),
    step_run_id text REFERENCES agent_step_run(step_run_id),
    operation_key text NOT NULL,
    role_key text NOT NULL,
    node_key text NOT NULL,
    iteration integer NOT NULL CHECK (iteration >= 0),
    profile_hash text NOT NULL,
    baseline_hash text,
    task_hash text NOT NULL,
    w_revision bigint,
    policy_json jsonb NOT NULL CHECK (jsonb_typeof(policy_json)='object'),
    manifest_json jsonb NOT NULL CHECK (jsonb_typeof(manifest_json)='object'),
    manifest_hash text NOT NULL CHECK (manifest_hash ~ '^[0-9a-f]{64}$'),
    status text NOT NULL DEFAULT 'frozen' CHECK (status IN ('frozen','legacy-untracked','stale')),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(run_id, operation_key)
);
CREATE INDEX IF NOT EXISTS agent_context_snapshot_run_idx
    ON agent_context_snapshot(run_id, created_at, context_id);

CREATE TABLE IF NOT EXISTS agent_context_dependency (
    context_id text NOT NULL REFERENCES agent_context_snapshot(context_id),
    dependency_id text NOT NULL,
    dependency_kind text NOT NULL,
    version_ref text,
    content_hash text,
    predicate_json jsonb,
    read_mode text NOT NULL CHECK (read_mode IN ('read','absence','excluded')),
    PRIMARY KEY(context_id, dependency_id, dependency_kind)
);

CREATE TABLE IF NOT EXISTS agent_invocation_slot (
    run_id text NOT NULL REFERENCES agent_run(run_id),
    operation_key text NOT NULL,
    node_key text NOT NULL,
    iteration integer NOT NULL CHECK (iteration >= 0),
    context_id text NOT NULL REFERENCES agent_context_snapshot(context_id),
    schema_hash text NOT NULL,
    status text NOT NULL CHECK (status IN ('planned','running','succeeded','failed','unknown')),
    result_artifact_id text REFERENCES agent_run_artifact(artifact_id),
    attempt_count integer NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(run_id, operation_key)
);

CREATE TABLE IF NOT EXISTS agent_collaboration_revision (
    root_run_id text NOT NULL REFERENCES agent_run(run_id),
    revision bigint NOT NULL CHECK (revision >= 0),
    state_json jsonb NOT NULL CHECK (jsonb_typeof(state_json)='object'),
    state_hash text NOT NULL CHECK (state_hash ~ '^[0-9a-f]{64}$'),
    actor_run_id text NOT NULL REFERENCES agent_run(run_id),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(root_run_id, revision)
);

CREATE TABLE IF NOT EXISTS agent_collaboration_proposal (
    proposal_id text PRIMARY KEY,
    root_run_id text NOT NULL REFERENCES agent_run(run_id),
    expected_revision bigint NOT NULL CHECK (expected_revision >= 0),
    actor_run_id text NOT NULL REFERENCES agent_run(run_id),
    role_key text NOT NULL,
    operation_key text NOT NULL,
    patch_json jsonb NOT NULL CHECK (jsonb_typeof(patch_json)='object'),
    decision text NOT NULL DEFAULT 'pending' CHECK (decision IN ('pending','accepted','rejected','conflict')),
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS agent_collaboration_proposal_lookup
    ON agent_collaboration_proposal(root_run_id, decision, created_at);

ALTER TABLE agent_run ADD COLUMN IF NOT EXISTS collaboration_revision bigint NOT NULL DEFAULT 0;
ALTER TABLE agent_step_run ADD COLUMN IF NOT EXISTS context_id text REFERENCES agent_context_snapshot(context_id);
ALTER TABLE agent_tool_call ADD COLUMN IF NOT EXISTS context_id text REFERENCES agent_context_snapshot(context_id);
ALTER TABLE agent_tool_call ADD COLUMN IF NOT EXISTS manifest_hash text;
ALTER TABLE agent_tool_call ADD COLUMN IF NOT EXISTS operation_key text;
ALTER TABLE agent_model_call ADD COLUMN IF NOT EXISTS context_id text REFERENCES agent_context_snapshot(context_id);
ALTER TABLE agent_model_call ADD COLUMN IF NOT EXISTS manifest_hash text;
ALTER TABLE agent_model_call ADD COLUMN IF NOT EXISTS operation_key text;
ALTER TABLE agent_run_plan ADD COLUMN IF NOT EXISTS context_id text REFERENCES agent_context_snapshot(context_id);
ALTER TABLE agent_run_plan ADD COLUMN IF NOT EXISTS manifest_hash text;
CREATE INDEX IF NOT EXISTS agent_model_call_context_idx ON agent_model_call(context_id, created_at);
CREATE INDEX IF NOT EXISTS agent_tool_call_context_idx ON agent_tool_call(context_id, created_at);

CREATE OR REPLACE FUNCTION reject_agent_context_mutation() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'context and collaboration records are append-only' USING ERRCODE='55000';
END;
$$;
DROP TRIGGER IF EXISTS agent_context_snapshot_immutable ON agent_context_snapshot;
CREATE TRIGGER agent_context_snapshot_immutable BEFORE UPDATE OR DELETE ON agent_context_snapshot
    FOR EACH ROW EXECUTE FUNCTION reject_agent_context_mutation();
DROP TRIGGER IF EXISTS agent_context_dependency_immutable ON agent_context_dependency;
CREATE TRIGGER agent_context_dependency_immutable BEFORE UPDATE OR DELETE ON agent_context_dependency
    FOR EACH ROW EXECUTE FUNCTION reject_agent_context_mutation();
DROP TRIGGER IF EXISTS agent_invocation_slot_immutable ON agent_invocation_slot;
DROP TRIGGER IF EXISTS agent_collaboration_revision_immutable ON agent_collaboration_revision;
CREATE TRIGGER agent_collaboration_revision_immutable BEFORE UPDATE OR DELETE ON agent_collaboration_revision
    FOR EACH ROW EXECUTE FUNCTION reject_agent_context_mutation();
DROP TRIGGER IF EXISTS agent_collaboration_proposal_immutable ON agent_collaboration_proposal;
CREATE TRIGGER agent_collaboration_proposal_immutable BEFORE UPDATE OR DELETE ON agent_collaboration_proposal
    FOR EACH ROW EXECUTE FUNCTION reject_agent_context_mutation();
