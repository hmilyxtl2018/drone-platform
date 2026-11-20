"""Authoring projections only. Persisted drafts never install role executors."""
from copy import deepcopy

from .uav_execution_contracts import AGENTS, VERSION, RUNTIME, role_contract, publication_issues
from .execution_baseline import COUNTERS


TOOL_CANDIDATES = {
    'cap-read-ontology': ('ontology.get_entity', 'ontology.query_evidence'),
    'cap-acquire-sources': ('web.search', 'web.scrape'),
    'doc-index-records': ('ontology.get_entity', 'ontology.query_evidence'),
    'review-check-citations': ('ontology.query_evidence',),
    'review-check-source-links': ('ontology.query_evidence',),
}


def prepare_document(agent_key, live):
    """Build a reviewable successor; do not change live Profiles or authority."""
    spec = role_contract(agent_key)
    value = deepcopy(live)
    version = live['definition']['current_version'].split('.')
    if len(version) != 3 or not all(v.isdigit() for v in version):
        raise ValueError('role baseline drafts require a semantic Profile version')
    value['definition'].update(current_version=f'{version[0]}.{version[1]}.{int(version[2]) + 1}', status='testing')
    config = value['models']['config_json']
    config.update(engineMode=RUNTIME, researchModeAvailable=False,
                  roleContractVersion=VERSION, roleContractHash=spec['contentHash'])
    limits = config['budgetLimits']
    enabled = {b['tool_key'] for b in value['bindings'] if b['enabled']}
    policies, steps = {}, []
    model_nodes = sum(n['executionKind'] == 'llm' for n in spec['nodes'])
    for sequence, node in enumerate(spec['nodes'], 1):
        kind = node['executionKind']
        budget = {k: 0 for k in COUNTERS}
        if kind == 'llm':
            for k in ('model_calls', 'http_calls', 'input_tokens', 'output_tokens'):
                budget[k] = min(limits.get(k, 0), max(1, limits.get(k, 0) // model_nodes))
        elif kind == 'tool':
            for k in ('tool_calls', 'http_calls', 'search_calls', 'scrape_calls'):
                budget[k] = min(limits.get(k, 0), 8 if k in ('tool_calls', 'http_calls') else 2)
        budget.update(max_rounds=1, max_parallelism=1,
                      max_runtime_seconds=min(300, value['convergence']['max_runtime_seconds']))
        policies[node['id']] = {k: deepcopy(node[k]) for k in (
            'executionKind', 'executorKey', 'condition', 'completionCriteria', 'completionChecks', 'failurePolicy')}
        policies[node['id']].update(nodeType='stage', plannerMode='fixed', budget=budget)
        steps.append({'step_key': node['id'], 'name': node['name'],
            'description': node['completionCriteria'][0], 'sequence_no': sequence,
            'input_contract': deepcopy(node['inputContract']), 'output_contract': deepcopy(node['outputContract']),
            'parallel_group': None, 'required': True, 'retry_limit': 0,
            'dependencies': sorted(e['from'] for e in spec['edges'] if e['to'] == node['id']),
            'tools': sorted(set(TOOL_CANDIDATES.get(node['id'], ())) & enabled)})
    config['executionBaseline'] = {'nodePolicies': policies}
    value['steps'] = steps
    # Historical loop records stay in the live Profile and frozen Runs. The
    # new role contract has no back edges and stops on a failed required node.
    value['loops'] = []
    return value


def read_draft_baseline(cursor, agent_key, live):
    """SELECT-only: return a registered, hash-checked draft based on this live document."""
    if agent_key not in AGENTS:
        return None
    from agent_admin.config_publication import _hash
    from .execution_baseline import build_baseline, read_baseline
    cursor.execute('''SELECT d.draft_id,d.current_revision,d.base_document_json,
                            r.document_json,r.document_hash
                       FROM agent_config_draft d JOIN agent_config_draft_revision r
                         ON r.draft_id=d.draft_id AND r.revision=d.current_revision
                      WHERE d.agent_key=%s
                        AND r.document_json #>> '{models,config_json,roleContractVersion}'=%s
                        AND NOT EXISTS (SELECT 1 FROM agent_config_publication p WHERE p.draft_id=d.draft_id)
                      ORDER BY d.created_at DESC,d.draft_id DESC LIMIT 1''', (agent_key, VERSION))
    row = cursor.fetchone()
    if not row:
        return None
    document = row['document_json']
    if _hash(document) != row['document_hash'] or _hash(row['base_document_json']) != _hash(live):
        return None
    graph = build_baseline(agent_key, document)
    spec = role_contract(agent_key)
    if graph.get('roleContractHash') != spec['contentHash']:
        return None
    baseline = read_baseline(cursor, agent_key, graph)
    if not baseline['baselineId'] or baseline['status'] != 'testing' or not baseline['validation']['accepted']:
        return None
    issues = publication_issues(agent_key, document, graph)
    if any(i['code'] not in {'runtime-not-installed', 'executor-not-implemented'} for i in issues):
        return None
    # Only safe graph fields leave the service; prompts, bindings, dependency
    # snapshots and infrastructure settings in the draft are never returned.
    return {**baseline, 'viewState': 'proposed', 'draftId': row['draft_id'],
            'draftRevision': row['current_revision'],
            'readiness': {'ready': False, 'status': 'draft-not-published', 'issues': issues},
            'basedOnProfileVersion': live['definition']['current_version']}
