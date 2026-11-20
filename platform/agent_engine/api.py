"""Optional HTTP API for the Agent Runtime Engine."""
from __future__ import annotations

from .engine import AgentEngine
from .uav_execution_contracts import BaselineNotReady


def create_app(engine: AgentEngine):
    try:
        from fastapi import FastAPI, HTTPException
    except ImportError as exc:
        raise RuntimeError("install fastapi to enable Agent Engine API") from exc

    app = FastAPI(title="Agent Runtime Engine", version="0.1.0")

    def not_found(value):
        if not value:
            raise HTTPException(404, "run not found")
        return value

    @app.post("/api/agent-runs")
    async def create_run(body: dict):
        try:
            rid = engine.create_run(body["agentKey"], body.get("input", {}), body.get("agentVersion"))
            return {"runId": rid, "status": "created"}
        except BaselineNotReady as exc:
            raise HTTPException(409, detail=exc.admission_details) from None
        except (KeyError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/api/agent-runs/{run_id}")
    def get_run(run_id: str):
        return not_found(engine.repo.get_run(run_id))

    @app.post("/api/agent-runs/{run_id}/execute")
    async def execute(run_id: str, body: dict | None = None):
        body = body or {}
        try:
            return await engine.execute(run_id, body.get("agentKey"), body.get("input"), body.get("agentVersion"))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/agent-runs/{run_id}/pause")
    def pause(run_id: str, body: dict | None = None):
        try:
            return engine.pause(run_id, (body or {}).get("reason", "paused by user"))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/agent-runs/{run_id}/resume")
    async def resume(run_id: str, body: dict | None = None):
        body = body or {}
        try:
            return await engine.execute(run_id, body.get("agentKey"), body.get("input"), body.get("agentVersion"))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/agent-runs/{run_id}/cancel")
    def cancel(run_id: str):
        try:
            engine.cancel(run_id)
            return {"runId": run_id, "status": "cancelled"}
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/agent-runs/{run_id}/approvals/{approval_id}")
    def decide_approval(run_id: str, approval_id: str, body: dict):
        try:
            result = engine.decide_approval(approval_id, body["approved"], body.get("reviewer", "api"), body.get("comment", ""), run_id=run_id)
            if result.get("run_id") != run_id:
                raise HTTPException(409, "approval does not belong to run")
            return result
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(400, f"missing field: {exc.args[0]}") from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/agent-runs/{run_id}/approvals")
    def request_approval(run_id: str, body: dict):
        try:
            return engine.request_approval(run_id, body["action"], body["reason"], body.get("requestedBy", "api"), body.get("stepKey"))
        except KeyError as exc:
            raise HTTPException(400, f"missing field: {exc.args[0]}") from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/api/agent-runs/{run_id}/steps")
    def steps(run_id: str):
        not_found(engine.repo.get_run(run_id))
        return engine.repo.list_steps(run_id)

    @app.get("/api/agent-runs/{run_id}/trace")
    def trace(run_id: str):
        not_found(engine.repo.get_run(run_id))
        return engine.repo.list_events(run_id)

    @app.get("/api/agent-runs/{run_id}/artifacts")
    def artifacts(run_id: str):
        not_found(engine.repo.get_run(run_id))
        return engine.repo.list_artifacts(run_id)

    @app.get("/api/agent-runs/{run_id}/approvals")
    def approvals(run_id: str):
        not_found(engine.repo.get_run(run_id))
        return engine.repo.list_approvals(run_id)

    return app
