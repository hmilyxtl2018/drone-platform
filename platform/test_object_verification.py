"""Verify that checks address ontology targets and never fabricate trusted facts."""

from copy import deepcopy
import hashlib
from pathlib import Path
import tempfile
import unittest

try:
    from capability_analysis import normalize_capabilities
    from object_verification import attach_object_verification, build_object_verification_checks
    from intelligence_agent import DeterministicTrendProvider, IntelligenceAgent
    from intelligence_store import IntelligenceStore
except ModuleNotFoundError:
    from .capability_analysis import normalize_capabilities
    from .object_verification import attach_object_verification, build_object_verification_checks
    from .intelligence_agent import DeterministicTrendProvider, IntelligenceAgent
    from .intelligence_store import IntelligenceStore


class ObjectVerificationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = IntelligenceStore(Path(self.temp.name) / "verification.sqlite")
        self.agent = IntelligenceAgent(self.store, trend_provider=DeterministicTrendProvider())
        body = "😀 Acme manufactures Camera X.\nCamera X reportedly carried 4 kg in one public test."
        self.source = {"itemId": "source-a", "title": "Public test record",
                       "sourceUri": "https://example.test/test", "sourceVersion": "v1",
                       "publishedAt": None, "contentHash": hashlib.sha256(body.encode()).hexdigest(),
                       "body": body}
        draft = DeterministicTrendProvider().generate(task_id="test", title="Camera research",
                                                     question="What is publicly reported?", horizon_years=3,
                                                     materials=[self.source])
        self.assessment = self.store.create_assessment(draft)["assessment"]
        quote = "Acme manufactures Camera X."
        ref = {key: self.source[key] for key in ("itemId", "sourceUri", "sourceVersion", "contentHash")}
        ref.update(quote=quote, startChar=2, endChar=2 + len(quote))
        def node(nid, kind, label, **extra):
            return {"id": nid, "type": kind, "label": label, "evidenceIds": ["source-a"],
                    "payload": {"extractionMethod": "llm", "confidence": "low", "evidenceRefs": [deepcopy(ref)], **extra}}
        self.graph_data = {"nodes": [node("org", "Organization", "Acme"), node("camera", "EquipmentModel", "Camera X"),
                                     node("test", "EventRecord", "Public test", eventKind="TestEvent", date="2026")],
                           "edges": [{"id": "manufactures", "from": "org", "to": "camera", "relation": "manufactures",
                                      "evidenceIds": ["source-a"], "payload": {"extractionMethod": "llm", "evidenceRefs": [deepcopy(ref)]}}]}
        self.graph = self.store.save_graph(self.assessment["assessmentId"], self.graph_data)

    def tearDown(self):
        self.temp.cleanup()

    def test_valid_quote_and_mapping_link_exact_target_without_claiming_truth(self):
        checks = build_object_verification_checks(self.assessment, self.graph)
        org = next(c for c in checks if c["graphElementId"] == "org")
        self.assertEqual(org["graphRevisionId"], self.graph["graphRevisionId"])
        self.assertEqual(org["machineStatus"], "passed")
        self.assertEqual(org["status"], "needs-evidence")
        self.assertFalse(org["truthVerified"])
        self.assertFalse(org["humanReviewRecorded"])
        self.assertTrue(org["eligibleForHumanReview"])
        self.assertEqual(org["evidenceRefs"][0]["startChar"], 2)
        self.assertEqual(org["ontologyClass"], "https://local.ontology/coast-raid-k#Organization")
        event = next(c for c in checks if c["targetKind"] == "event")
        self.assertTrue(event["ontologyClass"].endswith("#TestEvent"))
        self.assertEqual(event["unverifiedPropertyPaths"], ["payload.date"])

    def test_quote_tampering_version_drift_and_unsupported_identity_fail_the_target(self):
        for field, value in [("quote", "fabricated"), ("sourceVersion", "v2"), ("startChar", 0)]:
            graph = deepcopy(self.graph)
            graph["nodes"][0]["payload"]["evidenceRefs"][0][field] = value
            with self.subTest(field=field):
                org = build_object_verification_checks(self.assessment, graph)[0]
                self.assertEqual(org["machineStatus"], "failed")
                self.assertFalse(org["eligibleForHumanReview"])
        graph = deepcopy(self.graph)
        graph["nodes"][0]["label"] = "Other company"
        self.assertEqual(build_object_verification_checks(self.assessment, graph)[0]["machineStatus"], "failed")

    def test_relation_range_checked_and_context_link_cannot_be_admitted(self):
        relation = build_object_verification_checks(self.assessment, self.graph)[-1]
        self.assertTrue(relation["ontologyPredicate"].endswith("#manufacturedBy"))
        self.assertEqual(relation["endpointNodeIds"], ["org", "camera"])
        graph = deepcopy(self.graph)
        graph["edges"][0]["from"], graph["edges"][0]["to"] = "camera", "org"
        relation = build_object_verification_checks(self.assessment, graph)[-1]
        self.assertEqual(relation["machineStatus"], "failed")
        graph["edges"][0]["relation"] = "relatedTo"
        relation = build_object_verification_checks(self.assessment, graph)[-1]
        self.assertEqual(relation["machineStatus"], "needs-evidence")
        self.assertFalse(relation["eligibleForHumanReview"])

    def test_candidate_links_bind_revision_and_only_explicit_human_review_is_reported(self):
        aid = self.assessment["assessmentId"]
        candidate = self.store.propose_graph_admissions(aid, {"graphRevisionId": self.graph["graphRevisionId"], "graphNodeIds": ["org"]})[0]
        current = build_object_verification_checks(self.assessment, self.graph, self.store.list_admissions(aid))[0]
        self.assertEqual(current["admissionIds"], [candidate["admissionId"]])
        self.assertFalse(current["humanReviewRecorded"])
        self.store.review_admission(candidate["admissionId"], {"action": "accept", "reviewer": "isolated tester",
                                                             "reason": "Verified identity against original research record",
                                                             "expectedRevision": 1, "verifiedConfidence": "high"})
        current = build_object_verification_checks(self.assessment, self.graph, self.store.list_admissions(aid))[0]
        self.assertTrue(current["humanReviewRecorded"])
        self.assertFalse(current["truthVerified"])
        changed = deepcopy(self.graph_data)
        changed["nodes"][0]["payload"]["confidence"] = "medium"
        graph = self.store.save_graph(aid, changed)
        current = build_object_verification_checks(self.assessment, graph, self.store.list_admissions(aid))[0]
        self.assertEqual(current["admissionIds"], [])
        self.assertFalse(current["humanReviewRecorded"])

    def test_graph_cross_report_and_missing_extraction_are_explicit(self):
        self.assertEqual(build_object_verification_checks(self.assessment, None)[0]["code"], "ontology-coverage")
        self.assertEqual(build_object_verification_checks(self.assessment, {**self.graph, "assessmentId": "foreign"})[0]["status"], "failed")

    def test_agent_validation_persists_linked_checks_without_mutating_graph_or_confidence(self):
        aid = self.assessment["assessmentId"]
        before_graph = deepcopy(self.store.get_graph(aid))
        before_assessment = deepcopy(self.store.get_assessment(aid))
        round_record = self.agent.validate_assessment(aid)
        targets = [c for c in round_record["checks"] if c.get("targetKind") in {"object", "event", "relation"}]
        self.assertEqual(len(targets), 4)
        self.assertTrue(all(c["graphRevisionId"] == self.graph["graphRevisionId"] for c in targets))
        stored = self.store.list_validation_rounds(aid)[0]
        self.assertEqual(stored["checks"], round_record["checks"])
        self.assertFalse(stored["confidenceFactors"]["independenceVerified"])
        self.assertEqual(self.store.get_graph(aid), before_graph)
        self.assertEqual(self.store.get_assessment(aid), before_assessment)

    def test_capability_checks_target_exact_metric_and_data_basis_without_rank(self):
        capabilities = normalize_capabilities({"metrics": [{"itemId": "source-a", "quote": "Camera X reportedly carried 4 kg in one public test.",
                                                            "equipment": "Camera X", "dimension": "payload", "metric": "payload mass",
                                                            "value": 4, "unit": "kg", "qualifier": "exact", "basis": "reported-test",
                                                            "conditions": "One public test; sample size unknown", "assessment": "Reported result; not independent validation"}]}, [self.source])
        self.assertEqual(len(capabilities["metrics"]), 1)
        assessment = deepcopy(self.assessment)
        assessment["summary"]["capabilityAssessment"] = capabilities
        target = build_object_verification_checks(assessment, self.graph)[-1]
        self.assertEqual(target["graphElementId"], "capability:metric-1")
        self.assertEqual(target["machineStatus"], "passed")
        self.assertEqual(target["dataBasis"], "reported-test")
        capabilities["metrics"][0]["value"] = 40
        self.assertEqual(build_object_verification_checks(assessment, self.graph)[-1]["machineStatus"], "failed")

    def test_model_critique_links_findings_as_context_without_validating_objects(self):
        assessment = deepcopy(self.assessment)
        assessment["summary"]["findings"] = [{"id": "F1", "evidenceIds": ["source-a"]}]
        graph = deepcopy(self.graph)
        graph["nodes"].append({"id": "finding:F1", "type": "TrendHypothesis", "evidenceIds": ["source-a"], "payload": {}})
        original = {"kind": "model-critique", "status": "needs-evidence", "checks": [{"code": "model-critique:F1", "status": "needs-evidence", "detail": "Possible support", "evidenceRefs": [{"itemId": "source-a", "quote": "Acme manufactures Camera X."}]}],
                    "confidenceFactors": {"independenceVerified": False}}
        result = attach_object_verification(original, assessment, graph)
        finding_check = result["checks"][0]
        self.assertEqual(finding_check["graphElementId"], "finding:F1")
        self.assertEqual(finding_check["linkBasis"], "shared-source-context-only")
        self.assertEqual(set(finding_check["contextGraphNodeIds"]), {"org", "camera", "test"})
        self.assertFalse(finding_check["truthVerified"])
        self.assertNotIn("findingId", original["checks"][0])
        coverage = result["confidenceFactors"]["objectVerificationCoverage"]
        self.assertEqual(coverage["targetCount"], 4)
        self.assertEqual(coverage["machinePassedCount"], 4)
        self.assertFalse(coverage["truthVerified"])

    def test_critique_context_does_not_expand_to_unreviewed_finding_sources(self):
        assessment = deepcopy(self.assessment)
        assessment["summary"]["findings"] = [{"id": "F1", "evidenceIds": ["source-a", "source-unreviewed"]}]
        graph = deepcopy(self.graph)
        graph["nodes"].append({"id": "unreviewed-object", "type": "Organization", "label": "Other",
                               "evidenceIds": ["source-unreviewed"], "payload": {"extractionMethod": "llm"}})
        original = {"kind": "model-critique", "status": "needs-evidence", "checks": [
            {"code": "model-critique:F1", "status": "needs-evidence", "evidenceIds": ["source-a"],
             "evidenceRefs": [{"itemId": "source-a", "quote": "Acme manufactures Camera X."}]}]}
        check = attach_object_verification(original, assessment, graph)["checks"][0]
        self.assertEqual(check["evidenceIds"], ["source-a"])
        self.assertNotIn("unreviewed-object", check["contextGraphNodeIds"])
        legacy = deepcopy(original)
        legacy["checks"][0].pop("evidenceRefs")
        legacy["checks"][0].pop("evidenceIds")
        check = attach_object_verification(legacy, assessment, graph)["checks"][0]
        self.assertEqual(check["contextGraphNodeIds"], [])


if __name__ == "__main__":
    unittest.main()
