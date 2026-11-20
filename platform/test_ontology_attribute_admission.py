"""Exercise individual properties through evidence, review and KB publication."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from jsonschema import Draft202012Validator

try:
    from intelligence_agent import DeterministicTrendProvider
    from intelligence_store import IntelligenceStore, IntelligenceConflict, IntelligenceValidationError
    from intelligence_ontology import attach_intelligence_ontology
    from ontology_attribute_admission import ATTRIBUTE_PREDICATES, get_attribute_admission_catalog
    from object_verification import build_object_verification_checks
except ModuleNotFoundError:
    from .intelligence_agent import DeterministicTrendProvider
    from .intelligence_store import IntelligenceStore, IntelligenceConflict, IntelligenceValidationError
    from .intelligence_ontology import attach_intelligence_ontology
    from .ontology_attribute_admission import ATTRIBUTE_PREDICATES, get_attribute_admission_catalog
    from .object_verification import build_object_verification_checks

ROOT = Path(__file__).resolve().parents[1] / "ontology" / "coast-raid-k"
NS = "https://local.ontology/coast-raid-k#"


class OntologyAttributeAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = IntelligenceStore(Path(self.tmp.name) / "properties.sqlite")
        quote = "Camera X used an open interface under human oversight during a controlled laboratory test on 12 June 2026."
        body = "😀 " + quote + "\nCamera Y has a closed interface."
        self.source = {"itemId": "source-a", "title": "Public laboratory record", "sourceUri": "https://example.test/laboratory",
                       "sourceVersion": "v1", "contentHash": hashlib.sha256(body.encode()).hexdigest(), "body": body}
        draft = DeterministicTrendProvider().generate(task_id="property-task", title="Public camera descriptions",
                                                     question="What descriptions are source supported?", horizon_years=3, materials=[self.source])
        self.assessment = self.store.create_assessment(draft)["assessment"]
        self.aid = self.assessment["assessmentId"]
        self.ref = {key: self.source[key] for key in ("itemId", "sourceUri", "sourceVersion", "contentHash")}
        self.ref.update(quote=quote, startChar=2, endChar=2 + len(quote))
        self.data = {"nodes": [
            {"id": "camera", "type": "EquipmentModel", "label": "Camera X", "evidenceIds": ["source-a"],
             "payload": {"extractionMethod": "llm", "reviewStatus": "pending", "confidence": "low", "evidenceRefs": [deepcopy(self.ref)],
                         "attributes": [{"name": "接口", "value": "open interface", "displayValue": "开放接口",
                                         "conditions": "controlled laboratory test", "validTime": "12 June 2026",
                                         "reviewRecommendation": {"action": "consider", "reason": "Literal description; independent checks still needed.", "truthVerified": False},
                                         "evidenceRefs": [deepcopy(self.ref)]},
                                        {"name": "监督方式", "value": "human oversight", "evidenceRefs": [deepcopy(self.ref)]}]}},
            {"id": "test", "type": "EventRecord", "label": "Public camera test", "evidenceIds": ["source-a"],
             "payload": {"extractionMethod": "llm", "eventKind": "TestEvent", "evidenceRefs": [deepcopy(self.ref)],
                         "date": "12 June 2026", "dateEvidenceRefs": [deepcopy(self.ref)]}},
        ], "edges": []}
        self.graph = self.store.save_graph(self.aid, self.data)
        self.schema = json.loads((ROOT / "schema.intelligence.json").read_text("utf-8"))

    def validate(self, definition, value):
        Draft202012Validator({"$ref": "#/$defs/" + definition, "$defs": self.schema["$defs"]}).validate(value)

    def propose(self, *, path="payload.attributes[0].value", node="camera", predicate=None, owners=False):
        selection = {"graphNodeId": node, "propertyPath": path}
        if predicate is not None:
            selection["ontologyPredicate"] = predicate
        result = self.store.propose_graph_admissions(self.aid, {"graphRevisionId": self.graph["graphRevisionId"],
                                                              "graphNodeIds": [node] if owners else [], "graphProperties": [selection]})
        for candidate in result:
            self.validate("OntologyAdmission", candidate)
        return result

    def review(self, candidate, action="accept", **changes):
        payload = {"action": action, "reviewer": "Domain reviewer", "reason": "Checked literal value, subject attribution, source limits and normalized wording",
                   "expectedRevision": candidate["revision"]}
        if action == "accept":
            payload["verifiedConfidence"] = "high"
        payload.update(changes)
        result = self.store.review_admission(candidate["admissionId"], payload)
        self.validate("OntologyAdmission", result)
        return result

    def publish(self, candidate):
        return self.store.publish_admission(candidate["admissionId"], {"reviewer": "Knowledge publisher", "expectedRevision": candidate["revision"]})

    def test_property_is_independent_and_published_with_readable_evidence(self):
        before = self.store.get_assessment(self.aid)
        owner, prop = self.propose(owners=True)
        self.assertEqual(owner["assertion"]["attributes"], {})
        self.assertEqual(prop["kind"], "graph-property")
        self.assertEqual(prop["assertion"]["rawValue"], "open interface")
        self.assertEqual(prop["assertion"]["normalizedValue"], "开放接口")
        self.assertFalse(prop["assertion"]["reviewRecommendation"]["truthVerified"])
        self.assertEqual(prop["assertion"]["evidenceRefs"][0]["startChar"], 2)
        self.assertEqual(prop["assertion"]["validTime"], "12 June 2026")
        with self.assertRaises(IntelligenceConflict):
            self.publish(prop)
        for confidence in (None, "medium", "model-high"):
            with self.subTest(confidence=confidence), self.assertRaises(IntelligenceValidationError):
                self.review(prop, verifiedConfidence=confidence)
        accepted = self.review(prop)
        with self.assertRaises(IntelligenceConflict):
            self.publish(accepted)
        self.publish(self.review(owner))
        self.assertEqual(self.store.published_ontology()["objects"][0]["attributes"], {})
        published = self.publish(accepted)
        self.validate("OntologyAdmission", published)
        layer = self.store.published_ontology()
        objects = {obj["type"]: obj for obj in layer["objects"]}
        self.assertEqual(objects["EquipmentModel"]["attributes"]["reportedInterface"], ["open interface"])
        record = objects["EquipmentModel"]["admittedProperties"][0]
        self.assertEqual(record["propertyPath"], "payload.attributes[0].value")
        self.assertEqual(record["evidenceRefs"][0]["contentHash"], self.source["contentHash"])
        self.assertTrue(record["normalizedValueVerified"])
        for obj in layer["objects"]:
            self.validate("PublishedIntelligenceObject", obj)
        boot = attach_intelligence_ontology({"files": {"tbox.json": {"classes": []}}, "inventory": {"entries": []}}, layer, ROOT)
        self.assertEqual(boot["intelligenceOntology"]["propertyCount"], 1)
        self.assertEqual(boot["files"]["intelligence-ontology.json"]["provenance"][-1]["propertyPath"], record["propertyPath"])
        self.assertEqual(self.store.get_assessment(self.aid), before)

    def test_modify_retains_original_and_requires_new_review(self):
        owner, original = self.propose(owners=True)
        amended = self.review(original, "modify", editedProperty={"rawValue": "interface", "normalizedValue": "接口"})
        self.assertNotEqual(original["admissionId"], amended["admissionId"])
        self.assertEqual(amended["revision"], 1)
        self.assertEqual(amended["status"], "under-review")
        self.assertEqual(amended["mapping"]["supersedesAdmissionId"], original["admissionId"])
        old = next(p for p in self.store.list_admissions(self.aid) if p["admissionId"] == original["admissionId"])
        self.assertEqual(old["assertion"], original["assertion"])
        self.assertEqual(old["supersededByAdmissionId"], amended["admissionId"])
        self.assertEqual(old["review"]["action"], "modify")
        self.validate("OntologyAdmission", old)
        with self.assertRaises(IntelligenceConflict):
            self.review(old)
        with self.assertRaises(IntelligenceConflict):
            self.publish(amended)
        self.publish(self.review(owner))
        self.publish(self.review(amended))
        values = [o for o in self.store.published_ontology()["objects"] if o["type"] == "AttributeObservation"]
        self.assertEqual(values[0]["assertion"]["rawValue"], "interface")
        self.assertEqual(len(values), 1)
        with self.store._connect() as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM intelligence_ontology_attribute_amendment").fetchone()[0], 1)
            with self.assertRaises(sqlite3.DatabaseError):
                con.execute("UPDATE intelligence_ontology_admission SET assertion='{}' WHERE admission_id=?", (original["admissionId"],))

    def test_more_evidence_reject_and_modification_cannot_bypass_evidence(self):
        prop = self.propose()[0]
        pending = self.review(prop, "request-more-evidence")
        self.assertEqual(pending["status"], "needs-evidence")
        rejected = self.review(pending, "reject")
        self.assertEqual(rejected["status"], "rejected")
        for edited in ({"rawValue": "fully autonomous"}, {"unit": "kg"}, {"conditions": "all environments"},
                       {"validTime": "2027"}, {"ontologyPredicate": "manufacturingParameter"},
                       {"evidenceRefs": [{**self.ref, "startChar": 0}]}):
            with self.subTest(edited=edited), self.assertRaises(IntelligenceValidationError):
                self.review(rejected, "modify", editedProperty=edited)
        with self.assertRaises(IntelligenceValidationError):
            self.review(rejected, editedProperty={"normalizedValue": "unsupported silent edit"})
        self.assertEqual(len(self.store.list_admissions(self.aid)), 1)

    def test_foreign_subject_and_unlisted_property_are_rejected(self):
        changes = deepcopy(self.data)
        changes["nodes"][0]["payload"]["attributes"][0].update(value="closed interface", evidenceRefs=[{**self.ref, "quote": "Camera Y has a closed interface.", "startChar": len(self.source["body"]) - len("Camera Y has a closed interface."), "endChar": len(self.source["body"])}])
        self.graph = self.store.save_graph(self.aid, changes)
        with self.assertRaises(IntelligenceValidationError):
            self.propose()
        for path in ("payload.unknown", "payload.attributes[4].value", "payload.attributes[0].conditions"):
            with self.subTest(path=path), self.assertRaises(IntelligenceValidationError):
                self.propose(path=path)
        with self.assertRaises(IntelligenceValidationError):
            self.propose(predicate="reportedHumanOversight")

    def test_dates_and_graph_revision_guards(self):
        event_date = self.propose(node="test", path="payload.date")[0]
        self.assertEqual(event_date["assertion"]["predicate"], NS + "reportedEventDate")
        self.assertEqual(event_date["assertion"]["validTime"], "12 June 2026")
        accepted = self.review(event_date)
        prior = self.graph["graphRevisionId"]
        changed = deepcopy(self.data)
        changed["nodes"][0]["payload"]["confidence"] = "medium"
        self.graph = self.store.save_graph(self.aid, changed, expected_graph_revision=prior)
        for operation in (lambda: self.review(event_date), lambda: self.publish(accepted),
                          lambda: self.store.save_graph(self.aid, self.data, expected_graph_revision=prior),
                          lambda: self.store.save_graph(self.aid, self.data, expected_graph_revision=None)):
            with self.assertRaises(IntelligenceConflict):
                operation()
        self.assertTrue(self.store.list_admissions(self.aid)[0]["staleGraph"])

    def test_catalog_vocabulary_and_schema_are_consistent(self):
        Draft202012Validator.check_schema(self.schema)
        vocabulary = json.loads((ROOT / "tbox.json").read_text("utf-8"))
        declared = {item["id"]: item for item in vocabulary["dataProperties"]}
        catalog = get_attribute_admission_catalog()
        self.assertEqual({row["id"] for row in catalog}, set(ATTRIBUTE_PREDICATES))
        for row in catalog:
            self.assertEqual(declared[row["id"]]["allowedSubjectClasses"], row["domains"])
        self.assertIn("AttributeObservation", {item["id"] for item in vocabulary["classes"]})
        self.assertIn("hasAttributeObservation", {item["id"] for item in vocabulary["objectProperties"]})

    def test_property_acceptance_does_not_accept_object_or_other_attributes_and_stales(self):
        accepted = self.review(self.propose()[0])
        checks = build_object_verification_checks(self.assessment, self.graph, self.store.list_admissions(self.aid))
        camera = next(row for row in checks if row.get("graphElementId") == "camera")
        self.assertFalse(camera["humanReviewRecorded"])
        first, second = camera["propertyEvidence"]
        self.assertTrue(first["humanReviewRecorded"])
        self.assertEqual(first["admissionIds"], [accepted["admissionId"]])
        self.assertFalse(first["truthVerified"])
        self.assertFalse(second["humanReviewRecorded"])
        changed = deepcopy(self.data)
        changed["nodes"][0]["payload"]["confidence"] = "medium"
        graph = self.store.save_graph(self.aid, changed)
        checks = build_object_verification_checks(self.assessment, graph, self.store.list_admissions(self.aid))
        camera = next(row for row in checks if row.get("graphElementId") == "camera")
        self.assertFalse(camera["propertyEvidence"][0]["humanReviewRecorded"])
        self.assertEqual(camera["propertyEvidence"][0]["admissionIds"], [])

    def test_raw_correction_clears_old_translation_and_empty_graph_concurrency_guard(self):
        original = self.propose()[0]
        amended = self.review(original, "modify", editedProperty={"rawValue": "interface"})
        self.assertNotIn("normalizedValue", amended["assertion"])
        self.assertNotIn("reviewRecommendation", amended["assertion"])
        draft = DeterministicTrendProvider().generate(task_id="new-property-task", title="Other camera record",
                                                     question="What was reported?", horizon_years=3, materials=[self.source])
        other = self.store.create_assessment(draft)["assessment"]
        graph = self.store.save_graph(other["assessmentId"], self.data, expected_graph_revision=None)
        self.assertTrue(graph["graphRevisionId"])
        with self.assertRaises(IntelligenceConflict):
            self.store.save_graph(other["assessmentId"], self.data, expected_graph_revision=None)


if __name__ == "__main__":
    unittest.main()
