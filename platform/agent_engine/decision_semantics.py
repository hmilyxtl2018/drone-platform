"""Versioned research intent, semantic handoffs and traceable verification work.

This contract describes a research question. It does not grant permissions,
increase resource budgets, or authorize the execution of proposed follow-up work.
"""
from copy import deepcopy
import hashlib
import json

from jsonschema import Draft202012Validator, validate

VERSION = 'decision-semantics-v1'
TEXT = {'type': 'string', 'minLength': 1, 'maxLength': 2000}
ID = {'type': 'string', 'minLength': 1, 'maxLength': 120}
STRINGS = {'type': 'array', 'maxItems': 24, 'uniqueItems': True, 'items': TEXT}
BRIEF_SCHEMA = {'type': 'object', 'additionalProperties': False, 'properties': {
    'decisionToSupport': {'type': 'string', 'minLength': 1, 'maxLength': 10000}, 'decisionOwner': TEXT, 'objectsOfInterest': STRINGS,
    'alternatives': STRINGS, 'criteria': STRINGS, 'completionCriteria': STRINGS,
    'subjectRefs': {'type': 'array', 'maxItems': 24, 'uniqueItems': True, 'items': TEXT}}}
EPISTEMIC_KINDS = ['source-claim', 'analytical-inference', 'correlation', 'causal-hypothesis', 'conditional-hypothesis']
MODALITIES = ['reported', 'conditional-hypothesis']


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def enabled(profile):
    return getattr(profile, 'model_policy', {}).get('config_json', {}).get('decisionSemanticsVersion') == VERSION


def validate_brief(value):
    validate(value, BRIEF_SCHEMA)


def compile_task(ctx, perspectives):
    """Preserve literal user requirements; explicit defaults are never user facts."""
    brief = deepcopy(ctx.input.get('decisionBrief') or {})
    validate_brief(brief)
    defaults = {
        'decisionToSupport': ctx.input['question'],
        'decisionOwner': '研究报告审阅者（具体角色未指定）',
        'objectsOfInterest': [], 'alternatives': [],
        'criteria': ['引文对判断的直接支持', '适用条件与反证', '来源独立性与时效性'],
        'completionCriteria': ['每个研究问题均给出经复核的回答或明确缺口', '下一步验证任务可追溯到问题、判断与证据'],
        'subjectRefs': [],
    }
    spec = {'schemaVersion': VERSION, 'ownerRunId': ctx.run_id,
            'question': ctx.input['question'], 'scope': deepcopy(ctx.input.get('scope', {})),
            'decision': {k: deepcopy(brief.get(k, v)) for k, v in defaults.items()},
            'fieldOrigins': {k: 'user' if k in brief else 'input-question' if k == 'decisionToSupport' else 'system-default'
                             for k in defaults},
            'questions': [{'id': 'question:' + p['key'], 'perspectiveKey': p['key'],
                           'text': p.get('question') or ctx.input['question']} for p in perspectives],
            'assumptions': [{'field': k, 'value': deepcopy(v), 'origin': 'system-default'}
                            for k, v in defaults.items() if k not in brief and v],
            'missingDecisionInputs': [k for k in ('decisionOwner', 'objectsOfInterest', 'alternatives') if not brief.get(k)],
            'subjectBindings': []}
    registry = ctx.profile.model_policy.get('config_json', {}).get('identityRegistrySnapshot', {})
    entries = {e['id']: e for e in registry.get('entries', [])}
    for identity in spec['decision']['subjectRefs']:
        entry = entries.get(identity)
        spec['subjectBindings'].append({'requestedId': identity, 'status': 'resolved' if entry else 'unresolved',
            **({'canonicalId': identity, 'registryVersion': registry.get('registryVersion'),
                'registryHash': registry.get('sourceHash'), 'type': entry.get('type'), 'label': entry.get('label'),
                'identityVerified': False, 'resolutionMeaning': 'registration-reference-only'}
               if entry else {'reason': 'not-in-frozen-identity-registry'})})
    spec['contentHash'] = digest(spec)
    validate_task_spec(spec)
    return spec


def validate_task_spec(spec):
    if not isinstance(spec, dict) or spec.get('schemaVersion') != VERSION:
        raise ValueError('missing semantic task contract')
    if digest({k: v for k, v in spec.items() if k != 'contentHash'}) != spec.get('contentHash'):
        raise ValueError('semantic task contract hash mismatch')
    for field in ('ownerRunId', 'question'):
        validate(spec.get(field), BRIEF_SCHEMA['properties']['decisionToSupport'] if field == 'question' else TEXT)
    validate(spec.get('decision'), {**BRIEF_SCHEMA, 'required': list(BRIEF_SCHEMA['properties'])})
    questions = spec.get('questions', [])
    if not questions or len({q['id'] for q in questions}) != len(questions):
        raise ValueError('semantic question identities must be nonempty and unique')
    for q in questions:
        validate(q, {'type': 'object', 'required': ['id', 'perspectiveKey', 'text'],
                     'properties': {'id': ID, 'perspectiveKey': ID, 'text': TEXT}, 'additionalProperties': False})
    return spec


def task_spec(ctx):
    local = ((ctx.state.get('steps') or {}).get('define-research-question') or {}).get('taskDefinition', {}).get('decisionSpec')
    value = local or ctx.input.get('decisionSpec')
    return validate_task_spec(value) if value else None


def validate_handoff(ctx):
    if not enabled(ctx.profile) or ctx.profile.agent_key not in {'scenario-analysis-action', 'evidence-extraction-action'}:
        return
    spec = validate_task_spec(ctx.input.get('decisionSpec'))
    if (spec['ownerRunId'] != ctx.input.get('parentRunId') or spec['question'] != ctx.input.get('question')
            or spec['scope'] != ctx.input.get('scope', {})
            or {(q['perspectiveKey'], q['text']) for q in spec['questions']} !=
               {(p['key'], p.get('question') or spec['question']) for p in ctx.input.get('perspectives', [])}):
        raise ValueError('semantic handoff differs from the parent task')


def quarantine(repo, ctx, sid, stage, rows):
    if rows:
        return repo.add_artifact(ctx.run_id, 'semantic-candidate-quarantine', {
            'schemaVersion': VERSION, 'stage': stage, 'items': deepcopy(rows),
            'method': 'item-validation-no-semantic-rewrite', 'truthVerified': False}, sid)


def errors(value, schema):
    return [{'path': list(e.absolute_path), 'rule': e.validator} for e in Draft202012Validator(schema).iter_errors(value)]


def verification_work(spec, findings, gaps):
    """Turn actual unanswered questions and limitations into proposed research work."""
    coverage, tasks = [], []
    known = {f['findingId']: f for f in findings if f.get('findingId')}
    for question in spec['questions']:
        matches = [f for f in findings if question['id'] in f.get('answeredQuestionIds', []) and f.get('status') == 'candidate']
        coverage.append({'questionId': question['id'], 'perspectiveKey': question['perspectiveKey'],
                         'status': 'supported-answer' if matches else 'unanswered',
                         'findingIds': [f['findingId'] for f in matches],
                         'evidenceIds': sorted({e for f in matches for e in f.get('evidenceIds', [])}),
                         'truthVerified': False})
        reasons = [g for g in gaps if g.get('perspectiveKey') == question['perspectiveKey']]
        if not matches:
            tasks.append({'kind': 'evidence-research', 'questionId': question['id'],
                'perspectiveKey': question['perspectiveKey'], 'findingIds': [], 'evidenceIds': [],
                'target': question['text'], 'reason': reasons[-1].get('summary') or reasons[-1].get('reason') if reasons else '尚无经复核的直接回答',
                'requiredMaterial': ['直接回答“' + question['text'] + '”的公开原文、日期与适用条件', '与候选解释不一致的公开证据'],
                'ownerRole': '研究采集 Agent',
                'completionCriteria': ['保存可定位原文与来源快照', '由独立复核调用核对引文是否回答该问题；无法回答则保留缺口']})
    for identity, finding in known.items():
        if finding.get('status') != 'candidate':
            continue
        for field, kind, owner in [('unknowns', 'hypothesis-test', '研究评测 Agent'),
                                    ('conditions', 'condition-validation', '证据复核 Agent')]:
            for text in finding.get(field, []):
                tasks.append({'kind': kind, 'questionId': (finding.get('questionIds') or [''])[0],
                    'perspectiveKey': finding['perspectiveKey'], 'findingIds': [identity],
                    'evidenceIds': list(finding.get('evidenceIds', [])), 'target': text,
                    'reason': '判断的未决问题' if field == 'unknowns' else '判断的适用条件尚待核验',
                    'ownerRole': owner, 'requiredMaterial': ['关于“' + text + '”的公开评测或原文与反证'],
                    'completionCriteria': ['给出材料、适用条件及引文定位', '记录支持、不支持或仍未知的复核结果；不得将研究建议当作事实']})
    unique = {}
    for task in tasks:
        identity = 'verify:' + digest(task)[:20]
        unique[identity] = {**task, 'id': identity, 'status': 'proposed', 'executionStatus': 'not-scheduled',
            'budgetPolicy': 'existing-source-scope-and-remaining-budget', 'truthVerified': False}
    return coverage, list(unique.values())


def review_scope_preserved(review, *, allow_legacy=False):
    assessment = review.get('scopeAssessment')
    if assessment is None:
        return allow_legacy
    return (isinstance(assessment, dict) and assessment.get('attribution') == 'preserved'
            and assessment.get('absenceClaims') in {'none', 'excerpt-bounded'}
            and bool(assessment.get('reason')))


def validate_report(report):
    spec = validate_task_spec(report['taskDefinition']['decisionSpec'])
    qs = {q['id'] for q in spec['questions']}
    findings = {f['findingId']: f for f in report['findings']}
    if len(findings) != len(report['findings']):
        raise ValueError('duplicate atomic finding identity')
    evidence = {e['evidenceId'] for e in report['evidence']}
    for finding in findings.values():
        if finding.get('status') == 'candidate' and not review_scope_preserved(finding.get('supportReview', {}), allow_legacy=True):
            raise ValueError('finding exceeds reviewed source scope')
        if not set(finding.get('questionIds', [])) <= qs:
            raise ValueError('finding references unknown research question')
        answered = set(finding.get('answeredQuestionIds', []))
        reviewed = {q['questionId'] for q in finding.get('supportReview', {}).get('questionVerdicts', []) if q.get('verdict') == 'answered'}
        if not answered <= set(finding.get('questionIds', [])) or not answered <= reviewed:
            raise ValueError('question answer lacks separate semantic review')
    expected_coverage, expected_tasks = verification_work(spec, report['findings'], report.get('semanticGaps', []))
    if expected_coverage != report.get('questionCoverage') or expected_tasks != report.get('verificationTasks'):
        raise ValueError('research coverage or verification work differs from its basis')
    for task in report['verificationTasks']:
        if task['questionId'] not in qs or not set(task['findingIds']) <= findings.keys() or not set(task['evidenceIds']) <= evidence:
            raise ValueError('verification task has unresolved references')


def validate_step(ctx, step, result):
    """Reject semantically disconnected material before persisting the handoff."""
    if not enabled(ctx.profile) or result.get('status') == 'blocked':
        return
    if step.key == 'define-research-question':
        validate_task_spec(result['taskDefinition']['decisionSpec'])
        return
    if not result.get('perspectiveFindings'):
        return
    spec = task_spec(ctx)
    if not spec or result.get('decisionSpecHash') != spec['contentHash']:
        raise ValueError('judgment handoff lacks the frozen task hash')
    qs = {q['id']: q['perspectiveKey'] for q in spec['questions']}
    source = ((ctx.state.get('steps') or {}).get('fuse-evidence-and-conflicts') or {}).get('evidence')
    source = source or (ctx.state.get('_actionMaterial') or {}).get('evidence') or []
    evidence = {e['evidenceId']: e for e in source}
    from .decision_reasoning import valid_modality, RELATION
    seen = set()
    for finding in result['perspectiveFindings']:
        identity = finding.get('findingId')
        if not identity or identity in seen or not finding.get('questionIds'):
            raise ValueError('atomic judgment handoff has missing or duplicate identities')
        seen.add(identity)
        if any(qs.get(q) != finding.get('perspectiveKey') for q in finding['questionIds']):
            raise ValueError('atomic judgment belongs to a different question or perspective')
        if finding.get('status') == 'insufficient-evidence':
            if finding.get('supportingClaims') or finding.get('relations'):
                raise ValueError('abstention contains substantive claims')
            continue
        refs = finding.get('supportingClaims', [])
        if (not refs or not set(refs) <= evidence.keys() or not valid_modality(finding)
                or finding.get('supportReview', {}).get('verdict') != 'supported'):
            raise ValueError('atomic judgment lacks evidence, modality or separate review')
        entities = {e['id'] for e in finding['entities']}
        for relation in finding['relations']:
            validate({k: v for k, v in relation.items() if k in RELATION['properties']}, RELATION)
            if (not valid_modality(relation) or relation['subject'] not in entities or relation['object'] not in entities
                    or not set(relation['evidenceIds']) <= set(refs) or relation['supportReview']['verdict'] != 'supported'):
                raise ValueError('atomic relation handoff has unresolved support or endpoints')


def step_contract(key, previous):
    """Database-published types for the existing DAG; references checked at runtime."""
    schema = deepcopy(previous or {})
    schema['type'] = 'object'
    schema.setdefault('properties', {})
    schema['properties'].update({'stepKey': ID, 'gaps': {'type': 'array', 'items': {'type': 'object'}},
                                'decisionSpecHash': {'type': 'string', 'pattern': '^[a-f0-9]{64}$'}})
    arrays = {'perspectives', 'hypotheses', 'sourcePlan', 'facts', 'evidence', 'systemFindings',
              'conflicts', 'perspectiveFindings', 'claims'}
    objects = {'taskDefinition', 'sourceBundle', 'redTeam', 'judgmentReport', 'toolResults'}
    for field in schema.get('required', []):
        if field in arrays:
            schema['properties'][field] = {'type': 'array', 'maxItems': 1000, 'items': {'type': 'object'}}
        elif field in objects:
            schema['properties'][field] = {'type': 'object'}
    if key in {'analyze-by-perspective', 'review-perspective-evidence', 'structure-scenario-judgment'}:
        schema['properties']['perspectiveFindings'] = {'type': 'array', 'maxItems': 32, 'items': {
            'type': 'object', 'required': ['findingId', 'perspectiveKey', 'questionIds', 'statement', 'status',
                                         'supportingClaims', 'supportingEvidence', 'entities', 'relations', 'supportReview'],
            'properties': {'findingId': ID, 'perspectiveKey': ID, 'questionIds': {'type': 'array', 'minItems': 1, 'items': ID},
                'statement': TEXT, 'status': {'enum': ['candidate', 'insufficient-evidence']},
                'supportingClaims': STRINGS, 'supportingEvidence': {'type': 'array', 'items': {'type': 'object'}},
                'entities': {'type': 'array', 'items': {'type': 'object'}},
                'relations': {'type': 'array', 'items': {'type': 'object'}}, 'supportReview': {'type': 'object'}}}}
        schema['required'] = sorted(set(schema.get('required', [])) | {'perspectiveFindings', 'decisionSpecHash'})
    if key == 'define-research-question':
        schema['properties']['taskDefinition'] = {'type': 'object', 'required': ['question', 'scope', 'decisionSpec'],
            'properties': {'question': {'type': 'string', 'minLength': 1, 'maxLength': 10000}, 'scope': {'type': 'object'},
                'decisionSpec': {'type': 'object', 'required': ['schemaVersion', 'ownerRunId', 'question', 'scope',
                    'decision', 'questions', 'subjectBindings', 'fieldOrigins', 'contentHash']}}}
    if key == 'plan-source-coverage':
        schema['properties']['sourcePlan']['items'] = {'type': 'object', 'required': ['perspectiveKey', 'questionIds',
            'decisionSpecHash', 'queryVariants', 'status', 'evidenceRequirements'],
            'properties': {'perspectiveKey': ID, 'questionIds': {'type': 'array', 'minItems': 1, 'items': ID},
                'queryVariants': STRINGS, 'status': {'enum': ['planned', 'focused-out-of-scope', 'budget-unfunded']},
                'decisionSpecHash': schema['properties']['decisionSpecHash'], 'evidenceRequirements': {'type': 'array', 'items': {'type': 'object'}}}}
    return schema
