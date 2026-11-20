-- Model-family lineage extracted from the workbook's 改型血缘 sheet.
-- Edges remain pending until a reviewer confirms the identity mapping.
CREATE TABLE IF NOT EXISTS ontology_lineage_edge (
  lineage_edge_id TEXT PRIMARY KEY,
  snapshot_id TEXT NOT NULL REFERENCES ontology_import_snapshot(snapshot_id),
  source_record_id TEXT REFERENCES ontology_raw_record(raw_record_id),
  evidence_id TEXT REFERENCES ontology_evidence_link(evidence_id),
  family_name TEXT NOT NULL,
  sequence_no INTEGER NOT NULL CHECK (sequence_no >= 1),
  predecessor_entity_id TEXT REFERENCES ontology_entity(entity_id),
  successor_entity_id TEXT REFERENCES ontology_entity(entity_id),
  predecessor_name TEXT NOT NULL,
  successor_name TEXT NOT NULL,
  raw_chain TEXT NOT NULL,
  qualifier_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(qualifier_json)),
  match_status TEXT NOT NULL DEFAULT 'matched' CHECK (match_status IN ('matched','identity-review')),
  review_status TEXT NOT NULL DEFAULT 'pending' CHECK (review_status IN ('pending','under-review','accepted','rejected','published','withdrawn')),
  created_at TEXT NOT NULL,
  UNIQUE(snapshot_id, family_name, sequence_no, predecessor_name, successor_name)
);
CREATE INDEX IF NOT EXISTS ontology_lineage_edge_entity_idx
  ON ontology_lineage_edge(predecessor_entity_id, successor_entity_id, review_status);
