"""Active workers retain an OS lease across recovery in another instance."""

from pathlib import Path
import subprocess
import sys
import threading
import unittest
from unittest.mock import patch

try:
    from extraction_jobs import ExtractionJobManager
    import test_extraction_jobs as fixtures
except ModuleNotFoundError:  # pragma: no cover
    from .extraction_jobs import ExtractionJobManager
    from . import test_extraction_jobs as fixtures


class ExtractionJobLeaseTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ExtractionJobTests(methodName="runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.manager, self.agent, self.store = self.fixture.manager, self.fixture.agent, self.fixture.store

    def test_second_manager_cannot_recover_or_run_a_worker_in_a_provider_call(self):
        job = self.fixture.submit()
        entered, release = threading.Event(), threading.Event()
        failures = []
        def in_provider(object_id):
            if object_id == "camera-0":
                entered.set()
                if not release.wait(5):
                    raise AssertionError("fixture provider was not released")
        self.fixture.json_provider.before_response = in_provider
        def run_worker():
            try:
                self.manager.run(job["jobId"])
            except BaseException as exc:
                failures.append(exc)
        thread = threading.Thread(target=run_worker, daemon=True)
        thread.start()
        try:
            self.assertTrue(entered.wait(5))
            other = ExtractionJobManager(self.agent)
            self.assertEqual(other.recover(start=False), [])
            other.run(job["jobId"])
            self.assertEqual(other.get(job["jobId"])["status"], "running")
            self.assertEqual(self.fixture.json_provider.calls, ["camera-0"])
        finally:
            release.set()
            thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(failures, [])
        self.assertEqual(self.fixture.json_provider.calls, ["camera-0", "camera-1", "camera-2"])
        self.assertEqual(self.manager.get(job["jobId"])["status"], "completed")

    def test_other_process_kernel_lease_prevents_recovery_and_releases_on_exit(self):
        job = self.fixture.submit()
        script = "\n".join([
            "import sys",
            "from extraction_jobs import job_lease",
            "with job_lease(sys.argv[1], sys.argv[2]) as owned:",
            "    print('owned' if owned else 'busy', flush=True)",
            "    sys.stdin.read(1)",
        ])
        process = subprocess.Popen([sys.executable, "-c", script, str(self.store.path), job["jobId"]],
            cwd=Path(__file__).parent, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            self.assertEqual(process.stdout.readline().strip(), "owned")
            with self.store._connect() as con:
                con.execute("UPDATE research_extraction_job SET status='running' WHERE job_id=?", (job["jobId"],))
            self.assertEqual(self.manager.recover(start=False), [])
            self.manager.run(job["jobId"])
            self.assertEqual(self.manager.get(job["jobId"])["status"], "running")
            self.assertEqual(self.fixture.json_provider.calls, [])
            # Process termination releases the kernel lock even without a
            # normal Python context-manager exit, as during a service crash.
            process.terminate()
            process.communicate(timeout=5)
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=5)
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream and not stream.closed:
                    stream.close()
        self.assertEqual(self.manager.recover(start=False), [job["jobId"]])
        self.assertEqual(self.manager.get(job["jobId"])["status"], "interrupted")
        self.manager.run(job["jobId"])
        self.assertEqual(self.manager.get(job["jobId"])["status"], "completed")

    def test_lease_io_failure_propagates_without_marking_unowned_job_failed(self):
        job = self.fixture.submit()
        module_name = self.manager.__class__.__module__
        with patch(module_name + ".job_lease", side_effect=PermissionError("fixture-lease-io-failure")):
            with self.assertRaises(PermissionError):
                self.manager.run(job["jobId"])
            with self.assertRaises(PermissionError):
                self.manager.recover(start=False)
        self.assertEqual(self.manager.get(job["jobId"])["status"], "queued")
        self.assertEqual(self.fixture.json_provider.calls, [])


if __name__ == "__main__":
    unittest.main()
