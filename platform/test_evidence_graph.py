"""Trust-boundary checks for DeepSeek research graph extraction."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import unittest

try:
    from evidence_graph import extract_research_graph
except ModuleNotFoundError:
    from .evidence_graph import extract_research_graph


class EvidenceGraphTests(unittest.TestCase):
    def setUp(self):
        self.body = "😀 Acme tested SensorOne in a public laboratory. Acme received a procurement contract."
        self.material = {"itemId": "item-a", "title": "Public test", "sourceUri": "https://example.test/report", "sourceVersion": "v1", "publishedAt": None, "contentHash": hashlib.sha256(self.body.encode()).hexdigest(), "body": self.body}
        quote = "Acme tested SensorOne in a public laboratory."
        self.response = {
            "nodes": [
                {"id": "org", "type": "Organization", "label": "Acme", "itemId": "item-a", "quote": quote},
                {"id": "sensor", "type": "EquipmentModel", "label": "SensorOne", "itemId": "item-a", "quote": quote},
                {"id": "test", "type": "TestEvent", "label": "Public laboratory test", "itemId": "item-a", "quote": quote},
            ],
            "edges": [{"from": "test", "to": "sensor", "relation": "testsEquipment", "itemId": "item-a", "quote": quote}],
            "gaps": [],
        }

    def test_exact_quotes_preserve_unicode_offsets_and_pending_authority(self):
        result = extract_research_graph([self.material], lambda messages: self.response)
        self.assertEqual(len(result["nodes"]), 3)
        self.assertEqual(result["nodes"][2]["type"], "EventRecord")
        self.assertEqual(result["nodes"][2]["payload"]["eventKind"], "TestEvent")
        for record in result["nodes"] + result["edges"]:
            ref = record["payload"]["evidenceRefs"][0]
            self.assertEqual(ref["startChar"], 2)
            self.assertEqual(self.body[ref["startChar"]:ref["endChar"]], ref["quote"])
            self.assertEqual(ref["contentHash"], self.material["contentHash"])
            self.assertEqual(record["payload"]["reviewStatus"], "pending")
            self.assertEqual(record["evidenceIds"], ["item-a"])
        self.assertEqual(result["metadata"]["equipmentObjectCoverage"]["retainedEquipmentCount"], 1)
        self.assertFalse(result["metadata"]["equipmentObjectCoverage"]["truthVerified"])

    def test_absent_supported_equipment_records_explicit_identity_gap(self):
        response = deepcopy(self.response)
        response["nodes"] = [response["nodes"][0]]
        response["edges"] = []
        result = extract_research_graph([self.material], lambda _: response)
        self.assertEqual(result["metadata"]["equipmentObjectCoverage"]["retainedEquipmentCount"], 0)
        self.assertTrue(any("不能把厂商名称" in gap for gap in result["gaps"]))

    def test_unknown_sources_bad_quotes_and_missing_endpoints_are_dropped(self):
        response = deepcopy(self.response)
        response["nodes"][0]["itemId"] = "unknown"
        response["nodes"][1]["quote"] = "SensorOne flew 100 km"
        response["nodes"].append({"id": "bad", "type": ["Organization"], "label": "Acme", "itemId": "item-a", "quote": self.body})
        result = extract_research_graph([self.material], lambda messages: response)
        self.assertEqual(len(result["nodes"]), 1)
        self.assertEqual(result["edges"], [])
        self.assertEqual(result["metadata"]["rejectedNodes"], 3)
        self.assertEqual(result["metadata"]["rejectedEdges"], 1)

    def test_all_sources_are_sent_in_bounded_batches(self):
        materials = [dict(self.material, itemId=f"item-{i}", body="A" * 5000 + self.body) for i in range(31)]
        response = deepcopy(self.response)
        for node in response["nodes"]:
            node["itemId"] = "item-0"
        response["nodes"][1]["itemId"] = "item-30"
        captured = []
        result = extract_research_graph(materials, lambda messages: captured.extend(messages) or response)
        self.assertEqual(len(result["nodes"]), 3)
        self.assertEqual(result["metadata"]["rejectedNodes"], 0)
        self.assertEqual(result["nodes"][0]["payload"]["evidenceRefs"][0]["startChar"], 5002)
        prompts = [message["content"] for message in captured if message.get("role") == "user"]
        self.assertNotIn('"itemId": "item-30"', prompts[0])
        self.assertTrue(any('"itemId": "item-30"' in prompt for prompt in prompts))
        self.assertIn("public laboratory", prompts[0])
        self.assertEqual(result["metadata"]["modelInputItems"], 31)
        self.assertEqual(result["metadata"]["modelInputBatches"], 4)

    def test_disallowed_event_and_relation_types_are_dropped(self):
        response = deepcopy(self.response)
        response["nodes"][2]["type"] = "DeploymentEvent"
        response["edges"][0].update({"from": "org", "relation": "targets"})
        result = extract_research_graph([self.material], lambda messages: response)
        self.assertEqual(len(result["nodes"]), 2)
        self.assertEqual(result["edges"], [])

    def test_duplicate_model_ids_are_not_ambiguously_linked(self):
        response = deepcopy(self.response)
        response["nodes"][0]["id"] = "sensor"
        result = extract_research_graph([self.material], lambda messages: response)
        self.assertEqual(len(result["nodes"]), 1)
        self.assertEqual(result["edges"], [])

    def test_batch_local_ids_keep_distinct_objects_and_local_edge_endpoints(self):
        materials = [dict(self.material, itemId=f"item-{i}", body=f"Acme{i} tested Sensor{i} in a public laboratory.") for i in range(16)]
        def request(messages):
            rows = json.loads(messages[-1]["content"].rsplit("\n", 1)[-1])["materials"]
            first = rows[0]["itemId"]
            index = int(first.split("-")[-1])
            quote = materials[index]["body"]
            return {"nodes": [
                {"id": "node-1", "type": "EquipmentModel", "label": f"Sensor{index}", "itemId": first, "quote": quote},
                {"id": "node-2", "type": "TestEvent", "label": f"Test {index}", "itemId": first, "quote": quote},
            ], "edges": [{"from": "node-2", "to": "node-1", "relation": "testsEquipment", "itemId": first, "quote": quote}], "gaps": []}
        result = extract_research_graph(materials, request, identity_only=True)
        self.assertEqual(len(result["nodes"]), 4)
        self.assertEqual(len(result["edges"]), 2)
        by_id = {node["id"]: node for node in result["nodes"]}
        for edge in result["edges"]:
            source_id = edge["evidenceIds"][0]
            self.assertEqual(by_id[edge["from"]]["evidenceIds"], [source_id])
            self.assertEqual(by_id[edge["to"]]["evidenceIds"], [source_id])
        self.assertEqual(result["metadata"]["rejectedNodes"], 0)

    def test_provider_failure_returns_sanitized_metadata(self):
        def fail(messages):
            raise RuntimeError("credential=private-value")
        result = extract_research_graph([self.material], fail)
        self.assertEqual(result["metadata"]["status"], "failed")
        self.assertEqual(result["errors"], ["research-graph-extraction-failed"])
        self.assertNotIn("private-value", str(result))
        self.assertEqual(result["nodes"], [])


if __name__ == "__main__":
    unittest.main()
