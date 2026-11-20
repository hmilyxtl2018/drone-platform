"""Opt-in policy for one read-only, report-quality reflection. No candidate writes."""
from copy import deepcopy
from .application_identity import FUTURE_AGENT_KEYS
from .task_intent import COUNTERS, digest

REFLECTION_AGENT = 'research-reflection-agent'
DEFAULT_LIMITS = {key: 0 for key in COUNTERS}
DEFAULT_LIMITS.update(model_calls=1, http_calls=1, input_tokens=24000, output_tokens=3072)


def validate_policy(value):
    if not isinstance(value, dict) or set(value)-{'enabled','mode','budgetLimits','maxRuntimeSeconds'}:
        raise ValueError('only enabled, mode, budgetLimits and maxRuntimeSeconds can be configured')
    if type(value.get('enabled')) is not bool:
        raise ValueError('enabled must be boolean')
    limits = deepcopy(DEFAULT_LIMITS)
    mode = value.get('mode','observe-only')
    if mode not in {'observe-only','auto-trial'}:
        raise ValueError('unsupported evolution mode')
    calls = 4 if mode == 'auto-trial' else 1
    limits.update(model_calls=calls,http_calls=calls,input_tokens=24000*calls,output_tokens=3072*calls)
    requested = value.get('budgetLimits', {})
    if not isinstance(requested, dict) or set(requested)-{'input_tokens','output_tokens'}:
        raise ValueError('only token ceilings can be configured; calls and tools are fixed by mode')
    for key, amount in requested.items():
        ceiling = (48000 if key == 'input_tokens' else 4096)*calls
        floor = (8000 if key == 'input_tokens' else 1024)*calls
        if type(amount) is not int or not floor <= amount <= ceiling:
            raise ValueError('token budget outside supported reflection range: '+key)
        limits[key] = amount
    runtime = value.get('maxRuntimeSeconds', 900 if calls>1 else 180)
    if type(runtime) is not int or not (300 if calls>1 else 60) <= runtime <= (1800 if calls>1 else 600):
        raise ValueError('runtime outside the supported mode range')
    return {'schemaVersion':'research-evolution.v1','enabled':value['enabled'],
            'mode':mode,'maxTrials':int(calls>1),'maxReflections':1,
            'allowedOperations':['read-evidence','analyze'],'allowedTools':[],
            'budgetLimits':limits,'maxRuntimeSeconds':runtime,
            'mutableFields':['definition.system_prompt','definition.current_version'] if calls>1 else [],
            'candidateScope':'report-quality-on-frozen-fragments' if calls>1 else None,
            'automaticPublication':False,
            'experienceSetHash':digest([])}


def assert_supported_agent(agent_key):
    if agent_key not in FUTURE_AGENT_KEYS:
        raise ValueError('batch 1 is limited to registered public-research application profiles')
