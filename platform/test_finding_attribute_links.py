"""Semantic references require explicit model proposals and retained property IDs."""
from copy import deepcopy
import hashlib
import json
import unittest

try:
    from finding_attribute_links import link_finding_attributes, MAX_INPUT_CHARACTERS
    from research_trace import execution_context
except ModuleNotFoundError:  # pragma: no cover
    from .finding_attribute_links import link_finding_attributes, MAX_INPUT_CHARACTERS
    from .research_trace import execution_context


def object_node(index=0, *, item_id="source-a", ref_size=0):
    label = f"Camera {index}"
    quote = label + " uses an open interface." + " " * ref_size
    ref = {"itemId": item_id, "sourceUri": "https://example.test/technical", "sourceVersion": "v1",
           "contentHash": hashlib.sha256(quote.encode()).hexdigest(), "quote": quote, "startChar": 100, "endChar": 100 + len(quote)}
    return {"id": f"camera-{index}", "label": label, "type": "EquipmentModel", "evidenceIds": [item_id],
            "payload": {"extractionMethod": "llm", "evidenceRefs": [ref], "attributes": [{"assertionId": f"attribute-{index}",
            "name": "接口方式", "attributeKey": "interface-standard", "value": "an open interface",
            "displayValue": "开放接口", "evidenceRefs": [deepcopy(ref)], "claimKind": "manufacturer-claim", "state": "pending-review"}]}}


def finding(fid="finding-a", evidence_ids=None):
    return {"id": fid, "title": "公开接口发展", "judgment": "公开资料显示开放接口设计正受到关注，采用效果尚待验证。",
            "evidenceIds": evidence_ids or ["source-a"], "confidence": "low"}


def proposal(index=0, fid="finding-a", **changes):
    return {"findingId": fid, "objectId": f"camera-{index}", "propertyPath": "payload.attributes[0].value",
            "assertionId": f"attribute-{index}", "basis": "context", "supportReason": "来源中的公开接口描述为该趋势提供背景，不能直接证明实际采用效果。", **changes}


class FindingAttributeLinkTests(unittest.TestCase):
    def test_model_proposal_copies_existing_evidence_and_does_not_upgrade_finding(self):
        findings, nodes = [finding()], [object_node()]
        originals = deepcopy((findings, nodes))
        requests, events = [], []
        def request(messages):
            requests.append(messages)
            return {"links": [proposal(truthVerified=True, confidence="high")], "gaps": []}
        with execution_context(events.append, task_id="test"):
            result = link_finding_attributes(findings, nodes, request, model="local-fixture-model", graph_revision_id="graph-a")
        self.assertEqual((findings, nodes), originals)
        link = result["findings"][0]["objectPropertyRefs"][0]
        self.assertEqual(link["rawValue"], "an open interface")
        self.assertEqual(link["evidenceRefs"][0]["quote"], nodes[0]["payload"]["attributes"][0]["evidenceRefs"][0]["quote"])
        self.assertEqual(link["graphRevisionId"], "graph-a")
        self.assertEqual(link["state"], "pending-model-review")
        self.assertFalse(link["truthVerified"])
        self.assertNotIn("confidence", link)
        self.assertEqual(result["findings"][0]["confidence"], "low")
        self.assertEqual(len(requests), 1)
        self.assertEqual([event["status"] for event in events], ["started", "succeeded"])
        self.assertEqual(events[-1]["details"]["retainedItemCount"], 1)

    def test_matching_source_without_model_proposal_does_not_create_link(self):
        result = link_finding_attributes([finding()], [object_node()], lambda _: {"links": []})
        self.assertEqual(result["findings"][0]["objectPropertyRefs"], [])
        self.assertEqual(result["metadata"]["status"], "no-supported-links")
        unconfigured = link_finding_attributes([finding()], [object_node()])
        self.assertEqual(unconfigured["metadata"]["calls"], 0)
        self.assertEqual(unconfigured["metadata"]["status"], "not-configured")

    def test_wrong_assertion_path_object_finding_and_new_quote_are_rejected(self):
        changes = [{"assertionId": "changed"}, {"propertyPath": "payload.attributes[1].value"},
                   {"objectId": "other-object"}, {"findingId": "missing"}, {"quote": "new quote"},
                   {"evidenceRefs": []}, {"supportReason": ""}, {"basis": "verified"}, {"basis": []}]
        result = link_finding_attributes([finding()], [object_node()], lambda _: {"links": [proposal(**change) for change in changes]})
        self.assertEqual(result["metadata"]["rejectedLinks"], len(changes))
        self.assertEqual(result["metadata"]["retainedLinks"], 0)
        self.assertEqual(result["findings"][0]["objectPropertyRefs"], [])

    def test_source_intersection_is_checked_per_finding_and_not_any_report_source(self):
        findings = [finding(), finding("finding-b", ["source-b"])]
        nodes = [object_node(), object_node(1, item_id="source-b")]
        response = {"links": [proposal(1), proposal(1, "finding-b", basis="contrary")]}
        result = link_finding_attributes(findings, nodes, lambda _: response)
        self.assertEqual(result["metadata"]["retainedLinks"], 1)
        self.assertEqual(result["metadata"]["rejectedReasonCounts"], {"finding-property-source-mismatch": 1})
        self.assertEqual(result["findings"][0]["objectPropertyRefs"], [])
        self.assertEqual(result["findings"][1]["objectPropertyRefs"][0]["basis"], "contrary")

    def test_provider_failure_keeps_valid_previous_links_and_excludes_exception_text(self):
        first = link_finding_attributes([finding()], [object_node()], lambda _: {"links": [proposal()]}, graph_revision_id="graph-a")
        def fail(_):
            raise RuntimeError("sensitive-key-not-for-output")
        second = link_finding_attributes(first["findings"], [object_node()], fail, graph_revision_id="graph-a")
        self.assertEqual(second["findings"][0]["objectPropertyRefs"], first["findings"][0]["objectPropertyRefs"])
        self.assertEqual(second["metadata"]["status"], "failed")
        self.assertNotIn("sensitive-key-not-for-output", json.dumps(second))

    def test_changed_graph_or_attribute_marks_old_link_stale_without_automatic_relink(self):
        nodes = [object_node()]
        first = link_finding_attributes([finding()], nodes, lambda _: {"links": [proposal()]}, graph_revision_id="graph-a")
        changed_graph = link_finding_attributes(first["findings"], nodes, graph_revision_id="graph-b")
        self.assertEqual(changed_graph["findings"][0]["objectPropertyRefs"], [])
        self.assertEqual(changed_graph["metadata"]["staleLinks"], 1)
        self.assertEqual(changed_graph["findings"][0]["staleObjectPropertyRefs"][0]["state"], "stale-model-proposal")
        nodes[0]["payload"]["attributes"][0]["conditions"] = "new condition"
        changed_attribute = link_finding_attributes(first["findings"], nodes, graph_revision_id="graph-a")
        self.assertEqual(changed_attribute["findings"][0]["objectPropertyRefs"], [])
        self.assertEqual(changed_attribute["metadata"]["staleLinks"], 1)

    def test_payload_object_and_finding_limits_hold_without_synthesizing_remaining_links(self):
        nodes = [object_node(i, ref_size=1000) for i in range(80)]
        findings = [finding(f"finding-{i}") for i in range(25)]
        inputs = []
        def request(messages):
            inputs.append(json.loads(messages[1]["content"].split("\n", 1)[1]))
            return {"links": []}
        result = link_finding_attributes(findings, nodes, request)
        self.assertLessEqual(len(inputs[0]["attributes"]), 40)
        self.assertEqual(len(inputs[0]["findings"]), 20)
        self.assertLessEqual(len(json.dumps(inputs[0], ensure_ascii=False)), MAX_INPUT_CHARACTERS)
        self.assertLess(result["metadata"]["selectedAttributes"], result["metadata"]["availableAttributes"])
        self.assertTrue(result["gaps"])

    def test_no_valid_or_source_matching_properties_prevents_provider_call(self):
        invalid = object_node()
        invalid["payload"]["attributes"][0]["value"] = "invented value"
        calls = []
        result = link_finding_attributes([finding()], [invalid], lambda messages: calls.append(messages))
        self.assertEqual(calls, [])
        self.assertEqual(result["metadata"]["status"], "no-input")
        mismatch = link_finding_attributes([finding(evidence_ids=["foreign"])], [object_node()], lambda messages: calls.append(messages))
        self.assertEqual(mismatch["metadata"]["status"], "no-input")
        self.assertEqual(calls, [])

    def test_duplicate_links_and_per_finding_budget_are_explicit(self):
        nodes = [object_node(i) for i in range(10)]
        result = link_finding_attributes([finding()], nodes, lambda _: {"links": [proposal(0), proposal(0)] + [proposal(i) for i in range(1, 10)]})
        self.assertEqual(result["metadata"]["retainedLinks"], 8)
        self.assertEqual(result["metadata"]["rejectedReasonCounts"], {"duplicate-link": 1, "finding-link-limit": 2})


if __name__ == "__main__":
    unittest.main()
