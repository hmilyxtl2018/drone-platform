"""Versioned transport contracts; domain evidence is validated separately."""
from copy import deepcopy

VERSION = 'agent-data.v1'
UAV_AGENTS = {'uav-capability-analyst', 'uav-design-engineer', 'uav-evidence-reviewer'}
DETERMINISTIC_AGENTS = {'evidence-collection-action', 'trend-analysis-action', 'red-team-action'}


def envelope(fields, required=None):
    return {'type': 'object', 'required': ['input', 'state'], 'additionalProperties': False,
            'properties': {'input': {'type': 'object', 'required': list(fields) if required is None else required,
                                     'properties': deepcopy(fields)}, 'state': {'type': 'object'}}}


def action_input(agent_key):
    from agent_tools.future_battlefield import ARGUMENT_SCHEMAS
    if agent_key == 'evidence-collection-action':
        return envelope(ARGUMENT_SCHEMAS['analysis.source_deduplicate']['properties'])
    if agent_key == 'red-team-action':
        return envelope(ARGUMENT_SCHEMAS['analysis.claim_audit']['properties'], ['claims', 'judgments', 'conflicts'])
    if agent_key == 'trend-analysis-action':
        return envelope({'observations': {'type': 'array', 'maxItems': 100,
                         'items': {'type': 'object'}}, 'parameters': {'type': 'object'}})
    return None


def uav_input():
    return envelope({'entityId': {'type': 'string', 'minLength': 1, 'maxLength': 200},
                     'requirements': {'type': 'object', 'maxProperties': 100},
                     'question': {'type': 'string', 'maxLength': 10000}}, ['entityId'])


def action_output(agent_key, step_key):
    from agent_tools.future_battlefield import TOOL_OUTPUT_SCHEMAS
    from agent_tools.research import OUTPUT_SCHEMA
    keys = {'evidence-collection-action': ['analysis.source_deduplicate'],
            'red-team-action': ['analysis.citation_validate', 'analysis.claim_audit'],
            'trend-analysis-action': ['analysis.series_build', 'analysis.change_point']}[agent_key]
    results = {key: deepcopy(OUTPUT_SCHEMA if agent_key == 'trend-analysis-action' else TOOL_OUTPUT_SCHEMAS[key])
               for key in keys}
    return {'type': 'object', 'required': ['stepKey', 'toolResults'],
            'properties': {'stepKey': {'const': step_key}, 'toolResults': {
                'type': 'object', 'required': keys, 'properties': results, 'additionalProperties': False}}}


def validate_admission(profile, data):
    """New Profiles reject malformed first-node material before a Run is written."""
    if getattr(profile, 'model_policy', {}).get('config_json', {}).get('dataContractVersion') != VERSION:
        return
    from jsonschema import validate
    if profile.agent_key in UAV_AGENTS:
        if data.get('executionMode') != 'research':
            raise ValueError('UAV Profiles require executionMode=research; fixed domain executors are not installed')
        schema = uav_input()
    elif profile.steps:
        schema = profile.steps[0].input_contract
    else:
        return
    validate({'input': data, 'state': {}}, schema)


def quality_input(engine_mode):
    fields = {'sourceRunId': {'type': 'string', 'minLength': 1},
              'scope': {'type': 'object', 'properties': {'purpose': {'const': 'report-quality-only'}},
                        'required': ['purpose']},
              'taskIntent': {'type': 'object'}}
    if engine_mode == 'reflection-v1':
        fields['sourceOutputHash'] = {'type': 'string', 'pattern': '^[a-f0-9]{64}$'}
        fields['auditBundle'] = {'type': 'object', 'required': ['references'], 'properties': {
            'references': {'type': 'array', 'items': {'type': 'object', 'required': ['refId']}}}}
    else:
        fields['modelInput'] = {'type': 'object'}
        fields['caseHash'] = {'type': 'string', 'pattern': '^[a-f0-9]{64}$'}
    return envelope(fields)


def quality_output(model_schema, engine_mode):
    """The persisted Run adds audit metadata to the model's narrow response."""
    schema = deepcopy(model_schema)
    fields = {'sourceRunId': {'type': 'string', 'minLength': 1}, 'truthVerified': {'const': False},
              'automaticPublication': {'const': False}}
    hashed = {'type': 'string', 'pattern': '^[a-f0-9]{64}$'}
    if engine_mode == 'reflection-v1':
        fields.update(sourceOutputHash=hashed, references={'type': 'array', 'items': {'type': 'object'}},
                      candidateCreated={'const': False}, diagnosisVerified={'const': False},
                      scope={'const': 'report-quality-only'})
    else:
        fields.update(caseHash=hashed, targetProfileHash={'anyOf': [hashed, {'type': 'null'}]},
                      scope={'const': 'report-quality-on-frozen-fragments'})
    schema.setdefault('properties', {}).update(fields)
    schema['required'] = sorted(set(schema.get('required', [])) | fields.keys())
    return schema
