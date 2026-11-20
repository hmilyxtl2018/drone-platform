"""Admin HTTP API. Only server-authenticated human identities can control runs."""
from __future__ import annotations

from dataclasses import asdict
import re

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from agent_engine.worker import control_context
from .credentials import AdminAuth
from .repository import AdminRepository
from agent_engine.uav_execution_contracts import BaselineNotReady


SESSION_COOKIE = "agent_admin_session"


def create_admin_router(engine, queue, auth=None, review_service=None):
    factory = getattr(engine, "base_connection_factory", queue.connection_factory)
    auth = auth or AdminAuth(factory)
    repository = AdminRepository(engine.repo.connection_factory)
    from agent_engine.followup import FollowupService
    followups = FollowupService(engine, queue)
    if review_service is None:
        from agent_engine.review import ReviewService
        review_service = ReviewService(repository)
    def reject_cross_origin(request: Request):
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            same_origin(request)
    router = APIRouter(prefix="/api/agent-admin", tags=["agent-admin"], dependencies=[Depends(reject_cross_origin)])

    def principal(request: Request):
        authorization = request.headers.get("authorization", "")
        token = authorization[7:] if authorization.startswith("Bearer ") else request.cookies.get(SESSION_COOKIE)
        actor = auth.authenticate(token)
        if actor is None:
            raise HTTPException(401, "authenticated local operator required", headers={"WWW-Authenticate": "Bearer"})
        return actor

    def require(role):
        def check(actor=Depends(principal)):
            if not actor.permits(role):
                raise HTTPException(403, role + " role required")
            return actor
        return check

    def require_run(run_id):
        row = engine.repo.get_run(run_id)
        if not row:
            raise HTTPException(404, "run not found")
        return row

    def safe_failure(exc):
        from agent_engine.connectors.runtime import ConnectorAdmissionError
        if isinstance(exc, ConnectorAdmissionError):
            status = 409 if exc.code == 'connector-policy-version-conflict' else 429 if exc.retry_after else 403
            headers = {'Retry-After': str(exc.retry_after)} if exc.retry_after else None
            raise HTTPException(status, {'code': exc.code}, headers=headers) from None
        if isinstance(exc, BaselineNotReady):
            raise HTTPException(409, detail=exc.admission_details) from None
        from agent_tools.ontology_catalog import SelectionChanged
        if isinstance(exc, SelectionChanged):
            raise HTTPException(409, str(exc)) from None
        if isinstance(exc, HTTPException):
            raise exc
        if isinstance(exc, PermissionError):
            raise HTTPException(403, str(exc)) from None
        if isinstance(exc, (ValueError, KeyError)):
            raise HTTPException(400, str(exc)) from None
        # PostgreSQL details can expose configuration/source contents.
        raise HTTPException(409, "operation could not be applied; inspect server verification records") from None

    def same_origin(request):
        origin = request.headers.get("origin")
        if origin and origin.rstrip("/") != str(request.base_url).rstrip("/"):
            raise HTTPException(403, "cross-origin session request rejected")

    @router.post("/session")
    def start_session(request: Request, response: Response):
        same_origin(request)
        authorization = request.headers.get("authorization", "")
        if not authorization.startswith("Bearer "):
            raise HTTPException(401, "server-issued Bearer token required")
        actor = auth.authenticate(authorization[7:])
        if actor is None:
            raise HTTPException(401, "invalid or expired token")
        if request.scope.get("agent_admin_auth_mode") == "local":
            response.headers["Cache-Control"] = "no-store"
            return {**asdict(actor), "auth_mode": "local"}
        response.set_cookie(SESSION_COOKIE, authorization[7:], httponly=True, secure=request.url.scheme == "https",
                            samesite="strict", path="/api/agent-admin", max_age=8 * 3600)
        response.headers["Cache-Control"] = "no-store"
        return asdict(actor)

    @router.delete("/session")
    def end_session(request: Request, response: Response):
        same_origin(request)
        response.delete_cookie(SESSION_COOKIE, path="/api/agent-admin")
        return {"status": "signed-out"}

    @router.get("/me")
    def me(request: Request, actor=Depends(principal)):
        result = asdict(actor)
        if request.scope.get("agent_admin_auth_mode") == "local":
            result["auth_mode"] = "local"
        return result

    @router.get("/config/{resource}")
    def config(resource: str, actor=Depends(principal)):
        try: return repository.list_config(resource)
        except Exception as exc: safe_failure(exc)

    @router.get('/connectors')
    def connectors(actor=Depends(principal)):
        from agent_engine.connectors.catalog import ConnectorCatalog
        try:
            return {'schemaVersion': 'connector-catalog.v1', 'items': ConnectorCatalog(factory).list()}
        except Exception as exc:
            safe_failure(exc)

    @router.get('/connectors/{connector_ref}')
    def connector_detail(connector_ref: str, actor=Depends(principal)):
        from agent_engine.connectors.catalog import ConnectorCatalog
        from agent_engine.connectors.runtime import ConnectorRuntime
        try:
            item = ConnectorCatalog(factory).get(connector_ref)
            if item is None:
                raise HTTPException(404, 'connector not found')
            item['policy'] = ConnectorRuntime(factory).get_policy(connector_ref)
            return item
        except Exception as exc:
            safe_failure(exc)

    @router.get('/connectors/{connector_ref}/policy')
    def connector_policy(connector_ref: str, actor=Depends(principal)):
        from agent_engine.connectors.runtime import ConnectorRuntime
        from agent_engine.connectors.catalog import ConnectorCatalog
        try:
            if ConnectorCatalog(factory).get(connector_ref) is None:
                raise HTTPException(404, 'connector not found')
            return ConnectorRuntime(factory).get_policy(connector_ref)
        except Exception as exc:
            safe_failure(exc)

    @router.put('/connectors/{connector_ref}/policy')
    def update_connector_policy(connector_ref: str, body: dict, actor=Depends(require('admin'))):
        from agent_engine.connectors.runtime import ConnectorRuntime
        from agent_engine.connectors.catalog import ConnectorCatalog
        try:
            if type(body.get('expectedPolicyVersion')) is not int or body['expectedPolicyVersion'] < 0:
                raise ValueError('expectedPolicyVersion is required')
            if not isinstance(body.get('reason'), str) or not 1 <= len(body['reason'].strip()) <= 1000:
                raise ValueError('a nonempty policy change reason is required')
            if ConnectorCatalog(factory).get(connector_ref) is None:
                raise HTTPException(404, 'connector not found')
            return ConnectorRuntime(factory).set_policy(connector_ref, body, actor.principal_id)
        except Exception as exc:
            safe_failure(exc)

    @router.post('/connector-source-events')
    def source_change_event(body: dict, actor=Depends(require('admin'))):
        from agent_engine.connectors.events import SourceEventService
        if set(body) != {'beforeArtifactId', 'afterArtifactId'}:
            raise HTTPException(400, 'two stored source artifacts are required')
        try:
            return SourceEventService(factory).record_change(
                body['beforeArtifactId'], body['afterArtifactId'], actor.principal_id)
        except Exception as exc:
            safe_failure(exc)

    @router.post("/config/{resource}/{key}")
    def create_config(resource: str, key: str, body: dict, actor=Depends(require("admin"))):
        try: return repository.write_config(resource, key, body, actor, create=True)
        except Exception as exc: safe_failure(exc)

    @router.put("/config/{resource}/{key}")
    def update_config(resource: str, key: str, body: dict, actor=Depends(require("admin"))):
        try: return repository.write_config(resource, key, body, actor)
        except Exception as exc: safe_failure(exc)

    @router.delete("/config/{resource}/{key}")
    def disable_config(resource: str, key: str, actor=Depends(require("admin"))):
        try: return repository.disable_config(resource, key, actor)
        except Exception as exc: safe_failure(exc)

    # Compact aliases consumed by the local console.
    for resource in ("agents", "tools", "providers", "sources"):
        def route(resource_name):
            def listing(actor=Depends(principal)):
                return repository.list_config(resource_name)
            return listing
        router.add_api_route("/" + resource, route(resource), methods=["GET"])

    @router.get("/ontology/objects")
    def ontology_objects(agentKey: str, objectClass: str = "UnmannedAircraft", category: str = "",
                         q: str = "", offset: int = 0, limit: int = 30, actor=Depends(principal)):
        try:
            from agent_tools.ontology_catalog import catalog
            return catalog(engine.loader.load(agentKey), object_class=objectClass, category=category,
                           query=q, offset=offset, limit=limit)
        except Exception as exc: safe_failure(exc)

    @router.get('/agents/{agent_key}/execution-readiness')
    def execution_readiness(agent_key: str, actor=Depends(principal)):
        from agent_engine.uav_execution_contracts import readiness
        try:
            from agent_engine.cognitive_identity import AGENT_KEYS as COGNITIVE_KEYS
            if agent_key in COGNITIVE_KEYS:
                from agent_engine.cognitive_admission import readiness as cognitive_readiness
                return cognitive_readiness(engine, agent_key)
            return readiness(engine.repo, agent_key)
        except LookupError:
            raise HTTPException(404, 'role readiness not found') from None
        except Exception as exc:
            safe_failure(exc)

    @router.get('/cognitive-suite/context')
    def cognitive_context(agentKey: str = 'ontology-cognitive-analyst', actor=Depends(principal)):
        from agent_engine.cognitive_admission import CognitiveAdmissionService
        try:
            return CognitiveAdmissionService(engine).contexts(agentKey)
        except Exception as exc:
            safe_failure(exc)

    @router.get('/cognitive-suite/design')
    def cognitive_design(actor=Depends(principal)):
        from agent_engine.cognitive_profiles import design
        return design()

    @router.post('/cognitive-suite/admission')
    def cognitive_admission(body: dict, actor=Depends(require('operator'))):
        from agent_engine.cognitive_admission import CognitiveAdmissionService
        try:
            return CognitiveAdmissionService(engine).admit(body, actor.principal_id)
        except Exception as exc:
            safe_failure(exc)

    @router.get('/agents/{agent_key}/execution-contract')
    def execution_contract(agent_key: str, actor=Depends(principal)):
        from agent_engine.uav_execution_contracts import role_contract
        try:
            return {'status': 'target-contract', 'contract': role_contract(agent_key)}
        except KeyError:
            raise HTTPException(404, 'role contract not found') from None

    @router.get('/agents/{agent_key}/goal-contract')
    def goal_contract(agent_key: str, actor=Depends(principal)):
        from agent_engine.goal_contract import GoalService
        try: return GoalService(factory).contract(agent_key)
        except Exception as exc: safe_failure(exc)

    @router.get('/agents/{agent_key}/skills')
    def agent_skills(agent_key: str, actor=Depends(principal)):
        from agent_engine.skill_views import configuration
        try: return configuration(engine, agent_key)
        except Exception as exc: safe_failure(exc)

    @router.get('/runs/{run_id}/skills')
    def run_skills(run_id: str, actor=Depends(principal)):
        require_run(run_id)
        from agent_engine.skill_views import run_view
        return run_view(engine, run_id)

    @router.get('/runs/{run_id}/skills/{invocation_id}')
    def skill_invocation(run_id: str, invocation_id: str, actor=Depends(principal)):
        require_run(run_id)
        from agent_engine.skill_views import invocation_detail
        try: return invocation_detail(engine, run_id, invocation_id)
        except LookupError: raise HTTPException(404, 'Skill invocation not found in this Run') from None

    @router.get('/runs/{run_id}/skill-trials')
    def skill_trials(run_id: str, actor=Depends(principal)):
        require_run(run_id)
        from agent_engine.skill_trials import SkillTrialService
        return SkillTrialService(engine,queue).list(run_id)

    @router.post('/runs/{run_id}/skill-trials', status_code=202)
    def create_skill_trial(run_id: str, body: dict, actor=Depends(require('operator'))):
        require_run(run_id)
        from agent_engine.skill_trials import SkillTrialService
        try: return SkillTrialService(engine,queue).create(run_id,body,actor)
        except Exception as exc: safe_failure(exc)

    @router.post('/runs/{run_id}/skill-trials/{trial_id}/evaluate')
    def evaluate_skill_trial(run_id: str, trial_id: str, actor=Depends(require('operator'))):
        require_run(run_id)
        from agent_engine.skill_trials import SkillTrialService
        try: return SkillTrialService(engine,queue).evaluate(run_id,trial_id)
        except Exception as exc: safe_failure(exc)

    @router.post('/runs/{run_id}/skills/{invocation_id}/expert-label')
    def skill_expert_label(run_id: str, invocation_id: str, body: dict, actor=Depends(require('reviewer'))):
        require_run(run_id)
        from agent_engine.skill_trials import SkillTrialService
        try: return SkillTrialService(engine,queue).label(run_id,invocation_id,body,actor)
        except Exception as exc: safe_failure(exc)

    @router.post('/runs/{run_id}/skill-trials/{trial_id}/release-draft')
    def skill_release(run_id: str, trial_id: str, body: dict, actor=Depends(require('admin'))):
        require_run(run_id)
        from agent_engine.skill_release import SkillReleaseService
        try: return SkillReleaseService(engine).draft(run_id,trial_id,body,actor)
        except Exception as exc: safe_failure(exc)

    @router.post('/runs/{run_id}/skill-releases/{publication_id}/rollback-draft')
    def skill_rollback(run_id: str, publication_id: str, body: dict, actor=Depends(require('admin'))):
        require_run(run_id)
        from agent_engine.skill_release import SkillReleaseService
        try: return SkillReleaseService(engine).rollback(run_id,publication_id,body,actor)
        except Exception as exc: safe_failure(exc)

    @router.post('/agents/{agent_key}/goal-contract')
    def publish_goal_contract(agent_key: str, body: dict, actor=Depends(require('admin'))):
        from agent_engine.goal_contract import GoalService
        try: return GoalService(factory).publish(agent_key, body, actor)
        except Exception as exc: safe_failure(exc)

    @router.get('/runs/{run_id}/goal')
    def run_goal(run_id: str, actor=Depends(principal)):
        require_run(run_id)
        from agent_engine.goal_contract import GoalService
        try: return GoalService(factory).run(run_id)
        except Exception as exc: safe_failure(exc)

    @router.post('/runs/{run_id}/goal/reassess')
    def reassess_goal(run_id: str, body: dict, actor=Depends(require('operator'))):
        require_run(run_id)
        from agent_engine.goal_contract import GoalService
        try: return GoalService(factory).reassess(run_id, body, actor)
        except Exception as exc: safe_failure(exc)

    @router.get("/ontology/objects/{entity_id}/preflight")
    def object_preflight(entity_id: str, agentKey: str, metrics: str = "enduranceHours,payloadKg", actor=Depends(principal)):
        try:
            from agent_tools.ontology_catalog import preflight
            return preflight(engine.loader.load(agentKey), entity_id, metrics.split(","))
        except Exception as exc: safe_failure(exc)

    @router.get("/runs")
    def runs(limit: int = 50, agentKey: str | None = None, actor=Depends(principal)):
        try: return repository.list_runs(limit, agent_key=agentKey) if agentKey is not None else repository.list_runs(limit)
        except Exception as exc: safe_failure(exc)

    @router.post("/runs", status_code=202)
    def create_run(body: dict, actor=Depends(require("operator"))):
        if set(body) - {"agentKey", "agentVersion", "input"} or not isinstance(body.get("input", {}), dict):
            raise HTTPException(400, "invalid run fields")
        reserved = {"upstreamArtifacts", "parentRunId", "rootRunId", "handoffId", "upstreamAgentKey", "previousRunId", "trustedReviewStates", "reviewer",
                    "goalContract", "localGoal", "parentGoalId", "goalAssessment",
                    "workspaceId", "workspaceRevision", "workspaceRevisionHash", "workspaceMaterials", "selectedObject", "selectionReason"}
        if reserved.intersection(body.get("input", {})):
            raise HTTPException(400, "run ancestry and trusted review fields are server-controlled")
        try:
            from agent_engine.task_intent import admission_actor
            from agent_engine.run_origin import origin_context
            with admission_actor(actor), origin_context(actor, source_kind='admin'):
                run_id = engine.create_run(body["agentKey"], body.get("input", {}), body.get("agentVersion"))
            with control_context(run_id):
                engine.repo.append_event(run_id, "admin.run-created", detail={"principalId": actor.principal_id})
                queue.enqueue(run_id)
            return {"runId": run_id, "status": "queued"}
        except Exception as exc: safe_failure(exc)

    @router.get("/runs/{run_id}")
    def run(run_id: str, actor=Depends(principal)):
        return {"run": require_run(run_id), "queue": queue.get(run_id)}

    @router.get("/runs/{run_id}/graph")
    def execution_graph(run_id: str, actor=Depends(principal)):
        run_record = require_run(run_id)
        try:
            from .execution_graph import build_execution_graph
            model_rows = getattr(engine.repo, 'list_model_call_summaries', engine.repo.list_model_calls)(run_id)
            annotate = getattr(engine.repo, 'annotate_connector_calls', lambda rows, kind: rows)
            return build_execution_graph(
                run_record, engine.repo.load_profile_snapshot(run_id),
                plans=engine.repo.list_plans(run_id),
                steps=getattr(engine.repo, 'list_step_summaries', engine.repo.list_steps)(run_id),
                model_calls=annotate(model_rows, 'model'),
                tool_calls=getattr(engine.repo, 'list_tool_call_summaries', engine.repo.list_tool_calls)(run_id),
                approvals=engine.repo.list_approvals(run_id),
                artifacts=getattr(engine.repo, 'list_artifact_summaries', engine.repo.list_artifacts)(run_id),
                events=engine.repo.list_events(run_id),
                actions=getattr(engine.repo, 'list_execution_delegations', lambda _run_id: [])(run_id),
                skill_invocations=engine.repo.list_skill_invocations(run_id),
                contexts=getattr(engine.repo, 'list_contexts', lambda _run_id: [])(run_id))
        except Exception as exc:
            safe_failure(exc)

    @router.get('/runs/{run_id}/followups')
    def followup_links(run_id: str, actor=Depends(principal)):
        require_run(run_id)
        return followups.links(run_id)

    @router.post('/runs/{run_id}/followups', status_code=202)
    def create_followup(run_id: str, body: dict, actor=Depends(require('operator'))):
        if set(body) - {'idempotencyKey', 'reason', 'input'}:
            raise HTTPException(400, 'invalid follow-up request')
        try:
            return followups.create(run_id, actor, idempotency_key=body['idempotencyKey'],
                                    reason=body['reason'], changes=body.get('input', {}))
        except Exception as exc: safe_failure(exc)

    @router.get("/runs/{run_id}/events")
    def events(run_id: str, after: int = 0, limit: int = 200, actor=Depends(principal)):
        require_run(run_id)
        try: return repository.events_after(run_id, after, limit)
        except Exception as exc: safe_failure(exc)

    @router.get("/runs/{run_id}/plans")
    def plans(run_id: str, actor=Depends(principal)):
        require_run(run_id)
        return engine.repo.list_plans(run_id)

    @router.get("/runs/{run_id}/budget")
    def budget(run_id: str, actor=Depends(principal)):
        require_run(run_id)
        try: return engine.repo.get_budget(run_id)
        except ValueError: return {"runId": run_id, "status": "not-initialized"}

    @router.get("/runs/{run_id}/evidence")
    def evidence(run_id: str, actor=Depends(principal)):
        require_run(run_id)
        try:
            result = repository.evidence_bundle(run_id)
            if hasattr(review_service, "conflicts"):
                result["conflicts"] = review_service.conflicts(run_id)
            result["reviews"] = review_service.list_reviews(run_id)
            result["trustedReviewStates"] = review_service.trusted_review_states(run_id)
            return result
        except Exception as exc: safe_failure(exc)

    @router.get("/runs/{run_id}/model-calls")
    def model_calls(run_id: str, summary: bool = False, actor=Depends(principal)):
        require_run(run_id)
        rows = engine.repo.list_model_call_summaries(run_id) if summary else engine.repo.list_model_calls(run_id)
        return getattr(engine.repo, 'annotate_connector_calls', lambda values, kind: values)(rows, 'model')

    @router.get("/runs/{run_id}/model-calls/{call_id}")
    def model_call(run_id: str, call_id: str, actor=Depends(principal)):
        require_run(run_id)
        value = getattr(engine.repo, 'get_model_call', lambda _run_id, _call_id: None)(run_id, call_id)
        if not value: raise HTTPException(404, "model call not found")
        return getattr(engine.repo, 'annotate_connector_calls', lambda values, kind: values)([value], 'model')[0]

    @router.get("/runs/{run_id}/contexts")
    def contexts(run_id: str, actor=Depends(principal)):
        require_run(run_id)
        return getattr(engine.repo, 'list_contexts', lambda _run_id: [])(run_id)

    @router.get("/runs/{run_id}/contexts/{context_id}")
    def context(run_id: str, context_id: str, actor=Depends(principal)):
        require_run(run_id)
        value = getattr(engine.repo, 'get_context', lambda _context_id: None)(context_id)
        if not value or value.get("run_id") != run_id:
            raise HTTPException(404, "context not found")
        return value

    @router.get("/runs/{run_id}/contexts/{context_id}/dependencies")
    def context_dependencies(run_id: str, context_id: str, actor=Depends(principal)):
        require_run(run_id)
        value = getattr(engine.repo, 'get_context', lambda _context_id: None)(context_id)
        if not value or value.get("run_id") != run_id:
            raise HTTPException(404, "context not found")
        return value.get("dependencies", [])

    @router.get("/runs/{run_id}/collaboration")
    def collaboration(run_id: str, actor=Depends(principal)):
        require_run(run_id)
        return getattr(engine.repo, 'collaboration', lambda _run_id: {
            'rootRunId': _run_id, 'revision': None, 'state': None, 'proposals': []})(run_id)

    @router.post("/runs/{run_id}/collaboration/proposals")
    def collaboration_proposal(run_id: str, body: dict, actor=Depends(require("operator"))):
        run = require_run(run_id)
        allowed = {'operationKey', 'expectedRevision', 'roleKey', 'patch'}
        if (set(body) - allowed or not isinstance(body.get('patch'), dict)
                or not isinstance(body.get('operationKey'), str)
                or not body['operationKey'].strip()
                or type(body.get('expectedRevision')) is not int
                or body['expectedRevision'] < 0):
            raise HTTPException(400, "invalid collaboration proposal")
        try:
            if body.get('roleKey') and body['roleKey'] != run.get('agent_key'):
                raise ValueError('roleKey must match the run Agent')
            result = engine.repo.propose_collaboration(
                run_id, role_key=body.get('roleKey') or run.get('agent_key'),
                operation_key=body['operationKey'], expected_revision=body['expectedRevision'],
                patch=body['patch'])
            if result.get('decision') == 'conflict':
                raise HTTPException(409, detail=result)
            return result
        except Exception as exc:
            safe_failure(exc)

    @router.get("/runs/{run_id}/tool-calls")
    def tool_calls(run_id: str, summary: bool = False, actor=Depends(principal)):
        require_run(run_id)
        return getattr(engine.repo, 'list_tool_call_summaries', engine.repo.list_tool_calls)(run_id) if summary else engine.repo.list_tool_calls(run_id)

    @router.get("/runs/{run_id}/tool-calls/{call_id}")
    def tool_call(run_id: str, call_id: str, actor=Depends(principal)):
        require_run(run_id)
        value = getattr(engine.repo, 'get_tool_call', lambda _run_id, _call_id: None)(run_id, call_id)
        if not value: raise HTTPException(404, "tool call not found")
        return value

    @router.get("/runs/{run_id}/steps")
    def steps(run_id: str, summary: bool = False, actor=Depends(principal)):
        require_run(run_id)
        return getattr(engine.repo, 'list_step_summaries', engine.repo.list_steps)(run_id) if summary else engine.repo.list_steps(run_id)

    @router.get("/runs/{run_id}/steps/{step_run_id}")
    def step(run_id: str, step_run_id: str, actor=Depends(principal)):
        require_run(run_id)
        value = getattr(engine.repo, 'get_step', lambda _run_id, _step_id: None)(run_id, step_run_id)
        if not value:
            raise HTTPException(404, "step not found")
        return value

    @router.get("/runs/{run_id}/artifacts/{artifact_id}")
    def artifact(run_id: str, artifact_id: str, actor=Depends(principal)):
        require_run(run_id)
        value = getattr(engine.repo, 'get_artifact', lambda _run_id, _artifact_id: None)(run_id, artifact_id)
        if not value: raise HTTPException(404, "artifact not found")
        return value

    @router.get("/runs/{run_id}/approvals")
    def approvals(run_id: str, actor=Depends(principal)):
        require_run(run_id)
        return engine.repo.list_approvals(run_id)

    @router.post("/runs/{run_id}/approvals/{approval_id}")
    def decide_approval(run_id: str, approval_id: str, body: dict, actor=Depends(require("reviewer"))):
        run = require_run(run_id)
        if set(body) - {"approved", "comment"} or type(body.get("approved")) is not bool:
            raise HTTPException(400, "approval requires a boolean; reviewer is server-controlled")
        comment = body.get("comment", "")
        if not isinstance(comment, str) or len(comment) > 4000:
            raise HTTPException(400, "invalid approval comment")
        try:
            with control_context(run_id):
                reviewer = actor if run.get("agent_key") == "future-battlefield-analyst" else actor.principal_id
                return engine.decide_approval(approval_id, body["approved"], reviewer, comment, run_id=run_id)
        except Exception as exc: safe_failure(exc)

    @router.get("/runs/{run_id}/artifacts")
    def artifacts(run_id: str, summary: bool = False, actor=Depends(principal)):
        require_run(run_id)
        return getattr(engine.repo, 'list_artifact_summaries', engine.repo.list_artifacts)(run_id) if summary else engine.repo.list_artifacts(run_id)

    @router.get("/runs/{run_id}/reviews")
    def reviews(run_id: str, actor=Depends(principal)):
        require_run(run_id)
        return review_service.list_reviews(run_id)

    @router.post("/runs/{run_id}/reviews")
    def decide_review(run_id: str, body: dict, actor=Depends(require("reviewer"))):
        require_run(run_id)
        if set(body) - {"subjectType", "subjectId", "expectedHash", "decision", "reason", "conflictId"}:
            raise HTTPException(400, "reviewer identity and trust flags are server-controlled")
        try:
            return review_service.decide(run_id, actor, subject_type=body["subjectType"], subject_id=body["subjectId"],
                expected_hash=body["expectedHash"], decision=body["decision"], reason=body["reason"], conflict_id=body.get("conflictId"))
        except Exception as exc: safe_failure(exc)

    @router.post("/runs/{run_id}/pause")
    def pause(run_id: str, actor=Depends(require("operator"))):
        require_run(run_id)
        try:
            queue.request_pause(run_id)
            with control_context(run_id):
                return engine.pause(run_id, "paused by " + actor.principal_id)
        except Exception as exc: safe_failure(exc)

    @router.post("/runs/{run_id}/cancel")
    def cancel(run_id: str, actor=Depends(require("operator"))):
        require_run(run_id)
        try:
            queue.request_cancel(run_id)
            with control_context(run_id):
                return engine.cancel(run_id)
        except Exception as exc: safe_failure(exc)

    @router.post("/runs/{run_id}/resume", status_code=202)
    def resume(run_id: str, actor=Depends(require("operator"))):
        value = require_run(run_id)
        try:
            if value["status"] in {"completed", "completed-with-gaps", "requires-human-review", "blocked", "cancelled"}:
                raise ValueError("terminal runs require a linked follow-up run")
            if any(a["status"] == "pending" for a in engine.repo.list_approvals(run_id)):
                raise ValueError("pending approvals prevent resume")
            with control_context(run_id):
                if value["status"] == "failed":
                    engine.repo.transition(run_id, "queued")
                queue.resume(run_id)
            return {"runId": run_id, "status": "queued"}
        except Exception as exc: safe_failure(exc)

    return router


__all__ = ["create_admin_router", "SESSION_COOKIE"]
