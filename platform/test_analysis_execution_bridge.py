"""Regression tests for the audit-to-multi-agent compatibility bridge."""
from __future__ import annotations

import unittest
import threading
import time
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from analysis_execution_bridge import AGENT_ROLES, EntityAuditExecutionBridge, _artifact_content


class FakeOrchestrator:
    def __init__(self):
        self.run = {"analysisRunId": "analysis-e2e", "tasks": [
            {"agentTaskId": f"task-{index}", "role": role, "status": "queued"}
            for index, role in enumerate(AGENT_ROLES)
        ]}
        # planner is initially ready; the remaining tasks model the migration
        # dependency DAG and become ready after their predecessor completes.
        self.done = set()
        self.artifacts = []
        self.handoffs = []

    def create(self, request, *, entity_id=None):
        self.run["request"] = request
        self.run["entityId"] = entity_id
        return self.run

    def start(self, run_id):
        self.run["status"] = "running"
        return self.run

    def claim_ready_tasks(self, run_id, *, limit=None, worker_id="orchestrator"):
        deps = {
            "ontology-planner": (),
            "intelligence-collector": ("ontology-planner",),
            "attribute-relation-extractor": ("intelligence-collector",),
            "signal-imagery-analyst": ("intelligence-collector",),
            "evidence-fusion": ("attribute-relation-extractor", "signal-imagery-analyst"),
            "capability-quantification": ("evidence-fusion",),
            "red-team-governance-reviewer": ("capability-quantification",),
        }
        for row in self.run["tasks"]:
            if row["role"] in self.done or any(dep not in self.done for dep in deps[row["role"]]):
                continue
            row["status"] = "running"
            return [row]
        return []

    def add_artifact(self, task_id, artifact_type, content, *, status="under-review"):
        self.assert_review_only(status)
        artifact = {"artifactId": f"artifact-{len(self.artifacts)}", "agentTaskId": task_id,
                    "artifactType": artifact_type, "status": status, "content": content}
        self.artifacts.append(artifact)
        return artifact

    def complete_task(self, task_id, output=None, *, worker_id=None):
        row = next(row for row in self.run["tasks"] if row["agentTaskId"] == task_id)
        self.done.add(row["role"])
        row["status"] = "succeeded"
        row["output"] = output or {}
        return row

    def handoff(self, from_task_id, to_task_id, *, artifact_id=None, payload=None):
        handoff = {"handoffId": f"handoff-{len(self.handoffs)}", "fromTaskId": from_task_id,
                   "toTaskId": to_task_id, "artifactId": artifact_id, "status": "pending",
                   "payload": payload or {}}
        self.handoffs.append(handoff)
        return handoff

    def update_handoff(self, handoff_id, status):
        row = next(row for row in self.handoffs if row["handoffId"] == handoff_id)
        row["status"] = status
        return row

    @staticmethod
    def assert_review_only(status):
        if status != "under-review":
            raise AssertionError(f"candidate unexpectedly entered {status}")


class FakeAuditRuns:
    def submit(self, request, entity_id, *, background=False):
        return {
            "auditRunId": "audit-e2e", "entityId": entity_id, "objectRevision": 1,
            "request": request, "result": {"quality": {"conflictCount": 1}},
            "events": [
                {"stage": "collecting", "status": "completed", "detail": {"sourceCount": 2}},
            ],
            "candidates": [{"candidateType": "attribute", "candidateId": "candidate-1"}],
            "signal": {
                "observations": [{"observationId": "observation-1"}],
                "claims": [{"claimId": "claim-1", "reviewStatus": "under-review"}],
                "evidenceBundles": [{"evidenceBundleId": "bundle-1"}],
                "verificationRuns": [{"verificationRunId": "verification-1"}],
                "emitterSignatures": [],
            },
        }


class FakeConversationStore:
    def __init__(self):
        self.bindings = []

    def bind_run(self, session_id, run_id, *, prompt=None, intent=None):
        self.bindings.append({"sessionId": session_id, "runId": run_id, "prompt": prompt, "intent": intent})
        return {"sessionId": session_id}


class OrderedAuditRuns(FakeAuditRuns):
    """Provider stub that records DAG progress before the slow audit step."""

    def __init__(self, orchestrator):
        self.orchestrator = orchestrator
        self.planner_status = None

    def submit(self, request, entity_id, *, background=False):
        planner = next(row for row in self.orchestrator.run["tasks"]
                       if row["role"] == "ontology-planner")
        self.planner_status = planner["status"]
        return super().submit(request, entity_id, background=background)


class AnalysisExecutionBridgeTests(unittest.TestCase):
    def test_candidate_context_and_source_snapshot_are_preserved(self):
        audit = {
            "candidates": [{
                "candidate": {
                    "candidateId": "candidate-ctx", "entityId": "equipment-ababil-3",
                    "attributeKey": "datalink-range", "proposedValue": 180,
                    "evidence": [{
                        "sourceUri": "https://army.example/report", "sourceVersion": "v2",
                        "contentHash": "a" * 64, "quote": "Data link range was 180 km.",
                        "lineStart": 12, "lineEnd": 12, "snapshotMatch": True,
                    }],
                }
            }],
            "signal": {"observations": []},
            "events": [],
        }
        extracted = _artifact_content(audit, "attribute-relation-extractor")
        self.assertEqual(extracted["candidateFacts"][0]["evidenceRefs"][0]["contentHash"], "a" * 64)
        self.assertEqual(extracted["candidateFacts"][0]["contextGaps"], ["conditions", "variant", "configuration"])
        collected = _artifact_content(audit, "intelligence-collector")
        self.assertEqual(collected["sourceCount"], 1)
        self.assertTrue(collected["sourceRefs"][0]["snapshotMatched"])

    def test_planner_completes_before_slow_audit_worker(self):
        orchestrator = FakeOrchestrator()
        audits = OrderedAuditRuns(orchestrator)
        output = EntityAuditExecutionBridge(orchestrator, audits).execute(
            "equipment-planner-order", {"trigger": "manual"}
        )

        self.assertEqual(audits.planner_status, "succeeded")
        self.assertEqual(output["artifacts"][0]["artifactType"], "ontology-planner")
        self.assertEqual(orchestrator.run["tasks"][0]["status"], "succeeded")

    def test_ababil_e2e_records_all_agents_and_never_publishes(self):
        orchestrator = FakeOrchestrator()
        bridge = EntityAuditExecutionBridge(orchestrator, FakeAuditRuns())
        output = bridge.execute("equipment-ababil-3", {"maxJobs": 2})

        self.assertEqual(output["analysisRunId"], "analysis-e2e")
        self.assertEqual(len(output["artifacts"]), 7)
        self.assertEqual(len(output["handoffs"]), 7)
        self.assertFalse(output["autoPublished"])
        self.assertEqual(output["publication"], "requires-explicit-human-review")
        self.assertEqual(set(orchestrator.done), set(AGENT_ROLES))
        self.assertTrue(all(row["status"] == "under-review" for row in orchestrator.artifacts))
        self.assertTrue(all(row["content"].get("auditRunId") == "audit-e2e" for row in orchestrator.artifacts[1:]))
        red_team = next(row for row in orchestrator.artifacts if row["artifactType"] == "red-team-governance-reviewer")
        self.assertEqual(red_team["content"]["findings"][0]["code"], "source-conflict")
        self.assertTrue(all(row["status"] == "accepted" for row in orchestrator.handoffs))

    def test_audit_request_cannot_enable_auto_publish(self):
        orchestrator = FakeOrchestrator()
        output = EntityAuditExecutionBridge(orchestrator, FakeAuditRuns()).execute(
            "equipment-mq1c", {"autoPublish": True, "trigger": "periodic"}
        )
        self.assertFalse(output["autoPublished"])
        self.assertFalse(orchestrator.run["request"]["autoPublish"])

    def test_conversation_session_is_bound_before_agent_execution(self):
        orchestrator = FakeOrchestrator()
        sessions = FakeConversationStore()
        bridge = EntityAuditExecutionBridge(orchestrator, FakeAuditRuns(), sessions)
        bridge.execute("equipment-ababil-3", {
            "conversationSessionId": "conversation-demo",
            "prompt": "审计协同与通信能力",
            "intent": {"capabilityDomains": ["communications", "coordination"]},
        })
        self.assertEqual(sessions.bindings, [{
            "sessionId": "conversation-demo", "runId": "analysis-e2e",
            "prompt": "审计协同与通信能力",
            "intent": {"capabilityDomains": ["communications", "coordination"]},
        }])

    def test_real_orchestrator_persists_ababil_chain_and_review_gate(self):
        """Exercise the bridge against the migration-backed MissionOrchestrator."""
        try:
            from intelligence_store import IntelligenceStore
            from agent_orchestrator import MissionOrchestrator
        except ImportError:  # pragma: no cover - package invocation
            from .intelligence_store import IntelligenceStore
            from .agent_orchestrator import MissionOrchestrator
        with tempfile.TemporaryDirectory() as directory:
            store = IntelligenceStore(Path(directory) / "analysis.sqlite")
            agent = SimpleNamespace(store=store)
            agent._entity_document = lambda entity_id: ({"entityId": entity_id}, {})
            orchestrator = MissionOrchestrator(agent)
            result = EntityAuditExecutionBridge(orchestrator, FakeAuditRuns()).execute(
                "equipment-ababil-3", {"trigger": "manual", "maxJobs": 2}
            )
            run = orchestrator.get(result["analysisRunId"])
            self.assertEqual(run["status"], "completed")
            self.assertEqual(len(run["tasks"]), 7)
            self.assertTrue(all(task["status"] == "succeeded" for task in run["tasks"]))
            self.assertEqual(len(run["artifacts"]), 7)
            self.assertTrue(all(artifact["status"] == "under-review" for artifact in run["artifacts"]))
            self.assertEqual(len(run["handoffs"]), 7)
            self.assertTrue(all(handoff["status"] == "accepted" for handoff in run["handoffs"]))
            self.assertEqual(run["result"]["publication"], "requires-explicit-human-review")

    def test_execute_endpoint_dispatches_async_and_returns_202_envelope(self):
        """Smoke-test the production route without starting a network server."""
        try:
            import serve  # platform/ test invocation
        except ImportError:  # pragma: no cover - package invocation
            from . import serve

        class HttpOrchestrator:
            def __init__(self, _agent):
                self.run = {"analysisRunId": "analysis-http", "entityId": "equipment-ababil-3", "request": {}}

            def get(self, run_id):
                self.run["analysisRunId"] = run_id
                return self.run

        called = threading.Event()

        class HttpBridge:
            def __init__(self, _orchestrator, _audits, *_optional):
                pass

            def execute_existing(self, run_id, entity_id, request, *, envelope):
                self.args = (run_id, entity_id, request, envelope)
                called.set()

        sent = {}
        handler = object.__new__(serve.Handler)
        class AgentStub:
            store = object()
        with patch.object(serve, "MissionOrchestrator", HttpOrchestrator), \
             patch.object(serve, "EntityAuditExecutionBridge", HttpBridge), \
             patch.object(serve, "ontology_agent", return_value=AgentStub()), \
             patch.object(serve.Handler, "_read_json", return_value={"trigger": "manual"}), \
             patch.object(serve.Handler, "_send_json", side_effect=lambda value, status=200: sent.update({"value": value, "status": status})):
            handler.handle_ontology_post("/api/ontology/analysis-runs/analysis-http/execute")

        self.assertEqual(sent["status"], 202)
        self.assertEqual(sent["value"]["analysisRun"]["dispatch"], "queued")
        self.assertTrue(called.wait(1), "background execution worker was not dispatched")


if __name__ == "__main__":
    unittest.main()
