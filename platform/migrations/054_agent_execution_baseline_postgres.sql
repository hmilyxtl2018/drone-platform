-- Versioned execution graphs. Legacy steps remain the authoring projection.
CREATE TABLE IF NOT EXISTS agent_execution_baseline (
    baseline_id text PRIMARY KEY,
    agent_key text NOT NULL REFERENCES agent_definition(agent_key),
    profile_version text NOT NULL,
    baseline_version bigint NOT NULL CHECK (baseline_version>0),
    schema_version text NOT NULL,
    status text NOT NULL CHECK (status IN ('testing','published','retired')),
    graph_json jsonb NOT NULL CHECK (jsonb_typeof(graph_json)='object'),
    graph_hash text NOT NULL CHECK (graph_hash ~ '^[0-9a-f]{64}$'),
    created_by text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    published_at timestamptz,
    UNIQUE (agent_key,baseline_version)
);
CREATE INDEX IF NOT EXISTS agent_execution_baseline_lookup
    ON agent_execution_baseline(agent_key,profile_version,graph_hash,created_at DESC);

CREATE TABLE IF NOT EXISTS agent_execution_edge (
    baseline_id text NOT NULL REFERENCES agent_execution_baseline(baseline_id),
    from_node_key text NOT NULL,
    to_node_key text NOT NULL,
    edge_type text NOT NULL CHECK (edge_type='dependency'),
    CHECK (from_node_key<>to_node_key),
    PRIMARY KEY (baseline_id,from_node_key,to_node_key)
);

CREATE OR REPLACE FUNCTION reject_execution_baseline_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'execution baseline records are append-only';
END $$;
DROP TRIGGER IF EXISTS agent_execution_baseline_immutable ON agent_execution_baseline;
CREATE TRIGGER agent_execution_baseline_immutable BEFORE UPDATE OR DELETE ON agent_execution_baseline
    FOR EACH ROW EXECUTE FUNCTION reject_execution_baseline_mutation();
DROP TRIGGER IF EXISTS agent_execution_edge_immutable ON agent_execution_edge;
CREATE TRIGGER agent_execution_edge_immutable BEFORE UPDATE OR DELETE ON agent_execution_edge
    FOR EACH ROW EXECUTE FUNCTION reject_execution_baseline_mutation();

ALTER TABLE agent_run ADD COLUMN IF NOT EXISTS baseline_id text REFERENCES agent_execution_baseline(baseline_id);
ALTER TABLE agent_run ADD COLUMN IF NOT EXISTS baseline_hash text;
ALTER TABLE agent_run_harness_snapshot ADD COLUMN IF NOT EXISTS baseline_hash text;
ALTER TABLE agent_run_plan ADD COLUMN IF NOT EXISTS baseline_hash text;
ALTER TABLE agent_run_plan ADD COLUMN IF NOT EXISTS parent_node_key text;
