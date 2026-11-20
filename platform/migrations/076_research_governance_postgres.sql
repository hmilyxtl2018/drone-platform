-- Additive research governance. Historical Runs intentionally remain unbound.
CREATE TABLE IF NOT EXISTS agent_task_intent_revision (
    intent_id text PRIMARY KEY,
    task_id text NOT NULL,
    revision integer NOT NULL CHECK(revision > 0),
    parent_intent_id text REFERENCES agent_task_intent_revision(intent_id),
    document_json jsonb NOT NULL,
    content_hash text NOT NULL CHECK(length(content_hash)=64),
    issuer_id text NOT NULL,
    issuer_kind text NOT NULL CHECK(issuer_kind IN ('human','system','delegated')),
    expires_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(task_id,revision)
);
CREATE TABLE IF NOT EXISTS agent_run_task_intent (
    run_id text PRIMARY KEY REFERENCES agent_run(run_id),
    intent_id text NOT NULL UNIQUE REFERENCES agent_task_intent_revision(intent_id),
    content_hash text NOT NULL,
    parent_run_id text REFERENCES agent_run(run_id),
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS agent_run_task_intent_parent_idx ON agent_run_task_intent(parent_run_id);
CREATE TABLE IF NOT EXISTS agent_task_intent_event (
    event_id bigserial PRIMARY KEY,
    intent_id text NOT NULL REFERENCES agent_task_intent_revision(intent_id),
    event_type text NOT NULL CHECK(event_type='revoked'),
    actor_id text NOT NULL,
    reason text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(intent_id,event_type)
);
CREATE TABLE IF NOT EXISTS agent_improvement_proposal (
    proposal_id text PRIMARY KEY,
    agent_key text NOT NULL REFERENCES agent_definition(agent_key),
    source_run_id text NOT NULL REFERENCES agent_run(run_id),
    draft_id text NOT NULL UNIQUE REFERENCES agent_config_draft(draft_id),
    draft_revision bigint NOT NULL,
    base_live_hash text NOT NULL,
    candidate_hash text NOT NULL,
    diff_json jsonb NOT NULL,
    rationale_json jsonb NOT NULL,
    created_by text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY(draft_id,draft_revision) REFERENCES agent_config_draft_revision(draft_id,revision)
);
CREATE TABLE IF NOT EXISTS agent_improvement_evaluation (
    assessment_id text PRIMARY KEY,
    proposal_id text NOT NULL REFERENCES agent_improvement_proposal(proposal_id),
    candidate_hash text NOT NULL,
    candidate_run_id text NOT NULL REFERENCES agent_run(run_id),
    evaluation_run_id text REFERENCES agent_evaluation_run(evaluation_run_id),
    baseline_evaluation_run_id text REFERENCES agent_evaluation_run(evaluation_run_id),
    checks_json jsonb NOT NULL,
    passed boolean NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(proposal_id,candidate_run_id)
);
CREATE TABLE IF NOT EXISTS agent_improvement_candidate_run (
    run_id text PRIMARY KEY REFERENCES agent_run(run_id),
    proposal_id text NOT NULL REFERENCES agent_improvement_proposal(proposal_id),
    candidate_hash text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS agent_release_decision (
    decision_id text PRIMARY KEY,
    proposal_id text NOT NULL REFERENCES agent_improvement_proposal(proposal_id),
    candidate_hash text NOT NULL,
    assessment_id text NOT NULL REFERENCES agent_improvement_evaluation(assessment_id),
    decision text NOT NULL CHECK(decision IN ('approved','rejected')),
    actor_id text NOT NULL REFERENCES agent_admin_identity(principal_id),
    reason text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE OR REPLACE FUNCTION agent_research_history_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'research governance history is append-only' USING ERRCODE='55000';
END; $$;
DO $$ DECLARE target text; BEGIN
    FOREACH target IN ARRAY ARRAY['agent_task_intent_revision','agent_run_task_intent',
        'agent_task_intent_event','agent_improvement_proposal','agent_improvement_evaluation','agent_improvement_candidate_run','agent_release_decision'] LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS research_history_immutable ON %I',target);
        EXECUTE format('CREATE TRIGGER research_history_immutable BEFORE UPDATE OR DELETE ON %I FOR EACH ROW EXECUTE FUNCTION agent_research_history_immutable()',target);
        EXECUTE format('DROP TRIGGER IF EXISTS research_history_no_truncate ON %I',target);
        EXECUTE format('CREATE TRIGGER research_history_no_truncate BEFORE TRUNCATE ON %I FOR EACH STATEMENT EXECUTE FUNCTION agent_research_history_immutable()',target);
    END LOOP;
END; $$;
DROP TRIGGER IF EXISTS research_intent_fence ON agent_run_task_intent;
CREATE TRIGGER research_intent_fence BEFORE INSERT ON agent_run_task_intent
FOR EACH ROW EXECUTE FUNCTION agent_worker_fence_guard();
