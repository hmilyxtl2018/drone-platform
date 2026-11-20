"""Read-only HTTP queries for authenticated human review workbenches."""
from fastapi import APIRouter, Depends, HTTPException, Request, Response

from agent_admin.api import SESSION_COOKIE
from agent_admin.credentials import AdminAuth
from agent_engine.evidence_repository import EvidenceRepository
from .review_workbench import ReviewWorkbenchService


def create_review_workbench_router(connection_factory, *, auth=None, service=None):
    auth = auth or AdminAuth(connection_factory)
    service = service or ReviewWorkbenchService(EvidenceRepository(connection_factory))
    router = APIRouter(prefix="/api/agent-admin/review-workbench", tags=["review-workbench"])

    def access(request: Request, response: Response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        bearer = request.headers.get("authorization", "")
        token = bearer[7:] if bearer.startswith("Bearer ") else request.cookies.get(SESSION_COOKIE)
        actor = auth.authenticate(token)
        if actor is None:
            raise HTTPException(401, "authenticated human reviewer required")
        if not actor.permits("reviewer"):
            raise HTTPException(403, "reviewer or administrator permission required")
        return actor, token

    def invoke(method, actor, token, *args):
        try:
            return method(actor, *args, actor_token=token)
        except PermissionError:
            raise HTTPException(403, "current human reviewer permission required") from None
        except (ValueError, KeyError, TypeError):
            raise HTTPException(400, "invalid review material or immutable snapshot") from None
        except Exception:
            raise HTTPException(409, "review material could not be read consistently") from None

    @router.get("/runs/{run_id}")
    def overview(run_id: str, authenticated=Depends(access)):
        actor, token = authenticated
        return invoke(service.overview, actor, token, run_id)

    @router.get("/runs/{run_id}/sources/{source_id}")
    def source(run_id: str, source_id: str, authenticated=Depends(access)):
        actor, token = authenticated
        return invoke(service.source, actor, token, run_id, source_id)

    @router.get("/runs/{run_id}/subjects/{subject_type}/{subject_id}")
    def subject(run_id: str, subject_type: str, subject_id: str, authenticated=Depends(access)):
        actor, token = authenticated
        return invoke(service.subject, actor, token, run_id, subject_type, subject_id)

    return router
