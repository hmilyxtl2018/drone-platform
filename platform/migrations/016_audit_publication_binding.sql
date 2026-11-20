-- Bind immutable capability publications back to the formal entity audit.
ALTER TABLE ontology_capability_publication ADD COLUMN audit_run_id TEXT REFERENCES ontology_audit_run(audit_run_id);
CREATE INDEX IF NOT EXISTS ontology_capability_publication_audit_idx ON ontology_capability_publication(audit_run_id);
