-- P1-C: structured engineering verification rows.  These rows are inputs to
-- review and test planning; they cannot represent a passed test or a released
-- manufacturing requirement.
CREATE TABLE IF NOT EXISTS future_battlefield_verification_requirement (
    requirement_id text PRIMARY KEY,
    run_id text NOT NULL REFERENCES agent_run(run_id),
    lifecycle_revision integer NOT NULL,
    dimension text NOT NULL,
    metric text NOT NULL,
    unit text NOT NULL,
    operating_conditions jsonb NOT NULL CHECK (jsonb_typeof(operating_conditions)='object'),
    input_basis jsonb NOT NULL CHECK (jsonb_typeof(input_basis)='object'),
    verification_method text NOT NULL,
    test_procedure text NOT NULL,
    acceptance_criteria text NOT NULL,
    configuration_version text NOT NULL,
    result_evidence jsonb NOT NULL DEFAULT '[]' CHECK (jsonb_typeof(result_evidence)='array'),
    review_status text NOT NULL CHECK (review_status IN ('proposed-human-review','pending','blocked','accepted-human')),
    row_json jsonb NOT NULL CHECK (jsonb_typeof(row_json)='object'),
    row_hash text NOT NULL CHECK (row_hash ~ '^[0-9a-f]{64}$'),
    created_by text NOT NULL REFERENCES agent_admin_identity(principal_id),
    created_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (run_id, lifecycle_revision)
      REFERENCES future_battlefield_lifecycle(run_id, revision),
    CHECK (review_status <> 'accepted-human' OR jsonb_typeof(result_evidence)='array')
);
CREATE INDEX IF NOT EXISTS future_battlefield_verification_run_idx
    ON future_battlefield_verification_requirement(run_id, lifecycle_revision, requirement_id);

DROP TRIGGER IF EXISTS future_battlefield_verification_immutable ON future_battlefield_verification_requirement;
CREATE TRIGGER future_battlefield_verification_immutable
    BEFORE UPDATE OR DELETE ON future_battlefield_verification_requirement
    FOR EACH ROW EXECUTE FUNCTION agent_engine_reject_mutation();
DROP TRIGGER IF EXISTS future_battlefield_verification_no_truncate ON future_battlefield_verification_requirement;
CREATE TRIGGER future_battlefield_verification_no_truncate
    BEFORE TRUNCATE ON future_battlefield_verification_requirement
    FOR EACH STATEMENT EXECUTE FUNCTION agent_engine_reject_mutation();
