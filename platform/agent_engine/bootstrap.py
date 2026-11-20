"""Application bootstrap for the UAV Agent Engine POC."""

from .engine import AgentEngine
from .postgres import connection_factory
from .worker import fenced_connection_factory
from agent_tools.uav import register_uav_tools
from agent_tools.ontology_runtime import register_source_tools


def build_engine(config_path=None, database="ontology_agent_admin"):
    from agent_tools.engineering_runtime import register_engineering_tools
    from agent_tools.future_battlefield import register_future_battlefield_tools
    from provider_secrets import load_provider_environment
    load_provider_environment()
    base_factory = connection_factory(config_path, database)
    engine = AgentEngine(fenced_connection_factory(base_factory))
    engine.base_connection_factory = base_factory
    register_uav_tools(engine)
    register_source_tools(engine)
    register_engineering_tools(engine)
    register_future_battlefield_tools(engine)
    from agent_tools.cognitive import register_cognitive_tools
    register_cognitive_tools(engine)
    from agent_tools.document_runtime import register_document_tools
    register_document_tools(engine)
    return engine


def build_app(config_path=None, database="ontology_agent_admin", *, local_access=False):
    from pathlib import Path
    from fastapi import FastAPI, Depends, HTTPException, Request
    from fastapi.responses import FileResponse, RedirectResponse
    from agent_admin.api import create_admin_router
    from agent_admin.credentials import AdminAuth
    from .review import ReviewService
    from .worker import WorkerQueue
    engine = build_engine(config_path, database)
    queue = WorkerQueue(engine.base_connection_factory)
    queue.configure_pool(2)
    app = FastAPI(title="Ontology Agent Admin", version="0.3.0")
    app.state.engine, app.state.queue = engine, queue
    from .verification import VerificationService
    from .verification_api import create_verification_router
    verification = VerificationService(engine.base_connection_factory)
    app.state.verification = verification
    app.include_router(create_verification_router(engine.base_connection_factory, verification))
    if local_access:
        from agent_admin.local_access import LocalAccessMiddleware, LocalOperatorCredential
        credential = LocalOperatorCredential(engine.base_connection_factory)
        app.add_middleware(LocalAccessMiddleware, credential=credential)
        app.add_event_handler('startup', credential.start)
        app.add_event_handler('shutdown', credential.stop)
    from .operations_api import create_operations_router
    app.include_router(create_operations_router(engine.base_connection_factory))
    app.include_router(create_admin_router(engine, queue, auth=AdminAuth(engine.base_connection_factory),
                                          review_service=ReviewService(engine.repo)))
    from agent_admin.identity_api import create_identity_router
    app.include_router(create_identity_router(engine.base_connection_factory))
    from agent_admin.config_publication_api import create_config_publication_router
    app.include_router(create_config_publication_router(engine.base_connection_factory))
    from agent_workbench.api import create_workspace_router
    from agent_workbench.admission import build_workspace_run_service
    app.include_router(create_workspace_router(engine.base_connection_factory,
                       run_service=build_workspace_run_service(engine, queue)))
    from agent_workbench.review_workbench_api import create_review_workbench_router
    app.include_router(create_review_workbench_router(engine.base_connection_factory))
    from .traceability_api import create_traceability_router
    app.include_router(create_traceability_router(engine.base_connection_factory))
    from agent_knowledge.api import create_knowledge_router
    app.include_router(create_knowledge_router(engine.base_connection_factory))
    from .evaluation_api import create_evaluation_router
    app.include_router(create_evaluation_router(engine.base_connection_factory))
    from .engineering_api import create_engineering_router
    app.include_router(create_engineering_router(engine))
    from .engineering_lifecycle_api import create_engineering_lifecycle_router
    app.include_router(create_engineering_lifecycle_router(engine.base_connection_factory))
    from .reasoning_api import create_reasoning_router
    app.include_router(create_reasoning_router(engine.base_connection_factory))
    from .dsh_api import create_dsh_router
    app.include_router(create_dsh_router(engine, queue, auth=AdminAuth(engine.base_connection_factory),
                                       verification=verification))
    from .research_governance_api import create_research_governance_router
    app.include_router(create_research_governance_router(engine,queue))
    from .report_review_api import create_report_review_router
    app.include_router(create_report_review_router(engine,queue))
    from .evolution_api import create_evolution_router
    app.include_router(create_evolution_router(engine))
    auth = AdminAuth(engine.base_connection_factory)
    from .legacy_bridge import LegacyRunBridge
    bridge = LegacyRunBridge(engine.repo)
    def authenticated(request: Request):
        bearer = request.headers.get("authorization", "")
        token = bearer[7:] if bearer.startswith("Bearer ") else request.cookies.get("agent_admin_session")
        actor = auth.authenticate(token)
        if actor is None:
            raise HTTPException(401, "authenticated local operator required")
        return actor
    @app.get("/api/agent-admin/legacy-runs/{analysis_id}")
    def legacy_lookup(analysis_id: str, actor=Depends(authenticated)):
        try:
            value = bridge.lookup(analysis_id)
            if not value:
                raise HTTPException(404, "no Engine association exists")
            return value
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
    @app.post("/api/agent-admin/legacy-runs/{analysis_id}")
    def legacy_link(analysis_id: str, body: dict, request: Request, actor=Depends(authenticated)):
        if not actor.permits("operator"):
            raise HTTPException(403, "operator role required")
        if request.headers.get("origin", str(request.base_url)).rstrip("/") != str(request.base_url).rstrip("/"):
            raise HTTPException(403, "cross-origin request rejected")
        try:
            return bridge.link(analysis_id, body["runId"])
        except (ValueError, KeyError) as exc:
            raise HTTPException(400, str(exc)) from None
    @app.get("/api/agent-admin/runs/{run_id}/handoffs")
    def handoffs(run_id: str, actor=Depends(authenticated)):
        from .orchestration import HandoffRepository
        return HandoffRepository(engine.repo).list_children(run_id)
    root = Path(__file__).resolve().parents[1]
    @app.get("/api/agent-admin/process-catalog", tags=["profile-reference"])
    def process_catalog(actor=Depends(authenticated)):
        # Reference content is read-only and separate from executable Profile configuration.
        catalog = root.parent / "docs" / "uav-process-content" / "process-catalog.json"
        if not catalog.is_file():
            raise HTTPException(404, "process reference catalog is unavailable")
        return FileResponse(catalog, media_type="application/json", headers={"Cache-Control": "no-store"})
    # Expose individual assets, never the workspace or local credential files.
    for filename in ("agent-admin.html", "agent-admin.js", "agent-admin.css", "agent-graph-core.js", "agent-admin-phase4.js",
                     "agent-admin-workspaces.js", "agent-review-workbench.js", "agent-admin-config.js", "agent-admin-verification.js", "agent-admin-connectors.js", "agent-admin-cognitive.js",
                     "agent-admin-knowledge.js", "agent-admin-engineering.js", "agent-admin-evaluation.js",
                     "agent-admin-operations.js", "agent-admin-skills.js", "agent-admin-graph.js", "agent-admin-targets.js", "agent-admin-baseline.js", "agent-admin-report-graph.js", "agent-admin-reasoning.js", "agent-admin-review.js"):
        def asset_route(name):
            def asset():
                return FileResponse(root / name, headers={"Cache-Control": "no-store"})
            return asset
        app.add_api_route("/" + filename, asset_route(filename), methods=["GET"], include_in_schema=False)
    icon_root = root / "icons"
    for icon_name in ("brain.svg", "plan.svg", "execute.svg", "info.svg", "scope.svg", "tools.svg", "loop.svg", "data-model.svg"):
        def icon_route(name):
            def asset():
                return FileResponse(icon_root / name, media_type="image/svg+xml", headers={"Cache-Control": "no-store"})
            return asset
        app.add_api_route("/icons/" + icon_name, icon_route(icon_name), methods=["GET"], include_in_schema=False)
    @app.get("/", include_in_schema=False)
    def index():
        return RedirectResponse("/agent-admin.html")
    return app
