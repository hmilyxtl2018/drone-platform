"""Offline checks for the optional DeepSeek/OpenAI-compatible provider."""

from __future__ import annotations

import json
import unittest
import urllib.request
from unittest.mock import MagicMock, patch

try:
    from intelligence_agent import DeepSeekClaimProvider, DeepSeekTrendProvider, DeterministicClaimProvider, build_claim_provider
except ModuleNotFoundError:  # pragma: no cover
    from .intelligence_agent import DeepSeekClaimProvider, DeepSeekTrendProvider, DeterministicClaimProvider, build_claim_provider


class _MockResponse:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _limit=-1):
        return self.payload


class DeepSeekProviderTests(unittest.TestCase):
    DOCUMENT = {"id": "source-1", "body": "装备 | status | in testing\n采购 | status | planned"}

    def test_missing_key_uses_deterministic_without_network(self):
        provider = DeepSeekClaimProvider(api_key="")
        with patch.object(urllib.request, "build_opener", side_effect=AssertionError("network call")) as call:
            claims = provider.extract(self.DOCUMENT)
        call.assert_not_called()
        self.assertIsInstance(claims, list)
        self.assertFalse(provider.configured)

    def test_mock_response_filters_unknown_iri_and_keeps_evidence(self):
        provider = DeepSeekClaimProvider(api_key="test-key", base_url="https://mock.deepseek.test")
        known_iri = next(term["iri"] for term in provider.terms if term["iri"].endswith("#InformationEntity"))
        response = {
            "choices": [{"message": {"content": json.dumps({"claims": [{
                "statement": "装备 | status | in testing",
                "subject": "装备", "predicate": "status", "value": "in testing",
                "matchKey": "装备|status", "lineStart": 1, "lineEnd": 1,
                "analysisType": "source-fact",
                "concepts": [known_iri, "https://example.invalid/not-in-tbox"],
                "uncertainty": ["needs-review"],
            }]})}}],
        }
        opener = MagicMock()
        opener.open.return_value = _MockResponse(response)
        with patch.object(urllib.request, "build_opener", return_value=opener):
            claims = provider.extract(self.DOCUMENT)
        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0]["subject"], "装备")
        self.assertEqual(claims[0]["predicate"], "status")
        self.assertEqual(claims[0]["quote"], "装备 | status | in testing")
        self.assertEqual([item["iri"] for item in claims[0]["concepts"]], [known_iri])
        self.assertIn("llm-proposed", claims[0]["uncertainty"])
        self.assertEqual(claims[0]["recommendation"]["analysisType"], "source-fact")
        self.assertEqual(claims[0]["recommendation"]["model"], "deepseek-chat")
        sent = json.loads(opener.open.call_args.args[0].data.decode("utf-8"))
        self.assertEqual(sent["model"], "deepseek-chat")
        self.assertNotIn("thinking", sent)
        self.assertNotIn("reasoning_effort", sent)
        self.assertNotIn("test-key", json.dumps(sent))

    def test_provider_error_falls_back_and_marks_uncertainty(self):
        provider = DeepSeekClaimProvider(api_key="test-key")
        with patch.object(urllib.request, "build_opener", side_effect=OSError("offline")):
            claims = provider.extract(self.DOCUMENT)
        self.assertTrue(claims)
        self.assertTrue(all("llm-fallback" in claim["uncertainty"] for claim in claims))

    def test_exact_v4_model_is_confirmed_from_response_not_only_request(self):
        provider = DeepSeekClaimProvider(api_key="private-test-key", model="deepseek-v4-pro")
        opener = MagicMock()
        opener.open.return_value = _MockResponse({"model": "deepseek-v4-pro", "choices": [{"message": {"content": '{"ok":true}'}}],
                                                  "usage": {"prompt_tokens": 10, "completion_tokens": 4}})
        with patch.object(urllib.request, "build_opener", return_value=opener):
            self.assertEqual(provider._request_json([{"role": "user", "content": "Return JSON."}]), {"ok": True})
        self.assertTrue(provider.metadata()["lastCall"]["modelConfirmed"])
        self.assertEqual(provider.metadata()["lastCall"]["actualModel"], "deepseek-v4-pro")
        self.assertNotIn("private-test-key", json.dumps(provider.metadata()))
        payload = json.loads(opener.open.call_args.args[0].data)
        self.assertEqual(payload["thinking"], {"type": "enabled"})
        self.assertEqual(payload["reasoning_effort"], "low")

    def test_v4_graph_mode_disables_thinking_without_reasoning_effort(self):
        provider = DeepSeekClaimProvider(api_key="private-test-key", model="deepseek-v4-pro")
        opener = MagicMock()
        opener.open.return_value = _MockResponse({"model": "deepseek-v4-pro", "choices": [{"finish_reason": "stop", "message": {"content": '{"nodes":[],"edges":[]}'}}]})
        with patch.object(urllib.request, "build_opener", return_value=opener):
            self.assertEqual(provider._request_json([{"role": "user", "content": "Extract quoted objects."}], thinking=False), {"nodes": [], "edges": []})
        payload = json.loads(opener.open.call_args.args[0].data)
        self.assertEqual(payload["model"], "deepseek-v4-pro")
        self.assertEqual(payload["thinking"], {"type": "disabled"})
        self.assertNotIn("reasoning_effort", payload)
        self.assertEqual(payload["max_tokens"], 8192)
        self.assertEqual(provider.last_call["actualModel"], "deepseek-v4-pro")
        self.assertTrue(provider.last_call["modelConfirmed"])

    def test_legacy_model_disable_flag_does_not_add_v4_request_options(self):
        provider = DeepSeekClaimProvider(api_key="synthetic-test-key", model="deepseek-chat")
        opener = MagicMock()
        opener.open.return_value = _MockResponse({"model": "deepseek-chat", "choices": [{"message": {"content": '{"ok":true}'}}]})
        with patch.object(urllib.request, "build_opener", return_value=opener):
            provider._request_json([{"role": "user", "content": "Return JSON."}], thinking=False)
        payload = json.loads(opener.open.call_args.args[0].data)
        self.assertNotIn("thinking", payload)
        self.assertNotIn("reasoning_effort", payload)

    def test_trend_and_quantitative_stages_request_v4_with_low_thinking(self):
        provider = DeepSeekTrendProvider(api_key="synthetic-test-key", model="deepseek-v4-pro")
        materials = [
            {"itemId": "a", "title": "Controlled trial", "sourceUri": "https://a.example/trial", "sourceVersion": "1", "contentHash": "a" * 64, "body": "Acme interoperability test was reported with human oversight."},
            {"itemId": "b", "title": "Evaluation context", "sourceUri": "https://b.example/context", "sourceVersion": "1", "contentHash": "b" * 64, "body": "A public controlled prototype evaluation remains incomplete."},
        ]
        trend = {"findings": [{"id": "f1", "title": "Public trial", "judgment": "A reported prototype warrants further public evaluation, without deployment conclusions.", "evidenceIds": ["a", "b"], "confidence": "low", "uncertainty": ["test-conditions-not-verified"]}]}
        opener = MagicMock()
        opener.open.side_effect = [_MockResponse({"model": "deepseek-v4-pro", "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(payload)}}]}) for payload in (trend, {"metrics": [], "gaps": ["No measured values."]})]
        with patch.object(urllib.request, "build_opener", return_value=opener):
            report = provider.generate(task_id="t1", title="Public trial", question="What public evidence exists?", horizon_years=5, materials=materials)
        self.assertEqual(report["analysisType"], "model-assessment")
        self.assertEqual(report["model"], "deepseek-v4-pro")
        self.assertEqual(opener.open.call_count, 2)
        for call in opener.open.call_args_list:
            payload = json.loads(call.args[0].data)
            self.assertEqual(payload["model"], "deepseek-v4-pro")
            self.assertEqual(payload["thinking"], {"type": "enabled"})
            self.assertEqual(payload["reasoning_effort"], "low")
        self.assertTrue(provider.claim_provider.last_call["modelConfirmed"])

    def test_v4_mismatch_or_missing_response_model_cannot_be_labeled_success(self):
        for actual in ["deepseek-flash", None]:
            with self.subTest(actual=actual):
                provider = DeepSeekClaimProvider(api_key="test-key", model="deepseek-v4-pro")
                opener = MagicMock()
                opener.open.return_value = _MockResponse({"model": actual, "choices": [{"message": {"content": '{"ok":true}'}}]})
                with patch.object(urllib.request, "build_opener", return_value=opener), self.assertRaises(ValueError):
                    provider._request_json([{"role": "user", "content": "Return JSON."}])
                self.assertFalse(provider.metadata()["lastCall"]["modelConfirmed"])

    def test_environment_selection_never_requires_key(self):
        with patch.dict("os.environ", {"CRK_LLM_PROVIDER": "deepseek", "DEEPSEEK_API_KEY": "", "CRK_LLM_API_KEY": ""}, clear=False):
            self.assertIs(type(build_claim_provider()), DeterministicClaimProvider)
        with patch.dict("os.environ", {"CRK_LLM_PROVIDER": "deepseek", "DEEPSEEK_API_KEY": "", "CRK_LLM_API_KEY": "test-key"}, clear=False):
            provider = build_claim_provider()
            self.assertIsInstance(provider, DeepSeekClaimProvider)
            self.assertTrue(provider.configured)
            self.assertNotIn("test-key", json.dumps(provider.metadata()))

    def test_invalid_evidence_lines_are_discarded_not_clamped(self):
        provider = DeepSeekClaimProvider(api_key="test-key")
        valid = {"statement": "采购计划存在", "lineStart": 2, "lineEnd": 2, "concepts": []}
        invalid = [{"statement": "unsupported", "lineStart": a, "lineEnd": b} for a, b in [(0, 1), (1, 3), ("1", 1), (True, 1), (2, 1)]]
        claims = provider._normalize(self.DOCUMENT, {"claims": invalid + [valid]})
        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0]["quote"], "采购 | status | planned")
        self.assertEqual(claims[0]["recommendation"]["analysisType"], "inference")
        with self.assertRaises(ValueError):
            provider._normalize(self.DOCUMENT, {"claims": invalid})


if __name__ == "__main__":
    unittest.main()
