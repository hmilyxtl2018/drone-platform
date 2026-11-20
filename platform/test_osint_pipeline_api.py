"""Isolated HTTP regression for graph, validation and ontology admission.

No application database, saved credentials, public provider, or live service
is touched. Each test serves a disposable SQLite fixture on an ephemeral port.
"""

from __future__ import annotations

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
    from intelligence_agent import IntelligenceAgent, DeterministicClaimProvider, DeterministicTrendProvider
    from intelligence_store import IntelligenceStore
    from capability_analysis import normalize_capabilities
except ModuleNotFoundError:
    from .intelligence_agent import IntelligenceAgent, DeterministicClaimProvider, DeterministicTrendProvider
    from .intelligence_store import IntelligenceStore
    from .capability_analysis import normalize_capabilities


class OSINTPipelineAPITests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.temp = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.store = IntelligenceStore(Path(self.temp) / "api.sqlite")
        self.agent = IntelligenceAgent(self.store, provider=DeterministicClaimProvider(), trend_provider=DeterministicTrendProvider())
        prefix = "demo." if IntelligenceAgent.__module__.startswith("demo.") else ""
        secrets = importlib.import_module(prefix + "provider_secrets")
        agent_module = importlib.import_module(prefix + "intelligence_agent")
        runtime = importlib.import_module(prefix + "runtime_store")
        osint = importlib.import_module(prefix + "osint_store")
        # Importing serve normally constructs singletons and loads encrypted
        # provider config. Patch their constructors before loading the module.
        with patch.object(secrets, "load_provider_environment"), patch.object(agent_module, "IntelligenceAgent", return_value=self.agent), patch.object(runtime, "RuntimeStore", return_value=object()), patch.object(osint, "OSINTTaskStore", return_value=object()):
            serve = importlib.import_module(prefix + "serve")
        self.stack.enter_context(patch.object(serve, "INTELLIGENCE_AGENT", self.agent))
        self.stack.enter_context(patch.object(serve.Handler, "log_message", lambda *_args: None))
        self.server = serve.ThreadingHTTPServer(("127.0.0.1", 0), serve.Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        body = "Inspection camera throughput reached 120 images/min under laboratory lighting."
        material = {"itemId": "http-source", "title": "Public inspection camera test", "sourceUri": "https://example.test/public-camera-test", "sourceVersion": "v1", "publishedAt": "2026-09-01T00:00:00Z", "contentHash": hashlib.sha256(body.encode()).hexdigest(), "body": body}
        draft = DeterministicTrendProvider().generate(task_id="http-task", title="Public camera test", question="What is documented?", horizon_years=3, materials=[material])
        draft["summary"]["capabilityAssessment"] = normalize_capabilities({"metrics": [{"equipment": "Inspection camera", "dimension": "throughput", "metric": "processing rate", "value": 120, "unit": "images/min", "qualifier": "exact", "basis": "reported-test", "conditions": "laboratory lighting", "assessment": "Source-reported laboratory result; repeatability unknown.", "itemId": "http-source", "quote": body}]}, [material])
        self.assessment = self.store.create_assessment(draft)["assessment"]
        self.aid = self.assessment["assessmentId"]

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        self.stack.close()

    def request(self, path, body=None):
        req = Request(self.base + path, data=json.dumps(body).encode("utf-8") if body is not None else None, headers={"Content-Type": "application/json"}, method="POST" if body is not None else "GET")
        try:
            with urlopen(req, timeout=5) as response:
                return response.status, json.load(response)
        except HTTPError as exc:
            with exc:
                return exc.code, json.load(exc)

    def test_graph_and_validation_aliases_persist_and_return_same_records(self):
        for root in ("intelligence", "osint"):
            status, payload = self.request(f"/api/{root}/assessments/{self.aid}/graph", {"expectedRevision": 1})
            self.assertEqual(status, 201)
            graph_id = payload["graph"]["graphRevisionId"]
            for other in ("intelligence", "osint"):
                status, read = self.request(f"/api/{other}/assessments/{self.aid}/graph")
                self.assertEqual(status, 200)
                self.assertEqual(read["graph"]["graphRevisionId"], graph_id)
            status, payload = self.request(f"/api/{root}/assessments/{self.aid}/validate", {"expectedRevision": 1, "kind": "deterministic"})
            self.assertEqual(status, 201)
            self.assertEqual(payload["validationRound"]["kind"], "deterministic")
            self.assertFalse(payload["validationRound"]["confidenceFactors"]["independenceVerified"])
        for root in ("intelligence", "osint"):
            status, payload = self.request(f"/api/{root}/assessments/{self.aid}/validation")
            self.assertEqual(status, 200)
            self.assertEqual(len(payload["validationRounds"]), 2)
            self.assertEqual(payload["rounds"], payload["validationRounds"])

    def test_unknown_assessment_and_admission_ids_return_404(self):
        for root in ("intelligence", "osint"):
            for suffix in ("graph", "validation", "admissions"):
                for body in (None, {}):
                    with self.subTest(root=root, suffix=suffix, method="GET" if body is None else "POST"):
                        status, _ = self.request(f"/api/{root}/assessments/no-such-assessment/{suffix}", body)
                        self.assertEqual(status, 404)
            status, _ = self.request(f"/api/{root}/admissions/no-such-admission")
            self.assertEqual(status, 404)
            for action in ("review", "publish"):
                status, _ = self.request(f"/api/{root}/admissions/no-such-admission/{action}", {"action": "accept", "reviewer": "Test Analyst", "reason": "Fixture only", "expectedRevision": 1})
                self.assertEqual(status, 404)

    def test_invalid_and_stale_revisions_are_rejected_over_http(self):
        for root in ("intelligence", "osint"):
            status, _ = self.request(f"/api/{root}/assessments/{self.aid}/graph", {"expectedRevision": True})
            self.assertEqual(status, 400)
            status, _ = self.request(f"/api/{root}/assessments/{self.aid}/graph", {"expectedRevision": 99})
            self.assertEqual(status, 409)
            status, payload = self.request(f"/api/{root}/assessments/{self.aid}/admissions", {})
            self.assertEqual(status, 201)
            admission_id = payload["admissions"][0]["admissionId"]
            for revision in (True, "1", 1.5):
                status, _ = self.request(f"/api/{root}/admissions/{admission_id}/review", {"action": "accept", "reviewer": "Test Analyst", "reason": "Fixture only", "expectedRevision": revision})
                self.assertEqual(status, 400)

    def test_admission_requires_human_review_then_enters_runtime_query(self):
        for root in ("intelligence", "osint"):
            status, trusted = self.request(f"/api/{root}/ontology")
            self.assertEqual(status, 200)
            self.assertEqual(trusted["admissionCount"], 0)
        status, proposed = self.request(f"/api/intelligence/assessments/{self.aid}/admissions", {"metricIds": ["metric-1"]})
        self.assertEqual(status, 201)
        admission_id = proposed["admissions"][0]["admissionId"]
        status, duplicate = self.request(f"/api/osint/assessments/{self.aid}/admissions", {})
        self.assertEqual(status, 201)
        self.assertEqual(duplicate["admissions"][0]["admissionId"], admission_id)
        status, _ = self.request(f"/api/osint/admissions/{admission_id}/publish", {"reviewer": "Test Analyst", "expectedRevision": 1})
        self.assertEqual(status, 409)
        status, accepted = self.request(f"/api/intelligence/admissions/{admission_id}/review", {"action": "accept", "reviewer": "Test Analyst", "reason": "Checked source, metric meaning, test conditions and mapping", "expectedRevision": 1})
        self.assertEqual(status, 200)
        self.assertEqual(accepted["admission"]["revision"], 2)
        status, trusted = self.request("/api/osint/ontology")
        self.assertEqual(trusted["admissionCount"], 0)
        status, published = self.request(f"/api/osint/admissions/{admission_id}/publish", {"reviewer": "Test Analyst", "expectedRevision": 2})
        self.assertEqual(status, 200)
        self.assertEqual(published["admission"]["status"], "published")
        for root in ("intelligence", "osint"):
            status, trusted = self.request(f"/api/{root}/ontology")
            self.assertEqual(status, 200)
            self.assertEqual(trusted["admissionCount"], 1)
            self.assertEqual(trusted["provenance"][0]["admissionId"], admission_id)
            status, payload = self.request(f"/api/{root}/assessments/{self.aid}/admissions")
            self.assertEqual(status, 200)
            self.assertEqual(payload["admissions"][0]["status"], "published")

    def test_graph_object_selection_and_explicit_verified_confidence_over_http(self):
        source = self.assessment["sourceSnapshot"][0]
        body = source["body"]
        evidence = {key: source[key] for key in ("itemId", "sourceUri", "sourceVersion", "contentHash")}
        evidence.update({"quote": body, "startChar": 0, "endChar": len(body)})
        graph = self.store.save_graph(self.aid, {"nodes": [{"id": "camera", "type": "EquipmentModel", "label": "Inspection camera", "evidenceIds": [source["itemId"]], "payload": {"evidenceRefs": [evidence], "confidence": "high"}}], "edges": []})
        selection = {"graphRevisionId": graph["graphRevisionId"], "graphNodeIds": ["camera"], "graphEdgeIds": []}
        status, proposed = self.request(f"/api/osint/assessments/{self.aid}/admissions", selection)
        self.assertEqual(status, 201)
        admission = proposed["admissions"][0]
        self.assertEqual(admission["kind"], "graph-node")
        self.assertEqual(admission["proposedConfidence"], "medium")
        status, duplicate = self.request(f"/api/intelligence/assessments/{self.aid}/admissions", selection)
        self.assertEqual(status, 201)
        self.assertEqual(duplicate["admissions"][0]["admissionId"], admission["admissionId"])
        forged = {**selection, "assertion": {"label": "Fake fact"}}
        status, _ = self.request(f"/api/osint/assessments/{self.aid}/admissions", forged)
        self.assertEqual(status, 400)
        review = {"action": "accept", "reviewer": "Test Analyst", "reason": "Source checked; fixture only", "expectedRevision": 1}
        status, _ = self.request(f"/api/osint/admissions/{admission['admissionId']}/review", review)
        self.assertEqual(status, 400)
        status, accepted = self.request(f"/api/osint/admissions/{admission['admissionId']}/review", {**review, "verifiedConfidence": "high"})
        self.assertEqual(status, 200)
        status, _ = self.request(f"/api/intelligence/admissions/{admission['admissionId']}/publish", {"reviewer": "Test Analyst", "expectedRevision": accepted["admission"]["revision"]})
        self.assertEqual(status, 200)
        status, trusted = self.request("/api/osint/ontology")
        self.assertEqual(status, 200)
        self.assertEqual(trusted["objects"][0]["type"], "EquipmentModel")
        retained = trusted["provenance"][0]["evidenceRefs"][0]
        self.assertTrue(all(retained[key] == value for key, value in evidence.items()))
        self.assertEqual(retained["verificationStatus"], "unknown")


if __name__ == "__main__":
    unittest.main()
