"""Mocked Firecrawl and DeepSeek flow for model-bound manufacturing evidence."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    from intelligence_store import IntelligenceStore
    from ontology_agent import OntologyAgent
    from ontology_workbook_import import import_workbook
    from equipment_verification import EquipmentVerificationQueue
    from manufacturing_research import ManufacturingResearchWorkflow
    from manufacturing_store import ManufacturingStore
    from intelligence_agent import DeepSeekClaimProvider
    from test_ontology_agent import fixture_workbook
except ModuleNotFoundError:  # pragma: no cover
    from .intelligence_store import IntelligenceStore
    from .ontology_agent import OntologyAgent
    from .ontology_workbook_import import import_workbook
    from .equipment_verification import EquipmentVerificationQueue
    from .manufacturing_research import ManufacturingResearchWorkflow
    from .manufacturing_store import ManufacturingStore
    from .intelligence_agent import DeepSeekClaimProvider
    from .test_ontology_agent import fixture_workbook


BODY = "Test UAV Block 1 final assembly was completed at Test Works in 2025."


class FakeFirecrawl:
    def __init__(self):
        self.query = ""
        self.keywords = []
        self.body = BODY

    def search(self, query, keywords, *, since_days, max_items, source_ids, **kwargs):
        self.query = query
        self.keywords = keywords
        return {
            "fetchedAt": "2026-10-22T00:00:00Z",
            "items": [{
                "title": "Test UAV manufacturing disclosure",
                "sourceUri": "https://example.gov/test-uav-manufacturing",
                "itemUri": "https://example.gov/test-uav-manufacturing",
                "sourceVersion": "public-v1",
                "publishedAt": "2025-01-01T00:00:00Z",
                "body": self.body,
            }],
            "errors": [],
        }


class FakeExtractor:
    configured = True
    provider = None
    model = "deepseek-test"

    def __init__(self, response=None):
        self.response = response
        self.received_sources = []

    def extract(self, entity, sources):
        self.received_sources = sources
        return self.response(entity, sources) if callable(self.response) else self.response


class ManufacturingResearchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        workbook = root / "equipment.xlsx"
        fixture_workbook(workbook)
        self.store = IntelligenceStore(root / "manufacturing.sqlite")
        import_workbook(workbook, self.store.path)
        self.search = FakeFirecrawl()
        self.agent = OntologyAgent(self.store, verification_queue=EquipmentVerificationQueue(self.store, self.search))
        self.entity_id = next(item["entityId"] for item in self.agent._entities() if item["preferredName"] == "Test UAV")
        self.extractor = FakeExtractor(self._valid_document)
        self.workflow = ManufacturingResearchWorkflow(self.agent, self.extractor)

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def _valid_document(entity, sources):
        source = sources[0]
        return {
            "schemaVersion": "uav-manufacturing-extraction.v1",
            "entity": {"entityId": entity["entityId"], "name": entity["preferredName"]},
            "observations": [{
                "category": "process",
                "configuration": "Block 1",
                "data": {"processName": "Final assembly", "processType": "assembly"},
                "evidence": {
                    "sourceUri": source["sourceUri"],
                    "snapshotId": source["snapshotId"],
                    "contentHash": source["contentHash"],
                    "sourceSubject": "Test UAV",
                    "quote": source["body"],
                },
            }],
            "gaps": [],
        }

    def _collect(self):
        return self.workflow.collect(self.entity_id)

    def test_targeted_collection_and_extraction_create_review_only_candidate(self):
        collected = self._collect()
        self.assertEqual(collected["retainedItems"], 1)
        self.assertIn("configuration composition", self.search.query)
        self.assertIn("procurement-budget", collected["verification"]["sourceScopeIds"])
        self.assertNotIn("body", collected["verification"])

        extracted = self.workflow.extract(self.entity_id)
        self.assertEqual(extracted["status"], "awaiting-human-review")
        self.assertEqual(extracted["candidateCount"], 1)
        candidate = extracted["candidates"][0]
        self.assertEqual(candidate["status"], "under-review")
        self.assertEqual(candidate["sourceSnapshotIds"], [self.extractor.received_sources[0]["snapshotId"]])
        evidence = candidate["payload"]["evidence"]
        self.assertEqual(evidence["quote"], BODY)
        self.assertEqual(evidence["sourceUri"], self.extractor.received_sources[0]["sourceUri"])
        self.assertEqual(evidence["contentHash"], self.extractor.received_sources[0]["contentHash"])
        self.assertIsNotNone(extracted.get("factorGraph"))
        self.assertEqual(extracted["factorGraph"]["summary"]["candidateCount"], 1)
        self.assertEqual(len(ManufacturingStore(self.store).get(self.entity_id)["factorGraphRuns"]), 1)

    def test_bad_model_observations_are_rejected_without_candidates(self):
        self._collect()
        valid = self._valid_document
        invalid_outputs = []

        def mutate(edit):
            def output(entity, sources):
                document = valid(entity, sources)
                edit(document, sources[0])
                return document
            invalid_outputs.append(output)

        mutate(lambda doc, source: doc["observations"][0]["evidence"].update(quote="A quote not present in the saved source."))
        mutate(lambda doc, source: doc["observations"][0]["evidence"].update(sourceUri="https://foreign.example/fake"))
        mutate(lambda doc, source: doc["observations"][0]["evidence"].update(sourceSubject="Test Loiter"))
        mutate(lambda doc, source: doc["observations"][0]["evidence"].update(snapshotId="forged-snapshot"))
        mutate(lambda doc, source: doc["observations"][0]["data"].update(processType="factory-line"))
        mutate(lambda doc, source: doc["entity"].update(entityId="another-uav"))

        for output in invalid_outputs:
            with self.subTest(output=invalid_outputs.index(output)):
                self.extractor.response = output
                try:
                    result = self.workflow.extract(self.entity_id)
                except ValueError:
                    # A mismatched root entity remains a fatal envelope error.
                    result = None
                if result is not None:
                    self.assertEqual(result["status"], "no-candidates")
                    self.assertGreaterEqual(len(result["gaps"]), 1)
                self.assertEqual(ManufacturingStore(self.store).list_candidates(self.entity_id), [])

        self.search.body = "Test Loiter Block 2 final assembly was completed at Loiter Works in 2025."
        self.workflow.collect(self.entity_id, {"force": True})
        def wrong_model_document(entity, sources):
            document = self._valid_document(entity, sources)
            document["observations"][0]["evidence"]["quote"] = sources[0]["body"]
            return document
        self.extractor.response = wrong_model_document
        # The root is valid, but this single observation is rejected. The
        # resulting batch has no usable observations and therefore does not
        # create a candidate.
        result = self.workflow.extract(self.entity_id)
        self.assertEqual(result["status"], "no-candidates")
        self.assertTrue(any("different equipment model" in gap for gap in result["gaps"]))
        self.assertEqual(ManufacturingStore(self.store).list_candidates(self.entity_id), [])

    def test_valid_observation_survives_invalid_sibling(self):
        self._collect()

        def mixed(entity, sources):
            document = self._valid_document(entity, sources)
            bad = self._valid_document(entity, sources)["observations"][0]
            bad["evidence"] = dict(bad["evidence"], quote="not present in saved source")
            document["observations"].append(bad)
            return document

        self.extractor.response = mixed
        result = self.workflow.extract(self.entity_id)
        self.assertEqual(result["status"], "awaiting-human-review")
        self.assertEqual(result["candidateCount"], 1)
        self.assertTrue(any("Observation 2 rejected" in gap for gap in result["gaps"]))

    def test_backend_fills_optional_evidence_fields_from_snapshot(self):
        self._collect()

        def snapshot_only(entity, sources):
            document = self._valid_document(entity, sources)
            evidence = document["observations"][0]["evidence"]
            evidence.pop("sourceUri")
            evidence.pop("contentHash")
            evidence.pop("sourceSubject")
            return document

        self.extractor.response = snapshot_only
        result = self.workflow.extract(self.entity_id)
        self.assertEqual(result["candidateCount"], 1)
        evidence = result["candidates"][0]["payload"]["evidence"]
        self.assertEqual(evidence["sourceUri"], self.extractor.received_sources[0]["sourceUri"])
        self.assertEqual(evidence["contentHash"], self.extractor.received_sources[0]["contentHash"])
        self.assertEqual(evidence["sourceSubject"], "Test UAV")

    def test_deepseek_unconfigured_does_not_create_candidates(self):
        self._collect()
        self.extractor.configured = False
        result = self.workflow.extract(self.entity_id)
        self.assertEqual(result["status"], "model-unavailable")
        self.assertEqual(result["candidateCount"], 0)
        self.assertEqual(ManufacturingStore(self.store).list_candidates(self.entity_id), [])

    def test_extraction_uses_only_the_latest_recollection_snapshots(self):
        first = self._collect()
        first_snapshot = first["verification"]["snapshotIds"][0]
        self.search.body = "Test UAV Block 2 deliveries began in 2026."
        latest = self.workflow.collect(self.entity_id, {"force": True})
        latest_snapshot = latest["verification"]["snapshotIds"][0]
        self.assertNotEqual(first_snapshot, latest_snapshot)

        result = self.workflow.extract(self.entity_id)
        self.assertEqual(result["candidateCount"], 1)
        self.assertEqual([source["snapshotId"] for source in self.extractor.received_sources], [latest_snapshot])
        self.assertEqual(result["candidates"][0]["sourceSnapshotIds"], [latest_snapshot])

    def test_deepseek_transport_receives_bounded_saved_source_material(self):
        provider = DeepSeekClaimProvider(api_key="unit-test-key")
        entity = {"entityId": "equipment-test", "preferredName": "Test UAV"}
        sources = [{"snapshotId": "snapshot-1", "contentHash": "sha256:test-source", "sourceUri": "https://example.gov/uav", "body": BODY}]
        with patch.object(provider, "_request_json", return_value={"observations": []}) as request:
            self.assertEqual(provider.extract_manufacturing(entity, sources), {"observations": []})
        messages = request.call_args.args[0]
        self.assertIn("untrusted data", messages[0]["content"])
        self.assertIn("entity must contain exactly entityId and name", messages[0]["content"])
        self.assertIn("gaps is an array of plain strings", messages[0]["content"])
        self.assertIn("Never return an alternate facts/candidates format", messages[0]["content"])
        self.assertIn("snapshot-1", messages[1]["content"])
        self.assertIn("sha256:test-source", messages[1]["content"])
        self.assertIn(BODY, messages[1]["content"])
        self.assertFalse(request.call_args.kwargs["thinking"])


if __name__ == "__main__":
    unittest.main()
