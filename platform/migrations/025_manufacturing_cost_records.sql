-- Evidence-backed production, delivery and cost records for EquipmentModel.
CREATE TABLE IF NOT EXISTS ontology_production_record (
  production_record_id TEXT PRIMARY KEY,
  entity_id TEXT NOT NULL,
  record_type TEXT NOT NULL CHECK(record_type IN ('production','delivery','order')),
  organization_id TEXT,
  quantity REAL,
  quantity_unit TEXT NOT NULL DEFAULT 'unit',
  start_date TEXT,
  end_date TEXT,
  configuration TEXT,
  source_snapshot_ids_json TEXT NOT NULL DEFAULT '[]' CHECK(json_valid(source_snapshot_ids_json)),
  evidence_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(evidence_json)),
  review_status TEXT NOT NULL DEFAULT 'pending' CHECK(review_status IN ('pending','accepted','rejected','published')),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ontology_production_record_entity_idx ON ontology_production_record(entity_id, start_date);

CREATE TABLE IF NOT EXISTS ontology_cost_record (
  cost_record_id TEXT PRIMARY KEY,
  entity_id TEXT NOT NULL,
  cost_type TEXT NOT NULL CHECK(cost_type IN ('unit-production','flyaway','system-procurement','development','operating','lifecycle','other')),
  amount REAL NOT NULL,
  currency TEXT NOT NULL,
  price_basis_year INTEGER,
  quantity REAL,
  quantity_unit TEXT NOT NULL DEFAULT 'unit',
  configuration TEXT,
  includes_json TEXT NOT NULL DEFAULT '[]' CHECK(json_valid(includes_json)),
  excludes_json TEXT NOT NULL DEFAULT '[]' CHECK(json_valid(excludes_json)),
  source_snapshot_ids_json TEXT NOT NULL DEFAULT '[]' CHECK(json_valid(source_snapshot_ids_json)),
  evidence_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(evidence_json)),
  value_status TEXT NOT NULL DEFAULT 'reported' CHECK(value_status IN ('reported','estimated','calculated','unknown')),
  review_status TEXT NOT NULL DEFAULT 'pending' CHECK(review_status IN ('pending','accepted','rejected','published')),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ontology_cost_record_entity_idx ON ontology_cost_record(entity_id, cost_type, price_basis_year);
