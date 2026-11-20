-- Domain records for the five-category UAV manufacturing contract.
CREATE TABLE IF NOT EXISTS ontology_manufacturing_configuration (
  configuration_id TEXT PRIMARY KEY,
  entity_id TEXT NOT NULL,
  name TEXT NOT NULL,
  variant TEXT,
  valid_from TEXT,
  valid_to TEXT,
  evidence_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(evidence_json)),
  review_status TEXT NOT NULL DEFAULT 'pending' CHECK(review_status IN ('pending','accepted','rejected','published')),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ontology_manufacturing_configuration_entity_idx ON ontology_manufacturing_configuration(entity_id, valid_from);

CREATE TABLE IF NOT EXISTS ontology_assembly_item (
  assembly_item_id TEXT PRIMARY KEY,
  entity_id TEXT NOT NULL,
  configuration_id TEXT REFERENCES ontology_manufacturing_configuration(configuration_id),
  parent_item_id TEXT REFERENCES ontology_assembly_item(assembly_item_id),
  part_name TEXT NOT NULL,
  relation TEXT NOT NULL CHECK(relation IN ('partOfAircraft','partOfGroundSegment','installedOn','compatibleWith')),
  quantity REAL,
  evidence_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(evidence_json)),
  review_status TEXT NOT NULL DEFAULT 'pending' CHECK(review_status IN ('pending','accepted','rejected','published')),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ontology_assembly_item_entity_idx ON ontology_assembly_item(entity_id, configuration_id);

CREATE TABLE IF NOT EXISTS ontology_manufacturing_process_record (
  process_record_id TEXT PRIMARY KEY,
  entity_id TEXT NOT NULL,
  configuration_id TEXT REFERENCES ontology_manufacturing_configuration(configuration_id),
  process_name TEXT NOT NULL,
  process_type TEXT NOT NULL CHECK(process_type IN ('design','fabrication','assembly','inspection','ground-test','flight-test','maintenance')),
  sequence_no INTEGER,
  organization_id TEXT,
  evidence_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(evidence_json)),
  review_status TEXT NOT NULL DEFAULT 'pending' CHECK(review_status IN ('pending','accepted','rejected','published')),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ontology_manufacturing_process_entity_idx ON ontology_manufacturing_process_record(entity_id, configuration_id, sequence_no);

CREATE TABLE IF NOT EXISTS ontology_manufacturing_org_role (
  org_role_id TEXT PRIMARY KEY,
  entity_id TEXT NOT NULL,
  organization_id TEXT,
  organization_name TEXT NOT NULL,
  role TEXT NOT NULL CHECK(role IN ('designer','manufacturer','assembler','supplier','integrator','operator')),
  valid_from TEXT,
  valid_to TEXT,
  evidence_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(evidence_json)),
  review_status TEXT NOT NULL DEFAULT 'pending' CHECK(review_status IN ('pending','accepted','rejected','published')),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ontology_manufacturing_org_role_entity_idx ON ontology_manufacturing_org_role(entity_id, role, valid_from);
