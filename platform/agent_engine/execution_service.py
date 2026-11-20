"""Worker entrypoint: execute a Run and enqueue its deterministic successor."""
from __future__ import annotations

from .orchestration import AgentOrchestrator, HandoffRepository, DELIVERABLE_STATUSES
from .worker import control_context
from .uav_execution_contracts import BaselineNotReady
import logging

logger = logging.getLogger(__name__)


class ExecutionService:
    def __init__(self, engine, queue):
        self.engine, self.queue = engine, queue
        self.orchestrator = AgentOrchestrator(HandoffRepository(engine.repo), self._enqueue)

    def _enqueue(self, run_id):
        # A predecessor can be retried after its successor already completed.
        # Existing queue state remains authoritative; never restart that child.
        existing = self.queue.get(run_id)
        if existing:
            return existing
        run = self.engine.repo.get_run(run_id)
        if run and run.get('status') in {'completed', 'completed-with-gaps', 'requires-human-review',
                                          'blocked', 'failed', 'cancelled'}:
            return {'runId': run_id, 'status': run['status'], 'enqueued': False}
        return self.queue.enqueue(run_id)

    def reconcile(self):
        """Resume committed delivery/outbox work after a process interruption.

        This trusted worker coordinator never calls providers. Repeating an
        existing handoff only ensures its missing queue entry is created.
        """
        with self.engine.repo.connection_factory() as con:
            with con.cursor() as c:
                c.execute("SELECT r.run_id FROM agent_run r JOIN agent_run_queue q USING(run_id) "
                          "WHERE q.pool_key=%s AND q.status='done' AND r.status=ANY(%s) "
                          "AND r.input_json->>'collaborate'='true' AND r.agent_key<>'uav-evidence-reviewer'",
                          (self.queue.pool_key, sorted(DELIVERABLE_STATUSES)))
                ids = [row['run_id'] for row in c.fetchall()]
        for run_id in ids:
            with control_context(run_id):
                try:
                    self._continue(run_id)
                except BaselineNotReady:
                    # A newly tightened admission policy must neither change a
                    # delivered historical Run nor prevent the worker starting.
                    logger.warning('Successor baseline not ready for historical Run %s', run_id)
        from .cognitive_identity import APPLICATION_KEY
        with self.engine.repo.connection_factory() as con:
            with con.cursor() as c:
                c.execute("SELECT to_regclass(current_schema() || '.agent_cognitive_revision') AS name")
                if not c.fetchone()['name']:
                    return
                c.execute("SELECT r.run_id FROM agent_run r JOIN agent_run_queue q USING(run_id) "
                          "WHERE q.pool_key=%s AND q.status IN ('done','failed') AND r.status='completed-with-gaps' "
                          "AND r.agent_key=%s AND NOT r.input_json ? 'cognitiveRevisionOf'",
                          (self.queue.pool_key, APPLICATION_KEY))
                cognitive_ids = [row['run_id'] for row in c.fetchall()]
        for run_id in cognitive_ids:
            with control_context(run_id):
                try:
                    self._cognitive_continue(run_id)
                except Exception:
                    # A hard historical integrity failure stays visible in its
                    # original Run; it cannot stop unrelated worker recovery.
                    logger.warning('Cognitive successor could not be reconciled for Run %s', run_id)

    def _cognitive_continue(self, run_id):
        from .cognitive_revision import create_successor
        successor = create_successor(self.engine.repo, run_id)
        if successor.get('runId'):
            self._enqueue(successor['runId'])
        self.engine.repo.append_event(run_id, 'cognitive.successor-admission', detail=successor)
        return successor

    def _continue(self, run_id):
        run = self.engine.repo.get_run(run_id)
        permitted = {"agent-output", "EvidenceBundle", "ontology-source-snapshot", "web-source-snapshot",
                     "Assessment", "GapList", "Assumptions", "DesignBrief", "ConstraintMatrix",
                     "DataRequestList", "VerificationPlan", "ReviewPackage", "BlockingIssues", "TraceabilityMatrix"}
        ids = [a["artifact_id"] for a in self.engine.repo.list_artifacts(run_id)
               if a["artifact_type"] in permitted]
        return self.orchestrator.after_delivery(
            run_id, ids, include_design=bool(run["input_json"].get("includeDesign", False)))

    async def execute(self, run_id):
        result = await self.engine.execute(run_id)
        run = self.engine.repo.get_run(run_id)
        from .cognitive_identity import APPLICATION_KEY
        if (run.get('agent_key') == APPLICATION_KEY and result['status'] == 'completed-with-gaps'
                and not run['input_json'].get('cognitiveRevisionOf')):
            result['successor'] = self._cognitive_continue(run_id)
        if (run["input_json"].get("executionMode") == "research"
                and run["input_json"].get("collaborate", False)
                and result["status"] in DELIVERABLE_STATUSES):
            # Hash-bound source bodies travel with the report; downstream
            # citation validation never relies on the upstream prose alone.
            try:
                result["handoffs"] = self._continue(run_id)
            except BaselineNotReady as exc:
                result.update(handoffs=[], handoffStatus='baseline-not-ready',
                              handoffAdmission=exc.admission_details)
        return result
