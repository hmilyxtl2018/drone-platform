"""Serve the swarm effect console on localhost.

kb.html reads the knowledge package via /api/kb/boot. Browser never talks to 5432.
PostgreSQL is preferred; the checked-in SQLite snapshot is used when it is offline.
Connection: env PGPASSWORD… or ../db.local.json (not under /demo or /ontology).
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from contextlib import nullcontext
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

try:  # Supports both ``python serve.py`` and ``python -m demo.serve``.
    from provider_secrets import load_provider_environment
    from runtime_store import Conflict, NotFound, RuntimeStore, RuntimeStoreError, ValidationError
    from intelligence_agent import IntelligenceAgent
    from intelligence_ontology import attach_intelligence_ontology
    from intelligence_store import IntelligenceConflict, IntelligenceNotFound, IntelligenceStoreError, IntelligenceValidationError
    from osint_store import OSINTNotFound, OSINTStoreError, OSINTTaskStore, OSINTValidationError, public_sources
    from imagery_evidence import ImageryEvidenceRegistry
    from extraction_jobs import ExtractionJobManager
    from research_followups import ResearchFollowups
    from research_trace import trace_call
    from equipment_verification import EquipmentVerificationQueue
    from ontology_agent import OntologyAgent, ontology_taxonomy
    from capability_profile import CapabilityProfiles
    from capability_workflow import CapabilityWorkflow
    from audit_run import EntityAuditRuns
    from agent_orchestrator import MissionOrchestrator
    from analysis_execution_bridge import EntityAuditExecutionBridge
    from conversation_store import ConversationStore
    from manufacturing_store import ManufacturingStore
    from manufacturing_research import ManufacturingResearchWorkflow
    from factor_graph import FactorGraphStore
except ModuleNotFoundError:  # pragma: no cover - exercised by module launch
    from .provider_secrets import load_provider_environment
    from .runtime_store import Conflict, NotFound, RuntimeStore, RuntimeStoreError, ValidationError
    from .intelligence_agent import IntelligenceAgent
    from .intelligence_ontology import attach_intelligence_ontology
    from .intelligence_store import IntelligenceConflict, IntelligenceNotFound, IntelligenceStoreError, IntelligenceValidationError
    from .osint_store import OSINTNotFound, OSINTStoreError, OSINTTaskStore, OSINTValidationError, public_sources
    from .imagery_evidence import ImageryEvidenceRegistry
    from .extraction_jobs import ExtractionJobManager
    from .research_followups import ResearchFollowups
    from .research_trace import trace_call
    from .equipment_verification import EquipmentVerificationQueue
    from .ontology_agent import OntologyAgent, ontology_taxonomy
    from .capability_profile import CapabilityProfiles
    from .capability_workflow import CapabilityWorkflow
    from .audit_run import EntityAuditRuns
    from .agent_orchestrator import MissionOrchestrator
    from .analysis_execution_bridge import EntityAuditExecutionBridge
    from .conversation_store import ConversationStore
    from .manufacturing_store import ManufacturingStore
    from .manufacturing_research import ManufacturingResearchWorkflow
    from .factor_graph import FactorGraphStore

# Some test runners add ``platform/`` to sys.path before importing this module.
# In that case the broad compatibility import above can load a second
# top-level ``capability_workflow`` module even though callers use
# ``demo.capability_workflow``.  Resolve the package class explicitly when
# running as a package so HTTP handlers and test patches share one identity.
if __package__:
    from .capability_workflow import CapabilityWorkflow as _PackageCapabilityWorkflow
    CapabilityWorkflow = _PackageCapabilityWorkflow

ROOT = Path(__file__).resolve().parent
ONTOLOGY = ROOT.parent / "ontology"
DB_LOCAL = ROOT.parent / "db.local.json"
SQLITE_DB = ROOT.parent / "ontology" / "coast-raid-k" / "coast-raid-k.sqlite"
SQLITE_DEFAULT = ONTOLOGY / "coast-raid-k" / "coast-raid-k.sqlite"
ONTOLOGY_STATIC_EXTENSIONS = {".json", ".jsonld", ".ttl", ".md", ".txt", ".sql"}
# Load optional DPAPI-protected local provider configuration before constructing
# the Agent singletons. Environment variables still take precedence.
load_provider_environment()
# Runtime state intentionally lives in a different SQLite file from the
# generated, read-only ontology snapshot.  CRK_RUNTIME_SQLITE_PATH can point
# at a temporary file when running API tests or a demo fixture.
RUNTIME_STORE = RuntimeStore()
INTELLIGENCE_AGENT = IntelligenceAgent()
OSINT_STORE = OSINTTaskStore(path=os.environ.get("CRK_OSINT_SQLITE_PATH"), intelligence=INTELLIGENCE_AGENT)
EXTRACTION_JOBS = ExtractionJobManager(INTELLIGENCE_AGENT, OSINT_STORE)


def ontology_agent():
    return OntologyAgent(INTELLIGENCE_AGENT.store, trend_provider=INTELLIGENCE_AGENT.trend_provider)


def start_verification_recovery_monitor():
    """Keep abandoned verification leases from blocking future audits.

    Requests also run an opportunistic scan, but the monitor makes recovery
    independent of page visits or a new audit submission.
    """
    queue = EquipmentVerificationQueue(INTELLIGENCE_AGENT.store)

    def monitor():
        while True:
            try:
                queue.reclaim_stale()
            except Exception:
                # Recovery is housekeeping; never take down the HTTP server.
                pass
            time.sleep(60)

    thread = threading.Thread(target=monitor, daemon=True, name="verification-recovery")
    thread.start()
    return thread


CAPABILITY_RECOVERY_COUNT = CapabilityWorkflow(ontology_agent()).recover()
# Verification jobs use their own worker lease.  Reclaim only leases whose
# heartbeat has expired; a live provider call keeps its lease via heartbeat.
VERIFICATION_RECOVERY = EquipmentVerificationQueue(INTELLIGENCE_AGENT.store).reclaim_stale()
AUDIT_RECOVERY_COUNT = EntityAuditRuns(ontology_agent()).recover()


def research_followups():
    # Resolve request stores here so isolated API tests and alternate local
    # stores never accidentally collect against the production singleton.
    return ResearchFollowups(OSINT_STORE, INTELLIGENCE_AGENT.store)


def assessment_operation(assessment_id, stage, operation, callback, *, creates_assessment=False):
    """Attribute observed provider calls to this HTTP request's exact draft."""
    assessment=INTELLIGENCE_AGENT.store.get_assessment(assessment_id)
    if assessment is None:
        raise IntelligenceNotFound(f"assessment not found: {assessment_id}")
    binder=getattr(OSINT_STORE,"execution_context",None)
    context=binder(task_id=assessment.get("taskId"),assessment_id=None if creates_assessment else assessment_id,stage=stage,operation=operation) if callable(binder) else nullcontext()
    with context as group:
        with trace_call(provider="ontology-engine",operation=operation,details={"sourceCount":len(assessment.get("sourceSnapshot") or [])}) as trace:
            result=callback()
            if isinstance(result,dict):
                if isinstance(result.get("nodes"),list):trace["nodeCount"]=len(result["nodes"])
                if isinstance(result.get("edges"),list):trace["edgeCount"]=len(result["edges"])
                if result.get("kind"):trace["method"]=result["kind"]
                if creates_assessment and group is not None and result.get("assessment"):
                    OSINT_STORE.bind_execution_assessment(group.run_id,result["assessment"]["assessmentId"])
            return result


def execution_payload(*, task_id=None, assessment_id=None):
    trace=OSINT_STORE.list_execution_trace(task_id=task_id,assessment_id=assessment_id)
    return {"ok":True,"executionTrace":trace,"traceCoverage":"recorded" if trace else "legacy-unrecorded"}


def chain_assessment_extraction(result):
    """Complete the automatic analysis chain after saving a new draft.

    In automatic mode the trend draft contains the batched public metric
    observations when the DeepSeek trend provider is available. The saved
    draft is therefore sufficient to continue into object/attribute
    extraction; only deterministic or legacy drafts without a real model
    capability result need a compatibility derived quantification assessment.
    Reads and historical-page hydration never start model work or backfill
    jobs.
    Report persistence is already complete, so a queue problem is a separate,
    recoverable result rather than a failed report-generation response.
    """
    assessment=result.get("assessment") or {}
    response=dict(result)

    # The task owns the automation policy.  Do not infer automatic execution
    # from the provider name: manual tasks may use the same model and must
    # remain operator-triggered.  Missing/legacy task metadata keeps the old
    # behavior for backwards compatibility.
    task = None
    try:
        task = OSINT_STORE.get_task(assessment.get("taskId")) if assessment.get("taskId") else None
    except Exception:
        task = None
    automation = task.get("automation") if isinstance(task, dict) else {}
    automatic_analysis = bool(
        isinstance(task, dict)
        and task.get("workflowMode") == "auto"
        and isinstance(automation, dict)
        and automation.get("autoStartAssessment") is True
    )

    # DeepSeekTrendProvider.generate() already performs the batched public
    # metric extraction and embeds its result in the saved trend draft. Do
    # not issue a second quantification request for that draft: it duplicates
    # model spend and creates a redundant derived assessment. A deterministic
    # or legacy draft with no real model capability result still gets the
    # compatibility follow-up when the configured provider is available.
    capability = (assessment.get("summary") or {}).get("capabilityAssessment")
    embedded_model_quantification = bool(
        isinstance(capability, dict)
        and str(capability.get("provider") or "").casefold().startswith("deepseek")
        and str(capability.get("model") or "").strip()
    )
    if embedded_model_quantification:
        response["quantificationAssessment"] = assessment
        response["quantificationSource"] = "trend-generation"
        response["quantificationSkipped"] = True
    elif automatic_analysis and getattr(INTELLIGENCE_AGENT.trend_provider, "configured", False):
        try:
            quantified = assessment_operation(
                assessment["assessmentId"],
                "capability-analysis",
                "quantify-public-observations",
                lambda: INTELLIGENCE_AGENT.quantify_assessment(assessment["assessmentId"]),
                creates_assessment=True,
            )
            quantified_assessment = quantified.get("assessment") if isinstance(quantified, dict) else None
            if quantified_assessment:
                response["baseAssessment"] = assessment
                response["quantificationAssessment"] = quantified_assessment
                response["quantificationDuplicate"] = bool(quantified.get("duplicate"))
                response["assessment"] = quantified_assessment
                assessment = quantified_assessment
        except Exception:
            # The trend draft is still useful and remains the recovery point.
            # Do not expose provider URLs, prompts or credential-bearing error
            # text in the API response.
            response["quantificationError"] = "公开指标量化未完成；趋势研判已保存，可在执行总览中重试。"

    # Automatic mode is allowed to continue object extraction after a
    # quantification transport failure.  A missing metric is a coverage gap,
    # whereas a failed model call is a recoverable operation state.
    should_extract = bool(
        assessment.get("model")
        or assessment.get("provider") == "deepseek-trend-provider"
        or (automatic_analysis and getattr(INTELLIGENCE_AGENT.trend_provider, "configured", False))
    )
    if not should_extract:
        return response
    try:
        extraction=EXTRACTION_JOBS.submit(assessment["assessmentId"], {
            "expectedRevision":assessment["revision"], "extractEntities":True,
        }, reuse_existing=True)
        response["extractionJob"]=extraction["job"]
    except Exception as exc:
        if isinstance(exc, IntelligenceValidationError) and not getattr(INTELLIGENCE_AGENT.trend_provider,"configured",False):
            error="对象与属性自动提取未启动：DeepSeek 未配置；报告已保存，配置后可重试。"
        elif isinstance(exc, IntelligenceConflict):
            error="对象与属性自动提取未启动：已有任务运行或报告、图谱版本已变更；报告已保存，请刷新后重试。"
        else:
            error="对象与属性自动提取未能启动；报告已保存，可手动重试。"
        # Exception text may contain provider URLs, source bodies or keys.
        response["extractionError"]=error
    return response


def db_kwargs():
    cfg = {}
    if DB_LOCAL.exists():
        cfg = json.loads(DB_LOCAL.read_text(encoding="utf-8"))
    return {
        "host": os.environ.get("PGHOST", cfg.get("host", "127.0.0.1")),
        "port": int(os.environ.get("PGPORT", cfg.get("port", 5432))),
        "user": os.environ.get("PGUSER", cfg.get("user", "postgres")),
        "password": os.environ.get("PGPASSWORD", cfg.get("password", "")),
        "dbname": os.environ.get("PGDATABASE", cfg.get("dbname", "coast_raid_k")),
    }


def sqlite_path() -> Path:
    """Return the local read-only fallback database path.

    The generated SQLite snapshot is part of the ontology package, so the
    console can still boot when PostgreSQL is stopped or psycopg2 is absent.
    An environment override is useful for local development and CI fixtures.
    """
    cfg = {}
    if DB_LOCAL.exists():
        try:
            cfg = json.loads(DB_LOCAL.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            cfg = {}
    raw = os.environ.get("CRK_SQLITE_PATH") or cfg.get("sqlite_path")
    path = Path(raw).expanduser() if raw else SQLITE_DEFAULT
    if not path.is_absolute():
        path = ROOT.parent / path
    return path.resolve()


def pg_connect():
    import psycopg2
    from psycopg2.extras import RealDictCursor

    kw = db_kwargs()
    if not kw.get("password"):
        raise RuntimeError("no postgres password: set PGPASSWORD or write db.local.json")
    return psycopg2.connect(cursor_factory=RealDictCursor, **kw)


def _decode_doc(doc):
    """Normalize JSONB rows and SQLite TEXT rows to the same API shape."""
    if not isinstance(doc, str):
        return doc
    try:
        return json.loads(doc)
    except json.JSONDecodeError:
        return doc


def _boot_from_cursor(cur, store: dict) -> dict:
    """Build the browser boot payload from a DB-API cursor.

    Both psycopg2's RealDictCursor and sqlite3.Row are supported.  Keeping
    this projection in one place prevents the fallback from drifting from
    the PostgreSQL response contract.
    """
    cur.execute("SELECT id, version, title, kind, envelope, unit, frame, excludes, parts FROM crk_package")
    pkg = cur.fetchone()
    if not pkg:
        raise RuntimeError("crk_package is empty")
    cur.execute("SELECT file, doc FROM crk_part")
    files = {row["file"]: _decode_doc(row["doc"]) for row in cur.fetchall()}
    cur.execute("SELECT type, id, nav, file, label, iri FROM crk_individual ORDER BY nav, type, id")
    entries = []
    for row in cur.fetchall():
        # sqlite3.Row does not implement dict.get; use key membership so this
        # works for both RealDictCursor and sqlite3.Row rows.
        rec = {
            "id": row["id"],
            "type": row["type"],
            "module": row["nav"],
            "file": row["file"],
            "iri": row["iri"],
        }
        if row["label"]:
            rec["label"] = row["label"]
        entries.append(rec)
    return {
        "ok": True,
        "store": store,
        "package": {
            "id": pkg["id"],
            "version": pkg["version"],
            "title": pkg["title"],
            "kind": pkg["kind"],
        },
        "files": files,
        "inventory": {
            "package": pkg["id"],
            "generated": True,
            "entries": entries,
        },
    }


def _kb_boot_postgres() -> dict:
    con = pg_connect()
    try:
        kw = db_kwargs()
        return _boot_from_cursor(
            con.cursor(),
            {
                "kind": "postgres",
                "host": kw["host"],
                "port": kw["port"],
                "dbname": kw["dbname"],
            },
        )
    finally:
        con.close()


def _kb_boot_sqlite(pg_error: Exception | None = None) -> dict:
    path = sqlite_path()
    if not path.is_file():
        raise RuntimeError(f"SQLite fallback database not found: {path}")
    # Open the packaged snapshot in SQLite's read-only URI mode.  This keeps a
    # browser request from accidentally mutating the source-of-record export.
    con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        store = {
            "kind": "sqlite",
            "path": str(path),
            "dbname": path.stem,
            "readOnly": True,
        }
        if pg_error is not None:
            # This is diagnostic metadata only; the response remains
            # compatible with the existing boot payload.
            store["fallback"] = True
            store["postgresError"] = str(pg_error)
        return _boot_from_cursor(con.cursor(), store)
    finally:
        con.close()


def kb_boot() -> dict:
    """Load the KB from PostgreSQL, falling back to the SQLite snapshot."""
    if os.environ.get("CRK_KB_STORE") == "sqlite":
        result = _kb_boot_sqlite()
        result["releaseVersion"] = os.environ.get("CRK_RELEASE_VERSION")
        return result
    try:
        return _kb_boot_postgres()
    except Exception as pg_error:
        try:
            return _kb_boot_sqlite(pg_error)
        except Exception as sqlite_error:
            raise RuntimeError(f"PostgreSQL unavailable ({pg_error}); SQLite fallback failed ({sqlite_error})") from sqlite_error


def kb_health() -> dict:
    """Report the active store while keeping health useful offline."""
    if os.environ.get("CRK_KB_STORE") == "sqlite":
        boot = _kb_boot_sqlite()
        return {"ok": True, "store": boot["store"], "releaseVersion": os.environ.get("CRK_RELEASE_VERSION")}
    try:
        con = pg_connect()
        con.close()
        kw = db_kwargs()
        return {
            "ok": True,
            "host": kw["host"],
            "dbname": kw["dbname"],
            "store": {"kind": "postgres", "host": kw["host"], "port": kw["port"], "dbname": kw["dbname"]},
        }
    except Exception as pg_error:
        path = sqlite_path()
        if not path.is_file():
            raise RuntimeError(f"PostgreSQL unavailable ({pg_error}); SQLite fallback database not found: {path}") from pg_error
        return {
            "ok": True,
            "host": "local",
            "dbname": path.stem,
            "store": {"kind": "sqlite", "path": str(path), "dbname": path.stem, "readOnly": True, "fallback": True},
        }


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def log_message(self, fmt, *args):
        print(self.address_string(), "-", fmt % args)

    def translate_path(self, path):
        raw = unquote(path.split("?", 1)[0])
        if raw.startswith("/ontology/"):
            rel = raw[len("/ontology/") :].lstrip("/").replace("\\", "/")
            parts = [p for p in rel.split("/") if p and p != "."]
            if any(p == ".." or p.startswith(".") for p in parts):
                return str(ROOT / "_denied")
            target = ONTOLOGY.joinpath(*parts).resolve()
            try:
                target.relative_to(ONTOLOGY.resolve())
            except ValueError:
                return str(ROOT / "_denied")
            if target.suffix.casefold() not in ONTOLOGY_STATIC_EXTENSIONS:
                return str(ROOT / "_denied")
            return str(target)
        return super().translate_path(path)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_GET(self):
        parsed = urlsplit(self.path)
        raw = unquote(parsed.path)
        if raw == "/health":
            state = INTELLIGENCE_AGENT.state()
            self._send_json({"ok": True, "service": "ontology-agents", "port": self.server.server_address[1], "summary": state.get("summary", {}), "provider": state.get("provider"), "trendProvider": state.get("trendProvider")})
            return
        if raw.startswith("/api/ontology/"):
            self.handle_ontology_get(raw, parse_qs(parsed.query))
            return
        if raw == "/api/intelligence/ontology":
            self.handle_intelligence_get(raw, parse_qs(parsed.query))
            return
        if raw == "/api/osint/ontology" or raw == "/api/osint/ontology/audits":
            self.handle_osint_get(raw, parse_qs(parsed.query))
            return
        if raw == "/api/kb/boot" or raw == "/api/kb/health":
            self.handle_kb(raw)
            return
        if raw == "/api/runs" or raw.startswith("/api/runs/") or raw.startswith("/api/human-reviews/"):
            self.handle_runtime_get(raw, parse_qs(parsed.query))
            return
        if raw.startswith("/api/intelligence/"):
            self.handle_intelligence_get(raw, parse_qs(parsed.query))
            return
        if raw == "/api/osint/state" or raw.startswith("/api/osint/tasks/") or raw.startswith("/api/osint/assessments/") or raw.startswith("/api/osint/admissions/") or raw.startswith("/api/osint/audits") or raw.startswith("/api/osint/audit-runs/") or raw.startswith("/api/osint/audit-findings"):
            self.handle_osint_get(raw, parse_qs(parsed.query))
            return
        super().do_GET()

    def _send_json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValidationError("invalid Content-Length") from exc
        if length > 2_000_000:
            raise ValidationError("request body exceeds 2 MB")
        raw = self.rfile.read(max(0, length))
        if not raw:
            return {}
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValidationError("request body must be valid JSON") from exc
        if not isinstance(payload, dict):
            raise ValidationError("request body must be a JSON object")
        return payload

    @staticmethod
    def _runtime_error_status(exc: Exception) -> int:
        if isinstance(exc, NotFound):
            return 404
        if isinstance(exc, Conflict):
            return 409
        if isinstance(exc, ValidationError):
            return 400
        return 500

    @staticmethod
    def _intelligence_error_status(exc: Exception) -> int:
        if isinstance(exc, IntelligenceNotFound):
            return 404
        if isinstance(exc, IntelligenceConflict):
            return 409
        if isinstance(exc, IntelligenceValidationError):
            return 400
        if isinstance(exc, (ValueError, TypeError)):
            return 400
        if isinstance(exc, LookupError):
            return 404
        return 500

    @staticmethod
    def _osint_error_status(exc: Exception) -> int:
        if isinstance(exc, OSINTNotFound):
            return 404
        if isinstance(exc, OSINTValidationError):
            return 400
        return 500

    def _assessment_payload(self, assessment_id: str) -> dict:
        assessment = INTELLIGENCE_AGENT.store.get_assessment(assessment_id)
        if assessment is None:
            raise IntelligenceNotFound(f"assessment not found: {assessment_id}")
        return {
            "ok": True,
            "assessment": assessment,
            "reviews": INTELLIGENCE_AGENT.store.list_assessment_reviews(assessment_id),
            "graph": INTELLIGENCE_AGENT.store.get_graph(assessment_id),
            "validationRounds": INTELLIGENCE_AGENT.list_validation_rounds(assessment_id),
            "admissions": INTELLIGENCE_AGENT.list_admissions(assessment_id),
        }

    def _assessment_graph_payload(self, assessment_id: str) -> dict:
        return {"ok": True, "graph": INTELLIGENCE_AGENT.store.get_graph(assessment_id)}

    def _assessment_validation_payload(self, assessment_id: str) -> dict:
        rounds = INTELLIGENCE_AGENT.list_validation_rounds(assessment_id)
        return {"ok": True, "validationRounds": rounds, "rounds": rounds}

    def _assessment_admissions_payload(self, assessment_id: str) -> dict:
        return {"ok": True, "admissions": INTELLIGENCE_AGENT.list_admissions(assessment_id)}

    def _assessment_audit_payload(self, assessment_id: str) -> dict:
        assessment = INTELLIGENCE_AGENT.store.get_assessment(assessment_id)
        if assessment is None:
            raise IntelligenceNotFound(f"assessment not found: {assessment_id}")
        audits = INTELLIGENCE_AGENT.store.list_audits(assessment_id)
        findings = INTELLIGENCE_AGENT.store.list_audit_findings(assessment_id)
        return {"ok": True, "audits": audits, "auditRuns": audits, "findings": findings}

    def _audit_payload(self, audit_id: str) -> dict:
        audit = INTELLIGENCE_AGENT.store.get_audit(audit_id)
        if audit is None:
            raise IntelligenceNotFound(f"audit not found: {audit_id}")
        return {"ok": True, "audit": audit, "auditRun": audit,
                "findings": INTELLIGENCE_AGENT.store.list_audit_findings(audit_id=audit_id)}

    def _audit_finding_payload(self, finding_id: str) -> dict:
        finding = INTELLIGENCE_AGENT.store.get_audit_finding(finding_id)
        if finding is None:
            raise IntelligenceNotFound(f"audit finding not found: {finding_id}")
        return {"ok": True, "finding": finding}

    def _admission_payload(self, admission_id: str) -> dict:
        admission = next((x for x in INTELLIGENCE_AGENT.list_admissions() if x["admissionId"] == admission_id), None)
        if admission is None:
            raise IntelligenceNotFound(f"admission not found: {admission_id}")
        return {"ok": True, "admission": admission}

    def handle_osint_get(self, raw: str, query: dict[str, list[str]] | None = None) -> None:
        try:
            query = query or {}
            parts = [part for part in raw.split("/") if part]
            if len(parts) >= 4 and parts[:3] == ["api", "osint", "assessments"] and INTELLIGENCE_AGENT.store.get_assessment(parts[3]) is None:
                raise IntelligenceNotFound(f"assessment not found: {parts[3]}")
            if parts == ["api", "osint", "ontology"]:
                payload = {"ok": True, **INTELLIGENCE_AGENT.store.published_ontology(detailed=True)}
            elif parts == ["api", "osint", "ontology", "audits"]:
                payload = {"ok": True, "audits": INTELLIGENCE_AGENT.store.list_audits()}
            elif parts == ["api", "osint", "state"]:
                payload = {"ok": True, "sources": public_sources(), "tasks": OSINT_STORE.list_tasks(), "provider": INTELLIGENCE_AGENT.state().get("trendProvider")}
            elif parts in (["api", "osint", "audits"], ["api", "osint", "audit-runs"]):
                assessment_id = (query.get("assessmentId") or [None])[0]
                audits = INTELLIGENCE_AGENT.store.list_audits(assessment_id)
                payload = {"ok": True, "audits": audits, "auditRuns": audits}
            elif parts == ["api", "osint", "audit-findings"]:
                assessment_id = (query.get("assessmentId") or [None])[0]
                audit_id = (query.get("auditId") or [None])[0]
                payload = {"ok": True, "findings": INTELLIGENCE_AGENT.store.list_audit_findings(assessment_id, audit_id)}
            elif len(parts) == 4 and parts[:3] == ["api", "osint", "tasks"]:
                task = OSINT_STORE.get_task(parts[3])
                if task is None:
                    raise OSINTNotFound(f"task not found: {parts[3]}")
                payload = {"ok": True, "task": task}
            elif len(parts) == 5 and parts[:3] == ["api", "osint", "tasks"] and parts[4] == "assessments":
                task = OSINT_STORE.get_task(parts[3])
                if task is None:
                    raise OSINTNotFound(f"task not found: {parts[3]}")
                payload = {"ok": True, "assessments": INTELLIGENCE_AGENT.list_assessments(parts[3])}
            elif len(parts) == 5 and parts[:3] == ["api", "osint", "tasks"] and parts[4] in {"execution", "trace"}:
                if OSINT_STORE.get_task(parts[3]) is None:
                    raise OSINTNotFound(f"task not found: {parts[3]}")
                payload = execution_payload(task_id=parts[3])
            elif len(parts) == 5 and parts[:3] == ["api", "osint", "tasks"] and parts[4] == "imagery-evidence":
                payload = {"ok": True, "imageryEvidence": ImageryEvidenceRegistry(OSINT_STORE, INTELLIGENCE_AGENT.store).list(parts[3]), "confidencePromotion": False}
            elif len(parts) == 5 and parts[:3] == ["api", "osint", "tasks"] and parts[4] == "parent-followup":
                if OSINT_STORE.get_task(parts[3]) is None:
                    raise OSINTNotFound('task not found')
                payload = {"ok": True, "followup": research_followups().parent(parts[3])}
            elif len(parts) == 4 and parts[:3] == ["api", "osint", "assessments"]:
                payload = self._assessment_payload(parts[3])
            elif len(parts) == 5 and parts[:3] == ["api", "osint", "assessments"] and parts[4] in {"audit", "audits", "audit-runs"}:
                payload = self._assessment_audit_payload(parts[3])
            elif len(parts) == 4 and parts[:3] == ["api", "osint", "audit-runs"]:
                payload = self._audit_payload(parts[3])
            elif len(parts) == 4 and parts[:3] == ["api", "osint", "audit-findings"]:
                payload = self._audit_finding_payload(parts[3])
            elif len(parts) == 5 and parts[:3] == ["api", "osint", "assessments"] and parts[4] == "graph":
                payload = self._assessment_graph_payload(parts[3])
            elif len(parts) == 5 and parts[:3] == ["api", "osint", "assessments"] and parts[4] in {"validation", "validations"}:
                payload = self._assessment_validation_payload(parts[3])
            elif len(parts) == 5 and parts[:3] == ["api", "osint", "assessments"] and parts[4] == "admissions":
                payload = self._assessment_admissions_payload(parts[3])
            elif len(parts) == 5 and parts[:3] == ["api", "osint", "assessments"] and parts[4] in {"execution", "trace"}:
                payload = execution_payload(assessment_id=parts[3])
            elif len(parts) == 4 and parts[:3] == ["api", "osint", "admissions"]:
                payload = self._admission_payload(parts[3])
            else:
                self._send_json({"ok": False, "error": "OSINT endpoint not found"}, 404)
                return
            self._send_json(payload)
        except Exception as exc:
            self._send_json({"ok": False, "error": str(exc)}, self._intelligence_error_status(exc) if isinstance(exc, IntelligenceStoreError) else self._osint_error_status(exc))

    def handle_intelligence_get(self, raw: str, query: dict[str, list[str]]) -> None:
        try:
            parts = [part for part in raw.split("/") if part]
            if len(parts) >= 4 and parts[:3] == ["api", "intelligence", "assessments"] and INTELLIGENCE_AGENT.store.get_assessment(parts[3]) is None:
                raise IntelligenceNotFound(f"assessment not found: {parts[3]}")
            if parts == ["api", "intelligence", "ontology"]:
                payload = {"ok": True, **INTELLIGENCE_AGENT.store.published_ontology(detailed=True)}
            elif parts == ["api", "intelligence", "ontology", "audits"]:
                assessment_id = (query.get("assessmentId") or [None])[0]
                payload = {"ok": True, "audits": INTELLIGENCE_AGENT.store.list_audits(assessment_id)}
            elif parts in (["api", "intelligence", "attribute-catalog"], ["api", "intelligence", "attribute-predicates"]):
                try:
                    from ontology_attribute_admission import get_attribute_admission_catalog
                except ModuleNotFoundError:
                    from .ontology_attribute_admission import get_attribute_admission_catalog
                catalog = get_attribute_admission_catalog()
                payload = {"ok": True, "attributes": catalog, "attributePredicates": catalog}
            elif len(parts) == 5 and parts[:3] == ["api", "intelligence", "assessments"] and parts[4] == "follow-up-plan":
                payload = {"ok": True, "plan": research_followups().plan(parts[3])}
            elif len(parts) == 5 and parts[:3] == ["api", "intelligence", "assessments"] and parts[4] == "followups":
                payload = {"ok": True, "followups": research_followups().list(parts[3])}
            elif len(parts) == 4 and parts[:3] == ["api", "intelligence", "extraction-jobs"]:
                payload = {"ok": True, "job": EXTRACTION_JOBS.get(parts[3])}
            elif len(parts) == 5 and parts[:3] == ["api", "intelligence", "assessments"] and parts[4] == "extraction-jobs":
                payload = {"ok": True, "jobs": EXTRACTION_JOBS.list(parts[3])}
            elif parts == ["api", "intelligence", "state"]:
                payload = {"ok": True, **INTELLIGENCE_AGENT.state()}
            elif parts == ["api", "intelligence", "documents"]:
                payload = {"ok": True, "documents": INTELLIGENCE_AGENT.store.list_documents()}
            elif parts == ["api", "intelligence", "candidates"]:
                document_id = (query.get("documentId") or [None])[0]
                payload = {"ok": True, "candidates": INTELLIGENCE_AGENT._decorate_candidates(INTELLIGENCE_AGENT.store.list_candidates(document_id=document_id))}
            elif parts == ["api", "intelligence", "conflicts"]:
                payload = {"ok": True, "conflicts": INTELLIGENCE_AGENT.store.list_conflicts()}
            elif parts == ["api", "intelligence", "reviews"]:
                candidate_id = (query.get("candidateId") or [None])[0]
                payload = {"ok": True, "reviews": INTELLIGENCE_AGENT.store.list_reviews(candidate_id)}
            elif parts == ["api", "intelligence", "versions"]:
                payload = {"ok": True, "versions": INTELLIGENCE_AGENT.store.list_versions()}
            elif parts == ["api", "intelligence", "events"]:
                payload = {"ok": True, "events": INTELLIGENCE_AGENT.store.list_events()}
            elif parts in (["api", "intelligence", "audits"], ["api", "intelligence", "audit-runs"]):
                assessment_id = (query.get("assessmentId") or [None])[0]
                audits = INTELLIGENCE_AGENT.store.list_audits(assessment_id)
                payload = {"ok": True, "audits": audits, "auditRuns": audits}
            elif parts == ["api", "intelligence", "audit-findings"]:
                assessment_id = (query.get("assessmentId") or [None])[0]
                audit_id = (query.get("auditId") or [None])[0]
                payload = {"ok": True, "findings": INTELLIGENCE_AGENT.store.list_audit_findings(assessment_id, audit_id)}
            elif parts == ["api", "intelligence", "assessments"]:
                task_id = (query.get("taskId") or [None])[0]
                payload = {"ok": True, "assessments": INTELLIGENCE_AGENT.list_assessments(task_id)}
            elif len(parts) == 4 and parts[:3] == ["api", "intelligence", "assessments"]:
                payload = self._assessment_payload(parts[3])
            elif len(parts) == 5 and parts[:3] == ["api", "intelligence", "assessments"] and parts[4] in {"audit", "audits", "audit-runs"}:
                payload = self._assessment_audit_payload(parts[3])
            elif len(parts) == 4 and parts[:3] == ["api", "intelligence", "audit-runs"]:
                payload = self._audit_payload(parts[3])
            elif len(parts) == 4 and parts[:3] == ["api", "intelligence", "audit-findings"]:
                payload = self._audit_finding_payload(parts[3])
            elif len(parts) == 5 and parts[:3] == ["api", "intelligence", "assessments"] and parts[4] == "graph":
                payload = self._assessment_graph_payload(parts[3])
            elif len(parts) == 5 and parts[:3] == ["api", "intelligence", "assessments"] and parts[4] in {"validation", "validations"}:
                payload = self._assessment_validation_payload(parts[3])
            elif len(parts) == 5 and parts[:3] == ["api", "intelligence", "assessments"] and parts[4] == "admissions":
                payload = self._assessment_admissions_payload(parts[3])
            elif len(parts) == 5 and parts[:3] == ["api", "intelligence", "assessments"] and parts[4] in {"execution", "trace"}:
                payload = execution_payload(assessment_id=parts[3])
            elif len(parts) == 4 and parts[:3] == ["api", "intelligence", "admissions"]:
                payload = self._admission_payload(parts[3])
            elif len(parts) == 4 and parts[:3] == ["api", "intelligence", "candidates"]:
                candidate = INTELLIGENCE_AGENT.store.get_candidate(parts[3])
                if candidate is None:
                    raise IntelligenceNotFound(f"candidate not found: {parts[3]}")
                payload = {"ok": True, "candidate": INTELLIGENCE_AGENT._decorate_candidate(candidate), "reviews": INTELLIGENCE_AGENT.store.list_reviews(parts[3])}
            else:
                self._send_json({"ok": False, "error": "intelligence endpoint not found"}, 404)
                return
            self._send_json(payload)
        except Exception as exc:
            self._send_json({"ok": False, "error": str(exc)}, self._intelligence_error_status(exc))

    def handle_runtime_get(self, raw: str, query: dict[str, list[str]]) -> None:
        try:
            parts = [part for part in raw.split("/") if part]
            if parts == ["api", "runs"]:
                payload = {"ok": True, "runs": RUNTIME_STORE.list_runs(int((query.get("limit") or [100])[0]))}
            elif len(parts) == 3 and parts[:2] == ["api", "runs"]:
                run_id = parts[2]
                run = RUNTIME_STORE.get_run(run_id)
                if run is None:
                    raise NotFound(f"run not found: {run_id}")
                payload = {"ok": True, "run": run}
            elif len(parts) == 4 and parts[:2] == ["api", "runs"] and parts[3] == "events":
                run_id = parts[2]
                if RUNTIME_STORE.get_run(run_id) is None:
                    raise NotFound(f"run not found: {run_id}")
                payload = {
                    "ok": True,
                    "runId": run_id,
                    "events": RUNTIME_STORE.list_events(
                        run_id,
                        after_seq=int((query.get("afterSeq") or [0])[0]),
                        limit=int((query.get("limit") or [500])[0]),
                    ),
                }
            elif len(parts) == 4 and parts[:2] == ["api", "runs"] and parts[3] == "trace":
                payload = {"ok": True, **RUNTIME_STORE.trace(parts[2])}
            elif len(parts) == 4 and parts[:2] == ["api", "runs"] and parts[3] == "human-queue":
                if RUNTIME_STORE.get_run(parts[2]) is None:
                    raise NotFound(f"run not found: {parts[2]}")
                status = (query.get("status") or ["pending"])[0]
                payload = {"ok": True, "runId": parts[2], "reviews": RUNTIME_STORE.list_reviews(parts[2], status=status or None)}
            elif len(parts) == 3 and parts[:2] == ["api", "human-reviews"]:
                review = RUNTIME_STORE.get_review(parts[2])
                if review is None:
                    raise NotFound(f"human review not found: {parts[2]}")
                payload = {"ok": True, "review": review}
            else:
                self._send_json({"ok": False, "error": "runtime endpoint not found"}, 404)
                return
            self._send_json(payload)
        except Exception as exc:
            self._send_json({"ok": False, "error": str(exc)}, self._runtime_error_status(exc))

    def do_POST(self):
        parsed = urlsplit(self.path)
        raw = unquote(parsed.path)
        if raw.startswith("/api/ontology/"):
            self.handle_ontology_post(raw)
            return
        if raw == "/api/runs" or raw.startswith("/api/runs/") or raw.startswith("/api/human-reviews/"):
            self.handle_runtime_post(raw)
            return
        if raw.startswith("/api/intelligence/"):
            self.handle_intelligence_post(raw)
            return
        if raw.startswith("/api/osint/"):
            self.handle_osint_post(raw)
            return
        self._send_json({"ok": False, "error": "POST endpoint not found"}, 404)

    def handle_ontology_get(self, raw: str, query: dict[str, list[str]]) -> None:
        """Expose the reviewable workbook candidate layer, never published facts."""
        try:
            parts = [part for part in raw.split("/") if part]
            store = INTELLIGENCE_AGENT.store
            profiles=CapabilityProfiles(ontology_agent())
            audits=EntityAuditRuns(ontology_agent())
            orchestrator=MissionOrchestrator(ontology_agent())
            if parts in (['api','ontology','conversation-sessions'], ['api','ontology','conversations']):
                conversations=ConversationStore(ontology_agent())
                self._send_json({'ok':True,'sessions':conversations.list_sessions(entity_id=(query.get('entityId') or [None])[0],status=(query.get('status') or [None])[0],limit=(query.get('limit') or ['50'])[0])});return
            if len(parts)==4 and parts[:3] in (['api','ontology','conversation-sessions'], ['api','ontology','conversations']):
                conversations=ConversationStore(ontology_agent())
                include = (query.get('includeMessages') or ['false'])[0].lower() in {'1','true','yes'}
                self._send_json({'ok':True,'session':conversations.get_session(parts[3],include_messages=include,message_limit=(query.get('limit') or ['100'])[0])});return
            if len(parts)==5 and parts[:3] in (['api','ontology','conversation-sessions'], ['api','ontology','conversations']) and parts[4]=='messages':
                conversations=ConversationStore(ontology_agent())
                self._send_json({'ok':True,'messages':conversations.list_messages(parts[3],limit=(query.get('limit') or ['200'])[0],before_sequence=int((query.get('beforeSequence') or ['0'])[0]) or None,after_sequence=int((query.get('afterSequence') or ['0'])[0]) or None)});return
            if parts == ['api','ontology','agent-roles']:
                self._send_json({'ok':True,'roles':orchestrator.roles()});return
            if parts == ['api','ontology','analysis-runs']:
                self._send_json({'ok':True,'analysisRuns':orchestrator.list(entity_id=(query.get('entityId') or [None])[0],status=(query.get('status') or [None])[0],limit=(query.get('limit') or ['50'])[0])});return
            if len(parts)==4 and parts[:3]==['api','ontology','analysis-runs']:
                self._send_json({'ok':True,'analysisRun':orchestrator.get(parts[3])});return
            if len(parts)==5 and parts[:3]==['api','ontology','analysis-runs'] and parts[4]=='tasks':
                run=orchestrator.get(parts[3]); self._send_json({'ok':True,'tasks':run.get('tasks',[])});return
            if parts == ['api','ontology','audit-schedules']:
                self._send_json({'ok':True,'schedules':audits.list_schedules()});return
            if parts == ['api','ontology','audit-runs']:
                self._send_json({'ok':True,'auditRuns':audits.list(entity_id=(query.get('entityId') or [None])[0],status=(query.get('status') or [None])[0],limit=(query.get('limit') or ['50'])[0])});return
            if parts == ['api','ontology','audit-review-queue']:
                pending = audits.list(status='awaiting-review', limit=(query.get('limit') or ['50'])[0])
                pending += audits.list(status='completed-with-gaps', limit=(query.get('limit') or ['50'])[0])
                self._send_json({'ok':True,'auditRuns':pending});return
            if len(parts)==4 and parts[:3]==['api','ontology','audit-runs']:
                self._send_json({'ok':True,'auditRun':audits.get(parts[3])});return
            if len(parts)==5 and parts[:3]==['api','ontology','audit-runs'] and parts[4] in {'signals','observations','capability-claims','claims','evidence-bundles','verification-runs','signal-chain'}:
                kind = {'signals':'signals','observations':'observations','capability-claims':'claims','claims':'claims','evidence-bundles':'evidence-bundles','verification-runs':'verification-runs','signal-chain':None}[parts[4]]
                self._send_json({'ok':True, **audits.list_signal_records(audit_id=parts[3], kind=kind, limit=(query.get('limit') or ['100'])[0])});return
            if len(parts)==4 and parts[:3]==['api','ontology','verifications']:
                self._send_json({'ok':True,'verification':EquipmentVerificationQueue(store).get(parts[3])});return
            if len(parts)==5 and parts[:3]==['api','ontology','entities'] and parts[4] in {'audit-runs','audits'}:
                self._send_json({'ok':True,'auditRuns':audits.list(entity_id=parts[3],limit=(query.get('limit') or ['50'])[0])});return
            if len(parts)==5 and parts[:3]==['api','ontology','entities'] and parts[4] in {'signals','observations','capability-claims','claims','evidence-bundles','verification-runs','signal-chain'}:
                kind = {'signals':'signals','observations':'observations','capability-claims':'claims','claims':'claims','evidence-bundles':'evidence-bundles','verification-runs':'verification-runs','signal-chain':None}[parts[4]]
                self._send_json({'ok':True, **audits.list_signal_records(entity_id=parts[3], kind=kind, limit=(query.get('limit') or ['100'])[0])});return
            if parts == ['api','ontology','capability-batches'] or parts == ['api','ontology','enrichment-batches']:
                self._send_json({'ok':True,'batches':CapabilityWorkflow(ontology_agent()).list_batches(entity_id=(query.get('entityId') or [None])[0],limit=(query.get('limit') or ['20'])[0])});return
            if len(parts)==4 and parts[:3] in (['api','ontology','capability-batches'],['api','ontology','enrichment-batches']):
                self._send_json({'ok':True,'batch':CapabilityWorkflow(ontology_agent()).get_batch(parts[3])});return
            if len(parts)==5 and parts[:3]==['api','ontology','entities'] and parts[4]=='capability-runs':
                self._send_json({'ok':True,'runs':CapabilityWorkflow(ontology_agent()).list(parts[3])});return
            if len(parts)==6 and parts[:3]==['api','ontology','entities'] and parts[4:] in (['agent','runs'],['agent','enrichment-runs']):
                self._send_json({'ok':True,'runs':CapabilityWorkflow(ontology_agent()).list(parts[3])});return
            if len(parts)==4 and parts[:3]==['api','ontology','capability-runs']:
                self._send_json({'ok':True,'run':CapabilityWorkflow(ontology_agent()).get(parts[3])});return
            if len(parts)==5 and parts[:3]==['api','ontology','entities'] and parts[4] in {'manufacturing','production','costs'}:
                entity,_=ontology_agent()._entity_document(parts[3])
                result=ManufacturingStore(ontology_agent()).get(entity['entityId'])
                self._send_json({'ok':True, **result});return
            if len(parts)==5 and parts[:3]==['api','ontology','entities'] and parts[4]=='factor-graph':
                entity,_=ontology_agent()._entity_document(parts[3])
                graph_store=FactorGraphStore(ontology_agent().store)
                runs=graph_store.list_runs(entity['entityId'])
                run_id=(query.get('runId') or [None])[0]
                graph=graph_store.get(run_id) if run_id else (graph_store.get(runs[0]['graphRunId']) if runs else None)
                self._send_json({'ok':True,'graph':graph,'runs':runs});return
            if len(parts)==5 and parts[:3]==['api','ontology','entities'] and parts[4]=='manufacturing-research':
                self._send_json(ManufacturingResearchWorkflow(ontology_agent()).status(parts[3]));return
            if parts == ['api','ontology','manufacturing-candidates']:
                self._send_json({'ok':True,'candidates':ManufacturingStore(ontology_agent()).list_candidates((query.get('entityId') or [None])[0])});return
            if parts == ['api','ontology','manufacturing-extractions']:
                self._send_json({'ok':True,'candidates':ManufacturingStore(ontology_agent()).list_candidates((query.get('entityId') or [None])[0])});return
            if len(parts)==5 and parts[:3]==['api','ontology','entities'] and parts[4]=='capability-profile':
                self._send_json({'ok':True,'profile':profiles.read(parts[3])});return
            if len(parts)==5 and parts[:3]==['api','ontology','entities'] and parts[4]=='capability-profile-history':
                self._send_json({'ok':True,'history':profiles.history(parts[3],(query.get('attributeKey') or [None])[0])});return
            if len(parts)==5 and parts[:3]==['api','ontology','entities'] and parts[4]=='capability-publications':
                entity,_=profiles.agent._entity_document(parts[3])
                self._send_json({'ok':True,'publications':profiles.publications(entity['entityId'])});return
            if len(parts)==4 and parts[:3]==['api','ontology','capability-profiles']:
                self._send_json({'ok':True,'profile':profiles.get(parts[3])});return
            if parts==['api','ontology','capability-compare']:
                self._send_json({'ok':True,'comparison':profiles.compare((query.get('entityIds') or [''])[0].split(','),(query.get('attributeKey') or [''])[0])});return
            with store._connect() as con:
                if parts == ["api", "ontology", "agent", "state"]:
                    self._send_json(ontology_agent().state())
                    return
                if parts == ["api", "ontology", "trend-summary"]:
                    entity_rows = con.execute("SELECT entity_id,canonical_key,entity_type,preferred_name,english_name,category,subcategory,origin_country FROM ontology_entity ORDER BY preferred_name").fetchall()
                    revisions = con.execute("SELECT entity_id,attributes_json FROM ontology_entity_revision WHERE revision=(SELECT current_revision FROM ontology_entity e2 WHERE e2.entity_id=ontology_entity_revision.entity_id)").fetchall()
                    category_counts, ontology_layer_counts, ontology_class_counts, ontology_subclass_counts, country_counts, years = {}, {}, {}, {}, {}, {}
                    performance = {}
                    for entity in entity_rows:
                        taxonomy = ontology_taxonomy(entity["entity_type"], entity["category"], entity["subcategory"], entity["preferred_name"], entity["english_name"], entity["canonical_key"])
                        category = str(entity["category"] or taxonomy["ontologyClassLabel"])
                        country = str(entity["origin_country"] or "未知")
                        category_counts[category] = category_counts.get(category, 0) + 1
                        layer = taxonomy["ontologyLayerLabel"]
                        ontology_layer_counts[layer] = ontology_layer_counts.get(layer, 0) + 1
                        ontology_class = taxonomy["ontologyClassLabel"]
                        ontology_class_counts[ontology_class] = ontology_class_counts.get(ontology_class, 0) + 1
                        ontology_subclass = taxonomy.get("equipmentSubclassLabel") or ontology_class
                        ontology_subclass_counts[ontology_subclass] = ontology_subclass_counts.get(ontology_subclass, 0) + 1
                        country_counts[country] = country_counts.get(country, 0) + 1
                        revision = next((row for row in revisions if row["entity_id"] == entity["entity_id"]), None)
                        if revision is None:
                            continue
                        try:
                            attributes = (json.loads(revision["attributes_json"]) or {}).get("attributes") or {}
                        except (TypeError, ValueError):
                            attributes = {}
                        def value(key):
                            bucket = attributes.get(key) or {}
                            values = bucket.get("values") or []
                            return values[0] if values else {}
                        first_flight = value("first-flight-year").get("normalizedValue")
                        try:
                            year = int(first_flight)
                            if 1900 <= year <= 2100:
                                years[str(year)] = years.get(str(year), 0) + 1
                        except (TypeError, ValueError):
                            pass
                        bucket = performance.setdefault(category, {"category": category, "models": 0, "endurance": [], "speed": [], "payload": []})
                        bucket["models"] += 1
                        for key, target, unit in (("endurance", "endurance", "h"), ("max-speed", "speed", "km/h"), ("payload-capacity", "payload", "kg")):
                            item = value(key)
                            number = item.get("normalizedValue")
                            if isinstance(number, (int, float)) and not isinstance(number, bool):
                                source_unit = str(item.get("unit") or "")
                                if key == "max-speed" and source_unit.lower() in {"kt", "kts", "ktas"}:
                                    number = number * 1.852
                                elif key == "payload-capacity" and source_unit.lower() in {"lb", "lbs", "pound", "pounds"}:
                                    number = number * 0.45359237
                                elif source_unit and source_unit.lower() != unit.lower():
                                    continue
                                bucket[target].append(float(number))
                    performance_rows = []
                    for row in performance.values():
                        performance_rows.append({"category": row["category"], "models": row["models"], "avgEndurance": round(sum(row["endurance"]) / len(row["endurance"]), 1) if row["endurance"] else None, "avgSpeed": round(sum(row["speed"]) / len(row["speed"]), 1) if row["speed"] else None, "avgPayload": round(sum(row["payload"]) / len(row["payload"]), 1) if row["payload"] else None})
                    summary = {"entityCount": len(entity_rows), "categoryCounts": [{"label": key, "count": value} for key, value in sorted(category_counts.items(), key=lambda item: (-item[1], item[0]))], "ontologyLayerCounts": [{"label": key, "count": value} for key, value in sorted(ontology_layer_counts.items(), key=lambda item: (-item[1], item[0]))], "ontologyClassCounts": [{"label": key, "count": value} for key, value in sorted(ontology_class_counts.items(), key=lambda item: (-item[1], item[0]))], "ontologySubclassCounts": [{"label": key, "count": value} for key, value in sorted(ontology_subclass_counts.items(), key=lambda item: (-item[1], item[0]))], "countryCounts": [{"label": key, "count": value} for key, value in sorted(country_counts.items(), key=lambda item: (-item[1], item[0]))], "firstFlightByYear": [{"label": key, "count": value} for key, value in sorted(years.items())], "performanceByCategory": sorted(performance_rows, key=lambda row: (-row["models"], row["category"]))}
                    self._send_json({"ok": True, "summary": summary})
                    return
                if parts == ["api", "ontology", "imports"]:
                    rows = con.execute("SELECT snapshot_id,file_name,file_hash,source_version,workbook_schema_version,parser_version,row_count,status,error_message,imported_at,completed_at FROM ontology_import_snapshot ORDER BY imported_at DESC").fetchall()
                    imports = [dict(row) for row in rows]
                    for row in imports:
                        row["snapshotId"] = row.pop("snapshot_id")
                        row["fileName"] = row.pop("file_name")
                        row["fileHash"] = row.pop("file_hash")
                        row["sourceVersion"] = row.pop("source_version")
                        row["workbookSchemaVersion"] = row.pop("workbook_schema_version")
                        row["parserVersion"] = row.pop("parser_version")
                        row["rowCount"] = row.pop("row_count")
                        row["errorMessage"] = row.pop("error_message")
                        row["importedAt"] = row.pop("imported_at")
                        row["completedAt"] = row.pop("completed_at")
                    self._send_json({"ok": True, "imports": imports})
                    return
                if parts == ["api", "ontology", "verifications"]:
                    entity_id = (query.get("entityId") or [None])[0]
                    status = (query.get("status") or [None])[0]
                    self._send_json({"ok": True, "verifications": EquipmentVerificationQueue(store).list(entity_id=entity_id, status=status)})
                    return
                if len(parts) == 4 and parts[:3] == ["api", "ontology", "verifications"]:
                    self._send_json({"ok": True, "verification": EquipmentVerificationQueue(store).get(parts[3])})
                    return
                if len(parts) == 5 and parts[:3] == ["api", "ontology", "entities"] and parts[4] == "agent":
                    self._send_json(ontology_agent().object_brief(parts[3]))
                    return
                if len(parts) == 6 and parts[:3] == ["api", "ontology", "entities"] and parts[4:] == ["agent", "candidates"]:
                    self._send_json({"ok": True, "candidates": ontology_agent().list_attribute_candidates(parts[3])})
                    return
                if len(parts) == 6 and parts[:3] == ["api", "ontology", "entities"] and parts[4:] in (["agent", "relation-candidates"], ["agent", "relations"]):
                    self._send_json({"ok": True, "relationCandidates": ontology_agent().list_relation_candidates(parts[3])})
                    return
                if len(parts) == 5 and parts[:3] == ["api", "ontology", "entities"] and parts[4] == "sources":
                    entity_id = parts[3]
                    entity = con.execute("SELECT entity_id FROM ontology_entity WHERE entity_id=? OR canonical_key=?", (entity_id, entity_id)).fetchone()
                    if entity is None:
                        raise IntelligenceNotFound(f"ontology entity not found: {entity_id}")
                    rows = con.execute("SELECT source_reference_id,source_scope,subject_name,source_name,source_url,citation_text,sheet_name,row_number,cell_range,details_json,source_reliability,verification_status,review_status,match_status,created_at FROM ontology_source_reference WHERE entity_id=? ORDER BY source_scope,sheet_name,row_number", (entity[0],)).fetchall()
                    sources = []
                    for row in rows:
                        item = dict(row)
                        item["sourceReferenceId"] = item.pop("source_reference_id")
                        item["sourceScope"] = item.pop("source_scope")
                        item["subjectName"] = item.pop("subject_name")
                        item["sourceName"] = item.pop("source_name")
                        item["sourceUrl"] = item.pop("source_url")
                        item["citationText"] = item.pop("citation_text")
                        item["sheetName"] = item.pop("sheet_name")
                        item["rowNumber"] = item.pop("row_number")
                        item["cellRange"] = item.pop("cell_range")
                        item["details"] = json.loads(item.pop("details_json") or "{}")
                        item["sourceReliability"] = item.pop("source_reliability")
                        item["verificationStatus"] = item.pop("verification_status")
                        item["reviewStatus"] = item.pop("review_status")
                        item["matchStatus"] = item.pop("match_status")
                        item["createdAt"] = item.pop("created_at")
                        sources.append(item)
                    self._send_json({"ok": True, "entityId": entity[0], "sources": sources})
                    return
                if len(parts) == 5 and parts[:3] == ["api", "ontology", "entities"] and parts[4] == "relations":
                    """Return source-qualified relation assertions for an entity.

                    The workbook importer stores lineage separately because it
                    has a dedicated review workflow.  Model/import relations
                    live in ``ontology_relation_assertion``; expose both end
                    points here so the dossier can show incoming and outgoing
                    claims without making the browser reconstruct SQL joins.
                    This endpoint is read-only and deliberately returns the
                    review status and evidence ids unchanged.
                    """
                    entity_id = parts[3]
                    entity = con.execute(
                        "SELECT entity_id FROM ontology_entity WHERE entity_id=? OR canonical_key=?",
                        (entity_id, entity_id),
                    ).fetchone()
                    if entity is None:
                        raise IntelligenceNotFound(f"ontology entity not found: {entity_id}")
                    rows = con.execute(
                        """SELECT r.relation_id,r.from_entity_id,r.to_entity_id,r.predicate,
                                  r.qualifier,r.valid_time,r.evidence_ids,r.claim_kind,
                                  r.confidence,r.review_status,r.created_at,
                                  f.preferred_name AS from_name,t.preferred_name AS to_name
                           FROM ontology_relation_assertion r
                           JOIN ontology_entity f ON f.entity_id=r.from_entity_id
                           JOIN ontology_entity t ON t.entity_id=r.to_entity_id
                           WHERE r.from_entity_id=? OR r.to_entity_id=?
                           ORDER BY r.review_status, r.predicate, r.relation_id""",
                        (entity[0], entity[0]),
                    ).fetchall()
                    relations = []
                    for row in rows:
                        item = dict(row)
                        item["relationId"] = item.pop("relation_id")
                        item["fromEntityId"] = item.pop("from_entity_id")
                        item["toEntityId"] = item.pop("to_entity_id")
                        item["fromName"] = item.pop("from_name")
                        item["toName"] = item.pop("to_name")
                        item["validTime"] = item.pop("valid_time")
                        item["evidenceIds"] = json.loads(item.pop("evidence_ids") or "[]")
                        item["qualifier"] = json.loads(item.pop("qualifier") or "{}") if isinstance(item.get("qualifier"), str) else (item.get("qualifier") or {})
                        item["claimKind"] = item.pop("claim_kind")
                        item["reviewStatus"] = item.pop("review_status")
                        item["createdAt"] = item.pop("created_at")
                        item["direction"] = "outgoing" if item["fromEntityId"] == entity[0] else "incoming"
                        relations.append(item)
                    self._send_json({"ok": True, "entityId": entity[0], "relations": relations})
                    return
                if len(parts) == 5 and parts[:3] == ["api", "ontology", "entities"] and parts[4] == "lineage":
                    entity_id = parts[3]
                    entity = con.execute("SELECT entity_id FROM ontology_entity WHERE entity_id=? OR canonical_key=?", (entity_id, entity_id)).fetchone()
                    if entity is None:
                        raise IntelligenceNotFound(f"ontology entity not found: {entity_id}")
                    rows = con.execute("SELECT lineage_edge_id,family_name,sequence_no,predecessor_entity_id,successor_entity_id,predecessor_name,successor_name,raw_chain,qualifier_json,match_status,review_status,created_at FROM ontology_lineage_edge WHERE predecessor_entity_id=? OR successor_entity_id=? ORDER BY family_name,sequence_no", (entity[0], entity[0])).fetchall()
                    lineage = []
                    for row in rows:
                        item = dict(row)
                        item["lineageEdgeId"] = item.pop("lineage_edge_id")
                        item["familyName"] = item.pop("family_name")
                        item["sequenceNo"] = item.pop("sequence_no")
                        item["predecessorEntityId"] = item.pop("predecessor_entity_id")
                        item["successorEntityId"] = item.pop("successor_entity_id")
                        item["predecessorName"] = item.pop("predecessor_name")
                        item["successorName"] = item.pop("successor_name")
                        item["rawChain"] = item.pop("raw_chain")
                        item["qualifier"] = json.loads(item.pop("qualifier_json") or "{}")
                        item["matchStatus"] = item.pop("match_status")
                        item["reviewStatus"] = item.pop("review_status")
                        item["createdAt"] = item.pop("created_at")
                        lineage.append(item)
                    self._send_json({"ok": True, "entityId": entity[0], "lineage": lineage})
                    return
                if parts == ["api", "ontology", "entities"]:
                    limit = min(max(int((query.get("limit") or ["100"])[0]), 1), 500)
                    rows = con.execute("SELECT entity_id,canonical_key,entity_type,preferred_name,english_name,category,subcategory,lifecycle_status,wikidata_id,current_revision,review_status,origin_country,manufacturer,user_countries_json,combat_status,updated_at FROM ontology_entity ORDER BY preferred_name LIMIT ?", (limit,)).fetchall()
                    entities = []
                    for row in rows:
                        item = dict(row)
                        item.update(ontology_taxonomy(item["entity_type"], item["category"], item["subcategory"], item["preferred_name"], item["english_name"], item["canonical_key"]))
                        item["entityId"] = item.pop("entity_id")
                        item["canonicalKey"] = item.pop("canonical_key")
                        item["entityType"] = item.pop("entity_type")
                        item["preferredName"] = item.pop("preferred_name")
                        item["englishName"] = item.pop("english_name")
                        item["lifecycleStatus"] = item.pop("lifecycle_status")
                        item["wikidataId"] = item.pop("wikidata_id")
                        item["currentRevision"] = item.pop("current_revision")
                        item["reviewStatus"] = item.pop("review_status")
                        item["originCountry"] = item.pop("origin_country")
                        item["manufacturer"] = item.pop("manufacturer")
                        item["userCountries"] = json.loads(item.pop("user_countries_json") or "[]")
                        item["combatStatus"] = item.pop("combat_status")
                        item["updatedAt"] = item.pop("updated_at")
                        entities.append(item)
                    self._send_json({"ok": True, "entities": entities})
                    return
                if parts == ["api", "ontology", "tree"]:
                    """Return the ontology navigation tree and its leaf entities.

                    The existing ``/entities`` endpoint remains the compact
                    list used by older clients.  This read model preserves the
                    ontology layer/class/subclass hierarchy so a tree client
                    can navigate directly to an entity dossier without
                    reimplementing taxonomy grouping in JavaScript.
                    """
                    rows = con.execute(
                        """SELECT entity_id,canonical_key,entity_type,preferred_name,
                                  english_name,category,subcategory,current_revision,
                                  review_status,updated_at
                           FROM ontology_entity ORDER BY preferred_name"""
                    ).fetchall()
                    layers = {}
                    for row in rows:
                        taxonomy = ontology_taxonomy(row["entity_type"], row["category"], row["subcategory"], row["preferred_name"], row["english_name"], row["canonical_key"])
                        layer = layers.setdefault(taxonomy["ontologyLayer"], {"key": taxonomy["ontologyLayer"], "label": taxonomy["ontologyLayerLabel"], "classes": {}})
                        cls = layer["classes"].setdefault(taxonomy["ontologyClass"], {"key": taxonomy["ontologyClass"], "label": taxonomy["ontologyClassLabel"], "subclasses": {}, "entities": []})
                        subclass_key = taxonomy.get("equipmentSubclass")
                        target = cls
                        if subclass_key:
                            target = cls["subclasses"].setdefault(subclass_key, {"key": subclass_key, "label": taxonomy.get("equipmentSubclassLabel") or subclass_key, "entities": []})
                        target["entities"].append({"entityId": row["entity_id"], "canonicalKey": row["canonical_key"], "preferredName": row["preferred_name"], "englishName": row["english_name"], "currentRevision": row["current_revision"], "reviewStatus": row["review_status"], "updatedAt": row["updated_at"], **taxonomy})
                    tree = []
                    for layer in layers.values():
                        classes = []
                        for cls in layer["classes"].values():
                            subclasses = list(cls.pop("subclasses", {}).values())
                            classes.append({**cls, "subclasses": sorted(subclasses, key=lambda value: value["label"])})
                        tree.append({"key": layer["key"], "label": layer["label"], "classes": sorted(classes, key=lambda value: value["label"])})
                    tree.sort(key=lambda value: value["label"])
                    self._send_json({"ok": True, "tree": tree, "entityCount": len(rows)})
                    return
                if len(parts) == 5 and parts[:3] == ["api", "ontology", "entities"] and parts[4] == "attributes":
                    entity_id = parts[3]
                    entity = con.execute("SELECT entity_id,current_revision FROM ontology_entity WHERE entity_id=? OR canonical_key=?", (entity_id, entity_id)).fetchone()
                    if entity is None:
                        raise IntelligenceNotFound(f"ontology entity not found: {entity_id}")
                    requested_revision = (query.get("revision") or [None])[0]
                    revision = int(requested_revision) if requested_revision else int(entity["current_revision"])
                    row = con.execute("SELECT entity_revision_id,revision,attributes_json,attributes_schema_version,attributes_sha256,source_snapshot_id,source_record_id,review_status,created_at FROM ontology_entity_revision WHERE entity_id=? AND revision=?", (entity["entity_id"], revision)).fetchone()
                    if row is None:
                        raise IntelligenceNotFound(f"ontology revision not found: {entity_id}@{revision}")
                    item = dict(row)
                    item["entityRevisionId"] = item.pop("entity_revision_id")
                    item["attributes"] = json.loads(item.pop("attributes_json"))
                    item["attributesSchemaVersion"] = item.pop("attributes_schema_version")
                    item["attributesSha256"] = item.pop("attributes_sha256")
                    item["sourceSnapshotId"] = item.pop("source_snapshot_id")
                    item["sourceRecordId"] = item.pop("source_record_id")
                    item["reviewStatus"] = item.pop("review_status")
                    item["createdAt"] = item.pop("created_at")
                    self._send_json({"ok": True, "entityId": entity["entity_id"], "revision": item})
                    return
                if len(parts) == 4 and parts[:3] == ["api", "ontology", "entities"]:
                    entity_id = parts[3]
                    entity = con.execute("SELECT * FROM ontology_entity WHERE entity_id=? OR canonical_key=?", (entity_id, entity_id)).fetchone()
                    if entity is None:
                        raise IntelligenceNotFound(f"ontology entity not found: {entity_id}")
                    revisions = con.execute("SELECT entity_revision_id,revision,attributes_json,attributes_schema_version,attributes_sha256,source_snapshot_id,source_record_id,review_status,created_at FROM ontology_entity_revision WHERE entity_id=? ORDER BY revision DESC", (entity["entity_id"],)).fetchall()
                    aliases = con.execute("SELECT alias,alias_type,language,confidence,review_status FROM ontology_entity_alias WHERE entity_id=? ORDER BY alias", (entity["entity_id"],)).fetchall()
                    payload = dict(entity)
                    payload.update(ontology_taxonomy(payload["entity_type"], payload["category"], payload["subcategory"], payload["preferred_name"], payload["english_name"], payload["canonical_key"]))
                    payload["entityId"] = payload.pop("entity_id")
                    payload["canonicalKey"] = payload.pop("canonical_key")
                    payload["entityType"] = payload.pop("entity_type")
                    payload["preferredName"] = payload.pop("preferred_name")
                    payload["englishName"] = payload.pop("english_name")
                    payload["lifecycleStatus"] = payload.pop("lifecycle_status")
                    payload["wikidataId"] = payload.pop("wikidata_id")
                    payload["currentRevision"] = payload.pop("current_revision")
                    payload["reviewStatus"] = payload.pop("review_status")
                    payload["sourceSnapshotIds"] = json.loads(payload.pop("source_snapshot_ids") or "[]")
                    payload["userCountries"] = json.loads(payload.pop("user_countries_json") or "[]")
                    payload["originCountry"] = payload.pop("origin_country")
                    payload["manufacturer"] = payload.pop("manufacturer")
                    payload["combatStatus"] = payload.pop("combat_status")
                    payload["updatedAt"] = payload.pop("updated_at")
                    payload["createdAt"] = payload.pop("created_at")
                    payload["aliases"] = [dict(row) for row in aliases]
                    payload["revisions"] = []
                    for revision in revisions:
                        item = dict(revision)
                        item["entityRevisionId"] = item.pop("entity_revision_id")
                        item["attributesSchemaVersion"] = item.pop("attributes_schema_version")
                        item["attributesSha256"] = item.pop("attributes_sha256")
                        item["sourceSnapshotId"] = item.pop("source_snapshot_id")
                        item["sourceRecordId"] = item.pop("source_record_id")
                        item["attributes"] = json.loads(item.pop("attributes_json"))
                        item["reviewStatus"] = item.pop("review_status")
                        item["createdAt"] = item.pop("created_at")
                        payload["revisions"].append(item)
                    self._send_json({"ok": True, "entity": payload})
                    return
            self._send_json({"ok": False, "error": "ontology endpoint not found"}, 404)
        except Exception as exc:
            self._send_json({"ok": False, "error": str(exc)}, self._intelligence_error_status(exc))

    def handle_ontology_post(self, raw: str) -> None:
        try:
            parts = [part for part in raw.split("/") if part]
            payload = self._read_json()
            profiles=CapabilityProfiles(ontology_agent())
            audits=EntityAuditRuns(ontology_agent())
            orchestrator=MissionOrchestrator(ontology_agent())
            if parts in (['api','ontology','conversation-sessions'], ['api','ontology','conversations']):
                conversations=ConversationStore(ontology_agent())
                self._send_json({'ok':True,'session':conversations.create_session(payload)}, 201);return
            if len(parts)==5 and parts[:3] in (['api','ontology','conversation-sessions'], ['api','ontology','conversations']):
                conversations=ConversationStore(ontology_agent())
                session_id, action = parts[3], parts[4]
                if action == 'messages': result={'message': conversations.add_message(session_id,payload)}
                elif action == 'close': result={'session': conversations.close_session(session_id,status=str(payload.get('status') or 'closed'))}
                elif action == 'restore': result={'session': conversations.restore_session(session_id)}
                elif action == 'archive': result={'session': conversations.archive_session(session_id)}
                elif action == 'clear-display': result={'session': conversations.clear_display(session_id)}
                elif action == 'bind-run': result={'session': conversations.bind_run(session_id,str(payload.get('analysisRunId') or ''),prompt=payload.get('prompt'),intent=payload.get('intent') if isinstance(payload.get('intent'),dict) else None)}
                elif action == 'prompt': result=conversations.submit_prompt(session_id,payload,orchestrator)
                else: result=None
                if result is not None: self._send_json({'ok':True,**result}, 202 if action=='prompt' else (201 if action=='messages' else 200));return
            if parts == ['api','ontology','analysis-runs']:
                entity_id = payload.get('entityId')
                self._send_json({'ok':True,'analysisRun':orchestrator.create(payload, entity_id=entity_id)}, 202);return
            if len(parts)==5 and parts[:3]==['api','ontology','analysis-runs']:
                run_id, action = parts[3], parts[4]
                if action == 'start' or action == 'resume': result=orchestrator.start(run_id)
                elif action == 'pause': result=orchestrator.pause(run_id, str(payload.get('reason') or 'operator paused'))
                elif action == 'cancel': result=orchestrator.cancel(run_id, str(payload.get('reason') or 'operator cancelled'))
                elif action == 'retry': result=orchestrator.retry(run_id, task_id=payload.get('taskId'), request=payload)
                elif action == 'claim': result={'tasks': orchestrator.claim_ready_tasks(run_id, limit=payload.get('limit'), worker_id=str(payload.get('workerId') or 'orchestrator'))}
                elif action == 'handoffs': result={'handoff': orchestrator.handoff(str(payload.get('fromTaskId') or ''), str(payload.get('toTaskId') or ''), artifact_id=payload.get('artifactId'), payload=payload.get('payload') or {})}
                elif action == 'execute':
                    # The adapter runs the existing Firecrawl/DeepSeek audit
                    # synchronously inside a daemon worker while the HTTP
                    # request returns the durable queued/running envelope.
                    run = orchestrator.get(run_id)
                    entity_id = run.get('entityId')
                    if not entity_id:
                        raise IntelligenceValidationError('analysis run has no entityId')
                    conversation_store = None
                    if run.get('request', {}).get('conversationSessionId') or run.get('request', {}).get('sessionId') or payload.get('conversationSessionId') or payload.get('sessionId'):
                        conversation_store = ConversationStore(ontology_agent())
                    bridge = EntityAuditExecutionBridge(orchestrator, audits, conversation_store)
                    def _execute_analysis_run():
                        try:
                            bridge.execute_existing(run_id, entity_id, {**(run.get('request') or {}), **payload}, envelope=run)
                        except Exception as exc:
                            # Keep failures visible in the orchestration DB;
                            # candidate artifacts remain review-gated.
                            try:
                                with orchestrator._lock, orchestrator.store._connect() as con:
                                    stamp = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
                                    con.execute("UPDATE ontology_analysis_run SET status='failed',error_message=?,stop_reason='execution-failed',updated_at=?,completed_at=? WHERE analysis_run_id=? AND status NOT IN ('completed','completed-with-gaps','cancelled')", (str(exc)[:1000], stamp, stamp, run_id))
                                    orchestrator._event_tx(con, run_id, 'run-failed', {'error': str(exc)[:500], 'bridge': True})
                            except Exception:
                                pass
                    threading.Thread(target=_execute_analysis_run, daemon=True, name=f'analysis-{run_id}').start()
                    result = {**run, 'dispatch': 'queued'}
                else: result=None
                if result is not None: self._send_json({'ok':True, 'analysisRun':result} if action not in {'claim','handoffs'} else {'ok':True, **result}, 202);return
            if len(parts)==5 and parts[:3]==['api','ontology','agent-tasks']:
                task_id, action = parts[3], parts[4]
                if action == 'complete': result=orchestrator.complete_task(task_id, payload.get('output') or {}, worker_id=payload.get('workerId'))
                elif action == 'fail': result=orchestrator.fail_task(task_id, str(payload.get('error') or 'agent task failed'), retry=bool(payload.get('retry', True)), worker_id=payload.get('workerId'))
                elif action == 'artifact': result=orchestrator.add_artifact(task_id, str(payload.get('artifactType') or ''), payload.get('content') or {}, status=str(payload.get('status') or 'under-review'))
                else: result=None
                if result is not None: self._send_json({'ok':True, 'task':result} if action != 'artifact' else {'ok':True, 'artifact':result}, 200);return
            if parts == ['api','ontology','audit-schedules']:
                self._send_json({'ok':True,'schedule':audits.create_schedule(payload)},201);return
            if parts == ['api','ontology','verification-recovery']:
                recovered = EquipmentVerificationQueue(INTELLIGENCE_AGENT.store).reclaim_stale(payload.get('maxAgeSeconds'))
                self._send_json({'ok':True,'recovered':recovered,'count':len(recovered)},200);return
            if len(parts)==5 and parts[:3]==['api','ontology','audit-schedules'] and parts[4]=='run':
                self._send_json({'ok':True,'scheduler':audits.run_schedule(parts[3])},202);return
            if parts == ['api','ontology','audit-scheduler','run-due']:
                self._send_json({'ok':True,'scheduler':audits.run_due_schedules()},202);return
            if len(parts)==5 and parts[:3]==['api','ontology','entities'] and parts[4] in {'audit-runs','audits'}:
                self._send_json({'ok':True,'auditRun':audits.submit(payload,parts[3])},202);return
            if len(parts)==5 and parts[:3]==['api','ontology','entities'] and parts[4] in {'signal-observations','observations','emitter-signatures','capability-claims','claims','evidence-bundles','verification-runs'}:
                kind = {'signal-observations':'SignalObservation','observations':'SignalObservation','emitter-signatures':'EmitterSignature','capability-claims':'CapabilityClaim','claims':'CapabilityClaim','evidence-bundles':'EvidenceBundle','verification-runs':'VerificationRun'}[parts[4]]
                self._send_json(audits.create_signal_record(parts[3], kind, payload), 201);return
            if parts == ['api','ontology','audit-runs']:
                entity_ids = payload.get('entityIds') or payload.get('entities') or []
                self._send_json({'ok':True,**audits.submit_batch(payload,entity_ids)},202);return
            if len(parts)==4 and parts[:3]==['api','ontology','audit-runs']:
                self._send_json({'ok':True,'auditRun':audits.retry(parts[3],payload)},202);return
            if len(parts)==5 and parts[:3]==['api','ontology','audit-runs'] and parts[4]=='cancel':
                self._send_json({'ok':True,'auditRun':audits.cancel(parts[3],str(payload.get('reason') or 'operator cancelled'))},200);return
            if len(parts)==5 and parts[:3]==['api','ontology','audit-runs'] and parts[4]=='publish':
                self._send_json(audits.publish(parts[3],payload),200);return
            if len(parts)==5 and parts[:3]==['api','ontology','audit-runs'] and parts[4]=='review':
                self._send_json(audits.review(parts[3],str(payload.get('candidateType') or ''),str(payload.get('candidateId') or ''),payload),200);return
            if len(parts)==5 and parts[:3]==['api','ontology','audit-runs'] and parts[4] in {'signal-observations','observations','emitter-signatures','capability-claims','claims','evidence-bundles','verification-runs'}:
                audit = audits.get(parts[3])
                kind = {'signal-observations':'SignalObservation','observations':'SignalObservation','emitter-signatures':'EmitterSignature','capability-claims':'CapabilityClaim','claims':'CapabilityClaim','evidence-bundles':'EvidenceBundle','verification-runs':'VerificationRun'}[parts[4]]
                self._send_json(audits.create_signal_record(audit['entityId'], kind, payload, audit_id=parts[3]), 201);return
            if parts == ['api','ontology','audit-scheduler','run']:
                self._send_json({'ok':True,'scheduler':audits.run_periodic(payload)},202);return
            if len(parts)==5 and parts[:3]==['api','ontology','entities'] and parts[4]=='capability-runs':
                self._send_json({'ok':True,'run':CapabilityWorkflow(ontology_agent()).submit(payload,parts[3])},202);return
            if parts in (['api','ontology','capability-batches'],['api','ontology','enrichment-batches']):
                self._send_json({'ok':True,'batch':CapabilityWorkflow(ontology_agent()).submit_batch(payload)},202);return
            if len(parts)==6 and parts[:3]==['api','ontology','entities'] and parts[4:] in (['agent','enrich'],['agent','research'],['agent','enrichment'],['agent','run']):
                self._send_json({'ok':True,'run':CapabilityWorkflow(ontology_agent()).submit(payload,parts[3])},202);return
            if len(parts)==5 and parts[:3]==['api','ontology','entities'] and parts[4]=='capability-profile':
                self._send_json(profiles.save(parts[3]),201);return
            if len(parts)==5 and parts[:3]==['api','ontology','capability-profiles'] and parts[4] in {'review','publish'}:
                result=profiles.review(parts[3],payload) if parts[4]=='review' else profiles.publish(parts[3],payload)
                self._send_json(result,200);return
            if len(parts)==5 and parts[:3]==['api','ontology','entities'] and parts[4] in {'production','costs'}:
                entity,_=ontology_agent()._entity_document(parts[3])
                store=ManufacturingStore(ontology_agent())
                result=store.create_production(entity['entityId'],payload) if parts[4]=='production' else store.create_cost(entity['entityId'],payload)
                self._send_json({'ok':True,'record':result},201);return
            if len(parts)==5 and parts[:3]==['api','ontology','entities'] and parts[4]=='manufacturing-candidates':
                entity,_=ontology_agent()._entity_document(parts[3]); candidate_type=str(payload.get('candidateType') or payload.get('type') or '').strip()
                result=ManufacturingStore(ontology_agent()).create_candidate(entity['entityId'],candidate_type,payload.get('payload') if isinstance(payload.get('payload'),dict) else payload)
                self._send_json({'ok':True,'candidate':result},201);return
            if len(parts)==6 and parts[:3]==['api','ontology','entities'] and parts[4:] == ['manufacturing-research','collect']:
                result=ManufacturingResearchWorkflow(ontology_agent()).collect(parts[3],payload)
                self._send_json(result,200);return
            if len(parts)==6 and parts[:3]==['api','ontology','entities'] and parts[4:] == ['manufacturing-research','extract']:
                result=ManufacturingResearchWorkflow(ontology_agent()).extract(parts[3])
                self._send_json(result,200);return
            if len(parts)==5 and parts[:3]==['api','ontology','manufacturing-candidates'] and parts[4] in {'review','publish'}:
                store=ManufacturingStore(ontology_agent())
                result=store.review_candidate(parts[3],payload) if parts[4]=='review' else store.publish_candidate(parts[3],payload)
                self._send_json({'ok':True, **({'candidate':result} if parts[4]=='review' else result)},200);return
            if parts == ['api','ontology','manufacturing-extractions']:
                result=ManufacturingStore(ontology_agent()).ingest_extraction(payload)
                self._send_json({'ok':True,**result},201);return
            if parts == ["api", "ontology", "agent", "query"]:
                self._send_json(ontology_agent().query(payload), 200)
                return
            if len(parts) == 6 and parts[:3] == ["api", "ontology", "entities"] and parts[4:] == ["agent", "actions"]:
                self._send_json(ontology_agent().object_action(parts[3], payload), 200)
                return
            if len(parts) == 6 and parts[:3] == ["api", "ontology", "entities"] and parts[4:] == ["agent", "attribute-plan"]:
                self._send_json(ontology_agent().attribute_plan(parts[3], payload), 200)
                return
            if len(parts) == 6 and parts[:3] == ["api", "ontology", "entities"] and parts[4:] == ["agent", "collect"]:
                self._send_json(ontology_agent().collect_attribute_evidence(parts[3], payload), 200)
                return
            if len(parts) == 6 and parts[:3] == ["api", "ontology", "entities"] and parts[4:] == ["agent", "extract"]:
                self._send_json(ontology_agent().extract_attribute_candidates(parts[3], payload), 200)
                return
            if len(parts) == 5 and parts[:3] == ["api", "ontology", "attribute-candidates"] and parts[4] == "review":
                self._send_json(ontology_agent().review_attribute_candidate(parts[3], payload), 200)
                return
            if len(parts) == 5 and parts[:3] == ["api", "ontology", "relation-candidates"] and parts[4] == "review":
                self._send_json(ontology_agent().review_relation_candidate(parts[3], payload), 200)
                return
            if len(parts) == 5 and parts[:3] == ["api", "ontology", "relation-candidates"] and parts[4] == "publish":
                self._send_json(ontology_agent().publish_relation_candidate(parts[3], payload), 200)
                return
            if len(parts) == 5 and parts[:3] == ["api", "ontology", "entities"] and parts[4] == "verification-plan":
                jobs = EquipmentVerificationQueue(INTELLIGENCE_AGENT.store).plan(parts[3], force=bool(payload.get("force")))
                self._send_json({"ok": True, "verifications": jobs}, 201)
                return
            if len(parts) == 5 and parts[:3] == ["api", "ontology", "verifications"] and parts[4] == "execute":
                job = EquipmentVerificationQueue(INTELLIGENCE_AGENT.store).execute(parts[3])
                self._send_json({"ok": True, "verification": job}, 200)
                return
            if parts != ["api", "ontology", "import"]:
                self._send_json({"ok": False, "error": "ontology endpoint not found"}, 404)
                return
            source_path = str(payload.get("sourcePath") or payload.get("workbookPath") or "").strip()
            if not source_path:
                raise IntelligenceValidationError("sourcePath or workbookPath is required")
            if Path(source_path).suffix.casefold() == ".json":
                try:
                    from ontology_json_import import import_agent_result
                except ModuleNotFoundError:
                    from .ontology_json_import import import_agent_result
                result = import_agent_result(source_path, INTELLIGENCE_AGENT.store.path)
                self._send_json({"ok": True, "import": result}, 200 if result.get("status") == "duplicate" else 201)
                return
            try:
                from ontology_workbook_import import import_workbook
            except ModuleNotFoundError:
                from .ontology_workbook_import import import_workbook
            result = import_workbook(source_path, INTELLIGENCE_AGENT.store.path)
            self._send_json({"ok": True, "import": result}, 200 if result.get("status") == "duplicate" else 201)
        except Exception as exc:
            self._send_json({"ok": False, "error": str(exc)}, self._intelligence_error_status(exc))

    def handle_runtime_post(self, raw: str) -> None:
        try:
            payload = self._read_json()
            parts = [part for part in raw.split("/") if part]
            if parts == ["api", "runs"]:
                run = RUNTIME_STORE.create_run(payload)
                self._send_json({"ok": True, "run": run}, 201)
                return
            if len(parts) == 4 and parts[:2] == ["api", "runs"] and parts[3] == "events":
                event = RUNTIME_STORE.append_event(parts[2], payload)
                self._send_json({"ok": True, "event": event}, 201)
                return
            if len(parts) == 4 and parts[:2] == ["api", "runs"] and parts[3] == "status":
                run = RUNTIME_STORE.update_run(parts[2], status=str(payload.get("status") or ""), ended_at=payload.get("endedAt"))
                self._send_json({"ok": True, "run": run}, 200)
                return
            if len(parts) == 4 and parts[:2] == ["api", "runs"] and parts[3] == "human-reviews":
                review = RUNTIME_STORE.create_review(parts[2], payload)
                self._send_json({"ok": True, "review": review}, 201)
                return
            if len(parts) == 4 and parts[:2] == ["api", "human-reviews"] and parts[3] == "decision":
                review = RUNTIME_STORE.decide_review(parts[2], payload)
                self._send_json({"ok": True, "review": review}, 200)
                return
            self._send_json({"ok": False, "error": "runtime endpoint not found"}, 404)
        except Exception as exc:
            self._send_json({"ok": False, "error": str(exc)}, self._runtime_error_status(exc))

    def handle_intelligence_post(self, raw: str) -> None:
        try:
            payload = self._read_json()
            parts = [part for part in raw.split("/") if part]
            if len(parts) >= 4 and parts[:3] == ["api", "intelligence", "assessments"] and INTELLIGENCE_AGENT.store.get_assessment(parts[3]) is None:
                raise IntelligenceNotFound(f"assessment not found: {parts[3]}")
            if len(parts) == 5 and parts[:3] == ["api", "intelligence", "assessments"] and parts[4] == "extraction-jobs":
                result = EXTRACTION_JOBS.submit(parts[3], payload)
                self._send_json({"ok": True, **result}, 202)
                return
            if len(parts) == 5 and parts[:3] == ["api", "intelligence", "assessments"] and parts[4] in {"follow-up-plan", "followups"}:
                result = research_followups().execute_target(parts[3], payload) if parts[4] == "follow-up-plan" else research_followups().create(parts[3], payload)
                self._send_json({"ok": True, **result}, 200 if result.get("duplicate") else 202)
                return
            if parts == ["api", "intelligence", "documents"]:
                result = INTELLIGENCE_AGENT.ingest(payload)
                self._send_json({"ok": True, **result}, 200 if result.get("duplicate") else 201)
                return
            if parts == ["api", "intelligence", "ontology", "audits"]:
                assessment_id = str(payload.get("assessmentId") or "").strip()
                if not assessment_id:
                    raise IntelligenceValidationError("assessmentId is required to run an ontology audit")
                result = INTELLIGENCE_AGENT.store.run_periodic_audit(assessment_id, {**payload, "trigger": payload.get("trigger") or "publication-gate"})
                self._send_json({"ok": True, "audit": result, "auditRun": result}, 201)
                return
            if len(parts) == 5 and parts[:3] == ["api", "intelligence", "candidates"] and parts[4] == "review":
                result = INTELLIGENCE_AGENT.review(parts[3], payload)
                self._send_json({"ok": True, "candidate": result, "review": INTELLIGENCE_AGENT.store.list_reviews(parts[3])[-1]}, 200)
                return
            if len(parts) == 5 and parts[:3] == ["api", "intelligence", "assessments"] and parts[4] == "review":
                result = INTELLIGENCE_AGENT.review_assessment(parts[3], payload)
                self._send_json({"ok": True, "assessment": result}, 200)
                return
            if len(parts) == 5 and parts[:3] == ["api", "intelligence", "assessments"] and parts[4] in {"audit", "audits", "audit-runs"}:
                result = INTELLIGENCE_AGENT.store.run_periodic_audit(parts[3], payload)
                self._send_json({"ok": True, "audit": result, "auditRun": result}, 201)
                return
            if len(parts) == 5 and parts[:3] == ["api", "intelligence", "assessments"] and parts[4] == "graph":
                result = assessment_operation(parts[3], "object-extraction" if payload.get("extractEntities") else "object-attributes" if payload.get("extractAttributes") else "graph-linking", "extract-objects-and-attributes" if payload.get("extractEntities") else "extract-object-attributes" if payload.get("extractAttributes") else "build-graph", lambda: INTELLIGENCE_AGENT.build_assessment_graph(parts[3], payload))
                self._send_json({"ok": True, "graph": result}, 201)
                return
            if len(parts) == 5 and parts[:3] == ["api", "intelligence", "assessments"] and parts[4] in {"validate", "validation"}:
                result = assessment_operation(parts[3], "evidence-verification", "model-critique" if payload.get("kind") == "model-critique" else "verify-source-quotes", lambda: INTELLIGENCE_AGENT.validate_assessment(parts[3], payload))
                self._send_json({"ok": True, "validationRound": result}, 201)
                return
            if len(parts) == 5 and parts[:3] == ["api", "intelligence", "assessments"] and parts[4] == "admissions":
                metric_ids = payload.get("metricIds")
                if metric_ids is not None and not isinstance(metric_ids, list):
                    raise IntelligenceValidationError("metricIds must be an array")
                graph_selection = payload if any(key in payload for key in ("graphRevisionId", "graphNodeIds", "graphEdgeIds", "graphProperties")) else None
                result = INTELLIGENCE_AGENT.propose_ontology_admissions(parts[3], metric_ids, graph_selection=graph_selection)
                self._send_json({"ok": True, "admissions": result}, 201)
                return
            if len(parts) == 5 and parts[:3] == ["api", "intelligence", "admissions"] and parts[4] == "review":
                result = INTELLIGENCE_AGENT.review_admission(parts[3], payload)
                self._send_json({"ok": True, "admission": result}, 200)
                return
            if len(parts) == 5 and parts[:3] == ["api", "intelligence", "admissions"] and parts[4] == "publish":
                result = INTELLIGENCE_AGENT.publish_admission(parts[3], payload)
                self._send_json({"ok": True, "admission": result}, 200)
                return
            if len(parts) == 5 and parts[:3] == ["api", "intelligence", "audit-findings"] and parts[4] == "review":
                result = INTELLIGENCE_AGENT.store.review_audit_finding(parts[3], payload)
                self._send_json({"ok": True, "finding": result}, 200)
                return
            if len(parts) == 5 and parts[:3] == ["api", "intelligence", "assessments"] and parts[4] == "publish":
                result = INTELLIGENCE_AGENT.publish_assessment(parts[3], payload)
                self._send_json({"ok": True, "assessment": result}, 200)
                return
            if parts == ["api", "intelligence", "conflicts"]:
                match_key = str(payload.get("matchKey") or "").strip()
                candidate_ids = payload.get("candidateIds")
                reason = str(payload.get("reason") or "").strip()
                if not match_key or not isinstance(candidate_ids, list) or not reason:
                    raise IntelligenceValidationError("matchKey, candidateIds and reason are required")
                conflict = INTELLIGENCE_AGENT.store.add_conflict(match_key, candidate_ids, reason)
                self._send_json({"ok": True, "conflict": conflict}, 201)
                return
            if parts == ["api", "intelligence", "publish"]:
                result = INTELLIGENCE_AGENT.publish(payload)
                self._send_json({"ok": True, "version": result}, 201)
                return
            self._send_json({"ok": False, "error": "intelligence endpoint not found"}, 404)
        except Exception as exc:
            self._send_json({"ok": False, "error": str(exc)}, self._intelligence_error_status(exc))

    def handle_osint_post(self, raw: str) -> None:
        try:
            payload = self._read_json()
            parts = [part for part in raw.split("/") if part]
            if parts == ["api", "osint", "ontology", "audits"]:
                assessment_id = str(payload.get("assessmentId") or "").strip()
                if not assessment_id:
                    raise IntelligenceValidationError("assessmentId is required to run an ontology audit")
                result = INTELLIGENCE_AGENT.store.run_periodic_audit(assessment_id, {**payload, "trigger": payload.get("trigger") or "publication-gate"})
                self._send_json({"ok": True, "audit": result}, 201)
                return
            if len(parts) >= 4 and parts[:3] == ["api", "osint", "assessments"] and INTELLIGENCE_AGENT.store.get_assessment(parts[3]) is None:
                raise IntelligenceNotFound(f"assessment not found: {parts[3]}")
            if parts == ["api", "osint", "tasks"]:
                task = OSINT_STORE.create_task(payload)
                self._send_json({"ok": True, "task": task}, 201)
                return
            if parts in (["api", "osint", "history", "clear"], ["api", "osint", "history", "archive"]):
                result = OSINT_STORE.archive_history(payload.get("reason") or "Operator requested a fresh validation run")
                self._send_json({"ok": True, **result}, 200)
                return
            if parts == ["api", "osint", "history", "purge"]:
                result = OSINT_STORE.purge_history(payload.get("reason") or "Operator requested permanent reset")
                self._send_json({"ok": True, **result}, 200)
                return
            if len(parts) == 5 and parts[:3] == ["api", "osint", "tasks"] and parts[4] == "candidates":
                item_ids = payload.get("itemIds")
                if not isinstance(item_ids, list):
                    raise OSINTValidationError("itemIds must be an array")
                result = OSINT_STORE.handoff(parts[3], item_ids)
                self._send_json({"ok": True, **result}, 201)
                return
            if len(parts) == 5 and parts[:3] == ["api", "osint", "tasks"] and parts[4] == "assessments":
                result = chain_assessment_extraction(OSINT_STORE.create_assessment(parts[3], payload))
                self._send_json({"ok": True, **result}, 200 if result.get("duplicate") else 201)
                return
            if len(parts) == 5 and parts[:3] == ["api", "osint", "tasks"] and parts[4] == "imagery-evidence":
                result = ImageryEvidenceRegistry(OSINT_STORE, INTELLIGENCE_AGENT.store).attach(parts[3], payload)
                self._send_json({"ok": True, **result, "confidencePromotion": False}, 200 if result.get("duplicate") else 201)
                return
            if len(parts) == 5 and parts[:3] == ["api", "osint", "assessments"] and parts[4] == "review":
                result = INTELLIGENCE_AGENT.review_assessment(parts[3], payload)
                self._send_json({"ok": True, "assessment": result}, 200)
                return
            if len(parts) == 5 and parts[:3] == ["api", "osint", "assessments"] and parts[4] in {"audit", "audits", "audit-runs"}:
                result = INTELLIGENCE_AGENT.store.run_periodic_audit(parts[3], payload)
                self._send_json({"ok": True, "audit": result, "auditRun": result}, 201)
                return
            if len(parts) == 5 and parts[:3] == ["api", "osint", "assessments"] and parts[4] == "graph":
                result = assessment_operation(parts[3], "object-extraction" if payload.get("extractEntities") else "object-attributes" if payload.get("extractAttributes") else "graph-linking", "extract-objects-and-attributes" if payload.get("extractEntities") else "extract-object-attributes" if payload.get("extractAttributes") else "build-graph", lambda: INTELLIGENCE_AGENT.build_assessment_graph(parts[3], payload))
                self._send_json({"ok": True, "graph": result}, 201)
                return
            if len(parts) == 5 and parts[:3] == ["api", "osint", "assessments"] and parts[4] in {"validate", "validation"}:
                result = assessment_operation(parts[3], "evidence-verification", "model-critique" if payload.get("kind") == "model-critique" else "verify-source-quotes", lambda: INTELLIGENCE_AGENT.validate_assessment(parts[3], payload))
                self._send_json({"ok": True, "validationRound": result}, 201)
                return
            if len(parts) == 5 and parts[:3] == ["api", "osint", "assessments"] and parts[4] == "admissions":
                metric_ids = payload.get("metricIds")
                if metric_ids is not None and not isinstance(metric_ids, list):
                    raise IntelligenceValidationError("metricIds must be an array")
                graph_selection = payload if any(key in payload for key in ("graphRevisionId", "graphNodeIds", "graphEdgeIds", "graphProperties")) else None
                result = INTELLIGENCE_AGENT.propose_ontology_admissions(parts[3], metric_ids, graph_selection=graph_selection)
                self._send_json({"ok": True, "admissions": result}, 201)
                return
            if len(parts) == 5 and parts[:3] == ["api", "osint", "admissions"] and parts[4] == "review":
                result = INTELLIGENCE_AGENT.review_admission(parts[3], payload)
                self._send_json({"ok": True, "admission": result}, 200)
                return
            if len(parts) == 5 and parts[:3] == ["api", "osint", "admissions"] and parts[4] == "publish":
                result = INTELLIGENCE_AGENT.publish_admission(parts[3], payload)
                self._send_json({"ok": True, "admission": result}, 200)
                return
            if len(parts) == 5 and parts[:3] == ["api", "osint", "audit-findings"] and parts[4] == "review":
                result = INTELLIGENCE_AGENT.store.review_audit_finding(parts[3], payload)
                self._send_json({"ok": True, "finding": result}, 200)
                return
            if len(parts) == 5 and parts[:3] == ["api", "osint", "assessments"] and parts[4] == "capabilities":
                result = assessment_operation(parts[3], "capability-analysis", "quantify-public-observations", lambda: INTELLIGENCE_AGENT.quantify_assessment(parts[3]), creates_assessment=True)
                self._send_json({"ok": True, **result}, 200 if result.get("duplicate") else 201)
                return
            if len(parts) == 5 and parts[:3] == ["api", "osint", "assessments"] and parts[4] == "publish":
                result = INTELLIGENCE_AGENT.publish_assessment(parts[3], payload)
                self._send_json({"ok": True, "assessment": result}, 200)
                return
            self._send_json({"ok": False, "error": "OSINT endpoint not found"}, 404)
        except Exception as exc:
            self._send_json({"ok": False, "error": str(exc)}, self._intelligence_error_status(exc) if isinstance(exc, IntelligenceStoreError) else self._osint_error_status(exc))

    def handle_kb(self, raw: str):
        try:
            if raw == "/api/kb/health":
                payload = kb_health()
            else:
                payload = attach_intelligence_ontology(kb_boot(), INTELLIGENCE_AGENT.store.published_ontology(detailed=True), ONTOLOGY / "coast-raid-k")
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception as exc:
            body = json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False).encode("utf-8")
            self.send_response(503)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Local ontology and Agent workbenches")
    parser.add_argument("--port", type=int, default=8765)
    port = parser.parse_args().port
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    EXTRACTION_JOBS.recover()
    start_verification_recovery_monitor()
    # Keep startup output ASCII-safe on Windows terminals using cp1252.
    print(f"open http://127.0.0.1:{port}/kb.html    (ontology knowledge base)")
    print(f"     http://127.0.0.1:{port}/agents.html?api (agent workbench)")
    print(f"     http://127.0.0.1:{port}/intelligence.html?api (intelligence review)")
    print(f"     http://127.0.0.1:{port}/api/kb/boot")
    print(f"     http://127.0.0.1:{port}/space.html  (maritime situation)")
    print(f"     http://127.0.0.1:{port}/cop.html    (common operational picture)")
    print(f"     http://127.0.0.1:{port}/index.html  (engagement ontology)")
    print(f"     http://127.0.0.1:{port}/swarm.html  (formation view)")
    print(f"     http://127.0.0.1:{port}/field.html  (agent coordination)")
    httpd.serve_forever()
