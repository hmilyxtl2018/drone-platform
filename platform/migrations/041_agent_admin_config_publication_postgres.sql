-- P4-02 configuration drafts and immutable publications. No profiles are seeded.
ALTER TABLE agent_step ADD COLUMN IF NOT EXISTS dependencies_json jsonb NOT NULL DEFAULT '[]'::jsonb
    CHECK(jsonb_typeof(dependencies_json)='array');

CREATE TABLE IF NOT EXISTS agent_config_draft (
    draft_id text PRIMARY KEY,
    agent_key text NOT NULL REFERENCES agent_definition(agent_key),
    base_live_hash text NOT NULL CHECK(base_live_hash ~ '^[0-9a-f]{64}$'),
    base_document_json jsonb NOT NULL CHECK(jsonb_typeof(base_document_json)='object'),
    current_revision bigint NOT NULL DEFAULT 1 CHECK(current_revision>0),
    created_by text NOT NULL REFERENCES agent_admin_identity(principal_id),
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS agent_config_draft_revision (
    draft_id text NOT NULL REFERENCES agent_config_draft(draft_id),
    revision bigint NOT NULL CHECK(revision>0),
    document_json jsonb NOT NULL CHECK(jsonb_typeof(document_json)='object'),
    document_hash text NOT NULL CHECK(document_hash ~ '^[0-9a-f]{64}$'),
    dependency_snapshot_json jsonb NOT NULL,
    reason text NOT NULL CHECK(length(reason) BETWEEN 1 AND 2000),
    actor_id text NOT NULL REFERENCES agent_admin_identity(principal_id),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(draft_id,revision)
);
-- Repeatable when an earlier development revision has already been installed.
ALTER TABLE agent_config_draft_revision ADD COLUMN IF NOT EXISTS dependency_snapshot_json jsonb NOT NULL DEFAULT '{}'::jsonb;
CREATE TABLE IF NOT EXISTS agent_config_publication (
    publication_id text PRIMARY KEY,
    agent_key text NOT NULL REFERENCES agent_definition(agent_key),
    publication_no bigint NOT NULL CHECK(publication_no>0),
    draft_id text NOT NULL,
    revision bigint NOT NULL,
    base_live_hash text NOT NULL CHECK(base_live_hash ~ '^[0-9a-f]{64}$'),
    live_hash text NOT NULL CHECK(live_hash ~ '^[0-9a-f]{64}$'),
    document_json jsonb NOT NULL,
    dependency_snapshot_json jsonb NOT NULL,
    diff_json jsonb NOT NULL,
    actor_id text NOT NULL REFERENCES agent_admin_identity(principal_id),
    idempotency_key text NOT NULL CHECK(length(idempotency_key) BETWEEN 1 AND 160),
    reason text NOT NULL CHECK(length(reason) BETWEEN 1 AND 2000),
    published_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY(draft_id,revision) REFERENCES agent_config_draft_revision(draft_id,revision),
    UNIQUE(agent_key,publication_no),
    UNIQUE(draft_id,revision),
    UNIQUE(agent_key,idempotency_key)
);
CREATE INDEX IF NOT EXISTS agent_config_draft_agent_idx ON agent_config_draft(agent_key,created_at);
CREATE INDEX IF NOT EXISTS agent_config_publication_agent_idx
    ON agent_config_publication(agent_key,publication_no);

CREATE OR REPLACE FUNCTION agent_config_history_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'configuration history is append-only' USING ERRCODE='23514';
END;
$$;
DROP TRIGGER IF EXISTS agent_config_revision_immutable ON agent_config_draft_revision;
CREATE TRIGGER agent_config_revision_immutable BEFORE UPDATE OR DELETE ON agent_config_draft_revision
    FOR EACH ROW EXECUTE FUNCTION agent_config_history_immutable();
DROP TRIGGER IF EXISTS agent_config_revision_no_truncate ON agent_config_draft_revision;
CREATE TRIGGER agent_config_revision_no_truncate BEFORE TRUNCATE ON agent_config_draft_revision
    FOR EACH STATEMENT EXECUTE FUNCTION agent_config_history_immutable();
DROP TRIGGER IF EXISTS agent_config_publication_immutable ON agent_config_publication;
CREATE TRIGGER agent_config_publication_immutable BEFORE UPDATE OR DELETE ON agent_config_publication
    FOR EACH ROW EXECUTE FUNCTION agent_config_history_immutable();
DROP TRIGGER IF EXISTS agent_config_publication_no_truncate ON agent_config_publication;
CREATE TRIGGER agent_config_publication_no_truncate BEFORE TRUNCATE ON agent_config_publication
    FOR EACH STATEMENT EXECUTE FUNCTION agent_config_history_immutable();

CREATE OR REPLACE FUNCTION agent_config_draft_head_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP='DELETE' THEN
        RAISE EXCEPTION 'configuration draft history cannot be deleted' USING ERRCODE='23514';
    END IF;
    IF NEW.draft_id IS DISTINCT FROM OLD.draft_id OR NEW.agent_key IS DISTINCT FROM OLD.agent_key
       OR NEW.base_live_hash IS DISTINCT FROM OLD.base_live_hash
       OR NEW.base_document_json IS DISTINCT FROM OLD.base_document_json
       OR NEW.created_by IS DISTINCT FROM OLD.created_by OR NEW.created_at IS DISTINCT FROM OLD.created_at
       OR NEW.current_revision<>OLD.current_revision+1 THEN
        RAISE EXCEPTION 'draft base is immutable; revisions only advance by one' USING ERRCODE='23514';
    END IF;
    IF NOT EXISTS(SELECT 1 FROM agent_config_draft_revision r
                  WHERE r.draft_id=NEW.draft_id AND r.revision=NEW.current_revision) THEN
        RAISE EXCEPTION 'draft head requires its saved revision' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS agent_config_draft_head_guard ON agent_config_draft;
CREATE TRIGGER agent_config_draft_head_guard BEFORE UPDATE OR DELETE ON agent_config_draft
    FOR EACH ROW EXECUTE FUNCTION agent_config_draft_head_guard();
