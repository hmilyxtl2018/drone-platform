-- Batch 1: additive, opt-in research reflection. No historical backfill.
CREATE TABLE IF NOT EXISTS agent_evolution_policy_revision (
    policy_id text PRIMARY KEY,
    agent_key text NOT NULL REFERENCES agent_definition(agent_key),
    revision integer NOT NULL CHECK(revision > 0),
    document_json jsonb NOT NULL,
    content_hash text NOT NULL CHECK(length(content_hash)=64),
    created_by text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(agent_key,revision)
);
CREATE TABLE IF NOT EXISTS agent_evolution_policy (
    agent_key text PRIMARY KEY REFERENCES agent_definition(agent_key),
    policy_id text NOT NULL REFERENCES agent_evolution_policy_revision(policy_id),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS agent_run_terminal_outbox (
    outbox_id text PRIMARY KEY,
    source_run_id text NOT NULL REFERENCES agent_run(run_id),
    policy_id text NOT NULL REFERENCES agent_evolution_policy_revision(policy_id),
    source_status text NOT NULL,
    source_profile_hash text NOT NULL,
    source_output_hash text NOT NULL,
    source_output_json jsonb NOT NULL,
    status text NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','processed','skipped')),
    reason text,
    created_at timestamptz NOT NULL DEFAULT now(),
    processed_at timestamptz,
    UNIQUE(source_run_id,policy_id,source_output_hash)
);
CREATE INDEX IF NOT EXISTS agent_terminal_outbox_pending_idx ON agent_run_terminal_outbox(created_at) WHERE status='pending';
CREATE TABLE IF NOT EXISTS agent_evolution_cycle (
    cycle_id text PRIMARY KEY,
    outbox_id text NOT NULL UNIQUE REFERENCES agent_run_terminal_outbox(outbox_id),
    source_run_id text NOT NULL REFERENCES agent_run(run_id),
    policy_id text NOT NULL REFERENCES agent_evolution_policy_revision(policy_id),
    policy_hash text NOT NULL,
    source_profile_hash text NOT NULL,
    source_output_hash text NOT NULL,
    status text NOT NULL CHECK(status IN ('reflecting','reflected','stopped','failed')),
    max_trials integer NOT NULL DEFAULT 0 CHECK(max_trials=0),
    revision bigint NOT NULL DEFAULT 1,
    stop_reason text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS agent_evolution_run_binding (
    run_id text PRIMARY KEY REFERENCES agent_run(run_id),
    cycle_id text NOT NULL REFERENCES agent_evolution_cycle(cycle_id),
    role text NOT NULL CHECK(role='reflection'),
    input_hash text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(cycle_id,role)
);
CREATE TABLE IF NOT EXISTS agent_evolution_event (
    event_id bigserial PRIMARY KEY,
    cycle_id text NOT NULL REFERENCES agent_evolution_cycle(cycle_id),
    event_type text NOT NULL,
    detail_json jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS agent_evolution_source_idx ON agent_evolution_cycle(source_run_id,created_at);
DO $$ DECLARE target text; BEGIN
    FOREACH target IN ARRAY ARRAY['agent_evolution_policy_revision','agent_evolution_run_binding','agent_evolution_event'] LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS evolution_immutable ON %I',target);
        EXECUTE format('CREATE TRIGGER evolution_immutable BEFORE UPDATE OR DELETE ON %I FOR EACH ROW EXECUTE FUNCTION agent_research_history_immutable()',target);
        EXECUTE format('DROP TRIGGER IF EXISTS evolution_no_truncate ON %I',target);
        EXECUTE format('CREATE TRIGGER evolution_no_truncate BEFORE TRUNCATE ON %I FOR EACH STATEMENT EXECUTE FUNCTION agent_research_history_immutable()',target);
    END LOOP;
END $$;
DROP TRIGGER IF EXISTS evolution_binding_fence ON agent_evolution_run_binding;
CREATE TRIGGER evolution_binding_fence BEFORE INSERT ON agent_evolution_run_binding
FOR EACH ROW EXECUTE FUNCTION agent_worker_fence_guard();
