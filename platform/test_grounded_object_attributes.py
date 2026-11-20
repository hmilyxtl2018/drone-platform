"""Reject unsupported object properties and bind each retained field to its quote."""

from copy import deepcopy
import hashlib
from pathlib import Path
import tempfile
import unittest

try:
    from evidence_graph import attribute_value_supported, extract_research_graph
    from intelligence_agent import DeterministicTrendProvider
    from intelligence_store import IntelligenceStore
    from object_verification import build_object_verification_checks
except ModuleNotFoundError:
    from .evidence_graph import attribute_value_supported, extract_research_graph
    from .intelligence_agent import DeterministicTrendProvider
    from .intelligence_store import IntelligenceStore
    from .object_verification import build_object_verification_checks


class GroundedObjectAttributeTests(unittest.TestCase):
    def setUp(self):
        self.quote = "Camera X used an open interface in a controlled laboratory test on 12 June 2026."
        self.body = "😀 " + self.quote + "\nCamera X retained human oversight during the demonstration."
        self.source = {"itemId": "source-a", "title": "Public demonstration", "sourceUri": "https://example.test/test",
                       "sourceVersion": "v1", "publishedAt": "2026-07-01", "body": self.body,
                       "contentHash": hashlib.sha256(self.body.encode()).hexdigest()}
        self.attribute = {"name": "接口方式", "value": "open interface", "conditions": "controlled laboratory test",
                          "itemId": "source-a", "quote": self.quote}
        self.response = {"nodes": [
            {"id": "camera", "type": "EquipmentModel", "label": "Camera X", "itemId": "source-a", "quote": self.quote,
             "attributes": [deepcopy(self.attribute)]},
            {"id": "event", "type": "TestEvent", "label": "公开实验室试验", "itemId": "source-a", "quote": self.quote,
             "date": {"value": "12 June 2026", "itemId": "source-a", "quote": self.quote}},
        ], "edges": [], "gaps": []}

    def extract(self, response=None, sources=None):
        return extract_research_graph(sources or [self.source], lambda _: response or self.response)

    def test_property_and_date_keep_their_own_immutable_unicode_locators(self):
        result = self.extract()
        attribute = result["nodes"][0]["payload"]["attributes"][0]
        self.assertEqual(attribute["value"], "open interface")
        self.assertEqual(attribute["conditions"], "controlled laboratory test")
        self.assertEqual(attribute["reviewStatus"], "pending")
        ref = attribute["evidenceRefs"][0]
        self.assertEqual(ref["startChar"], 2)
        self.assertEqual(self.body[ref["startChar"]:ref["endChar"]], ref["quote"])
        self.assertEqual(ref["contentHash"], self.source["contentHash"])
        event = result["nodes"][1]["payload"]
        self.assertEqual(event["date"], "12 June 2026")
        self.assertEqual(event["dateEvidenceRefs"][0]["quote"], self.quote)
        self.assertNotEqual(event["date"], self.source["publishedAt"])

    def test_unsupported_values_qualifiers_sources_and_subjects_are_dropped(self):
        changes = [{"value": "fully autonomous"}, {"unit": "kg"}, {"conditions": "all environments"},
                   {"itemId": "unknown"}, {"quote": "retained human oversight during the demonstration."},
                   {"value": {"nested": "open interface"}}, {"value": True}]
        for change in changes:
            response = deepcopy(self.response)
            response["nodes"][0]["attributes"][0].update(change)
            with self.subTest(change=change):
                result = self.extract(response)
                self.assertEqual(result["nodes"][0]["payload"]["attributes"], [])
                self.assertEqual(result["metadata"]["rejectedAttributes"], 1)

    def test_attribute_can_cite_another_selected_source_with_exact_subject(self):
        quote = "Camera X retained human oversight during the demonstration."
        source_b = {**self.source, "itemId": "source-b", "body": quote,
                    "contentHash": hashlib.sha256(quote.encode()).hexdigest(), "sourceUri": "https://example.test/second"}
        response = deepcopy(self.response)
        response["nodes"][0]["attributes"] = [{"name": "监督", "value": "human oversight", "itemId": "source-b", "quote": quote}]
        node = self.extract(response, [self.source, source_b])["nodes"][0]
        self.assertEqual(node["evidenceIds"], ["source-a", "source-b"])
        self.assertEqual(node["payload"]["evidenceRefs"][0]["itemId"], "source-a")
        self.assertEqual(node["payload"]["attributes"][0]["evidenceRefs"][0]["itemId"], "source-b")

    def test_event_date_needs_source_overlap_and_literal_support(self):
        for change in [{"value": "2026-06-12"}, {"value": "2026-07-01"},
                       {"value": "open interface"},
                       {"quote": "Camera X retained human oversight during the demonstration.", "value": "demonstration"}]:
            response = deepcopy(self.response)
            response["nodes"][1]["date"].update(change)
            with self.subTest(change=change):
                result = self.extract(response)
                self.assertNotIn("date", result["nodes"][1]["payload"])
                self.assertEqual(result["metadata"]["rejectedEventDates"], 1)

    def test_numeric_tokens_do_not_turn_40_into_4_or_negative_into_positive(self):
        self.assertTrue(attribute_value_supported(4, "Camera X reported 4 kg."))
        for quote in ("Camera X reported 40 kg.", "Camera X reported -4 kg.", "Camera X reported 4.5 kg."):
            self.assertFalse(attribute_value_supported(4, quote))
        self.assertFalse(attribute_value_supported(float("inf"), "inf"))
        self.assertFalse(attribute_value_supported(10 ** 900, "anything"))

    def stored_checks(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        store = IntelligenceStore(Path(temp.name) / "test.sqlite")
        draft = DeterministicTrendProvider().generate(task_id="task", title="Public research", question="Public technical trend?",
                                                     horizon_years=3, materials=[self.source])
        assessment = store.create_assessment(draft)["assessment"]
        graph = store.save_graph(assessment["assessmentId"], self.extract())
        return store, assessment, graph

    def test_checks_bind_property_path_source_and_exact_graph_revision(self):
        _, assessment, graph = self.stored_checks()
        checks = build_object_verification_checks(assessment, graph)
        camera = checks[0]
        self.assertIn("payload.attributes[0].value", camera["propertyPaths"])
        self.assertIn("payload.attributes[0].conditions", camera["propertyPaths"])
        prop = camera["propertyEvidence"][0]
        self.assertEqual(prop["machineStatus"], "passed")
        self.assertEqual(prop["evidenceRefs"][0]["quote"], self.quote)
        self.assertEqual(camera["graphRevisionId"], graph["graphRevisionId"])
        self.assertFalse(prop["truthVerified"])
        self.assertEqual(camera["status"], "needs-evidence")
        event = checks[1]
        self.assertIn("payload.date", event["propertyPaths"])
        self.assertEqual(event["unverifiedPropertyPaths"], [])
        self.assertEqual(event["propertyEvidence"][0]["machineStatus"], "passed")

    def test_tampered_property_value_locator_or_date_fail_its_object_check(self):
        _, assessment, graph = self.stored_checks()
        for field, value in (("value", "fully autonomous"), ("conditions", "combat proven")):
            altered = deepcopy(graph)
            altered["nodes"][0]["payload"]["attributes"][0][field] = value
            with self.subTest(field=field):
                target = build_object_verification_checks(assessment, altered)[0]
                self.assertEqual(target["machineStatus"], "failed")
                self.assertFalse(target["eligibleForHumanReview"])
        altered = deepcopy(graph)
        altered["nodes"][0]["payload"]["attributes"][0]["evidenceRefs"][0]["startChar"] = 0
        self.assertEqual(build_object_verification_checks(assessment, altered)[0]["machineStatus"], "failed")
        altered = deepcopy(graph)
        altered["nodes"][1]["payload"]["date"] = "2026-06-12"
        self.assertEqual(build_object_verification_checks(assessment, altered)[1]["machineStatus"], "failed")

    def test_attribute_valid_time_is_checked_against_own_quote_and_field_path(self):
        _, assessment, graph = self.stored_checks()
        graph["nodes"][0]["payload"]["attributes"][0]["validTime"] = "12 June 2026"
        target = build_object_verification_checks(assessment, graph)[0]
        self.assertEqual(target["machineStatus"], "passed")
        self.assertIn("payload.attributes[0].validTime", target["propertyPaths"])
        time_check = next(check for check in target["checks"] if check["code"] == "attribute-valid-time-quote")
        self.assertEqual(time_check["status"], "passed")
        self.assertEqual(time_check["propertyPath"], "payload.attributes[0].validTime")
        self.assertFalse(target["truthVerified"])

    def test_forged_attribute_time_and_publication_date_cannot_pass_as_valid_time(self):
        _, assessment, graph = self.stored_checks()
        for value in ("2027", self.source["publishedAt"], "", " " * 5, True, 2026, ["12 June 2026"], "x" * 201):
            changed = deepcopy(graph)
            changed["nodes"][0]["payload"]["attributes"][0]["validTime"] = value
            with self.subTest(value=value):
                target = build_object_verification_checks(assessment, changed)[0]
                self.assertEqual(target["machineStatus"], "failed")
                self.assertFalse(target["eligibleForHumanReview"])
                self.assertEqual(next(check for check in target["checks"] if check["code"] == "attribute-valid-time-quote")["status"], "failed")

    def test_unknown_attribute_valid_time_remains_unknown_without_failure_or_publication_fill(self):
        _, assessment, graph = self.stored_checks()
        before = deepcopy(graph)
        graph["nodes"][0]["payload"]["attributes"][0]["validTime"] = None
        target = build_object_verification_checks(assessment, graph)[0]
        self.assertEqual(target["machineStatus"], "passed")
        self.assertNotIn("payload.attributes[0].validTime", target["propertyPaths"])
        self.assertFalse(any(check["code"] == "attribute-valid-time-quote" for check in target["checks"]))
        self.assertIsNone(graph["nodes"][0]["payload"]["attributes"][0]["validTime"])
        self.assertNotIn("validTime", before["nodes"][0]["payload"]["attributes"][0])

    def test_historical_field_category_failure_does_not_invalidate_other_property(self):
        quote = "Camera X accelerates the review loop. Camera X retains human oversight. Camera X has an open architecture."
        material = {**self.source, "body": quote, "contentHash": hashlib.sha256(quote.encode()).hexdigest()}
        ref = {key: material[key] for key in ("itemId", "sourceUri", "sourceVersion", "contentHash")}
        ref.update(quote=quote, startChar=0, endChar=len(quote))
        attrs = [
            {"name": "监督方式", "attributeKey": "human-oversight", "value": "accelerates the review loop", "evidenceRefs": [deepcopy(ref)]},
            {"name": "架构", "attributeKey": "architecture", "value": "open architecture", "evidenceRefs": [deepcopy(ref)]},
        ]
        graph = {"graphRevisionId": "fixed-test-version", "nodes": [
            {"id": "camera", "label": "Camera X", "type": "EquipmentModel", "evidenceIds": ["source-a"],
             "payload": {"extractionMethod": "llm", "evidenceRefs": [deepcopy(ref)], "attributes": attrs}}], "edges": []}
        before = deepcopy(graph)
        target = build_object_verification_checks({"assessmentId": "test", "sourceSnapshot": [material]}, graph)[0]
        self.assertEqual(target["machineStatus"], "failed")
        self.assertEqual(target["propertyEvidence"][0]["machineStatus"], "failed")
        self.assertEqual(target["propertyEvidence"][1]["machineStatus"], "passed")
        field_check = next(check for check in target["checks"] if check["code"] == "attribute-field-context")
        self.assertEqual(field_check["propertyPath"], "payload.attributes[0].value")
        self.assertEqual(field_check["status"], "failed")
        self.assertTrue(all(check["status"] == "passed" for check in target["checks"]
                            if check["code"] in {"snapshot-quote-locator", "ontology-class", "identity-quote"}))
        self.assertEqual(graph, before)

    def test_historical_known_name_uses_field_cue_without_reclassifying_generic_names(self):
        _, assessment, graph = self.stored_checks()
        for name, value in (("人工监督", "open interface"), ("接口", "controlled laboratory test"), ("公开试验状态", "open interface")):
            changed = deepcopy(graph)
            changed["nodes"][0]["payload"]["attributes"][0].update(name=name, value=value)
            # The quoted test/interface words elsewhere in this same sentence
            # can be coarse context, so isolate each proposed value statement.
            quote = "Camera X is " + value + "."
            source = {**self.source, "body": quote, "contentHash": hashlib.sha256(quote.encode()).hexdigest()}
            ref = {key: source[key] for key in ("itemId", "sourceUri", "sourceVersion", "contentHash")}
            ref.update(quote=quote, startChar=0, endChar=len(quote))
            changed["nodes"][0]["payload"].update(evidenceRefs=[deepcopy(ref)])
            changed["nodes"][0]["payload"]["attributes"][0].update(evidenceRefs=[deepcopy(ref)])
            changed["nodes"][0]["payload"]["attributes"][0].pop("conditions", None)
            with self.subTest(name=name):
                target = build_object_verification_checks({"assessmentId": "test", "sourceSnapshot": [source]}, {"nodes": [changed["nodes"][0]], "edges": []})[0]
                self.assertEqual(target["propertyEvidence"][0]["machineStatus"], "failed")
                self.assertEqual(next(check for check in target["checks"] if check["code"] == "attribute-field-context")["status"], "failed")
        generic = deepcopy(graph)
        generic["nodes"][0]["payload"]["attributes"][0]["name"] = "描述"
        target = build_object_verification_checks(assessment, generic)[0]
        self.assertEqual(target["propertyEvidence"][0]["machineStatus"], "passed")
        self.assertFalse(any(check["code"] == "attribute-field-context" for check in target["checks"]))

    def test_literal_field_cues_pass_without_trust_promotion(self):
        _, assessment, graph = self.stored_checks()
        target = build_object_verification_checks(assessment, graph)[0]
        field_check = next(check for check in target["checks"] if check["code"] == "attribute-field-context")
        self.assertEqual(field_check["attributeKey"], "interface-standard")
        self.assertEqual(field_check["status"], "passed")
        self.assertEqual(target["propertyEvidence"][0]["machineStatus"], "passed")
        self.assertFalse(target["propertyEvidence"][0]["truthVerified"])

    def test_descriptive_research_properties_do_not_expand_admission_whitelist(self):
        store, assessment, graph = self.stored_checks()
        candidates = store.propose_graph_admissions(assessment["assessmentId"],
                                                   {"graphRevisionId": graph["graphRevisionId"],
                                                    "graphNodeIds": [n["id"] for n in graph["nodes"]]})
        camera, event = candidates
        self.assertEqual(camera["assertion"]["attributes"], {})
        self.assertEqual(event["assertion"]["attributes"], {"eventKind": "TestEvent"})
        self.assertNotIn("date", str(camera["assertion"]["attributes"]))


if __name__ == "__main__":
    unittest.main()
