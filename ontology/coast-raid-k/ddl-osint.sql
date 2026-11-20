-- Public-source OSINT collection store.
--
-- This database is separate from both the static ontology package and the
-- runtime execution store.  An osint_item is untrusted research material:
-- it can be reviewed/imported into intelligence_document, but it can never be
-- a published knowledge assertion by itself.

CREATE TABLE IF NOT EXISTS osint_source_allowlist (
  source_id text PRIMARY KEY,
  name text NOT NULL,
  source_uri text NOT NULL UNIQUE CHECK (source_uri LIKE 'https://%'),
  category text NOT NULL,
  access_basis text NOT NULL DEFAULT 'public' CHECK (access_basis = 'public'),
  enabled boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now()
);

-- The rows mirror platform/osint_collector.py.  ON CONFLICT DO NOTHING keeps the
-- source registry append-only when this DDL is applied repeatedly.
INSERT INTO osint_source_allowlist (source_id, name, source_uri, category) VALUES
  ('gao-reports', 'U.S. GAO Reports', 'https://www.gao.gov/rss/reports.xml', 'defense-policy'),
  ('defense-news-pentagon', 'Defense News - Pentagon', 'https://www.defensenews.com/arc/outboundfeeds/rss/category/pentagon/?outputType=xml', 'defense-policy'),
  ('defense-news-industry', 'Defense News - Industry', 'https://www.defensenews.com/arc/outboundfeeds/rss/category/industry/?outputType=xml', 'equipment-procurement-industry'),
  ('csis', 'CSIS Analysis', 'https://www.csis.org/rss.xml', 'security-research'),
  ('congress-bills', 'U.S. Congress Bills', 'https://www.congress.gov/rss/most-viewed-bills.xml', 'defense-policy')
ON CONFLICT DO NOTHING;

CREATE TABLE IF NOT EXISTS osint_task (
  task_id text PRIMARY KEY,
  title text NOT NULL CHECK (length(btrim(title)) > 0),
  question text NOT NULL CHECK (length(btrim(question)) > 0),
  source_ids jsonb NOT NULL CHECK (jsonb_typeof(source_ids) = 'array'),
  keywords jsonb NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(keywords) = 'array'),
  since_days integer NOT NULL DEFAULT 30 CHECK (since_days BETWEEN 1 AND 365),
  max_items integer NOT NULL DEFAULT 20 CHECK (max_items BETWEEN 1 AND 100),
  trigger_mode text NOT NULL DEFAULT 'manual' CHECK (trigger_mode IN ('manual')),
  status text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','created','running','succeeded','completed','partial','failed','cancelled')),
  steps jsonb NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(steps) = 'array'),
  errors jsonb NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(errors) = 'array'),
  source_results jsonb NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(source_results) = 'array'),
  brief jsonb,
  requested_by text NOT NULL DEFAULT 'operator',
  created_at timestamptz NOT NULL DEFAULT now(),
  started_at timestamptz,
  completed_at timestamptz
);
CREATE INDEX IF NOT EXISTS osint_task_status_idx ON osint_task(status, created_at DESC);
CREATE INDEX IF NOT EXISTS osint_task_sources_idx ON osint_task USING gin(source_ids);

CREATE TABLE IF NOT EXISTS osint_item (
  item_id text PRIMARY KEY,
  task_id text NOT NULL REFERENCES osint_task(task_id),
  source_id text NOT NULL REFERENCES osint_source_allowlist(source_id),
  source_name text NOT NULL,
  source_uri text NOT NULL, -- article/item URI exposed to reviewers
  source_feed_uri text NOT NULL, -- curated feed URI used for collection
  source_version text,
  item_uri text,
  category text,
  title text NOT NULL,
  published_at timestamptz,
  body text NOT NULL CHECK (length(btrim(body)) > 0),
  content_hash char(64) NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
  matched_keywords jsonb NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(matched_keywords) = 'array'),
  provenance jsonb NOT NULL DEFAULT '{}'::jsonb,
  review_state text NOT NULL DEFAULT 'pending-review' CHECK (review_state = 'pending-review'),
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (task_id, content_hash)
);
CREATE INDEX IF NOT EXISTS osint_item_task_idx ON osint_item(task_id, created_at DESC);
CREATE INDEX IF NOT EXISTS osint_item_source_time_idx ON osint_item(source_id, published_at DESC);
CREATE INDEX IF NOT EXISTS osint_item_keywords_idx ON osint_item USING gin(matched_keywords);

-- Research records and their provenance are append-only.  A reviewer creates
-- an EvidenceClaim/KnowledgeCandidate revision in ddl-intelligence.sql rather
-- than editing or promoting this item in place.
CREATE OR REPLACE FUNCTION osint_immutable_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION '% is immutable; create a new OSINT record instead', TG_TABLE_NAME;
END; $$;

DROP TRIGGER IF EXISTS osint_item_no_update ON osint_item;
CREATE TRIGGER osint_item_no_update BEFORE UPDATE OR DELETE ON osint_item
FOR EACH ROW EXECUTE FUNCTION osint_immutable_guard();

-- Task configuration is immutable after creation.  Only operational status
-- and completion fields may move through the bounded manual lifecycle.
CREATE OR REPLACE FUNCTION osint_task_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF OLD.title IS DISTINCT FROM NEW.title
     OR OLD.question IS DISTINCT FROM NEW.question
     OR OLD.source_ids IS DISTINCT FROM NEW.source_ids
     OR OLD.keywords IS DISTINCT FROM NEW.keywords
     OR OLD.since_days IS DISTINCT FROM NEW.since_days
     OR OLD.max_items IS DISTINCT FROM NEW.max_items
     OR OLD.trigger_mode IS DISTINCT FROM NEW.trigger_mode
     OR OLD.requested_by IS DISTINCT FROM NEW.requested_by
     OR OLD.created_at IS DISTINCT FROM NEW.created_at THEN
    RAISE EXCEPTION 'OSINT task configuration is immutable';
  END IF;
  RETURN NEW;
END; $$;

DROP TRIGGER IF EXISTS osint_task_configuration_guard ON osint_task;
CREATE TRIGGER osint_task_configuration_guard BEFORE UPDATE ON osint_task
FOR EACH ROW EXECUTE FUNCTION osint_task_guard();

DROP TRIGGER IF EXISTS osint_source_no_update ON osint_source_allowlist;
CREATE TRIGGER osint_source_no_update BEFORE UPDATE OR DELETE ON osint_source_allowlist
FOR EACH ROW EXECUTE FUNCTION osint_immutable_guard();
