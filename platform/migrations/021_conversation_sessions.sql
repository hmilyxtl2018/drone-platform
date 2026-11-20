-- Conversation-driven analysis foundations.
-- Sessions and messages are an interaction log; they do not grant permission
-- to publish ontology facts. Message rows are append-only and analysis runs
-- may continue to exist without a session (legacy compatibility).

CREATE TABLE IF NOT EXISTS ontology_conversation_session (
  conversation_session_id TEXT PRIMARY KEY,
  entity_id TEXT,
  title TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','closed','archived')),
  actor_id TEXT NOT NULL DEFAULT 'operator',
  context_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(context_json) AND json_type(context_json)='object'),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  closed_at TEXT,
  last_message_at TEXT
);
CREATE INDEX IF NOT EXISTS ontology_conversation_session_entity_idx
  ON ontology_conversation_session(entity_id, status, updated_at DESC);

CREATE TABLE IF NOT EXISTS ontology_conversation_message (
  conversation_message_id TEXT PRIMARY KEY,
  conversation_session_id TEXT NOT NULL REFERENCES ontology_conversation_session(conversation_session_id),
  analysis_run_id TEXT REFERENCES ontology_analysis_run(analysis_run_id),
  sequence_no INTEGER NOT NULL CHECK(sequence_no >= 1),
  role TEXT NOT NULL CHECK(role IN ('user','assistant','system','tool')),
  content_text TEXT NOT NULL,
  content_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(content_json) AND json_type(content_json)='object'),
  intent_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(intent_json) AND json_type(intent_json)='object'),
  content_hash TEXT NOT NULL CHECK(length(content_hash)=64),
  created_at TEXT NOT NULL,
  UNIQUE(conversation_session_id, sequence_no)
);
CREATE INDEX IF NOT EXISTS ontology_conversation_message_session_idx
  ON ontology_conversation_message(conversation_session_id, sequence_no);
CREATE INDEX IF NOT EXISTS ontology_conversation_message_run_idx
  ON ontology_conversation_message(analysis_run_id, created_at DESC);
CREATE TRIGGER IF NOT EXISTS ontology_conversation_message_no_update BEFORE UPDATE ON ontology_conversation_message BEGIN SELECT RAISE(ABORT,'conversation messages are immutable'); END;
CREATE TRIGGER IF NOT EXISTS ontology_conversation_message_no_delete BEFORE DELETE ON ontology_conversation_message BEGIN SELECT RAISE(ABORT,'conversation messages are retained'); END;

ALTER TABLE ontology_analysis_run ADD COLUMN conversation_session_id TEXT REFERENCES ontology_conversation_session(conversation_session_id);
ALTER TABLE ontology_analysis_run ADD COLUMN prompt_text TEXT;
ALTER TABLE ontology_analysis_run ADD COLUMN intent_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(intent_json) AND json_type(intent_json)='object');
CREATE INDEX IF NOT EXISTS ontology_analysis_run_session_idx
  ON ontology_analysis_run(conversation_session_id, created_at DESC);
