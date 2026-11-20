-- P4-05: proposed knowledge change sets and explicitly human-published versions.
-- This schema never writes the source ontology SQLite database. Every payload is
-- an immutable, provenance-bearing projection and truthVerified is always false.

CREATE TABLE IF NOT EXISTS agent_knowledge_change_set (
    change_set_id text PRIMARY KEY,
    stream_key text NOT NULL CHECK(stream_key ~ '^[A-Za-z0-9_.:/-]{1,240}$'),
    run_id text NOT NULL REFERENCES agent_run(run_id),
    proposed_by text NOT NULL REFERENCES agent_admin_identity(principal_id),
    idempotency_key text NOT NULL CHECK(length(idempotency_key) BETWEEN 1 AND 160),
    payload_json jsonb NOT NULL CHECK(jsonb_typeof(payload_json)='object'),
    payload_hash text NOT NULL CHECK(payload_hash ~ '^[0-9a-f]{64}$'),
    evidence_set_hash text NOT NULL CHECK(evidence_set_hash ~ '^[0-9a-f]{64}$'),
    base_version bigint NOT NULL CHECK(base_version >= 0),
    expires_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(proposed_by,idempotency_key)
);
CREATE INDEX IF NOT EXISTS agent_knowledge_change_set_stream_idx
    ON agent_knowledge_change_set(stream_key,created_at,change_set_id);

CREATE TABLE IF NOT EXISTS agent_knowledge_change_event (
    event_id text PRIMARY KEY,
    change_set_id text NOT NULL REFERENCES agent_knowledge_change_set(change_set_id),
    event_type text NOT NULL CHECK(event_type IN ('proposed','approved','rejected','expired')),
    actor_id text NOT NULL REFERENCES agent_admin_identity(principal_id),
    expected_hash text CHECK(expected_hash IS NULL OR expected_hash ~ '^[0-9a-f]{64}$'),
    reason text NOT NULL CHECK(length(reason) BETWEEN 1 AND 4000),
    idempotency_key text NOT NULL CHECK(length(idempotency_key) BETWEEN 1 AND 160),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(change_set_id,event_type,idempotency_key)
);
CREATE INDEX IF NOT EXISTS agent_knowledge_change_event_idx
    ON agent_knowledge_change_event(change_set_id,created_at,event_id);

CREATE TABLE IF NOT EXISTS agent_knowledge_publication (
    publication_id text PRIMARY KEY,
    stream_key text NOT NULL CHECK(stream_key ~ '^[A-Za-z0-9_.:/-]{1,240}$'),
    version bigint NOT NULL CHECK(version > 0),
    change_set_id text NOT NULL REFERENCES agent_knowledge_change_set(change_set_id),
    payload_json jsonb NOT NULL CHECK(jsonb_typeof(payload_json)='object'),
    payload_hash text NOT NULL CHECK(payload_hash ~ '^[0-9a-f]{64}$'),
    evidence_set_hash text NOT NULL CHECK(evidence_set_hash ~ '^[0-9a-f]{64}$'),
    published_by text NOT NULL REFERENCES agent_admin_identity(principal_id),
    published_at timestamptz NOT NULL DEFAULT now(),
    idempotency_key text NOT NULL CHECK(length(idempotency_key) BETWEEN 1 AND 160),
    UNIQUE(stream_key,version),
    UNIQUE(change_set_id),
    UNIQUE(stream_key,idempotency_key)
);
CREATE INDEX IF NOT EXISTS agent_knowledge_publication_stream_idx
    ON agent_knowledge_publication(stream_key,version);

CREATE TABLE IF NOT EXISTS agent_knowledge_publication_event (
    event_id text PRIMARY KEY,
    publication_id text NOT NULL REFERENCES agent_knowledge_publication(publication_id),
    event_type text NOT NULL CHECK(event_type IN ('published','withdrawn')),
    actor_id text NOT NULL REFERENCES agent_admin_identity(principal_id),
    reason text NOT NULL CHECK(length(reason) BETWEEN 1 AND 4000),
    idempotency_key text NOT NULL CHECK(length(idempotency_key) BETWEEN 1 AND 160),
    replaces_publication_id text REFERENCES agent_knowledge_publication(publication_id),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(publication_id,event_type,idempotency_key)
);
CREATE INDEX IF NOT EXISTS agent_knowledge_publication_event_idx
    ON agent_knowledge_publication_event(publication_id,created_at,event_id);

CREATE OR REPLACE FUNCTION agent_knowledge_reject_mutation() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'knowledge history is append-only' USING ERRCODE='55000';
END;
$$;
DO $$
DECLARE table_name text;
BEGIN
    FOREACH table_name IN ARRAY ARRAY['agent_knowledge_change_set','agent_knowledge_change_event',
                                      'agent_knowledge_publication','agent_knowledge_publication_event'] LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS %I_immutable ON %I',table_name,table_name);
        EXECUTE format('CREATE TRIGGER %I_immutable BEFORE UPDATE OR DELETE ON %I '
                       'FOR EACH ROW EXECUTE FUNCTION agent_knowledge_reject_mutation()',table_name,table_name);
        EXECUTE format('DROP TRIGGER IF EXISTS %I_truncate ON %I',table_name,table_name);
        EXECUTE format('CREATE TRIGGER %I_truncate BEFORE TRUNCATE ON %I '
                       'FOR EACH STATEMENT EXECUTE FUNCTION agent_knowledge_reject_mutation()',table_name,table_name);
    END LOOP;
END;
$$;
