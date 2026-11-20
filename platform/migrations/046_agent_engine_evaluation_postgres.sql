-- P4-09 deterministic and paid-model evaluation records.
-- Evaluation inputs, expected contracts, snapshots, results and usage links are
-- append-only.  A model run may be evaluated, but this schema never invokes a
-- provider and never infers usage that is absent from an audit row.

CREATE TABLE IF NOT EXISTS agent_evaluation_dataset (
    dataset_id text PRIMARY KEY,
    dataset_key text NOT NULL,
    dataset_version text NOT NULL,
    description text NOT NULL DEFAULT '',
    dataset_json jsonb NOT NULL CHECK (jsonb_typeof(dataset_json)='object'),
    dataset_hash text NOT NULL CHECK (dataset_hash ~ '^[0-9a-f]{64}$'),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (dataset_key, dataset_version)
);

CREATE TABLE IF NOT EXISTS agent_evaluation_case (
    case_id text PRIMARY KEY,
    dataset_id text NOT NULL REFERENCES agent_evaluation_dataset(dataset_id),
    case_key text NOT NULL,
    case_version text NOT NULL,
    case_type text NOT NULL CHECK (case_type IN (
        'sufficient_data','missing_data','conflicting_data','wrong_units','unauthorized_action',
        'sufficient','missing','conflicting','wrong-units','unauthorized')),
    input_json jsonb NOT NULL CHECK (jsonb_typeof(input_json)='object'),
    expected_json jsonb NOT NULL CHECK (jsonb_typeof(expected_json)='object'),
    input_hash text NOT NULL CHECK (input_hash ~ '^[0-9a-f]{64}$'),
    expected_hash text NOT NULL CHECK (expected_hash ~ '^[0-9a-f]{64}$'),
    case_hash text NOT NULL CHECK (case_hash ~ '^[0-9a-f]{64}$'),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (dataset_id, case_key, case_version)
);
CREATE INDEX IF NOT EXISTS agent_evaluation_case_dataset_idx
    ON agent_evaluation_case(dataset_id, case_key);

CREATE TABLE IF NOT EXISTS agent_evaluation_run (
    evaluation_run_id text PRIMARY KEY,
    dataset_id text NOT NULL REFERENCES agent_evaluation_dataset(dataset_id),
    dataset_version text NOT NULL,
    dataset_hash text NOT NULL CHECK (dataset_hash ~ '^[0-9a-f]{64}$'),
    case_set_hash text NOT NULL CHECK (case_set_hash ~ '^[0-9a-f]{64}$'),
    profile_agent_key text NOT NULL,
    profile_version text NOT NULL,
    profile_hash text NOT NULL CHECK (profile_hash ~ '^[0-9a-f]{64}$'),
    prompt_version text NOT NULL,
    prompt_hash text NOT NULL CHECK (prompt_hash ~ '^[0-9a-f]{64}$'),
    source_versions_json jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(source_versions_json)='object'),
    source_versions_hash text NOT NULL CHECK (source_versions_hash ~ '^[0-9a-f]{64}$'),
    execution_mode text NOT NULL CHECK (execution_mode IN ('deterministic','paid_model')),
    source_run_id text,
    provider_key text,
    model_name text,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS agent_evaluation_run_dataset_idx
    ON agent_evaluation_run(dataset_id, created_at);

CREATE TABLE IF NOT EXISTS agent_evaluation_run_case (
    evaluation_run_id text NOT NULL REFERENCES agent_evaluation_run(evaluation_run_id),
    case_id text NOT NULL REFERENCES agent_evaluation_case(case_id),
    case_hash text NOT NULL CHECK (case_hash ~ '^[0-9a-f]{64}$'),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (evaluation_run_id, case_id)
);
CREATE INDEX IF NOT EXISTS agent_evaluation_run_case_case_idx
    ON agent_evaluation_run_case(case_id);

CREATE OR REPLACE FUNCTION agent_evaluation_case_binding_integrity() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE run_dataset text; case_dataset text; frozen_hash text;
BEGIN
    SELECT dataset_id INTO run_dataset FROM agent_evaluation_run WHERE evaluation_run_id=NEW.evaluation_run_id;
    SELECT dataset_id,case_hash INTO case_dataset,frozen_hash FROM agent_evaluation_case WHERE case_id=NEW.case_id;
    IF run_dataset IS NULL OR case_dataset IS NULL OR run_dataset IS DISTINCT FROM case_dataset
       OR frozen_hash IS DISTINCT FROM NEW.case_hash THEN
        RAISE EXCEPTION 'evaluation case is foreign or its hash changed' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS agent_evaluation_case_binding_check ON agent_evaluation_run_case;
CREATE TRIGGER agent_evaluation_case_binding_check BEFORE INSERT ON agent_evaluation_run_case
FOR EACH ROW EXECUTE FUNCTION agent_evaluation_case_binding_integrity();

CREATE TABLE IF NOT EXISTS agent_evaluation_case_result (
    result_id text PRIMARY KEY,
    evaluation_run_id text NOT NULL REFERENCES agent_evaluation_run(evaluation_run_id),
    case_id text NOT NULL REFERENCES agent_evaluation_case(case_id),
    case_hash text NOT NULL CHECK (case_hash ~ '^[0-9a-f]{64}$'),
    actual_output_json jsonb NOT NULL CHECK (jsonb_typeof(actual_output_json)='object'),
    actual_output_hash text NOT NULL CHECK (actual_output_hash ~ '^[0-9a-f]{64}$'),
    actual_references_json jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(actual_references_json)='object'),
    deterministic_status text NOT NULL CHECK (deterministic_status IN ('passed','failed','indeterminate')),
    deterministic_correct boolean NOT NULL,
    citation_status text NOT NULL CHECK (citation_status IN ('passed','failed','not-required','indeterminate')),
    convergence_status text NOT NULL CHECK (convergence_status IN ('passed','failed','not-required','indeterminate')),
    authorization_status text NOT NULL CHECK (authorization_status IN ('passed','failed','not-applicable','indeterminate')),
    details_json jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(details_json)='object'),
    result_hash text NOT NULL CHECK (result_hash ~ '^[0-9a-f]{64}$'),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (evaluation_run_id, case_id),
    UNIQUE (evaluation_run_id, result_hash)
);
CREATE INDEX IF NOT EXISTS agent_evaluation_result_run_idx
    ON agent_evaluation_case_result(evaluation_run_id, created_at);

CREATE OR REPLACE FUNCTION agent_evaluation_result_integrity() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE frozen_hash text;
BEGIN
    SELECT case_hash INTO frozen_hash FROM agent_evaluation_run_case
        WHERE evaluation_run_id=NEW.evaluation_run_id AND case_id=NEW.case_id;
    IF frozen_hash IS NULL OR frozen_hash IS DISTINCT FROM NEW.case_hash THEN
        RAISE EXCEPTION 'evaluation result case is not part of the frozen run' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS agent_evaluation_result_integrity_check ON agent_evaluation_case_result;
CREATE TRIGGER agent_evaluation_result_integrity_check BEFORE INSERT ON agent_evaluation_case_result
FOR EACH ROW EXECUTE FUNCTION agent_evaluation_result_integrity();

CREATE TABLE IF NOT EXISTS agent_evaluation_metric (
    metric_id text PRIMARY KEY,
    evaluation_run_id text NOT NULL REFERENCES agent_evaluation_run(evaluation_run_id),
    result_id text REFERENCES agent_evaluation_case_result(result_id),
    metric_kind text NOT NULL CHECK (metric_kind IN ('correctness','citation','convergence','authorization','usage')),
    metric_key text NOT NULL,
    metric_value numeric,
    metric_status text NOT NULL CHECK (metric_status IN ('passed','failed','not-required','indeterminate','unknown')),
    details_json jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(details_json)='object'),
    metric_hash text NOT NULL CHECK (metric_hash ~ '^[0-9a-f]{64}$'),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (evaluation_run_id, result_id, metric_kind, metric_key)
);

CREATE TABLE IF NOT EXISTS agent_evaluation_usage (
    usage_id text PRIMARY KEY,
    evaluation_run_id text NOT NULL REFERENCES agent_evaluation_run(evaluation_run_id),
    result_id text REFERENCES agent_evaluation_case_result(result_id),
    source_type text NOT NULL CHECK (source_type IN ('model_call','tool_call','run')),
    source_id text NOT NULL,
    provider_key text,
    model_name text,
    input_tokens bigint CHECK (input_tokens IS NULL OR input_tokens >= 0),
    output_tokens bigint CHECK (output_tokens IS NULL OR output_tokens >= 0),
    latency_ms bigint CHECK (latency_ms IS NULL OR latency_ms >= 0),
    cost numeric CHECK (cost IS NULL OR cost >= 0),
    usage_json jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(usage_json)='object'),
    source_hash text NOT NULL CHECK (source_hash ~ '^[0-9a-f]{64}$'),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (evaluation_run_id, source_type, source_id)
);

CREATE OR REPLACE FUNCTION agent_evaluation_reject_mutation() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'evaluation records are append-only' USING ERRCODE='55000';
END;
$$;

DO $$
DECLARE table_name text;
DECLARE trigger_name text;
BEGIN
    FOREACH table_name IN ARRAY ARRAY[
        'agent_evaluation_dataset','agent_evaluation_case','agent_evaluation_run',
        'agent_evaluation_run_case','agent_evaluation_case_result','agent_evaluation_metric','agent_evaluation_usage'
    ] LOOP
        trigger_name := table_name || '_immutable';
        EXECUTE format('DROP TRIGGER IF EXISTS %I ON %I', trigger_name, table_name);
        EXECUTE format('CREATE TRIGGER %I BEFORE UPDATE OR DELETE ON %I FOR EACH ROW EXECUTE FUNCTION agent_evaluation_reject_mutation()', trigger_name, table_name);
    END LOOP;
END;
$$;
