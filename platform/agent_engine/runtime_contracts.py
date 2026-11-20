"""Fail-closed checks for fixed Plan execution.

The authoring tables describe a node, but a description is not an executor.
This module keeps the small set of legacy built-ins explicit and rejects any
fixed node whose output contract cannot be produced by the generic tool
envelope.  Application-specific executors can be added to the registry as
versioned implementations; merely naming one in JSON is insufficient.
"""
from __future__ import annotations

from agent_engine.application_identity import FUTURE_AGENT_KEYS

from typing import Any
import os
from urllib.parse import urlsplit


GENERIC_OUTPUT_FIELDS = frozenset({"stepKey", "inputs", "toolResults"})

# These are implemented directly by StepExecutor and therefore do not need a
# model/tool adapter.  Keep this list intentionally small and reviewable.
BUILTIN_EXECUTOR_KEYS = {
    "parse-requirement": "builtin.parse_requirement.v1",
    # Conservative fixed executors for the Future Battlefield research graph.
    # They preserve unverified inputs and gaps; they do not invent facts.
    "define-research-question": "future-battlefield.define_question.v1",
    "generate-research-hypotheses": "future-battlefield.generate_hypotheses.v1",
    "plan-source-coverage": "future-battlefield.plan_sources.v1",
    "collect-public-evidence": "future-battlefield.collect_evidence.v1",
    "extract-facts": "future-battlefield.extract_facts.v1",
    "extract-war-system": "future-battlefield.extract_war_system.v1",
    "fuse-evidence-and-conflicts": "future-battlefield.fuse_evidence.v1",
    "analyze-by-perspective": "future-battlefield.analyze_perspectives.v1",
    "red-team-challenge": "future-battlefield.red_team.v1",
    "compose-judgment-report": "future-battlefield.compose_report.v1",
    "human-review": "future-battlefield.human_review_gate.v1",
    "extract-source-claims": "action.extract_source_claims.v1",
    "validate-citations": "action.validate_citations.v1",
    "review-perspective-evidence": "action.review_perspective_evidence.v1",
    "structure-scenario-judgment": "action.structure_scenarios.v1",
    "detect-trend-change-points": "action.evidence_series_cusum.v1",
}

# Concrete handlers for these reviewed keys are installed by AgentEngine.
from .cognitive_identity import NODE_IDS as COGNITIVE_NODE_IDS
BUILTIN_EXECUTOR_KEYS.update({key: 'cognitive.' + key + '.v1' for key in COGNITIVE_NODE_IDS})


def _config(profile) -> dict[str, Any]:
    model = getattr(profile, "model_policy", {}) or {}
    value = model.get("config_json", {}) if isinstance(model, dict) else {}
    return value if isinstance(value, dict) else {}


def _node_policies(profile) -> dict[str, Any]:
    baseline = _config(profile).get("executionBaseline", {})
    policies = baseline.get("nodePolicies", {}) if isinstance(baseline, dict) else {}
    return policies if isinstance(policies, dict) else {}


def _required_fields(contract: Any) -> set[str]:
    if not isinstance(contract, dict):
        return set()
    required = contract.get("required", [])
    return {value for value in required if isinstance(value, str)} if isinstance(required, list) else set()


def fixed_runtime_errors(profile) -> list[dict[str, Any]]:
    """Return preflight failures before a fixed Run is persisted.

    A node with tools may use the generic envelope only when its contract does
    not demand domain fields.  Domain fields require a registered executor;
    otherwise the old runtime would persist a step and fail after starting it.
    """
    config = _config(profile)
    if config.get("engineMode") == "research-v1" and profile.agent_key not in FUTURE_AGENT_KEYS:
        return []
    policies = _node_policies(profile)
    errors: list[dict[str, Any]] = []
    # Future Battlefield is a fixed, reviewed DAG, but its collection node is
    # still a Research boundary.  Admit a new Run only when the immutable
    # Profile exposes both acquisition tools and non-zero resource ceilings.
    # This prevents a configuration that can silently produce an empty report.
    if profile.agent_key in FUTURE_AGENT_KEYS:
        if config.get("researchModeAvailable") is not True:
            errors.append({"stepKey": "profile", "code": "research-mode-disabled"})
        for key, resource in (("web.search", "search_calls"), ("web.scrape", "scrape_calls")):
            binding = getattr(profile, "tools", {}).get(key)
            if not binding or not binding.enabled:
                errors.append({"stepKey": "collect-public-evidence", "code": "web-tool-binding-missing", "toolKey": key})
            if int((config.get("budgetLimits") or {}).get(resource, 0) or 0) <= 0:
                errors.append({"stepKey": "collect-public-evidence", "code": "web-budget-zero", "resource": resource})
        if int((config.get("budgetLimits") or {}).get("model_calls", 0) or 0) <= 0:
            errors.append({"stepKey": "profile", "code": "model-budget-zero"})
        source_policy = config.get("sourcePolicy") or {}
        if not isinstance(source_policy.get("allowedDomains"), list) or not source_policy.get("allowedDomains"):
            errors.append({"stepKey": "collect-public-evidence", "code": "source-allowlist-missing"})
        contract = getattr(profile, "harness_contract", {}) or {}
        grants = [item for item in contract.get("toolGrants", []) if isinstance(item, dict)]
        for key in ("web.search", "web.scrape"):
            binding = getattr(profile, "tools", {}).get(key)
            if not any(item.get('tool_key') == key and binding and item.get('tool_version') == binding.version
                       and item.get('permission') in {'invoke','read'} and not item.get('approvalRequired')
                       for item in grants):
                errors.append({"stepKey": "collect-public-evidence", "code": "harness-tool-grant-missing", "toolKey": key})
        networks = {item.get("domain_pattern") for item in contract.get("networkGrants", [])
                    if isinstance(item, dict) and 'GET' in item.get('methods', []) and not item.get('approvalRequired')}
        missing_domains = sorted(set(source_policy.get("allowedDomains", [])) - networks)
        if source_policy.get('includeSubdomains'):
            missing_domains += sorted({'*.' + domain for domain in source_policy.get('allowedDomains', [])} - networks)
        if missing_domains:
            errors.append({"stepKey": "collect-public-evidence", "code": "harness-network-grant-missing", "domains": missing_domains})
    for step in getattr(profile, "steps", ()):
        policy = policies.get(step.key, {})
        policy = policy if isinstance(policy, dict) else {}
        executor_key = policy.get("executorKey")
        if executor_key is not None and (not isinstance(executor_key, str) or not executor_key.strip()):
            errors.append({"stepKey": step.key, "code": "invalid-executor-key"})
            continue
        if executor_key:
            # An executor is valid only after its implementation is registered
            # in this reviewed runtime registry.  This prevents configuration
            # JSON from becoming a false capability declaration.
            if executor_key not in BUILTIN_EXECUTOR_KEYS.values():
                errors.append({"stepKey": step.key, "code": "executor-not-registered",
                               "executorKey": executor_key})
            continue
        if step.key in BUILTIN_EXECUTOR_KEYS:
            continue
        required = _required_fields(getattr(step, "output_contract", {}))
        unsupported = sorted(required - GENERIC_OUTPUT_FIELDS)
        # Legacy orchestration has bookkeeping/finalization steps with an
        # intentionally empty contract.  Those remain valid as long as they
        # do not claim to produce domain output.  Any declared field requires
        # a reviewed executor, regardless of whether tools are attached.
        if unsupported or (not getattr(step, "tools", ()) and required):
            errors.append({"stepKey": step.key, "code": "executor-missing",
                           "requiredFields": unsupported,
                           "message": "fixed node has no reviewed executor implementation"})
    return errors


def validate_fixed_runtime(profile) -> None:
    errors = fixed_runtime_errors(profile)
    if errors:
        details = "; ".join(
            f"{item['stepKey']} ({item['code']})" +
            (f": {','.join(item['requiredFields'])}" if item.get("requiredFields") else "")
            for item in errors
        )
        raise ValueError("fixed runtime preflight failed: " + details)


def future_budget_minimums(profile):
    """One round is a stage entry, not a web request or model thought."""
    minimums = dict(model_calls=2, search_calls=1, scrape_calls=1, http_calls=4,
                    tool_calls=8, input_tokens=1, output_tokens=1)
    stages = (getattr(profile, 'reasoning_policy', {}) or {}).get('stages', [])
    required = sum(stage.get('required', True) for stage in stages)
    if required:
        minimums['reasoning_rounds'] = required
    return minimums


def validate_future_research_admission(profile, input_data, *, environment=None):
    """Check effective (including user-reduced) budgets before any Run write."""
    config = _config(profile)
    validate_fixed_runtime(profile)
    if input_data.get('executionMode') == 'research' or config.get('engineMode') == 'research-v1':
        raise ValueError('Future Battlefield research uses its fixed DAG; executionMode must be profile-run')
    automation_mode = input_data.get('automationMode', 'unattended-draft')
    if not isinstance(automation_mode, str) or automation_mode not in {'unattended-draft', 'expert-draft', 'human-gate'}:
        raise ValueError('future battlefield automationMode must be unattended-draft, expert-draft or human-gate')
    question, perspectives = input_data.get('question'), input_data.get('perspectives')
    if not isinstance(question, str) or not question.strip() or len(question) > 10000:
        raise ValueError('future battlefield research requires a bounded question')
    if not isinstance(perspectives, list) or not 1 <= len(perspectives) <= 8:
        raise ValueError('future battlefield research requires one to eight perspectives')
    if any(not isinstance(p, dict) or not isinstance(p.get('key'),str) or
           not isinstance(p.get('question'),str) or not p['question'].strip() or len(p['question']) > 500
           for p in perspectives):
        raise ValueError('future battlefield perspectives require keys and bounded research questions')
    if len({p['key'] for p in perspectives}) != len(perspectives):
        raise ValueError('future battlefield perspective keys must be unique')
    from .phase3_repository import resolve_profile_budget, RunBudgetMinimumError
    limits = resolve_profile_budget(profile, input_data.get('budgetLimits', {}))
    issues = [{"field": "budget." + key, "resource": key,
               "requested": limits.get(key, 0), "minimum": minimum}
              for key, minimum in future_budget_minimums(profile).items()
              if limits.get(key, 0) < minimum]
    if issues:
        raise RunBudgetMinimumError(
            '有效预算不足以完成执行基线；reasoning_rounds 按节点进入次数计费，至少覆盖所有必经节点。', issues)
    provider = profile.model_policy.get('providerSnapshot') or {}
    web = profile.source_policy.get('firecrawlProvider') or {}
    if not provider.get('enabled') or not web.get('baseUrl'):
        raise ValueError('future battlefield research preflight failed: frozen providers missing or disabled')
    environment = os.environ if environment is None else environment
    for ref in [provider.get('credential_ref',''), 'env:' + web.get('credentialRef','')]:
        if not ref.startswith('env:') or not environment.get(ref[4:]):
            raise ValueError('future battlefield research preflight failed: provider credential unavailable')
    network = profile.harness_contract.get('networkGrants', [])
    for endpoint in [provider.get('base_url'), web.get('baseUrl')]:
        host = urlsplit(endpoint or '').hostname
        if not host or not any(n.get('domain_pattern') == host and 'POST' in n.get('methods', [])
                               and not n.get('approvalRequired') for n in network):
            raise ValueError(f'future battlefield research preflight failed: provider network grant missing ({host})')
    budgets = {b.get('step_key'):b for b in profile.harness_contract.get('stepBudgets', [])}
    for step, resources in [('collect-public-evidence',('search_calls','scrape_calls','http_calls','tool_calls')),
                             ('extract-facts',('model_calls','http_calls','input_tokens','output_tokens')),
                             ('analyze-by-perspective',('model_calls','http_calls','input_tokens','output_tokens'))]:
        for resource in resources:
            if int(budgets.get(step, {}).get(resource, 0) or 0) <= 0:
                raise ValueError(f'future battlefield research preflight failed: Harness budget zero ({step}:{resource})')


__all__ = ["BUILTIN_EXECUTOR_KEYS", "fixed_runtime_errors", "validate_fixed_runtime"]
