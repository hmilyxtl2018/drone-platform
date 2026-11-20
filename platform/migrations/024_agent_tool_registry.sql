-- Agent tool registry for UAV capability analysis and design/manufacturing workflows.
-- Tools are configuration and governance records; invocation results remain in
-- the existing analysis/event stores.

CREATE TABLE IF NOT EXISTS agent_tool_registry (
  tool_key TEXT PRIMARY KEY,
  tool_name TEXT NOT NULL,
  category TEXT NOT NULL CHECK(category IN ('ontology','capability','flight-data','health','design','manufacturing','knowledge')),
  description TEXT NOT NULL,
  tool_type TEXT NOT NULL DEFAULT 'api',
  protocol TEXT NOT NULL DEFAULT 'http',
  input_schema TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(input_schema) AND json_type(input_schema)='object'),
  output_schema TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(output_schema) AND json_type(output_schema)='object'),
  capability_tags TEXT NOT NULL DEFAULT '[]' CHECK(json_valid(capability_tags) AND json_type(capability_tags)='array'),
  risk_level TEXT NOT NULL DEFAULT 'medium' CHECK(risk_level IN ('low','medium','high','critical')),
  access_mode TEXT NOT NULL DEFAULT 'read_only' CHECK(access_mode IN ('read_only','write','approval_required')),
  deterministic INTEGER NOT NULL DEFAULT 1 CHECK(deterministic IN (0,1)),
  supports_parallel INTEGER NOT NULL DEFAULT 1 CHECK(supports_parallel IN (0,1)),
  timeout_seconds INTEGER NOT NULL DEFAULT 60 CHECK(timeout_seconds > 0),
  max_retries INTEGER NOT NULL DEFAULT 2 CHECK(max_retries >= 0),
  endpoint TEXT,
  status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','testing','approved','published','deprecated','retired')),
  version TEXT NOT NULL DEFAULT '1.0.0',
  owner TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS agent_tool_registry_category_idx ON agent_tool_registry(category,status);
CREATE INDEX IF NOT EXISTS agent_tool_registry_tags_idx ON agent_tool_registry(capability_tags);

CREATE TABLE IF NOT EXISTS agent_tool_binding (
  agent_role TEXT NOT NULL,
  tool_key TEXT NOT NULL REFERENCES agent_tool_registry(tool_key),
  permission_mode TEXT NOT NULL DEFAULT 'allow' CHECK(permission_mode IN ('allow','deny')),
  approval_required INTEGER NOT NULL DEFAULT 0 CHECK(approval_required IN (0,1)),
  enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)),
  config_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(config_json) AND json_type(config_json)='object'),
  created_at TEXT NOT NULL,
  PRIMARY KEY(agent_role,tool_key)
);

CREATE TABLE IF NOT EXISTS agent_tool_invocation (
  invocation_id TEXT PRIMARY KEY,
  analysis_run_id TEXT REFERENCES ontology_analysis_run(analysis_run_id),
  agent_task_id TEXT REFERENCES ontology_agent_task(agent_task_id),
  tool_key TEXT NOT NULL REFERENCES agent_tool_registry(tool_key),
  tool_version TEXT NOT NULL,
  input_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(input_json)),
  output_json TEXT CHECK(output_json IS NULL OR json_valid(output_json)),
  status TEXT NOT NULL CHECK(status IN ('queued','running','succeeded','failed','timeout','cancelled')),
  error_message TEXT,
  latency_ms INTEGER,
  retry_count INTEGER NOT NULL DEFAULT 0,
  trace_id TEXT,
  started_at TEXT,
  finished_at TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS agent_tool_invocation_run_idx ON agent_tool_invocation(analysis_run_id,created_at);
CREATE INDEX IF NOT EXISTS agent_tool_invocation_tool_idx ON agent_tool_invocation(tool_key,status,created_at);

CREATE TRIGGER IF NOT EXISTS agent_tool_invocation_no_update BEFORE UPDATE ON agent_tool_invocation BEGIN SELECT RAISE(ABORT,'tool invocations are immutable'); END;
CREATE TRIGGER IF NOT EXISTS agent_tool_invocation_no_delete BEFORE DELETE ON agent_tool_invocation BEGIN SELECT RAISE(ABORT,'tool invocations are retained'); END;

INSERT OR IGNORE INTO agent_tool_registry(tool_key,tool_name,category,description,capability_tags,access_mode,risk_level,created_at,updated_at) VALUES
 ('uav.get_profile','查询无人机档案','ontology','查询型号、构型、尺寸、重量、动力和航电信息','["uav_profile","ontology"]','read_only','low',datetime('now'),datetime('now')),
 ('uav.get_component_profile','查询部件档案','ontology','查询电机、电池、桨叶、飞控和传感器信息','["component","ontology"]','read_only','low',datetime('now'),datetime('now')),
 ('uav.search_ontology','检索无人机本体','ontology','按概念、属性和关系检索无人机本体库','["ontology","entity_relation"]','read_only','low',datetime('now'),datetime('now')),
 ('uav.get_capability_baseline','获取能力基线','capability','获取型号的能力基线和证据','["capability","baseline","evidence"]','read_only','low',datetime('now'),datetime('now')),
 ('uav.compare_capabilities','比较能力指标','capability','比较型号、改型或任务配置的能力差异','["capability","comparison"]','read_only','low',datetime('now'),datetime('now')),
 ('uav.check_requirement_compliance','检查需求符合性','capability','检查任务需求与现有能力的符合程度','["requirements","compliance"]','read_only','medium',datetime('now'),datetime('now')),
 ('uav.find_capability_gap','识别能力缺口','capability','计算需求与能力基线之间的指标缺口','["gap","requirements"]','read_only','medium',datetime('now'),datetime('now')),
 ('uav.query_flight_logs','查询飞行日志','flight-data','按飞机、任务和时间范围查询飞行日志','["flight_log","time_series"]','read_only','low',datetime('now'),datetime('now')),
 ('uav.get_mission_summary','获取任务摘要','flight-data','获取任务阶段、轨迹和关键事件','["mission","timeline"]','read_only','low',datetime('now'),datetime('now')),
 ('uav.calculate_energy_consumption','计算任务能耗','flight-data','计算任务分阶段能耗和剩余能量','["energy","flight_performance"]','read_only','medium',datetime('now'),datetime('now')),
 ('uav.analyze_flight_performance','分析飞行性能','flight-data','分析速度、爬升、姿态和控制性能','["performance","telemetry"]','read_only','medium',datetime('now'),datetime('now')),
 ('uav.detect_flight_anomalies','检测飞行异常','flight-data','检测温度、电流、振动、姿态和通信异常','["anomaly","telemetry"]','read_only','medium',datetime('now'),datetime('now')),
 ('uav.get_battery_health','评估电池健康','health','计算循环次数、容量衰减、SOH 和风险','["battery","soh","maintenance"]','read_only','medium',datetime('now'),datetime('now')),
 ('uav.calculate_weight_budget','计算重量预算','design','计算重量分配、增重和重心影响','["weight","cg","design"]','read_only','medium',datetime('now'),datetime('now')),
 ('uav.generate_test_matrix','生成验证试验矩阵','manufacturing','根据需求和设计方案生成地面及飞行试验矩阵','["verification","test"]','read_only','medium',datetime('now'),datetime('now'));

INSERT OR IGNORE INTO agent_tool_binding(agent_role,tool_key,created_at)
SELECT role, tool_key, datetime('now') FROM ontology_agent_role CROSS JOIN agent_tool_registry
WHERE role IN ('capability-quantification','ontology-planner')
  AND tool_key IN ('uav.get_profile','uav.get_component_profile','uav.search_ontology','uav.get_capability_baseline','uav.compare_capabilities','uav.check_requirement_compliance','uav.find_capability_gap','uav.query_flight_logs','uav.get_mission_summary','uav.calculate_energy_consumption','uav.analyze_flight_performance','uav.detect_flight_anomalies','uav.get_battery_health','uav.calculate_weight_budget','uav.generate_test_matrix');
