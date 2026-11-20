-- Continuous research authority is distinct from alignment confirmation.
CREATE TABLE IF NOT EXISTS agent_autopilot_mission (
 mission_id text PRIMARY KEY,
 principal_id text NOT NULL REFERENCES agent_admin_identity(principal_id),
 submission_key text NOT NULL,
 alignment_revision_id text REFERENCES agent_autopilot_alignment_revision(revision_id),
 goal_json jsonb NOT NULL,
 goal_hash text NOT NULL CHECK(length(goal_hash)=64),
 authority_kind text NOT NULL CHECK(authority_kind IN ('human-ui','explicit-user-delegation')),
 authority_json jsonb NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(principal_id,submission_key)
);
CREATE TABLE IF NOT EXISTS agent_autopilot_grant (
 grant_id text PRIMARY KEY,
 mission_id text NOT NULL REFERENCES agent_autopilot_mission(mission_id),
 revision integer NOT NULL CHECK(revision>0),
 document_json jsonb NOT NULL,
 content_hash text NOT NULL CHECK(length(content_hash)=64),
 expires_at timestamptz NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(mission_id,revision)
);
CREATE TABLE IF NOT EXISTS agent_autopilot_mission_event (
 event_id bigserial PRIMARY KEY,
 mission_id text NOT NULL REFERENCES agent_autopilot_mission(mission_id),
 grant_id text NOT NULL REFERENCES agent_autopilot_grant(grant_id),
 event_type text NOT NULL CHECK(event_type IN ('activated','paused','resumed','revoked','expired','exhausted','decision-resolved','dispatch-limited')),
 actor_kind text NOT NULL CHECK(actor_kind IN ('human','authorized-delegate','system')),
 detail_json jsonb NOT NULL DEFAULT '{}',
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS agent_autopilot_mission_state (
 mission_id text PRIMARY KEY REFERENCES agent_autopilot_mission(mission_id),
 grant_id text NOT NULL REFERENCES agent_autopilot_grant(grant_id),
 status text NOT NULL CHECK(status IN ('active','paused','revoked','expired','exhausted','needs-decision')),
 revision integer NOT NULL DEFAULT 1,
 next_check_at timestamptz NOT NULL DEFAULT now(),
 updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS agent_autopilot_trigger (
 trigger_id text PRIMARY KEY,
 mission_id text NOT NULL REFERENCES agent_autopilot_mission(mission_id),
 grant_id text NOT NULL REFERENCES agent_autopilot_grant(grant_id),
 kind text NOT NULL CHECK(kind IN ('goal-activated','freshness-check','evidence-gap','technical-retry','source-change')),
 dedup_key text NOT NULL,
 reference_json jsonb NOT NULL,
 content_hash text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(grant_id,dedup_key)
);
CREATE TABLE IF NOT EXISTS agent_autopilot_proposal (
 proposal_id text PRIMARY KEY,
 trigger_id text NOT NULL UNIQUE REFERENCES agent_autopilot_trigger(trigger_id),
 mission_id text NOT NULL REFERENCES agent_autopilot_mission(mission_id),
 grant_id text NOT NULL REFERENCES agent_autopilot_grant(grant_id),
 parent_proposal_id text REFERENCES agent_autopilot_proposal(proposal_id),
 attempt integer NOT NULL DEFAULT 0 CHECK(attempt BETWEEN 0 AND 2),
 request_json jsonb NOT NULL,
 content_hash text NOT NULL,
 status text NOT NULL CHECK(status IN ('proposed','queued','evaluated','limited','failed')),
 run_id text UNIQUE REFERENCES agent_run(run_id),
 error_code text,
 created_at timestamptz NOT NULL DEFAULT now(),
 updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS agent_autopilot_run_binding (
 run_id text PRIMARY KEY REFERENCES agent_run(run_id),
 mission_id text NOT NULL REFERENCES agent_autopilot_mission(mission_id),
 grant_id text NOT NULL REFERENCES agent_autopilot_grant(grant_id),
 proposal_id text NOT NULL REFERENCES agent_autopilot_proposal(proposal_id),
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS agent_autopilot_run_mission ON agent_autopilot_run_binding(mission_id,created_at);
CREATE TABLE IF NOT EXISTS agent_autopilot_assessment (
 assessment_id text PRIMARY KEY,
 proposal_id text NOT NULL UNIQUE REFERENCES agent_autopilot_proposal(proposal_id),
 run_id text NOT NULL UNIQUE REFERENCES agent_run(run_id),
 report_hash text NOT NULL,
 document_json jsonb NOT NULL,
 content_hash text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS agent_autopilot_decision (
 decision_id text PRIMARY KEY,
 mission_id text NOT NULL REFERENCES agent_autopilot_mission(mission_id),
 grant_id text NOT NULL REFERENCES agent_autopilot_grant(grant_id),
 proposal_id text REFERENCES agent_autopilot_proposal(proposal_id),
 kind text NOT NULL CHECK(kind IN ('budget','contract-change','goal-change')),
 dedup_key text NOT NULL,
 document_json jsonb NOT NULL,
 content_hash text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(grant_id,dedup_key)
);
CREATE TABLE IF NOT EXISTS agent_autopilot_decision_event (
 event_id text PRIMARY KEY,
 decision_id text NOT NULL REFERENCES agent_autopilot_decision(decision_id),
 principal_id text NOT NULL REFERENCES agent_admin_identity(principal_id),
 action text NOT NULL CHECK(action IN ('keep-boundary','defer','request-new-grant')),
 idempotency_key text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(decision_id,idempotency_key)
);
DO $$ DECLARE n text; BEGIN
 FOREACH n IN ARRAY ARRAY['agent_autopilot_mission','agent_autopilot_grant','agent_autopilot_mission_event',
 'agent_autopilot_trigger','agent_autopilot_run_binding','agent_autopilot_assessment',
 'agent_autopilot_decision','agent_autopilot_decision_event'] LOOP
  EXECUTE format('DROP TRIGGER IF EXISTS autopilot_immutable ON %I',n);
  EXECUTE format('CREATE TRIGGER autopilot_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON %I
    FOR EACH STATEMENT EXECUTE FUNCTION agent_autopilot_alignment_immutable()',n);
 END LOOP;
END $$;
