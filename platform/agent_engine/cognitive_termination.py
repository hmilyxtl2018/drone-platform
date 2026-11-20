"""Evidence-preserving drafts for bounded cognitive research interruptions.

Only exhaustion of the shared budget or deadline is recoverable here.  The
caller classifies the original exception.  All frozen-input, schema, source and
reference validation failures propagate, including during this finalization.
No model or tool is called and this module does not persist or publish a draft.
"""
from __future__ import annotations

from copy import deepcopy

from .cognitive_contracts import (
    build_explanation_graph, load_verified_artifact, partial_gap, validate_product,
)
from .cognitive_identity import APPLICATION_KEY
from .cognitive_runtime import (
    PRODUCT_BY_AGENT, _base, _graph, _initialize, _latest_product, _validate,
)


REASONS = frozenset({'budget-exhausted', 'deadline-exceeded'})
_PROFESSIONAL_TYPES = frozenset(PRODUCT_BY_AGENT.values()) - {'CognitiveResearchReport'}


def _merge(left, right, field):
    """Keep exact records; an identity collision is a hard contract failure."""
    result, seen = [], {}
    for value in list(left) + list(right):
        identity = value[field]
        if identity in seen:
            if seen[identity] != value:
                raise ValueError('cognitive termination has conflicting ' + field)
            continue
        seen[identity] = value
        result.append(deepcopy(value))
    return result


def _unknown_fields(ctx, product, reason):
    """Supply required fields only for a product that has not been produced."""
    kind = product['productType']
    limitation = '研究因 ' + reason + ' 结束；未执行部分的结果未知，尚未形成完整结论。'
    graph = _graph(product['claims'], ctx.state.get('_cognitiveLabels', {}))
    if kind == 'CognitiveResearchReport':
        product.update(
            summary=limitation,
            assessmentArtifactRefs=[], ontologyView=graph,
            decisionSupport={'researchImplications': [],
                             'unresolvedQuestions': [limitation],
                             'nextResearchActions': []})
    elif kind == 'SituationAssessment':
        product['findings'] = []
    elif kind == 'ConstraintAssessment':
        product.update(ruleFindings=[], researchConstraints=[limitation])
    elif kind == 'ResearchOptionComparison':
        # This is an explicit methodological criterion, not an invented option
        # or a professional finding. No alternative is ranked or recommended.
        product.update(comparisonState='insufficient-evidence', criteria=[{
            'criterionId': 'termination-evidence-sufficiency',
            'label': '可追溯证据是否充分',
            'definition': '只有在相同评价条件下具备可定位证据时才可比较；终止时未完成评价。'}],
            options=[])
    elif kind == 'ConsistencyReport':
        product.update(overallResult='insufficient-coverage',
            coverage={'performedCheckIds': [], 'excludedChecks': [limitation],
                      'entityCount': len(graph['entities']), 'relationCount': len(graph['relations'])},
            factualVerification='not-performed')
    else:
        raise ValueError('unsupported cognitive termination product')


def summarize(ctx, repo, reason):
    """Return a validated proposed product without executing further research.

    ``reason`` must be ``budget-exhausted`` or ``deadline-exceeded``. A deadline
    uses the existing Gap kind ``other`` with an explicit machine-readable
    reason in its description, since v1 has no deadline-specific Gap kind.
    Actual checks and source quotes survive; no missing check becomes a pass.
    """
    if reason not in REASONS:
        raise ValueError('cognitive termination only handles budget or deadline exhaustion')
    if ctx.profile.agent_key not in PRODUCT_BY_AGENT:
        raise ValueError('unsupported cognitive termination Agent')
    _initialize(ctx, repo)
    product = _latest_product(ctx)
    if product is None:
        product = _base(ctx, repo)
        _unknown_fields(ctx, product, reason)
    else:
        # Validate the untouched snapshot first. Filling defaults or projecting
        # a graph must never repair/hide an existing hard contract error.
        product = _validate(ctx, repo, product)

    product['claims'] = _merge(product['claims'], ctx.state.get('_cognitiveClaims', []), 'claimId')
    product['gaps'] = _merge(product['gaps'], ctx.state.get('_cognitiveGaps', []), 'gapId')
    if ctx.profile.agent_key == APPLICATION_KEY:
        # Preserve all completed professional receipts and their evidence. A
        # partial draft does not imply that an unexecuted branch was completed.
        for branch in ctx.state.get('_cognitiveProducts', {}).values():
            _validate(ctx, repo, branch)
            for name, identity in (('claims', 'claimId'), ('gaps', 'gapId'),
                                   ('conflicts', 'conflictId'), ('checks', 'checkId')):
                product[name] = _merge(product[name], branch.get(name, []), identity)
        refs = list(product['assessmentArtifactRefs'])
        for value in ctx.state.get('steps', {}).values():
            if value.get('product', {}).get('productType') in _PROFESSIONAL_TYPES and value.get('artifactRef'):
                if value['artifactRef'] not in refs:
                    refs.append(deepcopy(value['artifactRef']))
        product['assessmentArtifactRefs'] = refs
        for ref in refs:
            load_verified_artifact(ref, ctx.state['_cognitiveAuthority']['artifactRefs'], repo)
        product['ontologyView'] = build_explanation_graph(product)
    elif product['productType'] == 'ResearchOptionComparison':
        product['comparisonState'] = 'insufficient-evidence'
    elif product['productType'] == 'ConsistencyReport':
        # Known failed checks stay failed. The unfinished workflow cannot grant
        # a complete no-conflict conclusion from a subset of checks.
        if product['overallResult'] != 'contradictions-detected':
            product['overallResult'] = 'insufficient-coverage'
        excluded = product['coverage']['excludedChecks']
        limitation = '研究因 ' + reason + ' 终止，后续检查未执行。'
        if limitation not in excluded:
            excluded.append(limitation)

    description = reason + '：研究预算已耗尽，保留终止前的产物和依据。' if reason == 'budget-exhausted' else \
                  reason + '：研究运行期限已到，保留终止前的产物和依据。'
    gap = partial_gap('budget-exhausted' if reason == 'budget-exhausted' else 'other', description,
        '未执行分支和检查保持未知；本产物仅为带缺口的研究草稿。',
        '如需继续研究，应在新的获准预算和期限内从已保存的证据版本接续研究。')
    product['gaps'] = _merge(product['gaps'], [gap], 'gapId')
    provenance = product['provenance']
    if (provenance['runId'] != ctx.run_id or provenance['agentKey'] != ctx.profile.agent_key
            or provenance['profileVersion'] != ctx.profile.version):
        raise PermissionError('termination product does not belong to this Agent Run and version')
    # Passing a repository enables exact hash, owner, locator and quote checks.
    # Do not catch these errors or replace this result with a successful draft.
    product = validate_product(product, expected_context=ctx.state['_cognitiveContext'],
        allowed_refs=ctx.state['_cognitiveAuthority']['artifactRefs'], repository=repo)
    return _validate(ctx, repo, product)
