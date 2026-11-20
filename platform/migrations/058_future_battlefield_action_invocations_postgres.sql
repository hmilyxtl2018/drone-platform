-- P0-B: application Plan nodes must create auditable Action-Agent child Runs.
-- The row is mutable only for terminal status/output attachment; all authority
-- fields and hashes are retained for the life of the parent Run.

CREATE TABLE IF NOT EXISTS agent_action_invocation (
    invocation_id text PRIMARY KEY,
    run_id text NOT NULL REFERENCES agent_run(run_id),
    parent_step_run_id text NOT NULL,
    parent_step_key text NOT NULL,
    child_run_id text NOT NULL UNIQUE REFERENCES agent_run(run_id),
    composition_edge_id text NOT NULL REFERENCES agent_composition_edge(edge_id),
    idempotency_key text NOT NULL,
    input_artifact_id text NOT NULL REFERENCES agent_run_artifact(artifact_id),
    input_artifact_hash text NOT NULL CHECK (input_artifact_hash ~ '^[0-9a-f]{64}$'),
    output_artifact_id text REFERENCES agent_run_artifact(artifact_id),
    output_artifact_hash text CHECK (output_artifact_hash IS NULL OR output_artifact_hash ~ '^[0-9a-f]{64}$'),
    budget_grant_json jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(budget_grant_json)='object'),
    grant_hash text NOT NULL CHECK (grant_hash ~ '^[0-9a-f]{64}$'),
    status text NOT NULL DEFAULT 'running'
        CHECK (status IN ('planned','running','completed','completed-with-gaps','requires-human-review','blocked','failed','cancelled')),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK (run_id<>child_run_id),
    UNIQUE (run_id,idempotency_key)
);

CREATE INDEX IF NOT EXISTS agent_action_invocation_parent_idx
    ON agent_action_invocation(run_id,parent_step_key,created_at);
CREATE INDEX IF NOT EXISTS agent_action_invocation_child_idx
    ON agent_action_invocation(child_run_id,status);

DROP TRIGGER IF EXISTS agent_action_invocation_fence ON agent_action_invocation;
CREATE TRIGGER agent_action_invocation_fence BEFORE INSERT OR UPDATE ON agent_action_invocation
FOR EACH ROW EXECUTE FUNCTION agent_worker_fence_guard();

-- A child binding is executable only when the baseline node names it.  Store
-- this in the editable Profile document; the migration runner registers the
-- resulting baseline after applying this script.
UPDATE agent_model_policy
SET config_json=(config_json::jsonb || jsonb_build_object('executionBaseline',
    COALESCE(config_json::jsonb->'executionBaseline','{}'::jsonb) || jsonb_build_object('nodePolicies',
      COALESCE(config_json::jsonb #> '{executionBaseline,nodePolicies}','{}'::jsonb) || jsonb_build_object(
            'collect-public-evidence', jsonb_build_object('nodeType','action','childAgentKey','evidence-collection-action'),
            'extract-facts', jsonb_build_object('nodeType','action','childAgentKey','evidence-extraction-action'),
            'fuse-evidence-and-conflicts', jsonb_build_object('nodeType','action','childAgentKey','trend-analysis-action'),
            'analyze-by-perspective', jsonb_build_object('nodeType','action','childAgentKey','scenario-analysis-action'),
            'red-team-challenge', jsonb_build_object('nodeType','action','childAgentKey','red-team-action')
        ))))::text
WHERE agent_key='future-battlefield-analyst';

UPDATE agent_model_policy
SET config_json=jsonb_set(config_json::jsonb, '{capabilityActions}',
    (SELECT jsonb_agg(DISTINCT value ORDER BY value)
       FROM jsonb_array_elements_text(COALESCE(config_json::jsonb->'capabilityActions','[]'::jsonb) ||
            '["evidence-collection-action"]'::jsonb) AS values(value)), true)::text
WHERE agent_key='future-battlefield-analyst';

INSERT INTO agent_validation_rule(rule_id,agent_key,rule_type,expression,severity)
VALUES ('future-action-dispatch','future-battlefield-analyst','action-dispatch',
        'every baseline childAgentKey must create a child Run with input/output hashes and a shared budget grant','blocking')
ON CONFLICT (rule_id) DO NOTHING;

-- The parent Harness must explicitly grant the deterministic adapters used by
-- its child Actions.  The child profile still narrows these grants to its own
-- declared tool set; nothing is inherited implicitly at dispatch time.
INSERT INTO agent_tool_binding(agent_key,tool_key,tool_version,enabled,approval_required,config_json)
SELECT 'future-battlefield-analyst',tool_key,current_version,1,0,'{"timeoutSeconds":30,"retryLimit":1}'::jsonb
FROM tool_registry
WHERE tool_key IN ('analysis.scenario_structure','analysis.claim_audit')
ON CONFLICT (agent_key,tool_key) DO NOTHING;

-- Harness authority is append-only.  Publish a successor contract that adds
-- only the two deterministic Action adapters absent from the original 053
-- backfill; existing Runs keep their original authority snapshot.
DO $$
DECLARE old_id text := 'harness-future-battlefield-analyst-1.0.0';
DECLARE new_id text := 'harness-future-battlefield-analyst-1.0.1';
BEGIN
    IF EXISTS (SELECT 1 FROM agent_harness_contract WHERE harness_contract_id=old_id)
       AND NOT EXISTS (SELECT 1 FROM agent_harness_contract WHERE harness_contract_id=new_id) THEN
        INSERT INTO agent_harness_contract(
            harness_contract_id,agent_key,profile_version,contract_version,agent_layer,runtime_kind,
            invocation_mode,default_deny,max_child_depth,lifecycle_policy_json,failure_policy_json,
            memory_policy_json,side_effect_policy_json,contract_json,contract_hash,status,published_at)
        SELECT new_id,agent_key,profile_version,'1.0.1',agent_layer,runtime_kind,
               invocation_mode,default_deny,max_child_depth,lifecycle_policy_json,failure_policy_json,
               memory_policy_json,side_effect_policy_json,
               contract_json || '{"successor":"action-dispatch-v1"}'::jsonb,
               encode(sha256(convert_to((contract_json || '{"successor":"action-dispatch-v1"}'::jsonb)::text,'UTF8')),'hex'),
               status,published_at
          FROM agent_harness_contract WHERE harness_contract_id=old_id;
        INSERT INTO agent_harness_tool_grant(harness_contract_id,tool_key,tool_version,permission,approval_required,input_constraint_json,grant_config_json)
        SELECT new_id,tool_key,tool_version,permission,approval_required,input_constraint_json,grant_config_json
          FROM agent_harness_tool_grant WHERE harness_contract_id=old_id;
        INSERT INTO agent_harness_tool_grant(harness_contract_id,tool_key,tool_version,permission,approval_required,input_constraint_json,grant_config_json)
        SELECT new_id,tool_key,current_version,'invoke',false,'{}'::jsonb,'{}'::jsonb
          FROM tool_registry WHERE tool_key IN ('analysis.scenario_structure','analysis.claim_audit')
        ON CONFLICT DO NOTHING;
        INSERT INTO agent_harness_data_grant(grant_id,harness_contract_id,resource_type,resource_pattern,access_mode,sensitivity_level,approval_required)
        SELECT grant_id||'-v101',new_id,resource_type,resource_pattern,access_mode,sensitivity_level,approval_required
          FROM agent_harness_data_grant WHERE harness_contract_id=old_id;
        INSERT INTO agent_harness_network_grant(network_grant_id,harness_contract_id,domain_pattern,methods_json,max_response_bytes,approval_required)
        SELECT network_grant_id||'-v101',new_id,domain_pattern,methods_json,max_response_bytes,approval_required
          FROM agent_harness_network_grant WHERE harness_contract_id=old_id;
        INSERT INTO agent_harness_step_budget(harness_contract_id,step_key,max_rounds,model_calls,tool_calls,http_calls,search_calls,scrape_calls,input_tokens,output_tokens,max_parallelism,max_runtime_seconds,enforcement)
        SELECT new_id,step_key,max_rounds,model_calls,tool_calls,http_calls,search_calls,scrape_calls,input_tokens,output_tokens,max_parallelism,max_runtime_seconds,enforcement
          FROM agent_harness_step_budget WHERE harness_contract_id=old_id;
        UPDATE agent_definition SET default_harness_contract_id=new_id
         WHERE agent_key='future-battlefield-analyst';
    END IF;
END $$;
