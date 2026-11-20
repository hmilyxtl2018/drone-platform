-- P4-01: public token handles, optimistic identity revisions and immutable audit.
-- Apply after 035. No identity or access token is seeded by this migration.
-- gen_random_uuid() is built into supported PostgreSQL (13 and later).
ALTER TABLE agent_admin_identity
    ADD COLUMN IF NOT EXISTS revision bigint NOT NULL DEFAULT 1 CHECK (revision > 0);

ALTER TABLE agent_admin_token
    ADD COLUMN IF NOT EXISTS token_id text NOT NULL DEFAULT ('token-' || gen_random_uuid()::text);
CREATE UNIQUE INDEX IF NOT EXISTS agent_admin_token_id_unique ON agent_admin_token(token_id);
CREATE INDEX IF NOT EXISTS agent_admin_token_principal_idx
    ON agent_admin_token(principal_id, expires_at) WHERE revoked_at IS NULL;

CREATE TABLE IF NOT EXISTS agent_admin_identity_audit (
    audit_id text PRIMARY KEY,
    audit_seq bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
    actor_id text NOT NULL REFERENCES agent_admin_identity(principal_id),
    subject_principal_id text NOT NULL REFERENCES agent_admin_identity(principal_id),
    token_id text REFERENCES agent_admin_token(token_id),
    operation text NOT NULL CHECK (operation IN ('create-identity','update-identity','revoke-token')),
    reason text NOT NULL CHECK (length(reason) BETWEEN 1 AND 2000),
    before_json jsonb CHECK (before_json IS NULL OR jsonb_typeof(before_json)='object'),
    after_json jsonb NOT NULL CHECK (jsonb_typeof(after_json)='object'),
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS agent_admin_identity_audit_subject_idx
    ON agent_admin_identity_audit(subject_principal_id,audit_seq);

CREATE OR REPLACE FUNCTION agent_admin_identity_audit_reject_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'identity management audit is append-only' USING ERRCODE='23514';
END;
$$;
DROP TRIGGER IF EXISTS agent_admin_identity_audit_immutable ON agent_admin_identity_audit;
CREATE TRIGGER agent_admin_identity_audit_immutable
    BEFORE UPDATE OR DELETE ON agent_admin_identity_audit
    FOR EACH ROW EXECUTE FUNCTION agent_admin_identity_audit_reject_mutation();
DROP TRIGGER IF EXISTS agent_admin_identity_audit_no_truncate ON agent_admin_identity_audit;
CREATE TRIGGER agent_admin_identity_audit_no_truncate
    BEFORE TRUNCATE ON agent_admin_identity_audit
    FOR EACH STATEMENT EXECUTE FUNCTION agent_admin_identity_audit_reject_mutation();
