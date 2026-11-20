-- Explicit relation publication audit; assertions remain append-only.
CREATE TABLE IF NOT EXISTS ontology_relation_candidate_publication (
  publication_id TEXT PRIMARY KEY,
  candidate_id TEXT NOT NULL REFERENCES ontology_relation_candidate(candidate_id),
  relation_id TEXT NOT NULL REFERENCES ontology_relation_assertion(relation_id),
  published_by TEXT NOT NULL,
  reason TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ontology_relation_candidate_publication_idx
  ON ontology_relation_candidate_publication(candidate_id,created_at);
