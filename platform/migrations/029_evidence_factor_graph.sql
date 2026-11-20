-- Evidence factor graph projection for the structured-equipment SQLite store.
-- Rows are append-only proposals; this graph never publishes ontology facts.
CREATE TABLE IF NOT EXISTS ontology_factor_graph_run (
  graph_run_id TEXT PRIMARY KEY,
  entity_id TEXT NOT NULL REFERENCES ontology_entity(entity_id),
  entity_revision_id TEXT REFERENCES ontology_entity_revision(entity_revision_id),
  schema_version TEXT NOT NULL CHECK(schema_version='evidence-factor-graph.v1'),
  status TEXT NOT NULL CHECK(status IN ('pending-review','needs-evidence','conflict','complete-with-gaps')),
  graph_json TEXT NOT NULL CHECK(json_valid(graph_json) AND json_type(graph_json)='object'),
  content_hash TEXT NOT NULL CHECK(length(content_hash)=64),
  created_at TEXT NOT NULL,
  UNIQUE(entity_id, content_hash)
);
CREATE INDEX IF NOT EXISTS ontology_factor_graph_run_entity_idx
  ON ontology_factor_graph_run(entity_id, created_at DESC, status);

CREATE TABLE IF NOT EXISTS ontology_factor_graph_variable (
  variable_id TEXT PRIMARY KEY,
  graph_run_id TEXT NOT NULL REFERENCES ontology_factor_graph_run(graph_run_id),
  entity_id TEXT NOT NULL REFERENCES ontology_entity(entity_id),
  variable_key TEXT NOT NULL,
  variable_type TEXT NOT NULL CHECK(variable_type IN ('identity','configuration','organization','process','production','cost','composition')),
  state TEXT NOT NULL CHECK(state IN ('proposed','unknown','conflict','constrained')),
  value_json TEXT,
  unit TEXT,
  conditions_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(conditions_json) AND json_type(conditions_json)='object'),
  candidate_ids_json TEXT NOT NULL DEFAULT '[]' CHECK(json_valid(candidate_ids_json) AND json_type(candidate_ids_json)='array'),
  content_hash TEXT NOT NULL CHECK(length(content_hash)=64),
  created_at TEXT NOT NULL,
  UNIQUE(graph_run_id, variable_key)
);
CREATE INDEX IF NOT EXISTS ontology_factor_graph_variable_run_idx
  ON ontology_factor_graph_variable(graph_run_id, variable_type, state);

CREATE TABLE IF NOT EXISTS ontology_factor_graph_observation (
  observation_id TEXT PRIMARY KEY,
  graph_run_id TEXT NOT NULL REFERENCES ontology_factor_graph_run(graph_run_id),
  entity_id TEXT NOT NULL REFERENCES ontology_entity(entity_id),
  candidate_id TEXT,
  source_snapshot_id TEXT NOT NULL,
  source_uri TEXT NOT NULL,
  quote TEXT NOT NULL,
  claim_state TEXT NOT NULL CHECK(claim_state IN ('reported','derived','unknown','conflict','rejected')),
  variable_ids_json TEXT NOT NULL CHECK(json_valid(variable_ids_json) AND json_type(variable_ids_json)='array'),
  locator_json TEXT CHECK(locator_json IS NULL OR json_valid(locator_json)),
  content_hash TEXT NOT NULL CHECK(length(content_hash)=64),
  created_at TEXT NOT NULL,
  UNIQUE(graph_run_id, content_hash)
);
CREATE INDEX IF NOT EXISTS ontology_factor_graph_observation_run_idx
  ON ontology_factor_graph_observation(graph_run_id, source_snapshot_id);

CREATE TABLE IF NOT EXISTS ontology_factor_graph_factor (
  factor_id TEXT PRIMARY KEY,
  graph_run_id TEXT NOT NULL REFERENCES ontology_factor_graph_run(graph_run_id),
  factor_type TEXT NOT NULL CHECK(factor_type IN ('source-support','identity-match','configuration-scope','time-consistency','unit-normalization','source-independence','conflict','review-gate')),
  status TEXT NOT NULL CHECK(status IN ('supporting','contradicting','unresolved','satisfied','failed')),
  variable_ids_json TEXT NOT NULL DEFAULT '[]' CHECK(json_valid(variable_ids_json) AND json_type(variable_ids_json)='array'),
  observation_ids_json TEXT NOT NULL DEFAULT '[]' CHECK(json_valid(observation_ids_json) AND json_type(observation_ids_json)='array'),
  score REAL CHECK(score IS NULL OR (score >= 0 AND score <= 1)),
  details_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(details_json) AND json_type(details_json)='object'),
  content_hash TEXT NOT NULL CHECK(length(content_hash)=64),
  created_at TEXT NOT NULL,
  UNIQUE(graph_run_id, content_hash)
);
CREATE INDEX IF NOT EXISTS ontology_factor_graph_factor_run_idx
  ON ontology_factor_graph_factor(graph_run_id, factor_type, status);

CREATE TABLE IF NOT EXISTS ontology_factor_graph_constraint (
  constraint_id TEXT PRIMARY KEY,
  graph_run_id TEXT NOT NULL REFERENCES ontology_factor_graph_run(graph_run_id),
  constraint_type TEXT NOT NULL CHECK(constraint_type IN ('identity','configuration','time','unit','source','review')),
  status TEXT NOT NULL CHECK(status IN ('satisfied','failed','unresolved')),
  variable_ids_json TEXT NOT NULL DEFAULT '[]' CHECK(json_valid(variable_ids_json) AND json_type(variable_ids_json)='array'),
  details_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(details_json) AND json_type(details_json)='object'),
  content_hash TEXT NOT NULL CHECK(length(content_hash)=64),
  created_at TEXT NOT NULL,
  UNIQUE(graph_run_id, content_hash)
);
CREATE INDEX IF NOT EXISTS ontology_factor_graph_constraint_run_idx
  ON ontology_factor_graph_constraint(graph_run_id, constraint_type, status);

CREATE TRIGGER IF NOT EXISTS ontology_factor_graph_run_update_immutable
BEFORE UPDATE ON ontology_factor_graph_run BEGIN
  SELECT RAISE(ABORT, 'factor graph runs are append-only');
END;
CREATE TRIGGER IF NOT EXISTS ontology_factor_graph_run_delete_immutable
BEFORE DELETE ON ontology_factor_graph_run BEGIN
  SELECT RAISE(ABORT, 'factor graph runs are append-only');
END;
CREATE TRIGGER IF NOT EXISTS ontology_factor_graph_variable_update_immutable
BEFORE UPDATE ON ontology_factor_graph_variable BEGIN
  SELECT RAISE(ABORT, 'factor graph variables are append-only');
END;
CREATE TRIGGER IF NOT EXISTS ontology_factor_graph_variable_delete_immutable
BEFORE DELETE ON ontology_factor_graph_variable BEGIN
  SELECT RAISE(ABORT, 'factor graph variables are append-only');
END;
CREATE TRIGGER IF NOT EXISTS ontology_factor_graph_observation_update_immutable
BEFORE UPDATE ON ontology_factor_graph_observation BEGIN
  SELECT RAISE(ABORT, 'factor graph observations are append-only');
END;
CREATE TRIGGER IF NOT EXISTS ontology_factor_graph_observation_delete_immutable
BEFORE DELETE ON ontology_factor_graph_observation BEGIN
  SELECT RAISE(ABORT, 'factor graph observations are append-only');
END;
CREATE TRIGGER IF NOT EXISTS ontology_factor_graph_factor_update_immutable
BEFORE UPDATE ON ontology_factor_graph_factor BEGIN
  SELECT RAISE(ABORT, 'factor graph factors are append-only');
END;
CREATE TRIGGER IF NOT EXISTS ontology_factor_graph_factor_delete_immutable
BEFORE DELETE ON ontology_factor_graph_factor BEGIN
  SELECT RAISE(ABORT, 'factor graph factors are append-only');
END;
CREATE TRIGGER IF NOT EXISTS ontology_factor_graph_constraint_update_immutable
BEFORE UPDATE ON ontology_factor_graph_constraint BEGIN
  SELECT RAISE(ABORT, 'factor graph constraints are append-only');
END;
CREATE TRIGGER IF NOT EXISTS ontology_factor_graph_constraint_delete_immutable
BEFORE DELETE ON ontology_factor_graph_constraint BEGIN
  SELECT RAISE(ABORT, 'factor graph constraints are append-only');
END;
