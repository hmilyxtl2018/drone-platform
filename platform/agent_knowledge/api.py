"""Human-authenticated knowledge proposals and explicit publication operations.

Governance lives in KnowledgePublicationService. The API supplies only the
server-authenticated Principal and the presented credential, which the service
rechecks inside its database transaction before reading or writing anything.
"""
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request, Response

from agent_admin.api import SESSION_COOKIE
from agent_admin.credentials import AdminAuth, Principal
from .knowledge_publication import (
    KnowledgeConflictError, KnowledgeNotFoundError, KnowledgePublicationService,
    KnowledgeValidationError,
)


def create_knowledge_router(connection_factory, *, auth=None, service=None):
    auth = auth or AdminAuth(connection_factory)
    service = service or KnowledgePublicationService(connection_factory)
    router = APIRouter(prefix="/api/agent-admin/knowledge", tags=["knowledge-publication"])

    def access(request: Request, response: Response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            if ((origin and origin.rstrip("/") != str(request.base_url).rstrip("/"))
                    or request.headers.get("sec-fetch-site") == "cross-site"):
                raise HTTPException(403, "cross-origin knowledge operation rejected")
        authorization = request.headers.get("authorization", "")
        token = authorization[7:] if authorization.startswith("Bearer ") else request.cookies.get(SESSION_COOKIE)
        try:
            actor = auth.authenticate(token)
        except Exception:
            raise HTTPException(503, "knowledge authentication is temporarily unavailable") from None
        if not isinstance(actor, Principal) or actor.kind != "human":
            raise HTTPException(401, "authenticated human reviewer required", headers={"WWW-Authenticate": "Bearer"})
        if not actor.permits("reviewer"):
            raise HTTPException(403, "reviewer or administrator permission required")
        return actor, token

    def invoke(method, identity, *args, **kwargs):
        actor, token = identity
        try:
            return method(actor, *args, actor_token=token, **kwargs)
        except KnowledgeNotFoundError:
            raise HTTPException(404, "knowledge change set or publication not found") from None
        except KnowledgeConflictError:
            raise HTTPException(409, "knowledge operation is blocked; reload current reviews, evidence and versions") from None
        except PermissionError:
            raise HTTPException(403, "current human reviewer permission and independent decision required") from None
        except (KnowledgeValidationError, ValueError, TypeError, KeyError):
            raise HTTPException(400, "invalid knowledge operation fields") from None
        except Exception:
            # SQL, source material and credential details must never be echoed.
            raise HTTPException(409, "knowledge operation could not be applied") from None

    def fields(body, required, optional=()):
        if (not isinstance(body, dict) or not set(required).issubset(body)
                or set(body) - set(required) - set(optional)):
            raise HTTPException(400, "invalid knowledge operation fields; identity and review state are server-controlled")

    @router.get("/change-sets")
    def change_sets(streamKey: str, limit: int = 100, identity=Depends(access)):
        return invoke(service.list_change_sets, identity, streamKey, limit=limit)

    @router.get("/change-sets/{change_set_id}")
    def change_set(change_set_id: str, identity=Depends(access)):
        return invoke(service.get_change_set, identity, change_set_id)

    @router.post("/runs/{run_id}/change-sets", status_code=201)
    def propose(run_id: str, body: Any = Body(...), identity=Depends(access)):
        fields(body, {"streamKey", "candidateIds", "reason", "idempotencyKey"}, {"expiresAt"})
        return invoke(service.propose_change_set, identity, run_id, stream_key=body["streamKey"],
                      candidate_ids=body["candidateIds"], reason=body["reason"],
                      idempotency_key=body["idempotencyKey"], expires_at=body.get("expiresAt"))

    @router.post("/change-sets/{change_set_id}/approve")
    def approve(change_set_id: str, body: Any = Body(...), identity=Depends(access)):
        fields(body, {"expectedHash", "reason", "idempotencyKey"})
        return invoke(service.approve_change_set, identity, change_set_id, expected_hash=body["expectedHash"],
                      reason=body["reason"], idempotency_key=body["idempotencyKey"])

    @router.post("/change-sets/{change_set_id}/reject")
    def reject(change_set_id: str, body: Any = Body(...), identity=Depends(access)):
        fields(body, {"expectedHash", "reason", "idempotencyKey"})
        return invoke(service.reject_change_set, identity, change_set_id, expected_hash=body["expectedHash"],
                      reason=body["reason"], idempotency_key=body["idempotencyKey"])

    @router.post("/change-sets/{change_set_id}/publish")
    def publish(change_set_id: str, body: Any = Body(...), identity=Depends(access)):
        fields(body, {"expectedHash", "reason", "idempotencyKey"})
        return invoke(service.publish_change_set, identity, change_set_id, expected_hash=body["expectedHash"],
                      reason=body["reason"], idempotency_key=body["idempotencyKey"])

    @router.get("/publications")
    def publications(streamKey: str, limit: int = 100, identity=Depends(access)):
        return invoke(service.list_publications, identity, streamKey, limit=limit)

    @router.get("/publications/{publication_id}")
    def publication(publication_id: str, identity=Depends(access)):
        return invoke(service.get_publication, identity, publication_id)

    @router.post("/publications/{publication_id}/withdraw")
    def withdraw(publication_id: str, body: Any = Body(...), identity=Depends(access)):
        fields(body, {"reason", "idempotencyKey"})
        return invoke(service.withdraw_publication, identity, publication_id,
                      reason=body["reason"], idempotency_key=body["idempotencyKey"])

    return router
