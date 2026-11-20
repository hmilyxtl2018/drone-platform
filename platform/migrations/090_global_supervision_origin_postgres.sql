-- Additive provenance for new Runs. Historical ownership is projected from
-- existing immutable evidence; no old Profile, Run input or snapshot is edited.
CREATE TABLE IF NOT EXISTS agent_run_origin (
    run_id text PRIMARY KEY REFERENCES agent_run(run_id),
    owner_principal_id text REFERENCES agent_admin_identity(principal_id),
    tenant_id text,
    source_kind text NOT NULL CHECK (source_kind IN
        ('dsh','admin','autopilot','workspace','followup','delegation','handoff','engineering','evolution','system','unknown')),
    source_ref text,
    parent_run_id text REFERENCES agent_run(run_id),
    lineage_root_run_id text REFERENCES agent_run(run_id),
    attribution_status text NOT NULL CHECK (attribution_status IN ('recorded','derived','unknown','conflict')),
    evidence_refs_json jsonb NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(evidence_refs_json)='array'),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK (parent_run_id IS NULL OR parent_run_id<>run_id),
    CHECK ((attribution_status IN ('recorded','derived') AND owner_principal_id IS NOT NULL)
           OR attribution_status IN ('unknown','conflict'))
);
CREATE INDEX IF NOT EXISTS agent_origin_owner_created_idx
    ON agent_run_origin(owner_principal_id,created_at DESC,run_id);
CREATE INDEX IF NOT EXISTS agent_origin_parent_idx ON agent_run_origin(parent_run_id)
    WHERE parent_run_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS agent_origin_root_idx ON agent_run_origin(lineage_root_run_id)
    WHERE lineage_root_run_id IS NOT NULL;
CREATE OR REPLACE FUNCTION agent_origin_reject_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'run origin history is append-only' USING ERRCODE='55000'; END $$;
DROP TRIGGER IF EXISTS agent_origin_immutable ON agent_run_origin;
CREATE TRIGGER agent_origin_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON agent_run_origin
    FOR EACH STATEMENT EXECUTE FUNCTION agent_origin_reject_mutation();
DO $$ BEGIN
    IF to_regprocedure(current_schema() || '.agent_worker_fence_guard()') IS NOT NULL THEN
        DROP TRIGGER IF EXISTS agent_origin_worker_fence ON agent_run_origin;
        CREATE TRIGGER agent_origin_worker_fence BEFORE INSERT ON agent_run_origin
            FOR EACH ROW EXECUTE FUNCTION agent_worker_fence_guard();
    END IF;
END $$;
