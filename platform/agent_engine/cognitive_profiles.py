"""Profile and baseline source for the proposed ontology cognitive suite.

This module is deliberately independent of the database.  It produces the
authoring document consumed by the transactional registration command.  It
does not install executors, grant network access, create Runs, or publish a
Profile.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path

try:
    from agent_tools.cognitive import TOOL_KEYS as COGNITIVE_TOOL_KEYS
except Exception:  # Keep authoring module importable before runtime plugin load.
    COGNITIVE_TOOL_KEYS = (
        'ontology.map_candidates', 'artifact.read_capability',
        'analysis.explanation_graph', 'analysis.report_contract_validate',
        'analysis.temporal_align', 'analysis.rule_effectivity',
        'analysis.constraint_evaluate', 'analysis.comparison_validate',
        'analysis.temporal_validate', 'ontology.shape_validate',
        'ontology.relation_validate')


ROOT = Path(__file__).resolve().parents[2]
DESIGN = ROOT / 'docs' / 'agent-designs' / 'ontology-cognitive-suite' / 'design.json'
VERSION = '1.0.0'
ENGINE_MODE = 'cognitive-suite-v1'
APPLICATION_KEY = 'ontology-cognitive-analyst'
ACTION_KEYS = ('situation-assessment-action', 'rule-applicability-action',
               'tactics-research-action', 'ontology-consistency-action')
AGENT_KEYS = (APPLICATION_KEY, *ACTION_KEYS)

TOOL_VERSIONS = {
    'analysis.citation_validate': '1.2.6',
    'analysis.claim_audit': '1.2.6',
    'analysis.event_normalize': '1.2.6',
    'analysis.evidence_rank': '1.0.9',
    'analysis.scenario_structure': '1.2.6',
    'analysis.source_deduplicate': '1.2.6',
    'ontology.get_entity': '3.0.0',
    'ontology.query_evidence': '3.0.1',
    'web.search': '3.1.0',
    'web.scrape': '3.1.0',
}
TOOL_VERSIONS.update({key: '1.0.0' for key in COGNITIVE_TOOL_KEYS})

NAMES = {
    APPLICATION_KEY: ('认知方案综合 Agent', '组织公开资料和冻结仿真情景的本体认知研究，交付可追溯的条件性研究报告。'),
    ACTION_KEYS[0]: ('态势评估 Agent', '在冻结时点分析公开观察、状态变化和不确定性。'),
    ACTION_KEYS[1]: ('规则适用性审查 Agent', '解读公开规则文献的版本、适用条件和形式化检查结果。'),
    ACTION_KEYS[2]: ('战法研究比较 Agent', '在公开文献和仿真假设内比较研究选项及其限制。'),
    ACTION_KEYS[3]: ('本体一致性审查 Agent', '检查候选关系和专业视图的一致性并解释冲突。'),
}

FORBIDDEN = ['operational-tasking', 'target-assignment', 'weapon-control',
             'engagement-authorization', 'formal-ontology-write', 'self-expand-authority']

# Reviewed runtime authoring revision. The original proposed design remains
# immutable; Profile and initial Harness rows must derive the same allowances.
RUNTIME_BUDGET_REVISION = 'cognitive-comparison-input-allowance.20261119'
RUNTIME_NODE_INPUT_OVERRIDES = {
    ('tactics-research-action', key): 12000
    for key in ('compare-research-options', 'review-counterevidence')}


def effective_node_budget(agent_key, node):
    budget = deepcopy(node.get('budget') or {})
    maximum = RUNTIME_NODE_INPUT_OVERRIDES.get((agent_key, node['id']))
    if maximum is not None:
        if budget.get('input_tokens') != 8000:
            raise ValueError('comparison input authoring revision requires its unchanged proposed baseline')
        budget['input_tokens'] = maximum
    return budget


def _json_hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def design():
    return json.loads(DESIGN.read_text(encoding='utf-8'))


def _inline_schema(required, *, title='Cognitive step envelope'):
    properties = {
        'nodeKey': {'type': 'string', 'minLength': 1},
        'stepKey': {'type': 'string', 'minLength': 1},
        'status': {'enum': ['succeeded', 'partial', 'not-applicable', 'blocked']},
        'contextHash': {'type': 'string', 'pattern': '^[a-f0-9]{64}$'},
        'inputArtifactRefs': {'type': 'array', 'items': {'type': 'object'}, 'maxItems': 500},
        'product': {'type': 'object'}, 'artifactRef': {'type': 'object'},
        'data': {'type': 'object'}, 'gaps': {'type': 'array', 'items': {'type': 'object'}},
        'claims': {'type': 'array', 'items': {'type': 'object'}},
        'toolResults': {'type': 'object'}, 'actionInvocation': {'type': 'object'},
        'actionOutput': {'type': 'object'},
    }
    return {'title': title, 'type': 'object', 'additionalProperties': True,
            'properties': properties, 'required': required}


def _product_schema(product_type):
    # ConfigPublication disallows even local $ref; resolve the installed typed
    # contract into an independent, bounded inline schema before persistence.
    from .cognitive_contracts import schema
    base = schema(product_type)
    definitions = base.get('$defs', {})
    def resolve(value, trail=()):
        if isinstance(value, list): return [resolve(x, trail) for x in value]
        if not isinstance(value, dict): return deepcopy(value)
        ref = value.get('$ref')
        if ref:
            if not ref.startswith('#/$defs/'):
                raise ValueError('external cognitive schema references are forbidden')
            key = ref.rsplit('/', 1)[-1]
            if key in trail:
                raise ValueError('recursive cognitive schema cannot be inlined')
            target = resolve(definitions[key], (*trail, key))
            return {**target, **{k: resolve(v, trail) for k, v in value.items() if k != '$ref'}}
        return {k: resolve(v, trail) for k, v in value.items() if k not in {'$defs','$id','$schema'}}
    return resolve(base)


def _tool_keys(agent_key):
    if agent_key == APPLICATION_KEY:
        return ('web.search', 'web.scrape', 'analysis.source_deduplicate',
                'analysis.evidence_rank', 'analysis.event_normalize',
                'analysis.citation_validate', 'ontology.query_evidence', 'ontology.get_entity',
                *COGNITIVE_TOOL_KEYS)
    if agent_key == ACTION_KEYS[0]:
        return ('analysis.temporal_align', 'analysis.event_normalize', 'analysis.report_contract_validate', 'ontology.get_entity')
    if agent_key == ACTION_KEYS[1]:
        return ('analysis.rule_effectivity', 'analysis.constraint_evaluate',
                'analysis.report_contract_validate', 'ontology.query_evidence')
    if agent_key == ACTION_KEYS[2]:
        return ('analysis.comparison_validate', 'analysis.report_contract_validate', 'analysis.claim_audit')
    return ('ontology.shape_validate', 'ontology.relation_validate',
            'analysis.temporal_validate', 'analysis.report_contract_validate', 'analysis.claim_audit', 'ontology.query_evidence')


def _document_steps(agent_key, spec):
    profile = next(p for p in spec['profiles'] if p['agentKey'] == agent_key)
    child_bindings = {
        'deduplicate-sources': 'evidence-collection-action',
        'extract-claims': 'evidence-extraction-action',
        'assess-situation': ACTION_KEYS[0], 'assess-rules': ACTION_KEYS[1],
        'compare-options': ACTION_KEYS[2], 'check-consistency': ACTION_KEYS[3],
    }
    steps = []
    for seq, node in enumerate(profile['nodes'], 1):
        key = node['id']
        kind = node['executionKind']
        # The proposed design names become reviewed runtime tools only after
        # the cognitive tool registry migration.  Keep the exact key so the
        # Plan, binding, and runtime receipt cannot silently diverge.
        tools = [x.removeprefix('proposed.') for x in node.get('toolKeys', [])]
        if key in {'verify-state-evidence', 'verify-rule-evidence', 'verify-comparison-evidence', 'validate-delivery'}:
            tools = ['analysis.report_contract_validate']
            if key == 'verify-comparison-evidence':
                tools.insert(0, 'analysis.comparison_validate')
        if agent_key != APPLICATION_KEY:
            tools = [x for x in tools if x in _tool_keys(agent_key)]
        output = node['outputType']
        steps.append({
            'step_key': key, 'name': node['name'],
            'description': f"{NAMES[agent_key][0]}：{node['name']}。只处理公开研究或冻结仿真范围。",
            'sequence_no': seq, 'input_contract': _inline_schema(['input', 'state'], title='Cognitive runtime input'),
            'output_contract': _inline_schema(['nodeKey', 'status', 'contextHash', 'inputArtifactRefs']),
            'parallel_group': 'professional-views' if key in {'assess-situation','assess-rules','read-capability-evidence'} else None,
            'required': True, 'retry_limit': min(int(node.get('max_retries', 1)), 1),
            'dependencies': list(node.get('dependencies', [])), 'tools': sorted(tools),
        })
    return steps


def _node_policies(agent_key, spec):
    profile = next(p for p in spec['profiles'] if p['agentKey'] == agent_key)
    policy = {}
    for node in profile['nodes']:
        key, kind = node['id'], node['executionKind']
        value = {'nodeType': 'action' if key in {
                    'deduplicate-sources','extract-claims','assess-situation','assess-rules',
                    'compare-options','check-consistency'} else ('tool' if kind == 'tool' else 'stage'),
                 'executionKind': kind, 'executorKey': f'cognitive.{key}.v1',
                 'condition': 'always', 'completionCriteria': ['schema-valid', 'receipt-recorded'],
                 'completionChecks': list(node.get('completionChecks', [])),
                 'budget': effective_node_budget(agent_key, node), 'failurePolicy': {'action': 'stop'}}
        if key in {'deduplicate-sources','extract-claims','assess-situation','assess-rules','compare-options','check-consistency'}:
            value['childAgentKey'] = {
                'deduplicate-sources': 'evidence-collection-action',
                'extract-claims': 'evidence-extraction-action',
                'assess-situation': ACTION_KEYS[0], 'assess-rules': ACTION_KEYS[1],
                'compare-options': ACTION_KEYS[2], 'check-consistency': ACTION_KEYS[3],
            }[key]
            value['compositionEdgeId'] = f'cognitive-suite:{agent_key}:{value["childAgentKey"]}'
        policy[key] = value
    return policy


def build_document(agent_key, *, version=VERSION):
    if agent_key not in AGENT_KEYS:
        raise ValueError(f'unknown cognitive suite Agent: {agent_key}')
    spec = design()
    profile = next(p for p in spec['profiles'] if p['agentKey'] == agent_key)
    tools = _tool_keys(agent_key)
    is_app = agent_key == APPLICATION_KEY
    output_type = profile['outputSchemaRef'].rsplit('/', 1)[-1]
    budgets = dict(profile['budgetCeiling'])
    model_config = {
        'researchModeAvailable': True, 'cognitiveSuiteVersion': VERSION,
        'researchMethodsVersion': '1.0.0',
        'actionReasoningVersion': 'child-v1',
        'ontologySourceKey': 'uav-ontology', 'webProviderKey': 'firecrawl',
        'budgetLimits': budgets, 'capabilityActions': [],
        'sourcePolicy': {'allowedDomains': ['w3.org', 'nist.gov'],
                         'includeSubdomains': True, 'maxSources': 4, 'maxPages': budgets.get('scrape_calls', 4),
                         'maxMarkdownChars': 24000},
        'executionBaseline': {'nodePolicies': _node_policies(agent_key, spec)},
        'outputContractVersion': 'ontology-cognitive.v1',
        'runtimeAdmission': 'cognitive-suite-v1', 'profileStatus': 'testing',
    }
    from .cognitive_implementation import implementation_hash
    model_config['cognitiveImplementationHash'] = implementation_hash()
    if not is_app:
        model_config.update(agentLayer='action', runtimeKind='deterministic_action', actionExecutor='deterministic-v1')
    else:
        model_config.update(agentLayer='application', runtimeKind='llm_loop')
    # These are reviewed capability graph files installed with the suite. The
    # graph's action manifest is checked again against the live DB at publish
    # and Run admission; file presence alone is never invocation proof.
    model_config['capabilityGraphPath'] = f'capability_graphs/{agent_key}.v1.json'
    if is_app:
        model_config['capabilityActions'] = sorted(set(
            ['evidence-collection-action', 'evidence-extraction-action', *ACTION_KEYS]))
    bindings = [{'tool_key': k, 'tool_version': TOOL_VERSIONS[k], 'enabled': True,
                 'approval_required': False, 'config_json': {'timeoutSeconds': 60, 'retryLimit': 1}}
                for k in tools]
    steps = _document_steps(agent_key, spec)
    prompt = (f"你是{NAMES[agent_key][0]}。" + NAMES[agent_key][1] +
              '仅处理公开资料研究和冻结仿真，不执行目标定位、交战授权、武器控制或正式本体写入。'
              '所有输出必须结构化，绑定输入 Artifact、快照、条件、证据、未知和冲突；未知不得判定为通过。')
    return {
        'definition': {'agent_key': agent_key, 'display_name': NAMES[agent_key][0],
            'description': NAMES[agent_key][1], 'domain': 'ontology-cognitive-research',
            'status': 'testing', 'current_version': version, 'system_prompt': prompt},
        'scope': {'responsibility_json': {'goal': profile['goal'], 'scope': ['public-research','simulation']},
            'input_entities_json': ['ResearchQuestion','ScenarioRef','OntologySnapshotRef','RuleSetRef'],
            'output_entities_json': [output_type], 'allowed_write_json': [], 'forbidden_actions_json': FORBIDDEN},
        'models': {'model_provider': 'deepseek', 'model_name': 'deepseek-v4-pro', 'temperature': 0.1,
            'max_output_tokens': 8192, 'structured_output_required': True, 'planner_enabled': False,
            'prompt_version': f'cognitive-suite-{VERSION}', 'config_json': model_config},
        'resources': {'max_parallel_tools': 3, 'max_retries_per_tool': 1,
                      'permitted_risk_levels_json': ['low']},
        'convergence': {'completion_conditions_json': ['output-contract-valid','evidence-links-valid'],
            'allowed_terminal_states_json': ['completed','completed-with-gaps','blocked','failed'],
            'max_tool_calls': budgets['tool_calls'], 'max_model_calls': budgets['model_calls'],
            'max_runtime_seconds': 900 if is_app else 300, 'no_new_finding_rounds': 1},
        'output': {'schema_json': _product_schema(output_type), 'required_evidence': True,
                   'required_assumptions': True, 'required_confidence': True},
        'evidence': {'minimum_evidence_count': 1 if is_app else 0, 'require_source_uri': True,
            'require_data_timestamp': True, 'allow_inference': True,
            'inference_disclosure_required': True, 'conflict_action': 'retain-and-escalate',
            'policy_json': {'unknownPolicy': 'preserve'}},
        'approval': {'approval_points_json': [], 'approver_roles_json': ['reviewer']},
        'observability': {'trace_enabled': True, 'capture_tool_inputs': False,
            'capture_tool_outputs': False, 'retain_days': 365, 'emit_step_events': True,
            'emit_metrics': True, 'policy_json': {'emitModelReceipt': True, 'emitArtifactReceipt': True}},
        'steps': steps, 'bindings': bindings, 'loops': [],
        'validationRules': [{'rule_id': 'cognitive-no-operational-actions', 'rule_type': 'forbidden-capability',
            'expression': 'output contains no operational tasking, target assignment, weapon control or engagement authorization',
            'severity': 'blocking'}],
    }


def composition_edges(parent_version=VERSION):
    children = ('evidence-collection-action', 'evidence-extraction-action', *ACTION_KEYS)
    return [{'edge_id': f'cognitive-suite:{APPLICATION_KEY}:{child}',
             'parent_agent_key': APPLICATION_KEY, 'child_agent_key': child,
             'parent_profile_version': parent_version, 'child_profile_version': None,
             'handoff_schema_json': {'schemaVersion': 'ontology-cognitive.v1', 'directArtifactRefsOnly': True},
             'allowed_artifact_types_json': ['agent-output', 'cognitive-input', 'cognitive-product'],
             'budget_mode': 'shared', 'max_depth': 1, 'approval_required': False, 'enabled': True}
            for child in children]


def capability_graph(agent_key):
    path = ROOT / 'docs' / 'agent-designs' / 'ontology-cognitive-suite' / 'capability-graphs.json'
    all_graphs = json.loads(path.read_text(encoding='utf-8'))['graphs']
    return next(graph for graph in all_graphs if graph['agentKey'] == agent_key)


def document_hash(document):
    return _json_hash(document)


