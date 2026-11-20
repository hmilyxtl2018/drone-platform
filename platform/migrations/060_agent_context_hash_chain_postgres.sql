-- Append-only context lineage.  The manifest hash proves one projection;
-- the chain hash makes gaps or reordering visible during recovery audits.
ALTER TABLE agent_context_snapshot
    ADD COLUMN IF NOT EXISTS previous_chain_hash text,
    ADD COLUMN IF NOT EXISTS chain_hash text;

ALTER TABLE agent_context_snapshot
    DROP CONSTRAINT IF EXISTS agent_context_previous_chain_hash_format,
    DROP CONSTRAINT IF EXISTS agent_context_chain_hash_format;
ALTER TABLE agent_context_snapshot
    ADD CONSTRAINT agent_context_previous_chain_hash_format
      CHECK (previous_chain_hash IS NULL OR previous_chain_hash ~ '^[0-9a-f]{64}$'),
    ADD CONSTRAINT agent_context_chain_hash_format
      CHECK (chain_hash IS NULL OR chain_hash ~ '^[0-9a-f]{64}$');

CREATE INDEX IF NOT EXISTS agent_context_snapshot_root_chain_idx
    ON agent_context_snapshot(root_run_id, created_at, context_id);
