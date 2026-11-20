-- P0 Future Battlefield research enablement.
--
-- This migration is additive. It publishes a new Harness snapshot for new
-- Runs; historical Harness snapshots and Run profile snapshots remain
-- immutable and replayable. The fixed DAG remains the execution baseline, but
-- its collection node is now an explicitly bounded Research boundary.

INSERT INTO tool_registry(tool_key,tool_name,category,description,protocol,input_schema,output_schema,
                          tags_json,access_mode,risk_level,status,current_version)
VALUES
('web.search','Firecrawl bounded web search','web','Search only the frozen public source allowlist','python',
 '{"type":"object","required":["input","state"],"properties":{"input":{"type":"object"},"state":{"type":"object"},"arguments":{"type":"object","required":["query"],"properties":{"query":{"type":"string","minLength":1,"maxLength":1000},"limit":{"type":"integer","minimum":1,"maximum":6}},"additionalProperties":false}},"additionalProperties":false}',
 '{"type":"object","required":["status","results","query","retrievedAt","excludedCount"]}',
 '["future_battlefield","research","web"]','read_only','low','active','3.0.0'),
('web.scrape','Firecrawl bounded source scrape','web','Scrape only URLs returned by a prior bounded search','python',
 '{"type":"object","required":["input","state"],"properties":{"input":{"type":"object"},"state":{"type":"object"},"arguments":{"type":"object","required":["url"],"properties":{"url":{"type":"string","minLength":1,"maxLength":4096}},"additionalProperties":false}},"additionalProperties":false}',
 '{"type":"object","required":["status","sourceUri","artifactId","contentHash","retrievedAt","contentKind"]}',
 '["future_battlefield","research","web"]','read_only','low','active','3.0.0')
ON CONFLICT (tool_key) DO NOTHING;

INSERT INTO tool_version(tool_key,version,input_schema,output_schema,changelog,status)
SELECT tool_key,current_version,input_schema,output_schema,
       'bounded Firecrawl research adapter for Future Battlefield', 'active'
  FROM tool_registry WHERE tool_key IN ('web.search','web.scrape')
ON CONFLICT (tool_key,version) DO NOTHING;

INSERT INTO agent_tool_binding(agent_key,tool_key,tool_version,enabled,approval_required,config_json)
VALUES
('future-battlefield-analyst','web.search','3.0.0',1,0,'{"timeoutSeconds":45,"retryLimit":0}'::jsonb),
('future-battlefield-analyst','web.scrape','3.0.0',1,0,'{"timeoutSeconds":45,"retryLimit":0}'::jsonb)
ON CONFLICT (agent_key,tool_key) DO UPDATE
SET tool_version=EXCLUDED.tool_version, enabled=EXCLUDED.enabled,
    approval_required=EXCLUDED.approval_required, config_json=EXCLUDED.config_json;

INSERT INTO agent_step_tool(step_id,tool_key)
SELECT step_id,'web.search' FROM agent_step
 WHERE agent_key='future-battlefield-analyst' AND step_key='collect-public-evidence'
ON CONFLICT (step_id,tool_key) DO NOTHING;
INSERT INTO agent_step_tool(step_id,tool_key)
SELECT step_id,'web.scrape' FROM agent_step
 WHERE agent_key='future-battlefield-analyst' AND step_key='collect-public-evidence'
ON CONFLICT (step_id,tool_key) DO NOTHING;

UPDATE agent_model_policy
SET config_json=(config_json::jsonb || '{
    "researchModeAvailable": true,
    "runtimeTruthGate": "fixed-runtime-preflight-v2",
    "webProviderKey": "firecrawl",
    "budgetLimits": {"model_calls":24,"input_tokens":240000,"output_tokens":96000,
                      "tool_calls":80,"http_calls":40,"search_calls":20,"scrape_calls":20},
    "sourcePolicy": {"allowedDomains":["defense.gov","nato.int","rand.org","csis.org","gov.cn"],
                     "includeSubdomains":true,"maxSources":12,"maxPages":20,
                     "maxMarkdownChars":150000}
}'::jsonb)::text
WHERE agent_key='future-battlefield-analyst';

-- Publish a fresh immutable Harness contract with the new Web grants. The
-- existing contract is selected from the Profile's current default and is
-- copied verbatim before adding the explicitly reviewed grants.
DO $$
DECLARE old_id text; new_id text := 'harness-future-battlefield-analyst-1.0.3';
BEGIN
    SELECT default_harness_contract_id INTO old_id
      FROM agent_definition WHERE agent_key='future-battlefield-analyst';
    IF old_id IS NULL THEN
        RAISE EXCEPTION 'Future Battlefield Harness contract is missing';
    END IF;
    INSERT INTO agent_harness_contract(
        harness_contract_id,agent_key,profile_version,contract_version,agent_layer,runtime_kind,
        invocation_mode,default_deny,max_child_depth,lifecycle_policy_json,failure_policy_json,
        memory_policy_json,side_effect_policy_json,contract_json,contract_hash,status,published_at)
    SELECT new_id,agent_key,profile_version,'1.0.3',agent_layer,runtime_kind,
           invocation_mode,default_deny,max_child_depth,lifecycle_policy_json,failure_policy_json,
           memory_policy_json,side_effect_policy_json,contract_json,contract_hash,'testing',NULL
      FROM agent_harness_contract WHERE harness_contract_id=old_id
    ON CONFLICT (agent_key,profile_version,contract_version) DO NOTHING;

    INSERT INTO agent_harness_tool_grant(
        harness_contract_id,tool_key,tool_version,permission,approval_required,
        input_constraint_json,grant_config_json)
    SELECT new_id,tool_key,tool_version,permission,approval_required,
           input_constraint_json,grant_config_json
      FROM agent_harness_tool_grant WHERE harness_contract_id=old_id
    ON CONFLICT DO NOTHING;
    INSERT INTO agent_harness_tool_grant(
        harness_contract_id,tool_key,tool_version,permission,approval_required,
        input_constraint_json,grant_config_json)
    VALUES (new_id,'web.search','3.0.0','invoke',false,'{}'::jsonb,
            '{"timeoutSeconds":45,"retryLimit":0}'::jsonb),
           (new_id,'web.scrape','3.0.0','invoke',false,'{}'::jsonb,
            '{"timeoutSeconds":45,"retryLimit":0}'::jsonb)
    ON CONFLICT DO NOTHING;

    INSERT INTO agent_harness_data_grant(
        grant_id,harness_contract_id,resource_type,resource_pattern,access_mode,
        sensitivity_level,approval_required)
    SELECT grant_id||'-v103',new_id,resource_type,resource_pattern,access_mode,
           sensitivity_level,approval_required
      FROM agent_harness_data_grant WHERE harness_contract_id=old_id
    ON CONFLICT DO NOTHING;
    INSERT INTO agent_harness_network_grant(
        network_grant_id,harness_contract_id,domain_pattern,methods_json,
        max_response_bytes,approval_required)
    SELECT network_grant_id||'-v103',new_id,domain_pattern,methods_json,
           max_response_bytes,approval_required
      FROM agent_harness_network_grant WHERE harness_contract_id=old_id
    ON CONFLICT DO NOTHING;
    INSERT INTO agent_harness_network_grant(
        network_grant_id,harness_contract_id,domain_pattern,methods_json,
        max_response_bytes,approval_required)
    SELECT 'net-grant-future-battlefield-1.0.3-'||md5(domain),new_id,domain,
           '["POST"]'::jsonb,8388608,false
      FROM jsonb_array_elements_text('["defense.gov","nato.int","rand.org","csis.org","gov.cn"]'::jsonb) domains(domain)
    ON CONFLICT DO NOTHING;

    INSERT INTO agent_harness_step_budget(
        harness_contract_id,step_key,max_rounds,model_calls,tool_calls,http_calls,
        search_calls,scrape_calls,input_tokens,output_tokens,max_parallelism,
        max_runtime_seconds,enforcement)
    SELECT new_id,step_key,max_rounds,model_calls,tool_calls,http_calls,
           search_calls,scrape_calls,input_tokens,output_tokens,max_parallelism,
           max_runtime_seconds,enforcement
      FROM agent_harness_step_budget WHERE harness_contract_id=old_id
    ON CONFLICT DO NOTHING;
    UPDATE agent_definition SET default_harness_contract_id=new_id
     WHERE agent_key='future-battlefield-analyst';
END;
$$;

-- The Profile remains in testing until an operator promotes it. New Runs now
-- freeze the enabled Research policy and the 1.0.3 Harness grants.
UPDATE agent_definition SET status='testing'
 WHERE agent_key='future-battlefield-analyst' AND status NOT IN ('disabled','inactive','retired');
