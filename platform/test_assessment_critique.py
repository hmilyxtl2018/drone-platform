"""A model can flag evidence gaps, never certify its own conclusions."""
import unittest
from unittest.mock import Mock

from assessment_critique import critique_assessment


class AssessmentCritiqueTests(unittest.TestCase):
    def setUp(self):
        self.assessment = {
            "question": "Public technology maturity?",
            "sourceSnapshot": [{"itemId": "s1", "sourceUri": "https://example.test/a", "body": "A prototype test was reported. Conditions remain unknown."}],
            "summary": {"findings": [{"id": "f1", "judgment": "The technology is mature", "evidenceIds": ["s1"]}]},
        }

    @staticmethod
    def _review(**fields):
        return {"findingId": "f1", "verdict": "supported", "reason": "The reported test is only a prototype.",
                "evidence": [{"itemId": "s1", "quote": "A prototype test was reported."}], **fields}

    def test_model_support_does_not_promote_confidence_or_verify_independence(self):
        result = critique_assessment(self.assessment, Mock(return_value={"reviews": [self._review()]}))
        self.assertEqual(result["status"], "needs-evidence")
        self.assertEqual(result["checks"][0]["status"], "needs-evidence")
        self.assertFalse(result["confidenceFactors"]["independenceVerified"])
        self.assertFalse(result["confidenceFactors"]["humanConfirmed"])
        self.assertIn("A prototype test", result["checks"][0]["detail"])
        self.assertEqual(result["checks"][0]["evidenceIds"], ["s1"])
        ref = result["checks"][0]["evidenceRefs"][0]
        self.assertEqual(self.assessment["sourceSnapshot"][0]["body"][ref["startChar"]:ref["endChar"]], ref["quote"])
        self.assertEqual(ref["sourceUri"], "https://example.test/a")

    def test_forged_citations_and_unknown_findings_are_rejected(self):
        for fields in [
            {"findingId": "invented"},
            {"evidence": [{"itemId": "absent", "quote": "A prototype test was reported."}]},
            {"evidence": [{"itemId": "s1", "quote": "The test proved full maturity."}]},
            {"evidence": []},
        ]:
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                critique_assessment(self.assessment, Mock(return_value={"reviews": [self._review(**fields)]}))

    def test_only_sent_text_can_be_cited_and_sources_are_bounded(self):
        self.assessment["sourceSnapshot"][0]["body"] = "x" * 5000 + "unseen quote"
        self.assessment["sourceSnapshot"] += [{"itemId": f"s{i}", "body": "body"} for i in range(2, 32)]
        requester = Mock(return_value={"reviews": [self._review(evidence=[{"itemId": "s1", "quote": "unseen quote"}])]})
        with self.assertRaises(ValueError):
            critique_assessment(self.assessment, requester)
        prompts = [call.args[0][1]["content"] for call in requester.call_args_list]
        self.assertNotIn("unseen quote", "\n".join(prompts))
        self.assertTrue(any('"itemId": "s31"' in prompt for prompt in prompts))


if __name__ == "__main__":
    unittest.main()
