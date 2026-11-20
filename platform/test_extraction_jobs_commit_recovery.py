"""Recovery across the graph-commit/job-status boundary uses durable results."""

from copy import deepcopy
import json
import unittest
from unittest.mock import patch

try:
    from extraction_jobs import ExtractionJobManager, encoded
    import test_extraction_jobs as fixtures
except ModuleNotFoundError:  # pragma: no cover
    from .extraction_jobs import ExtractionJobManager, encoded
    from . import test_extraction_jobs as fixtures

ProcessInterrupted = fixtures.ProcessInterrupted


class ExtractionCommitRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ExtractionJobTests(methodName="runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.store, self.agent, self.manager = self.fixture.store, self.fixture.agent, self.fixture.manager
        self.provider, self.aid = self.fixture.json_provider, self.fixture.aid

    def kill_after_graph_save(self):
        job = self.fixture.submit()
        save = self.store.save_graph
        def saved_then_interrupted(*args, **kwargs):
            save(*args, **kwargs)
            raise ProcessInterrupted()
        with patch.object(self.store, "save_graph", side_effect=saved_then_interrupted):
            with self.assertRaises(ProcessInterrupted):
                self.manager.run(job["jobId"])
        return job

    def calls(self):
        return (len(self.provider.identity_calls), len(self.provider.calls), len(self.provider.link_calls))

    def restart(self, job):
        restarted = ExtractionJobManager(self.agent)
        self.assertIn(job["jobId"], restarted.recover(start=False))
        restarted.run(job["jobId"])
        return restarted.get(job["jobId"])

    def test_kill_after_save_adopts_exact_committed_graph_without_calls_or_extra_revision(self):
        job = self.kill_after_graph_save()
        committed = self.store.get_graph(self.aid)
        calls = self.calls()
        count = self.fixture.graph_count()
        checkpoint = self.fixture.checkpoint_data(job["jobId"])
        self.assertIn("finalGraph", checkpoint)
        self.assertEqual(self.manager.get(job["jobId"])["status"], "running")
        result = self.restart(job)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["resultGraphRevisionId"], committed["graphRevisionId"])
        self.assertEqual(self.calls(), calls)
        self.assertEqual(self.fixture.graph_count(), count)
        self.assertEqual(self.store.get_graph(self.aid), committed)

    def test_changed_current_graph_is_not_adopted_as_the_interrupted_job_result(self):
        job = self.kill_after_graph_save()
        current = self.store.get_graph(self.aid)
        changed = {"nodes": deepcopy(current["nodes"]), "edges": deepcopy(current["edges"])}
        changed["nodes"][0]["payload"]["externalRevisionMarker"] = "another-writer"
        different = self.store.save_graph(self.aid, changed)
        calls = self.calls()
        count = self.fixture.graph_count()
        result = self.restart(job)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["errorCode"], "graph-or-report-version-changed")
        self.assertEqual(self.store.get_graph(self.aid), different)
        self.assertEqual(self.calls(), calls)
        self.assertEqual(self.fixture.graph_count(), count)

    def test_kill_before_save_uses_final_checkpoint_and_commits_exactly_once(self):
        job = self.fixture.submit()
        with patch.object(self.store, "save_graph", side_effect=ProcessInterrupted()):
            with self.assertRaises(ProcessInterrupted):
                self.manager.run(job["jobId"])
        checkpoint = self.fixture.checkpoint_data(job["jobId"])
        self.assertIn("finalGraph", checkpoint)
        calls, count = self.calls(), self.fixture.graph_count()
        result = self.restart(job)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(self.calls(), calls)
        self.assertEqual(self.fixture.graph_count(), count + 1)
        committed = self.store.get_graph(self.aid)
        self.assertEqual(committed["nodes"], checkpoint["finalGraph"]["nodes"])
        self.assertEqual(committed["edges"], checkpoint["finalGraph"]["edges"])
        self.assertEqual(result["resultGraphRevisionId"], committed["graphRevisionId"])

    def test_mutated_job_checkpoint_cannot_replace_immutable_checkpoint_history(self):
        job = self.fixture.submit()
        with patch.object(self.store, "save_graph", side_effect=ProcessInterrupted()):
            with self.assertRaises(ProcessInterrupted):
                self.manager.run(job["jobId"])
        calls, count = self.calls(), self.fixture.graph_count()
        snapshot = self.store.get_graph(self.aid)
        changed = self.fixture.checkpoint_data(job["jobId"])
        changed["finalGraph"]["nodes"][0]["payload"]["forgedCheckpoint"] = True
        with self.store._connect() as con:
            con.execute("UPDATE research_extraction_job SET checkpoint=? WHERE job_id=?", (encoded(changed), job["jobId"]))
        result = self.restart(job)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(self.calls(), calls)
        self.assertEqual(self.fixture.graph_count(), count)
        self.assertEqual(self.store.get_graph(self.aid), snapshot)
        self.assertNotIn("forgedCheckpoint", json.dumps(snapshot))

    def test_erasing_checkpoint_does_not_bypass_history_check_or_repeat_model_calls(self):
        job = self.fixture.submit()
        with patch.object(self.store, "save_graph", side_effect=ProcessInterrupted()):
            with self.assertRaises(ProcessInterrupted):
                self.manager.run(job["jobId"])
        calls, count = self.calls(), self.fixture.graph_count()
        with self.store._connect() as con:
            con.execute("UPDATE research_extraction_job SET checkpoint='{}' WHERE job_id=?", (job["jobId"],))
        result = self.restart(job)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(self.calls(), calls)
        self.assertEqual(self.fixture.graph_count(), count)


if __name__ == "__main__":
    unittest.main()
