from __future__ import annotations

from agent_engine.application_identity import FUTURE_AGENT_KEYS

import asyncio
import time
from copy import deepcopy

from .approvals import ApprovalService, HumanApprovalRequired
from .convergence import ConvergenceEvaluator
from .domain import RunContext, RunStatus
from .executor import StepExecutor, refresh_results
from .loops import LoopController
from .planning import FixedPlanner, PlanValidator
from .profile_loader import ProfileLoader
from .recovery import RecoveryService
from .repository import PostgresRepository, as_json
from .evidence_repository import EvidenceRepository
from .state_machine import StateMachine
from .tool_gateway import ToolGateway
from .trace import TraceWriter
from .validation import ValidationEngine


TERMINAL = {'completed', 'completed-with-gaps', 'requires-human-review', 'blocked', 'failed', 'cancelled'}


class AgentEngine:
    def __init__(self, connection_factory):
        self.repo = EvidenceRepository(connection_factory)
        self.loader = ProfileLoader(self.repo)
        self.machine = StateMachine()
        self.gateway = ToolGateway(self.repo)
        self.validator = ValidationEngine()
        self.executor = StepExecutor(self.repo, self.gateway, self.validator)
        # Application Plan nodes can synchronously dispatch a reviewed Action
        # child Run.  The child still receives its own immutable snapshot and
        # joins the parent's shared budget; this callback only supplies the
        # scheduler entry point.
        self.executor.child_runner = self.execute
        self.convergence = ConvergenceEvaluator()
        self.planner, self.plan_validator = FixedPlanner(), PlanValidator()
        self.loop_controller = LoopController(self.repo)
        self.approvals, self.recovery = ApprovalService(self.repo), RecoveryService(self.repo)
        self.trace = TraceWriter(self.repo)
        from .future_battlefield_runtime import register_future_battlefield_executors
        register_future_battlefield_executors(self.executor)
        from .cognitive_runtime import register_cognitive_executors
        register_cognitive_executors(self.executor)

    def register_tool(self, key, handler, input_schema=None, output_schema=None):
        self.gateway.register(key, handler, input_schema, output_schema)

    def create_run(self, agent_key, input_data, version=None):
        if not isinstance(input_data, dict):
            raise ValueError('run input must be an object')
        if any(k in input_data for k in ('upstreamArtifacts','parentRunId','rootRunId','handoffId','upstreamAgentKey','previousRunId',
                                         'goalContract','localGoal','parentGoalId','goalAssessment')):
            raise PermissionError('handoff input can only be created by the coordinator')
        profile = self.loader.load(agent_key, version)
        from .cognitive_identity import AGENT_KEYS as COGNITIVE_KEYS, APPLICATION_KEY
        if agent_key in COGNITIVE_KEYS:
            if agent_key != APPLICATION_KEY:
                raise PermissionError('cognitive professional roles require a delegated application Run')
            if input_data.get('executionMode') != 'profile-run':
                raise PermissionError('cognitive research uses its admitted fixed baseline')
        # Future Battlefield is an unattended research draft by default.  A
        # caller may explicitly choose the publication gate with
        # automationMode='human-gate'; an omitted mode must not create a
        # hidden waiting-human pause in an automated run.
        if (agent_key in FUTURE_AGENT_KEYS
                and input_data.get('executionMode') == 'profile-run'
                and 'automationMode' not in input_data):
            input_data = {**input_data, 'automationMode': 'unattended-draft'}
        from .task_contracts import validate_task_envelope
        validate_task_envelope(agent_key, profile, input_data)
        if input_data.get('collaborate') and agent_key != 'uav-capability-analyst':
            raise ValueError('collaborative research must start with the capability analyst')
        if input_data.get('executionMode') != 'research':
            self.plan_validator.validate(profile.steps, profile)
            self.plan_validator.validate_runtime(profile)
        return self.repo.create_run(profile, input_data)

    def _run(self, run_id):
        run = self.repo.get_run(run_id)
        if not run:
            raise ValueError('run not found')
        return run

    async def execute(self, run_id, agent_key=None, input_data=None, version=None):
        with self.repo.execution_lock(run_id):
            profile = self.loader.load_for_run(run_id)
            if profile.model_policy.get('config_json',{}).get('engineMode') == 'skill-trial-v1':
                from .skill_trials import execute_trial
                return await execute_trial(self,run_id)
            if profile.model_policy.get('config_json',{}).get('engineMode') == 'reflection-v1':
                from .research_reflection import execute_reflection
                return await execute_reflection(self,run_id)
            if profile.model_policy.get('config_json',{}).get('engineMode') == 'quality-trial-v1':
                from .research_candidate import execute_quality
                return await execute_quality(self,run_id)
            if profile.model_policy.get('config_json', {}).get('engineMode') == 'research-v1':
                run = self._run(run_id)
                if agent_key and agent_key != run['agent_key']:
                    raise ValueError('agent key does not match run')
                if version and version != run['agent_version']:
                    raise ValueError('agent version does not match the run snapshot')
                if input_data is not None and input_data != as_json(run.get('input_json')):
                    raise ValueError('run input is immutable; create a new run for changed input')
                from .research import ResearchExecution
                return await ResearchExecution(self).execute(run_id)
            return await self._execute(run_id, agent_key, input_data, version)

    async def _execute(self, run_id, agent_key, input_data, version):
        run = self._run(run_id)
        if agent_key and agent_key != run['agent_key']:
            raise ValueError('agent key does not match run')
        if version and version != run['agent_version']:
            raise ValueError('agent version does not match the run snapshot')
        stored_input = as_json(run.get('input_json'))
        if input_data is not None and input_data != stored_input:
            raise ValueError('run input is immutable; create a new run for changed input')
        profile = self.loader.load_for_run(run_id)
        ctx = RunContext(run_id, profile, stored_input)
        ctx.root_run_id = run_id
        ctx.role_key = profile.agent_key
        if hasattr(self.repo, 'profile_snapshot_hash'):
            ctx.profile_snapshot_hash = self.repo.profile_snapshot_hash(run_id)
        ctx.state = deepcopy(as_json((run.get('state') or {}).get('state_json')))
        ctx.iteration = int(ctx.state.get('loopIteration', 0))
        ctx.tool_calls = int(run.get('tool_call_count') or 0)
        ctx.started_at = time.monotonic()
        ctx.tool_semaphore = asyncio.Semaphore(int(profile.resource_policy.get('max_parallel_tools', 4)))
        if run['status'] in TERMINAL:
            return self._result(ctx, run['status'])
        if run['status'] == 'waiting-human':
            approvals = self.repo.list_approvals(run_id)
            if any(a['status'] == 'pending' for a in approvals):
                return self._result(ctx, 'waiting-human')
            self._transition(run_id, 'running')
        elif run['status'] == 'created':
            self._transition(run_id, 'queued')
        if self._run(run_id)['status'] == 'queued':
            self._transition(run_id, 'planning')
        if self._run(run_id)['status'] == 'planning':
            self._transition(run_id, 'running')
        try:
            while True:
                steps = self.planner.plan(profile, ctx.state)
                self.plan_validator.validate(steps, profile)
                from .planning import dependency_batches
                groups = dependency_batches(steps)
                for batch in groups:
                    group = [step for step in batch if step.key not in ctx.state.get('steps', {})]
                    if not group:
                        continue
                    status = self._run(run_id)['status']
                    if status in {'waiting-human', 'cancelled', 'blocked', 'failed'}:
                        return self._result(ctx, status)
                    limit = int(profile.convergence.get('max_runtime_seconds', 0) or 0)
                    elapsed = float(ctx.state.get('_runtime_seconds', 0)) + time.monotonic() - ctx.started_at
                    if limit and elapsed >= limit:
                        raise TimeoutError(f'max runtime exceeded: {limit}s')
                    async with asyncio.timeout(max(0,limit-elapsed) if limit else None):
                        if len(group) > 1:
                            node_limit = asyncio.Semaphore(max(1, int(profile.resource_policy.get('max_parallel_tools', 4))))
                            async def execute_ready(step):
                                async with node_limit:
                                    if self._run(run_id)['status'] in {'waiting-human', 'cancelled', 'blocked', 'failed'}:
                                        return None
                                    return await self.executor.execute(ctx, step)
                            results = await asyncio.gather(*(execute_ready(s) for s in group), return_exceptions=True)
                            errors = [r for r in results if isinstance(r, BaseException)]
                            if errors:
                                raise errors[0]
                        else:
                            await self.executor.execute(ctx, group[0])
                status = self._run(run_id)['status']
                if status in {'waiting-human', 'cancelled', 'blocked', 'failed'}:
                    return self._result(ctx, status)
                from .cognitive_identity import AGENT_KEYS as COGNITIVE_KEYS
                if profile.agent_key in COGNITIVE_KEYS:
                    from .cognitive_runtime import final_output
                    ctx.output = final_output(ctx, self.repo)
                    self._transition(run_id, 'validating')
                    self.validator.schema(ctx.output, profile.output_contract.get('schema_json') or {})
                    status = 'completed-with-gaps' if (ctx.output.get('gaps') or ctx.output.get('conflicts')) else 'completed'
                    self.repo.transition(run_id, status, output=ctx.output)
                    return self._result(ctx, status)
                self._set_final_output(ctx)
                evidence_gaps=self.validator.evidence_gaps(profile,ctx.output)
                ctx.state['evidenceGap']=bool(evidence_gaps)
                ctx.output['unresolvedQuestions'].extend(evidence_gaps)
                ctx.state['conditions']={str(i):not bool(evidence_gaps) for i,_ in enumerate(as_json(profile.convergence.get('completion_conditions_json')) or [])}
                self._transition(run_id, 'validating')
                self.validator.output(profile, ctx.output)
                loop = self.loop_controller.evaluate(ctx)
                if loop:
                    ctx.state['loopIteration'] = ctx.iteration
                    rule = profile.loop_rules[0]
                    progress = {key: deepcopy(ctx.state.get(key, [])) for key in ('evidence', 'findings', 'capabilityGaps')}
                    self.repo.record_loop(run_id, rule, ctx.iteration, ctx.state.get('_loop_last_progress', {}), progress, loop.get('reason') if loop['action'] == 'stop' else None)
                    ctx.state['_loop_last_progress'] = progress
                    self.repo.save_state(ctx)
                    if loop['action'] == 'loop':
                        if any(step.dependencies for step in steps):
                            invalidated = {loop['returnStepKey']}
                            while True:
                                downstream = {step.key for step in steps if set(step.dependencies) & invalidated}
                                if downstream <= invalidated:
                                    break
                                invalidated.update(downstream)
                            keep = {step.key for step in steps} - invalidated
                        else:
                            return_seq = next(s.sequence for s in steps if s.key == loop['returnStepKey'])
                            keep = {s.key for s in steps if s.sequence < return_seq}
                        ctx.state['steps'] = {k: v for k, v in ctx.state.get('steps', {}).items() if k in keep}
                        for cache in ('_preparedSteps', '_actionResults'):
                            ctx.state[cache] = {k:v for k,v in ctx.state.get(cache, {}).items() if k in keep}
                        refresh_results(ctx)
                        self.repo.save_state(ctx)
                        self._transition(run_id, 'looping')
                        self._transition(run_id, 'running')
                        continue
                    status = loop['status']
                    ctx.output['stopReason'] = loop.get('reason')
                else:
                    status = self.convergence.evaluate(ctx).value
                # Exhausted research rounds and unresolved evidence are quality
                # outcomes, not a request to interrupt an unattended draft.
                # Keep the frozen publication policy and truth status separate.
                if (profile.agent_key in FUTURE_AGENT_KEYS
                        and ctx.input.get('automationMode')
                        in {'unattended-draft', 'expert-draft'}
                        and status == 'requires-human-review'):
                    status = 'completed-with-gaps'
                allowed=as_json(profile.convergence.get('allowed_terminal_states_json'))
                if allowed and status not in allowed:
                    raise ValueError(f'terminal status is not permitted by profile: {status}')
                ctx.output['completionStatus'] = status
                self.validator.output(profile, ctx.output)
                self.repo.transition(run_id, status, output=ctx.output)
                return self._result(ctx, status)
        except HumanApprovalRequired as exc:
            self._transition(run_id, 'waiting-human', error=str(exc))
            result = self._result(ctx, 'waiting-human')
            result['approvalId'] = exc.approval_id
            return result
        except Exception as exc:
            if self._run(run_id)['status'] in {'waiting-human', 'cancelled', 'blocked', 'failed'}:
                return self._result(ctx, self._run(run_id)['status'])
            from .cognitive_identity import AGENT_KEYS as COGNITIVE_KEYS
            from .phase3_repository import BudgetExceededError
            if profile.agent_key in COGNITIVE_KEYS and isinstance(exc, (BudgetExceededError, TimeoutError)):
                from .cognitive_termination import summarize
                reason = 'budget-exhausted' if isinstance(exc, BudgetExceededError) else 'deadline-exceeded'
                # Summarization performs no external calls. Contract, authority,
                # and source failures remain hard failures during finalization.
                try:
                    ctx.output = summarize(ctx, self.repo, reason)
                    self.validator.schema(ctx.output, profile.output_contract.get('schema_json') or {})
                    self.repo.save_state(ctx)
                    self.repo.append_event(run_id, 'cognitive.execution-stopped', detail={
                        'reason': reason, 'goalStatus': 'partial', 'truthVerified': False,
                        'completedNodes': sorted(ctx.state.get('steps', {}))})
                    current_status = self._run(run_id)['status']
                    if current_status in TERMINAL or current_status == 'waiting-human':
                        return self._result(ctx, current_status)
                    if current_status in {'waiting-tool', 'retrying', 'looping'}:
                        self._transition(run_id, 'running')
                    self._transition(run_id, 'validating')
                    self.repo.transition(run_id, 'completed-with-gaps', output=ctx.output)
                    return self._result(ctx, 'completed-with-gaps')
                except Exception as finalization_error:
                    current_status = self._run(run_id)['status']
                    if current_status in TERMINAL or current_status == 'waiting-human':
                        return self._result(ctx, current_status)
                    self._transition(run_id, 'failed', error=str(finalization_error))
                    raise
            self._transition(run_id, 'failed', error=str(exc))
            raise
        finally:
            ctx.state['_runtime_seconds'] = float(ctx.state.get('_runtime_seconds', 0)) + time.monotonic() - ctx.started_at
            self.repo.save_state(ctx)

    def _set_final_output(self, ctx):
        tool_results = {}
        for item in ctx.state.get('steps', {}).values():
            tool_results.update(item.get('toolResults', {}))
        if ctx.profile.agent_key in FUTURE_AGENT_KEYS:
            report = (ctx.state.get('steps', {}).get('compose-judgment-report') or {}).get('judgmentReport')
            if not isinstance(report, dict):
                raise ValueError('future battlefield report executor did not produce a report')
            ctx.output = deepcopy(report)
            ctx.output['toolResults'] = tool_results
            ctx.output['evidence'] = list(ctx.state.get('evidence', []))
            ctx.output['assumptions'] = list(ctx.output.get('assumptions', []))
            ctx.output['unresolvedQuestions'] = list(ctx.input.get('unresolvedQuestions', []))
            ctx.output['capabilityGaps'] = list(ctx.state.get('capabilityGaps', []))
            ctx.output['truthVerified'] = False
            review = ctx.state.get('steps', {}).get('human-review', {})
            for key in ('reviewStatus', 'reviewer', 'publicationStatus', 'publicationReviewRequired'):
                if key in review:
                    ctx.output[key] = review[key]
            return
        # Action Agents use the same fixed-step executor as the application
        # profile, but their output is a composable ActionResult rather than a
        # UAV capability assessment. Keep the result deterministic and expose
        # every tool result so the parent Agent can attach it as evidence.
        if ctx.profile.model_policy.get('config_json', {}).get('agentLayer') == 'action':
            evidence = list(ctx.state.get('evidence', []))
            gaps = list(ctx.state.get('capabilityGaps', []))
            ctx.output = {
                'agentKey': ctx.profile.agent_key,
                'toolResults': tool_results,
                'evidence': evidence,
                'assumptions': list(ctx.input.get('assumptions', [])),
                'confidence': 'medium' if evidence else 'low',
                'completionStatus': 'completed-with-gaps' if gaps else 'completed',
                'findings': list(ctx.state.get('findings', [])),
                'unresolvedQuestions': list(ctx.input.get('unresolvedQuestions', [])),
                'truthVerified': False,
                'reviewStatus': 'pending',
            }
            from .action_reasoning import child_reasoning
            if child_reasoning(ctx.profile):
                ctx.output['actionReasoningVersion'] = 'child-v1'
                ctx.output['gaps'] = gaps
                for item in ctx.state.get('steps', {}).values():
                    for key in ('claims', 'facts', 'perspectiveFindings', 'researchMethodMaterial', 'decisionSpecHash'):
                        if key in item:
                            ctx.output[key] = deepcopy(item[key])
            return
        gaps, evidence = ctx.state.get('capabilityGaps', []), ctx.state.get('evidence', [])
        ctx.output = {
            'evidence': evidence, 'assumptions': list(ctx.input.get('assumptions', [])),
            'unresolvedQuestions': list(ctx.input.get('unresolvedQuestions', [])),
            'confidence': 'medium' if evidence else 'low',
            'completionStatus': 'completed-with-gaps' if gaps or not evidence else 'completed',
            'assessment': {'uavId': ctx.input.get('uavId'), 'requirements': ctx.input.get('requirements', {}),
                           'baseline': tool_results.get('uav.get_capability_baseline', {}),
                           'compliance': tool_results.get('uav.check_requirement_compliance', {})},
            'capabilityGaps': gaps,
            'dataMode': 'fixture' if ctx.input.get('fixtureMode') else ctx.input.get('sourceSnapshot', {}).get('mode', 'provided-input'),
            'sourceSnapshot': deepcopy(ctx.input.get('sourceSnapshot', {})),
            'sourceGaps': list(ctx.input.get('sourceGaps', [])),
        }
        if ctx.output['sourceGaps']:
            ctx.output['confidence'] = 'low'
        if ctx.input.get('fixtureMode'):
            ctx.output['assumptions'].append('X200 fixture data validates runtime behavior; it is not a real aircraft assessment.')

    def _result(self, ctx, status):
        return {'runId': ctx.run_id, 'status': status, 'state': ctx.state,
                'output': ctx.output or self._run(ctx.run_id).get('output_json')}

    def pause(self, run_id, reason='paused by user'):
        self._transition(run_id, 'waiting-human', error=reason)
        self.repo.append_event(run_id, 'run.paused', detail={'reason': reason})
        return self._run(run_id)

    def cancel(self, run_id):
        self._transition(run_id, 'cancelled')
        return self._run(run_id)

    async def resume(self, run_id):
        return await self.execute(run_id)

    def request_approval(self, run_id, action, reason, requested_by, step_key=None):
        self._run(run_id)
        self._transition(run_id, 'waiting-human')
        aid = self.approvals.request(run_id, action, reason, requested_by, step_key)
        return {'approvalId': aid, 'runId': run_id, 'status': 'pending'}

    def decide_approval(self, approval_id, approved, reviewer, comment='', run_id=None):
        if type(approved) is not bool: raise ValueError('approved must be a boolean')
        approval = self.repo.get_approval(approval_id)
        if not approval or (run_id and approval['run_id'] != run_id):
            raise ValueError('approval does not belong to run')
        target_run = self._run(approval['run_id'])
        reviewer_id = reviewer
        # Persist the stable principal identifier in audit rows.  Passing the
        # Principal object through works accidentally for PostgreSQL adapters
        # but produces non-serializable values in lightweight repositories and
        # obscures who made the decision.
        from agent_admin.credentials import Principal
        if isinstance(reviewer, Principal):
            reviewer_id = reviewer.principal_id
        if target_run.get('agent_key') in FUTURE_AGENT_KEYS:
            if not isinstance(reviewer, Principal) or reviewer.kind != 'human' or not reviewer.permits('reviewer'):
                raise PermissionError('Future Battlefield approval requires an authenticated human reviewer')
        rid = self.approvals.decide(approval_id, approved, reviewer_id, comment)
        if not approved and self._run(rid)['status'] == 'waiting-human':
            self._transition(rid, 'requires-human-review')
        # Remain paused until resume; other pending approvals must still block.
        return self._run(rid)

    def _transition(self, run_id, target, error=None):
        self.repo.transition(run_id, target, error=error)
