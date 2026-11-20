"""Tests for the Firecrawl/DeepSeek evidence-chain adapter."""

from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from intelligence_store import IntelligenceStore
from signal_ingest import SignalIngestError, adapt_enrichment, persist_signal_records


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class SignalIngestTests(unittest.TestCase):
    def payload(self):
        body = "MQ-1C endurance is 24 hours."
        digest = _hash(body)
        return {
            "runId": "uav-capability-test",
            "createdAt": "2026-10-17T00:00:00Z",
            "sources": [{
                "sourceId": "source-test-1", "sourceUri": "https://army.example/mq1c",
                "publisherRole": "military-official", "contentHash": digest,
                "snapshotPath": ".firecrawl/test.json", "snapshotPosition": 1,
            }],
            "candidates": [{
                "candidateId": "attr-candidate-test-1", "entityId": "equipment-test",
                "entityName": "MQ-1C", "attributeKey": "endurance", "proposedValue": "24 hours",
                "unit": "h", "qualifier": "exact", "conditions": "standard test conditions",
                "variant": "Block 1", "configuration": "EO/IR", "extractionConfidence": 0.8,
                "status": "accepted",  # upstream status is intentionally ignored
                "evidence": [{
                    "sourceUri": "https://army.example/mq1c", "sourceVersion": "snapshot-1",
                    "contentHash": digest, "quote": body, "lineStart": 3, "lineEnd": 3,
                    "snapshotPath": ".firecrawl/test.json", "snapshotPosition": 1,
                    "snapshotMatch": True, "evidenceId": "evidence-test-1",
                }],
            }],
        }

    def test_adapter_preserves_quote_snapshot_and_review_boundary(self):
        adapted = adapt_enrichment(self.payload(), registry={"groups": [{"label": "飞行与任务持续", "keys": ["endurance"]}]})
        self.assertEqual(len(adapted["claims"]), 1)
        claim = adapted["claims"][0]
        self.assertEqual(claim["reviewStatus"], "under-review")
        self.assertFalse(claim["truthVerified"])
        self.assertEqual(claim["variant"], "Block 1")
        bundle = adapted["evidenceBundles"][0]
        self.assertEqual(bundle["validity"]["sourceSnapshots"][0]["snapshotPath"], ".firecrawl/test.json")
        ref = adapted["observations"][0]["sourceRefs"][0]
        self.assertEqual(ref["quote"], "MQ-1C endurance is 24 hours.")
        self.assertEqual(ref["contentHash"], _hash(ref["quote"]))

    def test_persist_writes_legacy_evidence_and_signal_tables(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "signal.sqlite"
            store = IntelligenceStore(db)
            stamp = "2026-10-17T00:00:00Z"
            with store._connect() as con:
                con.execute("INSERT INTO ontology_entity(entity_id,canonical_iri,canonical_key,entity_type,preferred_name,current_revision,review_status,source_snapshot_ids,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)", ("equipment-test", "https://local/equipment-test", "equipment-test", "EquipmentModel", "MQ-1C", 0, "pending", "[]", stamp, stamp))
            adapted = adapt_enrichment(self.payload())
            summary = persist_signal_records(store, adapted)
            self.assertEqual(summary["claims"], 1)
            with store._connect() as con:
                self.assertEqual(con.execute("SELECT COUNT(*) FROM ontology_signal_observation").fetchone()[0], 1)
                row = con.execute("SELECT review_status,truth_verified FROM ontology_capability_claim").fetchone()
                self.assertEqual(tuple(row), ("under-review", 0))
                self.assertEqual(con.execute("SELECT COUNT(*) FROM ontology_evidence_bundle").fetchone()[0], 1)
                self.assertEqual(con.execute("SELECT COUNT(*) FROM ontology_evidence_link WHERE evidence_id='evidence-test-1'").fetchone()[0], 1)

    def test_missing_quote_is_not_admitted_to_signal_chain(self):
        payload = self.payload()
        payload["candidates"][0]["evidence"][0]["quote"] = ""
        with self.assertRaises(SignalIngestError):
            adapt_enrichment(payload)


if __name__ == "__main__":
    unittest.main()
