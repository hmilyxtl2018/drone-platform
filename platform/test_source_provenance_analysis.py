"""Source-nature and independence semantics across the research pipeline."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from jsonschema import Draft202012Validator

try:
    from assessment_critique import critique_assessment
    from capability_analysis import empty_capabilities
    from evidence_graph import extract_research_graph, source_metadata
    from intelligence_agent import DeepSeekTrendProvider, DeterministicTrendProvider, IntelligenceAgent
    from intelligence_store import IntelligenceStore
except ModuleNotFoundError:
    from .assessment_critique import critique_assessment
    from .capability_analysis import empty_capabilities
    from .evidence_graph import extract_research_graph, source_metadata
    from .intelligence_agent import DeepSeekTrendProvider, DeterministicTrendProvider, IntelligenceAgent
    from .intelligence_store import IntelligenceStore


class SourceProvenanceAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.body = "Acme announced a procurement requirement for an autonomous sensor; no test result was provided."
        self.material = {
            "itemId": "item-a", "title": "Public procurement requirement", "sourceName": "Public Office",
            "sourceUri": "https://office.example/notice", "sourceVersion": "v1", "publishedAt": None,
            "body": self.body, "contentHash": hashlib.sha256(self.body.encode()).hexdigest(),
            "category": "procurement-budget", "sourceCategory": "procurement-budget",
            "sourceRole": "procurement-record", "publisherDomain": "office.example",
            "contentNature": "procurement-record", "verificationStatus": "unverified", "sourceReliability": "primary",
            "provenance": {"collector": "firecrawl-search", "publicationDatePresent": False, "publicationDateVerified": False, "query": "source query", "researchQuestion": "research question"},
        }

    def test_missing_historical_context_stays_unclassified_and_snapshot_is_unchanged(self):
        legacy = {"itemId": "old", "sourceUri": "https://www.army.mil/example", "body": "old report"}
        before = deepcopy(legacy)
        context = source_metadata(legacy)
        self.assertEqual(context["sourceRole"], "unclassified")
        self.assertEqual(context["verificationStatus"], "unknown")
        self.assertEqual(context["sourceReliability"], "unknown")
        self.assertEqual(legacy, before)
        projected = source_metadata(self.material)
        projected["provenance"]["query"] = "changed"
        self.assertEqual(self.material["provenance"]["query"], "source query")

    def test_provenance_fallback_and_prompt_bounds_preserve_source_nature(self):
        source = dict(self.material, provenance={**self.material["provenance"], "sourceRole": "manufacturer-disclosure", "contentNature": "manufacturer-claim", "query": "q" * 4000, "researchQuestion": "r" * 4000})
        source.pop("sourceRole")
        source.pop("contentNature")
        context = source_metadata(source, for_prompt=True)
        self.assertEqual(context["sourceRole"], "manufacturer-disclosure")
        self.assertEqual(context["contentNature"], "manufacturer-claim")
        self.assertNotIn("query", context["provenance"])
        self.assertFalse(context["provenance"]["publicationDateVerified"])
        self.assertLess(len(json.dumps(context)), 1200)

    def test_graph_prompt_and_citations_preserve_source_role_without_changing_node_type(self):
        captured = []
        response = {"nodes": [{"id": "acme", "type": "Organization", "label": "Acme", "itemId": "item-a", "quote": self.body}], "edges": [], "gaps": []}
        result = extract_research_graph([self.material], lambda messages: captured.extend(messages) or response)
        self.assertIn('"sourceRole": "procurement-record"', captured[1]["content"])
        self.assertIn("不等于已交付", captured[0]["content"])
        self.assertEqual(result["nodes"][0]["type"], "Organization")
        payload = result["nodes"][0]["payload"]
        self.assertEqual(payload["reviewStatus"], "pending")
        ref = payload["evidenceRefs"][0]
        self.assertEqual(ref["sourceRole"], "procurement-record")
        self.assertEqual(ref["verificationStatus"], "unverified")
        self.assertEqual(ref["provenance"], self.material["provenance"])

    def test_reprints_across_two_domains_do_not_raise_report_confidence(self):
        provider = DeepSeekTrendProvider(api_key="test-key", base_url="https://api.deepseek.com/v1")
        captured = []
        provider.claim_provider._request_json = lambda messages: captured.extend(messages) or {"findings": [{"id": "f1", "title": "Requirement", "judgment": "A procurement requirement was reported.", "confidence": "medium", "evidenceIds": ["item-a", "item-b"]}]}
        provider.quantify = lambda materials: empty_capabilities()
        secondary = dict(self.material, itemId="item-b", sourceUri="https://news.example/reprint", sourceRole="secondary-report", contentNature="secondary-report", sourceReliability="secondary")
        materials = [self.material, secondary]
        before = deepcopy(materials)
        result = provider.generate(task_id="task", title="T", question="Q", horizon_years=3, materials=materials)
        finding = result["summary"]["findings"][0]
        self.assertEqual(finding["confidence"], "low")
        self.assertIn("repeated-source-text", finding["uncertainty"])
        self.assertIn("source-independence-not-verified", finding["uncertainty"])
        self.assertIn("official-publication不等于verified", captured[0]["content"])
        self.assertIn('"contentNature": "secondary-report"', captured[1]["content"])
        self.assertEqual(result["evidenceRefs"][0]["sourceRole"], "procurement-record")
        self.assertEqual(result["sourceSnapshot"], before)
        self.assertEqual(materials, before)

    def test_capability_prompt_preserves_manufacturer_disclosure_as_source_context(self):
        provider = DeepSeekTrendProvider(api_key="test-key", base_url="https://api.deepseek.com/v1")
        captured = []
        provider.claim_provider._request_json = lambda messages: captured.extend(messages) or {"metrics": [], "gaps": ["No test evidence."]}
        material = dict(self.material, sourceRole="manufacturer-disclosure", contentNature="manufacturer-claim")
        result = provider.quantify([material])
        self.assertIn('"sourceRole": "manufacturer-disclosure"', captured[1]["content"])
        self.assertIn('"contentNature": "manufacturer-claim"', captured[1]["content"])
        self.assertIn("不能自动标作实测能力", captured[0]["content"])
        self.assertEqual(result["metrics"], [])

    def test_critique_retains_context_and_reprint_warning_without_verification(self):
        secondary = dict(self.material, itemId="item-b", sourceUri="https://news.example/reprint", sourceRole="secondary-report")
        assessment = {"question": "Q", "sourceSnapshot": [self.material, secondary], "summary": {"findings": [{"id": "f1", "judgment": "Requirement recorded"}]}}
        captured = []
        result = critique_assessment(assessment, lambda messages: captured.extend(messages) or {"reviews": [{"findingId": "f1", "verdict": "supported", "reason": "Source describes a requirement.", "evidence": [{"itemId": "item-a", "quote": self.body}]}]})
        self.assertIn('"sourceRole": "procurement-record"', captured[1]["content"])
        self.assertIn("厂商自述", captured[0]["content"])
        self.assertIn("sourceRole=procurement-record", result["checks"][0]["detail"])
        self.assertTrue(any(check["code"] == "source-text-overlap" for check in result["checks"]))
        self.assertFalse(result["confidenceFactors"]["independenceVerified"])
        self.assertEqual(result["confidenceFactors"]["independentPublisherCount"], 0)
        self.assertEqual(result["status"], "needs-evidence")

    def test_persisted_report_and_source_graph_keep_recorded_metadata(self):
        schema = json.loads((Path(__file__).parents[1] / "ontology/coast-raid-k/schema.intelligence.json").read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as folder:
            agent = IntelligenceAgent(IntelligenceStore(Path(folder) / "i.sqlite"), trend_provider=DeterministicTrendProvider())
            assessment = agent.create_assessment({"taskId": "task", "title": "T", "question": "Q", "horizonYears": 3, "sourceSnapshot": [self.material]})["assessment"]
            Draft202012Validator(schema).validate(assessment)
            graph = agent.build_assessment_graph(assessment["id"])
            source = next(node for node in graph["nodes"] if node["type"] == "EvidenceSource")
            self.assertEqual(source["payload"]["sourceRole"], "procurement-record")
            self.assertEqual(source["payload"]["verificationStatus"], "unverified")
            self.assertEqual(source["payload"]["provenance"], self.material["provenance"])
            self.assertEqual(agent.store.get_assessment(assessment["id"])["sourceSnapshot"], [self.material])


if __name__ == "__main__":
    unittest.main()
