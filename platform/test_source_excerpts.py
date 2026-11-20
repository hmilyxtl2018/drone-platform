"""Selected-window budgets and immutable quote boundaries across model stages."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import unittest

try:
    from assessment_critique import critique_assessment
    from capability_analysis import validate_capabilities
    from evidence_graph import extract_research_graph
    from intelligence_agent import DeepSeekTrendProvider, DeterministicTrendProvider
    from source_excerpts import select_source_excerpts, selected_quote_offset
except ModuleNotFoundError:
    from .assessment_critique import critique_assessment
    from .capability_analysis import validate_capabilities
    from .evidence_graph import extract_research_graph
    from .intelligence_agent import DeepSeekTrendProvider, DeterministicTrendProvider
    from .source_excerpts import select_source_excerpts, selected_quote_offset


class SourceExcerptTests(unittest.TestCase):
    def setUp(self):
        self.quote = "SensorOne interoperability trial reported 3 participants under controlled conditions."
        nav = "\n".join("[UAV interoperability](https://example.test/menu)" for _ in range(150))
        self.body = "😀 Public report\n\n" + nav + "\n\n" + "Neutral unrelated appendix. " * 220 + "\n\n" + self.quote
        self.source = {"itemId": "s1", "body": self.body, "title": "Public interoperability trial", "sourceUri": "https://example.test/report", "sourceVersion": "v1", "publishedAt": None, "contentHash": hashlib.sha256(self.body.encode()).hexdigest()}

    def test_navigation_is_downweighted_and_late_prose_is_sent_with_exact_offsets(self):
        before = self.body
        selected = select_source_excerpts(self.body)
        self.assertLessEqual(selected["selectedCharacters"], 5000)
        self.assertFalse(selected["complete"])
        for excerpt in selected["excerpts"]:
            self.assertEqual(self.body[excerpt["startChar"]:excerpt["endChar"]], excerpt["text"])
        self.assertEqual(selected_quote_offset(self.body, selected, self.quote), self.body.index(self.quote))
        self.assertEqual(self.body, before)
        self.assertEqual(select_source_excerpts(self.body), selected)

    def test_named_model_architecture_and_supervision_displace_media_and_related_cards(self):
        supervision = "The named platform retained human supervision during the controlled demonstration."
        identity = "### ExampleCo ZX-77A\nThe ZX-77A platform uses an open architecture interface under controlled test conditions."
        media = "Audio Track\nPicture-in-Picture\nModal Dialog\nUncrewed systems human supervision interoperability\n" * 18
        recommendations = "\n".join("[![UAV interoperability](https://example.test/image)](https://example.test/story?interoperability=open-architecture)" for _ in range(50))
        body = ("Public report\n" + "Neutral introductory prose. " * 110 + "\n" + supervision
                + "\n" + "Neutral context. " * 110 + "\n" + identity + "\n" + "Neutral context. " * 110
                + "\n" + media + "\n## Discover more\n" + recommendations)
        selected = select_source_excerpts(body)
        self.assertEqual(selected_quote_offset(body, selected, supervision), body.index(supervision))
        self.assertEqual(selected_quote_offset(body, selected, identity), body.index(identity))
        self.assertLessEqual(selected["selectedCharacters"], 5000)
        self.assertTrue(all("Audio Track" not in part["text"] for part in selected["excerpts"]))
        self.assertTrue(all(part["startChar"] < body.index("## Discover more") for part in selected["excerpts"]))

    def test_generic_named_model_cues_work_without_a_topic_specific_name_list(self):
        quote = "The AC-17 camera platform completed a public compatibility test."
        body = "Neutral introduction. " * 300 + "\n" + quote + "\n" + "Unrelated appendix. " * 200
        selected = select_source_excerpts(body)
        self.assertEqual(selected_quote_offset(body, selected, quote), body.index(quote))

    def test_image_discovery_title_cannot_enter_model_graph_metric_or_critique_evidence(self):
        image_quote = "Imaginary ZX-91 UAV interoperated under human supervision."
        image = {"itemId": "image-lead", "title": image_quote, "body": image_quote,
                 "provenance": {"contentKind": "image-search-metadata", "retrievalKind": "search-discovery-only"}}
        captured = []
        response = {"nodes": [{"id": "device", "type": "EquipmentModel", "label": "SensorOne", "itemId": "s1", "quote": self.quote},
                              {"id": "image-device", "type": "EquipmentModel", "label": "Imaginary ZX-91", "itemId": "image-lead", "quote": image_quote}],
                    "edges": [], "gaps": []}
        graph = extract_research_graph([image, self.source], lambda messages: captured.extend(messages) or response)
        self.assertNotIn(image_quote, captured[1]["content"])
        self.assertEqual([node["label"] for node in graph["nodes"]], ["SensorOne"])
        self.assertEqual(graph["metadata"]["excludedDiscoveryOnlyItems"], 1)
        self.assertEqual(graph["metadata"]["rejectedReasonCounts"]["node:unknown-source"], 1)
        # Image-only stages must not call an external model, even if a legacy
        # store filled an empty image body with its tempting search title.
        def never_call(_):
            self.fail("image discovery reached model")
        self.assertEqual(extract_research_graph([image], never_call)["metadata"]["status"], "no-input")
        provider = DeepSeekTrendProvider(api_key="synthetic-test-key")
        provider.claim_provider._request_json = never_call
        self.assertEqual(provider.quantify([image])["metrics"], [])
        self.assertNotIn(image_quote, provider._material_prompt([image, self.source]))
        fallback = DeterministicTrendProvider().generate(task_id="task", title="Public report", question="Public test?", horizon_years=3, materials=[image, self.source])
        self.assertEqual(fallback["sourceSnapshot"], [image, self.source])
        self.assertEqual([ref["itemId"] for ref in fallback["evidenceRefs"]], ["s1"])
        self.assertEqual(fallback["summary"]["statistics"]["excludedDiscoveryOnlyItems"], 1)
        with self.assertRaises(ValueError):
            critique_assessment({"sourceSnapshot": [image], "summary": {"findings": [{"id": "f1", "evidenceIds": ["image-lead"]}]}}, never_call)

    def test_quote_must_be_inside_one_sent_window_even_when_in_the_full_body(self):
        body = "abcDEFghijkl"
        selected = {"excerpts": [{"startChar": 0, "endChar": 3, "text": "abc"}, {"startChar": 3, "endChar": 6, "text": "DEF"}]}
        self.assertIsNone(selected_quote_offset(body, selected, "cD"))
        self.assertIsNone(selected_quote_offset(body, selected, "ghij"))
        self.assertEqual(selected_quote_offset(body, selected, "DEF"), 3)

    def test_unmatched_text_retains_bounded_head_and_small_budgets_are_observed(self):
        body = "plain unrelated text " * 500
        selected = select_source_excerpts(body)
        self.assertEqual(selected["excerpts"], [{"startChar": 0, "endChar": 5000, "text": body[:5000]}])
        small = select_source_excerpts(self.body, max_characters=120)
        self.assertGreater(small["selectedCharacters"], 0)
        self.assertLessEqual(small["selectedCharacters"], 120)

    def test_graph_accepts_selected_late_quote_and_preserves_immutable_body(self):
        captured = []
        before = deepcopy(self.source)
        response = {"nodes": [{"id": "sensor", "type": "EquipmentModel", "label": "SensorOne", "itemId": "s1", "quote": self.quote}], "edges": [], "gaps": []}
        result = extract_research_graph([self.source], lambda messages: captured.extend(messages) or response)
        self.assertEqual(len(result["nodes"]), 1)
        ref = result["nodes"][0]["payload"]["evidenceRefs"][0]
        self.assertEqual(ref["startChar"], self.body.index(self.quote))
        self.assertEqual(self.source, before)
        materials = json.loads(captured[1]["content"].split("\n", 1)[1])["materials"]
        self.assertNotIn("body", materials[0])
        self.assertLessEqual(sum(len(e["text"]) for e in materials[0]["excerpts"]), 5000)

    def test_metric_tail_offsets_match_full_snapshot_and_unsent_quotes_are_dropped(self):
        provider = DeepSeekTrendProvider(api_key="synthetic-test-key")
        row = {"equipment": "SensorOne", "dimension": "互操作", "metric": "参加对象数", "value": 3, "unit": "participants", "qualifier": "exact", "basis": "reported-test", "conditions": "controlled conditions", "assessment": "公开试验参加数量不表示作战效果", "itemId": "s1", "quote": self.quote}
        unsent = "Unsent device reported 9 participants."
        source = dict(self.source, body=self.body[:9000] + unsent + self.body[9000:])
        source["contentHash"] = hashlib.sha256(source["body"].encode()).hexdigest()
        provider.claim_provider._request_json = lambda messages: {"metrics": [row, {**row, "value": 9, "quote": unsent}], "gaps": []}
        result = provider.quantify([source])
        self.assertEqual(len(result["metrics"]), 1)
        self.assertEqual(result["metrics"][0]["evidence"]["startChar"], source["body"].index(self.quote))
        self.assertTrue(any("未发送窗口" in gap for gap in result["gaps"]))
        validate_capabilities(result, [source])

    def test_critique_uses_same_selected_tail_and_records_original_unicode_offset(self):
        assessment = {"question": "Public interoperability?", "sourceSnapshot": [self.source], "summary": {"findings": [{"id": "f1", "evidenceIds": ["s1"], "judgment": "A controlled trial was reported."}]}}
        review = {"findingId": "f1", "verdict": "supported", "reason": "Only a reported controlled trial.", "evidence": [{"itemId": "s1", "quote": self.quote}]}
        result = critique_assessment(assessment, lambda messages: {"reviews": [review]})
        ref = result["checks"][0]["evidenceRefs"][0]
        self.assertEqual(ref["startChar"], self.body.index(self.quote))
        self.assertFalse(result["confidenceFactors"]["independenceVerified"])
        self.assertTrue(any("未覆盖" in issue for issue in result["issues"]))

    def test_report_evidence_preview_uses_relevant_window_with_exact_source_lines(self):
        nav = "\n".join("[UAV interoperability](https://example.test/menu)" for _ in range(150))
        context = "\n".join("Neutral unrelated appendix sentence." for _ in range(80))
        body = "😀 Report header\n" + nav + "\n" + context + "\n" + self.quote + "\nHuman oversight was required during the public trial."
        material = dict(self.source, body=body)
        before = deepcopy(material)
        ref = DeterministicTrendProvider._evidence_ref(material)
        self.assertIn(self.quote, ref["quote"])
        self.assertGreater(ref["lineStart"], 150)
        self.assertEqual("\n".join(body.splitlines()[ref["lineStart"] - 1:ref["lineEnd"]]), ref["quote"])
        self.assertLessEqual(len(ref["quote"]), 1200)
        self.assertEqual(material, before)


if __name__ == "__main__":
    unittest.main()
