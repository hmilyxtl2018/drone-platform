-- New snapshots move reasoning to reviewed Action children. Historical Run,
-- model-call, Harness and baseline snapshots are deliberately left intact.
UPDATE agent_model_policy SET config_json=(config_json::jsonb ||
    '{"actionReasoningVersion":"child-v1"}'::jsonb)::text
WHERE agent_key IN ('future-battlefield-analyst','future-battlefield-codex',
                    'evidence-extraction-action','scenario-analysis-action');
UPDATE agent_model_policy SET config_json=(config_json::jsonb ||
    '{"actionExecutor":"reasoning-action-v1"}'::jsonb)::text
WHERE agent_key IN ('evidence-extraction-action','scenario-analysis-action');

INSERT INTO agent_step(step_id,agent_key,sequence_no,step_key,name,description,input_contract,
    output_contract,parallel_group,required,retry_limit,dependencies_json)
VALUES
('action-extract-source-claims-v1','evidence-extraction-action',0,'extract-source-claims','提取原文引文',
 '从委派材料中提取精确引文候选，推理和配额归本子运行。','{"type":"object"}',
 '{"type":"object","required":["stepKey","claims","gaps"]}',NULL,1,0,'[]'),
('action-review-perspective-evidence-v1','scenario-analysis-action',0,'review-perspective-evidence','研判与证据复核',
 '生成高层次条件判断并单独复核引文支持；保留模型相关性和未知项。','{"type":"object"}',
 '{"type":"object","required":["stepKey","gaps"]}',NULL,1,0,'[]')
ON CONFLICT(agent_key,step_key) DO NOTHING;
UPDATE agent_step SET dependencies_json='["extract-source-claims"]'
WHERE agent_key='evidence-extraction-action' AND step_key='validate-citations';
UPDATE agent_step SET dependencies_json='["review-perspective-evidence"]'
WHERE agent_key='scenario-analysis-action' AND step_key='structure-scenario-judgment';

-- Publish immutable child Harness successors, permitting only the configured
-- provider POST. Effective child authority is still intersected with the parent.
DO $$
DECLARE old_id text; new_id text; agent text; model_step text;
BEGIN
 FOREACH agent IN ARRAY ARRAY['evidence-extraction-action','scenario-analysis-action'] LOOP
  SELECT default_harness_contract_id INTO old_id FROM agent_definition WHERE agent_key=agent;
  new_id := 'harness-'||agent||'-1.1.0';
  IF old_id=new_id THEN CONTINUE; END IF;
  model_step := CASE WHEN agent='evidence-extraction-action' THEN 'extract-source-claims' ELSE 'review-perspective-evidence' END;
  INSERT INTO agent_harness_contract(harness_contract_id,agent_key,profile_version,contract_version,
   agent_layer,runtime_kind,invocation_mode,default_deny,max_child_depth,lifecycle_policy_json,
   failure_policy_json,memory_policy_json,side_effect_policy_json,contract_json,contract_hash,status,published_at)
  SELECT new_id,agent_key,profile_version,'1.1.0',agent_layer,runtime_kind,invocation_mode,default_deny,
   max_child_depth,lifecycle_policy_json,failure_policy_json,memory_policy_json,side_effect_policy_json,
   contract_json,contract_hash,status,published_at FROM agent_harness_contract WHERE harness_contract_id=old_id
  ON CONFLICT DO NOTHING;
  INSERT INTO agent_harness_tool_grant(harness_contract_id,tool_key,tool_version,permission,
   approval_required,input_constraint_json,grant_config_json)
  SELECT new_id,tool_key,tool_version,permission,approval_required,input_constraint_json,grant_config_json
   FROM agent_harness_tool_grant WHERE harness_contract_id=old_id ON CONFLICT DO NOTHING;
  INSERT INTO agent_harness_data_grant(grant_id,harness_contract_id,resource_type,resource_pattern,
   access_mode,sensitivity_level,approval_required)
  SELECT grant_id||'-reasoning-v1',new_id,resource_type,resource_pattern,access_mode,sensitivity_level,approval_required
   FROM agent_harness_data_grant WHERE harness_contract_id=old_id ON CONFLICT DO NOTHING;
  INSERT INTO agent_harness_network_grant(network_grant_id,harness_contract_id,domain_pattern,
   methods_json,max_response_bytes,approval_required)
  VALUES(new_id||'-provider',new_id,'api.deepseek.com','["POST"]',2000000,false) ON CONFLICT DO NOTHING;
  INSERT INTO agent_harness_step_budget(harness_contract_id,step_key,max_rounds,model_calls,tool_calls,
   http_calls,search_calls,scrape_calls,input_tokens,output_tokens,max_parallelism,max_runtime_seconds,
   enforcement,max_iterations,max_retries,estimated_cost_micros,max_evidence_bytes,max_concurrency)
  SELECT new_id,step_key,max_rounds,0,tool_calls,http_calls,0,0,0,0,max_parallelism,max_runtime_seconds,
   enforcement,max_iterations,max_retries,estimated_cost_micros,max_evidence_bytes,max_concurrency
   FROM agent_harness_step_budget WHERE harness_contract_id=old_id ON CONFLICT DO NOTHING;
  INSERT INTO agent_harness_step_budget(harness_contract_id,step_key,max_rounds,model_calls,tool_calls,
   http_calls,search_calls,scrape_calls,input_tokens,output_tokens,max_parallelism,max_runtime_seconds,
   enforcement,max_iterations,max_retries,estimated_cost_micros,max_evidence_bytes,max_concurrency)
  VALUES(new_id,model_step,1,6,0,6,0,0,60000,24000,1,600,'hard',1,1,0,0,1) ON CONFLICT DO NOTHING;
  UPDATE agent_definition SET default_harness_contract_id=new_id WHERE agent_key=agent;
 END LOOP;
END;
$$;
