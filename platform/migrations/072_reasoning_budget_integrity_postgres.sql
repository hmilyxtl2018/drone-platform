-- Stage attribution and actual usage are append-only additions. No historical
-- reservation, usage, budget or snapshot is rewritten by this migration.
ALTER TABLE agent_budget_reservation ADD COLUMN IF NOT EXISTS step_key text;
ALTER TABLE agent_reasoning_stage_usage ADD COLUMN IF NOT EXISTS billed_run_id text REFERENCES agent_run(run_id);

CREATE TABLE IF NOT EXISTS agent_reasoning_budget_binding (
    reservation_id text NOT NULL REFERENCES agent_budget_reservation(reservation_id),
    run_id text NOT NULL REFERENCES agent_run(run_id),
    owner_run_id text NOT NULL REFERENCES agent_run_reasoning_snapshot(run_id),
    stage_key text NOT NULL,
    iteration integer NOT NULL CHECK(iteration>0),
    usage_id text NOT NULL UNIQUE REFERENCES agent_reasoning_stage_usage(usage_id),
    PRIMARY KEY(reservation_id,owner_run_id)
);
CREATE TABLE IF NOT EXISTS agent_reasoning_stage_settlement (
    settlement_id text PRIMARY KEY,
    usage_id text NOT NULL REFERENCES agent_reasoning_stage_usage(usage_id),
    reservation_id text NOT NULL REFERENCES agent_budget_reservation(reservation_id),
    run_id text NOT NULL REFERENCES agent_run(run_id),
    owner_run_id text NOT NULL REFERENCES agent_run_reasoning_snapshot(run_id),
    revision integer NOT NULL CHECK(revision>0),
    effective_json jsonb NOT NULL CHECK(agent_phase3_valid_counters(effective_json)),
    overrun_json jsonb NOT NULL DEFAULT '{}' CHECK(agent_phase3_valid_counters(overrun_json)),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(usage_id,revision)
);

-- A descendant can charge its ancestor's stage under its own valid lease.
-- Membership is canonical; a caller-supplied parentRunId is never sufficient.
CREATE OR REPLACE FUNCTION agent_reasoning_usage_fence() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE target_run text := coalesce(NEW.billed_run_id,NEW.run_id);
    context_run text := nullif(current_setting('agent_engine.worker_run_id',true),'');
    context_owner text := nullif(current_setting('agent_engine.worker_owner',true),'');
    context_generation text := nullif(current_setting('agent_engine.worker_generation',true),'');
    control_run text := nullif(current_setting('agent_engine.control_run_id',true),'');
    queue_row agent_run_queue%ROWTYPE;
BEGIN
    IF target_run<>NEW.run_id AND NOT EXISTS(
        WITH RECURSIVE ancestors AS (
            SELECT run_id,parent_run_id,ARRAY[run_id] path FROM agent_run_budget_member WHERE run_id=target_run
            UNION ALL
            SELECT m.run_id,m.parent_run_id,a.path||m.run_id FROM ancestors a
            JOIN agent_run_budget_member m ON m.run_id=a.parent_run_id
            WHERE NOT m.run_id=ANY(a.path)
        ) SELECT 1 FROM ancestors WHERE run_id=NEW.run_id
    ) THEN RAISE EXCEPTION 'reasoning usage owner is not an ancestor' USING ERRCODE='23514'; END IF;
    SELECT * INTO queue_row FROM agent_run_queue WHERE run_id=target_run FOR SHARE;
    IF NOT FOUND THEN RETURN NEW; END IF;
    IF control_run=target_run THEN RETURN NEW; END IF;
    IF context_run IS DISTINCT FROM target_run OR context_owner IS DISTINCT FROM queue_row.owner_id
       OR context_generation IS DISTINCT FROM queue_row.generation::text OR queue_row.status<>'leased'
       OR queue_row.lease_until<=clock_timestamp() THEN
        RAISE EXCEPTION 'worker lease lost or missing for managed run %',target_run USING ERRCODE='55000';
    END IF;
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS reasoning_worker_fence ON agent_reasoning_stage_usage;
CREATE TRIGGER reasoning_worker_fence BEFORE INSERT ON agent_reasoning_stage_usage
    FOR EACH ROW EXECUTE FUNCTION agent_reasoning_usage_fence();

CREATE OR REPLACE FUNCTION agent_reasoning_budget_integrity() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_TABLE_NAME='agent_reasoning_budget_binding' THEN
        IF NOT EXISTS(SELECT 1 FROM agent_budget_reservation r JOIN agent_reasoning_stage_usage u ON u.usage_id=NEW.usage_id
            WHERE r.reservation_id=NEW.reservation_id AND r.run_id=NEW.run_id
              AND u.run_id=NEW.owner_run_id AND u.stage_key=NEW.stage_key AND u.iteration=NEW.iteration
              AND coalesce(u.billed_run_id,u.run_id)=NEW.run_id AND r.charges_json=u.charges_json)
        THEN RAISE EXCEPTION 'reasoning budget binding mismatch' USING ERRCODE='23514'; END IF;
    ELSIF NOT EXISTS(SELECT 1 FROM agent_reasoning_budget_binding b JOIN agent_budget_settlement s
        ON s.reservation_id=b.reservation_id AND s.revision=NEW.revision
        WHERE b.usage_id=NEW.usage_id AND b.reservation_id=NEW.reservation_id AND b.run_id=NEW.run_id
          AND b.owner_run_id=NEW.owner_run_id AND s.effective_json=NEW.effective_json)
    THEN RAISE EXCEPTION 'reasoning stage settlement mismatch' USING ERRCODE='23514'; END IF;
    RETURN NEW;
END $$;
DO $$ DECLARE target_table text; BEGIN
    FOREACH target_table IN ARRAY ARRAY['agent_reasoning_budget_binding','agent_reasoning_stage_settlement'] LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS reasoning_immutable ON %I',target_table);
        EXECUTE format('CREATE TRIGGER reasoning_immutable BEFORE UPDATE OR DELETE ON %I FOR EACH ROW EXECUTE FUNCTION agent_engine_reject_mutation()',target_table);
        EXECUTE format('DROP TRIGGER IF EXISTS reasoning_worker_fence ON %I',target_table);
        EXECUTE format('CREATE TRIGGER reasoning_worker_fence BEFORE INSERT ON %I FOR EACH ROW EXECUTE FUNCTION agent_worker_fence_guard()',target_table);
        EXECUTE format('DROP TRIGGER IF EXISTS reasoning_budget_integrity ON %I',target_table);
        EXECUTE format('CREATE TRIGGER reasoning_budget_integrity BEFORE INSERT ON %I FOR EACH ROW EXECUTE FUNCTION agent_reasoning_budget_integrity()',target_table);
    END LOOP;
END $$;
