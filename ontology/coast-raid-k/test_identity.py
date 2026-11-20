"""Focused tests for the F04 identity contract.

Run directly with ``python test_identity.py``.  This does not invoke the
legacy validator or rebuild any generated artifact.
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from identity import IdentityError, build_index, canonical_iri, load_registry, resolve, source_entries


ROOT = Path(__file__).resolve().parent


class IdentityContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = load_registry()

    def test_source_iri_is_preserved(self):
        entry = {
            "type": "AgentInstance",
            "id": "agent.monitor.gate",
            "iri": "https://local.ontology/coast-raid-k/agent/1.0.0#agent-monitor-gate",
        }
        self.assertEqual(canonical_iri(entry, self.registry), entry["iri"])

    def test_class_fallback_has_no_class_namespace(self):
        iri = canonical_iri({"type": "Class", "id": "Sensor"}, self.registry)
        self.assertEqual(iri, "https://local.ontology/coast-raid-k#Sensor")
        self.assertNotIn("Class#", iri)

    def test_individual_fallback_separates_same_local_id_by_type(self):
        rule = canonical_iri({"type": "Rule", "id": "gate"}, self.registry)
        bind = canonical_iri({"type": "FunctionBind", "id": "gate"}, self.registry)
        self.assertNotEqual(rule, bind)
        self.assertIn("individual/Rule/gate", rule)
        self.assertIn("individual/FunctionBind/gate", bind)

    def test_legacy_collision_is_explicit(self):
        idx = build_index([
            {"type": "Rule", "id": "gate"},
            {"type": "FunctionBind", "id": "gate"},
        ], self.registry)
        result = resolve("https://local.ontology/coast-raid-k#gate", idx)
        self.assertEqual(result["status"], "ambiguous")
        self.assertEqual({r.type_id for r in result["candidates"]}, {"Rule", "FunctionBind"})
        self.assertEqual(resolve("https://local.ontology/coast-raid-k#gate", idx, type_id="Rule")["status"], "resolved")

    def test_duplicate_canonical_iri_fails(self):
        with self.assertRaises(IdentityError):
            build_index([
                {"type": "Rule", "id": "one", "iri": "urn:x"},
                {"type": "FunctionBind", "id": "two", "iri": "urn:x"},
            ], self.registry)

    def test_namespace_collision_preserves_full_iris(self):
        idx=build_index([
            {'type':'Class','id':'Term','iri':'urn:first:Term'},
            {'type':'Class','id':'Term','iri':'urn:second:Term'},
        ],self.registry)
        self.assertEqual(len(idx['byIri']),2)
        self.assertEqual(resolve('https://local.ontology/coast-raid-k#Term',idx)['status'],'ambiguous')

    def test_missing_source_iri_is_run_scoped(self):
        one=canonical_iri({'type':'Observation','id':'o1','runId':'run1'},self.registry)
        two=canonical_iri({'type':'Observation','id':'o1','runId':'run2'},self.registry)
        self.assertNotEqual(one,two)

    def test_source_iri_whitespace_is_rejected_not_silently_trimmed(self):
        with self.assertRaises(IdentityError):
            canonical_iri({'type':'AgentInstance','id':'a','iri':' https://example.test/a'},self.registry)

    def test_current_sources_have_expected_agent_iris(self):
        inventory = json.loads((ROOT / "inventory.json").read_text(encoding="utf-8"))
        instances = json.loads((ROOT / "agent.instances.json").read_text(encoding="utf-8"))
        rows = source_entries(inventory=inventory, agent_instances=instances)
        idx = build_index(rows, self.registry)
        expected = {x["iri"] for x in instances["instances"]}
        actual = {r.iri for r in idx["records"] if r.type_id == "AgentInstance"}
        self.assertEqual(actual, expected)


if __name__ == "__main__":
    unittest.main()
