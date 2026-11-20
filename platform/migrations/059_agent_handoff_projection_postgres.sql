-- Immutable, hash-addressed role projection for application handoffs.
-- The full upstream artifacts remain in the existing artifact store; this
-- record proves exactly which task fields and artifact references were exposed
-- to the child Run.
ALTER TABLE agent_run_handoff
    ADD COLUMN IF NOT EXISTS projection_schema_version text,
    ADD COLUMN IF NOT EXISTS projection_json jsonb NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS projection_hash text;

ALTER TABLE agent_run_handoff
    DROP CONSTRAINT IF EXISTS agent_handoff_projection_hash_format;
ALTER TABLE agent_run_handoff
    ADD CONSTRAINT agent_handoff_projection_hash_format
    CHECK (projection_hash IS NULL OR projection_hash ~ '^[0-9a-f]{64}$');
