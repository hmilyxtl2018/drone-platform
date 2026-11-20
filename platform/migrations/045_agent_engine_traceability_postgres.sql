-- P4-08: immutable requirements-to-verification traceability matrices.
-- A matrix is a planning/verification record; it cannot certify manufacture or flight.

CREATE TABLE IF NOT EXISTS agent_traceability_matrix (
    matrix_id text PRIMARY KEY,
    run_id text NOT NULL REFERENCES agent_run(run_id),
    workspace_id text,
    workspace_revision bigint CHECK(workspace_revision IS NULL OR workspace_revision > 0),
    workspace_revision_hash text CHECK(workspace_revision_hash IS NULL OR workspace_revision_hash ~ '^[0-9a-f]{64}$'),
    run_profile_hash text NOT NULL CHECK(run_profile_hash ~ '^[0-9a-f]{64}$'),
    matrix_json jsonb NOT NULL CHECK(jsonb_typeof(matrix_json)='object'),
    matrix_hash text NOT NULL CHECK(matrix_hash ~ '^[0-9a-f]{64}$'),
    status text NOT NULL CHECK(status IN ('planned','unknown','blocked','passed','failed')),
    created_by text NOT NULL REFERENCES agent_admin_identity(principal_id),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(run_id,matrix_hash)
);
CREATE INDEX IF NOT EXISTS agent_traceability_matrix_run_idx
    ON agent_traceability_matrix(run_id,created_at,matrix_id);

CREATE TABLE IF NOT EXISTS agent_traceability_item (
    item_id text PRIMARY KEY,
    matrix_id text NOT NULL REFERENCES agent_traceability_matrix(matrix_id),
    item_type text NOT NULL CHECK(item_type IN ('requirement','constraint','calculation','verification','material','data-request')),
    item_json jsonb NOT NULL CHECK(jsonb_typeof(item_json)='object'),
    item_hash text NOT NULL CHECK(item_hash ~ '^[0-9a-f]{64}$'),
    status text NOT NULL CHECK(status IN ('planned','unknown','blocked','passed','failed','not-tested')),
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS agent_traceability_item_matrix_idx
    ON agent_traceability_item(matrix_id,item_type,item_id);

CREATE OR REPLACE FUNCTION agent_traceability_reject_mutation() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'traceability history is append-only' USING ERRCODE='55000';
END;
$$;
DO $$
DECLARE table_name text;
BEGIN
    FOREACH table_name IN ARRAY ARRAY['agent_traceability_matrix','agent_traceability_item'] LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS %I_immutable ON %I',table_name,table_name);
        EXECUTE format('CREATE TRIGGER %I_immutable BEFORE UPDATE OR DELETE ON %I '
                       'FOR EACH ROW EXECUTE FUNCTION agent_traceability_reject_mutation()',table_name,table_name);
    END LOOP;
END;
$$;
