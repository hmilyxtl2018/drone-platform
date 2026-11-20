-- P4-10 local operational state. No provider, credential or source body is stored.
-- Worker status is mutable liveness metadata; alerts are immutable observations.
CREATE TABLE IF NOT EXISTS agent_worker_status (
    worker_id text NOT NULL CHECK (worker_id ~ '^[A-Za-z0-9_.:-]{1,160}$'),
    pool_key text NOT NULL CHECK (pool_key ~ '^[A-Za-z0-9_.:-]{1,120}$'),
    status text NOT NULL CHECK (status IN ('running','stopped')),
    active_runs integer NOT NULL DEFAULT 0 CHECK (active_runs BETWEEN 0 AND 128),
    started_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    heartbeat_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    stopped_at timestamptz,
    PRIMARY KEY(pool_key,worker_id),
    CHECK ((status='running' AND stopped_at IS NULL)
        OR (status='stopped' AND stopped_at IS NOT NULL AND active_runs=0))
);
CREATE INDEX IF NOT EXISTS agent_worker_status_fresh_idx
    ON agent_worker_status(pool_key,status,heartbeat_at);

CREATE TABLE IF NOT EXISTS agent_operations_alert (
    alert_id text PRIMARY KEY,
    alert_kind text NOT NULL CHECK (alert_kind IN (
        'worker-stale','worker-unavailable','queue-failed','lease-expired',
        'budget-exhausted','budget-overrun')),
    severity text NOT NULL CHECK (severity IN ('warning','error')),
    subject_key text NOT NULL,
    reason_code text NOT NULL CHECK (reason_code IN (
        'worker-heartbeat-expired','no-fresh-worker','queue-execution-failed',
        'lease-expired-before-finish','shared-budget-exhausted','actual-usage-over-budget')),
    details_json jsonb NOT NULL CHECK (jsonb_typeof(details_json)='object'
        AND details_json - ARRAY['poolKey','workerId','runId','generation','attempt',
            'lastHeartbeatAt','leaseUntil','resource','used','limit','errorFingerprint',
            'workerStateHash']::text[] = '{}'::jsonb),
    fingerprint text NOT NULL CHECK (fingerprint ~ '^[0-9a-f]{64}$'),
    deduplication_key text NOT NULL UNIQUE CHECK (deduplication_key ~ '^[0-9a-f]{64}$'),
    observed_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX IF NOT EXISTS agent_operations_alert_observed_idx
    ON agent_operations_alert(observed_at DESC,alert_id);

CREATE OR REPLACE FUNCTION agent_operations_reject_mutation() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'operational alerts are append-only' USING ERRCODE='55000';
END;
$$;
DROP TRIGGER IF EXISTS agent_operations_alert_immutable ON agent_operations_alert;
CREATE TRIGGER agent_operations_alert_immutable BEFORE UPDATE OR DELETE ON agent_operations_alert
FOR EACH ROW EXECUTE FUNCTION agent_operations_reject_mutation();
