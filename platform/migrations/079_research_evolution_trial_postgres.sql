-- One bounded, offline report-quality experiment. Historical cycles retain their policy.
-- This credential-free principal can author cycle-bound drafts only. Human auth
-- and all decision/publication APIs continue rejecting non-human identities.
ALTER TABLE agent_admin_identity DROP CONSTRAINT IF EXISTS agent_admin_identity_kind_check;
ALTER TABLE agent_admin_identity DROP CONSTRAINT IF EXISTS agent_admin_identity_roles_json_check;
ALTER TABLE agent_admin_identity ADD CONSTRAINT agent_admin_identity_kind_check CHECK
 ((kind='human' AND jsonb_typeof(roles_json)='array' AND jsonb_array_length(roles_json)>0
    AND roles_json <@ '["operator","reviewer","admin"]'::jsonb)
  OR (kind='service' AND principal_id='service-research-evolution' AND roles_json='[]'::jsonb));
INSERT INTO agent_admin_identity(principal_id,display_name,roles_json,kind)
 VALUES('service-research-evolution','Research evolution draft service','[]','service') ON CONFLICT DO NOTHING;
ALTER TABLE agent_evolution_cycle DROP CONSTRAINT IF EXISTS agent_evolution_cycle_status_check;
ALTER TABLE agent_evolution_cycle ADD CONSTRAINT agent_evolution_cycle_status_check CHECK
 (status IN ('reflecting','reflected','designing','trial-running','evaluating','awaiting-approval',
             'not-improved','insufficient-validation','stopped','failed'));
ALTER TABLE agent_evolution_cycle DROP CONSTRAINT IF EXISTS agent_evolution_cycle_max_trials_check;
ALTER TABLE agent_evolution_cycle ADD CONSTRAINT agent_evolution_cycle_max_trials_check CHECK(max_trials IN (0,1));
ALTER TABLE agent_evolution_run_binding DROP CONSTRAINT IF EXISTS agent_evolution_run_binding_role_check;
ALTER TABLE agent_evolution_run_binding ADD CONSTRAINT agent_evolution_run_binding_role_check CHECK
 (role IN ('reflection','designer','baseline','candidate'));
CREATE TABLE IF NOT EXISTS agent_evolution_trial (
 cycle_id text PRIMARY KEY REFERENCES agent_evolution_cycle(cycle_id),
 agent_key text NOT NULL REFERENCES agent_definition(agent_key),
 base_live_hash text NOT NULL,
 base_document_json jsonb NOT NULL,
 case_json jsonb NOT NULL,
 case_hash text NOT NULL,
 evaluator_version text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS agent_evolution_proposal_binding (
 cycle_id text PRIMARY KEY REFERENCES agent_evolution_trial(cycle_id),
 proposal_id text NOT NULL UNIQUE REFERENCES agent_improvement_proposal(proposal_id),
 designer_run_id text NOT NULL REFERENCES agent_run(run_id),
 created_at timestamptz NOT NULL DEFAULT now()
);
DO $$ DECLARE target text; BEGIN
 FOREACH target IN ARRAY ARRAY['agent_evolution_trial','agent_evolution_proposal_binding'] LOOP
  EXECUTE format('DROP TRIGGER IF EXISTS evolution_immutable ON %I',target);
  EXECUTE format('CREATE TRIGGER evolution_immutable BEFORE UPDATE OR DELETE ON %I FOR EACH ROW EXECUTE FUNCTION agent_research_history_immutable()',target);
  EXECUTE format('DROP TRIGGER IF EXISTS evolution_no_truncate ON %I',target);
  EXECUTE format('CREATE TRIGGER evolution_no_truncate BEFORE TRUNCATE ON %I FOR EACH STATEMENT EXECUTE FUNCTION agent_research_history_immutable()',target);
 END LOOP;
END $$;
