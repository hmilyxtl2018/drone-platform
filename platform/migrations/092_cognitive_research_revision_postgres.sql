-- One immutable, evidence-targeted successor per original cognitive budget root.
CREATE TABLE IF NOT EXISTS agent_cognitive_revision (
 root_run_id text PRIMARY KEY REFERENCES agent_run_budget(root_run_id),
 parent_run_id text NOT NULL UNIQUE REFERENCES agent_run(run_id),
 child_run_id text NOT NULL UNIQUE REFERENCES agent_run(run_id),
 parent_profile_hash text NOT NULL CHECK (parent_profile_hash ~ '^[a-f0-9]{64}$'),
 input_json jsonb NOT NULL CHECK (jsonb_typeof(input_json)='object'),
 content_hash text NOT NULL CHECK (content_hash ~ '^[a-f0-9]{64}$'),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 CHECK (root_run_id=parent_run_id AND child_run_id<>parent_run_id)
);
DROP TRIGGER IF EXISTS cognitive_revision_immutable ON agent_cognitive_revision;
CREATE TRIGGER cognitive_revision_immutable BEFORE UPDATE OR DELETE ON agent_cognitive_revision
 FOR EACH ROW EXECUTE FUNCTION cognitive_research_immutable();
