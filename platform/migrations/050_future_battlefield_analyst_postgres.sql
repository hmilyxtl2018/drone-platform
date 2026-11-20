-- Future battlefield research workspace profile. Configuration only; no source
-- ontology writes and no existing profile/run mutation.
INSERT INTO agent_definition(agent_key,display_name,description,domain,status,current_version,system_prompt)
VALUES ('future-battlefield-analyst','未来战场形态研判 Agent',
        '围绕未来战争形态、关键特征、驱动因素、约束条件与演化趋势开展可审计多视角研判',
        'strategic-intelligence','testing','1.0.0',
        '你是未来战场形态研判 Agent。严格区分事实、判断、假设和未知；事实必须绑定来源 URI、原文引文、定位和时间；保留冲突与缺口；模型只能提出候选，不能发布事实。')
ON CONFLICT (agent_key) DO NOTHING;

INSERT INTO agent_convergence_policy VALUES
('future-battlefield-analyst',
 '["每个启用视角都有结论或明确缺口","关键判断均有证据链或明确标注为假设","冲突已解决或显式保留","红队审查完成"]',
 '["completed","completed-with-gaps","requires-human-review","blocked","failed"]',80,24,1800,2)
ON CONFLICT (agent_key) DO NOTHING;

INSERT INTO agent_evidence_policy VALUES
('future-battlefield-analyst',2,1,1,1,1,'retain-and-escalate',
 '{"independence":"unknown-until-reviewed","requiredLayers":["fact","judgment","assumption","unknown"]}')
ON CONFLICT (agent_key) DO NOTHING;

INSERT INTO agent_approval_policy VALUES
('future-battlefield-analyst','["before-report-publication"]','["reviewer","admin"]')
ON CONFLICT (agent_key) DO NOTHING;

INSERT INTO agent_model_policy VALUES
('future-battlefield-analyst','configured','deepseek-v4-pro',0.1,4096,1,1,'1.0.0',
 '{"perspectiveTabs":true,"modelCanProposeOnly":true}')
ON CONFLICT (agent_key) DO NOTHING;

INSERT INTO agent_resource_policy VALUES
('future-battlefield-analyst',3,2,'["low","medium"]')
ON CONFLICT (agent_key) DO NOTHING;

INSERT INTO agent_output_contract VALUES
('future-battlefield-analyst',
 '{"required":["taskDefinition","perspectives","findings","evidence","conflicts","redTeam","assumptions","unknowns","followUps","confidence","completionStatus"]}',1,1,1)
ON CONFLICT (agent_key) DO NOTHING;

INSERT INTO agent_observability_policy VALUES
('future-battlefield-analyst',1,1,1,365,1,1,'{"perspectiveTabs":true,"budgetTrace":true}')
ON CONFLICT (agent_key) DO NOTHING;

INSERT INTO agent_scope(agent_key,responsibility_json,input_entities_json,output_entities_json,allowed_write_json,forbidden_actions_json,created_at,updated_at)
VALUES ('future-battlefield-analyst',
 '{"responsibilities":["war-system analysis","technology and autonomy trends","evidence fusion","red-team challenge","multi-perspective judgment"]}',
 '["ResearchQuestion","Perspective","SourceDocument","EvidenceBundle"]',
 '["PerspectiveFinding","TrendJudgment","EvidenceGap","RedTeamPackage","JudgmentReport"]',
 '[]','["publish facts without human review","invent evidence","treat absence of evidence as evidence of absence","control weapons or operational systems"]',now(),now())
ON CONFLICT (agent_key) DO NOTHING;

INSERT INTO agent_step(step_id,agent_key,sequence_no,step_key,name,description,input_contract,output_contract,parallel_group,required,retry_limit,dependencies_json)
VALUES
('fb-question','future-battlefield-analyst',1,'define-research-question','定义研判问题','冻结问题、范围和启用视角','{"type":"object"}','{"required":["taskDefinition","perspectives"]}',NULL,1,1,'[]'),
('fb-hypotheses','future-battlefield-analyst',2,'generate-research-hypotheses','生成研究假设','把视角转为可验证假设','{"type":"object"}','{"required":["hypotheses"]}',NULL,1,1,'["define-research-question"]'),
('fb-source-plan','future-battlefield-analyst',3,'plan-source-coverage','规划来源覆盖','为每个视角规划来源组和检索条件','{"type":"object"}','{"required":["sourcePlan"]}',NULL,1,1,'["generate-research-hypotheses"]'),
('fb-collect','future-battlefield-analyst',4,'collect-public-evidence','采集公开证据','按冻结来源计划搜索和抓取公开材料','{"type":"object"}','{"required":["sourceBundle"]}',NULL,1,2,'["plan-source-coverage"]'),
('fb-facts','future-battlefield-analyst',5,'extract-facts','事实与事件抽取','抽取带引文、定位和时间的事实候选','{"type":"object"}','{"required":["facts","evidence"]}','analysis',1,2,'["collect-public-evidence"]'),
('fb-system','future-battlefield-analyst',6,'extract-war-system','作战体系要素抽取','抽取编组、协同、数据链和约束要素','{"type":"object"}','{"required":["systemFindings","evidence"]}','analysis',1,2,'["collect-public-evidence"]'),
('fb-fusion','future-battlefield-analyst',7,'fuse-evidence-and-conflicts','证据融合与冲突识别','去重、分组并保留冲突和缺口','{"type":"object"}','{"required":["evidence","conflicts","gaps"]}',NULL,1,2,'["extract-facts","extract-war-system"]'),
('fb-perspectives','future-battlefield-analyst',8,'analyze-by-perspective','多视角趋势研判','分别生成每个启用视角的条件化判断','{"type":"object"}','{"required":["perspectiveFindings"]}',NULL,1,2,'["fuse-evidence-and-conflicts"]'),
('fb-red-team','future-battlefield-analyst',9,'red-team-challenge','红队反证与不确定性审查','寻找反例、过度推断和发布风险','{"type":"object"}','{"required":["redTeam","unknowns"]}',NULL,1,2,'["analyze-by-perspective"]'),
('fb-report','future-battlefield-analyst',10,'compose-judgment-report','编排研判报告','形成事实、判断、假设、缺口四层报告','{"type":"object"}','{"required":["judgmentReport","confidence","completionStatus"]}',NULL,1,1,'["red-team-challenge"]'),
('fb-review','future-battlefield-analyst',11,'human-review','人工审核与发布门控','人工审核后允许发布候选报告','{"type":"object"}','{"required":["reviewStatus"]}',NULL,1,0,'["compose-judgment-report"]')
ON CONFLICT (agent_key,step_key) DO NOTHING;

INSERT INTO agent_loop_rule VALUES
('fb-evidence-loop','future-battlefield-analyst','视角存在关键证据缺口','collect-public-evidence',3,2,'requires-human-review')
ON CONFLICT (loop_id) DO NOTHING;
