"""Reviewed admission requirements for the three record-review roles.

These are target DAG contracts, not installed execution capabilities. A new
Run must satisfy both the contract and the server's implementation registry.
No request flag, model output or Profile JSON can register an implementation.
Historical Run snapshots and execution paths are deliberately not changed.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
from types import MappingProxyType


VERSION = 'uav-role-dag.v1'
RUNTIME = 'uav-role-dag-v1'
AGENTS = frozenset({'uav-capability-analyst', 'uav-design-engineer', 'uav-evidence-reviewer'})

# Batch 1 defines admission; batch 2 must install reviewed handlers AND route
# this runtime in AgentEngine. Merely populating executorKey in a Profile is
# insufficient. These server-code registries must not be filled from config.
RUNTIME_INSTALLED = False
REVIEWED_EXECUTORS = MappingProxyType({})


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(',', ':'), allow_nan=False).encode()).hexdigest()


@dataclass(frozen=True)
class Stage:
    key: str
    name: str
    kind: str
    dependencies: tuple[str, ...]
    outputs: tuple[str, ...]
    criterion: str
    group: str = ''
    condition: str = 'always'


STAGES = MappingProxyType({
    'uav-capability-analyst': (
        Stage('cap-freeze-scope', '冻结对象与审查范围', 'api', (), ('scope',), '记录对象、配置、时间与问题；缺少要求时仅进行资料盘点。'),
        Stage('cap-read-ontology', '读取本体资料', 'tool', ('cap-freeze-scope',), ('sourceInventory',), '保存获准读取的资料与来源引用。'),
        Stage('cap-check-upstream', '核对上游资料', 'api', ('cap-freeze-scope',), ('upstreamInventory',), '校验上游所有者与内容哈希；独立任务记录无上游。'),
        Stage('cap-plan-acquisition', '识别待回答问题', 'llm', ('cap-read-ontology', 'cap-check-upstream'), ('acquisitionPlan',), '计划逐项对应问题与资料缺口，不扩大范围或预算。', 'evidence-research'),
        Stage('cap-acquire-sources', '按授权补充资料', 'tool', ('cap-plan-acquisition',), ('sourceSnapshots',), '保留检索与正文；无需补证时记录无调用理由。', 'evidence-research', 'open-acquisition-gap'),
        Stage('cap-extract-claims', '提取主张与原文', 'llm', ('cap-acquire-sources',), ('candidateClaims',), '主张绑定原文、位置、对象与条件，缺失不推测。', 'evidence-research'),
        Stage('cap-check-citations', '检查引文与定位', 'tool', ('cap-extract-claims',), ('citationChecks',), '对保存正文复核引文位置及哈希；准确引文不等于事实成立。', 'evidence-research'),
        Stage('cap-save-evidence', '保存证据包和缺口', 'api', ('cap-check-citations',), ('evidenceBundle', 'evidenceGaps'), '证据与未解决缺口均有稳定标识和可追溯引用。', 'evidence-research'),
        Stage('cap-assess-support', '分析证据支持与缺口', 'llm', ('cap-save-evidence',), ('assessment', 'assumptions'), '每项判断映射具体问题及支持、冲突或未知。'),
        Stage('cap-compose-delivery', '生成报告与关系候选', 'api', ('cap-assess-support',), ('deliveryPackage',), '报告和本体关系候选引用相同的已保存依据。'),
        Stage('cap-validate-delivery', '交付验收', 'api', ('cap-compose-delivery',), ('deliveryAssessment',), '校验必需阶段回执、产物引用、Schema、预算与交付覆盖。'),
    ),
    'uav-design-engineer': (
        Stage('doc-freeze-scope', '冻结资料与审查目标', 'api', (), ('scope',), '范围限于工程资料与需求、验证记录的一致性审查。'),
        Stage('doc-extract-requirements', '提取文档要求', 'llm', ('doc-freeze-scope',), ('documentRequirements',), '每条要求引用原文，不生成设计参数或替代缺失要求。'),
        Stage('doc-index-records', '登记资料与验证记录', 'tool', ('doc-freeze-scope',), ('recordInventory',), '登记对象、配置、版本、验证记录与来源，不推断未执行试验。'),
        Stage('doc-review-consistency', '审查要求与资料一致性', 'llm', ('doc-extract-requirements', 'doc-index-records'), ('consistencyFindings',), '问题指向具体要求和文档记录，保留适用范围差异。'),
        Stage('doc-check-traceability', '检查引用与追溯覆盖', 'tool', ('doc-extract-requirements', 'doc-index-records'), ('traceabilityChecks',), '逐项标明要求、文档和验证记录之间的链接或缺口。'),
        Stage('doc-summarize-gaps', '归纳冲突和缺失输入', 'llm', ('doc-review-consistency', 'doc-check-traceability'), ('documentGaps',), '给出缺项及其依据，不把空矩阵表述为审查通过。'),
        Stage('doc-compose-delivery', '生成资料审查包', 'api', ('doc-summarize-gaps',), ('deliveryPackage',), '交付资料审查及验证资料缺口，保留未完成设计制造标记。'),
        Stage('doc-validate-delivery', '交付验收', 'api', ('doc-compose-delivery',), ('deliveryAssessment',), '验证阶段回执、要求覆盖、证据引用与声明边界。'),
    ),
    'uav-evidence-reviewer': (
        Stage('review-freeze-scope', '冻结审查对象和标准', 'api', (), ('reviewScope',), '冻结被审查报告、原始来源和评审标准。'),
        Stage('review-check-integrity', '校验产物与来源完整性', 'tool', ('review-freeze-scope',), ('integrityChecks',), '核对所有权、版本、内容哈希及缺失材料。'),
        Stage('review-check-citations', '核查原文和引文定位', 'tool', ('review-check-integrity',), ('citationChecks',), '直接检查保存原文，不能只读取上游摘要。'),
        Stage('review-check-source-links', '核查来源关联与独立性', 'tool', ('review-check-integrity',), ('sourceDependencyChecks',), '保留转载、共同来源与未知，不按网址数量计算独立性。'),
        Stage('review-check-semantics', '核查对象、条件与表述', 'llm', ('review-check-integrity',), ('semanticChecks',), '逐项核对被引对象、条件、时间和表述强度。'),
        Stage('review-assess-support', '复核判断支持程度', 'llm', ('review-check-citations', 'review-check-source-links', 'review-check-semantics'), ('reviewFindings',), '区分引文准确、判断支持与事实核验，并记录方法相关性。'),
        Stage('review-compose-delivery', '生成问题清单与追溯矩阵', 'api', ('review-assess-support',), ('reviewPackage', 'blockingIssues', 'traceabilityMatrix'), '问题定位到具体主张、证据或执行节点，说明影响和补证要求。'),
        Stage('review-validate-delivery', '审查交付验收', 'api', ('review-compose-delivery',), ('deliveryAssessment',), '自动交付审查草稿，保留未核实与未获发布授权的状态。'),
    ),
})


def _ref_schema(artifact_type=None):
    return {'type': 'object', 'required': ['runId', 'artifactId', 'artifactType', 'contentHash'],
            'additionalProperties': False, 'properties': {
                **{k: {'type': 'string', 'minLength': 1, 'maxLength': 200}
                   for k in ('runId', 'artifactId', 'artifactType')},
                'contentHash': {'type': 'string', 'pattern': '^[a-f0-9]{64}$'},
                **({'artifactType': {'const': artifact_type}} if artifact_type else {})}}


def role_contract(agent_key):
    """Return a detached specification; never return provider or source config."""
    if agent_key not in AGENTS:
        raise KeyError('unsupported role contract')
    nodes = []
    by_key = {stage.key: stage for stage in STAGES[agent_key]}
    scope_type = STAGES[agent_key][0].outputs[0]
    for stage in STAGES[agent_key]:
        inputs = ({'type': 'object', 'required': ['task'], 'additionalProperties': False,
                   'properties': {'task': {'type': 'object', 'required': ['entityId', 'question'],
                       'properties': {'entityId': {'type': 'string', 'minLength': 1},
                                      'question': {'type': 'string', 'minLength': 1},
                                      'requirements': {'type': 'object'}}}}} if not stage.dependencies else
                  {'type': 'object', 'required': ['scopeRef', 'inputs'], 'additionalProperties': False,
                   'properties': {'scopeRef': _ref_schema(scope_type), 'inputs': {
                       'type': 'object', 'required': list(stage.dependencies), 'additionalProperties': False,
                       'properties': {dep: {'type': 'object', 'required': list(by_key[dep].outputs),
                           'additionalProperties': False,
                           'properties': {k: _ref_schema(k) for k in by_key[dep].outputs}}
                           for dep in stage.dependencies}}}})
        outputs = {'type': 'object', 'required': ['nodeKey', 'status', 'artifacts', 'gaps'],
            'additionalProperties': False, 'properties': {
                'nodeKey': {'const': stage.key}, 'status': {'enum': ['succeeded', 'partial', 'not-applicable']},
                'artifacts': {'type': 'object', 'required': list(stage.outputs),
                              'additionalProperties': False,
                              'properties': {k: _ref_schema(k) for k in stage.outputs}},
                'gaps': {'type': 'array', 'maxItems': 100, 'items': {'type': 'object',
                    'required': ['code', 'subjectId', 'reason'], 'additionalProperties': False,
                    'properties': {k: {'type': 'string', 'minLength': 1, 'maxLength': 2000}
                                   for k in ('code', 'subjectId', 'reason')}}}}}
        minimum = {'model_calls': 1, 'http_calls': 1, 'input_tokens': 1, 'output_tokens': 1} if stage.kind == 'llm' else (
            {'tool_calls': 1} if stage.kind == 'tool' and stage.condition == 'always' else {})
        nodes.append({'id': stage.key, 'name': stage.name, 'required': True,
            'executionKind': stage.kind, 'group': stage.group, 'condition': stage.condition,
            'executorKey': 'uav-record-review.' + stage.key + '.v1',
            'inputContract': inputs, 'outputContract': outputs,
            'minimumBudget': minimum, 'completionCriteria': [stage.criterion],
            'completionChecks': ['output-schema-valid', 'dependency-artifact-hashes-valid',
                                 'scope-preserved', 'execution-receipt-recorded', 'budget-within-ceiling'],
            'budgetRequirements': ['model_calls', 'tool_calls', 'http_calls', 'search_calls',
                                   'scrape_calls', 'input_tokens', 'output_tokens',
                                   'max_rounds', 'max_parallelism', 'max_runtime_seconds'],
            'failurePolicy': {'action': 'stop'}})
    value = {'schemaVersion': VERSION, 'agentKey': agent_key, 'runtime': RUNTIME,
             'scope': 'record-and-evidence-review-only', 'nodes': nodes,
             'edges': sorted([{'from': dep, 'to': stage.key, 'kind': 'dependency'}
                              for stage in STAGES[agent_key] for dep in stage.dependencies],
                             key=lambda e: (e['from'], e['to'])),
             'budgetPolicy': 'per-node-ceilings-with-shared-root-budget',
             'publicationPolicy': 'unverified-draft-no-automatic-publication'}
    return {**value, 'contentHash': digest(value)}


MESSAGES = {
    'baseline-missing': '未登记执行基线。',
    'baseline-not-published': '执行基线尚未正式发布。',
    'baseline-invalid': '执行基线结构或资源约束无效。',
    'baseline-hash-mismatch': '执行基线内容与哈希不一致。',
    'baseline-profile-mismatch': '执行基线与 Agent 或 Profile 版本不一致。',
    'generic-research-envelope': '通用研究封套不能替代角色执行基线。',
    'role-contract-mismatch': '执行基线未绑定当前角色 DAG 契约。',
    'required-stage-missing': '缺少角色必需阶段。',
    'role-edges-mismatch': '执行依赖与角色契约不一致。',
    'unexpected-stage': '执行基线包含未经角色契约定义的固定阶段。',
    'node-contract-mismatch': '节点类型、输入输出、条件或完成标准与契约不一致。',
    'node-budget-insufficient': '节点资源额度不足以执行声明的工作。',
    'executor-not-implemented': '节点尚未绑定已实现的角色执行器。',
    'runtime-not-installed': '角色 DAG 调度器尚未安装，通用研究执行器不能代替。',
    'baseline-registration-mismatch': '数据库登记与当前基线快照不一致。',
    'baseline-registry-unavailable': '执行基线登记表尚未安装。',
}


def _issue(code, node=None):
    value = {'code': code, 'message': MESSAGES[code]}
    if node:
        value['nodeKey'] = node['id']
        value['nodeName'] = node['name']
    return value


def profile_issues(profile):
    """Pure readiness checks, with no configuration-controlled bypass."""
    if profile.agent_key not in AGENTS:
        return []
    from .execution_baseline import graph_hash, validate_baseline
    spec = role_contract(profile.agent_key)
    baseline = profile.execution_baseline if isinstance(profile.execution_baseline, dict) else {}
    graph = baseline.get('graph') if isinstance(baseline.get('graph'), dict) else {}
    issues = []
    if not baseline.get('baselineId'):
        issues.append(_issue('baseline-missing'))
    if baseline.get('status') != 'published' or not baseline.get('publishedAt'):
        issues.append(_issue('baseline-not-published'))
    if graph.get('agentKey') != profile.agent_key or graph.get('profileVersion') != profile.version:
        issues.append(_issue('baseline-profile-mismatch'))
    try:
        if graph_hash(graph) != baseline.get('graphHash'):
            issues.append(_issue('baseline-hash-mismatch'))
        if validate_baseline(graph, tools={k for k,v in profile.tools.items() if v.enabled}):
            issues.append(_issue('baseline-invalid'))
    except (KeyError, TypeError, ValueError, AttributeError):
        issues.append(_issue('baseline-invalid'))
    if graph.get('mode') == 'research-envelope':
        issues.append(_issue('generic-research-envelope'))
    if graph.get('roleContractVersion') != VERSION or graph.get('roleContractHash') != spec['contentHash']:
        issues.append(_issue('role-contract-mismatch'))
    if not RUNTIME_INSTALLED or graph.get('engineMode') != RUNTIME or profile.model_policy.get('config_json', {}).get('engineMode') != RUNTIME:
        issues.append(_issue('runtime-not-installed'))
    raw_nodes = graph.get('nodes', [])
    nodes = {n.get('id'): n for n in raw_nodes if isinstance(n, dict) and isinstance(n.get('id'), str)} if isinstance(raw_nodes, list) else {}
    if set(nodes) - {n['id'] for n in spec['nodes']}:
        issues.append(_issue('unexpected-stage'))
    if graph.get('edges') != spec['edges']:
        issues.append(_issue('role-edges-mismatch'))
    for expected in spec['nodes']:
        node = nodes.get(expected['id'])
        if node is None:
            issues.append(_issue('required-stage-missing', expected))
        else:
            fields = ('required', 'executionKind', 'executorKey', 'inputContract', 'outputContract',
                      'condition', 'completionCriteria', 'completionChecks', 'failurePolicy')
            if any(node.get(field) != expected[field] for field in fields):
                issues.append(_issue('node-contract-mismatch', expected))
            budget = node.get('budget') if isinstance(node.get('budget'), dict) else {}
            if (any(type(budget.get(k)) is not int or budget[k] < (1 if k.startswith('max_') else 0)
                    for k in expected['budgetRequirements']) or
                any(type(budget.get(k)) is not int or budget[k] < v for k,v in expected['minimumBudget'].items())):
                issues.append(_issue('node-budget-insufficient', expected))
        handler = REVIEWED_EXECUTORS.get((profile.agent_key, expected['id'], expected['executorKey']))
        if not callable(handler):
            issues.append(_issue('executor-not-implemented', expected))
    return issues


def publication_issues(agent_key, document, graph):
    """Validate a prospective publication without claiming it is published."""
    if agent_key not in AGENTS:
        return []
    from types import SimpleNamespace
    view = SimpleNamespace(agent_key=agent_key, version=document['definition']['current_version'],
        execution_baseline={'graph': graph, 'graphHash': digest(graph)},
        tools={b['tool_key']: SimpleNamespace(enabled=b.get('enabled', False)) for b in document.get('bindings', [])},
        model_policy=document.get('models', {}))
    # Publication state is checked atomically by ConfigPublicationService;
    # this check proves only the proposed graph and installed implementations.
    return [issue for issue in profile_issues(view)
            if issue['code'] not in {'baseline-missing', 'baseline-not-published'}]


def registration_issues(cursor, profile, *, lock=False):
    """Recheck the authoritative row in the admission transaction, before writes."""
    from .execution_baseline import baseline_installed, graph_hash
    if not baseline_installed(cursor):
        return [_issue('baseline-registry-unavailable')]
    baseline = profile.execution_baseline or {}
    if not baseline.get('baselineId'):
        return []  # Already reported by the pure checks.
    cursor.execute('''SELECT agent_key,profile_version,status,published_at,graph_hash,graph_json
                       FROM agent_execution_baseline WHERE baseline_id=%s''' + (' FOR SHARE' if lock else ''),
                   (baseline['baselineId'],))
    row = cursor.fetchone()
    try:
        valid = (row and row['agent_key'] == profile.agent_key and row['profile_version'] == profile.version
                 and row['graph_hash'] == baseline.get('graphHash')
                 and graph_hash(row['graph_json']) == row['graph_hash']
                 and row['status'] == baseline.get('status')
                 and bool(row['published_at']) == bool(baseline.get('publishedAt')))
    except (KeyError, TypeError, ValueError):
        valid = False
    return [] if valid else [_issue('baseline-registration-mismatch')]


class BaselineNotReady(ValueError):
    code = 'baseline-not-ready'

    def __init__(self, agent_key, issues):
        self.admission_details = {'code': self.code, 'reasonCode': self.code,
            'message': '角色执行基线未就绪；请先补齐并发布基线及执行器。',
            'agentKey': agent_key, 'contractVersion': VERSION, 'retryable': False,
            'issues': deepcopy(issues)}
        super().__init__('baseline-not-ready: 角色执行基线未就绪')


def require_ready(profile, cursor=None, *, lock=False):
    if profile.agent_key not in AGENTS:
        return
    issues = profile_issues(profile)
    if cursor is not None:
        issues.extend(registration_issues(cursor, profile, lock=lock and not issues))
    if issues:
        raise BaselineNotReady(profile.agent_key, issues)


def readiness(repository, agent_key):
    if agent_key not in AGENTS:
        raise LookupError('role readiness not found')
    profile = repository.load_profile(agent_key)
    issues = profile_issues(profile)
    with repository.connection_factory() as con:
        with con.cursor() as cursor:
            issues.extend(registration_issues(cursor, profile))
    spec = role_contract(agent_key)
    return {'agentKey': agent_key, 'profileVersion': profile.version, 'ready': not issues,
            'status': 'ready' if not issues else 'baseline-not-ready',
            'contractVersion': VERSION, 'contractHash': spec['contentHash'],
            'requiredNodeCount': len(spec['nodes']), 'runtimeInstalled': RUNTIME_INSTALLED,
            'issues': issues, 'authority': 'database-and-reviewed-runtime', 'readOnly': True}
