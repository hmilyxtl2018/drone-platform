-- Context fields kept on the candidate entity projection.  They are source
-- facts used for identity and review; publication still requires admission.
ALTER TABLE ontology_entity ADD COLUMN origin_country TEXT;
ALTER TABLE ontology_entity ADD COLUMN manufacturer TEXT;
ALTER TABLE ontology_entity ADD COLUMN user_countries_json TEXT NOT NULL DEFAULT '[]'
  CHECK (json_valid(user_countries_json));
ALTER TABLE ontology_entity ADD COLUMN combat_status TEXT
  CHECK (combat_status IN ('yes','no','unknown'));
CREATE INDEX IF NOT EXISTS ontology_entity_manufacturer_idx
  ON ontology_entity(manufacturer, origin_country);
