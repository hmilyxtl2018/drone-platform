"""Regression tests for the ontology tree to entity dossier read path."""
from contextlib import ExitStack
import importlib
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.request import Request, urlopen

from .intelligence_agent import IntelligenceAgent, DeterministicClaimProvider
from .intelligence_store import IntelligenceStore
from .ontology_workbook_import import import_workbook
from .ontology_agent import OntologyAgent
from .equipment_verification import EquipmentVerificationQueue
from .test_ontology_agent import fixture_workbook
from .test_capability_profile import Search, Trend


class OntologyTreeDossierAPITests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        workbook = root / "test.xlsx"
        database = root / "tree.sqlite"
        fixture_workbook(workbook)
        import_workbook(workbook, database)
        self.store = IntelligenceStore(database)
        agent = IntelligenceAgent(self.store, provider=DeterministicClaimProvider(), trend_provider=Trend())
        self.object_agent = OntologyAgent(self.store, trend_provider=Trend())
        self.object_agent.queue = EquipmentVerificationQueue(self.store, Search())
        with patch("demo.provider_secrets.load_provider_environment"), patch("demo.intelligence_agent.IntelligenceAgent", return_value=agent), patch("demo.runtime_store.RuntimeStore", return_value=object()), patch("demo.osint_store.OSINTTaskStore", return_value=object()):
            self.serve = importlib.import_module("demo.serve")
        self.stack.enter_context(patch.object(self.serve, "INTELLIGENCE_AGENT", agent))
        self.stack.enter_context(patch.object(self.serve, "ontology_agent", return_value=self.object_agent))
        self.stack.enter_context(patch.object(self.serve.Handler, "log_message", lambda *args: None))
        self.server = self.serve.ThreadingHTTPServer(("127.0.0.1", 0), self.serve.Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.entity_id = next(item["entityId"] for item in self.object_agent._entities() if item["preferredName"] == "Test UAV")

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(3)
        self.stack.close()

    def get(self, path):
        with urlopen(self.base + path, timeout=10) as response:
            return response.status, json.load(response)

    def test_tree_preserves_taxonomy_and_dossier_leaf(self):
        status, payload = self.get("/api/ontology/tree")
        self.assertEqual(status, 200)
        self.assertGreaterEqual(payload["entityCount"], 1)
        self.assertTrue(payload["tree"])
        leaves = []
        for layer in payload["tree"]:
            for cls in layer["classes"]:
                leaves.extend(cls.get("entities", []))
                for subclass in cls.get("subclasses", []):
                    leaves.extend(subclass.get("entities", []))
        leaf = next(item for item in leaves if item["entityId"] == self.entity_id)
        self.assertEqual(leaf["entityId"], self.entity_id)
        status, detail = self.get(f"/api/ontology/entities/{self.entity_id}")
        self.assertEqual(status, 200)
        self.assertEqual(detail["entity"]["entityId"], self.entity_id)
        self.assertTrue(detail["entity"]["revisions"])
        self.assertIn("attributes", detail["entity"]["revisions"][0])

    def test_dossier_relations_is_source_and_direction_aware(self):
        status, payload = self.get(f"/api/ontology/entities/{self.entity_id}/relations")
        self.assertEqual(status, 200)
        self.assertEqual(payload["entityId"], self.entity_id)
        self.assertIsInstance(payload["relations"], list)
        for relation in payload["relations"]:
            self.assertIn(relation["direction"], {"incoming", "outgoing"})
            self.assertIsInstance(relation["evidenceIds"], list)
            self.assertIn("reviewStatus", relation)


if __name__ == "__main__":
    unittest.main()
