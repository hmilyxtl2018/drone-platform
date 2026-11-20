-- Multi-agent analysis orchestration.
--
-- Analysis runs and agent tasks are execution records.  Agent outputs are
-- immutable candidate artifacts and remain outside the trusted ontology until
-- the existing human review/publication gates are used.

CREATE TABLE IF NOT EXISTS ontology_analysis_run (
  analysis_run_id TEXT PRIMARY KEY,
  entity_id TEXT,
  status TEXT NOT NULL CHECK(status IN ('queued','planning','running','paused','completed','completed-with-gaps','failed','cancelled')),
  request_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(request_json) AND json_type(request_json)='object'),
  budget_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(budget_json) AND json_type(budget_json)='object'),
  result_json TEXT CHECK(result_json IS NULL OR (json_valid(result_json) AND json_type(result_json)='object')),
  retry_of TEXT REFERENCES ontology_analysis_run(analysis_run_id),
  stop_reason TEXT,
  error_message TEXT,
  created_at TEXT NOT NULL,
  started_at TEXT,
  updated_at TEXT NOT NULL,
  completed_at TEXT
);
CREATE INDEX IF NOT EXISTS ontology_analysis_run_entity_idx ON ontology_analysis_run(entity_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ontology_analysis_run_status_idx ON ontology_analysis_run(status, created_at DESC);

CREATE TABLE IF NOT EXISTS ontology_agent_task (
  agent_task_id TEXT PRIMARY KEY,
  analysis_run_id TEXT NOT NULL REFERENCES ontology_analysis_run(analysis_run_id),
  role TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('queued','ready','running','succeeded','failed','blocked','cancelled')),
  dependency_ids TEXT NOT NULL DEFAULT '[]' CHECK(json_valid(dependency_ids) AND json_type(dependency_ids)='array'),
  input_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(input_json) AND json_type(input_json)='object'),
  output_json TEXT CHECK(output_json IS NULL OR json_valid(output_json)),
  budget_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(budget_json) AND json_type(budget_json)='object'),
  attempt INTEGER NOT NULL DEFAULT 0 CHECK(attempt >= 0),
  max_attempts INTEGER NOT NULL DEFAULT 2 CHECK(max_attempts >= 1),
  worker_id TEXT,
  error_message TEXT,
  created_at TEXT NOT NULL,
  started_at TEXT,
  updated_at TEXT NOT NULL,
  completed_at TEXT,
  UNIQUE(analysis_run_id, role)
);
CREATE INDEX IF NOT EXISTS ontology_agent_task_run_idx ON ontology_agent_task(analysis_run_id, status, created_at);
CREATE INDEX IF NOT EXISTS ontology_agent_task_role_idx ON ontology_agent_task(role, status, created_at DESC);

CREATE TABLE IF NOT EXISTS ontology_agent_artifact (
  artifact_id TEXT PRIMARY KEY,
  analysis_run_id TEXT NOT NULL REFERENCES ontology_analysis_run(analysis_run_id),
  agent_task_id TEXT NOT NULL REFERENCES ontology_agent_task(agent_task_id),
  artifact_type TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'under-review' CHECK(status IN ('candidate','under-review','rejected','superseded')),
  content_json TEXT NOT NULL CHECK(json_valid(content_json)),
  content_hash TEXT NOT NULL CHECK(length(content_hash)=64),
  created_at TEXT NOT NULL,
  UNIQUE(agent_task_id, content_hash)
);
CREATE INDEX IF NOT EXISTS ontology_agent_artifact_run_idx ON ontology_agent_artifact(analysis_run_id, status, created_at DESC);
CREATE INDEX IF NOT EXISTS ontology_agent_artifact_task_idx ON ontology_agent_artifact(agent_task_id, created_at DESC);
CREATE TRIGGER IF NOT EXISTS ontology_agent_artifact_no_update BEFORE UPDATE ON ontology_agent_artifact BEGIN SELECT RAISE(ABORT,'agent artifacts are immutable'); END;
CREATE TRIGGER IF NOT EXISTS ontology_agent_artifact_no_delete BEFORE DELETE ON ontology_agent_artifact BEGIN SELECT RAISE(ABORT,'agent artifacts are retained'); END;

CREATE TABLE IF NOT EXISTS ontology_agent_handoff (
  handoff_id TEXT PRIMARY KEY,
  analysis_run_id TEXT NOT NULL REFERENCES ontology_analysis_run(analysis_run_id),
  from_task_id TEXT NOT NULL REFERENCES ontology_agent_task(agent_task_id),
  to_task_id TEXT NOT NULL REFERENCES ontology_agent_task(agent_task_id),
  artifact_id TEXT REFERENCES ontology_agent_artifact(artifact_id),
  status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','accepted','rejected','cancelled')),
  payload_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(payload_json) AND json_type(payload_json)='object'),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ontology_agent_handoff_run_idx ON ontology_agent_handoff(analysis_run_id, status, created_at DESC);
CREATE INDEX IF NOT EXISTS ontology_agent_handoff_target_idx ON ontology_agent_handoff(to_task_id, status, created_at DESC);

CREATE TABLE IF NOT EXISTS ontology_analysis_event (
  event_id INTEGER PRIMARY KEY AUTOINCREMENT,
  analysis_run_id TEXT NOT NULL REFERENCES ontology_analysis_run(analysis_run_id),
  event_type TEXT NOT NULL,
  agent_task_id TEXT,
  detail_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(detail_json) AND json_type(detail_json)='object'),
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ontology_analysis_event_run_idx ON ontology_analysis_event(analysis_run_id,event_id);
CREATE TRIGGER IF NOT EXISTS ontology_analysis_event_no_update BEFORE UPDATE ON ontology_analysis_event BEGIN SELECT RAISE(ABORT,'analysis events are immutable'); END;
CREATE TRIGGER IF NOT EXISTS ontology_analysis_event_no_delete BEFORE DELETE ON ontology_analysis_event BEGIN SELECT RAISE(ABORT,'analysis events are retained'); END;

CREATE TABLE IF NOT EXISTS ontology_agent_role (
  role TEXT PRIMARY KEY,
  display_name TEXT NOT NULL,
  description TEXT NOT NULL,
  input_contract TEXT NOT NULL,
  output_contract TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1))
);

INSERT OR IGNORE INTO ontology_agent_role(role,display_name,description,input_contract,output_contract) VALUES
 ('ontology-planner','Ontology Planner','读取实体档案和缺口，生成研究计划','entity-profile.v1','research-plan.v1'),
 ('intelligence-collector','Intelligence Collector','按研究计划采集公开来源和影像证据','research-plan.v1','source-bundle.v1'),
 ('attribute-relation-extractor','Attribute & Relation Extractor','从来源正文提取属性、关系、条件和引文','source-bundle.v1','ontology-candidates.v1'),
 ('signal-imagery-analyst','Signal / Imagery Analyst','关联公开信号、遥测、影像与装备配置','source-bundle.v1','observation-bundle.v1'),
 ('evidence-fusion','Evidence Fusion Analyst','去重、判断来源独立性并标记冲突','ontology-candidates.v1+observation-bundle.v1','evidence-bundle.v1'),
 ('capability-quantification','Capability Quantification Analyst','把技术属性转换为带条件的作战能力指标','evidence-bundle.v1','capability-assessment.v1'),
 ('red-team-governance-reviewer','Red Team & Governance Reviewer','寻找反证、过度推断和发布风险','capability-assessment.v1','review-package.v1');
