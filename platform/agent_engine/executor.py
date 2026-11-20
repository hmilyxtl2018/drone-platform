from __future__ import annotations

from agent_engine.application_identity import FUTURE_AGENT_KEYS

import asyncio
import json
import time
from copy import deepcopy

from .approvals import HumanApprovalRequired
from .runtime_contracts import BUILTIN_EXECUTOR_KEYS
from .reasoning_harness import ReasoningHarness
from .action_reasoning import child_reasoning, receive_child_result


def refresh_results(ctx):
    """Rebuild derived state so a loop can clear findings and old evidence."""
    evidence = {}
    findings, gaps = {}, {}
    for step in ctx.state.get('steps', {}).values():
        for item in step.get('evidence', []):
            evidence[json.dumps(item, sort_keys=True)] = item
        for item in step.get('gaps', []):
            gaps[json.dumps(item, sort_keys=True)] = item
        for item in step.get('findings', []):
            findings[json.dumps(item, sort_keys=True)] = item
        for value in step.get('toolResults', {}).values():
            for item in value.get('evidence', []):
                evidence[json.dumps(item, sort_keys=True)] = item
            for item in value.get('gaps', []):
                gaps[json.dumps(item, sort_keys=True)] = item
    ctx.state['evidence'] = list(evidence.values())
    ctx.state['findings'] = list(findings.values())
    ctx.state['capabilityGaps'] = list(gaps.values())
    ctx.state['evidenceGap'] = not bool(evidence)


class StepExecutor:
    def __init__(self, repo, gateway, validator, child_runner=None):
        self.repo, self.gateway, self.validator = repo, gateway, validator
        self.child_runner = child_runner
        self._handlers = {}
        self._executor_keys = {}
        self._preparers = {}
        self.reasoning = ReasoningHarness(repo)

    def register(self, agent_key, step_key, handler, executor_key=None):
        if not callable(handler):
            raise TypeError('step executor must be callable')
        expected = BUILTIN_EXECUTOR_KEYS.get(step_key)
        if executor_key is not None and executor_key != expected:
            raise ValueError(f'executor key does not match reviewed contract: {agent_key}/{step_key}')
        self._handlers[(agent_key, step_key)] = handler
        self._executor_keys[(agent_key, step_key)] = executor_key or expected

    def has_executor(self, agent_key, step_key):
        return (agent_key, step_key) in self._handlers

    def register_preparer(self, agent_key, step_key, handler):
        """Prepare typed material before dispatching the node's Action child."""
        self._preparers[(agent_key, step_key)] = handler

    @staticmethod
    def _node_budget(ctx, key):
        baseline = getattr(ctx.profile, 'execution_baseline', {}) or {}
        graph = baseline.get('graph', {}) if isinstance(baseline, dict) else {}
        return next((node.get('budget', {}) for node in graph.get('nodes', [])
                     if node.get('id') == key), {})

    @staticmethod
    def _child_binding(ctx, key):
        baseline = getattr(ctx.profile, 'execution_baseline', {}) or {}
        graph = baseline.get('graph', {}) if isinstance(baseline, dict) else {}
        return next((node for node in graph.get('nodes', [])
                     if node.get('id') == key and node.get('childAgentKey')), None)

    def _rounds_consumed(self, run_id, step_key, attempt):
        """Count completed execution rounds while allowing gate/recovery resume.

        A waiting-human pause and a worker-interruption recovery are continuations
        of the same round.  Actual failures/cancellations consume a round and
        therefore remain bounded by the immutable node budget.
        """
        rows = self.repo.list_steps(run_id)
        consumed = 0
        for row in rows:
            if row.get('step_key') != step_key or int(row.get('attempt', -1)) >= attempt:
                continue
            status = row.get('status')
            error = str(row.get('error_message') or '')
            if status == 'waiting-human' or 'worker interrupted' in error:
                continue
            if hasattr(self.repo,'can_continue_infrastructure_step') and self.repo.can_continue_infrastructure_step(run_id,row):
                continue
            consumed += 1
        return consumed

    @staticmethod
    def _action_payload(ctx, step, child_agent_key):
        """Project only the typed material required by the configured Action."""
        from .cognitive_identity import APPLICATION_KEY
        if ctx.profile.agent_key == APPLICATION_KEY:
            from .cognitive_runtime import child_payload
            return child_payload(ctx, step, child_agent_key)
        state = ctx.state or {}
        steps = {**(state.get('steps') or {}), **(state.get('_preparedSteps') or {})}
        bundle = (steps.get('collect-public-evidence') or {}).get('sourceBundle') or {}
        if not bundle:
            supplied = ctx.input.get('sourceBundle') or ctx.input.get('sources') or {}
            bundle = supplied if isinstance(supplied, dict) else {'sources': supplied}
        claims = list(bundle.get('claims') or []) if isinstance(bundle, dict) else []
        claims = list((steps.get('extract-facts') or {}).get('claims', claims))
        sources = list(bundle.get('sources') or []) if isinstance(bundle, dict) else []
        judgments = list((steps.get('analyze-by-perspective') or {}).get('perspectiveFindings') or [])
        conflicts = list((steps.get('fuse-evidence-and-conflicts') or {}).get('conflicts') or [])
        if child_agent_key == 'evidence-collection-action':
            arguments = {'sources': sources}
        elif child_agent_key == 'evidence-extraction-action':
            # In the child-reasoning contract the parent sends source material;
            # claims are deliberately absent so the parent cannot precompute or
            # silently own the extraction model call.
            arguments = {'claims': [] if child_reasoning(ctx.profile) else claims,
                         'events': list(bundle.get('events') or []) if isinstance(bundle, dict) else []}
            if child_reasoning(ctx.profile):
                arguments.update(sourceBundle=deepcopy(bundle), question=ctx.input.get('question'),
                                 perspectives=deepcopy(ctx.input.get('perspectives', [])))
                if state.get('_claimsArchive'):
                    arguments['sourceBundle']['claims'] = deepcopy(state['_claimsArchive'])
        elif child_agent_key == 'trend-analysis-action':
            values = ctx.input.get('trendValues', [])
            arguments = {'values': list(values) if isinstance(values, list) else []}
            if ctx.profile.model_policy.get('config_json', {}).get('researchMethodsVersion') == '1.0.0':
                # Each point must originate in validated evidence, not arbitrary trendValues.
                arguments = {'observations': [deepcopy(c['observation']) for c in claims if isinstance(c.get('observation'), dict)],
                    'parameters': deepcopy(ctx.input.get('trendParameters') or {})}
        elif child_agent_key == 'scenario-analysis-action':
            arguments = {'judgments': [] if child_reasoning(ctx.profile) else [{**{key: deepcopy(item.get(key, [] if key != 'statement' else ''))
                                        for key in ('statement','supportingClaims','conditions','counterArguments','unknowns')},
                                       'scenarioCard': deepcopy(item.get('scenarioCard', {}))}
                                       for item in judgments if isinstance(item, dict)]}
            if child_reasoning(ctx.profile):
                arguments.update(question=ctx.input.get('question'), scope=deepcopy(ctx.input.get('scope')),
                    perspectives=deepcopy((steps.get('define-research-question') or {}).get('perspectives') or ctx.input.get('perspectives', [])),
                    evidence=deepcopy((steps.get('fuse-evidence-and-conflicts') or {}).get('evidence', [])),
                    searches=deepcopy(bundle.get('searches', [])), followupMode=ctx.input.get('followupMode'),
                    followupTarget=deepcopy(ctx.input.get('followupTarget')))
        elif child_agent_key == 'red-team-action':
            arguments = {'claims': claims,
                         'judgments': [{key: deepcopy(item.get(key, [] if key != 'statement' else ''))
                                        for key in ('statement','supportingClaims','conditions','counterArguments','unknowns')}
                                       for item in judgments if isinstance(item, dict)],
                         'conflicts': conflicts}
        else:
            raise ValueError('unsupported Action Agent binding: ' + child_agent_key)
        from .decision_semantics import enabled, task_spec
        if enabled(ctx.profile) and child_agent_key in {'evidence-extraction-action', 'scenario-analysis-action'}:
            spec = task_spec(ctx)
            arguments.update(decisionSpec=deepcopy(spec), scope=deepcopy(ctx.input.get('scope', {})))
        return {**arguments, 'parentStepKey': step.key,
                'parentRunId': ctx.run_id, 'inputAuthority': 'parent-run-state'}

    async def execute(self, ctx, step):
        self.repo.transition(ctx.run_id, 'running', step.key)
        attempt = self.repo.next_step_attempt(ctx.run_id, step.key)
        sid = self.repo.step(ctx.run_id, step.key, 'running', ctx.state, attempt=attempt)
        ctx.current_step_run_id = sid
        action_invocation = None
        action_output = None
        try:
            if hasattr(self.repo, 'assert_task_dispatch'):
                self.repo.assert_task_dispatch(ctx.run_id, operation='analyze')
            recovered = self.reasoning.before_step(ctx, step)
            if recovered is not None:
                return self._finish(ctx, step, attempt, recovered)
            # Check every gate before starting any tool in a parallel group.
            for key in step.tools:
                self.gateway.authorize(ctx, key, step.key)
            node_budget = self._node_budget(ctx, step.key)
            started = time.monotonic()
            timeout = int(node_budget.get('max_runtime_seconds', 0) or 0)
            async def bounded(operation):
                remaining = max(0, timeout - (time.monotonic() - started)) if timeout else None
                async with asyncio.timeout(remaining):
                    return await operation
            max_rounds = node_budget.get('max_rounds')
            if max_rounds is not None and int(max_rounds) > 0:
                # Gate pauses and worker recovery resume the same logical
                # round; actual failures/cancellations and loop re-entries are
                # counted by the immutable ceiling.
                if self._rounds_consumed(ctx.run_id, step.key, attempt) >= int(max_rounds):
                    raise RuntimeError(f'node round budget exceeded: {step.key}')
            args = {'input': ctx.input, 'state': ctx.state}
            self.validator.schema(args, step.input_contract)
            handler = self._handlers.get((ctx.profile.agent_key, step.key))
            # Persist the dispatcher actually entered, before preparations or
            # child scheduling can fail. This is not a claim of an LLM call.
            if hasattr(self.repo, 'append_event'):
                self.repo.append_event(ctx.run_id, 'step.execution', step.key, {
                    'stepRunId': sid,
                    'executorKey': self._executor_keys.get((ctx.profile.agent_key, step.key)) if handler else 'builtin.tool_dispatch.v1',
                    'handlerId': (handler.__module__ + '.' + handler.__qualname__) if handler and hasattr(handler, '__qualname__') else None})
            prepare = self._preparers.get((ctx.profile.agent_key, step.key))
            if prepare and not (child_reasoning(ctx.profile) and step.key in {'extract-facts', 'analyze-by-perspective'}):
                prepared = await bounded(prepare(ctx, step, sid, self.gateway, self.repo))
                if prepared.get('status') == 'blocked':
                    return self._finish(ctx, step, attempt, prepared)
                ctx.state.setdefault('_preparedSteps', {})[step.key] = prepared
                self.repo.save_state(ctx)
            binding = self._child_binding(ctx, step.key)
            if binding:
                if self.child_runner is None:
                    raise RuntimeError('Action child runner is not configured')
                from .orchestration import ActionInvocationRepository
                action_repo = ActionInvocationRepository(self.repo)
                payload = self._action_payload(ctx, step, binding['childAgentKey'])
                input_hash = __import__('hashlib').sha256(
                    __import__('json').dumps(payload, sort_keys=True, default=str).encode()).hexdigest()
                input_artifact_id = self.repo.add_artifact(ctx.run_id, 'action-input', payload, sid)
                action_invocation = action_repo.create(
                    ctx.run_id, sid, step.key, binding['childAgentKey'], binding['compositionEdgeId'],
                    input_artifact_id, input_hash, payload,
                    f'action:{ctx.run_id}:{step.key}:{getattr(ctx, "iteration", 0)}')
                try:
                    child_result = await bounded(self.child_runner(action_invocation['childRunId']))
                    child_status = child_result.get('status') if isinstance(child_result, dict) else 'failed'
                    action_invocation = action_repo.complete(
                        action_invocation['invocationId'], ctx.run_id,
                        action_invocation['childRunId'], child_status)
                    if child_status not in {'completed', 'completed-with-gaps', 'requires-human-review'}:
                        raise RuntimeError('Action child Run did not complete: ' + str(child_status))
                    action_output = deepcopy(child_result.get('output') or {})
                    from .cognitive_identity import APPLICATION_KEY as COGNITIVE_APPLICATION
                    if ctx.profile.agent_key == COGNITIVE_APPLICATION and step.key == 'extract-claims':
                        from .cognitive_legacy_adapter import receive_extraction_result
                        action_output = receive_extraction_result(self.repo, ctx, action_invocation['childRunId'],
                                                                  action_output, payload['sourceBundle'])
                    elif child_reasoning(ctx.profile) and step.key in {'extract-facts', 'analyze-by-perspective'}:
                        if action_output.get('actionReasoningVersion') != 'child-v1':
                            raise RuntimeError('Action child reasoning contract mismatch')
                        action_output = receive_child_result(self.repo, ctx, action_invocation['childRunId'], action_output)
                    persist = getattr(self.repo, 'add_future_evidence_reference', None)
                    if persist is not None:
                        for item in (action_output.get('evidence') or []):
                            if isinstance(item, dict) and item.get('sourceArtifactId'):
                                # The child carries the parent's immutable
                                # source reference. Persist it under the
                                # parent Run where that artifact is owned.
                                persist(ctx.run_id, item)
                    ctx.state.setdefault('_actionResults', {})[step.key] = deepcopy(action_output)
                except BaseException:
                    try:
                        action_repo.complete(action_invocation['invocationId'], ctx.run_id,
                                             action_invocation['childRunId'], 'failed')
                    except Exception:
                        pass
                    raise
            handler = self._handlers.get((ctx.profile.agent_key, step.key))
            if handler is not None:
                result = await bounded(handler(ctx, step, sid, self.gateway, self.repo))
            else:
                result = {'stepKey': step.key, 'inputs': deepcopy(ctx.input)}
                if step.key == 'parse-requirement':
                    result['requirements'] = ctx.input.get('requirements', {})
                if step.tools:
                    semaphore = ctx.tool_semaphore or asyncio.Semaphore(4)
                    node_budget = self._node_budget(ctx, step.key)
                    node_semaphore = asyncio.Semaphore(max(1, int(node_budget.get('max_parallelism', len(step.tools)) or 1)))
                    async def call(key):
                        async with semaphore:
                            async with node_semaphore:
                                return key, await self.gateway.invoke(ctx, sid, key, args, step.key)
                    operation = asyncio.gather(*(call(k) for k in step.tools), return_exceptions=True)
                    timeout = int(node_budget.get('max_runtime_seconds', 0) or 0)
                    pairs = await asyncio.wait_for(operation, timeout=timeout) if timeout > 0 else await operation
                    errors = [p for p in pairs if isinstance(p, BaseException)]
                    if errors:
                        raise errors[0]
                    result['toolResults'] = dict(pairs)
            if action_invocation:
                result['actionInvocation'] = deepcopy(action_invocation)
                result['actionOutput'] = deepcopy(action_output or {})
            self.validator.step_contract(step, result)
            return self._finish(ctx, step, attempt, result)
        except HumanApprovalRequired as exc:
            self.repo.step(ctx.run_id, step.key, 'waiting-human', error=str(exc), attempt=attempt)
            raise
        except asyncio.CancelledError:
            self.repo.step(ctx.run_id, step.key, 'cancelled', error='execution deadline or worker cancellation', attempt=attempt)
            raise
        except Exception as exc:
            self.repo.step(ctx.run_id, step.key, 'failed', error=str(exc), attempt=attempt)
            raise

    def _finish(self, ctx, step, attempt, result):
        from .decision_semantics import validate_step
        validate_step(ctx, step, result)
        result = self.reasoning.after_step(ctx, step, result)
        ctx.state.setdefault('steps', {})[step.key] = result
        ctx.state.get('_preparedSteps', {}).pop(step.key, None)
        refresh_results(ctx)
        self.repo.save_state(ctx)
        if result.get('status') == 'blocked':
            self.repo.step(ctx.run_id, step.key, 'blocked', output=result, attempt=attempt,
                           error='; '.join(str(g.get('reason')) for g in result.get('gaps', []) if g.get('reason'))[:1000])
            self.repo.transition(ctx.run_id, 'blocked', error='future-battlefield research precondition failed')
            return result
        from .cognitive_identity import AGENT_KEYS as COGNITIVE_KEYS
        status = 'completed-with-gaps' if (ctx.profile.agent_key in FUTURE_AGENT_KEYS or ctx.profile.agent_key in COGNITIVE_KEYS) and result.get('gaps') else 'succeeded'
        self.repo.step(ctx.run_id, step.key, status, output=result, attempt=attempt)
        return result
