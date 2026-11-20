"""Current database registration -> immutable verifier materials."""
from types import SimpleNamespace


class AgentRegistrationAdapter:
    kind = 'agent-registration'

    def load(self, subject, context, *, tables):
        return context.registration(subject['agent_key'], tables)


def public_projection(bundle):
    # Reuse the production capability mapper, check it with independent rules.
    from ...dsh_api import _profile_capability
    facts = bundle.facts()
    definition, goal = facts['definition'], facts.get('goal')
    model, reasoning = facts.get('model'), facts.get('reasoning')
    profile = SimpleNamespace(agent_key=bundle.agent_key,
        model_policy={'config_json': {'budgetLimits': model['budget'] if model else None}},
        reasoning_policy={'budgetLimits': reasoning.get('budget') or {},
                          'stages': reasoning.get('stages') or []} if reasoning else {},
        goal_contract={'contractId': goal['contract_id'], 'revision': goal['revision'],
                       'contentHash': goal['content_hash'], 'document': goal['document_json']} if goal else {})
    modes = facts['modes']
    result = {k: definition[k] for k in ('agent_key', 'display_name', 'current_version', 'description')}
    result.update(_profile_capability(profile, modes if isinstance(modes, list) else []))
    return result
