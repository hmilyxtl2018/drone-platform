-- Executable Agent specifications: scope, steps, loops, convergence and gates.
CREATE TABLE IF NOT EXISTS agent_scope (
  agent_key TEXT PRIMARY KEY REFERENCES agent_definition(agent_key),
  responsibility_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(responsibility_json)),
  input_entities_json TEXT NOT NULL DEFAULT '[]' CHECK(json_valid(input_entities_json)),
  output_entities_json TEXT NOT NULL DEFAULT '[]' CHECK(json_valid(output_entities_json)),
  allowed_write_json TEXT NOT NULL DEFAULT '[]' CHECK(json_valid(allowed_write_json)),
  forbidden_actions_json TEXT NOT NULL DEFAULT '[]' CHECK(json_valid(forbidden_actions_json)),
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS agent_step (
  step_id TEXT PRIMARY KEY,
  agent_key TEXT NOT NULL REFERENCES agent_definition(agent_key),
  sequence_no INTEGER NOT NULL,
  step_key TEXT NOT NULL,
  name TEXT NOT NULL,
  description TEXT NOT NULL,
  input_contract TEXT NOT NULL DEFAULT '{}',
  output_contract TEXT NOT NULL DEFAULT '{}',
  parallel_group TEXT,
  required INTEGER NOT NULL DEFAULT 1 CHECK(required IN (0,1)),
  retry_limit INTEGER NOT NULL DEFAULT 2 CHECK(retry_limit >= 0),
  UNIQUE(agent_key,step_key)
);
CREATE TABLE IF NOT EXISTS agent_step_tool (
  step_id TEXT NOT NULL REFERENCES agent_step(step_id),
  tool_key TEXT NOT NULL REFERENCES tool_registry(tool_key),
  PRIMARY KEY(step_id,tool_key)
);
CREATE TABLE IF NOT EXISTS agent_loop_rule (
  loop_id TEXT PRIMARY KEY,
  agent_key TEXT NOT NULL REFERENCES agent_definition(agent_key),
  trigger_condition TEXT NOT NULL,
  return_step_key TEXT NOT NULL,
  max_iterations INTEGER NOT NULL DEFAULT 3 CHECK(max_iterations > 0),
  no_progress_limit INTEGER NOT NULL DEFAULT 2 CHECK(no_progress_limit > 0),
  stop_action TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS agent_convergence_policy (
  agent_key TEXT PRIMARY KEY REFERENCES agent_definition(agent_key),
  completion_conditions_json TEXT NOT NULL DEFAULT '[]' CHECK(json_valid(completion_conditions_json)),
  allowed_terminal_states_json TEXT NOT NULL DEFAULT '["completed","completed-with-gaps","requires-human-review","blocked","failed"]' CHECK(json_valid(allowed_terminal_states_json)),
  max_tool_calls INTEGER NOT NULL DEFAULT 50,
  max_model_calls INTEGER NOT NULL DEFAULT 20,
  max_runtime_seconds INTEGER NOT NULL DEFAULT 900,
  no_new_finding_rounds INTEGER NOT NULL DEFAULT 2
);
CREATE TABLE IF NOT EXISTS agent_output_contract (
  agent_key TEXT PRIMARY KEY REFERENCES agent_definition(agent_key),
  schema_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(schema_json)),
  required_evidence INTEGER NOT NULL DEFAULT 1 CHECK(required_evidence IN (0,1)),
  required_assumptions INTEGER NOT NULL DEFAULT 1 CHECK(required_assumptions IN (0,1)),
  required_confidence INTEGER NOT NULL DEFAULT 1 CHECK(required_confidence IN (0,1))
);
CREATE TABLE IF NOT EXISTS agent_validation_rule (
  rule_id TEXT PRIMARY KEY,
  agent_key TEXT NOT NULL REFERENCES agent_definition(agent_key),
  rule_type TEXT NOT NULL,
  expression TEXT NOT NULL,
  severity TEXT NOT NULL DEFAULT 'error' CHECK(severity IN ('warning','error','blocking'))
);
CREATE TABLE IF NOT EXISTS agent_resource_policy (
  agent_key TEXT PRIMARY KEY REFERENCES agent_definition(agent_key),
  max_parallel_tools INTEGER NOT NULL DEFAULT 4,
  max_retries_per_tool INTEGER NOT NULL DEFAULT 2,
  permitted_risk_levels_json TEXT NOT NULL DEFAULT '["low","medium"]' CHECK(json_valid(permitted_risk_levels_json))
);
CREATE TABLE IF NOT EXISTS agent_approval_policy (
  agent_key TEXT PRIMARY KEY REFERENCES agent_definition(agent_key),
  approval_points_json TEXT NOT NULL DEFAULT '[]' CHECK(json_valid(approval_points_json)),
  approver_roles_json TEXT NOT NULL DEFAULT '["engineer"]' CHECK(json_valid(approver_roles_json))
);
CREATE TABLE IF NOT EXISTS workflow_transition (
  transition_id TEXT PRIMARY KEY,
  workflow_key TEXT NOT NULL REFERENCES workflow_definition(workflow_key),
  from_state TEXT NOT NULL,
  to_state TEXT NOT NULL,
  condition TEXT NOT NULL,
  priority INTEGER NOT NULL DEFAULT 100
);
CREATE INDEX IF NOT EXISTS agent_step_order_idx ON agent_step(agent_key,sequence_no);
CREATE INDEX IF NOT EXISTS validation_agent_idx ON agent_validation_rule(agent_key,severity);

INSERT OR IGNORE INTO agent_scope(agent_key,responsibility_json,input_entities_json,output_entities_json,forbidden_actions_json) VALUES
 ('uav-capability-analyst','{"responsibilities":["capability baseline","flight performance","requirement compliance","capability gap"]}','["UAV","Mission","FlightLog","Requirement"]','["CapabilityAssessment","CapabilityGap","EvidenceBundle"]','["modify design baseline","publish manufacturing BOM","control aircraft"]'),
 ('uav-design-engineer','{"responsibilities":["weight budget","energy budget","configuration comparison","manufacturability","verification planning"]}','["Requirement","UAV","Component","CapabilityGap"]','["DesignOption","WeightBudget","EnergyBudget","TestMatrix","BOMCandidate"]','["publish released design","modify PLM master data","skip verification"]'),
 ('uav-evidence-reviewer','{"responsibilities":["evidence completeness","source conflict","calculation reproducibility","traceability"]}','["Assessment","Evidence","Requirement","TestResult"]','["ReviewPackage","EvidenceGap","TraceabilityMatrix"]','["delete conflicting evidence","approve own review","change engineering result"]');
INSERT OR IGNORE INTO agent_convergence_policy(agent_key,completion_conditions_json,max_tool_calls,max_model_calls,max_runtime_seconds) VALUES
 ('uav-capability-analyst','["all hard requirements assessed","key metrics calculated","each conclusion has evidence or explicit gap","no unresolved blocking conflict"]',50,20,900),
 ('uav-design-engineer','["weight and energy budgets close","hard constraints satisfied or infeasibility proven","interfaces checked","verification matrix covers requirements"]',80,30,1800),
 ('uav-evidence-reviewer','["key conclusions have traceable evidence","conflicts resolved or retained explicitly","unsupported claims flagged"]',40,15,600);
INSERT OR IGNORE INTO agent_output_contract(agent_key,schema_json) VALUES
 ('uav-capability-analyst','{"required":["assessment","evidence","assumptions","unresolvedQuestions","confidence","completionStatus"]}'),
 ('uav-design-engineer','{"required":["designOptions","budgets","risks","verificationPlan","confidence","completionStatus"]}'),
 ('uav-evidence-reviewer','{"required":["findings","evidenceGaps","traceability","decision","completionStatus"]}');
