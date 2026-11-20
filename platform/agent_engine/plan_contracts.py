"""Deterministic contracts for plans. A validated plan is data, never code."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import ipaddress
import json
import re
from urllib.parse import parse_qsl, urlsplit

from jsonschema import Draft202012Validator, SchemaError, ValidationError


PLAN_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["schemaVersion", "goal", "nodes"],
    "properties": {
        "schemaVersion": {"const": "1"}, "goal": {"type": "string", "minLength": 1, "maxLength": 2000},
        "nodes": {"type": "array", "minItems": 1, "maxItems": 24, "items": {
            "type": "object", "additionalProperties": False,
            "required": ["id", "action", "dependsOn", "toolKey", "arguments", "reason"],
            "properties": {
                "id": {"type": "string", "pattern": "^[A-Za-z][A-Za-z0-9_-]{0,63}$"},
                "action": {"enum": ["tool", "report", "request-review", "stop"]},
                "dependsOn": {"type": "array", "uniqueItems": True, "maxItems": 23,
                              "items": {"type": "string", "pattern": "^[A-Za-z][A-Za-z0-9_-]{0,63}$"}},
                "toolKey": {"type": ["string", "null"], "maxLength": 128},
                "arguments": {"type": "object"},
                "reason": {"type": "string", "minLength": 1, "maxLength": 1000},
            }}}
    }
}


def _args(properties, required=()):
    return {"type": "object", "properties": properties, "required": list(required), "additionalProperties": False}


_ID = {"type": "string", "minLength": 1, "maxLength": 200}
_IDS = {"type": "array", "maxItems": 100, "items": _ID, "uniqueItems": True}
SOURCE_ARGUMENT_SCHEMAS = {
    "ontology.resolve_entity": _args({"entityId": _ID, "query": {"type": "string", "minLength": 1, "maxLength": 200},
                                      "limit": {"type": "integer", "minimum": 1, "maximum": 20}}),
    "ontology.get_entity": _args({"entityId": _ID, "revision": {"type": "integer", "minimum": 1}}),
    "ontology.query_evidence": _args({"entityId": _ID, "revision": {"type": "integer", "minimum": 1},
                                      "metrics": _IDS, "attributes": _IDS, "evidenceIds": _IDS}),
    "web.search": _args({"query": {"type": "string", "minLength": 1, "maxLength": 1000},
                         "limit": {"type": "integer", "minimum": 1, "maximum": 6}}, ("query",)),
    "web.scrape": _args({"url": {"type": "string", "minLength": 1, "maxLength": 4096}}, ("url",)),
}
_FORBIDDEN_KEYS = frozenset({"endpoint", "baseurl", "base_url", "headers", "authorization", "credential",
                           "credentialref", "api_key", "apikey", "sql", "shell", "command", "script",
                           "python", "code", "executable", "sourcepath", "path", "method"})


class PlanValidationError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class ValidatedPlan:
    plan: dict
    topological_order: tuple[str, ...]
    estimated_charges: dict[str, int]


def _schema_validate(value, schema):
    try:
        # Plan parameters must never resolve file or remote schema references.
        def refs(item):
            if isinstance(item, dict):
                if "$ref" in item or "$dynamicRef" in item:
                    raise PlanValidationError("tool-schema-references-unsupported")
                for child in item.values():
                    refs(child)
            elif isinstance(item, list):
                for child in item:
                    refs(child)
        refs(schema)
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(value)
    except (SchemaError, ValidationError, TypeError, RecursionError):
        raise PlanValidationError("plan-schema-invalid") from None


def argument_schema(binding):
    schema = binding.input_schema or {}
    configured = getattr(binding, "config", {}).get("argumentSchema")
    props = schema.get("properties", {})
    nested = props.get("arguments")
    if configured:
        return configured
    if isinstance(nested, dict) and nested.get("additionalProperties") is False:
        return nested
    if binding.key in SOURCE_ARGUMENT_SCHEMAS:
        return SOURCE_ARGUMENT_SCHEMAS[binding.key]
    if schema.get("additionalProperties") is False and not {"input", "state"}.intersection(props):
        return schema
    raise PlanValidationError("tool-argument-contract-required")


def build_tool_payload(ctx, node):
    binding = ctx.profile.tools[node["toolKey"]]
    schema = binding.input_schema or {}
    if {"input", "state"}.intersection(schema.get("properties", {})) or {"input", "state"}.intersection(schema.get("required", [])):
        return {"input": deepcopy(ctx.input), "state": deepcopy(ctx.state), "arguments": deepcopy(node["arguments"])}
    return deepcopy(node["arguments"])


def _validate_source_url(value, profile):
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or "").encode("idna").decode("ascii").lower().rstrip(".")
        if (parsed.scheme != "https" or not host or parsed.username is not None or parsed.password is not None
                or parsed.port not in (None, 443) or "\\" in value or any(c.isspace() or ord(c) < 32 for c in value)):
            raise PlanValidationError("source-url-invalid")
    except (ValueError, UnicodeError):
        raise PlanValidationError("source-url-invalid") from None
    policy = getattr(profile, "source_policy", {}) or {}
    domains = policy.get("allowedDomains", [])
    if not isinstance(domains, list) or not any(host == domain.lower().rstrip(".") or
            (policy.get("includeSubdomains") is True and host.endswith("." + domain.lower().rstrip(".")))
            for domain in domains if isinstance(domain, str)):
        raise PlanValidationError("source-url-outside-policy")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        raise PlanValidationError("source-url-not-public")
    if any(re.search(r"api.?key|token|password|secret", key, re.I) for key, _ in parse_qsl(parsed.query)):
        raise PlanValidationError("source-url-credentials-rejected")


def _check_arguments(value, profile):
    if isinstance(value, dict):
        for key, child in value.items():
            if key.lower() in _FORBIDDEN_KEYS or key.startswith("$"):
                raise PlanValidationError("tool-argument-forbidden")
            _check_arguments(child, profile)
    elif isinstance(value, list):
        for child in value:
            _check_arguments(child, profile)
    elif isinstance(value, str) and (value.startswith(("https://", "http://", "file://"))):
        _validate_source_url(value, profile)


def _check_scope(args, ctx):
    policy = getattr(ctx.profile, "scope_policy", {}) or {}
    allowed_entities = policy.get("allowedEntities")
    if allowed_entities is None:
        entity = ctx.input.get("entityId") or ctx.input.get("uavId")
        allowed_entities = [entity] if entity else None
    if allowed_entities is not None and args.get("entityId") is not None and args["entityId"] not in allowed_entities:
        raise PlanValidationError("plan-entity-outside-scope")
    allowed_metrics = policy.get("allowedMetrics")
    if allowed_metrics is not None:
        metrics = args.get("metrics", [args["metric"]] if "metric" in args else [])
        if not isinstance(metrics, list) or any(metric not in allowed_metrics for metric in metrics):
            raise PlanValidationError("plan-metric-outside-scope")


def _known_source_urls(ctx):
    urls = set(ctx.state.get("webSourceUrls", []))
    for value in ctx.state.get("researchTools", {}).values():
        urls.update(item["sourceUri"] for item in value.get("results", []) if item.get("sourceUri"))
    for snapshot in ctx.state.get("ontologySnapshots", {}).values():
        urls.update(item["sourceUri"] for item in snapshot.get("sourceEvidence", []) if item.get("sourceUri"))
    return urls


def validate_plan(plan, ctx, remaining_budget, *, max_nodes=24, allowed_tool_keys=None) -> ValidatedPlan:
    """Validate against immutable Profile and a fresh remaining-budget view.

    Charges reserve worst-case configured retries for all planned tool actions.
    Tool/Model gateways still recheck and atomically reserve at execution time.
    """
    try:
        raw = json.dumps(plan, ensure_ascii=False, allow_nan=False)
        if len(raw.encode("utf-8")) > 200_000:
            raise PlanValidationError("plan-too-large")
    except (ValueError, TypeError, RecursionError):
        raise PlanValidationError("plan-not-json") from None
    _schema_validate(plan, PLAN_SCHEMA)
    configured_max = ctx.profile.resource_policy.get("max_plan_nodes", 24)
    if type(configured_max) is not int or type(max_nodes) is not int or not 1 <= min(configured_max, max_nodes) <= 24:
        raise PlanValidationError("invalid-node-limit")
    if len(plan["nodes"]) > min(configured_max, max_nodes):
        raise PlanValidationError("plan-node-limit")
    if not isinstance(remaining_budget, dict) or any(type(v) is not int or v < 0 for v in remaining_budget.values()):
        raise PlanValidationError("invalid-remaining-budget")
    nodes = {node["id"]: node for node in plan["nodes"]}
    if len(nodes) != len(plan["nodes"]):
        raise PlanValidationError("duplicate-plan-node")
    charges, remaining_dependencies = {}, {}
    allowed_tool_keys = None if allowed_tool_keys is None else set(allowed_tool_keys)
    permitted_risks = ctx.profile.resource_policy.get("permitted_risk_levels_json", ["low", "medium"])
    if isinstance(permitted_risks, str):
        try:
            permitted_risks = json.loads(permitted_risks)
        except ValueError:
            raise PlanValidationError("invalid-profile-risk-policy") from None
    for node in nodes.values():
        dependencies = set(node["dependsOn"])
        if not dependencies.issubset(nodes) or node["id"] in dependencies:
            raise PlanValidationError("invalid-plan-dependency")
        remaining_dependencies[node["id"]] = dependencies
        if node["action"] != "tool":
            if node["toolKey"] is not None or node["arguments"]:
                raise PlanValidationError("non-tool-parameters-rejected")
            continue
        binding = ctx.profile.tools.get(node["toolKey"])
        if not binding or not binding.enabled:
            raise PlanValidationError("tool-not-enabled")
        if allowed_tool_keys is not None and node["toolKey"] not in allowed_tool_keys:
            raise PlanValidationError("tool-outside-research-envelope")
        if binding.risk_level not in permitted_risks or binding.access_mode != "read_only":
            raise PlanValidationError("tool-outside-planner-scope")
        if (getattr(binding, "protocol", "python") not in {"python", "http", "https"}
                or set(re.split(r"[._:-]", binding.key.lower())).intersection({"sql", "shell", "python", "eval", "exec"})):
            raise PlanValidationError("executable-tool-outside-planner-scope")
        _check_arguments(node["arguments"], ctx.profile)
        _check_scope(node["arguments"], ctx)
        if binding.key == 'web.scrape' and node['arguments'].get('url') not in _known_source_urls(ctx):
            raise PlanValidationError('scrape-url-not-established')
        _schema_validate(node["arguments"], argument_schema(binding))
        _schema_validate(build_tool_payload(ctx, node), binding.input_schema or {})
        retry_cap = ctx.profile.resource_policy.get("max_retries_per_tool", binding.retry_limit)
        if type(retry_cap) is not int or type(binding.retry_limit) is not int or min(retry_cap, binding.retry_limit) < 0:
            raise PlanValidationError("invalid-profile-retry-policy")
        attempts = 1 + min(retry_cap, binding.retry_limit)
        charges["tool_calls"] = charges.get("tool_calls", 0) + attempts
        if binding.key in {"web.search", "web.scrape"}:
            resource = "search_calls" if binding.key == "web.search" else "scrape_calls"
            charges[resource] = charges.get(resource, 0) + attempts
            charges["http_calls"] = charges.get("http_calls", 0) + attempts
        elif getattr(binding, "protocol", "python") in {"http", "https"}:
            charges["http_calls"] = charges.get("http_calls", 0) + attempts
    order = []
    while remaining_dependencies:
        ready = sorted(key for key, dependencies in remaining_dependencies.items() if not dependencies)
        if not ready:
            raise PlanValidationError("plan-dependency-cycle")
        order.extend(ready)
        for key in ready:
            del remaining_dependencies[key]
        for dependencies in remaining_dependencies.values():
            dependencies.difference_update(ready)
    if any(value > remaining_budget.get(key, 0) for key, value in charges.items()):
        raise PlanValidationError("plan-exceeds-remaining-budget")
    for node in nodes.values():
        if any(nodes[key]["action"] in {"stop", "report", "request-review"} for key in node["dependsOn"]):
            raise PlanValidationError("terminal-plan-node-has-dependent")
    terminals = [node for node in nodes.values() if node['action'] != 'tool']
    if len(terminals) > 1:
        raise PlanValidationError('multiple-terminal-plan-nodes')
    if terminals:
        ancestors, pending = set(), list(terminals[0]['dependsOn'])
        while pending:
            dependency = pending.pop()
            if dependency not in ancestors:
                ancestors.add(dependency)
                pending.extend(nodes[dependency]['dependsOn'])
        if any(node['action'] == 'tool' and node['id'] not in ancestors for node in nodes.values()):
            raise PlanValidationError('terminal-must-depend-on-all-tools')
    return ValidatedPlan(deepcopy(plan), tuple(order), charges)


__all__ = ["PLAN_SCHEMA", "SOURCE_ARGUMENT_SCHEMAS", "PlanValidationError", "ValidatedPlan",
           "validate_plan", "argument_schema", "build_tool_payload"]
