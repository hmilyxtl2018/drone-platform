-- Remaining Agent Profile policies.
CREATE TABLE IF NOT EXISTS agent_model_policy (
  agent_key TEXT PRIMARY KEY REFERENCES agent_definition(agent_key),
  model_provider TEXT NOT NULL DEFAULT 'configured',
  model_name TEXT NOT NULL DEFAULT 'default',
  temperature REAL NOT NULL DEFAULT 0.1 CHECK(temperature >= 0 AND temperature <= 2),
  max_output_tokens INTEGER NOT NULL DEFAULT 4096 CHECK(max_output_tokens > 0),
  structured_output_required INTEGER NOT NULL DEFAULT 1 CHECK(structured_output_required IN (0,1)),
  planner_enabled INTEGER NOT NULL DEFAULT 1 CHECK(planner_enabled IN (0,1)),
  prompt_version TEXT NOT NULL DEFAULT '1.0.0',
  config_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(config_json))
);
CREATE TABLE IF NOT EXISTS agent_evidence_policy (
  agent_key TEXT PRIMARY KEY REFERENCES agent_definition(agent_key),
  minimum_evidence_count INTEGER NOT NULL DEFAULT 1 CHECK(minimum_evidence_count >= 0),
  require_source_uri INTEGER NOT NULL DEFAULT 1 CHECK(require_source_uri IN (0,1)),
  require_data_timestamp INTEGER NOT NULL DEFAULT 1 CHECK(require_data_timestamp IN (0,1)),
  allow_inference INTEGER NOT NULL DEFAULT 1 CHECK(allow_inference IN (0,1)),
  inference_disclosure_required INTEGER NOT NULL DEFAULT 1 CHECK(inference_disclosure_required IN (0,1)),
  conflict_action TEXT NOT NULL DEFAULT 'retain-and-escalate' CHECK(conflict_action IN ('retain-and-escalate','reject','prefer-authoritative')),
  policy_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(policy_json))
);
CREATE TABLE IF NOT EXISTS agent_observability_policy (
  agent_key TEXT PRIMARY KEY REFERENCES agent_definition(agent_key),
  trace_enabled INTEGER NOT NULL DEFAULT 1 CHECK(trace_enabled IN (0,1)),
  capture_tool_inputs INTEGER NOT NULL DEFAULT 1 CHECK(capture_tool_inputs IN (0,1)),
  capture_tool_outputs INTEGER NOT NULL DEFAULT 1 CHECK(capture_tool_outputs IN (0,1)),
  retain_days INTEGER NOT NULL DEFAULT 365 CHECK(retain_days > 0),
  emit_step_events INTEGER NOT NULL DEFAULT 1 CHECK(emit_step_events IN (0,1)),
  emit_metrics INTEGER NOT NULL DEFAULT 1 CHECK(emit_metrics IN (0,1)),
  policy_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(policy_json))
);
INSERT OR IGNORE INTO agent_model_policy(agent_key) SELECT agent_key FROM agent_definition;
INSERT OR IGNORE INTO agent_evidence_policy(agent_key) SELECT agent_key FROM agent_definition;
INSERT OR IGNORE INTO agent_observability_policy(agent_key) SELECT agent_key FROM agent_definition;
