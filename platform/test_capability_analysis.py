"""Evidence-bound capability indicators and independently reviewed derivatives."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

try:
    from capability_analysis import normalize_capabilities, validate_capabilities
    from intelligence_agent import IntelligenceAgent, DeterministicTrendProvider, DeepSeekTrendProvider
    from intelligence_store import IntelligenceConflict, IntelligenceStore, IntelligenceValidationError
except ModuleNotFoundError:  # pragma: no cover
    from .capability_analysis import normalize_capabilities, validate_capabilities
    from .intelligence_agent import IntelligenceAgent, DeterministicTrendProvider, DeepSeekTrendProvider
    from .intelligence_store import IntelligenceConflict, IntelligenceStore, IntelligenceValidationError


QUOTE = "Cargo demonstrator lifted 1,200 pounds during a reported indoor test."
BODY = "Public test log 🛰️\n" + QUOTE + "\nOnly one trial was reported; outdoor conditions are unknown."


def material():
    return {
        "itemId": "item-cargo", "title": "Public cargo demonstrator test",
        "sourceName": "Test report publisher", "sourceUri": "https://public.example/test/cargo",
        "sourceVersion": "test-report-1", "publishedAt": "2026-09-20T00:00:00Z",
        "contentHash": hashlib.sha256(BODY.encode("utf-8")).hexdigest(), "body": BODY,
    }


def proposal(**changes):
    value = {
        "equipment": "Cargo demonstrator", "dimension": "运输保障", "metric": "试验载荷",
        "value": 1200, "unit": "pounds", "qualifier": "exact", "basis": "reported-test",
        "conditions": "Reported indoor test; one trial; outdoor conditions unknown.",
        "assessment": "报道的一次试验载荷，不能外推持续运行能力或可靠性。",
        "itemId": "item-cargo", "quote": QUOTE,
    }
    value.update(changes)
    return value


def capabilities():
    return normalize_capabilities({"metrics": [proposal()]}, [material()])


class CapabilityEvidenceTests(unittest.TestCase):
    def test_reported_number_unit_and_exact_quote_are_retained(self):
        result = capabilities()
        validate_capabilities(result, [material()])
        self.assertEqual(len(result["metrics"]), 1)
        metric = result["metrics"][0]
        self.assertEqual((metric["value"], metric["unit"], metric["basis"]), (1200, "pounds", "reported-test"))
        self.assertEqual(metric["evidence"]["quote"], QUOTE)
        self.assertEqual(result["statistics"], {
            "metricCount": 1, "equipmentCount": 1, "citedSourceCount": 1,
            "reportedTestCount": 1, "manufacturerClaimCount": 0,
            "programTargetCount": 0, "reportedObservationCount": 0,
        })

    def test_fabricated_numbers_conversions_and_absent_units_are_rejected(self):
        for changes in ({"value": 1300}, {"value": 544.31, "unit": "kg"}, {"unit": "kg"}):
            with self.subTest(changes=changes):
                result = normalize_capabilities({"metrics": [proposal(**changes)]}, [material()])
                self.assertEqual(result["metrics"], [])
                self.assertEqual(result["statistics"]["metricCount"], 0)
                self.assertTrue(any("未纳入" in gap for gap in result["gaps"]))

    def test_unit_must_be_a_whole_word_not_operations_substring(self):
        cases = (
            ("Operations teams reported a payload target of 4.", False),
            ("The reported payload RATIO target was 4.", True),
        )
        for quote, expected in cases:
            with self.subTest(quote=quote):
                source = {**material(), "body": quote}
                result = normalize_capabilities({"metrics": [proposal(value=4, unit="ratio", quote=quote)]}, [source])
                self.assertEqual(bool(result["metrics"]), expected)

    def test_sentence_final_numbers_preserve_decimal_boundaries(self):
        cases = (
            ("The reported payload ratio was 4.", 4, True),
            ("The reported payload ratio was 2.75.", 2.75, True),
            ("The reported payload ratio was 2.75.", 2, False),
            ("The reported payload ratio was 2.75.", 75, False),
            ("The payload in pounds was 1,200.", 1200, True),
        )
        for quote, value, expected in cases:
            with self.subTest(quote=quote, value=value):
                source = {**material(), "body": quote}
                unit = "pounds" if "pounds" in quote else "ratio"
                result = normalize_capabilities({"metrics": [proposal(value=value, unit=unit, quote=quote)]}, [source])
                self.assertEqual(bool(result["metrics"]), expected)

    def test_unknown_sources_and_fabricated_quotes_are_rejected(self):
        for changes in ({"itemId": "unknown"}, {"quote": QUOTE.replace("indoor", "outdoor")}):
            with self.subTest(changes=changes):
                self.assertEqual(normalize_capabilities({"metrics": [proposal(**changes)]}, [material()])["metrics"], [])

    def test_nonfinite_boolean_string_and_empty_values_are_rejected(self):
        for value in (float("nan"), float("inf"), float("-inf"), True, False, None, "", "1200"):
            with self.subTest(value=repr(value)):
                self.assertEqual(normalize_capabilities({"metrics": [proposal(value=value)]}, [material()])["metrics"], [])

    def test_missing_or_blank_required_fields_are_rejected(self):
        for field in ("equipment", "dimension", "metric", "conditions", "assessment", "unit", "quote", "basis", "qualifier"):
            for value in (None, "", "   "):
                with self.subTest(field=field, value=value):
                    self.assertEqual(normalize_capabilities({"metrics": [proposal(**{field: value})]}, [material()])["metrics"], [])

    def test_character_offsets_remain_exact_with_non_bmp_emoji(self):
        metric = capabilities()["metrics"][0]
        evidence = metric["evidence"]
        prefix = BODY.split(QUOTE)[0]
        self.assertEqual(evidence["startChar"], len(prefix))
        self.assertEqual(evidence["endChar"], len(prefix) + len(QUOTE))
        self.assertEqual(BODY[evidence["startChar"]:evidence["endChar"]], QUOTE)
        self.assertNotEqual(evidence["startChar"], len(prefix.encode("utf-16-le")) // 2)
        validate_capabilities(capabilities(), [material()])

    def test_model_metadata_and_counts_cannot_override_snapshot(self):
        row = proposal(id="forged-id", evidence={"sourceUri": "https://forged.example"}, startChar=0, endChar=2)
        result = normalize_capabilities({"metrics": [row], "statistics": {"metricCount": 999}}, [material()])
        metric = result["metrics"][0]
        self.assertEqual(metric["id"], "metric-1")
        self.assertEqual(metric["evidence"]["sourceUri"], material()["sourceUri"])
        self.assertEqual(metric["evidence"]["startChar"], BODY.index(QUOTE))
        self.assertEqual(result["statistics"]["metricCount"], 1)
        validate_capabilities(result, [material()])

    def test_persisted_forged_provenance_is_rejected(self):
        for field, bad in (
            ("sourceUri", "https://forged.example/article"), ("sourceVersion", "new-version"),
            ("contentHash", "0" * 64), ("publishedAt", "2020-01-01T00:00:00Z"),
            ("itemId", "unknown-source"),
        ):
            with self.subTest(field=field):
                result = capabilities()
                result["metrics"][0]["evidence"][field] = bad
                with self.assertRaises(ValueError):
                    validate_capabilities(result, [material()])

    def test_persisted_tampered_quote_or_offsets_are_rejected(self):
        for field, bad in (("quote", QUOTE.replace("indoor", "outdoor")), ("startChar", 0), ("startChar", True), ("endChar", len(BODY) + 1)):
            with self.subTest(field=field, bad=bad):
                result = capabilities()
                result["metrics"][0]["evidence"][field] = bad
                with self.assertRaises(ValueError):
                    validate_capabilities(result, [material()])

    def test_persisted_fabricated_statistics_are_rejected(self):
        for field in capabilities()["statistics"]:
            with self.subTest(field=field):
                result = capabilities()
                result["statistics"][field] += 1
                with self.assertRaises(ValueError):
                    validate_capabilities(result, [material()])

    def test_boolean_version_and_statistics_are_not_integer_counts(self):
        result = capabilities()
        result["version"] = True
        with self.assertRaises(ValueError):
            validate_capabilities(result, [material()])
        for field, value in capabilities()["statistics"].items():
            with self.subTest(field=field):
                result = capabilities()
                result["statistics"][field] = bool(value)
                with self.assertRaises(ValueError):
                    validate_capabilities(result, [material()])

    def test_duplicate_metric_proposals_are_counted_once(self):
        row = proposal()
        result = normalize_capabilities({"metrics": [row, deepcopy(row), deepcopy(row)]}, [material()])
        self.assertEqual(len(result["metrics"]), 1)
        self.assertEqual(result["statistics"]["metricCount"], 1)
        self.assertEqual(result["statistics"]["reportedTestCount"], 1)
        validate_capabilities(result, [material()])

    def test_whitespace_variants_do_not_inflate_metric_counts(self):
        rows = [
            proposal(),
            proposal(equipment=" Cargo demonstrator "),
            proposal(metric="\t试验载荷\n"),
            proposal(unit=" pounds "),
        ]
        result = normalize_capabilities({"metrics": rows}, [material()])
        self.assertEqual(len(result["metrics"]), 1)
        self.assertEqual(result["metrics"][0]["equipment"], "Cargo demonstrator")
        self.assertEqual(result["metrics"][0]["metric"], "试验载荷")
        self.assertEqual(result["metrics"][0]["unit"], "pounds")
        self.assertEqual(result["statistics"]["metricCount"], 1)
        self.assertEqual(result["statistics"]["equipmentCount"], 1)
        validate_capabilities(result, [material()])

    def test_deepseek_adapter_filters_mocked_response_without_network(self):
        provider = DeepSeekTrendProvider(api_key="synthetic-test-key", base_url="https://mock.example")
        response = {"metrics": [proposal(), proposal(value=1300)], "gaps": ["Only one reported trial."]}
        with patch.object(provider.claim_provider, "_request_json", return_value=response) as request:
            result = provider.quantify([material()])
        self.assertEqual(result["statistics"]["metricCount"], 1)
        self.assertIn("Only one reported trial.", result["gaps"])
        request.assert_called_once()
        self.assertIn(QUOTE, request.call_args.args[0][1]["content"])
        validate_capabilities(result, [material()])


class CapabilityDerivativeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = IntelligenceStore(Path(self.temp.name) / "capability.sqlite")
        self.provider = Mock(spec=DeterministicTrendProvider)
        self.provider.quantify.side_effect = lambda materials: normalize_capabilities({"metrics": [proposal()]}, materials)
        self.agent = IntelligenceAgent(self.store, provider=Mock(), trend_provider=self.provider)
        self.original_payload = DeterministicTrendProvider().generate(
            task_id="isolated-capability-task", title="公开装备技术研判", question="公开试验与保障指标是什么？",
            horizon_years=5, materials=[material()],
        )
        self.original = self.store.create_assessment(self.original_payload)["assessment"]

    def tearDown(self):
        self.temp.cleanup()

    def _publish_original(self):
        aid = self.original["assessmentId"]
        accepted = self.store.review_assessment(aid, {
            "action": "accept", "reviewer": "isolated-test-reviewer", "reason": "Synthetic fixture only.", "expectedRevision": 1,
        })
        return self.store.publish_assessment(aid, {"reviewer": "isolated-test-reviewer", "expectedRevision": accepted["revision"]})

    def test_derived_draft_preserves_original_and_never_inherits_review(self):
        published = self._publish_original()
        original_before = deepcopy(self.store.get_assessment(published["assessmentId"]))
        derivative = self.agent.quantify_assessment(published["assessmentId"])["assessment"]
        self.assertNotEqual(derivative["assessmentId"], published["assessmentId"])
        self.assertEqual(derivative["status"], "under-review")
        self.assertEqual(derivative["revision"], 1)
        self.assertIsNone(derivative["latestReviewId"])
        self.assertIsNone(derivative["publishedAt"])
        self.assertIsNone(derivative["publishedBy"])
        self.assertEqual(self.store.list_assessment_reviews(derivative["assessmentId"]), [])
        self.assertEqual(self.store.get_assessment(published["assessmentId"]), original_before)
        self.assertEqual(derivative["sourceSnapshot"], original_before["sourceSnapshot"])
        self.assertEqual(derivative["summary"]["findings"], original_before["summary"]["findings"])
        self.assertEqual(derivative["summary"]["executiveSummary"], original_before["summary"]["executiveSummary"])
        self.assertEqual(derivative["summary"]["capabilityAssessment"]["basedOnAssessmentId"], published["assessmentId"])
        self.assertEqual(derivative["summary"]["capabilityAssessment"]["statistics"]["metricCount"], 1)
        self.provider.quantify.assert_called_once_with(original_before["sourceSnapshot"])

    def test_derivative_publication_requires_its_own_human_review(self):
        published = self._publish_original()
        derivative = self.agent.quantify_assessment(published["assessmentId"])["assessment"]
        aid = derivative["assessmentId"]
        with self.assertRaises(IntelligenceConflict):
            self.agent.publish_assessment(aid, {"reviewer": "isolated-test-reviewer", "expectedRevision": 1})
        accepted = self.agent.review_assessment(aid, {
            "action": "accept", "reviewer": "isolated-test-reviewer", "reason": "Verified derived fixture independently.", "expectedRevision": 1,
        })
        final = self.agent.publish_assessment(aid, {"reviewer": "isolated-test-reviewer", "expectedRevision": accepted["revision"]})
        self.assertEqual(final["status"], "published")
        self.assertEqual(len(self.store.list_assessment_reviews(aid)), 1)

    def test_withdrawn_assessment_cannot_spawn_derivative_or_call_model(self):
        published = self._publish_original()
        self.store.withdraw_assessment_publication(published["assessmentId"], {
            "actor": "isolated-test-recovery", "reason": "Synthetic publication withdrawn.",
        })
        count_before = len(self.store.list_assessments())
        with self.assertRaises(IntelligenceValidationError):
            self.agent.quantify_assessment(published["assessmentId"])
        self.provider.quantify.assert_not_called()
        self.assertEqual(len(self.store.list_assessments()), count_before)

    def test_withdrawal_during_model_call_blocks_derivative_at_store_boundary(self):
        published = self._publish_original()
        aid = published["assessmentId"]
        count_before = len(self.store.list_assessments())

        def withdraw_while_extracting(materials):
            self.store.withdraw_assessment_publication(aid, {
                "actor": "isolated-test-recovery", "reason": "Withdrawn while extraction is in flight.",
            })
            return normalize_capabilities({"metrics": [proposal()]}, materials)

        self.provider.quantify.side_effect = withdraw_while_extracting
        with self.assertRaises(IntelligenceConflict):
            self.agent.quantify_assessment(aid)
        self.provider.quantify.assert_called_once()
        self.assertEqual(self.store.get_assessment(aid)["status"], "withdrawn")
        self.assertEqual(len(self.store.list_assessments()), count_before)
        created_events = [e for e in self.store.list_events() if e["eventType"] == "TrendAssessmentDraftCreated"]
        self.assertEqual(len(created_events), 1)

    def test_store_rejects_forged_capability_before_persisting(self):
        for mutation in ("metadata", "statistics", "number", "quote"):
            with self.subTest(mutation=mutation):
                forged = capabilities()
                if mutation == "metadata":
                    forged["metrics"][0]["evidence"]["contentHash"] = "0" * 64
                elif mutation == "statistics":
                    forged["statistics"]["metricCount"] = 999
                elif mutation == "number":
                    forged["metrics"][0]["value"] = 1300
                else:
                    forged["metrics"][0]["evidence"]["quote"] = QUOTE.replace("indoor", "outdoor")
                payload = deepcopy(self.original_payload)
                payload["summary"]["capabilityAssessment"] = forged
                with self.assertRaises(IntelligenceValidationError):
                    self.store.create_assessment(payload)
                self.assertEqual(len(self.store.list_assessments()), 1)


if __name__ == "__main__":
    unittest.main()
