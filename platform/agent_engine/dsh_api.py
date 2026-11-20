"""DeepSeek Harness (DSH) gateway for the Ontology Agent Engine.

DSH is treated as a user-facing host. It receives a bounded task envelope,
starts one real Agent Harness Run, and reads only Run-scoped summaries,
events, evidence and report artifacts. It cannot mutate ontology facts or
accept/publish relation candidates.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import json
import logging
import math
from pathlib import Path
import secrets
import traceback
from types import SimpleNamespace
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from psycopg2.extras import Json

from agent_admin.credentials import AdminAuth
from agent_admin.repository import AdminRepository
from .repository import as_json
from .uav_execution_contracts import BaselineNotReady


TERMINAL = {"completed", "completed-with-gaps", "requires-human-review", "blocked", "failed", "cancelled"}
DEFAULT_PROFILE = "future-battlefield-codex"
ALLOWED_MODES = {"unattended-draft", "expert-draft"}
FUTURE_AGENT_KEYS = {"future-battlefield-codex", "future-battlefield-analyst"}
UAV_AGENT_KEYS = {"uav-capability-analyst", "uav-design-engineer", "uav-evidence-reviewer"}
DSH_AGENT_KEYS = FUTURE_AGENT_KEYS | UAV_AGENT_KEYS
COGNITIVE_APPLICATION_KEY = "ontology-cognitive-analyst"
COGNITIVE_ACTION_KEYS = (
    "situation-assessment-action", "rule-applicability-action",
    "tactics-research-action", "ontology-consistency-action",
)
COGNITIVE_KEYS = {COGNITIVE_APPLICATION_KEY, *COGNITIVE_ACTION_KEYS}
COGNITIVE_PRODUCT_TYPES = {
    "CognitiveResearchReport", "SituationAssessment", "ConstraintAssessment",
    "ResearchOptionComparison", "ConsistencyReport",
}
MAX_BODY_BYTES = 96_000
MAX_QUESTION = 20_000
MAX_SCOPE_BYTES = 24_000
logger = logging.getLogger(__name__)


def _admission_diagnostic(exc: Exception, turn_id: str, agent_key: str, stage: str) -> dict[str, Any]:
    """Log correlation and code locations, never exception messages or locals."""
    return {"event": "dsh.run-admission-failed", "turnId": turn_id,
            "agentKey": agent_key, "stage": stage, "errorType": type(exc).__name__,
            "frames": [{"file": Path(frame.filename).name, "function": frame.name, "line": frame.lineno}
                       for frame in traceback.extract_tb(exc.__traceback__)[-5:]]}

FUTURE_PERSPECTIVES = {
    "war-system": ("作战体系", "指挥、编组、跨域协同和决策链如何变化？", "operational"),
    "unmanned-autonomy": ("无人化与自主化", "无人平台、自主决策和人机协同如何发展？", "operational"),
    "sensing-data-link": ("传感器与数据链", "感知、通信、融合和决策周期如何变化？", "technical"),
    "electromagnetic-cyber": ("电磁与网络对抗", "干扰、欺骗和网络攻击如何改变体系运作？", "technical"),
    "industrial-logistics": ("工业与后勤", "生产、补充、维修和保障约束如何影响持续性？", "operational"),
    "countermeasure-iteration": ("反制与技术迭代", "攻防反制和技术迭代如何相互塑造？", "tactical"),
    "cognitive-information": ("认知与信息影响", "认知影响和信息优势如何改变作战体系？", "strategic"),
    "law-escalation": ("法律与升级风险", "法律约束和升级风险如何影响能力运用？", "strategic"),
}
FUTURE_LAYERS = ("strategic", "operational", "tactical", "technical")
FUTURE_SOURCES = ("official", "research", "industry", "events")
FUTURE_SOURCE_ALIASES = {"academic": "research", "thinktank": "research"}


def _profile_capability(profile, modes):
    from .phase3_repository import profile_budget_limits
    result = {"allowedModes": list(modes), "budgetLimits": profile_budget_limits(profile)}
    if getattr(profile, 'tools', None):
        from .connectors.contracts import connector_manifest
        manifest = connector_manifest(profile)
        result['connectorDependencies'] = [
            {'ref': connection['ref'], 'version': connection['version'], 'driver': connection['driver']}
            for connection in manifest['connections']]
    goal = getattr(profile, 'goal_contract', {}) or {}
    if goal:
        document = goal.get('document') or {}
        result['goalContract'] = {key:goal[key] for key in ('contractId','revision','contentHash')}
        result['goalContract'].update({key:document.get(key) for key in ('objective','scope','exclusions','deliverables','stopConditions')})
        result['goalContract']['evaluationScope'] = '基础交付与追溯；语义覆盖与专业正确性尚需独立评估。'
    if profile.agent_key in FUTURE_AGENT_KEYS:
        from .runtime_contracts import future_budget_minimums
        result.update({"budgetMinimums": future_budget_minimums(profile),
                       "budgetSemantics": {"reasoning_rounds": "全 Run 共享的节点进入次数；不是每个节点的 maxRounds。"},
                       "scopeOptions": {"sourceTypes": list(FUTURE_SOURCES),
                                        "sourceTypeAliases": dict(FUTURE_SOURCE_ALIASES),
                                        "layers": list(FUTURE_LAYERS), "horizonYears": [1, 3, 5, 10]},
                       "perspectiveKeys": list(FUTURE_PERSPECTIVES)})
        from .decision_semantics import enabled, VERSION, BRIEF_SCHEMA
        if enabled(profile):
            result['decisionSemantics'] = {'version': VERSION, 'decisionBriefSchema': deepcopy(BRIEF_SCHEMA),
                'defaultPolicy': 'record-defaults-and-unanswered-questions',
                'verificationTasks': 'proposed-research-work-not-automatically-scheduled'}
    return result


def _cognitive_gateway_capability(engine):
    """Return the application-only cognitive DSH capability when ready.

    The four Action profiles are dependencies of this application. They are
    intentionally omitted from the DSH root capability list so a caller cannot
    bypass the typed application admission and delegation chain.
    """
    try:
        from .cognitive_admission import readiness
        states = {key: readiness(engine, key) for key in COGNITIVE_KEYS}
        if any(item.get('ready') is not True for item in states.values()):
            return None
        profile = engine.loader.load(COGNITIVE_APPLICATION_KEY)
        capability = _profile_capability(profile, ["unattended-draft"])
        capability.update({
            "agent_key": COGNITIVE_APPLICATION_KEY,
            "name": "Ontology Cognitive Research",
            "description": "公开文献与本体方法研究；只交付可追溯的条件性研究草稿。",
            "current_version": profile.version,
            "profileStatus": "testing",
            "rootAdmission": "cognitive-suite-v1",
            "delegatedAgents": list(COGNITIVE_ACTION_KEYS),
            "delegatedReadiness": {
                key: states[key].get('ready') is True for key in COGNITIVE_ACTION_KEYS
            },
            "supportedEndpoints": [
                "/api/dsh/v1/cognitive-suite/context",
                "/api/dsh/v1/cognitive-suite/admission",
                "/api/dsh/v1/cognitive-suite/runs",
                "/api/dsh/v1/cognitive-suite/runs/{run_id}/status",
                "/api/dsh/v1/cognitive-suite/runs/{run_id}/report",
                "/api/dsh/v1/cognitive-suite/runs/{run_id}/source",
            ],
            "scope": "public-document-and-ontology-method-research",
        })
        return capability
    except Exception:
        # Discovery must not turn an unavailable cognitive dependency into a
        # false ready capability or expose internal configuration details.
        return None


def _new_id(prefix: str) -> str:
    return f"{prefix}-{secrets.token_hex(10)}"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _future_defaults(question: str) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Return a bounded, explicit scope for a conversational DSH turn.

    The DSH user should be able to ask a research question without knowing the
    internal Agent task envelope.  These are defaults only; the frozen Run
    still records every value and the normal task contract validates it.
    """
    as_of = datetime.now(timezone.utc).date().isoformat()
    scope = {
        "timeWindow": {"asOf": as_of, "horizonYears": 5},
        "geography": "全球；重点关注印太与高强度对抗环境",
        "summary": "关注战争形态、体系协同、无人化与智能化、跨域对抗和保障韧性；以公开材料支持判断。",
        "constraints": "区分结构性趋势与短期事件；检视对手反制、欺骗、通信退化、保障与工业约束；关键判断保留反方证据和未知项。",
        "layers": list(FUTURE_LAYERS),
        "sourceTypes": list(FUTURE_SOURCES),
    }
    perspectives = [
        {"key": key, "name": value[0], "question": value[1], "layer": value[2]}
        for key, value in FUTURE_PERSPECTIVES.items()
        if key in {"war-system", "unmanned-autonomy", "sensing-data-link", "electromagnetic-cyber", "industrial-logistics", "countermeasure-iteration"}
    ]
    return scope, perspectives


def _normalise_future_input(question: str, scope: Any, perspectives: Any, budget: dict[str, int], mode: str,
                            agent_key: str, agent_version: str, decision_brief=None) -> dict[str, Any]:
    default_scope, default_perspectives = _future_defaults(question)
    merged_scope = {**default_scope, **(scope if isinstance(scope, dict) else {})}
    source_types = merged_scope.get('sourceTypes')
    if (not isinstance(source_types, list) or not source_types or len(source_types) > 12
            or any(not isinstance(value, str) or value not in {*FUTURE_SOURCES, *FUTURE_SOURCE_ALIASES}
                   for value in source_types)):
        raise HTTPException(400, detail={"code": "task-input-invalid", "field": "scope.sourceTypes",
            "message": "来源类型无效；academic 和 thinktank 可作为 research 的别名。",
            "allowedValues": list(FUTURE_SOURCES), "retryable": False})
    merged_scope['sourceTypes'] = list(dict.fromkeys(FUTURE_SOURCE_ALIASES.get(value, value) for value in source_types))
    if not isinstance(merged_scope.get('timeWindow'), dict):
        raise HTTPException(400, detail={"code": "task-input-invalid", "field": "scope.timeWindow",
            "message": "timeWindow 必须是包含 asOf 和 horizonYears 的对象。", "retryable": False})
    merged_window = {**default_scope["timeWindow"], **(merged_scope.get("timeWindow") or {})}
    merged_scope["timeWindow"] = merged_window
    selected = perspectives if perspectives else default_perspectives
    normalised: list[dict[str, str]] = []
    for item in selected:
        if isinstance(item, str):
            key = item
            if key not in FUTURE_PERSPECTIVES:
                key = next((candidate for candidate, value in FUTURE_PERSPECTIVES.items() if value[0] == item), "")
            if not key:
                raise HTTPException(400, f"unknown future battlefield perspective: {item}")
            name, prompt, layer = FUTURE_PERSPECTIVES[key]
            normalised.append({"key": key, "name": name, "question": prompt, "layer": layer})
        elif isinstance(item, dict):
            key = item.get("key")
            if key not in FUTURE_PERSPECTIVES:
                raise HTTPException(400, "future battlefield perspective key is invalid")
            name, prompt, layer = FUTURE_PERSPECTIVES[key]
            normalised.append({"key": key, "name": item.get("name") or name,
                               "question": item.get("question") or prompt,
                               "layer": item.get("layer") or layer})
        else:
            raise HTTPException(400, "perspectives must contain keys or objects")
    if len({item["key"] for item in normalised}) != len(normalised):
        raise HTTPException(400, "future battlefield perspective keys must be unique")
    analysis_context = {
        "asOf": merged_window.get("asOf"), "horizonYears": merged_window.get("horizonYears"),
        "theater": merged_scope.get("geography"), "layers": merged_scope.get("layers"),
        "sourceTypes": merged_scope.get("sourceTypes"), "constraints": merged_scope.get("constraints"),
    }
    specific = {
        "schemaVersion": "1.0", "kind": "future-battlefield-judgment",
        "asOf": merged_window.get("asOf"), "horizonYears": merged_window.get("horizonYears"),
        "theater": merged_scope.get("geography"), "scopeNote": merged_scope.get("summary"),
        "layers": merged_scope.get("layers"), "sourceTypes": merged_scope.get("sourceTypes"),
        "constraints": merged_scope.get("constraints"), "perspectives": normalised,
    }
    envelope = {
        "asOf": merged_window.get("asOf"), "question": question.strip(), "scope": merged_scope,
        "perspectives": normalised, "analysisContext": analysis_context,
        "analysisPurpose": "future-battlefield-judgment", "executionMode": "profile-run",
        "includeDesign": False, "automationMode": mode, "budgetLimits": budget,
        "commonSettings": {"schemaVersion": "1.0", "agentKey": agent_key, "agentVersion": agent_version,
                            "taskGoal": question.strip(), "executionMode": "profile-run", "budgetLimits": budget},
        "specificSettings": specific,
    }
    from .task_contracts import validate_task_envelope
    if decision_brief is not None:
        from .decision_semantics import validate_brief
        from jsonschema import ValidationError
        try:
            validate_brief(decision_brief)
        except ValidationError:
            raise HTTPException(400, detail={'code': 'task-input-invalid', 'field': 'decisionBrief',
                'message': '决策说明字段或内容无效。', 'retryable': False}) from None
        envelope['decisionBrief'] = deepcopy(decision_brief)
    try:
        validate_task_envelope(agent_key, SimpleNamespace(version=agent_version), envelope)
    except ValueError as exc:
        # These messages are authored by the task validator; never expose raw
        # engine/provider/database exceptions through this diagnostic path.
        raise HTTPException(400, detail={"code": "task-input-invalid", "message": str(exc),
            "retryable": False}) from None
    return envelope


def _normalise_uav_input(question: str, body: dict[str, Any], budget: dict[str, int], mode: str,
                         agent_key: str, agent_version: str) -> dict[str, Any]:
    """Build a frozen UAV task envelope; preflight is rechecked at admission."""
    entity_id = body.get("entityId") or body.get("uavId")
    requirements = body.get("requirements") or {"enduranceHours": None, "payloadKg": None}
    purpose = body.get("analysisPurpose") or "overview"
    context = body.get("analysisContext", {})
    selection = body.get("entitySelection")
    if not isinstance(entity_id, str) or not 1 <= len(entity_id) <= 200:
        raise HTTPException(400, "entityId is required for UAV analysis")
    if not isinstance(requirements, dict) or not requirements or len(requirements) > 20:
        raise HTTPException(400, "requirements must be a bounded non-empty object")
    from agent_tools.ontology_catalog import METRIC_LABELS
    from agent_tools.engineering import TOOL_INPUT_SCHEMAS
    from jsonschema import validate, ValidationError
    normalised = {}
    for metric, value in requirements.items():
        if metric not in METRIC_LABELS:
            raise HTTPException(400, "unsupported UAV requirement metric")
        if value is None:
            normalised[metric] = None
            continue
        if type(value) in (int, float) and math.isfinite(value) and value >= 0:
            value = {"operator": ">=", "quantity": {"value": value, "unit": METRIC_LABELS[metric][1]}}
        if not isinstance(value, dict):
            raise HTTPException(400, "UAV requirement must be a quantity, non-negative number or null")
        value = deepcopy(value)
        if "quantity" not in value and "unit" in value:
            value["quantity"] = {key: value.pop(key) for key in ("value", "min", "max", "unit") if key in value}
        value.setdefault("operator", ">=")
        try:
            validate(value, TOOL_INPUT_SCHEMAS["engineering.evaluate_requirement"]["properties"]["requirement"])
            if value.get("quantity") is None:
                raise ValueError("quantity required")
            from agent_tools.engineering import normalize_quantity
            quantity = normalize_quantity(value["quantity"])
            if quantity["dimension"] != normalize_quantity({"value": 1, "unit": METRIC_LABELS[metric][1]})["dimension"]:
                raise ValueError("requirement unit does not match metric")
        except (ValidationError, ValueError, TypeError):
            raise HTTPException(400, "invalid UAV requirement quantity, operator or conditions") from None
        normalised[metric] = value
    requirements = normalised
    if (not isinstance(context, dict) or set(context) - {"configuration", "requirementText"}
            or any(not isinstance(value, str) or len(value) > 4000 for value in context.values())):
        raise HTTPException(400, "analysisContext must contain bounded configuration and requirementText strings")
    if purpose not in {"overview", "requirements", "evidence"}:
        raise HTTPException(400, "unsupported UAV analysis purpose")
    context = {"configuration": context.get("configuration", ""),
               "requirementText": context.get("requirementText", "")}
    if purpose == "requirements" and not context["requirementText"].strip():
        # A conversational caller already supplied the requirement in the
        # question and structured thresholds. Preserve that text without
        # inventing a mission profile or any operating conditions.
        context["requirementText"] = (question.strip() if len(question.strip()) <= 4000 else
                                     "需求阈值见冻结的 requirements；原始问题见 question，未提供的工况保持未知。")
    if not isinstance(selection, dict):
        raise HTTPException(400, "entitySelection from UAV preflight is required")
    specific = {
        "schemaVersion": "1.0", "kind": {
            "uav-capability-analyst": "uav-capability-analysis",
            "uav-design-engineer": "uav-design",
            "uav-evidence-reviewer": "uav-evidence-review",
        }[agent_key],
        "entityId": entity_id, "entitySelection": selection,
        "metrics": list(requirements), "purpose": purpose,
        "configuration": context.get("configuration", ""),
        "requirementText": context.get("requirementText", ""),
    }
    return {
        "executionMode": "research", "automationMode": mode,
        "entityId": entity_id, "uavId": entity_id,
        "entitySelection": selection, "analysisPurpose": purpose,
        "analysisContext": {"configuration": context.get("configuration", ""),
                             "requirementText": context.get("requirementText", "")},
        "requirements": requirements, "question": question.strip(),
        "budgetLimits": budget, "specificSettings": specific,
        "commonSettings": {"schemaVersion": "1.0", "agentKey": agent_key,
                            "agentVersion": agent_version, "taskGoal": question.strip(),
                            "executionMode": "research", "budgetLimits": budget},
    }


def create_dsh_router(engine, queue, *, auth=None, verification=None):
    """Create the DSH-facing adapter without duplicating Agent Engine logic."""
    factory = getattr(engine, "base_connection_factory", queue.connection_factory)
    auth = auth or AdminAuth(factory)
    admin_repository = AdminRepository(engine.repo.connection_factory)
    router = APIRouter(prefix="/api/dsh/v1", tags=["dsh-gateway"])
    from .verification import VerificationService
    from .verification.contracts import VerificationError
    from .verification_api import verification_response
    verification = verification or VerificationService(factory)

    def principal(request: Request):
        authorization = request.headers.get("authorization", "")
        token = authorization[7:] if authorization.startswith("Bearer ") else request.cookies.get("agent_admin_session")
        actor = auth.authenticate(token)
        if actor is None or getattr(actor, "kind", None) != "human":
            if '/verifications/' in request.url.path or request.url.path.endswith('/contract-verification'):
                verification_response(verification, {}, None, 'dsh')
            raise HTTPException(401, "authenticated DSH operator required", headers={"WWW-Authenticate": "Bearer"})
        return actor

    def body_size(body: dict[str, Any]):
        try:
            size = len(json.dumps(body, ensure_ascii=False, default=str).encode("utf-8"))
        except (TypeError, ValueError):
            raise HTTPException(400, "request body must be JSON") from None
        if size > MAX_BODY_BYTES:
            raise HTTPException(413, "DSH request exceeds the configured size limit")

    def cognitive_mutation(request: Request, actor):
        if not actor.permits('operator'):
            raise HTTPException(403, 'operator role required')
        origin = request.headers.get('origin')
        if origin and origin.rstrip('/') != str(request.base_url).rstrip('/'):
            raise HTTPException(403, 'cross-origin cognitive request rejected')

    def session_row(session_id: str, actor):
        with factory() as con:
            with con.cursor() as cursor:
                cursor.execute("SELECT * FROM dsh_session WHERE session_id=%s AND principal_id=%s", (session_id, actor.principal_id))
                row = cursor.fetchone()
        if not row:
            raise HTTPException(404, "DSH session not found")
        return dict(row)

    def turn_row(turn_id: str, actor):
        with factory() as con:
            with con.cursor() as cursor:
                cursor.execute("""SELECT t.*,s.principal_id,s.agent_profile AS session_agent_profile
                                   FROM dsh_turn t JOIN dsh_session s USING(session_id)
                                  WHERE t.turn_id=%s AND s.principal_id=%s""", (turn_id, actor.principal_id))
                row = cursor.fetchone()
        if not row:
            raise HTTPException(404, "DSH turn not found")
        return dict(row)

    def run_access(run_id: str, actor):
        from .run_origin import visible_runs
        with factory() as con:
            with con.cursor() as cursor:
                cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
                origin = visible_runs(cursor, actor).get(run_id)
                if not origin:
                    raise HTTPException(404, "DSH Run not found")
                # Chat provenance is optional for Admin, background and child
                # Runs. It is never a substitute for the shared visibility gate.
                cursor.execute("""SELECT t.turn_id,t.session_id FROM dsh_turn t
                    JOIN dsh_session s USING(session_id)
                    WHERE t.run_id=ANY(%s) AND s.principal_id=%s
                    ORDER BY t.created_at DESC LIMIT 1""",
                    (list({run_id, origin.get('lineageRootRunId') or run_id}), actor.principal_id))
                turn = cursor.fetchone()
        return {'run_id': run_id, 'principal_id': actor.principal_id,
                **(dict(turn) if turn else {})}

    def exposed(agent_key: str) -> bool:
        with factory() as con:
            with con.cursor() as cursor:
                cursor.execute("SELECT to_regclass(current_schema() || '.agent_dsh_exposure_policy') AS table_name")
                installed = cursor.fetchone()
                if installed and installed.get("table_name"):
                    cursor.execute("SELECT enabled FROM agent_dsh_exposure_policy WHERE agent_key=%s", (agent_key,))
                    row = cursor.fetchone()
                    return bool(row and row["enabled"])
        return agent_key in DSH_AGENT_KEYS

    def allowed_modes(agent_key: str) -> list[str]:
        with factory() as con:
            with con.cursor() as cursor:
                cursor.execute("SELECT allowed_modes_json FROM agent_dsh_exposure_policy WHERE agent_key=%s AND enabled=true", (agent_key,))
                row = cursor.fetchone()
        if not row:
            return ["unattended-draft"] if agent_key == DEFAULT_PROFILE else []
        modes = row.get("allowed_modes_json") or []
        return [mode for mode in modes if mode in ALLOWED_MODES] if isinstance(modes, list) else []

    def profile_allowed(agent_key: str):
        if not isinstance(agent_key, str) or not agent_key or len(agent_key) > 128:
            raise HTTPException(400, "agentProfile is invalid")
        if not exposed(agent_key):
            raise HTTPException(403, "Agent Profile is not exposed to DSH")
        return agent_key

    def uav_profile_allowed(agent_key: str):
        agent_key = profile_allowed(agent_key)
        if agent_key not in UAV_AGENT_KEYS:
            raise HTTPException(400, "UAV Gateway requires a UAV Agent Profile")
        return agent_key

    def uav_profile(agent_key: str):
        try:
            return engine.loader.load(agent_key)
        except Exception as exc:
            raise HTTPException(404, "UAV Agent Profile not found") from exc

    def run_summary(run_id: str, run: dict[str, Any] | None = None):
        run = run or engine.repo.get_run(run_id)
        if not run:
            raise HTTPException(404, "Run not found")
        return {key: run.get(key) for key in (
            "run_id", "agent_key", "agent_version", "status", "current_step_key",
            "model_call_count", "tool_call_count", "created_at", "updated_at", "completed_at",
            "error_message", "baseline_id", "baseline_hash", "harness_contract_id")}

    def report_payload(run_id: str, run: dict[str, Any] | None = None):
        run = run or engine.repo.get_run(run_id)
        if not run:
            raise HTTPException(404, "Run not found")
        output = as_json(run.get("output_json"))
        if not isinstance(output, dict):
            output = {}
        native = output if output.get('productType') in COGNITIVE_PRODUCT_TYPES else output.get('report')
        if (run.get('agent_key') in COGNITIVE_KEYS and isinstance(native, dict)
                and native.get('productType') in COGNITIVE_PRODUCT_TYPES):
            from .cognitive_contracts import validate_schema
            from .cognitive_runtime import PRODUCT_BY_AGENT
            try:
                validate_schema(native['productType'], native)
                provenance = native['provenance']
                if (native['productType'] != PRODUCT_BY_AGENT[run['agent_key']]
                        or provenance['runId'] != run_id or provenance['agentKey'] != run['agent_key']
                        or provenance['profileVersion'] != run['agent_version']):
                    raise ValueError('cognitive product does not match its producing Run')
            except ValueError:
                raise HTTPException(409, {'code': 'invalid-cognitive-product', 'runId': run_id}) from None
            # DSH consumers receive the typed research product unchanged. In
            # particular, ArtifactRef hashes and ontologyView relations remain
            # available for exact source inspection; no legacy report adapter
            # is allowed to flatten or rename them.
            return {
                'runId': run_id, 'status': run.get('status'),
                'agentKey': run.get('agent_key'), 'agentVersion': run.get('agent_version'),
                'productType': native['productType'], 'report': deepcopy(native),
                'ontologyGraph': deepcopy(native.get('ontologyView') or {}),
                'evidence': deepcopy(native.get('claims') or []),
                'gaps': deepcopy(native.get('gaps') or []),
                'graphValidation': {'status': 'valid', 'issues': [], 'scope': 'typed-product-schema'},
                'nativeProduct': True, 'retrievedAt': _now(),
            }
        if run.get('agent_key') in COGNITIVE_KEYS:
            raise HTTPException(409, {'code': 'cognitive-product-not-available', 'runId': run_id})
        ontology = output.get("ontology") or (output.get("judgmentReport") or {}).get("ontology") or {}
        graph_validation = {"status": "not-applicable", "issues": []}
        if isinstance(ontology, dict) and str(ontology.get("schemaVersion", "")).startswith("uav-"):
            from .uav_report import validate_uav_graph
            issues = validate_uav_graph(ontology, run_id)
            if issues:
                # Do not let a malformed projection be mistaken for a complete
                # ontology result. Keep the diagnostic in the report so DSH can
                # explain why a fresh Run is required.
                graph_validation = {"status": "invalid", "issues": issues}
                ontology = {}
            elif "projectionScope" not in ontology or "contentHash" not in ontology:
                graph_validation = {"status": "legacy", "issues": [
                    "历史 UAV 图谱未完整声明投影范围和摘要；仅作为历史研判投影呈现。"
                ]}
            else:
                graph_validation = {"status": "valid", "issues": []}
        evidence = output.get("evidence", [])
        gaps = output.get("evidenceGaps") or output.get("gaps") or output.get("unresolvedQuestions") or []
        return {
            "runId": run_id,
            "status": run.get("status"),
            "agentKey": run.get("agent_key"),
            "agentVersion": run.get("agent_version"),
            "summary": output.get("summary") or output.get("title") or "",
            "entityId": output.get("entityId"),
            "assessment": deepcopy(output.get("assessment") or {}),
            "findings": output.get("findings") or output.get("claims") or [],
            "claims": output.get("findings") or output.get("claims") or [],
            "ontologyGraph": deepcopy(ontology) if isinstance(ontology, dict) else {},
            "graphValidation": graph_validation,
            "evidence": evidence if isinstance(evidence, list) else [],
            "gaps": gaps if isinstance(gaps, list) else [],
            "evidenceGaps": deepcopy(gaps) if isinstance(gaps, list) else [],
            "taskDefinition": deepcopy(output.get('taskDefinition') or {}),
            "questionCoverage": deepcopy(output.get('questionCoverage') or []),
            "verificationTasks": deepcopy(output.get('verificationTasks') or []),
            "quality": output.get("quality") or {},
            "truthStatus": output.get("truthStatus", "unverified"),
            "truthVerified": bool(output.get("truthVerified", False)),
            "reviewStatus": output.get("reviewStatus", "pending"),
            "sourceSnapshot": deepcopy(output.get("sourceSnapshot") or {}),
            "sourceIndependence": deepcopy(output.get("sourceIndependence") or {}),
            "assumptions": output.get("assumptions") or [],
            "conflicts": output.get("conflicts") or [],
            "deliverables": deepcopy(output.get("deliverables") or {}),
            "publicationStatus": output.get("publicationStatus", "not-published"),
            "publicationReviewRequired": bool(output.get("publicationReviewRequired", True)),
            "followup": {key: deepcopy(output.get(key)) for key in ("previousRunId", "followupMode", "followupTarget") if output.get(key) is not None},
            "unresolvedQuestions": output.get("unresolvedQuestions", []),
            "retrievedAt": _now(),
        }

    def update_turn(turn_id: str, *, status: str | None = None, run_id: str | None = None, answer: dict | None = None, error: str | None = None):
        assignments, values = [], []
        if status is not None:
            assignments.append("status=%s"); values.append(status)
        if run_id is not None:
            assignments.append("run_id=%s"); values.append(run_id)
        if answer is not None:
            assignments.append("answer_json=%s"); values.append(Json(answer))
        if error is not None:
            assignments.append("error_message=%s"); values.append(error[:2000])
        assignments.append("updated_at=now()")
        values.append(turn_id)
        with factory() as con:
            with con.cursor() as cursor:
                cursor.execute("UPDATE dsh_turn SET " + ",".join(assignments) + " WHERE turn_id=%s", values)

    @router.get("/capabilities")
    def capabilities(actor=Depends(principal)):
        try:
            profiles = deepcopy(verification.capabilities(actor))
        except VerificationError as exc:
            raise HTTPException(exc.status, {'code': exc.code, 'message': '当前能力契约暂不可读取。'}) from None
        cognitive = _cognitive_gateway_capability(engine)
        from .tool_manifest import tool_manifest
        manifest = tool_manifest()
        if getattr(engine, 'loader', None) is not None:
            from .connectors.contracts import connector_manifest
            for profile in profiles:
                frozen = engine.loader.load(profile['agent_key'])
                if frozen.version != profile['current_version']:
                    raise HTTPException(409, {'code': 'capability-version-changed', 'message': '能力版本已变化，请重新读取。'})
                connections = connector_manifest(frozen)['connections']
                profile['connectorDependencies'] = [
                    {key: connection[key] for key in ('ref', 'version', 'driver')} for connection in connections]
        # Only project dependencies of already authorized profiles; never
        # forward the management catalog's locations or configuration.
        from .connectors.catalog import ConnectorCatalog
        authorized_refs = {item['ref'] for profile in profiles
                           for item in profile.get('connectorDependencies', [])}
        connector_status = []
        connector_status_available = True
        if authorized_refs:
            try:
                for item in ConnectorCatalog(factory).list():
                    if item['ref'] in authorized_refs:
                        connector_status.append({key: item.get(key) for key in (
                            'ref', 'name', 'kind', 'version', 'configurationStatus',
                            'observationStatus', 'lastObservedAt')})
            except Exception:
                # Telemetry is optional here. Preserve the authoritative Agent
                # contract while exposing an explicit, redacted unknown state.
                connector_status_available = False
        return {"profiles": profiles, "testingProfiles": [cognitive] if cognitive is not None else [],
                "cognitiveSuite": cognitive, "tools": [t['name'] for t in manifest['tools']],
                "toolContracts": manifest['tools'], "contractVersion": "dsh-ontology-v1",
                "connectorCapabilities": connector_status,
                "connectorStatusAvailable": connector_status_available,
                "verification": {"schemaVersion": "verification.v1", "authority": "database",
                                 "supportedSubjects": list(verification.adapters), "readOnly": True}}

    @router.get("/cognitive-suite/context")
    def cognitive_context(agentKey: str = COGNITIVE_APPLICATION_KEY, actor=Depends(principal)):
        if agentKey != COGNITIVE_APPLICATION_KEY:
            raise HTTPException(404, "cognitive DSH context is application-scoped")
        capability = _cognitive_gateway_capability(engine)
        if capability is None:
            raise HTTPException(409, "cognitive application is not ready")
        try:
            from .cognitive_admission import CognitiveAdmissionService
            return CognitiveAdmissionService(engine).contexts(agentKey)
        except LookupError:
            raise HTTPException(404, "cognitive application context not found") from None
        except Exception:
            raise HTTPException(409, "cognitive context is unavailable") from None

    @router.post("/cognitive-suite/admission")
    def cognitive_admission(body: dict, request: Request, actor=Depends(principal)):
        cognitive_mutation(request, actor)
        body_size(body)
        if body.get('agentKey', COGNITIVE_APPLICATION_KEY) != COGNITIVE_APPLICATION_KEY:
            raise HTTPException(404, "only the cognitive application can be a DSH root")
        capability = _cognitive_gateway_capability(engine)
        if capability is None:
            raise HTTPException(409, "cognitive application is not ready")
        try:
            from .cognitive_admission import CognitiveAdmissionService
            result = CognitiveAdmissionService(engine).admit(body, actor.principal_id)
        except (ValueError, KeyError) as exc:
            raise HTTPException(400, str(exc)) from None
        except PermissionError:
            raise HTTPException(403, 'cognitive admission denied') from None
        except Exception:
            raise HTTPException(409, 'cognitive admission is unavailable') from None
        if result.get('admissionStatus') == 'rejected':
            # Rejections are typed contract outcomes, not server failures.
            return result
        return result

    @router.post("/cognitive-suite/runs", status_code=202)
    def cognitive_run(body: dict, request: Request, actor=Depends(principal)):
        cognitive_mutation(request, actor)
        body_size(body)
        if set(body) != {'agentKey', 'input'} or body.get('agentKey') != COGNITIVE_APPLICATION_KEY:
            raise HTTPException(400, "cognitive run requires application agentKey and frozen input")
        input_data = body.get('input')
        if not isinstance(input_data, dict) or not input_data.get('cognitiveAdmissionId'):
            raise HTTPException(400, "cognitive run requires a server-issued admission input")
        from .task_intent import admission_actor
        from .run_origin import origin_context
        run_id = None
        try:
            with admission_actor(actor), origin_context(actor, source_kind='dsh'):
                run_id = engine.create_run(COGNITIVE_APPLICATION_KEY, input_data)
            engine.repo.append_event(run_id, 'dsh.cognitive-run-created',
                                     detail={'principalId': actor.principal_id})
            queue.enqueue(run_id)
        except BaselineNotReady as exc:
            raise HTTPException(409, detail={**exc.admission_details, 'runId': run_id}) from None
        except Exception:
            status = 503 if run_id is not None else 400
            raise HTTPException(status, detail={'code': 'cognitive-run-admission-failed', 'runId': run_id}) from None
        return {'runId': run_id, 'status': 'queued', 'agentProfile': COGNITIVE_APPLICATION_KEY}

    @router.post('/verifications/check')
    def verify(body: dict, actor=Depends(principal)):
        body_size(body)
        return verification_response(verification, body, actor, 'dsh')

    @router.get('/agents/{agent_key}/contract-verification')
    def verify_contract(agent_key: str, observedFingerprint: str | None = None, actor=Depends(principal)):
        body = {'subject': {'kind': 'agent-registration', 'agent_key': agent_key}}
        if observedFingerprint is not None:
            body['observedFingerprint'] = observedFingerprint
        return verification_response(verification, body, actor, 'dsh')

    @router.get("/uav/assets")
    def uav_assets(agentProfile: str = "uav-capability-analyst", objectClass: str = "UnmannedAircraft",
                   category: str = "", q: str = "", offset: int = 0, limit: int = 30,
                   actor=Depends(principal)):
        agent_key = uav_profile_allowed(agentProfile)
        try:
            from agent_tools.ontology_catalog import catalog
            return catalog(uav_profile(agent_key), object_class=objectClass, category=category,
                           query=q, offset=offset, limit=limit)
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from None
        except Exception as exc:
            raise HTTPException(400, str(exc)) from None

    @router.get("/uav/assets/{entity_id}/preflight")
    def uav_asset_preflight(entity_id: str, agentProfile: str = "uav-capability-analyst",
                            metrics: str = "enduranceHours,payloadKg", actor=Depends(principal)):
        agent_key = uav_profile_allowed(agentProfile)
        selected = [item.strip() for item in metrics.split(",") if item.strip()]
        try:
            from agent_tools.ontology_catalog import preflight
            return preflight(uav_profile(agent_key), entity_id, selected)
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from None
        except Exception as exc:
            raise HTTPException(400, str(exc)) from None

    @router.post("/sessions", status_code=201)
    def create_session(body: dict | None = None, actor=Depends(principal)):
        body = body or {}; body_size(body)
        if set(body) - {"agentProfile", "tenantId", "metadata"}:
            raise HTTPException(400, "unsupported DSH session fields")
        agent_key = profile_allowed(body.get("agentProfile") or DEFAULT_PROFILE)
        tenant_id = body.get("tenantId", "local")
        if not isinstance(tenant_id, str) or not 1 <= len(tenant_id) <= 128:
            raise HTTPException(400, "tenantId is invalid")
        session_id = _new_id("dsh-session")
        with factory() as con:
            with con.cursor() as cursor:
                cursor.execute("""INSERT INTO dsh_session(session_id,principal_id,tenant_id,agent_profile,metadata_json)
                                 VALUES(%s,%s,%s,%s,%s)""",
                               (session_id, actor.principal_id, tenant_id, agent_key, Json(body.get("metadata") or {})))
        return {"sessionId": session_id, "agentProfile": agent_key, "status": "ready", "createdAt": _now()}

    @router.get("/sessions/{session_id}")
    def get_session(session_id: str, actor=Depends(principal)):
        row = session_row(session_id, actor)
        return {"sessionId": row["session_id"], "agentProfile": row["agent_profile"], "tenantId": row["tenant_id"],
                "createdAt": row["created_at"], "updatedAt": row["updated_at"]}

    @router.post("/sessions/{session_id}/turns", status_code=202)
    def create_turn(session_id: str, body: dict, actor=Depends(principal)):
        body_size(body)
        session = session_row(session_id, actor)
        allowed = {"question", "scope", "perspectives", "budget", "mode", "agentProfile", "idempotencyKey",
                   "entityId", "uavId", "requirements", "analysisPurpose", "analysisContext", "entitySelection", "taskIntent", "decisionBrief"}
        if set(body) - allowed:
            raise HTTPException(400, "unsupported DSH turn fields")
        question = body.get("question")
        if not isinstance(question, str) or not 1 <= len(question.strip()) <= MAX_QUESTION:
            raise HTTPException(400, "question must be a bounded non-empty string")
        scope = body.get("scope") or {}
        if not isinstance(scope, dict) or len(json.dumps(scope, ensure_ascii=False, default=str).encode()) > MAX_SCOPE_BYTES:
            raise HTTPException(400, "scope must be a bounded object")
        perspectives = body.get("perspectives") or []
        if not isinstance(perspectives, list) or len(perspectives) > 12:
            raise HTTPException(400, "perspectives must be a bounded list")
        if any((not isinstance(item, (str, dict)) or
                (isinstance(item, str) and len(item) > 256) or
                (isinstance(item, dict) and len(json.dumps(item, ensure_ascii=False, default=str).encode()) > 2000))
               for item in perspectives):
            raise HTTPException(400, "perspectives contain an invalid item")
        budget = body.get("budget") or {}
        if not isinstance(budget, dict) or any(not isinstance(key, str) or type(value) is not int or value < 0 for key, value in budget.items()):
            raise HTTPException(400, "budget must contain non-negative integer limits")
        mode = body.get("mode") or "unattended-draft"
        if mode not in ALLOWED_MODES:
            raise HTTPException(400, "unsupported DSH execution mode")
        agent_key = profile_allowed(body.get("agentProfile") or session["agent_profile"] or DEFAULT_PROFILE)
        modes = allowed_modes(agent_key)
        if mode not in modes:
            raise HTTPException(403, detail={"code": "execution-mode-not-exposed", "agentProfile": agent_key,
                "message": "当前 Agent Profile 未开放所选执行模式；请使用 allowedModes 中的模式。",
                "requestedMode": mode, "allowedModes": modes, "retryable": False})
        with factory() as con:
            with con.cursor() as cursor:
                cursor.execute("SELECT current_version FROM agent_definition WHERE agent_key=%s", (agent_key,))
                profile = cursor.fetchone()
        if not profile:
            raise HTTPException(404, "Agent Profile not found")
        if agent_key in FUTURE_AGENT_KEYS:
            input_data = _normalise_future_input(question, scope, perspectives, budget, mode,
                                                 agent_key, profile["current_version"], body.get('decisionBrief'))
        elif agent_key in UAV_AGENT_KEYS:
            input_data = _normalise_uav_input(question, body, budget, mode,
                                              agent_key, profile["current_version"])
        else:
            input_data = {
                "question": question.strip(), "scope": scope, "perspectives": perspectives,
                "budgetLimits": budget, "automationMode": mode,
            }
        key = body.get("idempotencyKey")
        if key is not None and (not isinstance(key, str) or not 1 <= len(key) <= 128):
            raise HTTPException(400, "idempotencyKey is invalid")
        if key:
            with factory() as con:
                with con.cursor() as cursor:
                    cursor.execute("SELECT * FROM dsh_turn WHERE session_id=%s AND idempotency_key=%s", (session_id, key))
                    existing = cursor.fetchone()
            if existing:
                return {"turnId": existing["turn_id"], "runId": existing["run_id"], "status": existing["status"], "duplicate": True}
        turn_id = _new_id("dsh-turn")
        input_data.update({"dshSessionId": session_id, "dshTurnId": turn_id})
        if 'taskIntent' in body:
            input_data['taskIntent'] = body['taskIntent']
        with factory() as con:
            with con.cursor() as cursor:
                cursor.execute("""INSERT INTO dsh_turn(turn_id,session_id,idempotency_key,question,input_json,status)
                                 VALUES(%s,%s,%s,%s,%s,'creating')""",
                               (turn_id, session_id, key, question.strip(), Json(input_data)))
        run_id = None
        stage = "create-run"
        try:
            from .task_intent import admission_actor
            from .run_origin import origin_context
            origin_kind = 'autopilot' if session_id.startswith('dsh-session-ap-') else 'dsh'
            with admission_actor(actor), origin_context(actor, source_kind=origin_kind, source_ref=turn_id):
                run_id = engine.create_run(agent_key, input_data)
            stage = "enqueue"
            with engine.repo.connection_factory() as con:
                with con.cursor() as cursor:
                    cursor.execute("SELECT 1")
            engine.repo.append_event(run_id, "dsh.turn-created", detail={"turnId": turn_id, "sessionId": session_id})
            queue.enqueue(run_id)
            update_turn(turn_id, status="queued", run_id=run_id)
        except Exception as exc:
            logger.error("DSH admission diagnostic %s", json.dumps(
                _admission_diagnostic(exc, turn_id, agent_key, stage), ensure_ascii=True))
            code = "run-admission-rejected" if run_id is None else "run-enqueue-failed"
            if run_id is None and isinstance(exc, BaselineNotReady):
                code = exc.code
            update_turn(turn_id, status="failed", run_id=run_id, error=code)
            from .phase3_repository import RunBudgetAdmissionError, RunBudgetMinimumError
            reason = (exc.admission_details if run_id is None and isinstance(
                exc, (RunBudgetAdmissionError, RunBudgetMinimumError, BaselineNotReady)) else {})
            status = 409 if isinstance(exc, BaselineNotReady) and run_id is None else 400 if run_id is None else 503
            raise HTTPException(status, detail={
                "code": code, "message": "DSH Run admission failed" if run_id is None else "DSH Run could not be queued",
                "turnId": turn_id, "runId": run_id, **reason,
            }) from None
        return {"turnId": turn_id, "runId": run_id, "status": "queued", "agentProfile": agent_key, "mode": mode}

    @router.get("/turns/{turn_id}")
    def get_turn(turn_id: str, actor=Depends(principal)):
        turn = turn_row(turn_id, actor)
        if turn.get("run_id"):
            run = engine.repo.get_run(turn["run_id"])
            status = run.get("status") if run else turn["status"]
            if status in TERMINAL and turn["status"] != "completed":
                answer = report_payload(turn["run_id"], run)
                update_turn(turn_id, status="completed", answer=answer)
            else:
                answer = as_json(turn.get("answer_json")) or None
            return {"turnId": turn_id, "runId": turn["run_id"], "status": status, "answer": answer, "error": turn.get("error_message")}
        return {"turnId": turn_id, "runId": None, "status": turn["status"], "answer": None, "error": turn.get("error_message")}

    @router.get("/turns/{turn_id}/events")
    def turn_events(turn_id: str, after: int = 0, limit: int = 200, actor=Depends(principal)):
        turn = turn_row(turn_id, actor)
        if not turn.get("run_id"):
            return {"events": [], "nextCursor": after}
        try:
            return admin_repository.events_after(turn["run_id"], after, min(limit, 500))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None

    @router.get("/turns/{turn_id}/events/stream")
    async def stream_turn_events(turn_id: str, after: int = 0, actor=Depends(principal)):
        turn = turn_row(turn_id, actor)
        if not turn.get("run_id"):
            raise HTTPException(409, "DSH turn has no Agent Run yet")
        run_id = turn["run_id"]

        async def events():
            cursor = after
            for _ in range(180):
                payload = await asyncio.to_thread(admin_repository.events_after, run_id, cursor, 200)
                rows = payload.get("events", [])
                for row in rows:
                    cursor = max(cursor, int(row["event_id"]))
                    yield "event: agent-event\ndata: " + json.dumps(row, ensure_ascii=False, default=str) + "\n\n"
                run = await asyncio.to_thread(engine.repo.get_run, run_id)
                if run and run.get("status") in TERMINAL:
                    yield "event: run-complete\ndata: " + json.dumps(run_summary(run_id, run), ensure_ascii=False, default=str) + "\n\n"
                    return
                await asyncio.sleep(1)
            yield "event: timeout\ndata: {}\n\n"

        return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @router.get("/runs/{run_id}/status")
    def run_status(run_id: str, actor=Depends(principal)):
        run_access(run_id, actor)
        return run_summary(run_id)

    from .research_governance import ResearchGovernance
    governance = ResearchGovernance(engine, queue)
    from .review_summary import ReviewSummaryService
    review_summaries = ReviewSummaryService(engine, queue)
    from .evolution_repository import EvolutionRepository
    evolution = EvolutionRepository(factory)

    @router.get('/runs/{run_id}/evolution')
    def evolution_view(run_id: str, actor=Depends(principal)):
        run_access(run_id,actor)
        return evolution.view(run_id)

    @router.get('/runs/{run_id}/collaboration')
    def collaboration(run_id: str, actor=Depends(principal)):
        run_access(run_id, actor)
        return governance.collaboration(run_id)

    @router.get('/runs/{run_id}/skills')
    def run_skills(run_id: str, actor=Depends(principal)):
        run_access(run_id, actor)
        from .skill_views import run_view
        return run_view(engine, run_id)

    @router.get('/runs/{run_id}/skills/{invocation_id}')
    def skill_detail(run_id: str, invocation_id: str, actor=Depends(principal)):
        run_access(run_id, actor)
        from .skill_views import invocation_detail
        try: return invocation_detail(engine, run_id, invocation_id)
        except LookupError: raise HTTPException(404, 'Skill invocation not found in this Run') from None

    @router.get('/runs/{run_id}/skill-trials')
    def skill_trials(run_id: str, actor=Depends(principal)):
        run_access(run_id,actor)
        from .skill_trials import SkillTrialService
        return SkillTrialService(engine,queue).list(run_id)

    @router.get('/runs/{run_id}/review-summary')
    def review_summary(run_id: str, actor=Depends(principal)):
        run_access(run_id, actor)
        try:
            return review_summaries.read(run_id, actor=actor)
        except LookupError:
            raise HTTPException(404, 'Run not found') from None
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None

    @router.get('/runs/{run_id}/improvements')
    def improvements(run_id: str, actor=Depends(principal)):
        run_access(run_id, actor)
        return governance.proposals(run_id)

    @router.get('/runs/{run_id}/releases')
    def release_history(run_id: str, actor=Depends(principal)):
        run_access(run_id,actor)
        run = engine.repo.get_run(run_id)
        with factory() as con:
            with con.cursor() as cursor:
                cursor.execute('''SELECT publication_id,agent_key,publication_no,actor_id,reason,published_at,
                    document_json->'definition'->>'current_version' AS version,live_hash
                    FROM agent_config_publication WHERE agent_key=%s ORDER BY publication_no DESC LIMIT 30''',(run['agent_key'],))
                rows=list(cursor.fetchall())
        return {'runId':run_id,'releases':rows}

    @router.post('/runs/{run_id}/improvements', status_code=201)
    def propose_improvement(run_id: str, body: dict, request: Request, actor=Depends(principal)):
        run_access(run_id, actor)
        if set(body) - {'instructions','kind'}:
            raise HTTPException(400, 'unsupported proposal fields')
        authorization = request.headers.get('authorization','')
        token = authorization[7:] if authorization.startswith('Bearer ') else request.cookies.get('agent_admin_session')
        try:
            return governance.propose(run_id,actor=actor,actor_token=token,origin='model',**body)
        except PermissionError as exc:
            raise HTTPException(403,str(exc)) from None
        except (ValueError,KeyError) as exc:
            raise HTTPException(409,str(exc)) from None

    @router.get("/runs/{run_id}/report")
    def run_report(run_id: str, actor=Depends(principal)):
        run_access(run_id, actor)
        return report_payload(run_id)

    @router.get("/cognitive-suite/runs/{run_id}/report")
    def cognitive_run_report(run_id: str, actor=Depends(principal)):
        run_access(run_id, actor)
        run = engine.repo.get_run(run_id)
        if not run or run.get('agent_key') not in COGNITIVE_KEYS:
            raise HTTPException(404, "cognitive research Run not found")
        payload = report_payload(run_id, run)
        if not payload.get('nativeProduct'):
            raise HTTPException(409, "cognitive Run has no typed research product yet")
        return payload

    @router.get("/cognitive-suite/runs/{run_id}/status")
    def cognitive_run_status(run_id: str, actor=Depends(principal)):
        run_access(run_id, actor)
        run = engine.repo.get_run(run_id)
        if not run or run.get('agent_key') not in COGNITIVE_KEYS:
            raise HTTPException(404, "cognitive research Run not found")
        return run_summary(run_id, run)

    @router.post("/cognitive-suite/runs/{run_id}/source")
    def cognitive_run_source(run_id: str, body: dict, actor=Depends(principal)):
        """Resolve only an exact evidence reference already in a visible report."""
        body_size(body)
        if set(body) != {'sourceArtifactRef'} or not isinstance(body['sourceArtifactRef'], dict):
            raise HTTPException(400, 'an exact sourceArtifactRef is required')
        payload = cognitive_run_report(run_id, actor)
        product = payload['report']
        evidences = []
        for field in ('claims', 'checks', 'conflicts', 'ruleFindings'):
            for item in product.get(field, []):
                evidences.extend(item.get('evidence', []))
        matches = [item for item in evidences if item.get('sourceArtifactRef') == body['sourceArtifactRef']]
        if not matches:
            raise HTTPException(404, 'source is not referenced by this cognitive report')
        from .cognitive_contracts import load_verified_artifact, verify_evidence
        ref = matches[0]['sourceArtifactRef']
        try:
            row = load_verified_artifact(ref, [ref], engine.repo)
            for evidence in matches:
                verify_evidence(evidence, [ref], engine.repo)
        except (PermissionError, ValueError, KeyError):
            raise HTTPException(409, {'code': 'cognitive-source-version-invalid', 'runId': run_id}) from None
        content = row['content_json']
        text = next((content.get(name) for name in ('markdown', 'text', 'content', 'body')
                     if isinstance(content.get(name), str)), '')
        return {'sourceArtifactRef': deepcopy(ref), 'sourceUri': content.get('sourceUri'),
                'retrievedAt': content.get('retrievedAt'), 'text': text,
                'evidence': deepcopy(matches), 'readOnly': True}

    @router.get("/runs/{run_id}/ontology-graph")
    def ontology_graph(run_id: str, actor=Depends(principal)):
        run_access(run_id, actor)
        payload = report_payload(run_id)
        if payload["graphValidation"]["status"] == "invalid":
            raise HTTPException(409, detail={"code": "invalid-ontology-projection",
                "message": "本次图谱未通过结构或完整性校验，请查看报告诊断。",
                "runId": run_id, "issues": payload["graphValidation"]["issues"]})
        return payload["ontologyGraph"]

    def explorer_report(run_id: str):
        payload = report_payload(run_id)
        if payload["graphValidation"]["status"] == "invalid":
            raise HTTPException(409, detail={"code": "invalid-ontology-projection",
                "message": "本次图谱未通过结构或完整性校验，请查看报告诊断。",
                "runId": run_id, "issues": payload["graphValidation"]["issues"]})
        from .ontology_quality import enrich_quality
        return enrich_quality(payload, engine.repo)

    @router.get("/runs/{run_id}/explorer")
    def explorer(run_id: str, actor=Depends(principal)):
        run_access(run_id, actor)
        from .ontology_explorer import explorer_graph
        try:
            return explorer_graph(explorer_report(run_id))
        except ValueError as exc:
            raise HTTPException(409, detail={"code": "graph-projection-invalid", "message": str(exc)}) from None

    @router.get("/runs/{run_id}/situation-map")
    def situation_map(run_id: str, request: Request, actor=Depends(principal)):
        """Return a deterministic, read-only situation-map projection of one Run."""
        run_access(run_id, actor)
        allowed_types = {"war-system", "capability", "risk", "logistics",
                         "electromagnetic-cyber", "technology-evolution", "scenario"}
        allowed_levels = {"L0", "L1", "L2", "L3", "L4"}
        allowed_evidence = {"E0", "E1", "E2", "E3", "E4"}
        query = request.query_params
        map_type = query.get("mapType", "war-system")
        perspective_key = query.get("perspectiveKey", "all")
        detail_level = query.get("detailLevel", "L2")
        evidence_level = query.get("evidenceLevel", "E0")
        try:
            evidence_filters = json.loads(query.get('evidenceFilters', '{}'))
        except (ValueError, TypeError):
            raise HTTPException(400, detail={'code': 'situation-map-invalid', 'field': 'evidenceFilters'}) from None
        from .situation_map import EVIDENCE_FILTERS
        if not isinstance(evidence_filters, dict) or set(evidence_filters) - EVIDENCE_FILTERS or any(type(v) is not bool for v in evidence_filters.values()):
            raise HTTPException(400, detail={'code': 'situation-map-invalid', 'field': 'evidenceFilters'})
        if map_type not in allowed_types:
            raise HTTPException(400, detail={"code": "situation-map-invalid", "field": "mapType",
                                             "allowedValues": sorted(allowed_types)})
        if detail_level not in allowed_levels:
            raise HTTPException(400, detail={"code": "situation-map-invalid", "field": "detailLevel",
                                             "allowedValues": sorted(allowed_levels)})
        if evidence_level not in allowed_evidence:
            raise HTTPException(400, detail={"code": "situation-map-invalid", "field": "evidenceLevel",
                                             "allowedValues": sorted(allowed_evidence)})
        if len(perspective_key) > 160:
            raise HTTPException(400, detail={"code": "situation-map-invalid", "field": "perspectiveKey"})
        from .ontology_explorer import explorer_graph
        from .situation_map import build_situation_map, validate_situation_map
        try:
            report = explorer_report(run_id)
            graph = explorer_graph(report)
            projection = build_situation_map(report, graph, map_type=map_type,
                                             perspective_key=perspective_key,
                                             detail_level=detail_level,
                                             minimum_evidence_level=evidence_level,
                                             evidence_filters=evidence_filters)
            validate_situation_map(projection, graph)
            return {"runId": run_id, "projectionHash": projection["contentHash"],
                    "sourceProjectionHash": graph["contentHash"], "situationMap": projection}
        except ValueError as exc:
            raise HTTPException(409, detail={"code": "situation-map-invalid", "message": str(exc)}) from None

    @router.get("/runs/{run_id}/explorer/lineage")
    def explorer_lineage(run_id: str, actor=Depends(principal)):
        run_access(run_id, actor)
        with factory() as con:
            with con.cursor() as cursor:
                cursor.execute('SELECT previous_run_id,run_id FROM agent_run_followup WHERE previous_run_id=%s OR run_id=%s ORDER BY created_at', (run_id, run_id))
                links = [dict(row) for row in cursor.fetchall()]
        visible = []
        for link in links:
            other = link['previous_run_id'] if link['run_id'] == run_id else link['run_id']
            try:
                run_access(other, actor)
            except HTTPException as exc:
                if exc.status_code == 404:
                    continue
                raise
            visible.append({'runId': other, 'direction': 'parent' if link['run_id'] == run_id else 'successor'})
        return {'runId': run_id, 'links': visible}

    @router.post("/runs/{run_id}/explorer/compare")
    def explorer_compare(run_id: str, body: dict, actor=Depends(principal)):
        run_access(run_id, actor)
        body_size(body)
        other = body.get('otherRunId')
        if set(body) != {'otherRunId', 'projectionHash'} or not isinstance(other, str) or not 1 <= len(other) <= 200:
            raise HTTPException(400, 'otherRunId and projectionHash are required')
        run_access(other, actor)
        with factory() as con:
            with con.cursor() as cursor:
                cursor.execute('SELECT previous_run_id,run_id FROM agent_run_followup WHERE '
                               '(previous_run_id=%s AND run_id=%s) OR (previous_run_id=%s AND run_id=%s)',
                               (run_id, other, other, run_id))
                link = cursor.fetchone()
        if not link:
            raise HTTPException(400, 'only directly linked report versions can be compared')
        from .ontology_explorer import explorer_graph
        from .ontology_semantics import compare_graphs
        current_report = explorer_report(run_id)
        current = explorer_graph(current_report)
        if current['contentHash'] != body['projectionHash']:
            raise HTTPException(409, detail={'code': 'projection-changed', 'message': '图谱版本已变化，请重新打开。'})
        linked_report = explorer_report(other)
        linked = explorer_graph(linked_report)
        successor = current_report if link['run_id'] == run_id else linked_report
        followup = successor.get('followup') or {}
        target = (followup.get('followupTarget') or {}).get('perspectiveKey')
        scope = [target] if followup.get('followupMode') == 'targeted-evidence' and target else None
        if link['run_id'] == run_id:
            return compare_graphs(linked, current, perspective_keys=scope)
        return compare_graphs(current, linked, perspective_keys=scope)

    @router.post("/runs/{run_id}/explorer/selection")
    def explorer_selection(run_id: str, body: dict, actor=Depends(principal)):
        access = run_access(run_id, actor)
        body_size(body)
        if (set(body) - {"kind", "id", "projectionHash"} or not isinstance(body.get("kind"), str) or body["kind"] not in {"node", "edge"}
                or not isinstance(body.get("id"), str) or not 1 <= len(body["id"]) <= 4096):
            raise HTTPException(400, "selection requires a node/edge kind and a bounded id")
        from .ontology_explorer import selection_detail
        try:
            detail = selection_detail(explorer_report(run_id), body["kind"], body["id"])
            if "projectionHash" in body and body["projectionHash"] != detail['projectionHash']:
                raise HTTPException(409, detail={"code": "projection-changed", "message": "图谱版本已变化，请重新打开当前运行的图谱。"})
            return {**detail, 'researchTurnId': access.get('turn_id')}
        except KeyError:
            raise HTTPException(404, "所选对象不属于当前运行图谱。") from None
        except ValueError as exc:
            raise HTTPException(409, detail={"code": "graph-projection-invalid", "message": str(exc)}) from None

    @router.post("/runs/{run_id}/explorer/source")
    def explorer_source(run_id: str, body: dict, actor=Depends(principal)):
        run_access(run_id, actor)
        body_size(body)
        if (set(body) - {"evidenceId", "projectionHash"} or not isinstance(body.get("evidenceId"), str)
                or not 1 <= len(body["evidenceId"]) <= 1024):
            raise HTTPException(400, "a bounded evidenceId is required")
        from .ontology_explorer import source_excerpt, explorer_graph
        try:
            report = explorer_report(run_id)
            projection_hash = explorer_graph(report)['contentHash']
            if "projectionHash" in body and body["projectionHash"] != projection_hash:
                raise HTTPException(409, detail={"code": "projection-changed", "message": "图谱版本已变化，请重新打开当前运行的图谱。"})
            return {**source_excerpt(report, body["evidenceId"], engine.repo), "projectionHash": projection_hash}
        except KeyError:
            raise HTTPException(404, "本次运行未保存该证据的来源快照。") from None
        except ValueError:
            raise HTTPException(409, detail={"code": "source-integrity-mismatch",
                "message": "来源快照、内容摘要或摘录定位不一致，无法展示为可核验依据。"}) from None

    @router.post("/turns/{turn_id}/followups", status_code=202)
    def followup(turn_id: str, body: dict, actor=Depends(principal)):
        turn = turn_row(turn_id, actor)
        if not turn.get("run_id"):
            raise HTTPException(409, "DSH turn has no completed Agent Run")
        allowed = {"reason", "input", "idempotencyKey"}
        if set(body) - allowed or not isinstance(body.get("reason"), str) or not body["reason"].strip():
            raise HTTPException(400, "reason and optional input are required")
        from .followup import FollowupService
        service = FollowupService(engine, queue)
        try:
            result = service.create(turn["run_id"], actor, idempotency_key=body.get("idempotencyKey") or _new_id("dsh-followup"),
                                    reason=body["reason"].strip(), changes=body.get("input") or {})
        except Exception as exc:
            raise HTTPException(400, "DSH follow-up admission failed") from exc
        return result

    from .autopilot import create_autopilot_router
    router.include_router(create_autopilot_router(factory=factory, engine=engine, principal=principal,
        capabilities=capabilities, create_turn=create_turn, normalize=_normalise_future_input))
    from .intent_alignment import create_alignment_router
    router.include_router(create_alignment_router(factory, principal))
    from .mission_service import create_mission_router
    router.include_router(create_mission_router(factory, engine, principal))
    from .supervision_api import create_supervision_router
    router.include_router(create_supervision_router(factory, engine, principal))
    return router

