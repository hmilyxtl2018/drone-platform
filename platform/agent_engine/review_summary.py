"""Deterministic human-readable projection of saved research, never an approval.

The shared budget ledger is authoritative. Unknown evidence, review and
publication states stay unknown; completion cannot promote any of them.
"""
from copy import deepcopy

from .context_contract import content_hash
from .repository import as_json
from .ontology_explorer import evidence_index, explorer_graph, PREDICATES
from .ontology_quality import enrich_quality, source_coverage

VERSION = 'review-summary.v1'
TERMINAL = {'completed', 'completed-with-gaps', 'requires-human-review', 'blocked', 'failed', 'cancelled'}
EXECUTION_LABELS = {'completed': '研究完成', 'completed-with-gaps': '研究完成，有缺口',
    'requires-human-review': '运行等待人工处理', 'blocked': '运行受阻', 'failed': '运行失败',
    'cancelled': '运行已取消', 'running': '研究中', 'queued': '排队中', 'created': '已创建',
    'pending': '等待执行', 'paused': '运行已暂停'}
GAP_TYPES = {'unknown', 'evidence-gap', 'EvidenceGap'}


def _dict(value):
    value = as_json(value)
    return value if isinstance(value, dict) else {}


def _rows(value):
    return [r for r in value if isinstance(r, dict)] if isinstance(value, list) else []


def _count(value):
    return value if type(value) is int and value >= 0 else None


def _state(status, labels, unknown):
    return {'status': status or 'unknown', 'label': labels.get(status, unknown)}


def stage_labels(run):
    data = _dict(run.get('input_json'))
    return {'human-review': '发布准备（运行外审阅）'} if data.get('automationMode') in {'expert-draft', 'unattended-draft'} else {}


def evidence_dimensions(assertion, evidence, report):
    """Independent predicates; no total confidence/credibility ordering exists."""
    ids = [str(k) for k in assertion.get('evidenceIds', [])]
    present = [evidence[k] for k in ids if k in evidence]
    located = any(row.get('quote') and row.get('sourceArtifactId') and row.get('sourceArtifactHash') for row in present)
    review = assertion.get('supportReview') or {}
    trace = assertion.get('auditTrace') or {}
    verdict = review.get('verdict')
    supported = (verdict == 'supported') if trace.get('status') == 'artifact-backed' and verdict in {'supported', 'unsupported', 'contradicted'} else None
    # IDs and status fields emitted by a model are not authority records.
    # These dimensions stay unknown until a repository-backed binding exists.
    independent = fact = reviewed = published = None
    return {'hasCitation': located if present else False, 'citationSupported': supported,
            'independentSources': independent, 'factVerified': fact,
            'humanReviewed': reviewed, 'published': published}


def _items(report, graph):
    nodes = {n['id']: n for n in graph.get('nodes', [])}
    edges = {e['id']: e for e in graph.get('edges', [])}
    assertions = _rows(graph.get('assertions'))
    result = []
    evidence = evidence_index(report)
    for assertion in assertions:
        identity = assertion['id']
        subject = nodes.get(assertion.get('subject'), {}).get('label') or assertion.get('subject', '')
        obj = nodes.get(assertion.get('object'), {}).get('label') or assertion.get('object', '')
        statement = assertion.get('statement') or f"{subject} {PREDICATES.get(assertion.get('predicate'), assertion.get('predicate', ''))} {obj}"
        edge_id = 'domain:' + identity
        ref = {'kind': 'edge', 'id': edge_id} if edge_id in edges else None
        finding = next((f for f in _rows(report.get('findings')) if
            (f.get('findingId') == assertion['findingId'] if assertion.get('findingId') else f.get('perspectiveKey') == assertion.get('perspectiveKey'))), {})
        conditions = assertion.get('conditions') or []
        condition_scope = assertion.get('conditionScope')
        if condition_scope == 'perspective' or (not condition_scope and conditions == finding.get('conditions')):
            conditions, condition_scope = [], 'perspective'
        result.append({'id': identity, 'kind': 'assertion', 'title': statement, 'text': statement,
            'perspectiveKey': assertion.get('perspectiveKey'), 'evidenceIds': list(assertion.get('evidenceIds') or []),
            'objectRef': ref, 'reason': (assertion.get('supportReview') or {}).get('reason'),
            'conditions': deepcopy(conditions), 'perspectiveConditions': deepcopy(finding.get('conditions') or []),
            'conditionScope': condition_scope or ('relation' if conditions else 'not-recorded'), 'priority': 'normal',
            'evidenceDimensions': evidence_dimensions(assertion, evidence, report)})
    gaps = _rows(report.get('evidenceGaps') or report.get('gaps'))
    for index, gap in enumerate(gaps):
        key = gap.get('perspectiveKey')
        gap_nodes = sorted(nodes.values(), key=lambda n: (n.get('id') != 'gap:' + str(key), n.get('type') not in {'evidence-gap', 'EvidenceGap'}))
        matching = next((n for n in gap_nodes if n.get('type') in GAP_TYPES and
            (n.get('perspectiveKey') == key or (key and key in (n.get('perspectiveKeys') or [])) or
             (key and n['id'] == 'gap:' + key))), None)
        text = gap.get('summary') or gap.get('neededEvidence') or gap.get('reason') or '已记录证据缺口'
        result.append({'id': gap.get('id') or (matching or {}).get('id') or 'gap:' + str(index),
            'kind': 'gap', 'title': gap.get('perspectiveName') or text, 'text': text,
            'perspectiveKey': key, 'evidenceIds': list(gap.get('evidenceIds') or []),
            'objectRef': {'kind': 'node', 'id': matching['id']} if matching else None,
            'reason': gap.get('reason'), 'conditions': [], 'priority': 'attention',
            'neededEvidence': gap.get('neededEvidence'), 'nextAction': gap.get('nextAction')})
    # UAV/legacy reports can retain a gap only in their source graph.
    referenced = {i['objectRef']['id'] for i in result if i.get('objectRef')}
    for node in nodes.values():
        if node.get('type') in {'evidence-gap', 'EvidenceGap'} and node['id'] not in referenced:
            result.append({'id': node['id'], 'kind': 'gap', 'title': node.get('label') or '证据缺口',
                'text': node.get('label') or '', 'perspectiveKey': node.get('perspectiveKey'),
                'evidenceIds': list(node.get('evidenceIds') or []), 'objectRef': {'kind': 'node', 'id': node['id']},
                'reason': node.get('reason'), 'conditions': [], 'priority': 'attention'})
    return result


def build_review_summary(run, collaboration, report=None):
    """Pure projection function. The caller supplies saved report and ledger."""
    output, data = _dict(run.get('output_json')), _dict(run.get('input_json'))
    report = deepcopy(report if report is not None else output)
    canonical = report.get('ontologyGraph') or output.get('ontology') or (output.get('judgmentReport') or {}).get('ontology') or {}
    report.update(runId=run['run_id'], ontologyGraph=canonical)
    graph = explorer_graph(report)
    available = bool(output) and bool(canonical or report.get('findings') or report.get('claims') or report.get('evidence') or report.get('summary'))
    evidence = list(evidence_index(report).values())
    coverage = source_coverage(evidence)
    items = _items(report, graph) if available else []
    assertions = _rows(graph.get('assertions'))
    counts = {'assertions': len(assertions), 'gaps': sum(i['kind'] == 'gap' for i in items),
        'perspectives': len(graph.get('perspectives') or report.get('perspectives') or []),
        'evidenceExcerpts': len(evidence), 'sourceGroups': coverage['sourceGroupCount'],
        'structuralEdges': sum(e.get('kind') != 'assertion' for e in graph.get('edges', []))} if available else {k: None for k in ('assertions', 'gaps', 'perspectives', 'evidenceExcerpts', 'sourceGroups', 'structuralEdges')}
    goal = data.get('question') or (data.get('commonSettings') or {}).get('taskGoal') or data.get('taskGoal') or ''
    status = run.get('status') or 'unknown'
    labels = stage_labels(run)
    baseline = collaboration.get('baseline') or {}
    current = run.get('current_step_key')
    node = next((n for n in baseline.get('nodes', []) if n.get('id') == current), {})
    children = (collaboration.get('completion') or {}).get('children') or 'unknown'
    budget = collaboration.get('budget')
    shared = None
    if isinstance(budget, dict):
        used, limits = budget.get('used') or {}, budget.get('limits') or {}
        shared = {'scope': 'shared-root-budget', 'rootRunId': budget.get('root_run_id'),
            'modelCalls': _count(used.get('model_calls')), 'toolCalls': _count(used.get('tool_calls')),
            'modelLimit': _count(limits.get('model_calls')), 'toolLimit': _count(limits.get('tool_calls')),
            'label': '共享研究调用'}
    source_root = collaboration.get('root') or {}
    # source_coverage groups sources, but does not verify their independence.
    verified_count, independent_known = None, False
    citation_status = {'supported': 0, 'contradicted': 0, 'unreviewed': 0}
    recorded = 0
    for assertion in assertions:
        verdict = (assertion.get('supportReview') or {}).get('verdict')
        if verdict in {'supported', 'contradicted', 'unsupported'}:
            recorded += 1
            citation_status['supported' if verdict == 'supported' else 'contradicted'] += 1
        else:
            citation_status['unreviewed'] += 1
    verified_support = sum((a.get('auditTrace') or {}).get('status') == 'artifact-backed' for a in assertions if (a.get('supportReview') or {}).get('verdict') == 'supported')
    citation = {'status': 'recorded' if recorded else 'unknown',
        'label': ('已记录引文支持复核' if recorded else '引文支持复核未记录'),
        **(citation_status if assertions else {k: None for k in citation_status}),
        'artifactBackedSupported': verified_support if assertions else None,
        'reviewIndependence': 'not-established', 'notice': '引文支持复核不等于事实核验；复核独立性未建立。'}
    fact_status = 'unverified'
    human_declared = report.get('reviewStatus') or 'unknown'
    human_status = 'not-requested' if human_declared == 'not-requested' else 'unknown'
    publication_declared = report.get('publicationStatus') or 'unknown'
    publication = 'not-published' if publication_declared == 'not-published' else 'unknown'
    limitations = []
    if not independent_known: limitations.append('来源独立性尚未建立')
    if fact_status != 'verified': limitations.append('事实未经核验')
    if counts['assertions'] == 0 and available: limitations.append('当前报告尚未形成可展示的本体断言')
    if not shared: limitations.append('共享预算账本未记录')
    if recorded and verified_support != citation_status['supported']: limitations.append('部分引文支持复核缺少可校验产物记录')
    # Evidence coverage and method/security checks have separate counters.
    quality_reasons = sorted({str(g.get('reason')) for g in _rows(output.get('capabilityGaps')) if g.get('reason')})
    quality_labels = {
        'source-content-prompt-injection-signal': '来源正文存在指令形态文本信号',
        'claim-source-prompt-injection-signal': '引文来源存在指令形态文本信号',
        'source-numeric-unit-review-required': '来源数字与单位尚待校验',
        'claim-source-numeric-unit-review-required': '引文来源数字与单位尚待校验',
        'insufficient-samples': '时间序列样本不足',
        'scenario-trigger-indicators-missing': '场景触发指标缺失',
        'scenario-counterfactuals-missing': '场景反事实缺失',
        'quality-gate:scenario-dimensions-complete': '场景维度覆盖不完整',
        'quality-gate:indicators-present': '缺少可观察指标',
        'quality-gate:relation-support-reviewed': '关系支持复核未通过'}
    quality_issues = [{'reason': r, 'label': quality_labels.get(r, r)} for r in quality_reasons]
    if quality_issues:
        limitations.append('质量检查仍有 ' + str(len(quality_issues)) + ' 类未解决事项')
    return {'schemaVersion': VERSION,
        'identity': {'runId': run['run_id'], 'agentKey': run.get('agent_key'),
            'agentName': source_root.get('display_name') or run.get('agent_key'),
            'taskTitle': goal[:64] + ('…' if len(goal) > 64 else ''), 'taskGoal': goal,
            'agentVersion': run.get('agent_version'),
            'reportTime': report.get('generatedAt') or report.get('savedAt') or run.get('completed_at'),
            'reportHash': content_hash(output) if available else None,
            'sourceGraphHash': graph.get('sourceGraphHash') if canonical else None,
            'projectionVersion': VERSION},
        'scope': {'kind': 'task', 'perspectiveKey': None, 'label': '整个任务'},
        'totalCounts': counts, 'scopedCounts': None,
        'qualityIssues': quality_issues,
        'execution': {'status': status, 'label': EXECUTION_LABELS.get(status, '运行状态未记录'),
            'terminal': status in TERMINAL, 'childrenStatus': children,
            'childrenLabel': {'completed': '子任务已完成', 'none': '无协作子任务', 'attention': '子任务需要关注', 'running': '子任务执行中'}.get(children, '子任务状态未记录'),
            'currentStepKey': current, 'currentStepLabel': labels.get(current) or node.get('name') or current,
            'stageLabels': labels, 'reportAvailable': available,
            'defaultTab': 'results' if available and status in TERMINAL else 'execute'},
        'evidence': {'excerptCount': counts['evidenceExcerpts'], 'sourceGroupCount': counts['sourceGroups'],
            'independence': {'status': 'verified' if independent_known else 'unknown',
                'label': '已记录独立来源核验' if independent_known else '来源独立性未建立',
                'verifiedCount': verified_count if independent_known else None}, 'citationReview': citation},
        'factVerification': _state(fact_status, {'unverified': '事实未核验'}, '事实核验状态未记录') | {'source': 'no-authority-backed-verification', 'legacyDeclaredStatus': report.get('truthStatus')},
        'humanReview': _state(human_status, {'not-requested': '未发起人工审阅', 'pending': '等待人工审阅', 'approved': '报告记录为已审阅', 'reviewed': '报告记录为已审阅', 'rejected': '报告记录为有异议'}, '人工审阅未记录') |
            {'writeAvailable': False, 'unavailableReason': '人工审阅记录功能尚未部署', 'reviewedObjects': None, 'unresolvedObjections': None, 'reviewRevision': 0, 'legacyDeclaredStatus': human_declared},
        'publication': {'report': _state(publication, {'not-published': '报告未发布'}, '报告发布未记录') | {'legacyDeclaredStatus': publication_declared},
            'relations': {'status': 'unknown', 'label': '关系发布未记录'},
            'profile': {'status': 'unknown', 'label': 'Profile 发布未记录'}},
        'items': items,
        'usage': {'shared': shared, 'direct': {'scope': 'root-direct', 'modelCalls': _count(run.get('model_call_count')),
            'toolCalls': _count(run.get('tool_call_count')), 'label': '根任务直接调用'}, 'improvement': None,
            'countingPolicy': '共享账本已包含子任务；不可重复相加'},
        'actions': [{'kind': 'read-report', 'label': '查看结果', 'enabled': available,
            'reason': None if available else '尚无已保存报告', 'createsRun': False, 'usesModel': False, 'budgetScope': None}],
        'limitations': limitations}


class ReviewSummaryService:
    def __init__(self, engine, queue):
        from .research_governance import ResearchGovernance
        self.engine = engine
        self.governance = ResearchGovernance(engine, queue)

    def report(self, run_id):
        run = self.engine.repo.get_run(run_id)
        if not run:
            raise LookupError('Run not found')
        output = _dict(run.get('output_json'))
        report = {**deepcopy(output), 'runId': run_id,
            'ontologyGraph': deepcopy(output.get('ontology') or (output.get('judgmentReport') or {}).get('ontology') or {})}
        report = enrich_quality(report, self.engine.repo)
        return run, report

    def read(self, run_id, actor=None):
        run, report = self.report(run_id)
        return self.project(run, report, actor)

    def project(self, run, report, actor=None):
        result = build_review_summary(run, self.governance.collaboration(run['run_id']), report)
        from .goal_contract import GoalService
        result['goalAssessment'] = GoalService(self.engine.repo.connection_factory).run(run['run_id'])
        from .report_review import ReportReviewService
        factory = getattr(self.engine, 'base_connection_factory', self.engine.repo.connection_factory)
        overlay = ReportReviewService(factory, self.engine.repo).overlay(run, result['identity'], actor)
        result['humanReview'].update(overlay)
        result['actions'].append({'kind': 'review-opinion', 'label': '留下人工意见',
            'enabled': overlay['writeAvailable'], 'reason': overlay['unavailableReason'],
            'createsRun': False, 'usesModel': False, 'budgetScope': None,
            'objectScope': 'exact-report-version', 'authorizationScope': 'human-reviewer-or-admin'})
        if overlay['reviewRevision']:
            result['limitations'].append('已有人工意见；正式人工审核与事实核验未由此建立')
        return result
