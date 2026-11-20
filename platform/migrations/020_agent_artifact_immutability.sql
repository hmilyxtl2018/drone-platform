-- Backfill append-only protection if migration 019 was applied before the
-- artifact triggers were added.
CREATE TRIGGER IF NOT EXISTS ontology_agent_artifact_no_update BEFORE UPDATE ON ontology_agent_artifact BEGIN SELECT RAISE(ABORT,'agent artifacts are immutable'); END;
CREATE TRIGGER IF NOT EXISTS ontology_agent_artifact_no_delete BEFORE DELETE ON ontology_agent_artifact BEGIN SELECT RAISE(ABORT,'agent artifacts are retained'); END;
