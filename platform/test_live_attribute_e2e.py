"""Validate the read-only live replay script with a saved public-source fixture."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

try:
    from intelligence_agent import DeterministicTrendProvider
    from intelligence_store import IntelligenceStore
    from object_attribute_extraction import enrich_object_attributes
    from verify_attribute_live_e2e import verify_live_attribute
except ModuleNotFoundError:  # pragma: no cover
    from .intelligence_agent import DeterministicTrendProvider
    from .intelligence_store import IntelligenceStore
    from .object_attribute_extraction import enrich_object_attributes
    from .verify_attribute_live_e2e import verify_live_attribute


class LiveAttributeE2ETests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.database = Path(self.temp.name) / "fake-live.sqlite"
        self.store = IntelligenceStore(self.database)
        fixture = json.loads((Path(__file__).resolve().parents[1] / "docs/fixtures/osint-uav-attributes-baseline-20261008.json").read_text(encoding="utf-8"))
        self.source = fixture["selectedMaterial"]
        proposal = fixture["knownExtractableAttributes"][0]
        draft = DeterministicTrendProvider().generate(task_id="isolated-replay-source", title="Public architecture",
            question="What architecture is publicly stated?", horizon_years=3, materials=[self.source])
        self.assessment = self.store.create_assessment(draft)["assessment"]
        self.node = {"id": "mission-system", "label": "MARS Mission System", "type": "Technology",
                     "evidenceIds": [self.source["itemId"]], "payload": {"extractionMethod": "llm", "attributes": [],
                     "evidenceRefs": [deepcopy(proposal["evidence"])], "model": "local-fixture-no-model"}}
        self.response = {"attributes": [{"attributeKey": "architecture", "name": "架构描述", "value": proposal["rawValue"],
                                         "displayValue": proposal["displayValue"], "itemId": self.source["itemId"], "quote": proposal["evidence"]["quote"]}]}

    def test_isolated_replay_gates_and_kb_readback_leave_source_database_unpublished(self):
        enriched = enrich_object_attributes([self.source], [self.node], lambda _: self.response)
        graph = self.store.save_graph(self.assessment["assessmentId"], {"nodes": enriched["nodes"], "edges": []})
        before_report, before_graph = self.store.get_assessment(self.assessment["assessmentId"]), self.store.get_graph(self.assessment["assessmentId"])
        result = verify_live_attribute({"assessmentId": self.assessment["assessmentId"], "graphRevisionId": graph["graphRevisionId"], "status": "test-fixture"}, database=self.database)
        self.assertEqual(result["status"], "passed-isolated-e2e")
        self.assertEqual(len(result["checks"]), 8)
        self.assertIn("knowledge-base-boot-overlay-value-and-evidence-readable", {check["code"] for check in result["checks"]})
        self.assertTrue(result["testOnly"])
        self.assertTrue(result["humanReviewSimulated"])
        self.assertFalse(result["truthVerified"])
        self.assertFalse(result["realPublicationPerformed"])
        self.assertEqual(result["origin"]["rawValue"], self.response["attributes"][0]["value"])
        self.assertEqual(result["isolatedResult"]["admissionCount"], 2)
        self.assertEqual(self.store.list_admissions(), [])
        self.assertEqual(self.store.published_ontology()["admissionCount"], 0)
        self.assertEqual(self.store.get_assessment(self.assessment["assessmentId"]), before_report)
        self.assertEqual(self.store.get_graph(self.assessment["assessmentId"]), before_graph)

    def test_missing_live_attributes_is_waiting_and_never_fills_fixture_values(self):
        graph = self.store.save_graph(self.assessment["assessmentId"], {"nodes": [self.node], "edges": []})
        result = verify_live_attribute({"assessmentId": self.assessment["assessmentId"], "graphRevisionId": graph["graphRevisionId"]}, database=self.database)
        self.assertEqual(result["status"], "waiting-for-live-attributes")
        self.assertEqual(result["checks"], [])
        self.assertNotIn("isolatedResult", result)
        self.assertEqual(self.store.list_admissions(), [])

    def test_valid_architecture_remains_individually_replayable_with_bad_historical_field(self):
        enriched = enrich_object_attributes([self.source], [self.node], lambda _: self.response)
        saved_node = enriched["nodes"][0]
        # A literal architecture value was historically mislabeled as human
        # oversight. Keep that draft alongside the independently valid one.
        saved_node["payload"]["attributes"].insert(0, {
            "name": "人工监督", "attributeKey": "human-oversight", "value": self.response["attributes"][0]["value"],
            "evidenceRefs": deepcopy(saved_node["payload"]["attributes"][0]["evidenceRefs"])})
        graph = self.store.save_graph(self.assessment["assessmentId"], {"nodes": [saved_node], "edges": []})
        before_graph = deepcopy(graph)
        result = verify_live_attribute({"assessmentId": self.assessment["assessmentId"], "graphRevisionId": graph["graphRevisionId"]}, database=self.database)
        self.assertEqual(result["status"], "passed-isolated-e2e")
        self.assertEqual(result["origin"]["propertyPath"], "payload.attributes[1].value")
        self.assertTrue(result["origin"]["predicate"].endswith("#reportedArchitecture"))
        self.assertEqual(self.store.get_graph(self.assessment["assessmentId"]), before_graph)
        self.assertEqual(self.store.list_admissions(), [])

    def test_only_misclassified_historical_field_cannot_be_chosen_for_replay(self):
        enriched = enrich_object_attributes([self.source], [self.node], lambda _: self.response)
        enriched["nodes"][0]["payload"]["attributes"][0].update(name="人工监督", attributeKey="human-oversight")
        graph = self.store.save_graph(self.assessment["assessmentId"], {"nodes": enriched["nodes"], "edges": []})
        result = verify_live_attribute({"assessmentId": self.assessment["assessmentId"], "graphRevisionId": graph["graphRevisionId"]}, database=self.database)
        self.assertEqual(result["status"], "waiting-for-live-attributes")
        self.assertNotIn("isolatedResult", result)
        self.assertEqual(self.store.list_admissions(), [])


if __name__ == "__main__":
    unittest.main()
