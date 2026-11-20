"""Canonical execution baselines shared by authoring, snapshots and scheduling."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json



COUNTERS = ('model_calls', 'tool_calls', 'http_calls', 'search_calls', 'scrape_calls',
            'input_tokens', 'output_tokens')
NODE_BUDGET_FIELDS = (*COUNTERS, 'max_rounds', 'max_iterations', 'max_retries',
                      'max_parallelism', 'max_concurrency', 'max_runtime_seconds',
                      'max_evidence_bytes', 'estimated_cost_micros')
NODE_TYPES = {'stage', 'action', 'tool', 'gate', 'human_review'}


def graph_hash(graph):
    raw = json.dumps(graph, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def baseline_installed(cursor):
    cursor.execute("SELECT to_regclass(current_schema() || '.agent_execution_baseline') AS table_name")
    row = cursor.fetchone()
    return bool(row and (row.get('table_name') if isinstance(row, dict) else row[0]))


def build_baseline(agent_key, document, harness=None):
    """Steps remain the authoring source; policies annotate, never replace them."""
    harness = harness or {}
    config = (document.get('models') or {}).get('config_json') or {}
    policy = config.get('executionBaseline') or {}
    policies = policy.get('nodePolicies') or {}
    convergence = document.get('convergence') or {}
    resources = document.get('resources') or {}
    totals = deepcopy(config.get('budgetLimits') or {})
    totals.setdefault('model_calls', convergence.get('max_model_calls', 0))
    totals.setdefault('tool_calls', convergence.get('max_tool_calls', 0))
    budgets = {row['step_key']: row for row in harness.get('stepBudgets', [])}
    composition_edges = {edge.get('child_agent_key'): edge for edge in harness.get('compositionEdges', [])
                         if edge.get('enabled', True)}
    steps = document.get('steps') or []
    research_available = bool(config.get('researchModeAvailable')) and config.get('agentLayer') != 'action'
    from .data_contracts import VERSION, UAV_AGENTS, uav_input
    from .uav_execution_contracts import VERSION as ROLE_VERSION, RUNTIME as ROLE_RUNTIME
    contracted_uav = config.get('dataContractVersion') == VERSION and agent_key in UAV_AGENTS
    role_dag = (agent_key in UAV_AGENTS and config.get('engineMode') == ROLE_RUNTIME
                and config.get('roleContractVersion') == ROLE_VERSION)
    research_only = research_available and (not steps or contracted_uav) and not role_dag
    if research_only:
        steps = [{'step_key':'research-envelope', 'name':'受限研究域', 'sequence_no':1,
                  'description':'依据冻结的职责、工具授权、证据契约和共享预算生成本次研究计划。',
                  'tools':sorted(binding['tool_key'] for binding in document.get('bindings', []) if binding.get('enabled')),
                  'input_contract':uav_input() if contracted_uav else {'type':'object'},
                  'output_contract':deepcopy(config.get('researchOutputContract', {}) if contracted_uav else
                                             (document.get('output') or {}).get('schema_json') or {}),
                  'retry_limit':resources.get('max_retries_per_tool',0), 'required':True}]
    nodes, edges = [], []
    explicit = any(step.get('dependencies') for step in steps)
    previous_group, current_group, previous_name = [], [], None
    for step in sorted(steps, key=lambda item: (item['sequence_no'], item['step_key'])):
        key = step['step_key']
        node_policy = policies.get(key) or {}
        dependencies = list(step.get('dependencies') or [])
        if not explicit:
            name = step.get('parallel_group')
            if not name or name != previous_name:
                previous_group, current_group = current_group or previous_group, []
            dependencies = list(previous_group)
            current_group.append(key)
            previous_name = name
        child_agent_key = node_policy.get('childAgentKey')
        if child_agent_key is not None and (not isinstance(child_agent_key, str) or not child_agent_key.strip()):
            raise ValueError(f'node {key} has an invalid childAgentKey')
        kind = node_policy.get('nodeType') or (
            'action' if child_agent_key else 'human_review' if key == 'human-review' else 'stage')
        composition_edge_id = node_policy.get('compositionEdgeId')
        if child_agent_key and not composition_edge_id:
            edge = composition_edges.get(child_agent_key)
            composition_edge_id = edge.get('edge_id') if edge else None
        ceiling = budgets.get(key)
        budget = {field: ceiling[field] for field in NODE_BUDGET_FIELDS if ceiling and field in ceiling}
        if not ceiling:
            budget = {field: totals.get(field, 0) for field in COUNTERS}
            budget.update(max_rounds=1, max_parallelism=resources.get('max_parallel_tools', 1),
                          max_runtime_seconds=convergence.get('max_runtime_seconds', 300))
            # Keep old schemas and their already registered baselines stable;
            # extended ceilings appear only when the Harness migration or an
            # explicit profile policy supplies them.
            if any(key in resources or key in convergence or key in config
                   for key in ('max_retries_per_tool', 'max_evidence_bytes', 'max_rounds',
                               'max_iterations', 'max_concurrency', 'estimated_cost_micros')):
                budget.update(max_iterations=convergence.get('max_iterations', 1),
                              max_retries=resources.get('max_retries_per_tool', 0),
                              max_concurrency=resources.get('max_concurrency', resources.get('max_parallel_tools', 1)),
                              max_evidence_bytes=resources.get('max_evidence_bytes', 0),
                              estimated_cost_micros=config.get('estimated_cost_micros', 0))
        budget.update(node_policy.get('budget') or {})
        if config.get('dataContractVersion') == VERSION:
            for counter in COUNTERS:
                if counter in totals and counter in budget:
                    budget[counter] = min(budget[counter], totals[counter])
        # A loop re-enters its return node once per allowed iteration.  Encode
        # the initial round plus those re-entries in the immutable baseline so
        # runtime enforcement never widens a harness ceiling dynamically.
        for loop in (document.get('loops') or []):
            return_key = loop.get('return_step_key') or loop.get('returnStepKey')
            if return_key == key:
                loop_rounds = int(loop.get('max_iterations', loop.get('maxIterations', 0)) or 0) + 1
                budget['max_rounds'] = max(int(budget.get('max_rounds', 0) or 0), loop_rounds)
        node = {
            'id': key, 'name': step['name'], 'description': step.get('description', ''),
            'sequence': step['sequence_no'], 'nodeType': kind,
            'plannerMode': node_policy.get('plannerMode', 'expandable' if research_only else 'fixed'),
            'required': bool(step.get('required', True)),
            'tools': list(step.get('tools') or []),
            'inputContract': deepcopy(step.get('input_contract') or {}),
            'outputContract': deepcopy(step.get('output_contract') or {}),
            'retryLimit': step.get('retry_limit', 0), 'budget': budget,
            # A baseline node may delegate to exactly one reviewed Action Agent.
            # This is an executable binding, not a descriptive capability tag.
            'childAgentKey': child_agent_key,
            'compositionEdgeId': composition_edge_id,
            'expansion': deepcopy(node_policy.get('expansion') or
                ({'maxNodes':24,'allowedToolKeys':step['tools']} if research_only else {})),
            'failurePolicy': deepcopy(node_policy.get('failurePolicy') or {'action': 'stop'}),
        }
        if node_policy.get('executorKey'):
            node['executorKey'] = node_policy['executorKey']
        if role_dag or config.get('cognitiveSuiteVersion') == '1.0.0':
            for field in ('executionKind', 'condition', 'completionCriteria', 'completionChecks'):
                if field in node_policy:
                    node[field] = deepcopy(node_policy[field])
        if role_dag:
            for field in ('executionKind', 'condition', 'completionCriteria', 'completionChecks'):
                if field in node_policy:
                    node[field] = deepcopy(node_policy[field])
        skill_bindings = [deepcopy(b) for b in harness.get('skillBindings',[]) if b.get('stepKey') == key]
        if skill_bindings:
            node['skillBindings'] = skill_bindings
        nodes.append(node)
        edges.extend({'from': dep, 'to': key, 'kind': 'dependency'} for dep in dependencies)
    # Re-entering a loop return node invalidates its dependency descendants;
    # those nodes are executed again in the same iteration and need the same
    # durable round ceiling.  Propagate the ceiling across the DAG instead of
    # letting the executor infer it from mutable Profile state.
    for loop in (document.get('loops') or []):
        return_key = loop.get('return_step_key') or loop.get('returnStepKey')
        if not return_key:
            continue
        loop_rounds = int(loop.get('max_iterations', loop.get('maxIterations', 0)) or 0) + 1
        affected = {return_key}
        changed = True
        while changed:
            changed = False
            for edge in edges:
                if edge['from'] in affected and edge['to'] not in affected:
                    affected.add(edge['to'])
                    changed = True
        for node in nodes:
            if node['id'] in affected:
                node['budget']['max_rounds'] = max(int(node['budget'].get('max_rounds', 0) or 0), loop_rounds)
    result = {
        'schemaVersion': 'execution-baseline.v1', 'graphType': 'baseline',
        'agentKey': agent_key,
        'profileVersion': (document.get('definition') or {}).get('current_version', 'unknown'),
        'engineMode': config.get('engineMode', 'fixed'),
        'supportedModes': ['research'] if research_only else ['fixed','research'] if research_available else ['fixed'],
        'mode': 'research-envelope' if research_only else 'fixed-graph',
        'nodes': nodes, 'edges': sorted(edges, key=lambda edge: (edge['from'], edge['to'])),
        'limits': {'counters': totals, 'maxParallelism': resources.get('max_parallel_tools', 1),
                   'maxRuntimeSeconds': convergence.get('max_runtime_seconds', 300)},
        'loopPolicies': deepcopy(document.get('loops') or []),
        'researchPolicy': {'enabled':research_available,'maxNodes':24,'maxPlanVersions':4,
            'allowedToolKeys':sorted(binding['tool_key'] for binding in document.get('bindings', []) if binding.get('enabled')),
            'parentNodeKey':'research-envelope' if research_only else None},
    }
    if config.get('actionReasoningVersion'):
        result['actionReasoningVersion'] = config['actionReasoningVersion']
    if role_dag:
        result.update(roleContractVersion=config['roleContractVersion'],
                      roleContractHash=config.get('roleContractHash'),
                      modelConfiguration={'provider': (document.get('models') or {}).get('model_provider'),
                                          'modelName': (document.get('models') or {}).get('model_name')})
    if any(key in resources or key in convergence or key in config
           for key in ('max_retries_per_tool', 'max_evidence_bytes', 'max_rounds',
                       'max_iterations', 'max_concurrency', 'estimated_cost_micros')):
        result['limits'].update(maxConcurrency=resources.get('max_concurrency', resources.get('max_parallel_tools', 1)),
                                maxRounds=config.get('max_rounds', 1),
                                maxIterations=convergence.get('max_iterations', 0),
                                maxEvidenceBytes=resources.get('max_evidence_bytes', 0),
                                estimatedCostMicros=config.get('estimated_cost_micros', 0))
    return result


def build_profile_baseline(profile):
    """Preserve frozen method bindings when deriving a constrained Run graph."""
    from .skills import validate_manifest,reference
    manifest = validate_manifest(getattr(profile,'skill_manifest',{}) or {},profile.agent_key,profile.version)
    return build_baseline(profile.agent_key,profile_document(profile),
        {**(profile.harness_contract or {}),
         'skillBindings':[reference(b) for b in manifest.get('bindings',[])]})


def profile_document(profile):
    return {
        'definition': {'current_version': profile.version}, 'models': deepcopy(profile.model_policy),
        'convergence': deepcopy(profile.convergence), 'resources': deepcopy(profile.resource_policy),
        'bindings': [{'tool_key':key,'enabled':binding.enabled} for key,binding in profile.tools.items()],
        'output':deepcopy(profile.output_contract),
        'steps': [{
            'step_key': step.key, 'name': step.name, 'description': step.description, 'sequence_no': step.sequence,
            'parallel_group': step.parallel_group, 'required': step.required,
            'retry_limit': step.retry_limit, 'dependencies': list(step.dependencies),
            'tools': list(step.tools), 'input_contract': step.input_contract,
            'output_contract': step.output_contract,
        } for step in profile.steps], 'loops': list(profile.loop_rules),
    }


def validate_baseline(graph, tools=None):
    """Return structured failures; unsupported runtime policies fail closed."""
    errors = []
    def bad(path, message):
        errors.append({'path': path, 'message': message})
    nodes = graph.get('nodes', [])
    if not nodes:
        bad('/nodes', 'baseline has no executable fixed nodes')
        return errors
    by_key = {node['id']: node for node in nodes}
    if len(by_key) != len(nodes):
        bad('/nodes', 'duplicate node IDs')
    incoming = {key: set() for key in by_key}
    outgoing = {key: set() for key in by_key}
    for edge in graph.get('edges', []):
        source, target = edge['from'], edge['to']
        if source not in by_key or target not in by_key:
            bad('/edges', 'edge refers to a missing node')
        elif source == target:
            bad('/edges', 'node cannot depend on itself')
        else:
            incoming[target].add(source)
            outgoing[source].add(target)
        if edge.get('kind') != 'dependency':
            bad('/edges', 'this runtime supports dependency edges only')
    ready = sorted(key for key in by_key if not incoming[key])
    complete = set()
    while ready:
        key = ready.pop(0)
        complete.add(key)
        for target in outgoing[key]:
            if target not in complete and incoming[target] <= complete and target not in ready:
                ready.append(target)
    if len(complete) != len(nodes):
        bad('/edges', 'execution graph contains a cycle')
    totals = graph.get('limits', {}).get('counters', {})
    for node in nodes:
        path = '/nodes/' + node['id']
        if node['nodeType'] not in NODE_TYPES:
            bad(path + '/nodeType', 'unsupported node type')
        if node['plannerMode'] not in {'fixed', 'expandable'}:
            bad(path + '/plannerMode', 'unsupported planner mode')
        if node['failurePolicy'] != {'action': 'stop'}:
            bad(path + '/failurePolicy', 'this runtime supports stop-on-failure only')
        for field, value in node['budget'].items():
            if field not in set(NODE_BUDGET_FIELDS):
                bad(path + '/budget/' + field, 'unsupported budget unit')
            elif type(value) is not int or value < (1 if field in {'max_parallelism', 'max_concurrency', 'max_runtime_seconds'} else 0):
                bad(path + '/budget/' + field, 'budget must be a bounded nonnegative integer')
            elif field in totals and value > totals[field]:
                bad(path + '/budget/' + field, 'node ceiling exceeds Agent ceiling')
        if tools is not None and any(key not in tools for key in node['tools']):
            bad(path + '/tools', 'node requires an enabled Profile tool')
        child = node.get('childAgentKey')
        if child is not None:
            if node['nodeType'] != 'action':
                bad(path + '/nodeType', 'child Agent bindings require an action node')
            if not isinstance(child, str) or not child.strip():
                bad(path + '/childAgentKey', 'child Agent key must be a non-empty string')
            if not isinstance(node.get('compositionEdgeId'), str) or not node['compositionEdgeId']:
                bad(path + '/compositionEdgeId', 'action node must bind a reviewed composition edge')
        if node['plannerMode'] == 'expandable':
            expansion = node['expansion']
            if 'research' not in graph.get('supportedModes', []):
                bad(path + '/plannerMode', 'dynamic expansion requires research-v1 runtime')
            if not isinstance(expansion.get('maxNodes'), int) or not 1 <= expansion.get('maxNodes', 0) <= 24:
                bad(path + '/expansion/maxNodes', 'dynamic node limit must be between 1 and 24')
            allowed = expansion.get('allowedToolKeys')
            if not isinstance(allowed, list) or not allowed or any(key not in node['tools'] for key in allowed):
                bad(path + '/expansion/allowedToolKeys', 'expansion tools must be a nonempty subset of node tools')
    if len(nodes) > 1:
        isolated = [key for key in by_key if not incoming[key] and not outgoing[key]]
        if isolated:
            bad('/nodes', 'isolated nodes: ' + ', '.join(isolated))
    return errors


def baseline_view(graph, *, row=None, tools=None):
    errors = validate_baseline(graph, tools)
    return {
        'graph': graph, 'graphHash': graph_hash(graph), 'graphType': 'baseline',
        'baselineId': row['baseline_id'] if row else None,
        'baselineVersion': row['baseline_version'] if row else None,
        'status': row['status'] if row else 'unregistered',
        'publishedAt': str(row['published_at']) if row and row.get('published_at') else None,
        'validation': {'accepted': not errors, 'errors': errors},
    }


def read_baseline(cursor, agent_key, graph):
    if not baseline_installed(cursor):
        return baseline_view(graph)
    cursor.execute('''SELECT baseline_id,baseline_version,status,published_at
                       FROM agent_execution_baseline
                      WHERE agent_key=%s AND profile_version=%s AND graph_hash=%s
                      ORDER BY created_at DESC LIMIT 1''',
                   (agent_key, graph['profileVersion'], graph_hash(graph)))
    row = cursor.fetchone()
    return baseline_view(graph, row=dict(row) if row else None)


def register_baseline(cursor, agent_key, graph, *, status='testing', created_by='migration'):
    from psycopg2.extras import Json
    from .repository import new_id
    errors = validate_baseline(graph)
    if errors:
        raise ValueError('invalid execution baseline: ' + '; '.join(error['message'] for error in errors))
    digest = graph_hash(graph)
    cursor.execute('''SELECT * FROM agent_execution_baseline
                      WHERE agent_key=%s AND profile_version=%s AND graph_hash=%s
                      ORDER BY baseline_version DESC LIMIT 1''',
                   (agent_key, graph['profileVersion'], digest))
    existing = cursor.fetchone()
    if existing and (status != 'published' or existing['status'] == 'published'):
        return baseline_view(graph, row=dict(existing))
    cursor.execute('SELECT COALESCE(max(baseline_version),0)+1 AS next FROM agent_execution_baseline WHERE agent_key=%s',
                   (agent_key,))
    version = cursor.fetchone()['next']
    cursor.execute('''INSERT INTO agent_execution_baseline(
        baseline_id,agent_key,profile_version,baseline_version,schema_version,status,graph_json,graph_hash,created_by,published_at)
        VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,CASE WHEN %s='published' THEN now() ELSE NULL END) RETURNING *''',
        (new_id('baseline'), agent_key, graph['profileVersion'], version, graph['schemaVersion'], status,
         Json(graph), digest, created_by, status))
    row = dict(cursor.fetchone())
    for edge in graph['edges']:
        cursor.execute('''INSERT INTO agent_execution_edge(baseline_id,from_node_key,to_node_key,edge_type)
                          VALUES(%s,%s,%s,%s)''',
                       (row['baseline_id'], edge['from'], edge['to'], edge['kind']))
    return baseline_view(graph, row=row)
