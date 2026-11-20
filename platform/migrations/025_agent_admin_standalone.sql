-- Independent Agent/tool administration database.
CREATE TABLE IF NOT EXISTS agent_definition (
  agent_key TEXT PRIMARY KEY,
  display_name TEXT NOT NULL,
  description TEXT NOT NULL,
  domain TEXT NOT NULL DEFAULT 'uav',
  status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','testing','published','deprecated','retired')),
  current_version TEXT NOT NULL DEFAULT '1.0.0',
  system_prompt TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS agent_version (
  agent_key TEXT NOT NULL REFERENCES agent_definition(agent_key),
  version TEXT NOT NULL,
  prompt TEXT NOT NULL DEFAULT '',
  config_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(config_json)),
  status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','testing','published','deprecated','retired')),
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  PRIMARY KEY(agent_key,version)
);
CREATE TABLE IF NOT EXISTS tool_registry (
  tool_key TEXT PRIMARY KEY,
  tool_name TEXT NOT NULL,
  category TEXT NOT NULL,
  description TEXT NOT NULL,
  protocol TEXT NOT NULL DEFAULT 'http',
  input_schema TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(input_schema)),
  output_schema TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(output_schema)),
  tags_json TEXT NOT NULL DEFAULT '[]' CHECK(json_valid(tags_json)),
  access_mode TEXT NOT NULL DEFAULT 'read_only' CHECK(access_mode IN ('read_only','write','approval_required')),
  risk_level TEXT NOT NULL DEFAULT 'medium' CHECK(risk_level IN ('low','medium','high','critical')),
  status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','testing','approved','published','deprecated','retired')),
  current_version TEXT NOT NULL DEFAULT '1.0.0',
  endpoint TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS tool_version (
  tool_key TEXT NOT NULL REFERENCES tool_registry(tool_key),
  version TEXT NOT NULL,
  endpoint TEXT,
  input_schema TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(input_schema)),
  output_schema TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(output_schema)),
  changelog TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','testing','approved','published','deprecated','retired')),
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  PRIMARY KEY(tool_key,version)
);
CREATE TABLE IF NOT EXISTS agent_tool_binding (
  agent_key TEXT NOT NULL REFERENCES agent_definition(agent_key),
  tool_key TEXT NOT NULL REFERENCES tool_registry(tool_key),
  tool_version TEXT NOT NULL DEFAULT '1.0.0',
  enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)),
  approval_required INTEGER NOT NULL DEFAULT 0 CHECK(approval_required IN (0,1)),
  config_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(config_json)),
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  PRIMARY KEY(agent_key,tool_key)
);
CREATE TABLE IF NOT EXISTS workflow_definition (
  workflow_key TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  description TEXT NOT NULL,
  definition_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(definition_json)),
  status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','testing','published','deprecated')),
  version TEXT NOT NULL DEFAULT '1.0.0',
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS tool_invocation_audit (
  invocation_id TEXT PRIMARY KEY,
  agent_key TEXT,
  workflow_key TEXT,
  tool_key TEXT NOT NULL,
  tool_version TEXT NOT NULL,
  status TEXT NOT NULL,
  input_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(input_json)),
  output_json TEXT,
  trace_id TEXT,
  latency_ms INTEGER,
  error_message TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS admin_audit_event (
  event_id INTEGER PRIMARY KEY AUTOINCREMENT,
  actor TEXT NOT NULL,
  action TEXT NOT NULL,
  object_type TEXT NOT NULL,
  object_key TEXT NOT NULL,
  detail_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(detail_json)),
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS tool_registry_category_idx ON tool_registry(category,status);
CREATE INDEX IF NOT EXISTS tool_invocation_tool_idx ON tool_invocation_audit(tool_key,created_at);

INSERT OR IGNORE INTO agent_definition(agent_key,display_name,description,domain,status) VALUES
 ('uav-capability-analyst','无人机能力分析 Agent','分析无人机本体能力、飞行表现和能力缺口','uav','published'),
 ('uav-design-engineer','无人机设计制造 Agent','围绕需求、改型、重量能源预算和试验验证提出设计方案','uav','testing'),
 ('uav-evidence-reviewer','无人机证据审查 Agent','审查结论、证据链和需求到试验的可追溯性','uav','testing');
INSERT OR IGNORE INTO agent_version(agent_key,version,status) SELECT agent_key,current_version,status FROM agent_definition;

INSERT OR IGNORE INTO tool_registry(tool_key,tool_name,category,description,tags_json,access_mode,risk_level,status) VALUES
 ('uav.get_profile','查询无人机档案','ontology','查询型号、构型、尺寸、重量、动力和航电信息','["uav_profile","ontology"]','read_only','low','published'),
 ('uav.get_component_profile','查询部件档案','ontology','查询电机、电池、桨叶、飞控和传感器信息','["component","ontology"]','read_only','low','published'),
 ('uav.search_ontology','检索无人机本体','ontology','按概念、属性和关系检索无人机本体库','["ontology","entity_relation"]','read_only','low','published'),
 ('uav.get_capability_baseline','获取能力基线','capability','获取型号的能力基线和证据','["capability","baseline","evidence"]','read_only','low','published'),
 ('uav.compare_capabilities','比较能力指标','capability','比较型号、改型或任务配置的能力差异','["capability","comparison"]','read_only','low','published'),
 ('uav.check_requirement_compliance','检查需求符合性','capability','检查任务需求与现有能力的符合程度','["requirements","compliance"]','read_only','medium','published'),
 ('uav.find_capability_gap','识别能力缺口','capability','计算需求与能力基线之间的指标缺口','["gap","requirements"]','read_only','medium','published'),
 ('uav.query_flight_logs','查询飞行日志','flight-data','按飞机、任务和时间范围查询飞行日志','["flight_log","time_series"]','read_only','low','published'),
 ('uav.get_mission_summary','获取任务摘要','flight-data','获取任务阶段、轨迹和关键事件','["mission","timeline"]','read_only','low','published'),
 ('uav.calculate_energy_consumption','计算任务能耗','flight-data','计算任务分阶段能耗和剩余能量','["energy","flight_performance"]','read_only','medium','published'),
 ('uav.analyze_flight_performance','分析飞行性能','flight-data','分析速度、爬升、姿态和控制性能','["performance","telemetry"]','read_only','medium','published'),
 ('uav.detect_flight_anomalies','检测飞行异常','flight-data','检测温度、电流、振动、姿态和通信异常','["anomaly","telemetry"]','read_only','medium','published'),
 ('uav.get_battery_health','评估电池健康','health','计算循环次数、容量衰减、SOH 和风险','["battery","soh","maintenance"]','read_only','medium','published'),
 ('uav.calculate_weight_budget','计算重量预算','design','计算重量分配、增重和重心影响','["weight","cg","design"]','read_only','medium','published'),
 ('uav.generate_test_matrix','生成验证试验矩阵','manufacturing','根据需求和设计方案生成地面及飞行试验矩阵','["verification","test"]','read_only','medium','published');
INSERT OR IGNORE INTO tool_version(tool_key,version,status) SELECT tool_key,current_version,status FROM tool_registry;
INSERT OR IGNORE INTO agent_tool_binding(agent_key,tool_key,approval_required)
 SELECT a.agent_key,t.tool_key,CASE WHEN t.risk_level IN ('high','critical') THEN 1 ELSE 0 END
 FROM agent_definition a CROSS JOIN tool_registry t
 WHERE a.agent_key IN ('uav-capability-analyst','uav-design-engineer','uav-evidence-reviewer');
INSERT OR IGNORE INTO workflow_definition(workflow_key,name,description,definition_json,status) VALUES
 ('uav-capability-gap-poc','无人机能力缺口分析 POC','需求输入到能力基线、缺口、改型建议和试验矩阵','{"steps":["requirements","baseline","gap","design","verification"]}','published');
