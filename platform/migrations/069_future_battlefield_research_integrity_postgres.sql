-- New Runs only: complete the fixed-DAG research boundary and preserve history.
INSERT INTO tool_version(tool_key,version,input_schema,output_schema,changelog,status,
                         side_effect_class,idempotency_required,network_access,data_classification,contract_hash)
SELECT tool_key,'3.1.0',input_schema,output_schema,
       'Audited bounded external reads; immutable search and body snapshots','active',
       'external_read',false,true,'public',
       encode(sha256(convert_to(tool_key||':3.1.0:'||input_schema||':'||output_schema,'UTF8')),'hex')
  FROM tool_version WHERE tool_key IN ('web.search','web.scrape') AND version='3.0.0'
ON CONFLICT (tool_key,version) DO NOTHING;

UPDATE agent_tool_binding SET tool_version='3.1.0'
 WHERE agent_key='future-battlefield-analyst' AND tool_key IN ('web.search','web.scrape');
UPDATE agent_step SET dependencies_json='["extract-facts"]'
 WHERE agent_key='future-battlefield-analyst' AND step_key='extract-war-system';
UPDATE agent_output_contract
   SET schema_json=jsonb_set(schema_json::jsonb,
       '{properties,findings,items,properties,supportingEvidence,items,properties,sourceArtifactHash}',
       '{"type":"string","pattern":"^[0-9a-f]{64}$"}'::jsonb,true)::text
 WHERE agent_key='future-battlefield-analyst';
-- Prior repeatable migrations appended duplicate required field names.
UPDATE agent_output_contract SET schema_json=jsonb_set(schema_json::jsonb,'{required}',
    (SELECT jsonb_agg(value ORDER BY first_position) FROM (
        SELECT value,min(ordinality) AS first_position
          FROM jsonb_array_elements(schema_json::jsonb->'required') WITH ORDINALITY
         GROUP BY value) names))::text
 WHERE agent_key='future-battlefield-analyst';

DO $$
DECLARE old_id text := 'harness-future-battlefield-analyst-1.0.3';
        budget_id text := 'harness-future-battlefield-analyst-1.0.2';
        new_id text := 'harness-future-battlefield-analyst-1.0.4';
        definition jsonb;
BEGIN
    SELECT contract_json || jsonb_build_object('contractVersion','1.0.4',
        'researchBoundary',jsonb_build_object('runtimeTruthGate','fixed-runtime-preflight-v2',
            'webToolVersion','3.1.0','providerPostDomains',jsonb_build_array('api.firecrawl.dev','api.deepseek.com'),
            'sourceGetDomains',(SELECT config_json::jsonb->'sourcePolicy'->'allowedDomains'
                FROM agent_model_policy WHERE agent_key='future-battlefield-analyst'),
            'requiresValidatedEvidence',true,'sourceSubdomains',true)) INTO definition
      FROM agent_harness_contract WHERE harness_contract_id=old_id;
    IF definition IS NULL THEN RAISE EXCEPTION 'Future research Harness predecessor missing'; END IF;
    INSERT INTO agent_harness_contract(
        harness_contract_id,agent_key,profile_version,contract_version,agent_layer,runtime_kind,
        invocation_mode,default_deny,max_child_depth,lifecycle_policy_json,failure_policy_json,
        memory_policy_json,side_effect_policy_json,contract_json,contract_hash,status,published_at)
    SELECT new_id,agent_key,profile_version,'1.0.4',agent_layer,runtime_kind,
           invocation_mode,default_deny,max_child_depth,lifecycle_policy_json,failure_policy_json,
           memory_policy_json,side_effect_policy_json,definition,
           encode(sha256(convert_to(definition::text,'UTF8')),'hex'),'testing',NULL
      FROM agent_harness_contract WHERE harness_contract_id=old_id
    ON CONFLICT (agent_key,profile_version,contract_version) DO NOTHING;
    INSERT INTO agent_harness_tool_grant(harness_contract_id,tool_key,tool_version,permission,
        approval_required,input_constraint_json,grant_config_json)
    SELECT new_id,tool_key,CASE WHEN tool_key IN ('web.search','web.scrape') THEN '3.1.0' ELSE tool_version END,
           permission,approval_required,input_constraint_json,grant_config_json
      FROM agent_harness_tool_grant WHERE harness_contract_id=old_id ON CONFLICT DO NOTHING;
    INSERT INTO agent_harness_data_grant(grant_id,harness_contract_id,resource_type,resource_pattern,
        access_mode,sensitivity_level,approval_required)
    SELECT 'fb104-'||md5(grant_id),new_id,resource_type,resource_pattern,
           access_mode,sensitivity_level,approval_required
      FROM agent_harness_data_grant WHERE harness_contract_id=old_id ON CONFLICT DO NOTHING;
    INSERT INTO agent_harness_network_grant(network_grant_id,harness_contract_id,domain_pattern,
        methods_json,max_response_bytes,approval_required)
    SELECT 'fb104-source-'||md5(pattern),new_id,pattern,'["GET"]'::jsonb,8388608,false
      FROM (SELECT domain AS pattern FROM jsonb_array_elements_text(definition->'researchBoundary'->'sourceGetDomains') d(domain)
            UNION SELECT '*.'||domain FROM jsonb_array_elements_text(definition->'researchBoundary'->'sourceGetDomains') d(domain)) domains
    ON CONFLICT DO NOTHING;
    INSERT INTO agent_harness_network_grant(network_grant_id,harness_contract_id,domain_pattern,
        methods_json,max_response_bytes,approval_required)
    VALUES ('fb104-firecrawl',new_id,'api.firecrawl.dev','["POST"]'::jsonb,8388608,false),
           ('fb104-deepseek',new_id,'api.deepseek.com','["POST"]'::jsonb,8388608,false)
    ON CONFLICT DO NOTHING;
    -- Copy every dimension from the last complete budget snapshot.
    INSERT INTO agent_harness_step_budget(harness_contract_id,step_key,max_rounds,model_calls,tool_calls,
        http_calls,search_calls,scrape_calls,input_tokens,output_tokens,max_parallelism,max_runtime_seconds,
        enforcement,max_iterations,max_retries,estimated_cost_micros,max_evidence_bytes,max_concurrency)
    SELECT new_id,step_key,max_rounds,model_calls,tool_calls,http_calls,search_calls,scrape_calls,
           input_tokens,output_tokens,max_parallelism,max_runtime_seconds,enforcement,max_iterations,
           max_retries,estimated_cost_micros,max_evidence_bytes,max_concurrency
      FROM agent_harness_step_budget WHERE harness_contract_id=budget_id ON CONFLICT DO NOTHING;
    UPDATE agent_definition SET default_harness_contract_id=new_id
     WHERE agent_key='future-battlefield-analyst';
END $$;
