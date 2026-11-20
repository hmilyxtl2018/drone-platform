-- Durable runtime records for Agent Engine execution.
CREATE TABLE IF NOT EXISTS agent_run (
  run_id TEXT PRIMARY KEY,
  agent_key TEXT NOT NULL REFERENCES agent_definition(agent_key),
  agent_version TEXT NOT NULL,
  workflow_key TEXT REFERENCES workflow_definition(workflow_key),
  workflow_version TEXT,
  status TEXT NOT NULL CHECK(status IN ('created','queued','planning','running','waiting-tool','waiting-human','retrying','looping','validating','completed','completed-with-gaps','requires-human-review','blocked','failed','cancelled')),
  input_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(input_json)),
  output_json TEXT CHECK(output_json IS NULL OR json_valid(output_json)),
  current_step_key TEXT,
  iteration INTEGER NOT NULL DEFAULT 0 CHECK(iteration >= 0),
  tool_call_count INTEGER NOT NULL DEFAULT 0 CHECK(tool_call_count >= 0),
  model_call_count INTEGER NOT NULL DEFAULT 0 CHECK(model_call_count >= 0),
  error_message TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  started_at TEXT,
  updated_at TEXT NOT NULL DEFAULT (datetime('now')),
  completed_at TEXT
);
CREATE TABLE IF NOT EXISTS agent_run_profile_snapshot (
  run_id TEXT PRIMARY KEY REFERENCES agent_run(run_id),
  profile_json TEXT NOT NULL CHECK(json_valid(profile_json)),
  profile_hash TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS agent_run_state (
  run_id TEXT PRIMARY KEY REFERENCES agent_run(run_id),
  state_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(state_json)),
  revision INTEGER NOT NULL DEFAULT 1 CHECK(revision > 0),
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS agent_step_run (
  step_run_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES agent_run(run_id),
  step_key TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('ready','running','succeeded','failed','skipped','waiting-human','cancelled')),
  attempt INTEGER NOT NULL DEFAULT 0 CHECK(attempt >= 0),
  input_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(input_json)),
  output_json TEXT CHECK(output_json IS NULL OR json_valid(output_json)),
  error_message TEXT,
  started_at TEXT,
  finished_at TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE(run_id,step_key,attempt)
);
CREATE TABLE IF NOT EXISTS agent_tool_call (
  call_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES agent_run(run_id),
  step_run_id TEXT REFERENCES agent_step_run(step_run_id),
  tool_key TEXT NOT NULL REFERENCES tool_registry(tool_key),
  tool_version TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('queued','running','succeeded','failed','timeout','cancelled')),
  input_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(input_json)),
  output_json TEXT CHECK(output_json IS NULL OR json_valid(output_json)),
  error_message TEXT,
  retry_count INTEGER NOT NULL DEFAULT 0,
  latency_ms INTEGER,
  trace_id TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  started_at TEXT,
  finished_at TEXT
);
CREATE TABLE IF NOT EXISTS agent_loop_run (
  loop_run_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES agent_run(run_id),
  loop_id TEXT NOT NULL REFERENCES agent_loop_rule(loop_id),
  iteration INTEGER NOT NULL CHECK(iteration > 0),
  trigger_reason TEXT NOT NULL,
  return_step_key TEXT NOT NULL,
  progress_before_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(progress_before_json)),
  progress_after_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(progress_after_json)),
  stop_reason TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS agent_approval_request (
  approval_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES agent_run(run_id),
  step_key TEXT,
  action TEXT NOT NULL,
  reason TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','approved','rejected','expired','cancelled')),
  requested_by TEXT NOT NULL,
  reviewed_by TEXT,
  review_comment TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  reviewed_at TEXT
);
CREATE TABLE IF NOT EXISTS agent_run_artifact (
  artifact_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES agent_run(run_id),
  step_run_id TEXT REFERENCES agent_step_run(step_run_id),
  artifact_type TEXT NOT NULL,
  content_json TEXT NOT NULL CHECK(json_valid(content_json)),
  content_hash TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE(run_id,content_hash)
);
CREATE TABLE IF NOT EXISTS agent_run_event (
  event_id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL REFERENCES agent_run(run_id),
  event_type TEXT NOT NULL,
  step_key TEXT,
  detail_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(detail_json)),
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS agent_run_status_idx ON agent_run(status,created_at);
CREATE INDEX IF NOT EXISTS agent_step_run_idx ON agent_step_run(run_id,created_at);
CREATE INDEX IF NOT EXISTS agent_tool_call_run_idx ON agent_tool_call(run_id,created_at);
CREATE INDEX IF NOT EXISTS agent_run_event_idx ON agent_run_event(run_id,event_id);
CREATE TRIGGER IF NOT EXISTS agent_run_event_no_update BEFORE UPDATE ON agent_run_event BEGIN SELECT RAISE(ABORT,'run events are immutable'); END;
CREATE TRIGGER IF NOT EXISTS agent_run_event_no_delete BEFORE DELETE ON agent_run_event BEGIN SELECT RAISE(ABORT,'run events are retained'); END;
CREATE TRIGGER IF NOT EXISTS agent_tool_call_no_update BEFORE UPDATE ON agent_tool_call BEGIN SELECT RAISE(ABORT,'tool calls are immutable'); END;
CREATE TRIGGER IF NOT EXISTS agent_tool_call_no_delete BEFORE DELETE ON agent_tool_call BEGIN SELECT RAISE(ABORT,'tool calls are retained'); END;
