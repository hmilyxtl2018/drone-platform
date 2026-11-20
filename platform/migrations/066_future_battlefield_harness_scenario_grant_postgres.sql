-- Publish fresh Harness snapshots for the scenario Action tool version. Old
-- Harness rows stay immutable and continue to validate historical Runs.
DO $$
DECLARE old_id text; new_id text; new_version text; agent text;
BEGIN
    FOREACH agent IN ARRAY ARRAY['future-battlefield-analyst','scenario-analysis-action'] LOOP
        SELECT default_harness_contract_id INTO old_id FROM agent_definition WHERE agent_key=agent;
        IF old_id IS NULL THEN CONTINUE; END IF;
        new_version := CASE WHEN agent='future-battlefield-analyst' THEN '1.0.2' ELSE '1.0.1' END;
        new_id := 'harness-'||agent||'-'||new_version;
        INSERT INTO agent_harness_contract(
            harness_contract_id,agent_key,profile_version,contract_version,agent_layer,runtime_kind,
            invocation_mode,default_deny,max_child_depth,lifecycle_policy_json,failure_policy_json,
            memory_policy_json,side_effect_policy_json,contract_json,contract_hash,status,published_at)
        SELECT new_id,agent_key,profile_version,new_version,agent_layer,runtime_kind,
               invocation_mode,default_deny,max_child_depth,lifecycle_policy_json,failure_policy_json,
               memory_policy_json,side_effect_policy_json,contract_json,contract_hash,status,published_at
          FROM agent_harness_contract WHERE harness_contract_id=old_id
        ON CONFLICT (agent_key,profile_version,contract_version) DO NOTHING;
        INSERT INTO agent_harness_tool_grant(
            harness_contract_id,tool_key,tool_version,permission,approval_required,input_constraint_json,grant_config_json)
        SELECT new_id,tool_key,CASE WHEN tool_key='analysis.scenario_structure' THEN '1.2.0' ELSE tool_version END,
               permission,approval_required,input_constraint_json,grant_config_json
          FROM agent_harness_tool_grant WHERE harness_contract_id=old_id
        ON CONFLICT DO NOTHING;
        INSERT INTO agent_harness_data_grant(
            grant_id,harness_contract_id,resource_type,resource_pattern,access_mode,sensitivity_level,approval_required)
        SELECT grant_id||'-'||new_version,new_id,resource_type,resource_pattern,access_mode,sensitivity_level,approval_required
          FROM agent_harness_data_grant WHERE harness_contract_id=old_id
        ON CONFLICT DO NOTHING;
        INSERT INTO agent_harness_network_grant(
            network_grant_id,harness_contract_id,domain_pattern,methods_json,max_response_bytes,approval_required)
        SELECT network_grant_id||'-'||new_version,new_id,domain_pattern,methods_json,max_response_bytes,approval_required
          FROM agent_harness_network_grant WHERE harness_contract_id=old_id
        ON CONFLICT DO NOTHING;
        INSERT INTO agent_harness_step_budget(
            harness_contract_id,step_key,max_rounds,model_calls,tool_calls,http_calls,search_calls,scrape_calls,
            input_tokens,output_tokens,max_parallelism,max_runtime_seconds,enforcement,max_iterations,max_retries,
            estimated_cost_micros,max_evidence_bytes,max_concurrency)
        SELECT new_id,step_key,max_rounds,model_calls,tool_calls,http_calls,search_calls,scrape_calls,
               input_tokens,output_tokens,max_parallelism,max_runtime_seconds,enforcement,max_iterations,max_retries,
               estimated_cost_micros,max_evidence_bytes,max_concurrency
          FROM agent_harness_step_budget WHERE harness_contract_id=old_id
        ON CONFLICT DO NOTHING;
        UPDATE agent_definition SET default_harness_contract_id=new_id WHERE agent_key=agent;
    END LOOP;
END;
$$;
