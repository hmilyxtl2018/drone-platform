-- P1-D: one vocabulary for node retries/rounds and an append-only usage ledger.
ALTER TABLE agent_harness_step_budget
    ADD COLUMN IF NOT EXISTS max_iterations integer NOT NULL DEFAULT 1 CHECK (max_iterations >= 0),
    ADD COLUMN IF NOT EXISTS max_retries integer NOT NULL DEFAULT 0 CHECK (max_retries >= 0),
    ADD COLUMN IF NOT EXISTS estimated_cost_micros bigint NOT NULL DEFAULT 0 CHECK (estimated_cost_micros >= 0),
    ADD COLUMN IF NOT EXISTS max_evidence_bytes bigint NOT NULL DEFAULT 0 CHECK (max_evidence_bytes >= 0),
    ADD COLUMN IF NOT EXISTS max_concurrency integer NOT NULL DEFAULT 1 CHECK (max_concurrency > 0);

UPDATE agent_harness_step_budget
   SET max_concurrency=GREATEST(max_parallelism,1)
 WHERE max_concurrency IS NULL OR max_concurrency < 1;

CREATE TABLE IF NOT EXISTS agent_run_budget_ledger (
    ledger_id text PRIMARY KEY,
    root_run_id text NOT NULL REFERENCES agent_run_budget(root_run_id),
    run_id text NOT NULL REFERENCES agent_run(run_id),
    step_key text,
    operation_key text,
    kind text NOT NULL CHECK (kind IN ('tool','model','http','search','scrape')),
    status text NOT NULL CHECK (status IN ('succeeded','failed','timeout','cancelled','unknown','incomplete')),
    estimate_json jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(estimate_json)='object'),
    actual_json jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(actual_json)='object'),
    retry_count integer NOT NULL DEFAULT 0 CHECK (retry_count >= 0),
    concurrency_peak integer CHECK (concurrency_peak IS NULL OR concurrency_peak > 0),
    cancellation_reason text,
    incomplete boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS agent_run_budget_ledger_root_idx
    ON agent_run_budget_ledger(root_run_id, created_at, ledger_id);
CREATE INDEX IF NOT EXISTS agent_run_budget_ledger_run_idx
    ON agent_run_budget_ledger(run_id, created_at, ledger_id);
CREATE UNIQUE INDEX IF NOT EXISTS agent_run_budget_ledger_operation_idx
    ON agent_run_budget_ledger(run_id, operation_key, kind)
 WHERE operation_key IS NOT NULL;

DROP TRIGGER IF EXISTS agent_run_budget_ledger_immutable ON agent_run_budget_ledger;
CREATE TRIGGER agent_run_budget_ledger_immutable
    BEFORE UPDATE OR DELETE ON agent_run_budget_ledger
    FOR EACH ROW EXECUTE FUNCTION agent_engine_reject_mutation();
DROP TRIGGER IF EXISTS agent_run_budget_ledger_no_truncate ON agent_run_budget_ledger;
CREATE TRIGGER agent_run_budget_ledger_no_truncate
    BEFORE TRUNCATE ON agent_run_budget_ledger
    FOR EACH STATEMENT EXECUTE FUNCTION agent_engine_reject_mutation();

CREATE OR REPLACE FUNCTION agent_run_budget_ledger_root_integrity() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE expected_root text;
BEGIN
    SELECT root_run_id INTO expected_root FROM agent_run_budget_member WHERE run_id=NEW.run_id;
    IF expected_root IS NULL OR NEW.root_run_id IS DISTINCT FROM expected_root THEN
        RAISE EXCEPTION 'budget ledger root does not match Run membership' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END; $$;
DROP TRIGGER IF EXISTS agent_run_budget_ledger_root_integrity ON agent_run_budget_ledger;
CREATE TRIGGER agent_run_budget_ledger_root_integrity
    BEFORE INSERT ON agent_run_budget_ledger
    FOR EACH ROW EXECUTE FUNCTION agent_run_budget_ledger_root_integrity();
