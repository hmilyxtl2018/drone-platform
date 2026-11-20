"""Smoke tests for conversation session HTTP routes."""
from __future__ import annotations

import unittest
from unittest.mock import patch

import serve


class _AgentStub:
    store = object()


class _FakeConversations:
    def create_session(self, payload): return {"conversationSessionId": "conversation-test", **payload}
    def submit_prompt(self, session_id, payload, orchestrator): return {"session": {"conversationSessionId": session_id}, "analysisRun": {"analysisRunId": "analysis-test"}, "message": {"analysisRunId": "analysis-test"}}
    def restore_session(self, session_id): return {"conversationSessionId": session_id, "status": "active"}
    def clear_display(self, session_id): return {"conversationSessionId": session_id, "displayCursor": 3}


class _FakeOrchestrator:
    pass


class ConversationApiTests(unittest.TestCase):
    def _run(self, path, body, sent):
        handler = object.__new__(serve.Handler)
        with patch.object(serve, "ontology_agent", return_value=_AgentStub()), \
             patch.object(serve, "CapabilityProfiles"), \
             patch.object(serve, "EntityAuditRuns"), \
             patch.object(serve, "ConversationStore", return_value=_FakeConversations()), \
             patch.object(serve, "MissionOrchestrator", return_value=_FakeOrchestrator()), \
             patch.object(serve.Handler, "_read_json", return_value=body), \
             patch.object(serve.Handler, "_send_json", side_effect=lambda value, status=200: sent.update({"value": value, "status": status})):
            handler.handle_ontology_post(path)

    def test_prompt_route_returns_bound_run(self):
        sent = {}
        self._run("/api/ontology/conversation-sessions/conversation-test/prompt", {"prompt": "audit datalink"}, sent)
        self.assertEqual(sent["status"], 202)
        self.assertEqual(sent["value"]["analysisRun"]["analysisRunId"], "analysis-test")

    def test_restore_and_clear_display_routes(self):
        for action, expected in (("restore", "active"), ("clear-display", 3)):
            sent = {}
            self._run(f"/api/ontology/conversation-sessions/conversation-test/{action}", {}, sent)
            self.assertEqual(sent["status"], 200)
            session = sent["value"]["session"]
            self.assertEqual(session.get("status", session.get("displayCursor")), expected)


if __name__ == "__main__":
    unittest.main()
