"""Regression tests for source-bound graph and ontology admission gates."""

from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path
import sqlite3
import tempfile
import unittest

try:
    from intelligence_agent import IntelligenceAgent, DeterministicTrendProvider
    from intelligence_store import IntelligenceStore, IntelligenceConflict, IntelligenceValidationError
    from capability_analysis import normalize_capabilities
except ModuleNotFoundError:
    from .intelligence_agent import IntelligenceAgent, DeterministicTrendProvider
    from .intelligence_store import IntelligenceStore, IntelligenceConflict, IntelligenceValidationError
    from .capability_analysis import normalize_capabilities


class GovernanceStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = IntelligenceStore(Path(self.temp.name) / "intelligence.sqlite")
        self.agent = IntelligenceAgent(self.store, trend_provider=DeterministicTrendProvider())
        body = "Inspection Camera X processing rate measured at 120 images/min under laboratory lighting."
        self.sources = [{"itemId": "source-a", "title": "Camera test", "sourceUri": "https://example.test/camera", "sourceVersion": "v1", "publishedAt": "2026-08-10T00:00:00Z", "contentHash": hashlib.sha256(body.encode()).hexdigest(), "body": body}]
        self.draft = DeterministicTrendProvider().generate(task_id="task-governance", title="Camera evidence", question="What has public testing established?", horizon_years=3, materials=self.sources)
        self.draft["summary"]["capabilityAssessment"] = normalize_capabilities({"metrics": [{"equipment": "Inspection Camera X", "dimension": "throughput", "metric": "processing rate", "value": 120, "unit": "images/min", "qualifier": "exact", "basis": "reported-test", "conditions": "laboratory lighting", "assessment": "One reported test; repeatability and field conditions unknown.", "itemId": "source-a", "quote": body}]}, self.sources)
        self.assessment = self.store.create_assessment(self.draft)["assessment"]
        self.aid = self.assessment["assessmentId"]

    def tearDown(self):
        self.temp.cleanup()

    def _propose(self):
        return self.agent.propose_ontology_admissions(self.aid)[0]

    def _withdraw(self):
        accepted = self.store.review_assessment(self.aid, {"action": "accept", "reviewer": "Analyst", "reason": "Verified public evidence boundary", "expectedRevision": 1})
        self.store.publish_assessment(self.aid, {"reviewer": "Analyst", "expectedRevision": accepted["revision"]})
        self.store.withdraw_assessment_publication(self.aid, {"actor": "Analyst", "reason": "Source correction"})

    def test_unknown_finding_reference_is_rejected_at_store_boundary(self):
        tampered = deepcopy(self.draft)
        tampered["summary"]["findings"] = [{"id": "f1", "evidenceIds": ["invented-source"]}]
        with self.assertRaises(IntelligenceValidationError):
            self.store.create_assessment(tampered)

    def test_evidence_source_version_must_match_snapshot(self):
        tampered = deepcopy(self.draft)
        tampered["evidenceRefs"][0]["sourceVersion"] = "v99"
        with self.assertRaises(IntelligenceValidationError):
            self.store.create_assessment(tampered)

    def test_graph_dedup_foreign_citations_and_immutable_revisions(self):
        graph = self.agent.build_assessment_graph(self.aid)
        same = self.agent.build_assessment_graph(self.aid)
        self.assertEqual(graph["graphRevisionId"], same["graphRevisionId"])
        tampered = deepcopy(graph)
        tampered["edges"][0]["evidenceIds"] = ["foreign-source"]
        with self.assertRaises(IntelligenceValidationError):
            self.store.save_graph(self.aid, tampered)
        with self.assertRaises(sqlite3.DatabaseError), self.store._connect() as con:
            con.execute("UPDATE intelligence_graph_revision SET nodes='[]'")

    def test_admission_publication_is_separate_from_report_review(self):
        admission = self._propose()
        self.assertEqual(self.store.published_ontology()["admissionCount"], 0)
        with self.assertRaises(IntelligenceConflict):
            self.store.publish_admission(admission["admissionId"], {"reviewer": "Analyst", "expectedRevision": 1})
        accepted = self.store.review_admission(admission["admissionId"], {"action": "accept", "reviewer": "Analyst", "reason": "Checked metric meaning, conditions and source", "expectedRevision": 1})
        with self.assertRaises(IntelligenceConflict):
            self.store.publish_admission(admission["admissionId"], {"reviewer": "Analyst", "expectedRevision": 1})
        self.store.publish_admission(admission["admissionId"], {"reviewer": "Analyst", "expectedRevision": accepted["revision"]})
        trusted = self.store.published_ontology()
        self.assertEqual(trusted["admissionCount"], 1)
        self.assertEqual(len(trusted["relations"]), 1)
        self.assertEqual(trusted["provenance"][0]["evidence"], admission["assertion"]["evidence"])
        with self.assertRaises(sqlite3.DatabaseError), self.store._connect() as con:
            con.execute("UPDATE intelligence_ontology_admission SET status='accepted'")

    def test_admission_assertion_and_mapping_cannot_diverge_from_metric(self):
        admission = self._propose()
        for key, value in [("value", 999), ("equipment", "Another Camera"), ("basis", "program-target")]:
            proposal = deepcopy(admission)
            proposal["assertion"][key] = value
            with self.subTest(key=key), self.assertRaises(IntelligenceValidationError):
                self.store.create_admissions(self.aid, [proposal])
        proposal = deepcopy(admission)
        proposal["mapping"]["predicate"] = "https://example.test/not-the-ontology"
        with self.assertRaises(IntelligenceValidationError):
            self.store.create_admissions(self.aid, [proposal])
        with self.assertRaises(sqlite3.DatabaseError), self.store._connect() as con:
            con.execute("UPDATE intelligence_ontology_admission SET assertion='{}'")

    def test_admission_unknown_selection_and_bad_revision_are_rejected(self):
        with self.assertRaises(IntelligenceValidationError):
            self.agent.propose_ontology_admissions(self.aid, ["metric-1", "unknown"])
        with self.assertRaises(IntelligenceValidationError):
            self.agent.propose_ontology_admissions(self.aid, [])
        admission = self._propose()
        for revision in (True, 1.5, "1", None):
            with self.subTest(revision=revision), self.assertRaises(IntelligenceValidationError):
                self.store.review_admission(admission["admissionId"], {"action": "accept", "reviewer": "A", "reason": "R", "expectedRevision": revision})

    def test_withdrawal_invalidates_graph_and_trusted_layer_and_blocks_actions(self):
        self.agent.build_assessment_graph(self.aid)
        admission = self._propose()
        accepted = self.store.review_admission(admission["admissionId"], {"action": "accept", "reviewer": "A", "reason": "R", "expectedRevision": 1})
        self.store.publish_admission(admission["admissionId"], {"reviewer": "A", "expectedRevision": accepted["revision"]})
        self._withdraw()
        self.assertIsNone(self.store.get_graph(self.aid))
        self.assertEqual(self.store.published_ontology()["admissionCount"], 0)
        self.assertEqual(self.store.list_admissions(self.aid)[0]["status"], "withdrawn")
        self.assertEqual(len(self.store.list_graph_revisions(self.aid)), 1)
        for operation in [lambda: self.agent.build_assessment_graph(self.aid), lambda: self._propose(), lambda: self.store.save_validation_round(self.aid, {"kind": "deterministic"}), lambda: self.store.review_admission(admission["admissionId"], {"action": "accept", "reviewer": "A", "reason": "R", "expectedRevision": 2}), lambda: self.store.publish_admission(admission["admissionId"], {"reviewer": "A", "expectedRevision": 2})]:
            with self.assertRaises((IntelligenceConflict, IntelligenceValidationError)):
                operation()
        self.assertEqual(self.store.withdraw_assessment_publication(self.aid, {"actor": "A", "reason": "repeat"})["status"], "withdrawn")

    def test_deterministic_validation_cannot_fabricate_independence(self):
        first = self.agent.validate_assessment(self.aid, {"provider": "model", "model": "fake", "confidenceFactors": {"independenceVerified": True}})
        second = self.agent.validate_assessment(self.aid)
        self.assertEqual(first["provider"], "deterministic-evidence-verifier")
        self.assertIsNone(first["model"])
        self.assertFalse(first["confidenceFactors"]["independenceVerified"])
        self.assertFalse(first["confidenceFactors"]["contraryEvidenceChecked"])
        self.assertEqual(second["status"], "needs-evidence")
        self.assertEqual(second["roundNo"], 2)
        with self.assertRaises(sqlite3.DatabaseError), self.store._connect() as con:
            con.execute("DELETE FROM intelligence_validation_round")


if __name__ == "__main__":
    unittest.main()
