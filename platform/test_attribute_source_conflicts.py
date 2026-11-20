"""Alternative source claims remain separate, unverified attribute drafts."""

from copy import deepcopy
import hashlib
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

try:
    from intelligence_agent import DeterministicTrendProvider, IntelligenceAgent
    from intelligence_store import IntelligenceStore, IntelligenceValidationError
    from object_attribute_extraction import enrich_object_attributes
except ModuleNotFoundError:  # pragma: no cover
    from .intelligence_agent import DeterministicTrendProvider, IntelligenceAgent
    from .intelligence_store import IntelligenceStore, IntelligenceValidationError
    from .object_attribute_extraction import enrich_object_attributes


def source(item_id, body, **extra):
    return {"itemId": item_id, "title": "Public camera description", "body": body,
            "sourceUri": "https://" + item_id + ".example/public-camera",
            "sourceVersion": "saved-" + item_id,
            "contentHash": hashlib.sha256(body.encode()).hexdigest(),
            "publishedAt": None, "contentNature": "manufacturer-claim",
            "sourceRole": "manufacturer-disclosure", **extra}


def identity(material):
    quote = material["body"]
    ref = {key: material[key] for key in ("itemId", "sourceUri", "sourceVersion", "contentHash")}
    ref.update(quote=quote, startChar=0, endChar=len(quote))
    return {"id": "camera-x", "label": "Camera X", "type": "EquipmentModel", "evidenceIds": [material["itemId"]],
            "payload": {"extractionMethod": "llm", "confidence": "low", "reviewStatus": "pending",
                        "attributes": [], "evidenceRefs": [ref]}}


def proposal(material, value, *, name="接口", key="interface-standard"):
    return {"name": name, "attributeKey": key, "value": value,
            "itemId": material["itemId"], "quote": material["body"],
            "confidence": "high", "modelConfidence": "high", "truthVerified": True,
            "sourceVersion": "model-forged-version", "contentHash": "model-forged-hash"}


class AttributeSourceConflictTests(unittest.TestCase):
    def setUp(self):
        self.sources = [source("source-a", "Camera X uses an open interface in a public laboratory description."),
                        source("source-b", "Camera X uses a closed interface in a public laboratory description.")]
        self.node = identity(self.sources[0])

    def extract(self):
        return enrich_object_attributes(self.sources, [self.node], lambda _: {"objectId": "camera-x", "attributes": [
            proposal(self.sources[0], "open interface"), proposal(self.sources[1], "closed interface")]})["nodes"][0]

    def assert_unverified(self, attrs):
        for attr in attrs:
            self.assertFalse(attr["truthVerified"])
            self.assertEqual(attr["reviewStatus"], "pending")
            self.assertEqual(attr["independenceStatus"], "unknown")
            self.assertNotEqual(attr.get("confidence"), "high")
            self.assertNotEqual(attr.get("modelConfidence"), "high")

    def test_different_source_values_are_preserved_separately_with_own_provenance(self):
        enriched = self.extract()
        attrs = enriched["payload"]["attributes"]
        self.assertEqual([attr["name"] for attr in attrs], ["接口", "接口"])
        self.assertEqual([attr["rawValue"] for attr in attrs], ["open interface", "closed interface"])
        self.assertEqual(len({attr["assertionId"] for attr in attrs}), 2)
        self.assertEqual(enriched["payload"]["confidence"], "low")
        self.assert_unverified(attrs)
        for attr, material in zip(attrs, self.sources):
            ref = attr["evidenceRefs"][0]
            for key in ("itemId", "sourceUri", "sourceVersion", "contentHash"):
                self.assertEqual(ref[key], material[key])
            self.assertEqual(material["body"][ref["startChar"]:ref["endChar"]], ref["quote"])
        self.assertEqual(self.node["payload"]["attributes"], [])

    def test_numeric_alternative_claims_are_not_averaged_or_selected_as_truth(self):
        materials = [source("source-a", "Camera X has reported maturity stage 2 in this public description."),
                     source("source-b", "Camera X has reported maturity stage 4 in this public description.")]
        result = enrich_object_attributes(materials, [identity(materials[0])], lambda _: {"attributes": [
            proposal(materials[0], 2, name="成熟度", key="industrial-maturity"),
            proposal(materials[1], 4, name="成熟度", key="industrial-maturity")]})
        attrs = result["nodes"][0]["payload"]["attributes"]
        self.assertEqual([attr["rawValue"] for attr in attrs], [2, 4])
        self.assertNotIn(3, [attr["rawValue"] for attr in attrs])
        self.assert_unverified(attrs)

    def test_reprints_under_two_urls_do_not_verify_attribute_independence(self):
        original = self.sources[0]
        reprint = {**original, "itemId": "reprint-b", "sourceUri": "https://secondary.example/reprint",
                   "sourceVersion": "saved-reprint-b", "contentNature": "secondary-report", "sourceRole": "secondary-report"}
        result = enrich_object_attributes([original, reprint], [self.node], lambda _: {"attributes": [
            proposal(original, "open interface"), proposal(reprint, "open interface")]})
        attrs = result["nodes"][0]["payload"]["attributes"]
        self.assertGreaterEqual(len(attrs), 1)
        self.assert_unverified(attrs)
        materials = {material["itemId"]: material for material in (original, reprint)}
        for attr in attrs:
            for ref in attr["evidenceRefs"]:
                material = materials[ref["itemId"]]
                self.assertEqual(ref["sourceUri"], material["sourceUri"])
                self.assertEqual(ref["sourceVersion"], material["sourceVersion"])
                self.assertEqual(ref["contentHash"], original["contentHash"])

    def store_graph(self, store):
        draft = DeterministicTrendProvider().generate(task_id="isolated-conflict-task", title="Public camera descriptions",
            question="Which public interface descriptions remain to be verified?", horizon_years=3, materials=self.sources)
        draft["summary"]["findings"] = []
        assessment = store.create_assessment(draft)["assessment"]
        graph = store.save_graph(assessment["assessmentId"], {"nodes": [self.extract()], "edges": []})
        return assessment, graph

    def test_agent_model_failure_preserves_both_existing_source_claims(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = IntelligenceStore(Path(tmp) / "isolated.sqlite")
            assessment, before = self.store_graph(store)
            calls = []
            def failed_provider(messages, **options):
                calls.append(messages)
                raise TimeoutError("fixture-provider-error")
            agent = IntelligenceAgent(store, trend_provider=SimpleNamespace(configured=True,
                claim_provider=SimpleNamespace(model="fixture-only", _request_json=failed_provider)))
            graph = agent.build_assessment_graph(assessment["assessmentId"], {"expectedRevision": 1,
                "extractAttributes": True, "objectIds": ["camera-x"]})
            saved = next(node for node in graph["nodes"] if node["id"] == "camera-x")
            self.assertEqual(saved["payload"]["attributes"], before["nodes"][0]["payload"]["attributes"])
            self.assertEqual(saved["payload"]["attributeExtraction"]["status"], "failed")
            self.assertEqual(len(calls), 1)
            self.assertEqual(store.published_ontology()["objects"], [])

    def test_single_attribute_admission_uses_selected_source_and_requires_human_high_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = IntelligenceStore(Path(tmp) / "isolated.sqlite")
            assessment, graph = self.store_graph(store)
            candidates = store.propose_graph_admissions(assessment["assessmentId"], {
                "graphRevisionId": graph["graphRevisionId"], "graphProperties": [
                    {"graphNodeId": "camera-x", "propertyPath": "payload.attributes[1].value", "ontologyPredicate": "reportedInterface"}]})
            self.assertEqual(len(candidates), 1)
            selected = candidates[0]
            self.assertEqual(selected["assertion"]["rawValue"], "closed interface")
            self.assertEqual(selected["assertion"]["evidenceRefs"][0]["itemId"], "source-b")
            self.assertEqual(selected["assertion"]["identityEvidenceRefs"][0]["itemId"], "source-a")
            self.assertEqual(selected["mapping"]["proposedConfidence"], "low")
            self.assertEqual(store.published_ontology()["objects"], [])
            for confidence in (None, "medium", "model-high"):
                payload = {"action": "accept", "reviewer": "Isolated human reviewer", "reason": "Review source attribution and unresolved alternative claim",
                           "expectedRevision": selected["revision"]}
                if confidence is not None:
                    payload["verifiedConfidence"] = confidence
                with self.subTest(confidence=confidence), self.assertRaises(IntelligenceValidationError):
                    store.review_admission(selected["admissionId"], payload)
            after = store.get_graph(assessment["assessmentId"])
            self.assertEqual(after["nodes"][0]["payload"]["attributes"], graph["nodes"][0]["payload"]["attributes"])
            self.assertEqual(store.published_ontology()["objects"], [])


if __name__ == "__main__":
    unittest.main()
