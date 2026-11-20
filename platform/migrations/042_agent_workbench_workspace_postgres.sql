-- Versioned research workspaces. Apply after 035/039; no source DB writes.
CREATE TABLE IF NOT EXISTS agent_research_workspace (
    workspace_id text PRIMARY KEY,
    owner_id text NOT NULL REFERENCES agent_admin_identity(principal_id),
    current_revision integer NOT NULL CHECK(current_revision>0),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS agent_workspace_revision (
    workspace_id text NOT NULL REFERENCES agent_research_workspace(workspace_id),
    revision integer NOT NULL CHECK(revision>0),
    content_json jsonb NOT NULL CHECK(jsonb_typeof(content_json)='object'),
    content_hash text NOT NULL CHECK(content_hash ~ '^[0-9a-f]{64}$'),
    actor_id text NOT NULL REFERENCES agent_admin_identity(principal_id),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(workspace_id,revision)
);
CREATE TABLE IF NOT EXISTS agent_workspace_run (
    binding_id text PRIMARY KEY,
    workspace_id text NOT NULL,
    revision integer NOT NULL,
    run_id text NOT NULL UNIQUE REFERENCES agent_run(run_id),
    idempotency_key text NOT NULL,
    request_hash text NOT NULL CHECK(request_hash ~ '^[0-9a-f]{64}$'),
    revision_hash text NOT NULL CHECK(revision_hash ~ '^[0-9a-f]{64}$'),
    actor_id text NOT NULL REFERENCES agent_admin_identity(principal_id),
    previous_run_id text REFERENCES agent_run(run_id),
    created_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY(workspace_id,revision) REFERENCES agent_workspace_revision(workspace_id,revision),
    UNIQUE(workspace_id,idempotency_key)
);
CREATE INDEX IF NOT EXISTS agent_workspace_owner_idx ON agent_research_workspace(owner_id,updated_at DESC);
CREATE INDEX IF NOT EXISTS agent_workspace_runs_idx ON agent_workspace_run(workspace_id,revision,created_at);
CREATE OR REPLACE FUNCTION agent_workspace_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'workspace history is immutable: %',TG_TABLE_NAME USING ERRCODE='55000';
END;
$$;
DROP TRIGGER IF EXISTS agent_workspace_revision_immutable ON agent_workspace_revision;
CREATE TRIGGER agent_workspace_revision_immutable BEFORE UPDATE OR DELETE ON agent_workspace_revision
FOR EACH ROW EXECUTE FUNCTION agent_workspace_immutable();
DROP TRIGGER IF EXISTS agent_workspace_run_immutable ON agent_workspace_run;
CREATE TRIGGER agent_workspace_run_immutable BEFORE UPDATE OR DELETE ON agent_workspace_run
FOR EACH ROW EXECUTE FUNCTION agent_workspace_immutable();
DROP TRIGGER IF EXISTS agent_workspace_revision_no_truncate ON agent_workspace_revision;
CREATE TRIGGER agent_workspace_revision_no_truncate BEFORE TRUNCATE ON agent_workspace_revision
FOR EACH STATEMENT EXECUTE FUNCTION agent_workspace_immutable();
DROP TRIGGER IF EXISTS agent_workspace_run_no_truncate ON agent_workspace_run;
CREATE TRIGGER agent_workspace_run_no_truncate BEFORE TRUNCATE ON agent_workspace_run
FOR EACH STATEMENT EXECUTE FUNCTION agent_workspace_immutable();

CREATE OR REPLACE FUNCTION agent_workspace_revision_integrity() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE head integer;
BEGIN
    SELECT current_revision INTO head FROM agent_research_workspace
        WHERE workspace_id=NEW.workspace_id FOR UPDATE;
    IF head IS DISTINCT FROM NEW.revision THEN
        RAISE EXCEPTION 'workspace revision must match locked head' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS agent_workspace_revision_integrity ON agent_workspace_revision;
CREATE TRIGGER agent_workspace_revision_integrity BEFORE INSERT ON agent_workspace_revision
FOR EACH ROW EXECUTE FUNCTION agent_workspace_revision_integrity();

CREATE OR REPLACE FUNCTION agent_workspace_binding_integrity() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE source_row record; run_row record;
BEGIN
    SELECT content_hash INTO source_row FROM agent_workspace_revision
        WHERE workspace_id=NEW.workspace_id AND revision=NEW.revision;
    SELECT input_json INTO run_row FROM agent_run WHERE run_id=NEW.run_id;
    IF source_row.content_hash IS DISTINCT FROM NEW.revision_hash
       OR run_row.input_json->>'workspaceId' IS DISTINCT FROM NEW.workspace_id
       OR run_row.input_json->>'workspaceRevision' IS DISTINCT FROM NEW.revision::text
       OR run_row.input_json->>'workspaceRevisionHash' IS DISTINCT FROM NEW.revision_hash
    THEN RAISE EXCEPTION 'run workspace revision binding mismatch' USING ERRCODE='23514'; END IF;
    RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS agent_workspace_binding_integrity ON agent_workspace_run;
CREATE TRIGGER agent_workspace_binding_integrity BEFORE INSERT ON agent_workspace_run
FOR EACH ROW EXECUTE FUNCTION agent_workspace_binding_integrity();
