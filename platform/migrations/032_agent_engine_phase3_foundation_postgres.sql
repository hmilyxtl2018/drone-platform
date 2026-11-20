-- Phase 3 foundation. Apply after 031, to ontology_agent_admin only.
-- Incremental and repeatable: no existing profile, source, or run is reset.
-- A caller may wrap this file in its own transaction.

CREATE OR REPLACE FUNCTION agent_phase3_valid_counters(value jsonb) RETURNS boolean
LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE item record;
BEGIN
    IF jsonb_typeof(value) <> 'object' THEN RETURN false; END IF;
    FOR item IN SELECT * FROM jsonb_each(value) LOOP
        IF item.key NOT IN ('model_calls','input_tokens','output_tokens','tool_calls',
                            'http_calls','search_calls','scrape_calls')
           OR jsonb_typeof(item.value) <> 'number'
           OR item.value::text !~ '^[0-9]+$'
           OR (item.value::text)::numeric > 9223372036854775807
        THEN RETURN false; END IF;
    END LOOP;
    RETURN true;
END;
$$;

-- Registries contain references, never embedded credentials, including nested
-- configuration. Runtime code receives no registry-writing API.
CREATE OR REPLACE FUNCTION agent_phase3_no_secrets(value jsonb) RETURNS boolean
LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE item record; child jsonb;
BEGIN
    IF jsonb_typeof(value) = 'object' THEN
        FOR item IN SELECT * FROM jsonb_each(value) LOOP
            IF regexp_replace(lower(item.key),'[^a-z0-9]','','g') IN
               ('apikey','authorization','password','passwd','secret','accesstoken',
                'bearertoken','refreshtoken','clientsecret','privatekey')
               OR NOT agent_phase3_no_secrets(item.value)
            THEN RETURN false; END IF;
        END LOOP;
    ELSIF jsonb_typeof(value) = 'array' THEN
        FOR child IN SELECT * FROM jsonb_array_elements(value) LOOP
            IF NOT agent_phase3_no_secrets(child) THEN RETURN false; END IF;
        END LOOP;
    END IF;
    RETURN true;
END;
$$;

CREATE TABLE IF NOT EXISTS provider_registry (
    provider_key text PRIMARY KEY,
    provider_type text NOT NULL CHECK (provider_type IN ('model','web','http')),
    base_url text NOT NULL CHECK (base_url LIKE 'https://%' AND position('@' in base_url)=0
                                 AND position('?' in base_url)=0 AND position('#' in base_url)=0),
    credential_ref text CHECK (credential_ref ~ '^(env|keychain|secret):[A-Za-z0-9_./:-]+$'),
    enabled boolean NOT NULL DEFAULT false,
    version text NOT NULL DEFAULT '1',
    config_json jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(config_json)='object' AND agent_phase3_no_secrets(config_json)),
    diagnostic_json jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(diagnostic_json)='object' AND agent_phase3_no_secrets(diagnostic_json)),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS data_source_registry (
    source_key text PRIMARY KEY,
    connector text NOT NULL,
    root_uri text NOT NULL CHECK (root_uri !~ '^[a-zA-Z][a-zA-Z0-9+.-]*://[^/]*@'
                                 AND position('?' in root_uri)=0 AND position('#' in root_uri)=0),
    credential_ref text CHECK (credential_ref ~ '^(env|keychain|secret):[A-Za-z0-9_./:-]+$'),
    read_only boolean NOT NULL DEFAULT true,
    enabled boolean NOT NULL DEFAULT false,
    policy_version text NOT NULL DEFAULT '1',
    config_json jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(config_json)='object' AND agent_phase3_no_secrets(config_json)),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

-- One account per budget root; descendants reference it rather than copying it.
-- used_json includes in-flight and uncertain consumption. Actual usage above a
-- reservation is retained as overrun and prevents subsequent reservations.
CREATE TABLE IF NOT EXISTS agent_run_budget (
    root_run_id text PRIMARY KEY REFERENCES agent_run(run_id),
    limits_json jsonb NOT NULL CHECK (agent_phase3_valid_counters(limits_json)),
    used_json jsonb NOT NULL DEFAULT '{}' CHECK (agent_phase3_valid_counters(used_json)),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS agent_run_budget_member (
    run_id text PRIMARY KEY REFERENCES agent_run(run_id),
    root_run_id text NOT NULL REFERENCES agent_run_budget(root_run_id),
    parent_run_id text REFERENCES agent_run(run_id),
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (parent_run_id IS NULL OR parent_run_id <> run_id)
);
CREATE INDEX IF NOT EXISTS agent_budget_member_root_idx ON agent_run_budget_member(root_run_id);
CREATE TABLE IF NOT EXISTS agent_budget_reservation (
    reservation_id text PRIMARY KEY,
    root_run_id text NOT NULL REFERENCES agent_run_budget(root_run_id),
    run_id text NOT NULL REFERENCES agent_run_budget_member(run_id),
    operation_key text,
    charges_json jsonb NOT NULL CHECK (agent_phase3_valid_counters(charges_json)),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (root_run_id, operation_key)
);
CREATE INDEX IF NOT EXISTS agent_budget_reservation_run_idx ON agent_budget_reservation(run_id);
CREATE TABLE IF NOT EXISTS agent_budget_settlement (
    settlement_id text PRIMARY KEY,
    reservation_id text NOT NULL REFERENCES agent_budget_reservation(reservation_id),
    revision integer NOT NULL CHECK (revision > 0),
    known_json jsonb NOT NULL CHECK (agent_phase3_valid_counters(known_json)),
    effective_json jsonb NOT NULL CHECK (agent_phase3_valid_counters(effective_json)),
    overrun_json jsonb NOT NULL DEFAULT '{}' CHECK (agent_phase3_valid_counters(overrun_json)),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (reservation_id, revision)
);

CREATE TABLE IF NOT EXISTS agent_model_call (
    call_id text PRIMARY KEY,
    run_id text NOT NULL REFERENCES agent_run(run_id),
    step_run_id text REFERENCES agent_step_run(step_run_id),
    root_run_id text REFERENCES agent_run_budget(root_run_id),
    reservation_id text REFERENCES agent_budget_reservation(reservation_id),
    provider_key text NOT NULL REFERENCES provider_registry(provider_key),
    requested_model text NOT NULL,
    actual_model text,
    status text NOT NULL,
    input_tokens bigint CHECK (input_tokens >= 0),
    output_tokens bigint CHECK (output_tokens >= 0),
    latency_ms bigint NOT NULL DEFAULT 0 CHECK (latency_ms >= 0),
    retry_count integer NOT NULL DEFAULT 0 CHECK (retry_count >= 0),
    http_status integer CHECK (http_status BETWEEN 100 AND 599),
    finish_reason text,
    error_code text,
    input_artifact_id text REFERENCES agent_run_artifact(artifact_id),
    output_artifact_id text REFERENCES agent_run_artifact(artifact_id),
    stage text,
    attempt_id text,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (run_id, attempt_id)
);
CREATE INDEX IF NOT EXISTS agent_model_call_run_idx ON agent_model_call(run_id,created_at);
CREATE TABLE IF NOT EXISTS agent_run_plan (
    plan_id text PRIMARY KEY,
    run_id text NOT NULL REFERENCES agent_run(run_id),
    plan_version integer NOT NULL CHECK (plan_version > 0),
    plan_json jsonb NOT NULL,
    plan_hash text NOT NULL,
    reason text,
    validation_json jsonb NOT NULL DEFAULT '{}',
    supersedes_plan_id text REFERENCES agent_run_plan(plan_id),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (run_id, plan_version)
);

CREATE OR REPLACE FUNCTION agent_phase3_budget_limits_immutable() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.root_run_id <> OLD.root_run_id OR NEW.limits_json <> OLD.limits_json THEN
        RAISE EXCEPTION 'budget root and limits are immutable' USING ERRCODE='55000';
    END IF;
    RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS agent_budget_limits_immutable ON agent_run_budget;
CREATE TRIGGER agent_budget_limits_immutable BEFORE UPDATE ON agent_run_budget
FOR EACH ROW EXECUTE FUNCTION agent_phase3_budget_limits_immutable();

-- Retain the same audit immutability contract as migration 031.
DROP TRIGGER IF EXISTS agent_budget_member_immutable ON agent_run_budget_member;
CREATE TRIGGER agent_budget_member_immutable BEFORE UPDATE OR DELETE ON agent_run_budget_member
FOR EACH ROW EXECUTE FUNCTION agent_engine_reject_mutation();
DROP TRIGGER IF EXISTS agent_budget_reservation_immutable ON agent_budget_reservation;
CREATE TRIGGER agent_budget_reservation_immutable BEFORE UPDATE OR DELETE ON agent_budget_reservation
FOR EACH ROW EXECUTE FUNCTION agent_engine_reject_mutation();
DROP TRIGGER IF EXISTS agent_budget_settlement_immutable ON agent_budget_settlement;
CREATE TRIGGER agent_budget_settlement_immutable BEFORE UPDATE OR DELETE ON agent_budget_settlement
FOR EACH ROW EXECUTE FUNCTION agent_engine_reject_mutation();
DROP TRIGGER IF EXISTS agent_model_call_immutable ON agent_model_call;
CREATE TRIGGER agent_model_call_immutable BEFORE UPDATE OR DELETE ON agent_model_call
FOR EACH ROW EXECUTE FUNCTION agent_engine_reject_mutation();
DROP TRIGGER IF EXISTS agent_run_plan_immutable ON agent_run_plan;
CREATE TRIGGER agent_run_plan_immutable BEFORE UPDATE OR DELETE ON agent_run_plan
FOR EACH ROW EXECUTE FUNCTION agent_engine_reject_mutation();
