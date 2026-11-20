-- Persistent single-PostgreSQL worker queue; apply after migration 032.
-- No existing Run or queue entry is reset. May run inside an outer transaction.
CREATE TABLE IF NOT EXISTS agent_worker_pool (
    pool_key text PRIMARY KEY,
    max_concurrency integer NOT NULL CHECK (max_concurrency BETWEEN 1 AND 128),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS agent_run_queue (
    run_id text PRIMARY KEY REFERENCES agent_run(run_id),
    pool_key text NOT NULL REFERENCES agent_worker_pool(pool_key),
    status text NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued','leased','paused','done','failed','cancelled')),
    priority integer NOT NULL DEFAULT 0,
    generation bigint NOT NULL DEFAULT 0 CHECK (generation >= 0),
    owner_id text,
    lease_until timestamptz,
    heartbeat_at timestamptz,
    available_at timestamptz NOT NULL DEFAULT now(),
    attempt integer NOT NULL DEFAULT 0 CHECK (attempt >= 0),
    last_error_code text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK ((status='leased' AND owner_id IS NOT NULL AND lease_until IS NOT NULL)
        OR (status<>'leased' AND owner_id IS NULL AND lease_until IS NULL))
);
CREATE INDEX IF NOT EXISTS agent_queue_claim_idx
    ON agent_run_queue(pool_key,status,available_at,priority DESC,created_at);
CREATE INDEX IF NOT EXISTS agent_queue_lease_idx ON agent_run_queue(pool_key,lease_until)
    WHERE status='leased';

-- Guard all queue-managed runtime writes, not just queue ownership. Queue
-- ownership alone cannot fence an old process that still has an HTTP request
-- or an execution callback in flight. The connection wrapper in worker.py sets
-- LOCAL context on every short transaction, inherited through contextvars.
--
-- Credentials/DB roles remain a service boundary: a DB administrator can issue
-- session settings directly. The operator context is only for trusted API code,
-- never a model-supplied value or browser-provided reviewer identity.
CREATE OR REPLACE FUNCTION agent_worker_fence_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    target_run text;
    context_run text := nullif(current_setting('agent_engine.worker_run_id',true),'');
    context_owner text := nullif(current_setting('agent_engine.worker_owner',true),'');
    context_generation text := nullif(current_setting('agent_engine.worker_generation',true),'');
    control_run text := nullif(current_setting('agent_engine.control_run_id',true),'');
    queue_row agent_run_queue%ROWTYPE;
BEGIN
    IF TG_TABLE_NAME='agent_run_budget' THEN
        -- Descendant calls update a shared root budget while retaining their
        -- own lease. Do not accidentally require the root's worker token.
        target_run := NEW.root_run_id;
        IF context_run IS NOT NULL AND EXISTS (
            SELECT 1 FROM agent_run_budget_member
             WHERE run_id=context_run AND root_run_id=target_run)
        THEN target_run := context_run; END IF;
    ELSIF TG_TABLE_NAME='agent_budget_settlement' THEN
        SELECT run_id INTO target_run FROM agent_budget_reservation
         WHERE reservation_id=NEW.reservation_id;
    ELSE
        target_run := NEW.run_id;
    END IF;

    -- Lock against revocation/reclaim until this write transaction commits.
    -- Consequently a newly claimed generation can never be overwritten by an
    -- earlier transaction that passed validation before the reclaim.
    SELECT * INTO queue_row FROM agent_run_queue WHERE run_id=target_run FOR SHARE;
    IF NOT FOUND THEN RETURN NEW; END IF;
    IF control_run=target_run THEN RETURN NEW; END IF;
    IF context_run IS DISTINCT FROM target_run
       OR context_owner IS DISTINCT FROM queue_row.owner_id
       OR context_generation IS DISTINCT FROM queue_row.generation::text
       OR queue_row.status<>'leased'
       OR queue_row.lease_until<=clock_timestamp()
    THEN
        RAISE EXCEPTION 'worker lease lost or missing for managed run %', target_run
            USING ERRCODE='55000';
    END IF;
    RETURN NEW;
END;
$$;

-- These are all existing Run-owned write surfaces as of phase 3 foundation.
-- New candidate/review/handoff tables must attach this same guard when added.
DO $$
DECLARE table_name text;
BEGIN
    FOREACH table_name IN ARRAY ARRAY[
        'agent_run','agent_run_state','agent_step_run','agent_tool_call',
        'agent_loop_run','agent_approval_request','agent_run_artifact',
        'agent_run_event','agent_run_profile_snapshot','agent_model_call',
        'agent_run_plan','agent_budget_reservation','agent_budget_settlement',
        'agent_run_budget','agent_run_budget_member']
    LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS agent_worker_fence ON %I',table_name);
        EXECUTE format('CREATE TRIGGER agent_worker_fence BEFORE INSERT OR UPDATE ON %I '
                       'FOR EACH ROW EXECUTE FUNCTION agent_worker_fence_guard()',table_name);
    END LOOP;
END;
$$;
