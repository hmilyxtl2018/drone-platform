-- Explicit human-requested successor research; independent budget, no inherited approvals.
CREATE TABLE IF NOT EXISTS agent_run_followup (
    previous_run_id text NOT NULL REFERENCES agent_run(run_id),
    run_id text NOT NULL UNIQUE REFERENCES agent_run(run_id),
    idempotency_key text NOT NULL,
    principal_id text NOT NULL REFERENCES agent_admin_identity(principal_id),
    reason text NOT NULL,
    previous_artifacts_json jsonb NOT NULL,
    request_hash text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK(previous_run_id<>run_id),
    PRIMARY KEY(previous_run_id,idempotency_key)
);
DROP TRIGGER IF EXISTS agent_followup_immutable ON agent_run_followup;
CREATE TRIGGER agent_followup_immutable BEFORE UPDATE OR DELETE ON agent_run_followup
FOR EACH ROW EXECUTE FUNCTION agent_engine_reject_mutation();
