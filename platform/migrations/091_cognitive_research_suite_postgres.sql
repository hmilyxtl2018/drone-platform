-- Versioned configuration and one-use admission for document/ontology research.
CREATE TABLE IF NOT EXISTS agent_cognitive_context (
 context_id text PRIMARY KEY,
 label text NOT NULL,
 context_json jsonb NOT NULL,
 snapshots_json jsonb NOT NULL,
 content_hash text NOT NULL CHECK (content_hash ~ '^[a-f0-9]{64}$'),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE IF NOT EXISTS agent_cognitive_admission (
 admission_id text PRIMARY KEY,
 principal_id text NOT NULL,
 agent_key text NOT NULL REFERENCES agent_definition(agent_key),
 input_json jsonb NOT NULL,
 content_hash text NOT NULL CHECK (content_hash ~ '^[a-f0-9]{64}$'),
 expires_at timestamptz NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE IF NOT EXISTS agent_cognitive_admission_use (
 admission_id text PRIMARY KEY REFERENCES agent_cognitive_admission(admission_id),
 run_id text NOT NULL UNIQUE REFERENCES agent_run(run_id),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE OR REPLACE FUNCTION cognitive_research_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'cognitive research records are immutable'; END $$;
DROP TRIGGER IF EXISTS cognitive_context_immutable ON agent_cognitive_context;
CREATE TRIGGER cognitive_context_immutable BEFORE UPDATE OR DELETE ON agent_cognitive_context
 FOR EACH ROW EXECUTE FUNCTION cognitive_research_immutable();
DROP TRIGGER IF EXISTS cognitive_admission_immutable ON agent_cognitive_admission;
CREATE TRIGGER cognitive_admission_immutable BEFORE UPDATE OR DELETE ON agent_cognitive_admission
 FOR EACH ROW EXECUTE FUNCTION cognitive_research_immutable();
DROP TRIGGER IF EXISTS cognitive_admission_use_immutable ON agent_cognitive_admission_use;
CREATE TRIGGER cognitive_admission_use_immutable BEFORE UPDATE OR DELETE ON agent_cognitive_admission_use
 FOR EACH ROW EXECUTE FUNCTION cognitive_research_immutable();
