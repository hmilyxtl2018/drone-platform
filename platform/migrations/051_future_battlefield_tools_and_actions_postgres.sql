-- Deterministic future-battlefield tools and the first Action-Agent layer.
-- This migration is additive and can be replayed safely.

INSERT INTO tool_registry(tool_key,tool_name,category,description,protocol,input_schema,output_schema,
                          tags_json,access_mode,risk_level,status,current_version)
VALUES
('analysis.source_deduplicate','来源去重与溯源分组','analysis','按正文哈希合并重复来源并保留全部来源身份','python',
 '{"type":"object","properties":{"input":{"type":"object"},"state":{"type":"object"},"arguments":{"type":"object"}},"additionalProperties":false}',
 '{"type":"object","required":["groups","uniqueCount","duplicateCount"],"properties":{"groups":{"type":"array"},"uniqueCount":{"type":"integer","minimum":0},"duplicateCount":{"type":"integer","minimum":0}},"additionalProperties":true}',
 '["future_battlefield","provenance","deduplication"]','read_only','low','published','1.0.0'),
('analysis.citation_validate','引文精确命中校验','analysis','检查候选引文是否为来源正文中的连续原文','python',
 '{"type":"object","properties":{"input":{"type":"object"},"state":{"type":"object"},"arguments":{"type":"object"}},"additionalProperties":false}',
 '{"type":"object","required":["validations","acceptedCount","rejectedCount"],"properties":{"validations":{"type":"array"},"acceptedCount":{"type":"integer","minimum":0},"rejectedCount":{"type":"integer","minimum":0}},"additionalProperties":true}',
 '["future_battlefield","evidence","citation"]','read_only','low','published','1.0.0'),
('analysis.event_normalize','事件日期规范化','analysis','规范化事件日期并显式保留未知日期','python',
 '{"type":"object","properties":{"input":{"type":"object"},"state":{"type":"object"},"arguments":{"type":"object"}},"additionalProperties":false}',
 '{"type":"object","required":["events","knownDateCount","unknownDateCount"],"properties":{"events":{"type":"array"},"knownDateCount":{"type":"integer","minimum":0},"unknownDateCount":{"type":"integer","minimum":0}},"additionalProperties":true}',
 '["future_battlefield","events","normalization"]','read_only','low','published','1.0.0'),
('analysis.change_point','趋势变化点检测','analysis','对已整理的时间序列执行可解释的相邻跳变检测','python',
 '{"type":"object","properties":{"input":{"type":"object"},"state":{"type":"object"},"arguments":{"type":"object"}},"additionalProperties":false}',
 '{"type":"object","required":["changes","sampleCount","threshold"],"properties":{"changes":{"type":"array"},"sampleCount":{"type":"integer","minimum":0},"threshold":{"type":"number","exclusiveMinimum":0}},"additionalProperties":true}',
 '["future_battlefield","trend","change_point"]','read_only','low','published','1.0.0')
ON CONFLICT (tool_key) DO NOTHING;

INSERT INTO tool_version(tool_key,version,input_schema,output_schema,changelog,status)
SELECT tool_key,current_version,input_schema,output_schema,'initial deterministic future-battlefield analysis adapter','published'
FROM tool_registry
WHERE tool_key IN ('analysis.source_deduplicate','analysis.citation_validate','analysis.event_normalize','analysis.change_point')
ON CONFLICT (tool_key,version) DO NOTHING;

INSERT INTO agent_tool_binding(agent_key,tool_key,tool_version,enabled,approval_required,config_json)
SELECT 'future-battlefield-analyst',tool_key,'1.0.0',1,0,'{"timeoutSeconds":30,"retryLimit":1}'::jsonb
FROM tool_registry
WHERE tool_key IN ('analysis.source_deduplicate','analysis.citation_validate','analysis.event_normalize','analysis.change_point')
ON CONFLICT (agent_key,tool_key) DO NOTHING;

INSERT INTO agent_step_tool(step_id,tool_key)
SELECT s.step_id, v.tool_key
FROM agent_step s
CROSS JOIN LATERAL (VALUES
    ('extract-facts','analysis.citation_validate'),
    ('extract-facts','analysis.event_normalize'),
    ('fuse-evidence-and-conflicts','analysis.source_deduplicate'),
    ('analyze-by-perspective','analysis.change_point')
) AS v(step_key,tool_key)
WHERE s.agent_key='future-battlefield-analyst' AND s.step_key=v.step_key
ON CONFLICT (step_id,tool_key) DO NOTHING;

-- Make the capability graph and resource controls part of the editable,
-- publishable Profile document. Existing operator edits are retained.
UPDATE agent_model_policy
SET model_provider='deepseek',
    config_json=(config_json::jsonb || '{
      "researchModeAvailable": true,
      "ontologySourceKey": "uav-ontology",
      "webProviderKey": "firecrawl",
      "budgetLimits": {"model_calls":24,"input_tokens":240000,"output_tokens":96000,"tool_calls":80,"http_calls":40,"search_calls":20,"scrape_calls":20},
      "capabilityGraphPath": "capability_graphs/future-battlefield-analyst.v1.json",
      "capabilityActions": ["evidence-extraction-action","trend-analysis-action","scenario-analysis-action","red-team-action","analysis.citation_validate"],
      "sourcePolicy": {"allowedDomains":["defense.gov","nato.int","rand.org","csis.org","gov.cn"],"includeSubdomains":true,"maxPages":20}
    }'::jsonb)::text
WHERE agent_key='future-battlefield-analyst' AND NOT(config_json::jsonb ? 'capabilityGraphPath');

UPDATE agent_output_contract
SET schema_json='{
  "type":"object",
  "required":["taskDefinition","perspectives","findings","evidence","conflicts","redTeam","assumptions","unknowns","followUps","confidence","completionStatus"],
  "properties":{
    "taskDefinition":{"type":"object"},"perspectives":{"type":"array"},"findings":{"type":"array","items":{"type":"object","required":["supportingClaims","conditions","counterArguments","unknowns"],"properties":{"supportingClaims":{"type":"array"},"conditions":{"type":"array"},"counterArguments":{"type":"array"},"unknowns":{"type":"array"}}}},
    "evidence":{"type":"array","items":{"type":"object","properties":{"sourceUri":{"type":"string"},"quote":{"type":"string"},"locator":{"type":"object"},"retrievedAt":{"type":"string"}},"required":["sourceUri","quote","locator","retrievedAt"]}},
    "supportingClaims":{"type":"array"},"conditions":{"type":"array"},"counterArguments":{"type":"array"},"unknowns":{"type":"array"},
    "counterExamples":{"type":"array"},"unsupportedClaims":{"type":"array"},"conflicts":{"type":"array"},"redTeam":{"type":"object","required":["counterExamples","unsupportedClaims","conflicts"],"properties":{"counterExamples":{"type":"array"},"unsupportedClaims":{"type":"array"},"conflicts":{"type":"array"}}},"assumptions":{"type":"array"},
    "followUps":{"type":"array"},"confidence":{"type":"string"},"completionStatus":{"type":"string"}
  }
}'
WHERE agent_key='future-battlefield-analyst' AND NOT(schema_json::jsonb ? 'properties');

-- Action Agent profiles. Their small DAGs are independently executable and
-- can be composed by the application-level Profile without bypassing budget
-- or evidence gates.
INSERT INTO agent_definition(agent_key,display_name,description,domain,status,current_version,system_prompt)
VALUES
('evidence-collection-action','证据采集 Action Agent','按冻结来源计划整理公开材料并形成来源包','strategic-intelligence-action','testing','1.0.0','只处理公开来源材料，保留来源身份与正文哈希。'),
('evidence-extraction-action','证据抽取 Action Agent','把来源材料转换为带精确引文的事实与事件候选','strategic-intelligence-action','testing','1.0.0','只输出可被原文精确命中的候选，不把候选发布为事实。'),
('trend-analysis-action','趋势分析 Action Agent','对已整理的事件和指标序列识别趋势与变化点','strategic-intelligence-action','testing','1.0.0','只对输入序列执行可解释计算，条件不足时输出未知。'),
('scenario-analysis-action','场景分析 Action Agent','按已启用研判视角组织条件、驱动因素和未知项','strategic-intelligence-action','testing','1.0.0','生成结构化候选判断，必须显式列出条件与未知。'),
('red-team-action','红队审查 Action Agent','检查引文、冲突、反例与过度推断','strategic-intelligence-action','testing','1.0.0','寻找反证和发布风险，不静默删除冲突。')
ON CONFLICT (agent_key) DO NOTHING;

INSERT INTO agent_model_policy(agent_key,model_provider,model_name,temperature,max_output_tokens,structured_output_required,planner_enabled,prompt_version,config_json)
SELECT agent_key,'deepseek','deepseek-v4-pro',0.1,4096,1,1,'1.0.0',
  '{"researchModeAvailable":true,"ontologySourceKey":"uav-ontology","webProviderKey":"firecrawl","budgetLimits":{"model_calls":6,"input_tokens":60000,"output_tokens":24000,"tool_calls":12,"http_calls":6,"search_calls":3,"scrape_calls":3},"capabilityActions":[]}'
FROM agent_definition
WHERE agent_key IN ('evidence-collection-action','evidence-extraction-action','trend-analysis-action','scenario-analysis-action','red-team-action')
ON CONFLICT (agent_key) DO NOTHING;

INSERT INTO agent_resource_policy(agent_key,max_parallel_tools,max_retries_per_tool,permitted_risk_levels_json)
SELECT agent_key,2,1,'["low","medium"]'
FROM agent_definition
WHERE agent_key IN ('evidence-collection-action','evidence-extraction-action','trend-analysis-action','scenario-analysis-action','red-team-action')
ON CONFLICT (agent_key) DO NOTHING;

INSERT INTO agent_convergence_policy(agent_key,completion_conditions_json,allowed_terminal_states_json,max_tool_calls,max_model_calls,max_runtime_seconds,no_new_finding_rounds)
SELECT agent_key,'[]','["completed","completed-with-gaps","requires-human-review","blocked","failed"]',12,6,600,2
FROM agent_definition
WHERE agent_key IN ('evidence-collection-action','evidence-extraction-action','trend-analysis-action','scenario-analysis-action','red-team-action')
ON CONFLICT (agent_key) DO NOTHING;

INSERT INTO agent_evidence_policy(agent_key,minimum_evidence_count,require_source_uri,require_data_timestamp,allow_inference,inference_disclosure_required,conflict_action,policy_json)
SELECT agent_key,0,1,0,1,1,'retain-and-escalate','{"layer":"action-candidate"}'
FROM agent_definition
WHERE agent_key IN ('evidence-collection-action','evidence-extraction-action','trend-analysis-action','scenario-analysis-action','red-team-action')
ON CONFLICT (agent_key) DO NOTHING;

INSERT INTO agent_approval_policy(agent_key,approval_points_json,approver_roles_json)
SELECT agent_key,'[]','["reviewer","admin"]'
FROM agent_definition
WHERE agent_key IN ('evidence-collection-action','evidence-extraction-action','trend-analysis-action','scenario-analysis-action','red-team-action')
ON CONFLICT (agent_key) DO NOTHING;

INSERT INTO agent_output_contract(agent_key,schema_json,required_evidence,required_assumptions,required_confidence)
SELECT agent_key,'{"type":"object","required":["agentKey","toolResults","evidence","assumptions","confidence","completionStatus"],"properties":{"agentKey":{"type":"string"},"toolResults":{"type":"object"},"evidence":{"type":"array"},"assumptions":{"type":"array"},"confidence":{"type":"string"},"completionStatus":{"type":"string"}}}',1,1,1
FROM agent_definition
WHERE agent_key IN ('evidence-collection-action','evidence-extraction-action','trend-analysis-action','scenario-analysis-action','red-team-action')
ON CONFLICT (agent_key) DO NOTHING;

INSERT INTO agent_observability_policy(agent_key,trace_enabled,capture_tool_inputs,capture_tool_outputs,retain_days,emit_step_events,emit_metrics,policy_json)
SELECT agent_key,1,1,1,365,1,1,'{"budgetTrace":true,"actionAgent":true}'
FROM agent_definition
WHERE agent_key IN ('evidence-collection-action','evidence-extraction-action','trend-analysis-action','scenario-analysis-action','red-team-action')
ON CONFLICT (agent_key) DO NOTHING;

INSERT INTO agent_scope(agent_key,responsibility_json,input_entities_json,output_entities_json,allowed_write_json,forbidden_actions_json,created_at,updated_at)
SELECT agent_key,'{"layer":"action","composable":true}','["EvidenceBundle","Perspective","ResearchQuestion"]','["ActionResult","EvidenceGap","CandidateFinding"]','[]','["publish facts without human review","control weapons or operational systems"]',now(),now()
FROM agent_definition
WHERE agent_key IN ('evidence-collection-action','evidence-extraction-action','trend-analysis-action','scenario-analysis-action','red-team-action')
ON CONFLICT (agent_key) DO NOTHING;

INSERT INTO agent_step(step_id,agent_key,sequence_no,step_key,name,description,input_contract,output_contract,parallel_group,required,retry_limit,dependencies_json)
VALUES
('fb-act-collect-1','evidence-collection-action',1,'deduplicate-source-bundle','整理来源包','合并重复来源并保留来源身份','{"type":"object"}','{"type":"object","required":["stepKey","toolResults"]}',NULL,1,1,'[]'),
('fb-act-extract-1','evidence-extraction-action',1,'validate-citations','校验精确引文','逐条检查候选引文是否命中正文','{"type":"object"}','{"type":"object","required":["stepKey","toolResults"]}',NULL,1,1,'[]'),
('fb-act-extract-2','evidence-extraction-action',2,'normalize-event-dates','规范事件日期','保留已知日期并显式记录未知日期','{"type":"object"}','{"type":"object","required":["stepKey","toolResults"]}',NULL,1,1,'["validate-citations"]'),
('fb-act-trend-1','trend-analysis-action',1,'detect-trend-change-points','检测趋势变化点','对输入序列执行相邻跳变检测','{"type":"object"}','{"type":"object","required":["stepKey","toolResults"]}',NULL,1,1,'[]'),
('fb-act-scenario-1','scenario-analysis-action',1,'structure-scenario-judgment','组织场景判断','整理条件、驱动因素、反方和未知项','{"type":"object"}','{"type":"object","required":["stepKey","toolResults"]}',NULL,1,0,'[]'),
('fb-act-red-1','red-team-action',1,'challenge-citations','审查引文与反证','标记不支持、冲突和反例','{"type":"object"}','{"type":"object","required":["stepKey","toolResults"]}',NULL,1,1,'[]')
ON CONFLICT (agent_key,step_key) DO NOTHING;

INSERT INTO agent_tool_binding(agent_key,tool_key,tool_version,enabled,approval_required,config_json)
SELECT a.agent_key,t.tool_key,'1.0.0',1,0,'{"timeoutSeconds":30,"retryLimit":1}'::jsonb
FROM agent_definition a CROSS JOIN tool_registry t
WHERE a.agent_key IN ('evidence-collection-action','evidence-extraction-action','trend-analysis-action','red-team-action')
  AND t.tool_key IN ('analysis.source_deduplicate','analysis.citation_validate','analysis.event_normalize')
  AND ((a.agent_key='evidence-collection-action' AND t.tool_key='analysis.source_deduplicate')
    OR (a.agent_key='evidence-extraction-action' AND t.tool_key IN ('analysis.citation_validate','analysis.event_normalize'))
    OR (a.agent_key='red-team-action' AND t.tool_key='analysis.citation_validate'))
ON CONFLICT (agent_key,tool_key) DO NOTHING;

INSERT INTO agent_tool_binding(agent_key,tool_key,tool_version,enabled,approval_required,config_json)
SELECT 'trend-analysis-action','analysis.change_point','1.0.0',1,0,'{"timeoutSeconds":30,"retryLimit":1}'::jsonb
WHERE EXISTS (SELECT 1 FROM tool_registry WHERE tool_key='analysis.change_point')
ON CONFLICT (agent_key,tool_key) DO NOTHING;

INSERT INTO agent_step_tool(step_id,tool_key)
VALUES
('fb-act-collect-1','analysis.source_deduplicate'),
('fb-act-extract-1','analysis.citation_validate'),
('fb-act-extract-2','analysis.event_normalize'),
('fb-act-trend-1','analysis.change_point'),
('fb-act-red-1','analysis.citation_validate')
ON CONFLICT (step_id,tool_key) DO NOTHING;

-- Structured scenario and red-team contracts are deterministic gates around
-- model proposals; they retain gaps instead of silently accepting them.
INSERT INTO tool_registry(tool_key,tool_name,category,description,protocol,input_schema,output_schema,
                          tags_json,access_mode,risk_level,status,current_version)
VALUES
('analysis.scenario_structure','场景判断结构化校验','analysis','校验条件、支持主张、反方论据和未知项是否显式给出','python',
 '{"type":"object","properties":{"input":{"type":"object"},"state":{"type":"object"},"arguments":{"type":"object"}},"additionalProperties":false}',
 '{"type":"object","required":["judgments","evidence","gaps","toolKey","deterministic","truthVerified","provenance","artifactId","artifactHash"],"additionalProperties":true}',
 '["future_battlefield","scenario","governance"]','read_only','low','published','1.0.0'),
('analysis.claim_audit','结论与反证审查','analysis','检查判断的证据支持、反方论据、冲突和未知项','python',
 '{"type":"object","properties":{"input":{"type":"object"},"state":{"type":"object"},"arguments":{"type":"object"}},"additionalProperties":false}',
 '{"type":"object","required":["counterExamples","unsupportedClaims","conflicts","uncheckedJudgments","evidence","gaps","toolKey","deterministic","truthVerified","provenance","artifactId","artifactHash"],"additionalProperties":true}',
 '["future_battlefield","red_team","governance"]','read_only','low','published','1.0.0')
ON CONFLICT (tool_key) DO NOTHING;

INSERT INTO tool_version(tool_key,version,input_schema,output_schema,changelog,status)
SELECT tool_key,current_version,input_schema,output_schema,'structured scenario and red-team validation adapter','published'
FROM tool_registry
WHERE tool_key IN ('analysis.scenario_structure','analysis.claim_audit')
ON CONFLICT (tool_key,version) DO NOTHING;

INSERT INTO agent_tool_binding(agent_key,tool_key,tool_version,enabled,approval_required,config_json)
SELECT 'scenario-analysis-action','analysis.scenario_structure','1.0.0',1,0,'{"timeoutSeconds":30,"retryLimit":1}'::jsonb
WHERE EXISTS (SELECT 1 FROM tool_registry WHERE tool_key='analysis.scenario_structure')
ON CONFLICT (agent_key,tool_key) DO NOTHING;

INSERT INTO agent_tool_binding(agent_key,tool_key,tool_version,enabled,approval_required,config_json)
SELECT 'red-team-action','analysis.claim_audit','1.0.0',1,0,'{"timeoutSeconds":30,"retryLimit":1}'::jsonb
WHERE EXISTS (SELECT 1 FROM tool_registry WHERE tool_key='analysis.claim_audit')
ON CONFLICT (agent_key,tool_key) DO NOTHING;

INSERT INTO agent_step_tool(step_id,tool_key)
VALUES ('fb-act-scenario-1','analysis.scenario_structure'),('fb-act-red-1','analysis.claim_audit')
ON CONFLICT (step_id,tool_key) DO NOTHING;
