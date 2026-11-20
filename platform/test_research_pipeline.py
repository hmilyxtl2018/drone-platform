"""Verify real orchestration boundaries using a deterministic provider stub."""
import tempfile
import json
import unittest
from pathlib import Path
from unittest.mock import Mock

try:
    from .intelligence_agent import IntelligenceAgent, DeterministicTrendProvider
    from .intelligence_store import IntelligenceStore, IntelligenceValidationError
    from .intelligence_ontology import attach_intelligence_ontology
    from .evidence_graph import extract_research_graph
    from .finding_attribute_links import link_finding_attributes
except ImportError:
    from intelligence_agent import IntelligenceAgent, DeterministicTrendProvider
    from intelligence_store import IntelligenceStore, IntelligenceValidationError
    from intelligence_ontology import attach_intelligence_ontology
    from evidence_graph import extract_research_graph
    from finding_attribute_links import link_finding_attributes


class ResearchPipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = IntelligenceStore(Path(self.tmp.name) / "test.sqlite")
        self.agent = IntelligenceAgent(self.store, trend_provider=DeterministicTrendProvider())
        body = "Acme tested SensorOne in a public laboratory."
        self.source = {"itemId": "s1", "sourceUri": "https://example.test/research", "sourceVersion": "v1", "contentHash": "a" * 64, "body": body}
        draft = DeterministicTrendProvider().generate(task_id="unit-test", title="Research", question="What does the evidence support?", horizon_years=3, materials=[self.source])
        draft["summary"]["findings"] = [{"id": "f1", "title": "Test", "judgment": "Public test report exists", "evidenceIds": ["s1"], "confidence": "low"}]
        self.assessment = self.store.create_assessment(draft)["assessment"]
        self.aid = self.assessment["assessmentId"]
        self.provider = Mock(configured=True)
        self.provider.claim_provider.model = "mock-model"
        self.agent.trend_provider = self.provider

    def test_model_graph_round_trip_preserves_extraction_on_regular_rebuild(self):
        identity_output = {
            "nodes": [{"id": "acme", "type": "Organization", "label": "Acme", "itemId": "s1", "quote": self.source["body"]},
                      {"id": "test", "type": "TestEvent", "label": "Laboratory test", "itemId": "s1", "quote": self.source["body"]}],
            "edges": [{"from": "test", "to": "acme", "relation": "involvesOrganization", "itemId": "s1", "quote": self.source["body"]}],
        }
        def extract(messages, **options):
            data = json.loads(messages[1]["content"].split("\n", 1)[1])
            if "findings" in data:
                attribute = data["attributes"][0]
                return {"links": [{"findingId": "f1", "objectId": attribute["objectId"],
                                   "propertyPath": attribute["propertyPath"], "assertionId": attribute["assertionId"],
                                   "basis": "support", "supportReason": "公开试验条件支持这里只讨论公开实验室试验，不能证明实际性能。"}]}
            if "object" not in data:
                return identity_output
            identity = data["object"]
            attributes = ([{"name": "公开试验条件", "value": "public laboratory", "itemId": "s1", "quote": self.source["body"]}]
                          if identity["type"] == "TestEvent" else [])
            return {"objectId": identity["objectId"], "attributes": attributes, "gaps": []}
        self.provider.claim_provider._request_json.side_effect = extract
        graph = self.agent.build_assessment_graph(self.aid, {"extractEntities": True, "expectedRevision": 1})
        self.assertTrue(any(node["type"] == "EventRecord" for node in graph["nodes"]))
        self.assertTrue(any(edge["relation"] == "reports" for edge in graph["edges"]))
        event = next(node for node in graph["nodes"] if node["type"] == "EventRecord")
        self.assertEqual(event["payload"]["attributes"][0]["value"], "public laboratory")
        self.assertEqual(event["payload"]["attributeExtraction"]["status"], "completed")
        trend = next(node for node in graph["nodes"] if node["type"] == "TrendHypothesis")
        reference = trend["payload"]["objectPropertyRefs"][0]
        self.assertEqual(reference["objectId"], event["id"])
        self.assertEqual(reference["assertionId"], event["payload"]["attributes"][0]["assertionId"])
        self.assertEqual(reference["evidenceRefs"][0]["quote"], self.source["body"])
        self.assertFalse(reference["truthVerified"])
        self.assertEqual(trend["payload"]["confidence"], "low")
        self.assertEqual(trend["payload"]["attributeLinkMetadata"]["status"], "completed")
        calls = self.provider.claim_provider._request_json.call_args_list
        self.assertEqual(len(calls), 4)
        self.assertEqual(calls[0].kwargs, {"thinking": False})
        object_requests = [json.loads(call.args[0][1]["content"].split("\n", 1)[1])["object"] for call in calls[1:3]]
        self.assertEqual({request["type"] for request in object_requests}, {"Organization", "TestEvent"})
        self.assertEqual(len({request["objectId"] for request in object_requests}), 2)
        for call in calls[1:3]:
            self.assertEqual(call.kwargs, {"thinking": False, "timeout_seconds": 90, "output_token_limit": 3072})
        self.assertEqual(calls[3].kwargs, {"thinking": False, "timeout_seconds": 90, "output_token_limit": 4096})
        rebuilt = self.agent.build_assessment_graph(self.aid)
        self.assertEqual(graph["graphRevisionId"], rebuilt["graphRevisionId"])
        self.assertEqual(self.provider.claim_provider._request_json.call_count, 4)
        rebuilt_trend = next(node for node in rebuilt["nodes"] if node["type"] == "TrendHypothesis")
        self.assertEqual(rebuilt_trend["payload"]["objectPropertyRefs"], trend["payload"]["objectPropertyRefs"])
        original_relations = [edge for edge in graph["edges"] if edge["payload"].get("extractionMethod") == "llm"]
        self.assertEqual([edge for edge in rebuilt["edges"] if edge["payload"].get("extractionMethod") == "llm"], original_relations)

    def test_equipment_node_carries_image_candidates_from_cited_source(self):
        source = dict(self.source, title="SensorOne public flight test", sourceName="Example Defense",
                      provenance={"retrievalKind": "public-web-material", "mediaReferences": [
                          {"imageUrl": "https://cdn.example.test/sensor-one.jpg", "sourceUrl": self.source["sourceUri"],
                           "caption": "SensorOne during public flight test", "mediaKind": "equipment-photo",
                           "verificationStatus": "unverified"}
                      ]})
        assessment = self.store.create_assessment(DeterministicTrendProvider().generate(
            task_id="media-test", title="Image evidence", question="What is shown?", horizon_years=3,
            materials=[source]))["assessment"]
        self.provider.claim_provider._request_json.side_effect = lambda messages, **options: (
            {"nodes": [{"id": "sensor", "type": "EquipmentModel", "label": "SensorOne", "itemId": "s1", "quote": source["body"]}], "edges": []}
            if "object" not in json.loads(messages[1]["content"].split("\n", 1)[1]) else
            {"objectId": "node:EquipmentModel:SensorOne:s1", "attributes": []}
        )
        graph = self.agent.build_assessment_graph(assessment["assessmentId"], {"extractEntities": True, "expectedRevision": 1})
        equipment = next(node for node in graph["nodes"] if node["type"] == "EquipmentModel")
        media = equipment["payload"]["mediaEvidence"]
        self.assertEqual(len(media), 1)
        self.assertEqual(media[0]["imageUrl"], "https://cdn.example.test/sensor-one.jpg")
        self.assertEqual(media[0]["matchBasis"], "cited-source-media")
        self.assertEqual(media[0]["verificationStatus"], "unverified")

    def test_identity_reextraction_keeps_previous_valid_attributes_when_one_object_fails(self):
        identity_output = {"nodes": [
            {"id": "org", "type": "Organization", "label": "Acme", "itemId": "s1", "quote": self.source["body"]},
            {"id": "sensor", "type": "EquipmentModel", "label": "SensorOne", "itemId": "s1", "quote": self.source["body"]}],
            "edges": [{"from": "org", "to": "sensor", "relation": "testsEquipment", "itemId": "s1", "quote": self.source["body"]}]}
        records = extract_research_graph([self.source], lambda _: identity_output, identity_only=True)
        sensor = next(node for node in records["nodes"] if node["type"] == "EquipmentModel")
        previous = {"assertionId": "saved-public-test", "name": "公开试验状态", "value": "tested",
                    "reviewStatus": "pending", "state": "pending-review", "evidenceRefs": sensor["payload"]["evidenceRefs"]}
        sensor["payload"]["attributes"] = [previous]
        saved = self.store.save_graph(self.aid, records)
        def extract(messages, **options):
            data = json.loads(messages[1]["content"].split("\n", 1)[1])
            if "findings" in data:
                prop = data["attributes"][0]
                return {"links": [{"findingId": "f1", "objectId": prop["objectId"], "propertyPath": prop["propertyPath"],
                                   "assertionId": prop["assertionId"], "basis": "support", "supportReason": "试验状态支持公开试验报道存在。"}]}
            if "object" in data:
                if data["object"]["type"] == "EquipmentModel":
                    raise TimeoutError("private-provider-detail")
                return {"attributes": []}
            return identity_output
        self.provider.claim_provider._request_json.side_effect = extract
        graph = self.agent.build_assessment_graph(self.aid, {"extractEntities": True, "expectedRevision": 1})
        retained = next(node for node in graph["nodes"] if node["id"] == sensor["id"])
        self.assertEqual(retained["payload"]["attributes"], [previous])
        self.assertEqual(retained["payload"]["evidenceRefs"], sensor["payload"]["evidenceRefs"])
        self.assertEqual(retained["payload"]["attributeExtraction"]["status"], "failed")
        self.assertEqual([edge for edge in graph["edges"] if edge["payload"].get("extractionMethod") == "llm"], records["edges"])
        self.assertNotEqual(graph["graphRevisionId"], saved["graphRevisionId"])
        self.assertNotIn("private-provider-detail", json.dumps(graph))

    def test_reextraction_removed_property_marks_trend_reference_stale_and_keeps_history(self):
        sensor_output = {"nodes": [{"id": "sensor", "type": "EquipmentModel", "label": "SensorOne", "itemId": "s1", "quote": self.source["body"]}], "edges": []}
        records = extract_research_graph([self.source], lambda _: sensor_output, identity_only=True)
        sensor = records["nodes"][0]
        sensor["payload"]["attributes"] = [{"assertionId": "saved-test-attribute", "name": "试验状态", "value": "tested", "evidenceRefs": sensor["payload"]["evidenceRefs"]}]
        old_finding = self.assessment["summary"]["findings"][0]
        linked = link_finding_attributes([old_finding], records["nodes"], lambda _: {"links": [{"findingId": "f1", "objectId": sensor["id"],
            "propertyPath": "payload.attributes[0].value", "assertionId": "saved-test-attribute", "basis": "support", "supportReason": "原文记载公开试验。"}]})
        old_links = linked["findings"][0]["objectPropertyRefs"]
        records["nodes"].append({"id": "finding:f1", "type": "TrendHypothesis", "label": "Test", "evidenceIds": ["s1"],
                                 "payload": {"objectPropertyRefs": old_links, "staleObjectPropertyRefs": [{"state": "stale-model-proposal", "assertionId": "older-history"}]}})
        self.store.save_graph(self.aid, records)
        def extract(messages, **options):
            data = json.loads(messages[1]["content"].split("\n", 1)[1])
            if "object" in data:
                return {"attributes": []}
            if "findings" in data:
                raise AssertionError("No attributes means no semantic model call")
            return {"nodes": [{"id": "org", "type": "Organization", "label": "Acme", "itemId": "s1", "quote": self.source["body"]}], "edges": []}
        self.provider.claim_provider._request_json.side_effect = extract
        graph = self.agent.build_assessment_graph(self.aid, {"extractEntities": True})
        trend = next(node for node in graph["nodes"] if node["type"] == "TrendHypothesis")
        self.assertEqual(trend["payload"]["objectPropertyRefs"], [])
        self.assertEqual(len(trend["payload"]["staleObjectPropertyRefs"]), 2)
        self.assertEqual(trend["payload"]["attributeLinkMetadata"]["status"], "no-input")
        self.assertEqual(self.provider.claim_provider._request_json.call_count, 2)

    def test_model_rounds_persist_actual_provider_and_stop_without_new_evidence(self):
        self.provider.claim_provider._request_json.return_value = {"reviews": [{"findingId": "f1", "verdict": "supported", "reason": "Only the reported test is supported.", "evidence": [{"itemId": "s1", "quote": self.source["body"]}]}]}
        for number in range(1, 4):
            record = self.agent.validate_assessment(self.aid, {"kind": "model-critique", "expectedRevision": 1, "provider": "invented"})
            self.assertEqual(record["roundNo"], number)
            self.assertEqual(record["provider"], "deepseek-critique")
            self.assertEqual(record["model"], "mock-model")
            self.assertEqual(record["status"], "needs-evidence")
        with self.assertRaises(IntelligenceValidationError):
            self.agent.validate_assessment(self.aid, {"kind": "model-critique"})
        self.assertEqual(self.provider.claim_provider._request_json.call_count, 3)
        self.assertEqual(self.store.get_assessment(self.aid)["summary"]["findings"][0]["confidence"], "low")

    def test_model_failure_leaves_no_false_success_or_leaked_error(self):
        self.provider.claim_provider._request_json.side_effect = RuntimeError("secret-do-not-expose")
        for operation in [lambda: self.agent.build_assessment_graph(self.aid, {"extractEntities": True}),
                          lambda: self.agent.validate_assessment(self.aid, {"kind": "model-critique"})]:
            with self.assertRaises(IntelligenceValidationError) as caught:
                operation()
            self.assertNotIn("secret-do-not-expose", str(caught.exception))
        self.assertIsNone(self.store.get_graph(self.aid))
        self.assertEqual(self.store.list_validation_rounds(self.aid), [])

    def test_ontology_overlay_keeps_base_unchanged_and_exposes_only_supplied_layer(self):
        base = {"files": {"tbox.json": {"classes": [{"id": "Entity"}]}}, "inventory": {"entries": []}}
        layer = {"objects": [], "relations": [], "provenance": [], "admissionCount": 0}
        root = Path(__file__).resolve().parent.parent / "ontology" / "coast-raid-k"
        view = attach_intelligence_ontology(base, layer, root)
        self.assertEqual(base["files"]["tbox.json"]["classes"], [{"id": "Entity"}])
        self.assertIn("CapabilityObservation", {c["id"] for c in view["files"]["tbox.json"]["classes"]})
        self.assertFalse(any(entry["type"] == "CapabilityObservation" for entry in view["inventory"]["entries"]))


if __name__ == "__main__":
    unittest.main()
