-- Signal intelligence, capability-claim and evidence-chain contracts.
--
-- These tables are append-only candidate records.  A row being verified here
-- does not publish a fact into the ontology; publication still goes through
-- ontology_audit_review and ontology_capability_publication.  JSON columns are
-- TEXT in SQLite and are projected as JSONB by db/signal-intelligence-jsonb.sql.

CREATE TABLE IF NOT EXISTS ontology_emitter_signature (
  signature_id TEXT PRIMARY KEY,
  entity_id TEXT REFERENCES ontology_entity(entity_id),
  signature_type TEXT NOT NULL CHECK (signature_type IN (
    'communications','radar','telemetry','infrared','acoustic','imagery','other'
  )),
  features_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(features_json) AND json_type(features_json)='object'),
  attribution_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(attribution_json) AND json_type(attribution_json)='object'),
  source_refs TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(source_refs) AND json_type(source_refs)='array'),
  confidence REAL CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
  verification_status TEXT NOT NULL DEFAULT 'unverified' CHECK (verification_status IN ('unverified','under-review','verified','conflict','rejected')),
  content_hash TEXT NOT NULL CHECK (length(content_hash)=64),
  created_at TEXT NOT NULL,
  UNIQUE(entity_id, content_hash)
);
CREATE INDEX IF NOT EXISTS ontology_emitter_signature_entity_idx
  ON ontology_emitter_signature(entity_id, signature_type, verification_status, created_at DESC);

CREATE TABLE IF NOT EXISTS ontology_signal_observation (
  observation_id TEXT PRIMARY KEY,
  entity_id TEXT REFERENCES ontology_entity(entity_id),
  audit_run_id TEXT REFERENCES ontology_audit_run(audit_run_id),
  observation_type TEXT NOT NULL CHECK (observation_type IN (
    'COMINT','ELINT','FISINT','EW','MASINT','IMAGERY','TELEMETRY','OSINT'
  )),
  collected_at TEXT NOT NULL,
  collection_location TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(collection_location) AND json_type(collection_location)='object'),
  conditions_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(conditions_json) AND json_type(conditions_json)='object'),
  features_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(features_json) AND json_type(features_json)='object'),
  emitter_signature_id TEXT REFERENCES ontology_emitter_signature(signature_id),
  source_refs TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(source_refs) AND json_type(source_refs)='array'),
  claim_state TEXT NOT NULL DEFAULT 'observed' CHECK (claim_state IN ('reported','observed','derived','inference','unknown','conflict','rejected')),
  confidence REAL CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
  verification_status TEXT NOT NULL DEFAULT 'unverified' CHECK (verification_status IN ('unverified','under-review','verified','conflict','rejected')),
  evidence_level TEXT NOT NULL DEFAULT 'E0' CHECK (evidence_level IN ('E0','E1','E2','E3','E4','E5')),
  content_hash TEXT NOT NULL CHECK (length(content_hash)=64),
  created_at TEXT NOT NULL,
  UNIQUE(entity_id, content_hash)
);
CREATE INDEX IF NOT EXISTS ontology_signal_observation_entity_idx
  ON ontology_signal_observation(entity_id, observation_type, collected_at DESC);
CREATE INDEX IF NOT EXISTS ontology_signal_observation_audit_idx
  ON ontology_signal_observation(audit_run_id, verification_status, created_at DESC);

CREATE TABLE IF NOT EXISTS ontology_evidence_bundle (
  evidence_bundle_id TEXT PRIMARY KEY,
  entity_id TEXT REFERENCES ontology_entity(entity_id),
  audit_run_id TEXT REFERENCES ontology_audit_run(audit_run_id),
  claim_ids TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(claim_ids) AND json_type(claim_ids)='array'),
  observation_ids TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(observation_ids) AND json_type(observation_ids)='array'),
  evidence_ids TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(evidence_ids) AND json_type(evidence_ids)='array'),
  source_independence_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(source_independence_json) AND json_type(source_independence_json)='object'),
  validity_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(validity_json) AND json_type(validity_json)='object'),
  counter_evidence_ids TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(counter_evidence_ids) AND json_type(counter_evidence_ids)='array'),
  evidence_level TEXT NOT NULL DEFAULT 'E0' CHECK (evidence_level IN ('E0','E1','E2','E3','E4','E5')),
  verification_status TEXT NOT NULL DEFAULT 'unverified' CHECK (verification_status IN ('unverified','under-review','verified','conflict','rejected')),
  content_hash TEXT NOT NULL CHECK (length(content_hash)=64),
  created_at TEXT NOT NULL,
  UNIQUE(entity_id, content_hash)
);
CREATE INDEX IF NOT EXISTS ontology_evidence_bundle_entity_idx
  ON ontology_evidence_bundle(entity_id, evidence_level, verification_status, created_at DESC);
CREATE INDEX IF NOT EXISTS ontology_evidence_bundle_audit_idx
  ON ontology_evidence_bundle(audit_run_id, created_at DESC);

CREATE TABLE IF NOT EXISTS ontology_capability_claim (
  claim_id TEXT PRIMARY KEY,
  entity_id TEXT NOT NULL REFERENCES ontology_entity(entity_id),
  entity_revision_id TEXT REFERENCES ontology_entity_revision(entity_revision_id),
  audit_run_id TEXT REFERENCES ontology_audit_run(audit_run_id),
  capability_domain TEXT NOT NULL,
  metric TEXT NOT NULL,
  raw_value TEXT NOT NULL,
  value_type TEXT NOT NULL CHECK (value_type IN ('number','quantity','range','text','enum','boolean','reference','list','object')),
  normalized_value TEXT,
  unit TEXT,
  qualifier TEXT NOT NULL DEFAULT 'unknown' CHECK (qualifier IN ('exact','approximately','at-least','at-most','greater-than','less-than','unknown')),
  conditions_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(conditions_json) AND json_type(conditions_json)='object'),
  variant TEXT,
  configuration TEXT,
  operational_effect TEXT,
  source_observation_ids TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(source_observation_ids) AND json_type(source_observation_ids)='array'),
  evidence_bundle_ids TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(evidence_bundle_ids) AND json_type(evidence_bundle_ids)='array'),
  claim_state TEXT NOT NULL DEFAULT 'reported' CHECK (claim_state IN ('reported','observed','derived','inference','unknown','conflict','rejected')),
  verification_status TEXT NOT NULL DEFAULT 'unverified' CHECK (verification_status IN ('unverified','under-review','verified','conflict','rejected')),
  evidence_level TEXT NOT NULL DEFAULT 'E0' CHECK (evidence_level IN ('E0','E1','E2','E3','E4','E5')),
  confidence REAL CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
  review_status TEXT NOT NULL DEFAULT 'under-review' CHECK (review_status IN ('pending','under-review','accepted','rejected','withdrawn','superseded','published')),
  truth_verified INTEGER NOT NULL DEFAULT 0 CHECK (truth_verified IN (0,1)),
  content_hash TEXT NOT NULL CHECK (length(content_hash)=64),
  created_at TEXT NOT NULL,
  UNIQUE(entity_id, metric, content_hash)
);
CREATE INDEX IF NOT EXISTS ontology_capability_claim_entity_idx
  ON ontology_capability_claim(entity_id, capability_domain, metric, review_status);
CREATE INDEX IF NOT EXISTS ontology_capability_claim_audit_idx
  ON ontology_capability_claim(audit_run_id, verification_status, evidence_level);

CREATE TABLE IF NOT EXISTS ontology_verification_run (
  verification_run_id TEXT PRIMARY KEY,
  entity_id TEXT NOT NULL REFERENCES ontology_entity(entity_id),
  audit_run_id TEXT REFERENCES ontology_audit_run(audit_run_id),
  run_type TEXT NOT NULL CHECK (run_type IN ('source-validation','signal-correlation','claim-verification','periodic-audit')),
  status TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','running','completed','completed-with-gaps','failed','cancelled')),
  method_version TEXT NOT NULL,
  input_refs TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(input_refs) AND json_type(input_refs)='object'),
  result_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(result_json) AND json_type(result_json)='object'),
  checks_json TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(checks_json) AND json_type(checks_json)='array'),
  evidence_level TEXT NOT NULL DEFAULT 'E0' CHECK (evidence_level IN ('E0','E1','E2','E3','E4','E5')),
  confidence REAL CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
  started_at TEXT,
  completed_at TEXT,
  content_hash TEXT NOT NULL CHECK (length(content_hash)=64),
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ontology_verification_run_entity_idx
  ON ontology_verification_run(entity_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ontology_verification_run_audit_idx
  ON ontology_verification_run(audit_run_id, status, created_at DESC);

-- Provenance records are retained.  New verification or review state is
-- represented by a new row or by the existing audit/review ledgers.
CREATE TRIGGER IF NOT EXISTS ontology_emitter_signature_no_update
BEFORE UPDATE ON ontology_emitter_signature BEGIN SELECT RAISE(ABORT,'emitter signatures are immutable'); END;
CREATE TRIGGER IF NOT EXISTS ontology_emitter_signature_no_delete
BEFORE DELETE ON ontology_emitter_signature BEGIN SELECT RAISE(ABORT,'emitter signatures are retained'); END;
CREATE TRIGGER IF NOT EXISTS ontology_signal_observation_no_update
BEFORE UPDATE ON ontology_signal_observation BEGIN SELECT RAISE(ABORT,'signal observations are immutable'); END;
CREATE TRIGGER IF NOT EXISTS ontology_signal_observation_no_delete
BEFORE DELETE ON ontology_signal_observation BEGIN SELECT RAISE(ABORT,'signal observations are retained'); END;
CREATE TRIGGER IF NOT EXISTS ontology_evidence_bundle_no_update
BEFORE UPDATE ON ontology_evidence_bundle BEGIN SELECT RAISE(ABORT,'evidence bundles are immutable'); END;
CREATE TRIGGER IF NOT EXISTS ontology_evidence_bundle_no_delete
BEFORE DELETE ON ontology_evidence_bundle BEGIN SELECT RAISE(ABORT,'evidence bundles are retained'); END;
CREATE TRIGGER IF NOT EXISTS ontology_capability_claim_no_update
BEFORE UPDATE ON ontology_capability_claim BEGIN SELECT RAISE(ABORT,'capability claims are immutable'); END;
CREATE TRIGGER IF NOT EXISTS ontology_capability_claim_no_delete
BEFORE DELETE ON ontology_capability_claim BEGIN SELECT RAISE(ABORT,'capability claims are retained'); END;
-- VerificationRun is the mutable lifecycle projection for one check.  Its
-- checks/result payloads and the audit-run event ledger retain the history;
-- source observations, claims and evidence bundles above remain immutable.
