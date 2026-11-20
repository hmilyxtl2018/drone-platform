import hashlib
import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from signal_contracts import SignalContractError, content_hash, validate_record, with_content_hash


def stamp():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class SignalContractTests(unittest.TestCase):
    def source(self):
        return {
            "evidenceId": "ev-1",
            "sourceUri": "https://example.test/report",
            "sourceVersion": "2026-10-01",
            "contentHash": "a" * 64,
            "quote": "documented observation",
            "independenceKey": "official-report-1",
        }

    def test_signal_observation_hash_and_validation(self):
        record = with_content_hash({
            "recordType": "SignalObservation",
            "observationId": "obs-1",
            "entityId": "equipment-1",
            "observationType": "ELINT",
            "collectedAt": stamp(),
            "collectionLocation": {"country": "public"},
            "conditions": {"mode": "test"},
            "features": {"band": "L"},
            "sourceRefs": [self.source()],
            "claimState": "observed",
            "confidence": 0.7,
            "verificationStatus": "under-review",
            "evidenceLevel": "E2",
        })
        self.assertEqual(record["contentHash"], content_hash(record))
        self.assertEqual(validate_record(record)["recordType"], "SignalObservation")

    def test_accepted_claim_requires_real_evidence(self):
        record = with_content_hash({
            "recordType": "CapabilityClaim",
            "claimId": "claim-1",
            "entityId": "equipment-1",
            "capabilityDomain": "通信与协同",
            "metric": "抗干扰链路",
            "rawValue": "具备跳频数据链",
            "valueType": "text",
            "normalizedValue": "跳频数据链",
            "conditions": {"environment": "未公开"},
            "sourceObservationIds": ["obs-1"],
            "evidenceBundleIds": ["bundle-1"],
            "claimState": "reported",
            "verificationStatus": "verified",
            "evidenceLevel": "E2",
            "confidence": 0.8,
            "reviewStatus": "accepted",
            "truthVerified": True,
        })
        self.assertTrue(validate_record(record))

    def test_low_evidence_cannot_be_accepted(self):
        payload = {
            "recordType": "CapabilityClaim",
            "claimId": "claim-2",
            "entityId": "equipment-1",
            "capabilityDomain": "通信与协同",
            "metric": "抗干扰链路",
            "rawValue": "未知",
            "valueType": "text",
            "conditions": {},
            "sourceObservationIds": [],
            "evidenceBundleIds": [],
            "claimState": "reported",
            "verificationStatus": "verified",
            "evidenceLevel": "E1",
            "reviewStatus": "accepted",
            "truthVerified": True,
        }
        with self.assertRaises(SignalContractError):
            with_content_hash(payload)

    def test_verified_bundle_needs_independence_and_reference(self):
        valid = with_content_hash({
            "recordType": "EvidenceBundle",
            "evidenceBundleId": "bundle-1",
            "entityId": "equipment-1",
            "claimIds": ["claim-1"],
            "observationIds": ["obs-1"],
            "evidenceIds": ["ev-1"],
            "sourceIndependence": {"independentSourceCount": 2},
            "validity": {"modelMatch": "MQ-1C"},
            "counterEvidenceIds": [],
            "evidenceLevel": "E3",
            "verificationStatus": "verified",
        })
        self.assertEqual(valid["recordType"], "EvidenceBundle")

        invalid = dict(valid)
        invalid["sourceIndependence"] = {}
        invalid["contentHash"] = content_hash(invalid)
        with self.assertRaises(SignalContractError):
            validate_record(invalid)

    def test_completed_verification_requires_checks(self):
        payload = {
            "recordType": "VerificationRun",
            "verificationRunId": "verify-1",
            "entityId": "equipment-1",
            "runType": "claim-verification",
            "status": "completed",
            "methodVersion": "verify.v1",
            "inputRefs": {"claimIds": ["claim-1"]},
            "result": {"verified": 1},
            "checks": [{"code": "quote-match", "status": "passed"}],
            "evidenceLevel": "E3",
            "confidence": 0.9,
            "createdAt": stamp(),
        }
        self.assertEqual(with_content_hash(payload)["status"], "completed")


if __name__ == "__main__":
    unittest.main()
