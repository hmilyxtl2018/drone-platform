-- Intelligence collection and evidence governance store.
-- Kept separate from the static ontology projection and runtime execution store.

CREATE TABLE IF NOT EXISTS intelligence_document (
  document_id text PRIMARY KEY,
  title text NOT NULL,
  source_uri text NOT NULL,
  source_version text NOT NULL,
  body text NOT NULL,
  content_hash char(64) NOT NULL UNIQUE,
  access_basis text NOT NULL CHECK (access_basis IN ('public','authorized')),
  source_reliability text NOT NULL DEFAULT 'unknown' CHECK (source_reliability IN ('unknown','primary','secondary')),
  status text NOT NULL DEFAULT 'ingested',
  immutable boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS intelligence_candidate (
  candidate_id text PRIMARY KEY,
  document_id text NOT NULL REFERENCES intelligence_document(document_id),
  claim_id text NOT NULL,
  revision integer NOT NULL DEFAULT 1 CHECK (revision > 0),
  original_statement text NOT NULL,
  statement text NOT NULL,
  subject text,
  predicate text,
  value jsonb,
  match_key text,
  status text NOT NULL DEFAULT 'under-review',
  evidence jsonb NOT NULL,
  concepts jsonb NOT NULL DEFAULT '[]'::jsonb,
  uncertainty jsonb NOT NULL DEFAULT '[]'::jsonb,
  recommendation jsonb NOT NULL DEFAULT '{}'::jsonb,
  latest_review_id text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (document_id, claim_id, revision)
);
CREATE INDEX IF NOT EXISTS intelligence_candidate_match_idx ON intelligence_candidate(match_key, status);
CREATE INDEX IF NOT EXISTS intelligence_candidate_document_idx ON intelligence_candidate(document_id, created_at);

CREATE TABLE IF NOT EXISTS intelligence_candidate_revision (
  candidate_id text NOT NULL REFERENCES intelligence_candidate(candidate_id),
  revision integer NOT NULL CHECK (revision > 0),
  statement text NOT NULL,
  subject text NOT NULL,
  predicate text NOT NULL,
  value jsonb,
  evidence jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (candidate_id, revision)
);

CREATE TABLE IF NOT EXISTS intelligence_conflict (
  conflict_id text PRIMARY KEY,
  match_key text NOT NULL,
  candidate_ids jsonb NOT NULL,
  status text NOT NULL DEFAULT 'open',
  reason text NOT NULL,
  resolution text,
  resolved_by text,
  resolved_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS intelligence_review (
  review_id text PRIMARY KEY,
  candidate_id text NOT NULL REFERENCES intelligence_candidate(candidate_id),
  action text NOT NULL CHECK (action IN ('accept','reject','request-more-evidence','edit')),
  reviewer text NOT NULL,
  reason text NOT NULL,
  edited_statement text,
  expected_revision integer NOT NULL CHECK (expected_revision > 0),
  resolve_conflict text,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS intelligence_review_candidate_idx ON intelligence_review(candidate_id, created_at);

CREATE TABLE IF NOT EXISTS intelligence_version (
  version_id text PRIMARY KEY,
  version text NOT NULL UNIQUE,
  content_hash char(64) NOT NULL UNIQUE,
  candidate_ids jsonb NOT NULL,
  snapshot jsonb NOT NULL,
  published_by text NOT NULL,
  title text,
  status text NOT NULL DEFAULT 'published',
  published_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS intelligence_event (
  event_id text PRIMARY KEY,
  seq bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
  event_type text NOT NULL,
  subject_id text,
  payload jsonb NOT NULL,
  payload_sha256 char(64) NOT NULL,
  actor text,
  occurred_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS intelligence_event_subject_idx ON intelligence_event(subject_id, seq);

-- Research assessment drafts/reports are separate from trusted knowledge
-- candidates.  Publication here means an analyst-reviewed report only; it
-- does not promote claims into the static ontology package.
CREATE TABLE IF NOT EXISTS intelligence_assessment (
  assessment_id text PRIMARY KEY,
  task_id text NOT NULL,
  title text NOT NULL,
  question text NOT NULL,
  horizon_years integer NOT NULL CHECK (horizon_years BETWEEN 1 AND 30),
  revision integer NOT NULL DEFAULT 1 CHECK (revision > 0),
  status text NOT NULL DEFAULT 'under-review',
  provider text NOT NULL,
  model text,
  analysis_type text NOT NULL,
  summary jsonb NOT NULL,
  evidence_refs jsonb NOT NULL,
  source_snapshot jsonb NOT NULL,
  uncertainty jsonb NOT NULL DEFAULT '[]'::jsonb,
  warnings jsonb NOT NULL DEFAULT '[]'::jsonb,
  content_hash char(64) NOT NULL UNIQUE,
  latest_review_id text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  published_at timestamptz,
  published_by text
);
CREATE INDEX IF NOT EXISTS intelligence_assessment_task_idx ON intelligence_assessment(task_id, created_at);

CREATE TABLE IF NOT EXISTS intelligence_assessment_review (
  review_id text PRIMARY KEY,
  assessment_id text NOT NULL REFERENCES intelligence_assessment(assessment_id),
  action text NOT NULL CHECK (action IN ('accept','reject','request-more-evidence')),
  reviewer text NOT NULL,
  reason text NOT NULL,
  expected_revision integer NOT NULL CHECK (expected_revision > 0),
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS intelligence_assessment_review_idx ON intelligence_assessment_review(assessment_id, created_at);

-- Administrative withdrawal appends TrendAssessmentPublicationWithdrawn to
-- intelligence_event. API projections overlay status='withdrawn' and expose
-- publicationWithdrawal (reason, actor, createdAt, eventId); the published
-- assessment payload, content hash and original reviews remain immutable.

CREATE OR REPLACE FUNCTION intelligence_immutable_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION '% is immutable', TG_TABLE_NAME; END; $$;
DROP TRIGGER IF EXISTS intelligence_document_no_update ON intelligence_document;
CREATE TRIGGER intelligence_document_no_update BEFORE UPDATE OR DELETE ON intelligence_document
FOR EACH ROW EXECUTE FUNCTION intelligence_immutable_guard();
DROP TRIGGER IF EXISTS intelligence_event_no_update ON intelligence_event;
CREATE TRIGGER intelligence_event_no_update BEFORE UPDATE OR DELETE ON intelligence_event
FOR EACH ROW EXECUTE FUNCTION intelligence_immutable_guard();
DROP TRIGGER IF EXISTS intelligence_review_no_update ON intelligence_review;
CREATE TRIGGER intelligence_review_no_update BEFORE UPDATE OR DELETE ON intelligence_review
FOR EACH ROW EXECUTE FUNCTION intelligence_immutable_guard();
DROP TRIGGER IF EXISTS intelligence_revision_no_update ON intelligence_candidate_revision;
CREATE TRIGGER intelligence_revision_no_update BEFORE UPDATE OR DELETE ON intelligence_candidate_revision
FOR EACH ROW EXECUTE FUNCTION intelligence_immutable_guard();
DROP TRIGGER IF EXISTS intelligence_version_no_update ON intelligence_version;
CREATE TRIGGER intelligence_version_no_update BEFORE UPDATE OR DELETE ON intelligence_version
FOR EACH ROW EXECUTE FUNCTION intelligence_immutable_guard();
CREATE OR REPLACE FUNCTION intelligence_assessment_update_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF OLD.status = 'published' THEN RAISE EXCEPTION 'published assessments are immutable'; END IF;
  RETURN NEW;
END; $$;
DROP TRIGGER IF EXISTS intelligence_assessment_published_no_update ON intelligence_assessment;
CREATE TRIGGER intelligence_assessment_published_no_update BEFORE UPDATE ON intelligence_assessment
FOR EACH ROW EXECUTE FUNCTION intelligence_assessment_update_guard();
DROP TRIGGER IF EXISTS intelligence_assessment_no_delete ON intelligence_assessment;
CREATE TRIGGER intelligence_assessment_no_delete BEFORE DELETE ON intelligence_assessment
FOR EACH ROW EXECUTE FUNCTION intelligence_immutable_guard();
DROP TRIGGER IF EXISTS intelligence_assessment_review_no_update ON intelligence_assessment_review;
CREATE TRIGGER intelligence_assessment_review_no_update BEFORE UPDATE OR DELETE ON intelligence_assessment_review
FOR EACH ROW EXECUTE FUNCTION intelligence_immutable_guard();
