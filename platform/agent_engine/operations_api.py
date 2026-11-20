"""Local liveness and authenticated operational detail; never calls providers."""
from fastapi import APIRouter, Depends, HTTPException, Request, Response

from agent_admin.api import SESSION_COOKIE
from agent_admin.credentials import AdminAuth
from .operations import OperationsService


def create_operations_router(connection_factory, *, auth=None, service=None):
    auth = auth or AdminAuth(connection_factory)
    service = service or OperationsService(connection_factory)
    router = APIRouter(tags=["operations"])

    @router.get("/health")
    def health(response: Response):
        value = service.health(pool_key="default")
        response.headers["Cache-Control"] = "no-store"
        # Liveness does not publish identities, run IDs or budget contents.
        response.status_code = 503 if value["status"] == "unavailable" else 200
        return {"status": value["status"], "database": value["database"]["status"],
                "workers": value["workers"]["status"]}

    def access(request: Request, response: Response):
        response.headers["Cache-Control"] = "no-store"
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            if origin and origin.rstrip("/") != str(request.base_url).rstrip("/"):
                raise HTTPException(403, "cross-origin operations request rejected")
        bearer = request.headers.get("authorization", "")
        token = bearer[7:] if bearer.startswith("Bearer ") else request.cookies.get(SESSION_COOKIE)
        actor = auth.authenticate(token)
        if actor is None:
            raise HTTPException(401, "authenticated local operator required")
        return actor

    @router.get("/api/agent-admin/operations")
    def status(actor=Depends(access)):
        return service.health(pool_key="default")

    @router.get("/api/agent-admin/operations/alerts")
    def alerts(limit: int = 100, actor=Depends(access)):
        try:
            return service.list_alerts(limit=limit)
        except ValueError:
            raise HTTPException(400, "invalid alert limit") from None
        except Exception:
            raise HTTPException(503, "operations history unavailable") from None

    @router.post("/api/agent-admin/operations/scan")
    def scan(actor=Depends(access)):
        if not actor.permits("operator"):
            raise HTTPException(403, "operator role required")
        try:
            return service.scan_alerts(pool_key="default")
        except Exception:
            raise HTTPException(503, "operations scan unavailable") from None

    return router
