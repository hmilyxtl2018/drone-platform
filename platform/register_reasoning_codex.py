"""Register an independent Future Battlefield Codex Profile.

The default is a read-only preview. Pass --apply to create the Profile in
ontology_agent_admin. Existing Codex Profiles are preserved in their entirety;
this command never upgrades or repairs a Profile over a user's configuration.
No Run, historical snapshot, provider, tool registry or ontology row is copied.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json

from psycopg2 import sql
from psycopg2.extras import Json

from agent_admin.config_publication import ConfigPublicationService, SECTIONS
from agent_engine.capabilities import graph_errors, graph_snapshot, load_graph
from agent_engine.execution_baseline import build_baseline, graph_hash, register_baseline, validate_baseline
from agent_engine.postgres import connection_factory
from agent_engine.reasoning_repository import validate_reasoning_policy


SOURCE = 'future-battlefield-analyst'
TARGET = 'future-battlefield-codex'
CAPABILITY_PATH = 'capability_graphs/future-battlefield-codex.v1.json'
CREATED_BY = 'register-reasoning-codex-v1'
PROFILE_TABLES = tuple(table for table, _ in SECTIONS.values())
HARNESS_TABLES = ('agent_harness_tool_grant', 'agent_harness_data_grant',
                  'agent_harness_network_grant', 'agent_harness_step_budget')
WRITE_TABLES = frozenset((*PROFILE_TABLES, *HARNESS_TABLES, 'agent_version', 'agent_step',
    'agent_step_tool', 'agent_tool_binding', 'agent_loop_rule', 'agent_validation_rule', 'agent_harness_contract',
    'agent_composition_edge', 'agent_reasoning_policy', 'agent_reasoning_stage'))
REQUIRED_TABLES = WRITE_TABLES | {'agent_execution_baseline', 'agent_execution_edge'}


def _id(kind, source):
    digest = hashlib.sha256(str(source).encode()).hexdigest()[:24]
    return f'{TARGET}-{kind}-{digest}'


def _rewrite(value, identities=None):
    identities = identities or {}
    if isinstance(value, dict):
        return {key: _rewrite(item, identities) for key, item in value.items()}
    if isinstance(value, list):
        return [_rewrite(item, identities) for item in value]
    if isinstance(value, str):
        return identities.get(value, value.replace(SOURCE, TARGET))
    return deepcopy(value)


def _json(value):
    return json.loads(value) if isinstance(value, str) else deepcopy(value)


def _set_json(row, field, value):
    row[field] = json.dumps(value, ensure_ascii=False) if isinstance(row.get(field), str) else deepcopy(value)


def _one(cursor, table, field, value, *, required=True):
    cursor.execute(sql.SQL('SELECT * FROM {} WHERE {}=%s').format(
        sql.Identifier(table), sql.Identifier(field)), (value,))
    row = cursor.fetchone()
    if row is None and required:
        raise ValueError(f'missing source configuration: {table}')
    return dict(row) if row else None


def read_source(cursor):
    """Read only the current authoring Profile and its current authority."""
    cursor.execute('SELECT table_name FROM information_schema.tables WHERE table_schema=current_schema()')
    installed = {row['table_name'] for row in cursor.fetchall()}
    missing = sorted(REQUIRED_TABLES - installed)
    if missing:
        raise ValueError('required migrations (including 070) are not installed: ' + ', '.join(missing))
    source = {'document': ConfigPublicationService._export(cursor, SOURCE)}
    for table in PROFILE_TABLES:
        source[table] = [_one(cursor, table, 'agent_key', SOURCE)]
    definition = source['agent_definition'][0]
    version = definition['current_version']
    harness_id = definition.get('default_harness_contract_id')
    if not harness_id:
        raise ValueError('source Profile has no default Harness')
    harness = _one(cursor, 'agent_harness_contract', 'harness_contract_id', harness_id)
    if harness['agent_key'] != SOURCE or harness['profile_version'] != version or harness['status'] not in {'testing', 'published'}:
        raise ValueError('source default Harness does not belong to its current runnable Profile')
    source['agent_harness_contract'] = [harness]
    for table in HARNESS_TABLES:
        cursor.execute(sql.SQL('SELECT * FROM {} WHERE harness_contract_id=%s').format(sql.Identifier(table)), (harness_id,))
        source[table] = [dict(row) for row in cursor.fetchall()]
    for table in ('agent_step', 'agent_tool_binding', 'agent_loop_rule', 'agent_validation_rule'):
        cursor.execute(sql.SQL('SELECT * FROM {} WHERE agent_key=%s').format(sql.Identifier(table)), (SOURCE,))
        source[table] = [dict(row) for row in cursor.fetchall()]
    cursor.execute('SELECT t.* FROM agent_step_tool t JOIN agent_step s USING(step_id) WHERE s.agent_key=%s', (SOURCE,))
    source['agent_step_tool'] = [dict(row) for row in cursor.fetchall()]
    cursor.execute('SELECT * FROM agent_composition_edge WHERE parent_agent_key=%s AND parent_profile_version=%s', (SOURCE, version))
    source['agent_composition_edge'] = [dict(row) for row in cursor.fetchall()]
    cursor.execute('SELECT * FROM agent_reasoning_policy WHERE agent_key=%s AND profile_version=%s ORDER BY version DESC LIMIT 1', (SOURCE, version))
    policy = cursor.fetchone()
    if not policy:
        raise ValueError('source reasoning policy missing; install migration 070 for the current Profile')
    source['agent_reasoning_policy'] = [dict(policy)]
    return source


def build_registration(source):
    """Build deterministic new identities without mutating the source snapshot."""
    identities = {}
    for table, field, kind in (('agent_step', 'step_id', 'step'),
        ('agent_loop_rule', 'loop_id', 'loop'), ('agent_validation_rule', 'rule_id', 'rule'),
        ('agent_composition_edge', 'edge_id', 'edge'), ('agent_harness_data_grant', 'grant_id', 'data'),
        ('agent_harness_network_grant', 'network_grant_id', 'network')):
        identities.update((row[field], _id(kind, row[field])) for row in source.get(table, []))
    old_harness = source['agent_harness_contract'][0]
    harness_id = _id('harness', old_harness['harness_contract_id'])
    identities[old_harness['harness_contract_id']] = harness_id
    policy_id = _id('reasoning', source['agent_definition'][0]['current_version'])
    identities[source['agent_reasoning_policy'][0]['policy_id']] = policy_id
    rows = {}
    for table in WRITE_TABLES - {'agent_version', 'agent_reasoning_stage'}:
        rows[table] = [_rewrite({k: v for k, v in row.items() if k not in {'created_at', 'updated_at', 'published_at'}}, identities)
                       for row in source.get(table, [])]
    definition = rows['agent_definition'][0]
    definition.update(agent_key=TARGET, display_name='未来战场研判 Codex', status='testing',
                      default_harness_contract_id=None)
    definition['system_prompt'] += ('\n以本体、证据与反证组织研究。冻结问题范围与执行预算；'
        '逐条绑定实体、关系、适用条件和证据；保留未知及冲突。模型输出始终为未核验候选，'
        '不得把同模型复核称为独立核验，不得自动发布源本体。')
    model = rows['agent_model_policy'][0]
    config = _json(model['config_json'])
    config.update(capabilityGraphPath=CAPABILITY_PATH, agentLayer='application', runtimeKind='llm_loop')
    # Explicit edge IDs, if authored, must point to the new parent's edges too.
    policies = (config.get('executionBaseline') or {}).get('nodePolicies', {})
    for item in policies.values():
        if item.get('compositionEdgeId') in identities:
            item['compositionEdgeId'] = identities[item['compositionEdgeId']]
    _set_json(model, 'config_json', config)
    harness = rows['agent_harness_contract'][0]
    contract = _json(harness['contract_json'])
    contract.update(agentKey=TARGET, profileVersion=definition['current_version'], contractVersion='1.0.0')
    harness.update(harness_contract_id=harness_id, agent_key=TARGET, contract_version='1.0.0', status='testing')
    _set_json(harness, 'contract_json', contract)
    harness['contract_hash'] = graph_hash(contract)
    policy_row = rows['agent_reasoning_policy'][0]
    policy = _json(policy_row['policy_json'])
    policy.update(policyId=policy_id, agentKey=TARGET, profileVersion=definition['current_version'], version=1,
                  requireEvidenceBinding=True, allowAutomaticPublication=False)
    policy = validate_reasoning_policy(policy)
    policy_row.update(policy_id=policy_id, agent_key=TARGET, version=1, created_by=CREATED_BY, policy_json=policy)
    rows['agent_reasoning_stage'] = [{'policy_id': policy_id, 'stage_key': stage['key'],
        'sequence_no': stage['sequence'], 'stage_json': deepcopy(stage)} for stage in policy['stages']]
    rows['agent_version'] = [{'agent_key': TARGET, 'version': definition['current_version'],
        'prompt': definition['system_prompt'], 'config_json': config, 'status': 'testing'}]
    document = _rewrite(source['document'], identities)
    document['definition'] = {field: definition[field] for field in document['definition']}
    document['models']['config_json'] = config
    baseline_harness = {'stepBudgets': rows['agent_harness_step_budget'],
                        'compositionEdges': rows['agent_composition_edge']}
    graph = build_baseline(TARGET, document, baseline_harness)
    enabled_tools = {row['tool_key'] for row in rows['agent_tool_binding'] if row['enabled']}
    errors = validate_baseline(graph, enabled_tools)
    if errors:
        raise ValueError('invalid derived baseline: ' + '; '.join(e['message'] for e in errors))
    stage_keys = [stage.get('stepKey', stage['key']) for stage in policy['stages']]
    if len(stage_keys) != len(set(stage_keys)) or set(stage_keys) != {node['id'] for node in graph['nodes']}:
        raise ValueError('reasoning stages must cover each execution node exactly once')
    budget_steps = {row['step_key'] for row in rows['agent_harness_step_budget']}
    if budget_steps != {node['id'] for node in graph['nodes']}:
        raise ValueError('source Harness requires a budget for every execution node')
    return {'rows': rows, 'document': document, 'graph': graph, 'harnessId': harness_id,
            'policyId': policy_id, 'capabilityGraph': load_graph(CAPABILITY_PATH, TARGET)}


def _insert(cursor, table, row):
    if table not in WRITE_TABLES:
        raise ValueError('registration cannot write this table')
    fields = list(row)
    cursor.execute(sql.SQL('INSERT INTO {} ({}) VALUES ({})').format(sql.Identifier(table),
        sql.SQL(',').join(map(sql.Identifier, fields)), sql.SQL(',').join(sql.Placeholder() for _ in fields)),
        tuple(Json(row[field]) if isinstance(row[field], (dict, list)) else row[field] for field in fields))


def register_connection(connection, *, apply=False):
    """Use the caller's transaction; tests may provide an isolated schema."""
    with connection.cursor() as cursor:
        if apply:
            ConfigPublicationService._config_lock(cursor, True)
        if _one(cursor, 'agent_definition', 'agent_key', TARGET, required=False):
            return {'agentKey': TARGET, 'status': 'already-exists-preserved', 'applied': False,
                    'message': 'Existing Profile and all user configuration were preserved.'}
        plan = build_registration(read_source(cursor))
        capability = graph_snapshot(cursor, plan['document']['models']['config_json'], TARGET)
        errors = graph_errors(capability, plan['document'])
        if errors:
            raise ValueError('derived Profile fails capability requirements: ' + '; '.join(e['message'] for e in errors))
        result = {'agentKey': TARGET, 'sourceAgentKey': SOURCE, 'status': 'created' if apply else 'dry-run',
                  'applied': apply, 'profileVersion': plan['graph']['profileVersion'],
                  'harnessId': plan['harnessId'], 'reasoningPolicyId': plan['policyId'],
                  'capabilityGraphId': plan['capabilityGraph']['graphId'], 'graphHash': graph_hash(plan['graph']),
                  'nodes': len(plan['graph']['nodes']), 'edges': len(plan['graph']['edges']),
                  'rowCounts': {table: len(rows) for table, rows in sorted(plan['rows'].items())}}
        if not apply:
            return result
        order = (*PROFILE_TABLES, 'agent_version', 'agent_step', 'agent_step_tool',
                 'agent_tool_binding', 'agent_loop_rule', 'agent_validation_rule', 'agent_harness_contract',
                 *HARNESS_TABLES, 'agent_composition_edge', 'agent_reasoning_policy', 'agent_reasoning_stage')
        for table in order:
            for row in plan['rows'][table]:
                _insert(cursor, table, row)
        cursor.execute('UPDATE agent_definition SET default_harness_contract_id=%s WHERE agent_key=%s',
                       (plan['harnessId'], TARGET))
        persisted = ConfigPublicationService._export(cursor, TARGET)
        graph = build_baseline(TARGET, persisted, ConfigPublicationService._baseline_harness(cursor, TARGET))
        if graph_hash(graph) != result['graphHash']:
            raise ValueError('registered configuration differs from the previewed execution baseline')
        baseline = register_baseline(cursor, TARGET, graph, status='testing', created_by=CREATED_BY)
        result.update(baselineId=baseline['baselineId'], baselineVersion=baseline['baselineVersion'])
        return result


def register(*, apply=False):
    """Only the dedicated administration database is a CLI registration target."""
    with connection_factory(database='ontology_agent_admin')() as connection:
        connection.set_session(readonly=not apply, isolation_level='SERIALIZABLE' if apply else 'REPEATABLE READ')
        return register_connection(connection, apply=apply)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true', help='create the new Profile in ontology_agent_admin')
    args = parser.parse_args(argv)
    try:
        result = register(apply=args.apply)
    except ValueError as exc:
        parser.exit(1, str(exc) + '\n')
    print(json.dumps(result, indent=2, ensure_ascii=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
