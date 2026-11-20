"""Independent required checks; missing facts never turn into success."""
import re
from ..goal_contract import digest
from ..phase3_repository import RESOURCES, MAX_COUNTER
from .contracts import MODES, overall


def budget_valid(value):
    return (isinstance(value, dict) and all(k in RESOURCES and type(v) is int and 0 <= v <= MAX_COUNTER
                                          for k, v in value.items()))


def registration_checks(bundle, projection):
    facts = bundle.facts()
    definition = facts['definition']
    goal, model, reasoning = facts.get('goal'), facts.get('model'), facts.get('reasoning')
    checks = []

    def add(n, status, reason):
        checks.append({'checkId': f'REG-{n:03}', 'status': status, 'required': True,
                       'reasonCode': reason, 'evidenceRefs': [bundle.agent_key]})

    version = definition.get('current_version')
    if not isinstance(version, str) or not re.fullmatch(r'\d+\.\d+\.\d+(?:[-+][a-zA-Z0-9.-]+)?', version):
        add(1, 'failed', 'profile-version-invalid')
    elif 'agent_harness_contract' in bundle.missing:
        add(1, 'inconclusive', 'profile-version-reference-unavailable')
    elif not facts.get('harness'):
        add(1, 'failed', 'profile-version-reference-missing')
    else:
        add(1, 'passed', 'profile-version-reference-valid')

    if 'agent_goal_contract_revision' in bundle.missing or not goal:
        add(2, 'inconclusive', 'goal-contract-unavailable')
    elif (not isinstance(goal.get('document_json'), dict)
          or digest(goal['document_json']) != goal.get('content_hash')
          or not isinstance(goal.get('contract_id'), str)
          or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,160}', goal['contract_id'])
          or type(goal.get('revision')) is not int or goal['revision'] < 1):
        add(2, 'failed', 'goal-contract-invalid-or-hash-mismatch')
    elif projection is None:
        add(2, 'inconclusive', 'public-projection-unavailable')
    elif (not isinstance(projection.get('goalContract'), dict) or
          any(projection['goalContract'].get(k) != v for k, v in {
              'contractId': goal['contract_id'], 'revision': goal['revision'],
              'contentHash': goal['content_hash']}.items())):
        add(2, 'failed', 'goal-reference-mismatch')
    else:
        add(2, 'passed', 'goal-reference-and-hash-match')

    modes = facts['modes']
    if (not isinstance(modes, list) or not modes or any(not isinstance(m, str) or m not in MODES for m in modes)
            or len(set(modes)) != len(modes)):
        add(3, 'failed', 'allowed-modes-invalid')
    elif projection is None:
        add(3, 'inconclusive', 'public-projection-unavailable')
    elif projection.get('allowedModes') != modes:
        add(3, 'failed', 'allowed-modes-mismatch')
    else:
        add(3, 'passed', 'allowed-modes-match')

    missing_budget_table = bool({'agent_model_policy', 'agent_reasoning_policy'} & set(bundle.missing))
    model_budget = model.get('budget') if model else None
    reasoning_budget = reasoning.get('budget') if reasoning else {}
    if reasoning_budget is None:
        reasoning_budget = {}
    metadata_valid = not reasoning or (reasoning['document_agent_key'] == bundle.agent_key
        and reasoning['document_profile_version'] == version
        and reasoning['document_version'] == reasoning['version'])
    effective = {**model_budget, **reasoning_budget} if budget_valid(model_budget) and budget_valid(reasoning_budget) else None
    if missing_budget_table or model is None:
        add(4, 'inconclusive', 'effective-budget-unavailable')
    elif not metadata_valid or not effective:
        add(4, 'failed', 'effective-budget-invalid')
    elif projection is None:
        add(4, 'inconclusive', 'public-projection-unavailable')
    elif projection.get('budgetLimits') != effective:
        add(4, 'failed', 'effective-budget-mismatch')
        checks[-1]['expected'] = effective
        if budget_valid(projection.get('budgetLimits')):
            checks[-1]['actual'] = projection['budgetLimits']
    else:
        add(4, 'passed', 'effective-budget-match')

    if projection is None:
        add(5, 'inconclusive', 'public-projection-unavailable')
    elif (projection.get('agent_key') != bundle.agent_key or projection.get('current_version') != version):
        add(5, 'failed', 'public-projection-identity-mismatch')
    elif overall(checks) != 'passed':
        add(5, 'inconclusive', 'public-projection-dependencies-unverified')
    else:
        add(5, 'passed', 'public-projection-match')
    return checks
