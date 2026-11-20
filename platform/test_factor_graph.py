import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

try:
    from factor_graph import FactorGraphStore, build_manufacturing_graph
    from factor_graph_contract import validate_document, with_content_hash
    from intelligence_store import IntelligenceStore
except ModuleNotFoundError:  # pragma: no cover
    from .factor_graph import FactorGraphStore, build_manufacturing_graph
    from .factor_graph_contract import validate_document, with_content_hash
    from .intelligence_store import IntelligenceStore


def candidate(candidate_id, amount, *, status="under-review", configuration="Block 1", snapshot=None):
    snapshot = snapshot or f"snapshot-{candidate_id}"
    return {
        "candidate_id": candidate_id,
        "candidate_type": "cost",
        "status": status,
        "payload": {
            "costType": "flyaway",
            "amount": amount,
            "currency": "USD",
            "configuration": configuration,
            "sourceSnapshotIds": [snapshot],
        },
        "sourceSnapshotIds": [snapshot],
        "evidence": {
            "snapshotId": snapshot,
            "sourceUri": f"https://example.test/{candidate_id}",
            "sourceSubject": "Test UAV",
            "quote": f"Test UAV flyaway price is {amount} USD.",
        },
    }


class FactorGraphTests(unittest.TestCase):
    def setUp(self):
        self.entity = {"entityId": "equipment-test", "preferredName": "Test UAV"}

    def _store(self, tmp):
        store = IntelligenceStore(Path(tmp) / "factor.sqlite")
        with store._connect() as con:
            con.execute(
                "INSERT INTO ontology_entity(entity_id,canonical_iri,canonical_key,entity_type,preferred_name,current_revision,review_status,source_snapshot_ids,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                ("equipment-test", "https://local.test/equipment-test", "equipment-test", "EquipmentModel", "Test UAV", 0, "under-review", "[]", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
            )
        return store

    def test_contract_hashes_nested_rows_and_rejects_unknown_references(self):
        graph = build_manufacturing_graph(self.entity, [candidate("c1", 2.5)])
        self.assertEqual(validate_document(graph), [])
        tampered = json.loads(json.dumps(graph))
        tampered["factors"][0]["variableIds"] = ["missing-variable"]
        self.assertTrue(any("unknown variable" in error for error in validate_document(tampered)))

    def test_conflicting_values_are_preserved_and_needs_human_review(self):
        graph = build_manufacturing_graph(self.entity, [candidate("c1", 2.5), candidate("c2", 3.1)])
        self.assertEqual(graph["inference"]["status"], "conflict")
        self.assertEqual(len(graph["observations"]), 2)
        self.assertTrue(any(factor["factorType"] == "source-independence" for factor in graph["factors"]))
        self.assertTrue(any("different candidate values" in item for item in graph["inference"]["conflicts"]))

    def test_store_is_idempotent_and_append_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            graph_store = FactorGraphStore(store)
            graph = graph_store.build_from_candidates(self.entity, [candidate("c1", 2.5)])
            self.assertEqual(graph_store.create(graph)["contentHash"], graph["contentHash"])
            self.assertEqual(len(graph_store.list_runs(self.entity["entityId"])), 1)
            with self.assertRaises(sqlite3.IntegrityError):
                with store._connect() as con:
                    con.execute("UPDATE ontology_factor_graph_run SET status='conflict' WHERE graph_run_id=?", (graph["graphRunId"],))
            self.assertEqual(graph_store.get(graph["graphRunId"])["contentHash"], graph["contentHash"])

    def test_existing_manufacturing_read_projection_exposes_only_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            graph_store = FactorGraphStore(store)
            graph_store.build_from_candidates(self.entity, [candidate("c1", 2.5)])
            result = graph_store.list_runs(self.entity["entityId"])
            self.assertEqual(result[0]["summary"]["candidateCount"], 1)
            self.assertNotIn("quote", result[0])


if __name__ == "__main__":
    unittest.main()
