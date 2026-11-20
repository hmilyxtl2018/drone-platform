"""Reviewed capability requirements frozen with configuration and Run snapshots."""
from __future__ import annotations

from agent_engine.application_identity import FUTURE_AGENT_KEYS

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re

from jsonschema import validate

GRAPH_ROOT = Path(__file__).resolve().parents[1] / 'capability_graphs'
GRAPH_SCHEMA = {'type': 'object', 'required': ['graphId', 'version', 'agentKey', 'capabilities', 'forbiddenCapabilities'],
    'properties': {'graphId': {'type': 'string', 'minLength': 1}, 'version': {'type': 'string', 'minLength': 1},
        'agentKey': {'type': 'string', 'minLength': 1}, 'forbiddenCapabilities': {'type': 'array', 'uniqueItems': True, 'items': {'type': 'string', 'minLength': 1}},
        'capabilities': {'type': 'array', 'minItems': 1, 'maxItems': 50, 'items': {'type': 'object',
            'required': ['key', 'name', 'requiredActions', 'requiredEvidence', 'acceptance'],
            'properties': {**{key: {'type': 'string', 'minLength': 1} for key in ('key', 'name')},
                **{key: {'type': 'array', 'minItems': 1, 'uniqueItems': True, 'items': {'type': 'string', 'minLength': 1}}
                   for key in ('requiredActions', 'requiredEvidence', 'acceptance')}}, 'additionalProperties': False}}}, 'additionalProperties': False}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode()).hexdigest()


def load_graph(reference, agent_key=None):
    if not isinstance(reference, str) or not re.fullmatch(r'(?:capability_graphs/)?[A-Za-z0-9_-]+\.v[0-9]+\.json', reference):
        raise ValueError('capability graph must reference a reviewed local graph')
    path = (GRAPH_ROOT / reference.rsplit('/', 1)[-1]).resolve()
    if path.parent != GRAPH_ROOT.resolve() or not path.is_file() or path.stat().st_size > 256000:
        raise ValueError('capability graph is unavailable or exceeds its size limit')
    graph = json.loads(path.read_text(encoding='utf-8'))
    validate(graph, GRAPH_SCHEMA)
    if agent_key and graph['agentKey'] != agent_key:
        raise ValueError('capability graph belongs to another Agent')
    keys = [item['key'] for item in graph['capabilities']]
    if len(keys) != len(set(keys)):
        raise ValueError('capability keys must be unique')
    return graph


def _json(value):
    return json.loads(value) if isinstance(value, str) else value


def action_manifest(cursor, key):
    """Freeze the actual Action configuration and executable tool versions."""
    cursor.execute('SELECT agent_key,current_version,status FROM agent_definition WHERE agent_key=%s', (key,))
    definition = cursor.fetchone()
    if not definition or definition['status'] in {'disabled', 'inactive', 'retired', 'deprecated'}:
        raise ValueError('required Action is missing or disabled: ' + key)
    cursor.execute('SELECT step_key,sequence_no,dependencies_json,input_contract,output_contract FROM agent_step WHERE agent_key=%s ORDER BY sequence_no', (key,))
    steps = [dict(row) for row in cursor.fetchall()]
    cursor.execute('SELECT s.step_key,t.tool_key FROM agent_step s JOIN agent_step_tool t USING(step_id) WHERE s.agent_key=%s ORDER BY s.sequence_no,t.tool_key', (key,))
    links = [dict(row) for row in cursor.fetchall()]
    cursor.execute('SELECT b.tool_key,b.tool_version,b.enabled,b.config_json,t.status,t.access_mode,t.risk_level,t.protocol,'
                   'v.status AS version_status,v.input_schema,v.output_schema FROM agent_tool_binding b '
                   'JOIN tool_registry t USING(tool_key) LEFT JOIN tool_version v ON v.tool_key=b.tool_key AND v.version=b.tool_version '
                   'WHERE b.agent_key=%s ORDER BY b.tool_key', (key,))
    tools = [dict(row) for row in cursor.fetchall()]
    enabled = {row['tool_key'] for row in tools if row['enabled'] and row['status'] not in {'disabled', 'inactive', 'retired', 'deprecated'}
               and row['version_status'] and row['version_status'] not in {'disabled', 'inactive', 'retired', 'deprecated'}}
    if not steps or not links or any(link['tool_key'] not in enabled for link in links):
        raise ValueError('required Action has no executable bound tools: ' + key)
    order = {step['step_key']: step['sequence_no'] for step in steps}
    for step in steps:
        deps = _json(step['dependencies_json'])
        if not isinstance(deps, list) or any(dep not in order or order[dep] >= step['sequence_no'] for dep in deps):
            raise ValueError('required Action has invalid dependencies: ' + key)
    policies = {}
    for table in ('agent_model_policy', 'agent_scope', 'agent_resource_policy', 'agent_convergence_policy',
                  'agent_output_contract', 'agent_evidence_policy', 'agent_approval_policy'):
        cursor.execute('SELECT * FROM ' + table + ' WHERE agent_key=%s', (key,))
        row = cursor.fetchone()
        if row is None:
            raise ValueError('required Action has incomplete policies: ' + key)
        policies[table] = dict(row)
    return {'definition': dict(definition), 'steps': steps, 'links': links, 'tools': tools, 'policies': policies}


def graph_snapshot(cursor, config, agent_key=None):
    reference = config.get('capabilityGraphPath')
    if reference is None:
        if agent_key in FUTURE_AGENT_KEYS:
            raise ValueError('future battlefield Agent requires its capability graph')
        return {}
    graph = load_graph(reference, agent_key)
    actions = sorted({key for item in graph['capabilities'] for key in item['requiredActions'] if '.' not in key})
    manifests = {key: action_manifest(cursor, key) for key in actions}
    return {'graph': graph, 'graphHash': digest(graph), 'actions': manifests, 'actionsHash': digest(manifests)}


def _required_path(schema, path):
    """Require every part of the declared JSON path, including array items."""
    for segment in path.split('.'):
        array = segment.endswith('[]')
        name = segment[:-2] if array else segment
        if not isinstance(schema, dict) or name not in schema.get('required', []):
            return False
        schema = schema.get('properties', {}).get(name)
        if array:
            schema = schema.get('items') if isinstance(schema, dict) else None
        if not isinstance(schema, dict):
            return False
    return True


def graph_errors(snapshot, document, tool_input_schemas=()):
    graph = snapshot['graph']
    config = document['models']['config_json']
    declared = config.get('capabilityActions', [])
    if not isinstance(declared, list) or any(not isinstance(item, str) for item in declared) or len(declared) != len(set(declared)):
        return [{'path': '/models/config_json/capabilityActions', 'message': 'capabilityActions must be a list of unique registered keys'}]
    bound = {item['tool_key'] for item in document['bindings'] if item['enabled']}
    used = {key for step in document['steps'] for key in step['tools']}
    errors = []
    for item in graph['capabilities']:
        for action in item['requiredActions']:
            if ('.' in action and (action not in bound or action not in used)) or ('.' not in action and action not in declared):
                errors.append({'path': '/models/config_json/capabilityActions', 'message': 'required Action/tool is not enabled in this Profile: ' + action})
        for path in item['requiredEvidence']:
            if not _required_path(document['output']['schema_json'], path):
                errors.append({'path': '/output/schema_json', 'message': 'output contract must require capability evidence: ' + path})
    # Inspect only capabilities exposed by tool names and input schema fields;
    # a forbidden_actions declaration is a restriction, never an exposure.
    def field_names(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key in {'properties', 'patternProperties'} and isinstance(child, dict):
                    yield from child
                yield from field_names(child)
        elif isinstance(value, list):
            for child in value: yield from field_names(child)
    exposed = list(bound) + [name for step in document['steps'] for name in field_names(step['input_contract'])]
    for schema in tool_input_schemas:
        exposed.extend(field_names(_json(schema)))
    for action in snapshot['actions'].values():
        exposed.extend(link['tool_key'] for link in action['links'])
        for tool in action['tools']:
            exposed.extend(field_names(_json(tool['input_schema'])))
    forbidden = [value.lower() for value in graph['forbiddenCapabilities']]
    for value in exposed:
        normalized = re.sub(r'[^a-z0-9]+', '_', value.lower())
        if any(item in normalized for item in forbidden):
            errors.append({'path': '/steps', 'message': 'forbidden capability is exposed: ' + value})
    return errors
