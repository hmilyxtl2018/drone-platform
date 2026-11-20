-- Resolution is part of a new append-only review, never a rewrite of source values.
ALTER TABLE ontology_capability_review ADD COLUMN resolution_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(resolution_json));
