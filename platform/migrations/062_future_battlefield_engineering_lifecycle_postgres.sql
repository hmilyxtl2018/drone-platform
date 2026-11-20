-- P1-B: append-only lifecycle admission between research and engineering.
-- A lifecycle row is a snapshot.  It is never updated in place, so the
-- current stage is always reconstructible from the highest revision.
CREATE TABLE IF NOT EXISTS future_battlefield_lifecycle (
    run_id text NOT NULL REFERENCES agent_run(run_id),
    revision integer NOT NULL CHECK (revision > 0),
    stage text NOT NULL CHECK (stage IN ('exploration','decision-support','engineering-input',
                                         'prototype','test','qualification','production-readiness')),
    source_run_id text NOT NULL REFERENCES agent_run(run_id),
    source_run_hash text NOT NULL CHECK (source_run_hash ~ '^[0-9a-f]{64}$'),
    state_json jsonb NOT NULL CHECK (jsonb_typeof(state_json)='object'),
    state_hash text NOT NULL CHECK (state_hash ~ '^[0-9a-f]{64}$'),
    created_by text NOT NULL REFERENCES agent_admin_identity(principal_id),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (run_id, revision),
    CHECK (run_id = source_run_id OR stage <> 'exploration')
);
CREATE INDEX IF NOT EXISTS future_battlefield_lifecycle_current_idx
    ON future_battlefield_lifecycle(run_id, revision DESC);

CREATE TABLE IF NOT EXISTS future_battlefield_lifecycle_transition (
    transition_id text PRIMARY KEY,
    run_id text NOT NULL REFERENCES agent_run(run_id),
    revision integer NOT NULL CHECK (revision > 1),
    from_stage text NOT NULL CHECK (from_stage IN ('exploration','decision-support','engineering-input',
                                                   'prototype','test','qualification','production-readiness')),
    to_stage text NOT NULL CHECK (to_stage IN ('exploration','decision-support','engineering-input',
                                               'prototype','test','qualification','production-readiness')),
    gate_json jsonb NOT NULL CHECK (jsonb_typeof(gate_json)='object'),
    gate_hash text NOT NULL CHECK (gate_hash ~ '^[0-9a-f]{64}$'),
    approval_id text NOT NULL REFERENCES agent_approval_request(approval_id),
    actor_id text NOT NULL REFERENCES agent_admin_identity(principal_id),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (run_id, revision),
    CHECK (from_stage <> to_stage)
);
CREATE INDEX IF NOT EXISTS future_battlefield_lifecycle_transition_idx
    ON future_battlefield_lifecycle_transition(run_id, revision);

DROP TRIGGER IF EXISTS future_battlefield_lifecycle_immutable ON future_battlefield_lifecycle;
CREATE TRIGGER future_battlefield_lifecycle_immutable
    BEFORE UPDATE OR DELETE ON future_battlefield_lifecycle
    FOR EACH ROW EXECUTE FUNCTION agent_engine_reject_mutation();
DROP TRIGGER IF EXISTS future_battlefield_lifecycle_no_truncate ON future_battlefield_lifecycle;
CREATE TRIGGER future_battlefield_lifecycle_no_truncate
    BEFORE TRUNCATE ON future_battlefield_lifecycle
    FOR EACH STATEMENT EXECUTE FUNCTION agent_engine_reject_mutation();
DROP TRIGGER IF EXISTS future_battlefield_lifecycle_transition_immutable ON future_battlefield_lifecycle_transition;
CREATE TRIGGER future_battlefield_lifecycle_transition_immutable
    BEFORE UPDATE OR DELETE ON future_battlefield_lifecycle_transition
    FOR EACH ROW EXECUTE FUNCTION agent_engine_reject_mutation();
DROP TRIGGER IF EXISTS future_battlefield_lifecycle_transition_no_truncate ON future_battlefield_lifecycle_transition;
CREATE TRIGGER future_battlefield_lifecycle_transition_no_truncate
    BEFORE TRUNCATE ON future_battlefield_lifecycle_transition
    FOR EACH STATEMENT EXECUTE FUNCTION agent_engine_reject_mutation();

CREATE OR REPLACE FUNCTION future_battlefield_lifecycle_integrity() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE parent_stage text;
DECLARE from_rank integer;
DECLARE to_rank integer;
BEGIN
    SELECT stage INTO parent_stage FROM future_battlefield_lifecycle
      WHERE run_id=NEW.run_id AND revision=NEW.revision-1;
    IF parent_stage IS NULL OR NEW.from_stage IS DISTINCT FROM parent_stage THEN
        RAISE EXCEPTION 'lifecycle transition does not follow current stage' USING ERRCODE='23514';
    END IF;
    IF NEW.revision <> 2 + COALESCE((SELECT count(*) FROM future_battlefield_lifecycle_transition WHERE run_id=NEW.run_id),0) THEN
        RAISE EXCEPTION 'lifecycle transition revision is not append-only' USING ERRCODE='23514';
    END IF;
    from_rank := array_position(ARRAY['exploration','decision-support','engineering-input','prototype','test','qualification','production-readiness'], NEW.from_stage);
    to_rank := array_position(ARRAY['exploration','decision-support','engineering-input','prototype','test','qualification','production-readiness'], NEW.to_stage);
    IF to_rank <> from_rank + 1 THEN
        RAISE EXCEPTION 'lifecycle transition must advance exactly one stage' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END; $$;
DROP TRIGGER IF EXISTS future_battlefield_lifecycle_integrity ON future_battlefield_lifecycle_transition;
CREATE TRIGGER future_battlefield_lifecycle_integrity
    BEFORE INSERT ON future_battlefield_lifecycle_transition
    FOR EACH ROW EXECUTE FUNCTION future_battlefield_lifecycle_integrity();

CREATE OR REPLACE FUNCTION future_battlefield_lifecycle_stage_integrity() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE previous text;
BEGIN
    IF NEW.revision = 1 THEN
        IF NEW.stage <> 'exploration' OR NEW.run_id <> NEW.source_run_id THEN
            RAISE EXCEPTION 'lifecycle must start at exploration' USING ERRCODE='23514';
        END IF;
    ELSE
        SELECT stage INTO previous FROM future_battlefield_lifecycle
          WHERE run_id=NEW.run_id AND revision=NEW.revision-1;
        IF previous IS NULL THEN
            RAISE EXCEPTION 'lifecycle revision has no predecessor' USING ERRCODE='23514';
        END IF;
        IF NOT EXISTS (SELECT 1 FROM future_battlefield_lifecycle_transition
                       WHERE run_id=NEW.run_id AND revision=NEW.revision AND to_stage=NEW.stage) THEN
            RAISE EXCEPTION 'lifecycle revision has no matching transition gate' USING ERRCODE='23514';
        END IF;
    END IF;
    RETURN NEW;
END; $$;
DROP TRIGGER IF EXISTS future_battlefield_lifecycle_stage_integrity ON future_battlefield_lifecycle;
CREATE TRIGGER future_battlefield_lifecycle_stage_integrity
    BEFORE INSERT ON future_battlefield_lifecycle
    FOR EACH ROW EXECUTE FUNCTION future_battlefield_lifecycle_stage_integrity();
