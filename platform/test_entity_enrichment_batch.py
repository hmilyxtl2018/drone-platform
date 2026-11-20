"""Regression tests for independent entity-directed enrichment batches."""
from pathlib import Path
import tempfile
import unittest

from .capability_workflow import CapabilityWorkflow
from .equipment_verification import EquipmentVerificationQueue
from .intelligence_agent import DeterministicClaimProvider, IntelligenceAgent
from .intelligence_store import IntelligenceStore
from .ontology_agent import OntologyAgent
from .ontology_workbook_import import import_workbook
from .test_capability_profile import Search, Trend
from .test_ontology_agent import fixture_workbook


class EntityEnrichmentBatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        workbook = root / "entities.xlsx"
        fixture_workbook(workbook)
        import_workbook(workbook, root / "entities.sqlite")
        self.store = IntelligenceStore(root / "entities.sqlite")
        self.agent = OntologyAgent(self.store, trend_provider=Trend())
        self.agent.queue = EquipmentVerificationQueue(self.store, Search())

    def tearDown(self):
        self.temp.cleanup()

    def test_batch_keeps_independent_entity_status_and_budget_fields(self):
        ids = [item["entityId"] for item in self.agent._entities()]
        workflow = CapabilityWorkflow(self.agent)
        batch = workflow.submit_batch(
            {"entityIds": ids, "capabilityCodes": ["communications"], "maxJobs": 1,
             "maxModelCalls": 1, "maxElapsedSeconds": 60, "maxFollowupRounds": 0},
            background=False,
        )
        self.assertEqual(batch["status"], "completed")
        self.assertEqual(len(batch["items"]), len(ids))
        self.assertTrue(all(item["runId"] for item in batch["items"]))
        self.assertTrue(all(item["maxModelCalls"] == 1 for item in batch["items"]))
        self.assertTrue(all(item["stopReason"] for item in batch["items"]))
        self.assertGreaterEqual(len(batch["events"]), len(ids))

    def test_plan_exposes_unknown_gaps_and_keeps_optional_lineage_context(self):
        entity = self.agent._entities()[0]
        jobs = self.agent.queue.plan(entity["entityId"])
        systems = next(job for job in jobs if job["targetType"] == "systems")
        self.assertIn("missingKeys", systems)
        self.assertTrue(systems["missingKeys"])
        self.assertTrue(any(key in systems["query"] for key in systems["missingKeys"]))


if __name__ == "__main__":
    unittest.main()
