-- Local human identity, immutable human review, and configuration audit.
-- Apply after 034. No credentials, initial tokens, or identities are seeded.
CREATE TABLE IF NOT EXISTS agent_admin_identity (
    principal_id text PRIMARY KEY,
    display_name text NOT NULL CHECK(length(display_name) BETWEEN 1 AND 120),
    roles_json jsonb NOT NULL CHECK(jsonb_typeof(roles_json)='array' AND jsonb_array_length(roles_json)>0
        AND roles_json <@ '["operator","reviewer","admin"]'::jsonb),
    kind text NOT NULL DEFAULT 'human' CHECK(kind='human'),
    enabled boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS agent_admin_token (
    token_hash text PRIMARY KEY CHECK(token_hash ~ '^[0-9a-f]{64}$'),
    principal_id text NOT NULL REFERENCES agent_admin_identity(principal_id),
    expires_at timestamptz NOT NULL,
    revoked_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS agent_human_review (
    review_id text PRIMARY KEY,
    review_seq bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
    run_id text NOT NULL REFERENCES agent_run(run_id),
    subject_type text NOT NULL CHECK(subject_type IN ('candidate','artifact')),
    subject_id text NOT NULL,
    subject_hash text NOT NULL CHECK(subject_hash ~ '^[0-9a-f]{64}$'),
    artifact_id text NOT NULL REFERENCES agent_run_artifact(artifact_id),
    artifact_hash text NOT NULL CHECK(artifact_hash ~ '^[0-9a-f]{64}$'),
    decision text NOT NULL CHECK(decision IN ('accept','reject','request-evidence','resolve-conflict')),
    reason text NOT NULL CHECK(length(reason) BETWEEN 1 AND 4000),
    conflict_id text,
    reviewer_id text NOT NULL REFERENCES agent_admin_identity(principal_id),
    supersedes_review_id text REFERENCES agent_human_review(review_id),
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS agent_human_review_run_idx ON agent_human_review(run_id,review_seq);
CREATE TABLE IF NOT EXISTS agent_admin_config_audit (
    audit_id text PRIMARY KEY,
    actor_id text NOT NULL REFERENCES agent_admin_identity(principal_id),
    resource_type text NOT NULL,
    resource_key text NOT NULL,
    operation text NOT NULL CHECK(operation IN ('create','update','disable')),
    config_json jsonb NOT NULL CHECK(agent_phase3_no_secrets(config_json)),
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE OR REPLACE FUNCTION agent_human_review_integrity() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE identity_row record; subject_row record; artifact_row record; prior_row record;
BEGIN
    SELECT * INTO identity_row FROM agent_admin_identity WHERE principal_id=NEW.reviewer_id;
    IF NOT FOUND OR NOT identity_row.enabled OR identity_row.kind<>'human'
       OR NOT (identity_row.roles_json ? 'reviewer' OR identity_row.roles_json ? 'admin') THEN
        RAISE EXCEPTION 'authenticated human reviewer required' USING ERRCODE='23514';
    END IF;
    SELECT run_id,content_hash INTO artifact_row FROM agent_run_artifact WHERE artifact_id=NEW.artifact_id;
    IF artifact_row.run_id IS DISTINCT FROM NEW.run_id OR artifact_row.content_hash IS DISTINCT FROM NEW.artifact_hash THEN
        RAISE EXCEPTION 'review source artifact changed or foreign' USING ERRCODE='23514';
    END IF;
    IF NEW.subject_type='candidate' THEN
        SELECT c.run_id,c.fingerprint,s.artifact_id,s.artifact_hash INTO subject_row FROM agent_evidence_candidate c
            JOIN agent_source_snapshot s ON s.source_id=c.source_id WHERE c.candidate_id=NEW.subject_id;
        IF subject_row.run_id IS DISTINCT FROM NEW.run_id OR subject_row.fingerprint IS DISTINCT FROM NEW.subject_hash
           OR subject_row.artifact_id IS DISTINCT FROM NEW.artifact_id OR subject_row.artifact_hash IS DISTINCT FROM NEW.artifact_hash THEN
            RAISE EXCEPTION 'review candidate changed or foreign' USING ERRCODE='23514';
        END IF;
    ELSIF NEW.subject_id<>NEW.artifact_id OR NEW.subject_hash<>NEW.artifact_hash THEN
        RAISE EXCEPTION 'artifact review hash mismatch' USING ERRCODE='23514';
    END IF;
    IF NEW.decision='resolve-conflict' AND COALESCE(length(NEW.conflict_id),0)=0 THEN
        RAISE EXCEPTION 'conflict resolution requires identity' USING ERRCODE='23514';
    END IF;
    IF NEW.supersedes_review_id IS NOT NULL THEN
        SELECT * INTO prior_row FROM agent_human_review WHERE review_id=NEW.supersedes_review_id;
        IF prior_row.run_id IS DISTINCT FROM NEW.run_id OR prior_row.subject_type IS DISTINCT FROM NEW.subject_type
           OR prior_row.subject_id IS DISTINCT FROM NEW.subject_id OR prior_row.subject_hash IS DISTINCT FROM NEW.subject_hash THEN
            RAISE EXCEPTION 'superseded review subject mismatch' USING ERRCODE='23514';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS agent_human_review_integrity ON agent_human_review;
CREATE TRIGGER agent_human_review_integrity BEFORE INSERT ON agent_human_review FOR EACH ROW EXECUTE FUNCTION agent_human_review_integrity();
DROP TRIGGER IF EXISTS agent_human_review_immutable ON agent_human_review;
CREATE TRIGGER agent_human_review_immutable BEFORE UPDATE OR DELETE ON agent_human_review FOR EACH ROW EXECUTE FUNCTION agent_engine_reject_mutation();
DROP TRIGGER IF EXISTS agent_admin_config_audit_immutable ON agent_admin_config_audit;
CREATE TRIGGER agent_admin_config_audit_immutable BEFORE UPDATE OR DELETE ON agent_admin_config_audit FOR EACH ROW EXECUTE FUNCTION agent_engine_reject_mutation();
DROP TRIGGER IF EXISTS agent_human_review_fence ON agent_human_review;
CREATE TRIGGER agent_human_review_fence BEFORE INSERT ON agent_human_review FOR EACH ROW EXECUTE FUNCTION agent_worker_fence_guard();
DROP TRIGGER IF EXISTS agent_admin_tool_version_immutable ON tool_version;
CREATE TRIGGER agent_admin_tool_version_immutable BEFORE UPDATE OR DELETE ON tool_version FOR EACH ROW EXECUTE FUNCTION agent_engine_reject_mutation();
