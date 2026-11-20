"""Human report-review surface. Opinion writes are never exposed as model tools."""
from fastapi import APIRouter, Depends, HTTPException, Request, Response

from agent_admin.credentials import AdminAuth
from .review_summary import ReviewSummaryService
from .report_review import (ReportReviewService, ReviewValidationError, ReviewConflictError, ReviewUnavailableError)


def create_report_review_router(engine, queue, *, auth=None):
    auth = auth or AdminAuth(engine.base_connection_factory)
    service = ReviewSummaryService(engine, queue)
    records = ReportReviewService(engine.base_connection_factory, engine.repo)
    router = APIRouter(prefix='/api/agent-admin/report-review', tags=['report-review'])

    def access(request: Request, response: Response):
        response.headers['Cache-Control'] = 'no-store'
        origin = request.headers.get('origin')
        if request.method != 'GET' and origin and origin.rstrip('/') != str(request.base_url).rstrip('/'):
            raise HTTPException(403, 'cross-origin report operation rejected')
        if request.headers.get('sec-fetch-site') == 'cross-site':
            raise HTTPException(403, 'cross-site report operation rejected')
        bearer = request.headers.get('authorization', '')
        token = bearer[7:] if bearer.startswith('Bearer ') else request.cookies.get('agent_admin_session')
        actor = auth.authenticate(token)
        if not actor or actor.kind != 'human':
            raise HTTPException(401, 'human operator required')
        return actor

    @router.get('/runs/{run_id}/summary')
    def summary(run_id: str, actor=Depends(access)):
        try:
            return service.read(run_id, actor=actor)
        except LookupError:
            raise HTTPException(404, 'Run not found') from None
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None

    def review_call(method, *args, **kwargs):
        try:
            return method(*args, **kwargs)
        except ReviewValidationError as exc:
            raise HTTPException(400, {'code': 'review-invalid', 'message': str(exc)}) from None
        except (ReviewConflictError, ReviewUnavailableError) as exc:
            raise HTTPException(409, {'code': 'review-conflict', 'message': str(exc)}) from None
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from None
        except LookupError:
            raise HTTPException(404, 'Run not found') from None

    @router.get('/runs/{run_id}/events')
    def review_events(run_id: str, objectKind: str | None = None, objectId: str | None = None, actor=Depends(access)):
        try:
            run, report = service.report(run_id)
            current_summary = service.project(run, report, actor=actor)
        except LookupError:
            raise HTTPException(404, 'Run not found') from None
        return review_call(records.read, run, report, current_summary['identity'], actor,
            object_kind=objectKind, object_id=objectId)

    @router.post('/runs/{run_id}/events', status_code=201)
    def append_review_event(run_id: str, body: dict, actor=Depends(access)):
        try:
            current_summary = service.read(run_id, actor=actor)
        except LookupError:
            raise HTTPException(404, 'Run not found') from None
        return review_call(records.append, run_id, body, actor, identity=current_summary['identity'])

    def current(run_id):
        from .ontology_explorer import explorer_graph
        try:
            _, report = service.report(run_id)
            return report, explorer_graph(report)
        except LookupError:
            raise HTTPException(404, 'Run not found') from None
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None

    def check_projection(body, graph, fields):
        if set(body) - fields:
            raise HTTPException(400, 'unsupported report detail fields')
        if body.get('projectionHash') != graph['contentHash']:
            raise HTTPException(409, {'code': 'projection-changed', 'message': '图谱展示版本已变化，请重新打开当前图谱。', 'projectionHash': graph['contentHash']})

    @router.get('/runs/{run_id}/graph')
    def graph(run_id: str, actor=Depends(access)):
        return current(run_id)[1]

    @router.post('/runs/{run_id}/selection')
    def selection(run_id: str, body: dict, actor=Depends(access)):
        from .ontology_explorer import selection_detail
        report, graph = current(run_id)
        check_projection(body, graph, {'kind', 'id', 'projectionHash'})
        if body.get('kind') not in {'node', 'edge'} or not isinstance(body.get('id'), str):
            raise HTTPException(400, 'invalid graph selection')
        try:
            return selection_detail(report, body['kind'], body['id'])
        except KeyError:
            raise HTTPException(404, '所选对象不属于当前运行图谱。') from None

    @router.post('/runs/{run_id}/source')
    def source(run_id: str, body: dict, actor=Depends(access)):
        from .ontology_explorer import source_excerpt
        report, graph = current(run_id)
        check_projection(body, graph, {'evidenceId', 'projectionHash'})
        if not isinstance(body.get('evidenceId'), str) or not 1 <= len(body['evidenceId']) <= 1024:
            raise HTTPException(400, 'invalid evidence reference')
        try:
            return source_excerpt(report, body['evidenceId'], engine.repo)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from None
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None

    return router
