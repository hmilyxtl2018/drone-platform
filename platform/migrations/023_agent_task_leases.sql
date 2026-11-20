-- Agent workers use a bounded lease so a crashed provider call can be
-- reclaimed without waiting for a process restart.  worker_id remains the
-- owner identifier; this nullable timestamp is the expiry boundary.
ALTER TABLE ontology_agent_task ADD COLUMN lease_expires_at TEXT;
CREATE INDEX IF NOT EXISTS ontology_agent_task_lease_idx
  ON ontology_agent_task(status, lease_expires_at);
