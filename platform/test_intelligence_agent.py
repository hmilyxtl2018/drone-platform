"""Meaningful persistence and governance checks for the intelligence demo."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
import urllib.error
from unittest.mock import patch
from pathlib import Path

try:
    from intelligence_agent import IntelligenceAgent, DeterministicTrendProvider, DeepSeekTrendProvider, _build_insight_layer
    from intelligence_store import IntelligenceConflict, IntelligenceStore, IntelligenceValidationError
except ModuleNotFoundError:  # pragma: no cover
    from .intelligence_agent import IntelligenceAgent, DeterministicTrendProvider, DeepSeekTrendProvider, _build_insight_layer
    from .intelligence_store import IntelligenceConflict, IntelligenceStore, IntelligenceValidationError


class IntelligenceAgentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.agent = IntelligenceAgent(IntelligenceStore(Path(self.temp.name) / "intelligence.sqlite"))

    def tearDown(self):
        self.temp.cleanup()

    def _submit(self, body: str, version: str = "1"):
        return self.agent.ingest({
            "title": "Retention policy", "sourceUri": "https://example.test/policy",
            "sourceVersion": version, "body": body, "accessBasis": "public", "sourceReliability": "primary",
        })

    def test_ingest_hash_dedup_and_evidence_locator(self):
        first = self._submit("Retention | days | 30")
        duplicate = self._submit("Retention | days | 30")
        self.assertFalse(first["duplicate"])
        self.assertTrue(duplicate["duplicate"])
        candidate = first["candidates"][0]
        self.assertEqual(candidate["evidence"]["lineStart"], 1)
        self.assertEqual(len(candidate["evidence"]["contentHash"]), 64)
        self.assertTrue(candidate["id"])

    def test_publication_requires_review_and_conflict_resolution(self):
        first = self._submit("Retention | days | 30")
        candidate = first["candidates"][0]
        with self.assertRaises(IntelligenceConflict):
            self.agent.publish({"reviewer": "operator", "candidateIds": [candidate["candidateId"]]})
        self.agent.review(candidate["candidateId"], {"action": "accept", "reviewer": "operator", "reason": "verified", "expectedRevision": 1})
        self.assertEqual(self.agent.publish({"reviewer": "operator", "candidateIds": [candidate["candidateId"]]})["version"], "v0001")

        newer = self._submit("Retention | days | 60", version="2")
        updated = newer["candidates"][0]
        self.assertTrue(updated["conflictIds"])
        self.agent.review(updated["candidateId"], {"action": "accept", "reviewer": "operator", "reason": "new source supersedes old", "expectedRevision": 1, "resolveConflict": "replace-existing"})
        self.assertEqual(self.agent.publish({"reviewer": "operator", "candidateIds": [updated["candidateId"]]})["version"], "v0002")

    def test_stale_review_and_immutable_document_are_rejected(self):
        result = self._submit("Mode | value | public")
        candidate = result["candidates"][0]
        with self.assertRaises(IntelligenceConflict):
            self.agent.review(candidate["candidateId"], {"action": "accept", "reviewer": "operator", "reason": "stale", "expectedRevision": 2})
        with self.assertRaises(IntelligenceValidationError):
            self.agent.review(candidate["candidateId"], {"action": "accept", "reviewer": "operator", "expectedRevision": 1})
        with self.assertRaises(sqlite3.DatabaseError):
            with self.agent.store._connect() as con:
                con.execute("UPDATE intelligence_document SET title='tampered'")

    def test_state_contains_claim_projection_and_audit_trace(self):
        result = self._submit("Mode | value | public")
        state = self.agent.state()
        self.assertEqual(len(state["claims"]), 1)
        self.assertEqual(state["claims"][0]["type"], "EvidenceClaim")
        self.assertGreaterEqual(len(state["events"]), 2)
        self.assertIn("provider", state)

    def test_edit_to_existing_key_detects_conflict(self):
        self._submit("Retention | days | 30")
        other = self._submit("Archive | mode | permanent", "2")["candidates"][0]
        edited = self.agent.review(other["candidateId"], {
            "action": "edit", "reviewer": "operator", "reason": "correct topic",
            "expectedRevision": 1, "editedStatement": "Retention | days | 60",
        })
        self.assertTrue(edited["conflictIds"])
        self.agent.review(other["candidateId"], {
            "action": "accept", "reviewer": "operator", "reason": "verified", "expectedRevision": 2,
        })
        with self.assertRaises(IntelligenceConflict):
            self.agent.publish({"reviewer": "operator", "candidateIds": [other["candidateId"]]})

    def test_matches_only_declared_ontology_resources(self):
        self.assertEqual(self.agent.provider._concepts("Retention | days | 30"), [])
        matches = self.agent.provider._concepts("InformationEntity")
        self.assertTrue(any(match["iri"].endswith("#InformationEntity") for match in matches))

    def test_rejection_cannot_supersede_other_candidates(self):
        old = self._submit("Retention | days | 30")["candidates"][0]
        new = self._submit("Retention | days | 60", "2")["candidates"][0]
        with self.assertRaises(IntelligenceValidationError):
            self.agent.review(new["candidateId"], {
                "action": "reject", "reviewer": "operator", "reason": "bad source",
                "expectedRevision": 1, "resolveConflict": "replace-existing",
            })
        self.assertEqual(self.agent.store.get_candidate(old["candidateId"])["status"], "under-review")

    def test_edit_creates_auditable_revision_before_acceptance(self):
        result = self._submit("Mode | value | public")
        candidate = result["candidates"][0]
        edited = self.agent.review(candidate["candidateId"], {"action": "edit", "reviewer": "operator", "reason": "clarified wording", "expectedRevision": 1, "editedStatement": "Mode | value | authorized"})
        self.assertEqual(edited["status"], "under-review")
        self.assertEqual(edited["revision"], 2)
        self.assertEqual(len(self.agent.store.list_candidate_revisions(candidate["candidateId"])), 2)
        with self.assertRaises(IntelligenceConflict):
            self.agent.publish({"reviewer": "operator", "candidateIds": [candidate["candidateId"]]})

    def test_trend_assessment_is_bounded_signal_summary_and_human_gated(self):
        materials = [
            {"itemId": "item-a", "title": "无人机采购项目", "sourceName": "Source A", "sourceUri": "https://a.example/news", "sourceVersion": "1", "publishedAt": "2026-09-01T00:00:00Z", "contentHash": "a" * 64, "body": "无人机采购 合同签订\n供应链扩大"},
            {"itemId": "item-b", "title": "Autonomous networked systems", "sourceName": "Source B", "sourceUri": "https://b.example/report", "sourceVersion": "2", "publishedAt": "2026-09-02T00:00:00Z", "contentHash": "b" * 64, "body": "Autonomous systems and networked data link in development."},
        ]
        draft = self.agent.create_assessment({"taskId": "osint-task-1", "title": "未来装备趋势", "question": "未来战争装备发展应用趋势是什么？", "horizonYears": 5, "sourceSnapshot": materials})["assessment"]
        self.assertEqual(draft["status"], "under-review")
        self.assertEqual(draft["analysisType"], "signal-summary")
        self.assertGreaterEqual(draft["summary"]["statistics"]["sourceCount"], 2)
        self.assertTrue(draft["evidenceRefs"][0]["quote"])
        with self.assertRaises(IntelligenceConflict):
            self.agent.publish_assessment(draft["assessmentId"], {"reviewer": "operator", "expectedRevision": 1})
        accepted = self.agent.review_assessment(draft["assessmentId"], {"action": "accept", "reviewer": "operator", "reason": "核验引用与范围", "expectedRevision": 1})
        self.assertEqual(accepted["status"], "accepted")
        self.assertEqual(accepted["revision"], 2)
        with self.assertRaises(IntelligenceConflict):
            self.agent.review_assessment(draft["assessmentId"], {"action": "reject", "reviewer": "stale-client", "reason": "stale", "expectedRevision": 1})
        published = self.agent.publish_assessment(draft["assessmentId"], {"reviewer": "operator", "expectedRevision": accepted["revision"]})
        self.assertEqual(published["status"], "published")

    def test_assessment_rejects_client_unknown_or_empty_sources(self):
        with self.assertRaises(IntelligenceValidationError):
            self.agent.create_assessment({"taskId": "t", "question": "q", "title": "x", "sourceSnapshot": []})

    def test_deepseek_trend_requires_cross_material_citations(self):
        provider = DeepSeekTrendProvider(api_key="test-key", base_url="https://mock.deepseek.test")
        provider.claim_provider._request_json = lambda messages: {"executiveSummary": "仅单源", "findings": [{"title": "bad", "judgment": "bad", "evidenceIds": ["a"], "confidence": "high"}]}
        materials = [
            {"itemId": "a", "title": "A", "sourceUri": "https://a.example/x", "sourceVersion": "1", "contentHash": "a" * 64, "body": "无人"},
            {"itemId": "b", "title": "B", "sourceUri": "https://b.example/x", "sourceVersion": "1", "contentHash": "b" * 64, "body": "采购"},
        ]
        result = provider.generate(task_id="t", title="x", question="q", horizon_years=5, materials=materials)
        self.assertEqual(result["provider"], "deterministic-trend-provider")
        self.assertTrue(any("回退" in warning for warning in result["warnings"]))

    def test_trend_model_counts_only_sent_and_cited_materials(self):
        provider = DeepSeekTrendProvider(api_key="test-key", base_url="https://mock.deepseek.test")
        materials = [{"itemId": f"m-{i}", "title": f"Public technology report {i}", "sourceName": "Publisher", "sourceUri": f"https://publisher{i % 2}.example/report/{i}", "sourceVersion": "1", "publishedAt": "2026-09-02T00:00:00Z", "contentHash": str(i % 10) * 64, "body": "Unmanned systems technology testing."} for i in range(31)]
        response = {"executiveSummary": "uncited summary must not survive", "findings": [
            {"title": "Valid finding", "judgment": "技术测试信号重复出现", "evidenceIds": ["m-0", "m-1"], "confidence": "high", "maturity": "测试阶段待核验", "procurementIndustry": "采购证据不足", "applicationImpact": "高层应用待核验", "contraryEvidence": "未发现相反材料"},
            {"title": "Unsent item", "judgment": "不可接受", "evidenceIds": ["m-0", "m-30"], "confidence": "medium"},
        ]}
        with patch.object(provider.claim_provider, "_request_json", return_value=response) as request:
            result = provider.generate(task_id="t", title="x", question="q", horizon_years=5, materials=materials)
        self.assertEqual(result["provider"], "deepseek-trend-provider")
        self.assertEqual(len(result["summary"]["findings"]), 1)
        finding = result["summary"]["findings"][0]
        self.assertEqual(finding["confidence"], "low")
        self.assertEqual(finding["maturity"], "测试阶段待核验")
        self.assertEqual(result["summary"]["statistics"]["selectedItems"], 31)
        # Trend requests now cover the complete snapshot in bounded batches;
        # no document is silently discarded because of the model context size.
        self.assertEqual(result["summary"]["statistics"]["modelInputItems"], 31)
        self.assertEqual(result["summary"]["statistics"]["citedItems"], 2)
        self.assertEqual(result["summary"]["statistics"]["citedPublisherCount"], 2)
        prompt = request.call_args.args[0][1]["content"]
        self.assertIn("PUBLISHER: Publisher", prompt)
        self.assertIn("PUBLISHED_AT: 2026-09-02", prompt)
        self.assertIn("SOURCE m-30", prompt)
        self.assertNotIn("uncited summary", result["summary"]["executiveSummary"])

    def test_trend_tail_batch_keeps_two_sources_without_increasing_batch_count(self):
        provider = DeepSeekTrendProvider(api_key="test-key", base_url="https://mock.deepseek.test")
        materials = [{
            "itemId": f"m-{i}", "title": f"Public technology report {i}",
            "sourceName": f"Publisher {i}", "sourceUri": f"https://publisher{i}.example/report/{i}",
            "sourceVersion": "1", "publishedAt": "2026-09-02T00:00:00Z",
            "contentHash": str(i % 10) * 64, "body": "Unmanned systems technology testing."
        } for i in range(25)]

        def response_for(messages, **_kwargs):
            content = messages[1]["content"]
            ids = [line.split("SOURCE ", 1)[1].strip() for line in content.splitlines() if line.startswith("SOURCE ")]
            # Every model finding must cite two sources.  The fourth logical
            # batch would otherwise contain only m-24; the implementation
            # should send m-23 as its context source.
            self.assertGreaterEqual(len(ids), 2)
            return {"findings": [{
                "title": f"direction-{ids[-1]}", "judgment": f"Signal supported by {ids[0]} and {ids[-1]}",
                "evidenceIds": ids[:2], "confidence": "medium",
            }]}

        with patch.object(provider.claim_provider, "_request_json", side_effect=response_for) as request, \
             patch.object(provider, "quantify", return_value=DeterministicTrendProvider().quantify(materials)):
            result = provider.generate(task_id="t", title="x", question="q", horizon_years=5, materials=materials)

        self.assertEqual(request.call_count, 4)
        last_prompt = request.call_args_list[-1].args[0][1]["content"]
        self.assertIn("SOURCE m-23", last_prompt)
        self.assertIn("SOURCE m-24", last_prompt)
        stats = result["summary"]["statistics"]
        self.assertEqual(stats["modelInputBatches"], 4)
        self.assertEqual(stats["successfulModelInputBatches"], 4)
        self.assertEqual(stats["failedModelInputBatches"], 0)
        self.assertEqual(stats["modelInputItems"], 25)

    def test_multibatch_reused_model_finding_ids_persist_as_unique_report_ids(self):
        provider = DeepSeekTrendProvider(api_key="test-key", base_url="https://mock.deepseek.test")
        materials = [{
            "itemId": f"m-{index}", "title": f"Public technology report {index}",
            "sourceName": f"Publisher {index}", "sourceUri": f"https://publisher{index}.example/report/{index}",
            "sourceVersion": "1", "publishedAt": "2026-09-02T00:00:00Z",
            "contentHash": f"{index:064x}", "body": f"Public interoperability test report {index}."
        } for index in range(16)]

        def repeat_batch_local_ids(messages, **_kwargs):
            ids = [line.split("SOURCE ", 1)[1].strip() for line in messages[1]["content"].splitlines() if line.startswith("SOURCE ")]
            batch = 0 if "m-0" in ids else 1
            # Independent responses commonly restart numbering at finding-1.
            # Include a suffix-shaped model ID to exercise collision handling
            # as well as identical IDs from different material batches.
            return {"findings": [{
                "id": model_id, "title": f"Direction {batch}-{index}",
                "judgment": f"Distinct public test signal {batch}-{index} requires further verification.",
                "evidenceIds": ids[:2], "confidence": "medium",
            } for index, model_id in enumerate(("finding-1", "finding-1-2"))]}

        agent = IntelligenceAgent(self.agent.store, trend_provider=provider)
        with patch.object(provider.claim_provider, "_request_json", side_effect=repeat_batch_local_ids) as request, \
             patch.object(provider, "quantify", return_value=DeterministicTrendProvider().quantify(materials)):
            first = agent.create_assessment({
                "taskId": "reused-local-finding-ids", "title": "Public trends", "question": "Which test signals recur?",
                "horizonYears": 5, "sourceSnapshot": materials,
            })
            repeated = agent.create_assessment({
                "taskId": "reused-local-finding-ids", "title": "Public trends", "question": "Which test signals recur?",
                "horizonYears": 5, "sourceSnapshot": materials,
            })

        self.assertEqual(request.call_count, 4)
        assessment = self.agent.store.get_assessment(first["assessment"]["assessmentId"])
        findings = assessment["summary"]["findings"]
        self.assertEqual(len(findings), 4)
        ids = [finding["id"] for finding in findings]
        self.assertEqual(len(set(ids)), 4)
        self.assertEqual([finding["modelId"] for finding in findings], ["finding-1", "finding-1-2", "finding-1", "finding-1-2"])
        self.assertTrue(repeated["duplicate"])
        self.assertEqual(repeated["assessment"]["assessmentId"], assessment["assessmentId"])
        self.assertEqual([finding["id"] for finding in repeated["assessment"]["summary"]["findings"]], ids)

    def test_trend_compaction_retries_entire_batch_and_reports_failed_coverage(self):
        provider = DeepSeekTrendProvider(api_key="test-key", base_url="https://mock.deepseek.test")
        materials = [{
            "itemId": f"m-{i}", "title": f"Public technology report {i}",
            "sourceName": f"Publisher {i}", "sourceUri": f"https://publisher{i}.example/report/{i}",
            "sourceVersion": "1", "publishedAt": "2026-09-02T00:00:00Z",
            "contentHash": str(i % 10) * 64, "body": "Unmanned systems technology testing."
        } for i in range(8)]
        calls = 0

        def response_after_compaction(messages, **_kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise ValueError("provider output exhausted its token budget")
            content = messages[1]["content"]
            ids = [line.split("SOURCE ", 1)[1].strip() for line in content.splitlines() if line.startswith("SOURCE ")]
            self.assertEqual(set(ids), {f"m-{i}" for i in range(8)})
            return {"findings": [{
                "title": "full batch", "judgment": "All sources were retained after compaction",
                "evidenceIds": ["m-0", "m-7"], "confidence": "medium",
            }]}

        with patch.object(provider.claim_provider, "_request_json", side_effect=response_after_compaction) as request, \
             patch.object(provider, "quantify", return_value=DeterministicTrendProvider().quantify(materials)):
            result = provider.generate(task_id="t", title="x", question="q", horizon_years=5, materials=materials)

        self.assertEqual(request.call_count, 2)
        stats = result["summary"]["statistics"]
        self.assertEqual(stats["modelInputItems"], 8)
        self.assertEqual(stats["successfulModelInputBatches"], 1)
        self.assertEqual(stats["failedModelInputBatches"], 0)

    def test_trend_failed_batch_is_excluded_from_model_input_coverage(self):
        provider = DeepSeekTrendProvider(api_key="test-key", base_url="https://mock.deepseek.test")
        materials = [{
            "itemId": f"m-{i}", "title": f"Public technology report {i}",
            "sourceName": f"Publisher {i}", "sourceUri": f"https://publisher{i}.example/report/{i}",
            "sourceVersion": "1", "publishedAt": "2026-09-02T00:00:00Z",
            "contentHash": str(i % 10) * 64, "body": "Unmanned systems technology testing."
        } for i in range(16)]

        def failing_second_batch(messages, **_kwargs):
            content = messages[1]["content"]
            ids = [line.split("SOURCE ", 1)[1].strip() for line in content.splitlines() if line.startswith("SOURCE ")]
            if "m-8" in ids:
                raise ValueError("provider unavailable")
            return {"findings": [{
                "title": "first batch", "judgment": "First batch has a citable signal",
                "evidenceIds": ["m-0", "m-1"], "confidence": "medium",
            }]}

        with patch.object(provider.claim_provider, "_request_json", side_effect=failing_second_batch), \
             patch.object(provider, "quantify", return_value=DeterministicTrendProvider().quantify(materials)):
            result = provider.generate(task_id="t", title="x", question="q", horizon_years=5, materials=materials)

        stats = result["summary"]["statistics"]
        self.assertEqual(stats["modelInputItems"], 8)
        self.assertEqual(stats["successfulModelInputBatches"], 1)
        self.assertEqual(stats["failedModelInputBatches"], 1)
        self.assertTrue(any("失败" in gap for gap in result["summary"]["gaps"]))

    def test_trend_transport_failure_does_not_skip_later_batches(self):
        provider = DeepSeekTrendProvider(api_key="test-key", base_url="https://mock.deepseek.test")
        materials = [{
            "itemId": f"m-{i}", "title": f"Public technology report {i}",
            "sourceName": f"Publisher {i}", "sourceUri": f"https://publisher{i}.example/report/{i}",
            "sourceVersion": "1", "publishedAt": "2026-09-02T00:00:00Z",
            "contentHash": str(i % 10) * 64, "body": "Unmanned systems technology testing."
        } for i in range(16)]

        for error in (TimeoutError("timed out"), urllib.error.HTTPError("https://mock.deepseek.test", 503, "unavailable", {}, None)):
            with self.subTest(error=type(error).__name__):
                def fail_first_batch(messages, **_kwargs):
                    ids = [line.split("SOURCE ", 1)[1].strip() for line in messages[1]["content"].splitlines() if line.startswith("SOURCE ")]
                    if "m-0" in ids:
                        raise error
                    return {"findings": [{
                        "title": "later batch", "judgment": "Later source batch remains available",
                        "evidenceIds": ["m-8", "m-9"], "confidence": "medium",
                    }]}

                with patch.object(provider.claim_provider, "_request_json", side_effect=fail_first_batch) as request, \
                     patch.object(provider, "quantify", return_value=DeterministicTrendProvider().quantify(materials)):
                    result = provider.generate(task_id="t", title="x", question="q", horizon_years=5, materials=materials)
                self.assertEqual(request.call_count, 2)
                self.assertEqual(result["provider"], "deepseek-trend-provider")
                stats = result["summary"]["statistics"]
                self.assertEqual(stats["modelInputItems"], 8)
                self.assertEqual(stats["successfulModelInputBatches"], 1)
                self.assertEqual(stats["failedModelInputBatches"], 1)

    def test_quantification_transport_failure_keeps_later_batches(self):
        provider = DeepSeekTrendProvider(api_key="test-key", base_url="https://mock.deepseek.test")
        materials = [{
            "itemId": f"m-{i}", "title": f"Public technology report {i}",
            "sourceName": f"Publisher {i}", "sourceUri": f"https://publisher{i}.example/report/{i}",
            "sourceVersion": "1", "publishedAt": "2026-09-02T00:00:00Z",
            "contentHash": str(i % 10) * 64, "body": "Unmanned systems technology testing."
        } for i in range(16)]

        def fail_first_quant_batch(messages, **_kwargs):
            ids = [line.split("SOURCE ", 1)[1].strip() for line in messages[1]["content"].splitlines() if line.startswith("SOURCE ")]
            if "m-0" in ids:
                raise TimeoutError("timed out")
            return {"metrics": [], "gaps": ["No numeric metric in this batch."]}

        with patch.object(provider.claim_provider, "_request_json", side_effect=fail_first_quant_batch) as request:
            result = provider.quantify(materials)
        self.assertEqual(request.call_count, 2)
        self.assertEqual(result["statistics"]["metricCount"], 0)
        self.assertTrue(any("量化材料批次调用失败" in gap for gap in result["gaps"]))

    def test_rule_signals_avoid_substrings_and_duplicate_publisher_confidence(self):
        materials = [
            {"itemId": "a", "title": "A said good", "sourceUri": "https://publisher.example/a", "sourceVersion": "1", "contentHash": "a" * 64, "body": "Unmanned technology\nA report"},
            {"itemId": "b", "title": "Aerospace report", "sourceUri": "https://publisher.example/b", "sourceVersion": "1", "contentHash": "b" * 64, "body": "Unmanned technology"},
        ]
        result = DeterministicTrendProvider().generate(task_id="t", title="x", question="q", horizon_years=5, materials=materials)
        self.assertEqual(result["summary"]["statistics"]["distinctPublisherCount"], 1)
        self.assertTrue(all(f["confidence"] == "low" for f in result["summary"]["findings"]))
        self.assertFalse(any(f["title"] == "传感与空间支援" for f in result["summary"]["findings"]))
        self.assertEqual(result["evidenceRefs"][0]["lineEnd"], 2)

    def test_insight_layer_drops_decision_text_that_repeats_trend_judgment(self):
        summary = {"findings": [{
            "id": "f1", "title": "受控互操作测试", "judgment": "受控互操作测试在公开材料中重复出现，但部署条件仍未知。",
            "decisionImpact": "受控互操作测试在公开材料中重复出现，但部署条件仍未知。",
            "manufacturingRequirements": [], "evidenceIds": ["a"], "confidence": "low",
        }]}
        layer = _build_insight_layer(summary)
        meaning = layer["findings"][0]["decisionMeaning"]
        self.assertNotEqual(meaning, summary["findings"][0]["judgment"])
        self.assertIn("补证优先级", meaning)

    def _maturity_report(self, maturity, *, cited_rating=None, uncited_rating=None):
        provider = DeepSeekTrendProvider(api_key="synthetic-test-key")
        materials = [
            {"itemId": "a", "title": "Controlled test", "sourceUri": "https://publisher-a.example/test", "sourceVersion": "1", "contentHash": "a" * 64, "body": "Acme interoperability demonstration was reported. " + (cited_rating or "")},
            {"itemId": "b", "title": "Independent context", "sourceUri": "https://publisher-b.example/context", "sourceVersion": "1", "contentHash": "b" * 64, "body": "A supervised laboratory prototype remains subject to evaluation."},
        ]
        if uncited_rating:
            materials.append({"itemId": "c", "title": "Uncited source", "sourceUri": "https://publisher-c.example/other", "sourceVersion": "1", "contentHash": "c" * 64, "body": uncited_rating})
        response = {"findings": [{"id": "f1", "title": "Reported trial", "judgment": "A reported controlled test supports limited public research, with deployment unknown.", "confidence": "medium", "evidenceIds": ["a", "b"], "uncertainty": ["reported-test"], "maturity": maturity}]}
        with patch.object(provider.claim_provider, "_request_json", return_value=response), patch.object(provider, "quantify", return_value={"version": 1, "scope": "public parameters", "metrics": [], "gaps": ["No quantitative test values."], "statistics": {"metricCount": 0, "equipmentCount": 0, "reportedTestCount": 0, "manufacturerClaimCount": 0, "programTargetCount": 0, "reportedObservationCount": 0}}):
            return provider.generate(task_id="t", title="Public maturity", question="What test stage is documented?", horizon_years=5, materials=materials)

    def test_maturity_rating_is_not_inferred_from_a_reported_demonstration(self):
        result = self._maturity_report("受控演示表明估计达到 TRL5-6。")
        self.assertEqual(result["analysisType"], "model-assessment")
        finding = result["summary"]["findings"][0]
        self.assertNotIn("TRL5-6", finding["maturity"])
        self.assertIn("等级证据不足", finding["maturity"])
        self.assertIn("unsubstantiated-maturity-rating-removed", finding["uncertainty"])
        self.assertEqual(finding["judgment"], "A reported controlled test supports limited public research, with deployment unknown.")

    def test_literal_maturity_rating_in_cited_source_is_retained_as_pending_claim(self):
        maturity = "Acme reports TRL5-6; conditions require review."
        result = self._maturity_report(maturity, cited_rating="Acme explicitly reports TRL5-6 for the demonstrator.")
        finding = result["summary"]["findings"][0]
        self.assertEqual(finding["maturity"], maturity)
        self.assertNotIn("unsubstantiated-maturity-rating-removed", finding["uncertainty"])
        self.assertIn("source-independence-not-verified", finding["uncertainty"])
        self.assertIn("llm-proposed-human-review-required", finding["uncertainty"])

    def test_rating_in_uncited_source_cannot_authorize_finding_maturity(self):
        result = self._maturity_report("TRL5-6", uncited_rating="Other demonstrator reached TRL5-6.")
        finding = result["summary"]["findings"][0]
        self.assertEqual(finding["evidenceIds"], ["a", "b"])
        self.assertIn("unsubstantiated-maturity-rating-removed", finding["uncertainty"])

    def test_one_supported_range_does_not_authorize_an_additional_unsupported_rating(self):
        result = self._maturity_report("Prototype TRL5-6, integration TRL7.", cited_rating="Prototype was described as TRL5-6.")
        finding = result["summary"]["findings"][0]
        self.assertIn("等级证据不足", finding["maturity"])
        self.assertIn("unsubstantiated-maturity-rating-removed", finding["uncertainty"])


if __name__ == "__main__":
    unittest.main()
