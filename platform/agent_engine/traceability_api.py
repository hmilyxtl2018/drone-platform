"""HTTP bindings for the P4-08 traceability service."""
from fastapi import APIRouter, Depends, HTTPException, Request, Response

from agent_admin.api import SESSION_COOKIE
from agent_admin.credentials import AdminAuth
from .traceability import TraceabilityService, TraceabilityValidationError, TraceabilityConflictError


def create_traceability_router(connection_factory, *, auth=None):
    auth = auth or AdminAuth(connection_factory)
    service = TraceabilityService(connection_factory)
    router = APIRouter(prefix="/api/agent-admin/runs", tags=["traceability"])

    def access(request: Request, response: Response):
        response.headers["Cache-Control"] = "no-store"
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            if origin and origin.rstrip("/") != str(request.base_url).rstrip("/"):
                raise HTTPException(403, "cross-origin traceability operation rejected")
        bearer = request.headers.get("authorization", "")
        token = bearer[7:] if bearer.startswith("Bearer ") else request.cookies.get(SESSION_COOKIE)
        actor = auth.authenticate(token)
        if actor is None:
            raise HTTPException(401, "authenticated local operator required")
        return actor, token

    def invoke(method, *args, **kwargs):
        try:
            return method(*args, **kwargs)
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from None
        except (TraceabilityValidationError, TraceabilityConflictError, ValueError, KeyError) as exc:
            raise HTTPException(400, str(exc)) from None
        except Exception:
            raise HTTPException(409, "traceability operation could not be applied") from None

    @router.post("/{run_id}/traceability", status_code=201)
    def create(run_id: str, body: dict, authenticated=Depends(access)):
        actor, token = authenticated
        if not actor.permits("operator") and not actor.permits("admin"):
            raise HTTPException(403, "operator role required")
        allowed = {"matrix", "workspaceId", "workspaceRevision", "workspaceRevisionHash"}
        if set(body) - allowed or not isinstance(body.get("matrix"), dict):
            raise HTTPException(400, "matrix and optional workspace binding required")
        return invoke(service.create, actor, run_id, body["matrix"], actor_token=token,
                      workspace_id=body.get("workspaceId"), workspace_revision=body.get("workspaceRevision"),
                      workspace_revision_hash=body.get("workspaceRevisionHash"))

    @router.get("/{run_id}/traceability")
    def listing(run_id: str, authenticated=Depends(access)):
        actor, token = authenticated
        return invoke(service.list, actor, run_id, actor_token=token)

    @router.get("/{run_id}/traceability/{matrix_id}")
    def get(run_id: str, matrix_id: str, authenticated=Depends(access)):
        actor, token = authenticated
        result = invoke(service.get, actor, matrix_id, actor_token=token)
        if result["runId"] != run_id:
            raise HTTPException(404, "traceability matrix not found")
        return result

    return router

