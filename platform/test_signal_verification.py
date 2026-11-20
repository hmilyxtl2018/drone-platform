"""Trust-boundary tests for public UAV signal evidence assessments."""

from __future__ import annotations

import hashlib
import json
import unittest

try:
    from signal_verification import (
        assess_observation,
        assess_source_independence,
        build_signal_verification_prompt,
        validate_signal_observation,
    )
except ModuleNotFoundError:  # pragma: no cover
    from .signal_verification import (
        assess_observation,
        assess_source_independence,
        build_signal_verification_prompt,
        validate_signal_observation,
    )


def source(
    source_ref: str,
    source_type: str,
    *,
    body: str,
    domain: str,
    published: str | None = "2026-01-02T00:00:00Z",
    capture: str | None = None,
    model_match: str = "exact",
) -> dict:
    return {
        "sourceRef": source_ref,
        "sourceType": source_type,
        "sourceReliability": "primary" if source_type in {"official-publication", "evaluation-report", "test-observation", "telemetry", "public-imagery"} else "secondary",
        "publisherDomain": domain,
        "originalSourceUri": None,
        "sourceVersion": "v1",
        "contentHash": hashlib.sha256(body.encode()).hexdigest(),
        "quote": body,
        "publishedAt": published,
        "captureDate": capture,
        "modelMatch": model_match,
        "independenceStatus": "unknown",
    }


def observation(*, claim_type="capability", signal_type="comint", refs=None, model_match="exact", time_match="exact", observed_at="2026-01-02T00:00:00Z", verification=None):
    refs = refs or []
    return {
        "schemaVersion": "signal-verification.v1",
        "observationId": "obs-uav-001",
        "entityRef": {"entityId": "equipment-mq1c", "entityRevisionId": "revision-1", "label": "MQ-1C", "variant": "Block 1", "configuration": "EO/IR", "modelMatch": model_match},
        "claimType": claim_type,
        "signalType": signal_type,
        "collection": {"observedAt": observed_at, "observedAtEnd": None, "accessBasis": "public", "authorizationStatus": "public", "collector": "open-source-record", "platform": "public-report", "locationGranularity": "region"},
        "sourceRefs": refs,
        "features": [{"name": "link-state", "value": "observed", "unit": None, "method": "quoted public test record", "featureConfidence": "medium", "sourceRef": refs[0]["sourceRef"] if refs else None}],
        "verification": verification or {"evidenceLevel": "E0", "independenceStatus": "single-source", "modelMatch": model_match, "timeMatch": time_match, "claimState": "reported", "verificationStatus": "pending-review", "truthVerified": False, "deceptionFlags": [], "autoAdmitEligible": False, "humanReviewRequired": True, "reviewDecision": "pending", "reviewer": None, "reviewedAt": None, "notes": []},
    }


class SignalVerificationTests(unittest.TestCase):
    def test_shared_hash_and_quote_are_not_independent_sources(self):
        first = source("https://a.example/r", "defense-media", body="MQ-1C link test", domain="a.example")
        second = source("https://b.example/reprint", "defense-media", body="MQ-1C link test", domain="b.example")
        result = assess_source_independence([first, second])
        self.assertEqual(result["status"], "dependent")
        self.assertEqual(result["independentSourceCount"], 1)

    def test_distinct_primary_domains_are_potentially_independent(self):
        first = source("https://army.example/test", "evaluation-report", body="MQ-1C data link was observed", domain="army.example")
        second = source("https://audit.example/test", "test-observation", body="MQ-1C data link recovered", domain="audit.example")
        result = assess_source_independence([first, second])
        self.assertEqual(result["status"], "independent")
        self.assertEqual(result["independentSourceCount"], 2)

    def test_signal_claim_stays_human_gated_even_with_e4_evidence(self):
        refs = [
            source("https://army.example/test", "evaluation-report", body="MQ-1C data link was observed", domain="army.example"),
            source("https://audit.example/test", "test-observation", body="MQ-1C data link recovered", domain="audit.example"),
        ]
        draft = observation(refs=refs)
        assessed = assess_observation(draft)
        self.assertEqual(assessed["evidenceLevel"], "E4")
        self.assertFalse(assessed["autoAdmitEligible"])
        self.assertTrue(assessed["humanReviewRequired"])
        self.assertFalse(assessed["truthVerified"])

    def test_low_risk_identity_can_be_auto_admit_eligible_but_not_published(self):
        refs = [
            source("https://army.example/uav", "official-publication", body="MQ-1C designation", domain="army.example"),
            source("https://imagery.example/uav", "public-imagery", body="MQ-1C captured at airfield", domain="imagery.example", published=None, capture="2026-01-02T00:00:00Z"),
        ]
        draft = observation(claim_type="designation", signal_type="public-imagery", refs=refs)
        assessed = assess_observation(draft)
        self.assertTrue(assessed["autoAdmitEligible"])
        self.assertFalse(assessed["publicationAllowed"])
        self.assertEqual(assessed["recommendation"], "auto-admit-low-risk")

    def test_reprint_manufacturer_and_plan_flags_lower_recommendation(self):
        body = "The manufacturer plans to demonstrate the MQ-1C capability."
        refs = [
            source("https://maker.example/release", "manufacturer-disclosure", body=body, domain="maker.example"),
            source("https://news.example/reprint", "defense-media", body=body, domain="news.example"),
        ]
        draft = observation(refs=refs)
        assessed = assess_observation(draft)
        self.assertIn("reprint", assessed["deceptionFlags"])
        self.assertIn("manufacturer-claim", assessed["deceptionFlags"])
        self.assertIn("planned-vs-performed", assessed["deceptionFlags"])
        self.assertIn("demo-not-combat", assessed["deceptionFlags"])
        self.assertEqual(assessed["recommendation"], "request-more-evidence")

    def test_model_cannot_claim_e5_or_truth_verified_without_review(self):
        ref = source("https://maker.example/spec", "manufacturer-disclosure", body="MQ-1C range 1000 km", domain="maker.example")
        draft = observation(refs=[ref], verification={"evidenceLevel": "E5", "independenceStatus": "single-source", "modelMatch": "exact", "timeMatch": "exact", "claimState": "observed", "verificationStatus": "verified", "truthVerified": True, "deceptionFlags": ["manufacturer-claim"], "autoAdmitEligible": True, "humanReviewRequired": False, "reviewDecision": "pending", "reviewer": None, "reviewedAt": None, "notes": []})
        errors = validate_signal_observation(draft)
        self.assertTrue(any("exceeds deterministic level" in error for error in errors))
        self.assertTrue(any("truthVerified requires" in error for error in errors))

    def test_prompt_prohibits_collection_and_publication(self):
        prompt = build_signal_verification_prompt({"entityId": "equipment-mq1c"}, [])
        self.assertIn("不采集", prompt["system"])
        self.assertIn("publicationAllowed", prompt["user"])
        self.assertIn("truthVerified", prompt["user"])


if __name__ == "__main__":
    unittest.main()
