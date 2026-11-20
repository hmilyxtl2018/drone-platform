-- coast-raid-k runtime ledger (PostgreSQL 14+)
--
-- This schema is deliberately separate from ddl.sql.  ddl.sql is a generated
-- static knowledge snapshot; these tables hold append-only runs and events and
-- must survive a new knowledge package load.  JSONB payloads keep the event
-- envelope extensible while the important replay and provenance keys remain
-- indexed columns.

CREATE TABLE IF NOT EXISTS crk_run (
  run_id          text PRIMARY KEY,
  package_id      text,
  package_version text,
  model_id        text,
  model_revision  text,
  scenario        text,
  status          text NOT NULL DEFAULT 'created',
  started_at      timestamptz NOT NULL,
  ended_at        timestamptz,
  manifest_hash   text,
  metadata        jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS crk_runtime_event (
  event_id        text PRIMARY KEY,
  run_id          text NOT NULL REFERENCES crk_run(run_id) ON DELETE CASCADE,
  seq             bigint NOT NULL,
  event_type      text NOT NULL,
  event_iri       text NOT NULL,
  actor_iri       text,
  subject_iri     text,
  causation_id    text,
  correlation_id  text,
  occurred_at     timestamptz NOT NULL,
  payload         jsonb NOT NULL,
  payload_sha256  text NOT NULL,
  provenance      jsonb NOT NULL DEFAULT '{}'::jsonb,
  visibility      text NOT NULL DEFAULT 'run',
  created_at      timestamptz NOT NULL DEFAULT now(),
  UNIQUE(run_id, seq),
  UNIQUE(run_id, event_iri)
);
CREATE INDEX IF NOT EXISTS crk_runtime_event_run_seq_idx ON crk_runtime_event(run_id, seq);
CREATE INDEX IF NOT EXISTS crk_runtime_event_type_idx ON crk_runtime_event(run_id, event_type);
CREATE INDEX IF NOT EXISTS crk_runtime_event_causation_idx ON crk_runtime_event(run_id, causation_id);
CREATE INDEX IF NOT EXISTS crk_runtime_event_correlation_idx ON crk_runtime_event(run_id, correlation_id);

CREATE TABLE IF NOT EXISTS crk_runtime_evidence (
  evidence_id    text PRIMARY KEY,
  run_id         text NOT NULL REFERENCES crk_run(run_id) ON DELETE CASCADE,
  event_id       text REFERENCES crk_runtime_event(event_id) ON DELETE CASCADE,
  subject_iri    text,
  relation       text NOT NULL,
  source_iri     text,
  source_version text,
  content_hash   text,
  detail         jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS crk_runtime_evidence_run_idx ON crk_runtime_evidence(run_id, created_at);
CREATE INDEX IF NOT EXISTS crk_runtime_evidence_event_idx ON crk_runtime_evidence(event_id);

CREATE TABLE IF NOT EXISTS crk_runtime_entity (
  run_id          text NOT NULL REFERENCES crk_run(run_id) ON DELETE CASCADE,
  entity_iri      text NOT NULL,
  entity_type     text,
  status          text,
  payload         jsonb NOT NULL DEFAULT '{}'::jsonb,
  latest_event_id text NOT NULL REFERENCES crk_runtime_event(event_id),
  updated_at      timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY(run_id, entity_iri)
);

CREATE TABLE IF NOT EXISTS crk_trigger (
  trigger_id        text PRIMARY KEY,
  package_id        text,
  package_version   text,
  event_type        text NOT NULL,
  condition         jsonb NOT NULL DEFAULT '{}'::jsonb,
  target_agent      text,
  cooldown_seconds  integer NOT NULL DEFAULT 0,
  dedupe_key        text,
  max_concurrency   integer NOT NULL DEFAULT 1,
  requires_approval boolean NOT NULL DEFAULT false,
  on_failure        text NOT NULL DEFAULT 'audit',
  enabled           boolean NOT NULL DEFAULT true,
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS crk_human_review (
  review_id            text PRIMARY KEY,
  run_id               text NOT NULL REFERENCES crk_run(run_id) ON DELETE CASCADE,
  action_request_iri   text NOT NULL,
  recommendation_iri   text,
  requested_by         text,
  status               text NOT NULL DEFAULT 'pending',
  decision             text,
  reason               text,
  requested_at         timestamptz NOT NULL DEFAULT now(),
  expires_at           timestamptz,
  decided_at           timestamptz,
  decided_by           text,
  request_event_id     text REFERENCES crk_runtime_event(event_id),
  decision_event_id    text REFERENCES crk_runtime_event(event_id),
  payload              jsonb NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS crk_human_review_queue_idx ON crk_human_review(run_id, status, requested_at);

-- PostgreSQL does not permit a simple BEFORE trigger to be defined inline, so
-- use one reusable guard for the two immutable event operations.
CREATE OR REPLACE FUNCTION crk_runtime_event_append_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'crk_runtime_event is append-only';
END;
$$;
DROP TRIGGER IF EXISTS crk_runtime_event_no_update ON crk_runtime_event;
CREATE TRIGGER crk_runtime_event_no_update BEFORE UPDATE ON crk_runtime_event
FOR EACH ROW EXECUTE FUNCTION crk_runtime_event_append_only();
DROP TRIGGER IF EXISTS crk_runtime_event_no_delete ON crk_runtime_event;
CREATE TRIGGER crk_runtime_event_no_delete BEFORE DELETE ON crk_runtime_event
FOR EACH ROW EXECUTE FUNCTION crk_runtime_event_append_only();

COMMENT ON TABLE crk_run IS 'Runtime run boundary and immutable knowledge/model version lock.';
COMMENT ON TABLE crk_runtime_event IS 'Append-only causal event ledger. (run_id, seq) is the replay order.';
COMMENT ON COLUMN crk_runtime_event.payload_sha256 IS 'SHA-256 of canonical UTF-8 JSON payload.';
COMMENT ON TABLE crk_runtime_evidence IS 'Evidence edges attached to a runtime event.';
COMMENT ON TABLE crk_runtime_entity IS 'Latest-state read model projected from the event ledger.';
COMMENT ON TABLE crk_human_review IS 'Human-in-the-loop queue; decisions produce HumanDecision events.';

