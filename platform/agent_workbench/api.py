"""Versioned research inputs; every operation rechecks the current credential.

Run admission is integrated separately so saving a workspace never starts a
provider request or queues a billable research run.
"""
from fastapi import APIRouter, Depends, HTTPException, Request, Response

from agent_admin.api import SESSION_COOKIE
from agent_admin.credentials import AdminAuth
from .workspaces import WorkspaceConflict, WorkspaceRepository
from agent_engine.uav_execution_contracts import BaselineNotReady


def create_workspace_router(connection_factory, *, auth=None, repository=None, run_service=None):
    auth = auth or AdminAuth(connection_factory)
    repository = repository or WorkspaceRepository(connection_factory)
    router = APIRouter(prefix='/api/agent-admin/workspaces', tags=['research-workspaces'])

    def access(request: Request, response: Response):
        response.headers['Cache-Control'] = 'no-store'
        if request.method not in {'GET', 'HEAD', 'OPTIONS'}:
            origin = request.headers.get('origin')
            if origin and origin.rstrip('/') != str(request.base_url).rstrip('/'):
                raise HTTPException(403, 'cross-origin workspace operation rejected')
        bearer = request.headers.get('authorization', '')
        token = bearer[7:] if bearer.startswith('Bearer ') else request.cookies.get(SESSION_COOKIE)
        actor = auth.authenticate(token)
        if actor is None:
            raise HTTPException(401, 'authenticated local operator required')
        return actor, token

    def invoke(method, *args, **kwargs):
        try:
            return method(*args, **kwargs)
        except BaselineNotReady as exc:
            raise HTTPException(409, detail=exc.admission_details) from None
        except WorkspaceConflict:
            raise HTTPException(409, 'workspace changed; reload the current revision') from None
        except PermissionError:
            raise HTTPException(403, 'current workspace owner or administrator permission required') from None
        except (ValueError, KeyError, TypeError):
            raise HTTPException(400, 'invalid workspace request') from None
        except Exception:
            raise HTTPException(409, 'workspace operation could not be applied') from None

    @router.get('')
    def listing(limit: int = 50, authenticated=Depends(access)):
        actor, token = authenticated
        return invoke(repository.list, actor, limit=limit, actor_token=token)

    @router.post('', status_code=201)
    def create(body: dict, authenticated=Depends(access)):
        actor, token = authenticated
        return invoke(repository.create, actor, body, actor_token=token)

    @router.get('/{workspace_id}')
    def get(workspace_id: str, revision: int | None = None, authenticated=Depends(access)):
        actor, token = authenticated
        return invoke(repository.get, actor, workspace_id, revision=revision, actor_token=token)

    @router.put('/{workspace_id}')
    def revise(workspace_id: str, body: dict, authenticated=Depends(access)):
        if set(body) != {'expectedRevision', 'content'}:
            raise HTTPException(400, 'expectedRevision and content required')
        actor, token = authenticated
        return invoke(repository.revise, actor, workspace_id, body['expectedRevision'],
                      body['content'], actor_token=token)

    @router.get('/{workspace_id}/revisions')
    def revisions(workspace_id: str, authenticated=Depends(access)):
        actor, token = authenticated
        return invoke(repository.revisions, actor, workspace_id, actor_token=token)

    @router.get('/{workspace_id}/runs')
    def bindings(workspace_id: str, authenticated=Depends(access)):
        actor, token = authenticated
        return invoke(repository.bindings, actor, workspace_id, actor_token=token)

    @router.get('/{workspace_id}/compare')
    def compare(workspace_id: str, leftRunId: str, rightRunId: str, authenticated=Depends(access)):
        actor, token = authenticated
        return invoke(repository.compare, actor, workspace_id, leftRunId, rightRunId, actor_token=token)

    if run_service is not None:
        @router.post('/{workspace_id}/runs', status_code=201)
        def start(workspace_id: str, body: dict, authenticated=Depends(access)):
            if not {'revision', 'idempotencyKey'}.issubset(body) or set(body) - {
                    'revision', 'idempotencyKey', 'options', 'previousRunId', 'reason'}:
                raise HTTPException(400, 'revision and idempotencyKey with optional run settings required')
            actor, token = authenticated
            return invoke(run_service.start, actor, workspace_id, body['revision'], body['idempotencyKey'],
                          options=body.get('options'), previous_run_id=body.get('previousRunId'),
                          reason=body.get('reason'), actor_token=token)

    return router
