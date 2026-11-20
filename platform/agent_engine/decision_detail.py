"""Read-only details grounded in saved queries and paired trial bindings."""
from copy import deepcopy

from .task_intent import digest
from .research_candidate import frozen_case

MISSING_COVERAGE = ['independent sufficient-evidence case', 'independent evidence-gap case',
                    'conflicting-evidence case', 'out-of-scope case']


def gap_detail(report, selected, perspective):
    if selected.get('type') not in {'evidence-gap', 'EvidenceGap', 'unknown'}:
        return None
    gaps = report.get('evidenceGaps') or report.get('gaps') or []
    gap = next((g for g in gaps if isinstance(g, dict) and
        (g.get('id') == selected.get('id') or (perspective and g.get('perspectiveKey') == perspective))), {})
    attempts = deepcopy(gap.get('attempts')) if isinstance(gap.get('attempts'), list) else []
    missing = []
    if not attempts:
        missing.append('逐次检索记录')
    if not gap.get('exclusionReason'):
        missing.append('来源排除理由')
    if not gap.get('exclusionRule'):
        missing.append('来源排除规则')
    if not gap:
        missing.append('与该节点精确对应的缺口记录')
    return {'summary': gap.get('summary') or selected.get('label') or '',
        'reason': gap.get('reason') or selected.get('reason'), 'perspectiveKey': perspective,
        'attempts': attempts, 'attemptedQueries': deepcopy(gap.get('attemptedQueries') or []),
        'attemptedUrls': deepcopy(gap.get('attemptedUrls') or []),
        'exclusionReason': deepcopy(gap.get('exclusionReason')), 'exclusionRule': deepcopy(gap.get('exclusionRule')),
        'missingRecords': missing, 'neededEvidence': gap.get('neededEvidence'),
        'nextAction': gap.get('nextAction'),
        'notice': '检索结果仅表示检索时发现的资料，不自动构成支撑判断的引文。'}


def _rule(entry, expected):
    if entry.get('kind') == 'finding':
        if expected == 'evidence-gap': return '状态为 insufficient-evidence 的字段应分类为证据缺口'
        if expected == 'judgment': return '有判断陈述且未标记证据不足的字段分类为判断'
        return '缺少可判别判断陈述的字段保留未知'
    if entry.get('kind') == 'gap':
        return '有明确排除规则、排除理由或规则引用时记为 recorded，否则记为 missing'
    if entry.get('kind') == 'redTeam':
        return '与证据不足视角的弃权陈述完全相同的文本分类为 nonclaim'
    return '按冻结字段契约比较；未知条目不推断规则'


def comparison_details(cycle, trial, roles, source_output):
    """Validate stored cases/inputs/outputs; never infer answers from errors."""
    result = {'scope': 'report-quality-on-frozen-fragments', 'caseHash': trial.get('case_hash'),
        'sourceRunId': cycle.get('source_run_id'), 'sourceOutputHash': cycle.get('source_output_hash'),
        'rows': [], 'missingCoverage': list(MISSING_COVERAGE), 'truthVerified': False,
        'publicationEligible': False, 'available': False, 'reason': None}
    case = trial.get('case_json')
    if not isinstance(case, dict) or digest(case) != trial.get('case_hash'):
        result['reason'] = '冻结比较用例缺失或内容摘要不匹配'
        return result
    model_input = case.get('modelInput') or {}
    if source_output is None or digest(source_output) != cycle.get('source_output_hash'):
        result['reason'] = '冻结源报告缺失或内容摘要不匹配'
        return result
    try:
        regenerated = frozen_case(cycle['source_run_id'], source_output, cycle['source_output_hash'])
    except (ValueError, KeyError, TypeError):
        result['reason'] = '源报告不能解析为该固定片段契约'
        return result
    if model_input != regenerated['modelInput'] or case.get('expected') != regenerated['expected']:
        result['reason'] = '用例片段或预期分类与冻结源报告不匹配'
        return result
    answers = {}
    hashes = {}
    for role in ('baseline', 'candidate'):
        row = roles.get(role)
        if not isinstance(row, dict):
            result['reason'] = '绑定的基线或候选运行尚未保存'
            return result
        data, output = row.get('input_json') or {}, row.get('output_json') or {}
        if digest(data) != row.get('input_hash') or data.get('caseHash') != trial['case_hash'] or data.get('modelInput') != model_input:
            result['reason'] = '基线或候选输入与冻结用例绑定不匹配'
            return result
        if (row.get('status') != 'completed' or output.get('caseHash') != trial['case_hash'] or
            output.get('sourceRunId') != cycle['source_run_id'] or
            output.get('scope') != result['scope'] or
            output.get('targetProfileHash') != data.get('targetProfileHash')):
            result['reason'] = '基线或候选输出尚未完成，或输出绑定不匹配'
            return result
        reviews = output.get('reviews')
        if not isinstance(reviews, list) or any(not isinstance(v, dict) for v in reviews):
            result['reason'] = '基线或候选逐字段输出缺失'
            return result
        by_ref = {v.get('refId'): v.get('classification') for v in reviews}
        if len(by_ref) != len(reviews) or set(by_ref) - set(case['expected']):
            result['reason'] = '逐字段输出含重复或不属于该用例的引用'
            return result
        answers[role], hashes[role] = by_ref, digest(output)
    for entry in model_input.get('entries', []):
        ref = entry['refId']
        expected = case['expected'].get(ref)
        before, after = answers['baseline'].get(ref), answers['candidate'].get(ref)
        result['rows'].append({'refId': ref, 'kind': entry.get('kind'), 'sourceValue': deepcopy(entry.get('value')),
            'expected': expected, 'baseline': before, 'candidate': after,
            'baselineCorrect': before == expected if before is not None else None,
            'candidateCorrect': after == expected if after is not None else None,
            'rule': _rule(entry, expected), 'baselineRunId': roles['baseline']['run_id'],
            'candidateRunId': roles['candidate']['run_id'], 'baselineOutputHash': hashes['baseline'],
            'candidateOutputHash': hashes['candidate']})
    result.update(available=True, reason=None, bindingStatus='verified-saved-case-and-run-bindings',
        limitations=deepcopy(case.get('limitations') or []))
    # This evaluator has no sealed regression dataset; a case's self-declared
    # coverageComplete cannot turn a smoke pair into complete validation.
    return result
