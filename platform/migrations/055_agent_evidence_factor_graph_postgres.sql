-- Evidence factor graph projection for Agent Runs.
-- It records explainable support/constraint relationships only. It cannot
-- promote a manufacturing candidate or ontology fact.
CREATE TABLE IF NOT EXISTS agent_factor_graph_run (
    graph_run_id text PRIMARY KEY,
    run_id text REFERENCES agent_run(run_id),
    entity_id text NOT NULL,
    entity_revision_id text,
    schema_version text NOT NULL CHECK (schema_version='evidence-factor-graph.v1'),
    status text NOT NULL CHECK (status IN ('pending-review','needs-evidence','conflict','complete-with-gaps')),
    graph_json jsonb NOT NULL CHECK (jsonb_typeof(graph_json)='object'),
    content_hash text NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(entity_id, content_hash)
);
CREATE INDEX IF NOT EXISTS agent_factor_graph_run_lookup
    ON agent_factor_graph_run(entity_id, created_at DESC, status);

CREATE TABLE IF NOT EXISTS agent_factor_graph_variable (
    variable_id text PRIMARY KEY,
    graph_run_id text NOT NULL REFERENCES agent_factor_graph_run(graph_run_id),
    variable_key text NOT NULL,
    variable_type text NOT NULL CHECK (variable_type IN ('identity','configuration','organization','process','production','cost','composition')),
    state text NOT NULL CHECK (state IN ('proposed','unknown','conflict','constrained')),
    value_json jsonb,
    unit text,
    conditions_json jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(conditions_json)='object'),
    candidate_ids_json jsonb NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(candidate_ids_json)='array'),
    content_hash text NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(graph_run_id, variable_key)
);
CREATE INDEX IF NOT EXISTS agent_factor_graph_variable_lookup
    ON agent_factor_graph_variable(graph_run_id, variable_type, state);

CREATE TABLE IF NOT EXISTS agent_factor_graph_observation (
    observation_id text PRIMARY KEY,
    graph_run_id text NOT NULL REFERENCES agent_factor_graph_run(graph_run_id),
    candidate_id text,
    source_snapshot_id text NOT NULL,
    source_uri text NOT NULL,
    quote text NOT NULL,
    claim_state text NOT NULL CHECK (claim_state IN ('reported','derived','unknown','conflict','rejected')),
    variable_ids_json jsonb NOT NULL CHECK (jsonb_typeof(variable_ids_json)='array'),
    locator_json jsonb CHECK (locator_json IS NULL OR jsonb_typeof(locator_json)='object'),
    content_hash text NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(graph_run_id, content_hash)
);
CREATE INDEX IF NOT EXISTS agent_factor_graph_observation_lookup
    ON agent_factor_graph_observation(graph_run_id, source_snapshot_id);

CREATE TABLE IF NOT EXISTS agent_factor_graph_factor (
    factor_id text PRIMARY KEY,
    graph_run_id text NOT NULL REFERENCES agent_factor_graph_run(graph_run_id),
    factor_type text NOT NULL CHECK (factor_type IN ('source-support','identity-match','configuration-scope','time-consistency','unit-normalization','source-independence','conflict','review-gate')),
    status text NOT NULL CHECK (status IN ('supporting','contradicting','unresolved','satisfied','failed')),
    variable_ids_json jsonb NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(variable_ids_json)='array'),
    observation_ids_json jsonb NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(observation_ids_json)='array'),
    score numeric CHECK (score IS NULL OR (score >= 0 AND score <= 1)),
    details_json jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(details_json)='object'),
    content_hash text NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(graph_run_id, content_hash)
);
CREATE INDEX IF NOT EXISTS agent_factor_graph_factor_lookup
    ON agent_factor_graph_factor(graph_run_id, factor_type, status);

CREATE TABLE IF NOT EXISTS agent_factor_graph_constraint (
    constraint_id text PRIMARY KEY,
    graph_run_id text NOT NULL REFERENCES agent_factor_graph_run(graph_run_id),
    constraint_type text NOT NULL CHECK (constraint_type IN ('identity','configuration','time','unit','source','review')),
    status text NOT NULL CHECK (status IN ('satisfied','failed','unresolved')),
    variable_ids_json jsonb NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(variable_ids_json)='array'),
    details_json jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(details_json)='object'),
    content_hash text NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(graph_run_id, content_hash)
);
CREATE INDEX IF NOT EXISTS agent_factor_graph_constraint_lookup
    ON agent_factor_graph_constraint(graph_run_id, constraint_type, status);

CREATE OR REPLACE FUNCTION reject_factor_graph_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'factor graph records are append-only';
END $$;
DO $$ DECLARE table_name text; BEGIN
    FOREACH table_name IN ARRAY ARRAY['agent_factor_graph_run','agent_factor_graph_variable','agent_factor_graph_observation','agent_factor_graph_factor','agent_factor_graph_constraint'] LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS %I ON %I', table_name || '_immutable', table_name);
        EXECUTE format('CREATE TRIGGER %I BEFORE UPDATE OR DELETE ON %I FOR EACH ROW EXECUTE FUNCTION reject_factor_graph_mutation()', table_name || '_immutable', table_name);
    END LOOP;
END $$;
