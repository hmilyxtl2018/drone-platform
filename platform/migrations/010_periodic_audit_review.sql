-- Periodic ontology audit and item-level human review.
-- The audit result is append-only; findings move through a separate review
-- ledger so an automated check can never masquerade as a human decision.
ALTER TABLE intelligence_ontology_audit ADD COLUMN run_no INTEGER NOT NULL DEFAULT 1;
ALTER TABLE intelligence_ontology_audit ADD COLUMN trigger TEXT NOT NULL DEFAULT 'periodic';
ALTER TABLE intelligence_ontology_audit ADD COLUMN assessment_revision INTEGER;
ALTER TABLE intelligence_ontology_audit ADD COLUMN graph_revision_id TEXT;
ALTER TABLE intelligence_ontology_audit ADD COLUMN completed_at TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS intelligence_ontology_audit_run_idx
  ON intelligence_ontology_audit(assessment_id, run_no);

CREATE TABLE IF NOT EXISTS intelligence_audit_finding (
  finding_id TEXT PRIMARY KEY,
  audit_id TEXT NOT NULL REFERENCES intelligence_ontology_audit(audit_id),
  assessment_id TEXT NOT NULL REFERENCES intelligence_assessment(assessment_id),
  target_type TEXT NOT NULL,
  target_id TEXT NOT NULL,
  code TEXT NOT NULL,
  severity TEXT NOT NULL CHECK (severity IN ('info','low','medium','high','critical')),
  state TEXT NOT NULL DEFAULT 'open' CHECK (state IN ('open','needs-evidence','confirmed','dismissed','resolved')),
  message TEXT NOT NULL,
  evidence_refs TEXT NOT NULL DEFAULT '[]',
  expected_revision INTEGER NOT NULL DEFAULT 1,
  revision INTEGER NOT NULL DEFAULT 1,
  latest_review_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(audit_id, target_type, target_id, code)
);
CREATE INDEX IF NOT EXISTS intelligence_audit_finding_run_idx
  ON intelligence_audit_finding(audit_id, state, severity);
CREATE INDEX IF NOT EXISTS intelligence_audit_finding_assessment_idx
  ON intelligence_audit_finding(assessment_id, state, created_at);

CREATE TABLE IF NOT EXISTS intelligence_audit_finding_review (
  audit_review_id TEXT PRIMARY KEY,
  finding_id TEXT NOT NULL REFERENCES intelligence_audit_finding(finding_id),
  action TEXT NOT NULL CHECK (action IN ('confirm','dismiss','request-more-evidence','reopen')),
  reviewer TEXT NOT NULL,
  reason TEXT NOT NULL,
  evidence_refs TEXT NOT NULL DEFAULT '[]',
  expected_revision INTEGER NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS intelligence_audit_finding_review_idx
  ON intelligence_audit_finding_review(finding_id, created_at);

CREATE TRIGGER IF NOT EXISTS intelligence_audit_finding_review_no_update
BEFORE UPDATE ON intelligence_audit_finding_review BEGIN
  SELECT RAISE(ABORT, 'audit finding reviews are immutable');
END;
CREATE TRIGGER IF NOT EXISTS intelligence_audit_finding_review_no_delete
BEFORE DELETE ON intelligence_audit_finding_review BEGIN
  SELECT RAISE(ABORT, 'audit finding reviews are retained');
END;
CREATE TRIGGER IF NOT EXISTS intelligence_audit_finding_no_delete
BEFORE DELETE ON intelligence_audit_finding BEGIN
  SELECT RAISE(ABORT, 'audit findings are retained');
END;
