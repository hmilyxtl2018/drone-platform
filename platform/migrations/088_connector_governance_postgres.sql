-- Unified connector governance. Registries and the existing budget ledger stay
-- authoritative; these rows only add execution metadata, scheduling and links.
-- Historical tool versions and Run Profile snapshots are never rewritten.
ALTER TABLE tool_version
    ADD COLUMN IF NOT EXISTS execution_transport text,
    ADD COLUMN IF NOT EXISTS adapter_version text;
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='tool_version_transport_check'
                   AND connamespace=current_schema()::regnamespace) THEN
        ALTER TABLE tool_version ADD CONSTRAINT tool_version_transport_check
            CHECK (execution_transport IS NULL OR execution_transport IN ('python','http','https'));
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS connector_policy (
    connector_ref text PRIMARY KEY CHECK (connector_ref ~ '^(provider|source|local|http):[A-Za-z0-9_.:-]+$'),
    policy_version integer NOT NULL DEFAULT 1 CHECK (policy_version > 0),
    max_concurrency integer NOT NULL DEFAULT 4 CHECK (max_concurrency BETWEEN 1 AND 128),
    requests_per_minute integer NOT NULL DEFAULT 60 CHECK (requests_per_minute BETWEEN 1 AND 100000),
    lease_seconds integer NOT NULL DEFAULT 300 CHECK (lease_seconds BETWEEN 5 AND 3600),
    failure_threshold integer NOT NULL DEFAULT 5 CHECK (failure_threshold BETWEEN 1 AND 100),
    cooldown_seconds integer NOT NULL DEFAULT 60 CHECK (cooldown_seconds BETWEEN 1 AND 3600),
    enabled boolean NOT NULL DEFAULT true,
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_by text
);
CREATE TABLE IF NOT EXISTS connector_policy_revision (
    connector_ref text NOT NULL,
    policy_version integer NOT NULL CHECK (policy_version > 0),
    policy_json jsonb NOT NULL CHECK (jsonb_typeof(policy_json)='object'),
    actor_id text NOT NULL,
    reason text NOT NULL DEFAULT '',
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (connector_ref,policy_version)
);
ALTER TABLE connector_policy_revision ADD COLUMN IF NOT EXISTS reason text NOT NULL DEFAULT '';
CREATE TABLE IF NOT EXISTS agent_run_connector_snapshot (
    run_id text PRIMARY KEY REFERENCES agent_run(run_id),
    manifest_json jsonb NOT NULL CHECK (jsonb_typeof(manifest_json)='object'),
    manifest_hash text NOT NULL CHECK (manifest_hash ~ '^[0-9a-f]{64}$'),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE IF NOT EXISTS connector_lease (
    lease_id text PRIMARY KEY,
    connector_ref text NOT NULL,
    connector_version text NOT NULL,
    run_id text NOT NULL REFERENCES agent_run(run_id),
    operation_key text NOT NULL,
    attempt_key text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    expires_at timestamptz NOT NULL,
    released_at timestamptz,
    release_reason text,
    CHECK (expires_at > created_at),
    UNIQUE (run_id,operation_key,attempt_key)
);
CREATE INDEX IF NOT EXISTS connector_lease_schedule_idx
    ON connector_lease(connector_ref,created_at DESC);
CREATE INDEX IF NOT EXISTS connector_lease_active_idx
    ON connector_lease(connector_ref,expires_at) WHERE released_at IS NULL;
CREATE TABLE IF NOT EXISTS connector_health_observation (
    observation_id text PRIMARY KEY,
    connector_ref text NOT NULL,
    connector_version text NOT NULL,
    binding_hash text NOT NULL CHECK (binding_hash ~ '^[0-9a-f]{64}$'),
    run_id text REFERENCES agent_run(run_id),
    step_run_id text,
    operation_key text NOT NULL,
    attempt_key text NOT NULL,
    status text NOT NULL CHECK (status IN ('success','succeeded','failed','timeout','unknown','rejected')),
    error_code text,
    error_class text,
    latency_ms bigint CHECK (latency_ms >= 0),
    observed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (run_id,operation_key,attempt_key)
);
-- Earlier drafts may already contain observations without a binding hash.
-- Keep their provenance unknown rather than inventing a hash from live config.
-- New calls always supply their frozen binding hash; old null hashes cannot
-- participate in the per-binding circuit query.
ALTER TABLE connector_health_observation
    ADD COLUMN IF NOT EXISTS binding_hash text CHECK (binding_hash ~ '^[0-9a-f]{64}$'),
    ADD COLUMN IF NOT EXISTS step_run_id text,
    ADD COLUMN IF NOT EXISTS error_class text;
CREATE INDEX IF NOT EXISTS connector_health_ref_idx
    ON connector_health_observation(connector_ref,connector_version,observed_at DESC);
CREATE INDEX IF NOT EXISTS connector_health_binding_idx
    ON connector_health_observation(connector_ref,connector_version,binding_hash,observed_at DESC);
CREATE TABLE IF NOT EXISTS connector_call_link (
    link_id text PRIMARY KEY,
    connector_ref text NOT NULL,
    connector_version text NOT NULL,
    run_id text NOT NULL REFERENCES agent_run(run_id),
    step_run_id text,
    operation_key text NOT NULL,
    attempt_key text NOT NULL,
    call_kind text NOT NULL CHECK (call_kind IN ('model','tool')),
    call_id text NOT NULL,
    cache_reused boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (call_kind,call_id)
);
ALTER TABLE connector_call_link ADD COLUMN IF NOT EXISTS cache_reused boolean NOT NULL DEFAULT false;
CREATE INDEX IF NOT EXISTS connector_call_run_idx ON connector_call_link(run_id,step_run_id);

CREATE OR REPLACE FUNCTION connector_history_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'connector history is append-only' USING ERRCODE='55000'; END $$;
DO $$ DECLARE item text; BEGIN
    FOREACH item IN ARRAY ARRAY['connector_policy_revision','agent_run_connector_snapshot',
                                'connector_health_observation','connector_call_link'] LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS connector_history_immutable ON %I',item);
        EXECUTE format('CREATE TRIGGER connector_history_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON %I '
                       'FOR EACH STATEMENT EXECUTE FUNCTION connector_history_immutable()',item);
    END LOOP;
END $$;

-- Keep worker fencing on every new Run-owned write, including observations.
-- Disposable/legacy databases without the queue guard retain compatibility.
DO $$ DECLARE item text; BEGIN
    IF to_regprocedure(current_schema() || '.agent_worker_fence_guard()') IS NOT NULL THEN
        FOREACH item IN ARRAY ARRAY['agent_run_connector_snapshot','connector_lease',
                                    'connector_health_observation','connector_call_link'] LOOP
            EXECUTE format('DROP TRIGGER IF EXISTS connector_worker_fence ON %I',item);
            EXECUTE format('CREATE TRIGGER connector_worker_fence BEFORE INSERT OR UPDATE ON %I '
                           'FOR EACH ROW EXECUTE FUNCTION agent_worker_fence_guard()',item);
        END LOOP;
    END IF;
END $$;
DROP TRIGGER IF EXISTS connector_lease_no_delete ON connector_lease;
CREATE TRIGGER connector_lease_no_delete BEFORE DELETE OR TRUNCATE ON connector_lease
    FOR EACH STATEMENT EXECUTE FUNCTION connector_history_immutable();
