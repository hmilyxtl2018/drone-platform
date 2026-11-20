"""Input-envelope checks shared by the task console and immutable Runs."""
from __future__ import annotations

from agent_engine.application_identity import FUTURE_AGENT_KEYS

from datetime import date
from typing import Any


FUTURE_LAYERS = {"strategic", "operational", "tactical", "technical"}
FUTURE_SOURCES = {"official", "research", "industry", "events"}
FUTURE_PERSPECTIVES = {
    "war-system", "unmanned-autonomy", "sensing-data-link", "electromagnetic-cyber",
    "industrial-logistics", "countermeasure-iteration", "cognitive-information", "law-escalation",
}
TASK_KINDS = {
    "future-battlefield-analyst": "future-battlefield-judgment",
    "future-battlefield-codex": "future-battlefield-judgment",
    "uav-capability-analyst": "uav-capability-analysis",
    "uav-design-engineer": "uav-design",
    "uav-evidence-reviewer": "uav-evidence-review",
}


def _bounded_text(value: Any, name: str, limit: int, *, required: bool = False) -> str:
    if not isinstance(value, str) or len(value) > limit or (required and not value.strip()):
        raise ValueError(f"{name} must be {'non-empty ' if required else ''}text up to {limit} characters")
    return value


def _choice_list(value: Any, allowed: set[str], name: str, *, minimum: int = 0) -> list[str]:
    if (not isinstance(value, list) or len(value) > len(allowed) or
            any(not isinstance(item, str) or item not in allowed for item in value) or
            len(set(value)) != len(value) or len(value) < minimum):
        raise ValueError(f"{name} contains invalid or duplicate choices")
    return value


def _future_specific_settings(input_data: dict[str, Any], specific: dict[str, Any]) -> None:
    _bounded_text(input_data.get("question"), "question", 10000, required=True)
    if specific.get("schemaVersion") != "1.0" or specific.get("kind") != "future-battlefield-judgment":
        raise ValueError("future battlefield task settings require schema 1.0")
    scope = input_data.get("scope")
    if not isinstance(scope, dict):
        raise ValueError("future battlefield scope is required")
    time_window = scope.get("timeWindow")
    if not isinstance(time_window, dict):
        raise ValueError("future battlefield time window is required")
    as_of = _bounded_text(time_window.get("asOf"), "scope.timeWindow.asOf", 10, required=True)
    try:
        if date.fromisoformat(as_of).isoformat() != as_of:
            raise ValueError
    except ValueError as exc:
        raise ValueError("scope.timeWindow.asOf must be an ISO calendar date") from exc
    horizon = time_window.get("horizonYears")
    if type(horizon) is not int or horizon not in {1, 3, 5, 10}:
        raise ValueError("scope.timeWindow.horizonYears must be 1, 3, 5 or 10")
    _bounded_text(scope.get("geography"), "scope.geography", 500, required=True)
    _bounded_text(scope.get("summary"), "scope.summary", 4000, required=True)
    _bounded_text(scope.get("constraints"), "scope.constraints", 4000, required=True)
    layers = _choice_list(scope.get("layers"), FUTURE_LAYERS, "scope.layers", minimum=1)
    _choice_list(scope.get("sourceTypes"), FUTURE_SOURCES, "scope.sourceTypes", minimum=1)
    perspectives = input_data.get("perspectives")
    if not isinstance(perspectives, list) or not 1 <= len(perspectives) <= len(FUTURE_PERSPECTIVES):
        raise ValueError("future battlefield requires between one and eight perspectives")
    keys: set[str] = set()
    for index, item in enumerate(perspectives):
        path = f"perspectives[{index}]"
        if not isinstance(item, dict):
            raise ValueError(f"{path} must be an object")
        key = item.get("key")
        if not isinstance(key, str) or key not in FUTURE_PERSPECTIVES or key in keys:
            raise ValueError(f"{path}.key is invalid or duplicated")
        keys.add(key)
        _bounded_text(item.get("name"), path + ".name", 100, required=True)
        _bounded_text(item.get("question"), path + ".question", 500, required=True)
        if item.get("layer") not in layers:
            raise ValueError(f"{path}.layer must be one of the selected research layers")
    if input_data.get("entityId") or input_data.get("uavId") or input_data.get("entitySelection"):
        raise ValueError("future battlefield research does not accept a UAV ontology object selection")
    if specific.get("asOf") != as_of or specific.get("horizonYears") != horizon:
        raise ValueError("future battlefield settings do not match the frozen research scope")
    if specific.get("theater") != scope.get("geography") or specific.get("scopeNote") != scope.get("summary"):
        raise ValueError("future battlefield settings do not match the frozen research scope")
    if specific.get("layers") != layers or specific.get("sourceTypes") != scope.get("sourceTypes"):
        raise ValueError("future battlefield settings do not match the frozen research scope")
    if specific.get("perspectives") != perspectives or specific.get("constraints") != scope.get("constraints"):
        raise ValueError("future battlefield settings do not match the frozen analysis perspectives")
    context = input_data.get("analysisContext")
    expected_context = {"asOf":as_of, "horizonYears":horizon, "theater":scope["geography"],
                        "layers":layers, "sourceTypes":scope["sourceTypes"], "constraints":scope["constraints"]}
    if context is not None and context != expected_context:
        raise ValueError("future battlefield analysis context does not match the frozen research scope")


def validate_task_envelope(agent_key: str, profile, input_data: dict[str, Any]) -> None:
    """Validate explicit settings when present; legacy direct Engine clients remain valid."""
    from .data_contracts import validate_admission
    validate_admission(profile, input_data)
    if 'decisionBrief' in input_data:
        from .decision_semantics import validate_brief
        validate_brief(input_data['decisionBrief'])
    common = input_data.get("commonSettings")
    if common is not None:
        if not isinstance(common, dict) or common.get("schemaVersion") != "1.0":
            raise ValueError("common task settings require schema 1.0")
        if common.get("agentKey") != agent_key or common.get("agentVersion") != profile.version:
            raise ValueError("common task settings do not match the selected Agent Profile")
        if common.get("executionMode") != input_data.get("executionMode"):
            raise ValueError("common task settings do not match the selected execution mode")
        _bounded_text(common.get("taskGoal"), "commonSettings.taskGoal", 10000, required=True)
        if common.get("budgetLimits") != input_data.get("budgetLimits", {}):
            raise ValueError("common task budget does not match the Run budget")
    specific = input_data.get("specificSettings")
    if specific is None:
        return
    if not isinstance(specific, dict):
        raise ValueError("specific task settings must be an object")
    expected_kind = TASK_KINDS.get(agent_key)
    if expected_kind and specific.get("kind") != expected_kind:
        raise ValueError("specific task settings do not match the selected Agent")
    if agent_key in FUTURE_AGENT_KEYS:
        _future_specific_settings(input_data, specific)
    elif specific.get("schemaVersion") != "1.0":
        raise ValueError("specific task settings require schema 1.0")
    if agent_key in {"uav-capability-analyst", "uav-design-engineer", "uav-evidence-reviewer"}:
        context = input_data.get("analysisContext", {})
        requirements = input_data.get("requirements", {})
        metrics = specific.get("metrics")
        # JSON objects (including PostgreSQL JSONB) have no key order. Only
        # membership is part of this contract; the explicit list still must
        # contain each selected metric exactly once.
        metrics_match = (isinstance(requirements, dict) and isinstance(metrics, list)
                         and all(isinstance(metric, str) for metric in metrics)
                         and len(metrics) == len(set(metrics))
                         and set(metrics) == set(requirements))
        if (not isinstance(context, dict) or not isinstance(requirements, dict) or
                specific.get("entityId") != input_data.get("entityId") or
                specific.get("entitySelection") != input_data.get("entitySelection") or
                not metrics_match or
                specific.get("purpose") != input_data.get("analysisPurpose") or
                specific.get("configuration") != context.get("configuration", "") or
                specific.get("requirementText") != context.get("requirementText", "")):
            raise ValueError("UAV task settings do not match the frozen ontology selection and requirements")
    if specific.get("kind") == "action":
        from jsonschema import validate
        from .data_contracts import VERSION
        if getattr(profile, 'model_policy', {}).get('config_json', {}).get('dataContractVersion') == VERSION:
            schema = profile.steps[0].input_contract['properties']['input']
            data = specific.get('input')
            if (not isinstance(data, dict) or set(data) - set(schema.get('properties', {}))
                    or any(input_data.get(key) != value for key, value in data.items())):
                raise ValueError('Action settings do not match the frozen Action input')
            validate(data, schema)
            return
        from agent_tools.future_battlefield import ARGUMENT_SCHEMAS
        action_tools = {
            "evidence-collection-action": ["analysis.source_deduplicate"],
            "evidence-extraction-action": ["analysis.citation_validate", "analysis.event_normalize"],
            "trend-analysis-action": ["analysis.change_point"],
            "scenario-analysis-action": ["analysis.scenario_structure"],
            "red-team-action": ["analysis.claim_audit"],
        }
        tool_keys = action_tools.get(agent_key)
        data = specific.get("input")
        if not tool_keys or not isinstance(data, dict):
            raise ValueError("Action input contract is not registered")
        allowed = {key for tool in tool_keys for key in ARGUMENT_SCHEMAS[tool]["properties"]}
        if set(data) - allowed or any(input_data.get(key) != value for key, value in data.items()):
            raise ValueError("Action settings do not match the frozen Action input")
        for tool in tool_keys:
            schema = ARGUMENT_SCHEMAS[tool]
            validate({key:value for key, value in data.items() if key in schema["properties"]}, schema)


__all__ = ["validate_task_envelope"]
