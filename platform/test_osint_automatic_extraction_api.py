"""Automatic OSINT object/attribute chaining with disposable local providers.

No public provider, application database, credential file or live service is
touched. HTTP creation uses server-owned OSINT material and a local JSON model.
"""

from contextlib import ExitStack
import hashlib
import importlib
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

try:
    from capability_analysis import normalize_capabilities
    from extraction_jobs import ExtractionJobManager
    from intelligence_agent import DeterministicClaimProvider, DeterministicTrendProvider, IntelligenceAgent
    from intelligence_store import IntelligenceStore
    from osint_store import OSINTTaskStore
except ModuleNotFoundError:
    from .capability_analysis import normalize_capabilities
    from .extraction_jobs import ExtractionJobManager
    from .intelligence_agent import DeterministicClaimProvider, DeterministicTrendProvider, IntelligenceAgent
    from .intelligence_store import IntelligenceStore
    from .osint_store import OSINTTaskStore


class LocalModelTrendProvider(DeterministicTrendProvider):
    configured = True

    def __init__(self):
        self.calls = []
        self.claim_provider = self
        self.model = "isolated-json-fixture-model"

    def generate(self, **kwargs):
        draft = super().generate(**kwargs)
        draft.update(provider="local-model-trend-provider", model=self.model)
        return draft

    def _request_json(self, messages, **_options):
        data = json.loads(messages[1]["content"].rsplit("\n", 1)[1])
        if "object" in data:
            identity = data["object"]
            self.calls.append(("attribute", identity["objectId"]))
            for material in data["materials"]:
                for excerpt in material["excerpts"]:
                    quote = excerpt["text"]
                    if identity["label"] in quote:
                        return {"objectId": identity["objectId"], "attributes": [{
                            "name": "接口方式", "attributeKey": "interface-standard", "value": "an open interface",
                            "itemId": material["itemId"], "quote": quote,
                        }], "gaps": []}
            return {"objectId": identity["objectId"], "attributes": [], "gaps": []}
        if "findings" in data:
            self.calls.append(("trend-links", None))
            return {"links": [], "gaps": ["Fixture does not infer trend support from shared source identity."]}
        self.calls.append(("identity", None))
        nodes = []
        for material in data["materials"]:
            for excerpt in material["excerpts"]:
                quote = excerpt["text"]
                for label in ("Camera Alpha", "Camera Beta"):
                    if label in quote:
                        nodes.append({"id": "fixture-" + label.lower().replace(" ", "-"), "type": "EquipmentModel",
                                      "label": label, "itemId": material["itemId"], "quote": quote})
        return {"nodes": nodes, "edges": [], "gaps": []}


class OSINTAutomaticExtractionAPITests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.temp = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.store = IntelligenceStore(Path(self.temp) / "intelligence.sqlite")
        self.provider = LocalModelTrendProvider()
        self.agent = IntelligenceAgent(self.store, provider=DeterministicClaimProvider(), trend_provider=self.provider)
        self.osint = OSINTTaskStore(Path(self.temp) / "osint.sqlite", collector=object(), intelligence=self.agent)
        self.task_id = "local-public-camera-task"
        self.items = []
        for index, label in enumerate(("Camera Alpha", "Camera Beta")):
            body = f"{label} uses an open interface in a public laboratory inspection test."
            self.items.append({"id": f"source-{index}", "title": f"{label} public test", "sourceId": "local-fixture",
                               "sourceName": "Local public fixture", "sourceUri": f"https://example.test/camera-{index}",
                               "sourceVersion": "v1", "body": body, "contentHash": hashlib.sha256(body.encode()).hexdigest()})
        self.task = {"id": self.task_id, "status": "completed", "title": "Public camera interfaces",
                     "question": "What interfaces are documented by public inspection tests?", "items": self.items}
        self.stack.enter_context(patch.object(self.osint, "get_task", return_value=self.task))
        self.manager = ExtractionJobManager(self.agent, self.osint)
        self.start = self.stack.enter_context(patch.object(self.manager, "start"))
        prefix = "demo." if IntelligenceAgent.__module__.startswith("demo.") else ""
        secrets = importlib.import_module(prefix + "provider_secrets")
        agent_module = importlib.import_module(prefix + "intelligence_agent")
        runtime = importlib.import_module(prefix + "runtime_store")
        osint_module = importlib.import_module(prefix + "osint_store")
        jobs_module = importlib.import_module(prefix + "extraction_jobs")
        # Imports are cached for later test classes. Their baseline singletons
        # must not retain this temporary OSINT store/job manager after cleanup.
        # Every request below installs its own fixture through scoped patches;
        # neutral import-only coordinators also keep constructors from opening
        # application databases or encrypted provider configuration.
        with patch.object(secrets, "load_provider_environment"), patch.object(agent_module, "IntelligenceAgent", return_value=self.agent), patch.object(runtime, "RuntimeStore", return_value=object()), patch.object(osint_module, "OSINTTaskStore", return_value=object()), patch.object(jobs_module, "ExtractionJobManager", return_value=object()):
            self.serve = importlib.import_module(prefix + "serve")
        for name, value in (("INTELLIGENCE_AGENT", self.agent), ("OSINT_STORE", self.osint), ("EXTRACTION_JOBS", self.manager)):
            self.stack.enter_context(patch.object(self.serve, name, value))
        self.stack.enter_context(patch.object(self.serve.Handler, "log_message", lambda *_args: None))
        self.server = self.serve.ThreadingHTTPServer(("127.0.0.1", 0), self.serve.Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close_server)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.create_path = f"/api/osint/tasks/{self.task_id}/assessments"

    def close_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)

    def request(self, path, body=None):
        req = Request(self.base + path, data=json.dumps(body).encode() if body is not None else None,
                      headers={"Content-Type": "application/json"}, method="POST" if body is not None else "GET")
        try:
            with urlopen(req, timeout=5) as response:
                return response.status, json.load(response)
        except HTTPError as exc:
            with exc:
                return exc.code, json.load(exc)

    def test_post_automatically_queues_identities_and_attributes_using_saved_sources(self):
        status, response = self.request(self.create_path, {"horizonYears": 3})
        self.assertEqual(status, 201)
        self.assertTrue(response["ok"])
        assessment, job = response["assessment"], response["extractionJob"]
        self.assertEqual(job["request"], {"expectedRevision": 1, "extractEntities": True})
        self.assertEqual(job["assessmentId"], assessment["assessmentId"])
        self.assertEqual(job["status"], "queued")
        self.start.assert_called_once_with(job["jobId"])
        self.assertEqual(self.provider.calls, [])
        self.manager.run(job["jobId"])
        self.assertEqual(self.manager.get(job["jobId"])["status"], "completed")
        graph = self.store.get_graph(assessment["assessmentId"])
        objects = [node for node in graph["nodes"] if node["payload"].get("extractionMethod") == "llm"]
        self.assertEqual({node["label"] for node in objects}, {"Camera Alpha", "Camera Beta"})
        for node in objects:
            self.assertEqual(node["payload"]["attributeExtraction"]["status"], "completed")
            self.assertEqual(len(node["payload"]["attributes"]), 1)
            self.assertEqual(node["payload"]["attributes"][0]["value"], "an open interface")
            self.assertIn(node["label"], node["payload"]["attributes"][0]["evidenceRefs"][0]["quote"])
        self.assertEqual(sum(kind == "identity" for kind, _ in self.provider.calls), 1)
        self.assertEqual(sum(kind == "attribute" for kind, _ in self.provider.calls), 2)
        self.assertEqual(self.store.get_assessment(assessment["assessmentId"])["status"], "under-review")
        self.assertEqual(self.store.published_ontology()["admissionCount"], 0)

    def test_auto_mode_quantifies_before_queueing_extraction(self):
        self.task.update({
            "workflowMode": "auto",
            "automation": {"autoStartAssessment": True},
        })
        self.items[0]["body"] = "Camera Alpha uses an open interface in 1 public laboratory inspection test."

        def quantify(materials):
            source = materials[0]
            quote = source["body"]
            return normalize_capabilities({"metrics": [{
                "equipment": "Camera Alpha", "dimension": "接口能力", "metric": "公开接口测试记录",
                "value": 1, "unit": "test", "qualifier": "exact", "basis": "reported-observation",
                "conditions": "public fixture", "assessment": "仅说明来源记录，不能外推实战效果。",
                "itemId": source["itemId"], "quote": quote,
            }]}, materials)

        self.provider.quantify = quantify
        status, response = self.request(self.create_path, {})
        self.assertEqual(status, 201)
        self.assertTrue(response["ok"])
        original = response["baseAssessment"]
        quantified = response["assessment"]
        self.assertNotEqual(quantified["assessmentId"], original["assessmentId"])
        self.assertEqual(quantified["summary"]["capabilityAssessment"]["statistics"]["metricCount"], 1)
        self.assertEqual(response["quantificationAssessment"]["assessmentId"], quantified["assessmentId"])
        self.assertEqual(response["extractionJob"]["assessmentId"], quantified["assessmentId"])
        self.assertEqual(response["extractionJob"]["request"], {"expectedRevision": 1, "extractEntities": True})

    def test_auto_mode_reuses_quantification_embedded_in_trend_draft(self):
        self.task.update({
            "workflowMode": "auto",
            "automation": {"autoStartAssessment": True},
        })
        original_generate = self.provider.generate

        def embedded_generate(**kwargs):
            draft = original_generate(**kwargs)
            capability = draft["summary"]["capabilityAssessment"]
            capability.update({"provider": "deepseek-trend-provider", "model": self.provider.model})
            return draft

        with patch.object(self.provider, "generate", side_effect=embedded_generate), \
             patch.object(self.provider, "quantify", side_effect=AssertionError("quantification must not repeat")):
            status, response = self.request(self.create_path, {})
        self.assertEqual(status, 201)
        self.assertTrue(response.get("quantificationSkipped"))
        self.assertEqual(response["assessment"]["assessmentId"], response["quantificationAssessment"]["assessmentId"])
        self.assertNotIn("quantificationError", response)
        self.assertEqual(response["extractionJob"]["assessmentId"], response["assessment"]["assessmentId"])

    def test_duplicate_report_reuses_completed_job_without_provider_or_worker_repeat(self):
        _, response = self.request(self.create_path, {})
        job = response["extractionJob"]
        self.manager.run(job["jobId"])
        calls = list(self.provider.calls)
        status, duplicate = self.request(self.create_path, {})
        self.assertEqual(status, 200)
        self.assertTrue(duplicate["duplicate"])
        self.assertEqual(duplicate["extractionJob"]["jobId"], job["jobId"])
        self.assertEqual(len(self.manager.list(response["assessment"]["assessmentId"])), 1)
        self.assertEqual(self.provider.calls, calls)
        self.start.assert_called_once()

    def test_explicit_manual_rerun_can_create_a_new_job_after_automatic_completion(self):
        _, response = self.request(self.create_path, {})
        old = response["extractionJob"]
        self.manager.run(old["jobId"])
        status, retry = self.request(f"/api/intelligence/assessments/{old['assessmentId']}/extraction-jobs",
                                     {"expectedRevision": 1, "extractEntities": True})
        self.assertEqual(status, 202)
        self.assertNotEqual(retry["job"]["jobId"], old["jobId"])
        self.assertEqual(retry["job"]["status"], "queued")

    def test_duplicate_after_graph_replacement_never_automatically_spends_again(self):
        _, response = self.request(self.create_path, {})
        job = response["extractionJob"]
        self.manager.run(job["jobId"])
        aid = job["assessmentId"]
        graph = self.store.get_graph(aid)
        graph["nodes"][0]["payload"]["localFixtureRevision"] = "new-explicit-graph"
        current = self.store.save_graph(aid, graph)
        self.assertNotEqual(current["graphRevisionId"], self.manager.get(job["jobId"])["resultGraphRevisionId"])
        calls = list(self.provider.calls)
        status, duplicate = self.request(self.create_path, {})
        self.assertEqual(status, 200)
        self.assertIn("图谱版本已变更", duplicate["extractionError"])
        self.assertEqual(len(self.manager.list(aid)), 1)
        self.assertEqual(self.provider.calls, calls)
        self.start.assert_called_once()

    def test_duplicate_after_report_review_never_creates_job_for_changed_revision(self):
        _, response = self.request(self.create_path, {})
        job = response["extractionJob"]
        self.manager.run(job["jobId"])
        aid = job["assessmentId"]
        reviewed = self.store.review_assessment(aid, {"action": "accept", "reviewer": "Local Test Analyst",
            "reason": "Disposable fixture review; never public knowledge", "expectedRevision": 1})
        self.assertEqual(reviewed["revision"], 2)
        status, duplicate = self.request(self.create_path, {})
        self.assertEqual(status, 200)
        self.assertEqual(duplicate["assessment"]["revision"], 2)
        self.assertIn("版本已变更", duplicate["extractionError"])
        self.assertEqual(len(self.manager.list(aid)), 1)
        self.start.assert_called_once()

    def test_duplicate_source_hash_mismatch_returns_error_without_new_job(self):
        _, response = self.request(self.create_path, {})
        aid = response["assessment"]["assessmentId"]
        with patch.object(self.store, "_snapshot_hash", return_value="unexpected-source-hash"):
            status, duplicate = self.request(self.create_path, {})
        self.assertEqual(status, 200)
        self.assertIn("版本已变更", duplicate["extractionError"])
        self.assertEqual(len(self.manager.list(aid)), 1)
        self.assertEqual(self.provider.calls, [])
        self.start.assert_called_once()

    def test_queue_failure_returns_saved_draft_and_safe_retry_error(self):
        with patch.object(self.manager, "submit", side_effect=OSError("fixture-sensitive-key-and-url")):
            status, response = self.request(self.create_path, {})
        self.assertEqual(status, 201)
        self.assertTrue(response["ok"])
        self.assertIn("报告已保存", response["extractionError"])
        self.assertNotIn("fixture-sensitive", json.dumps(response))
        self.assertNotIn("extractionJob", response)
        aid = response["assessment"]["assessmentId"]
        self.assertIsNotNone(self.store.get_assessment(aid))
        self.assertEqual(self.manager.list(aid), [])
        status, retry = self.request(f"/api/intelligence/assessments/{aid}/extraction-jobs", {"expectedRevision": 1, "extractEntities": True})
        self.assertEqual(status, 202)
        self.assertEqual(retry["job"]["status"], "queued")

    def test_worker_launch_failure_remains_failed_and_can_be_explicitly_retried(self):
        self.start.side_effect = RuntimeError("fixture-sensitive-thread-error")
        status, response = self.request(self.create_path, {})
        self.assertEqual(status, 201)
        self.assertIn("extractionError", response)
        aid = response["assessment"]["assessmentId"]
        old = self.manager.list(aid)[0]
        self.assertEqual(old["status"], "failed")
        self.assertEqual(old["errorCode"], "worker-start-failed")
        self.assertNotIn(old["jobId"], self.manager.workers)
        self.start.side_effect = None
        _, duplicate = self.request(self.create_path, {})
        self.assertEqual(duplicate["extractionJob"]["jobId"], old["jobId"])
        self.assertEqual(duplicate["extractionJob"]["status"], "failed")
        status, retry = self.request(f"/api/intelligence/assessments/{aid}/extraction-jobs", {"expectedRevision": 1, "extractEntities": True})
        self.assertEqual(status, 202)
        self.assertNotEqual(retry["job"]["jobId"], old["jobId"])

    def test_historical_gets_never_create_or_start_extraction_jobs(self):
        assessment = self.osint.create_assessment(self.task_id, {})["assessment"]
        aid = assessment["assessmentId"]
        for path in (self.create_path, f"/api/osint/assessments/{aid}", f"/api/osint/assessments/{aid}/graph",
                     f"/api/intelligence/assessments/{aid}/extraction-jobs", f"/api/osint/assessments/{aid}/execution"):
            status, _ = self.request(path)
            self.assertEqual(status, 200, path)
        self.assertEqual(self.manager.list(aid), [])
        self.assertEqual(self.provider.calls, [])
        self.start.assert_not_called()

    def test_deterministic_fallback_does_not_start_an_automatic_model_retry(self):
        self.agent.trend_provider = DeterministicTrendProvider()
        status, response = self.request(self.create_path, {})
        self.assertEqual(status, 201)
        self.assertNotIn("extractionJob", response)
        self.assertNotIn("extractionError", response)
        self.assertEqual(self.manager.list(response["assessment"]["assessmentId"]), [])
        self.start.assert_not_called()

    def test_missing_model_configuration_keeps_saved_report_with_clear_retry_error(self):
        self.provider.configured = False
        status, response = self.request(self.create_path, {})
        self.assertEqual(status, 201)
        self.assertIn("DeepSeek 未配置", response["extractionError"])
        self.assertIsNotNone(self.store.get_assessment(response["assessment"]["assessmentId"]))
        self.assertEqual(self.provider.calls, [])


if __name__ == "__main__":
    unittest.main()
