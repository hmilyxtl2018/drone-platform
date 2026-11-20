-- Install the reviewed fixed execution projections for the two UAV Agents that
-- previously had Profile research stages but no executable agent_step rows.
-- The rows are deliberately small, read-only and idempotent.  They describe
-- Agent work, not a claim that CAD/PLM/MES or shop-floor execution is present.

INSERT INTO agent_step(
    step_id, agent_key, sequence_no, step_key, name, description,
    input_contract, output_contract, parallel_group, required, retry_limit,
    dependencies_json
)
VALUES
('uav-design-d010','uav-design-engineer',1,'design-input-boundary',
 '澄清设计目标与适用约束','冻结机型、构型、任务场景、硬约束和待补数据。',
 '{"type":"object","required":["entityId"]}',
 '{"type":"object","required":["designBrief","dataRequests"]}',NULL,1,1,'[]'),
('uav-design-d020','uav-design-engineer',2,'design-baseline-evidence',
 '核对本体基线与上游证据','读取本体基线、上游产物和来源哈希，区分事实、假设与缺口。',
 '{"type":"object"}',
 '{"type":"object","required":["evidenceBundle","assumptions"]}',NULL,1,2,'["design-input-boundary"]'),
('uav-design-d030','uav-design-engineer',3,'design-constraint-matrix',
 '形成概念方案与约束矩阵','按结构、动力、航电和任务接口整理方案候选与风险。',
 '{"type":"object"}',
 '{"type":"object","required":["designOptions","constraintMatrix","risks"]}',NULL,1,2,'["design-baseline-evidence"]'),
('uav-design-d040','uav-design-engineer',4,'design-budget-verification',
 '组织质量能源预算与验证计划','在可追溯输入下计算预算、比较需求并形成验证矩阵候选。',
 '{"type":"object"}',
 '{"type":"object","required":["budgets","verificationPlan","completionStatus"]}',NULL,1,2,'["design-constraint-matrix"]')
ON CONFLICT (agent_key,step_key) DO NOTHING;

INSERT INTO agent_step(
    step_id, agent_key, sequence_no, step_key, name, description,
    input_contract, output_contract, parallel_group, required, retry_limit,
    dependencies_json
)
VALUES
('uav-review-r010','uav-evidence-reviewer',1,'review-scope-freeze',
 '登记审查范围与冻结对象','冻结实体、构型、报告、来源快照和审查责任。',
 '{"type":"object","required":["entityId"]}',
 '{"type":"object","required":["reviewScope","frozenReferences"]}',NULL,1,1,'[]'),
('uav-review-r020','uav-evidence-reviewer',2,'review-citation-trace',
 '核查引文与来源定位','核对原文、引文、定位和来源时间，登记缺失与截断。',
 '{"type":"object"}',
 '{"type":"object","required":["citationChecks","evidenceGaps"]}',NULL,1,2,'["review-scope-freeze"]'),
('uav-review-r030','uav-evidence-reviewer',3,'review-value-semantics',
 '核查实体归属与数值口径','核对型号、构型、单位、工况和最大/典型口径，冲突保留并要求补证。',
 '{"type":"object"}',
 '{"type":"object","required":["findings","conflicts","dataRequests"]}',NULL,1,2,'["review-citation-trace"]'),
('uav-review-r040','uav-evidence-reviewer',4,'review-decision-package',
 '形成审查包与人工决定材料','汇总独立性、计算复核、追溯矩阵和人工审核所需阻断项。',
 '{"type":"object"}',
 '{"type":"object","required":["reviewPackage","traceability","decision","completionStatus"]}',NULL,1,1,'["review-value-semantics"]')
ON CONFLICT (agent_key,step_key) DO NOTHING;

INSERT INTO agent_step_tool(step_id,tool_key)
SELECT s.step_id, v.tool_key
FROM agent_step s
JOIN (VALUES
    ('uav-design-engineer','design-baseline-evidence','uav.get_profile'),
    ('uav-design-engineer','design-baseline-evidence','uav.get_capability_baseline'),
    ('uav-design-engineer','design-baseline-evidence','uav.search_ontology'),
    ('uav-design-engineer','design-constraint-matrix','uav.compare_capabilities'),
    ('uav-design-engineer','design-budget-verification','uav.calculate_weight_budget'),
    ('uav-design-engineer','design-budget-verification','uav.calculate_energy_consumption'),
    ('uav-design-engineer','design-budget-verification','uav.check_requirement_compliance'),
    ('uav-design-engineer','design-budget-verification','uav.generate_test_matrix'),
    ('uav-evidence-reviewer','review-scope-freeze','uav.get_profile'),
    ('uav-evidence-reviewer','review-citation-trace','uav.search_ontology'),
    ('uav-evidence-reviewer','review-citation-trace','uav.get_capability_baseline'),
    ('uav-evidence-reviewer','review-value-semantics','uav.check_requirement_compliance'),
    ('uav-evidence-reviewer','review-value-semantics','uav.compare_capabilities'),
    ('uav-evidence-reviewer','review-decision-package','uav.generate_test_matrix')
) AS v(agent_key,step_key,tool_key)
  ON s.agent_key=v.agent_key AND s.step_key=v.step_key
ON CONFLICT (step_id,tool_key) DO NOTHING;

