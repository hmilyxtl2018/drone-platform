"""Read-only DSH routes for platform-wide Agent supervision."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from .supervision import (GlobalSupervisionService, SupervisionNotFound,
                          SupervisionUnavailable, SupervisionValidationError)


def create_supervision_router(factory, engine, principal):
    router = APIRouter(prefix="/supervision", tags=["global-supervision"])
    def service():
        # Telemetry buffers belong to this read request. Concurrent principals
        # must never share a mutable projection instance.
        return GlobalSupervisionService(factory, engine)

    def invoke(call):
        try:
            return call()
        except SupervisionValidationError as exc:
            raise HTTPException(400, str(exc)) from None
        except SupervisionNotFound:
            raise HTTPException(404, "supervision object not found") from None
        except SupervisionUnavailable as exc:
            raise HTTPException(503, str(exc)) from None

    @router.get("/overview")
    def overview(response: Response, windowHours: int = Query(24), agentKey: str | None = Query(None, max_length=200), actor=Depends(principal)):
        response.headers["Cache-Control"] = "no-store"
        return invoke(lambda: service().overview(actor, window_hours=windowHours, agent_key=agentKey))

    @router.get("/runs")
    def runs(response: Response, windowHours: int = Query(24), agentKey: str | None = Query(None, max_length=200), status: str | None = Query(None, max_length=40),
             cursor: str | None = Query(None, max_length=1024), limit: int = Query(30, ge=1, le=100), actor=Depends(principal)):
        response.headers["Cache-Control"] = "no-store"
        return invoke(lambda: service().runs(actor, window_hours=windowHours, agent_key=agentKey,
                                            status=status, cursor=cursor, limit=limit))

    @router.get("/dashboard")
    def dashboard(response: Response, windowHours: int = Query(24), agentKey: str | None = Query(None, max_length=200),
                  layer: str | None = Query(None, max_length=20), version: str | None = Query(None, max_length=100),
                  actor=Depends(principal)):
        from .supervision_dashboard import DashboardService
        response.headers["Cache-Control"] = "no-store"
        return invoke(lambda: DashboardService(factory, engine).dashboard(actor, window_hours=windowHours,
                         agent_key=agentKey, layer=layer, version=version))

    @router.get("/dashboard/runs")
    def dashboard_runs(response: Response, windowHours: int = Query(24), agentKey: str | None = Query(None, max_length=200),
                       layer: str | None = Query(None, max_length=20), version: str | None = Query(None, max_length=100),
                       metric: str | None = Query(None, max_length=100), status: str | None = Query(None, max_length=40),
                       cursor: str | None = Query(None, max_length=1024), limit: int = Query(30, ge=1, le=100),
                       actor=Depends(principal)):
        from .supervision_dashboard import DashboardService
        response.headers["Cache-Control"] = "no-store"
        return invoke(lambda: DashboardService(factory, engine).runs(actor, window_hours=windowHours,
                         agent_key=agentKey, layer=layer, version=version, metric=metric, status=status,
                         cursor=cursor, limit=limit))

    @router.get("/runs/{run_id}/trace")
    def trace(run_id: str, response: Response, actor=Depends(principal)):
        from .supervision_trace import SupervisionTraceService
        response.headers["Cache-Control"] = "no-store"
        return invoke(lambda: SupervisionTraceService(factory, engine).trace(actor, run_id))

    @router.get("/runs/{run_id}")
    def run(run_id: str, response: Response, actor=Depends(principal)):
        response.headers["Cache-Control"] = "no-store"
        return invoke(lambda: service().detail(actor, run_id))

    return router
