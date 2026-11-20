"""Bounded offline analysis adapters; every call uses ToolGateway's audit.

Provided bodies/claims remain unverified input. Exact text matching proves
quotation fidelity only, never the truth of the statement.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import re

from jsonschema import validate

from future_battlefield_algorithms import (
    body_hash, deduplicate_sources, detect_change_points, normalize_events, validate_citation,
    detect_prompt_injection, detect_numeric_unit_risks, detect_temporal_conflicts,
    detect_over_inference, detect_source_relations,
)

VERSION = '1.1.0'
SUPPORTED_VERSIONS = frozenset({'1.1.0', '1.2.0', '1.2.1', '1.2.2', '1.2.3', '1.2.4', '1.2.5', '1.2.6'})
QUALITY_RULESET_VERSION = 'p2.0'
TEXT = {'type': 'string', 'maxLength': 4000}
STRINGS = {'type': 'array', 'maxItems': 100, 'items': TEXT}
BODY = {'type': 'string', 'minLength': 1, 'maxLength': 250000}
SOURCE = {'type': 'object', 'required': ['sourceUri', 'body'], 'properties': {
    'sourceUri': {'type': 'string', 'minLength': 1, 'maxLength': 2048}, 'body': BODY,
    'retrievedAt': TEXT, 'sourceRole': TEXT, 'contentHash': {'type': 'string', 'pattern': '^[0-9a-f]{64}$'},
}, 'additionalProperties': True}
CLAIM = {'type': 'object', 'required': ['quote', 'body'], 'properties': {
    'claimId': TEXT, 'sourceUri': TEXT, 'retrievedAt': TEXT, 'quote': TEXT, 'body': BODY,
    'locator': {'type': 'object'}, 'contentHash': {'type': 'string', 'pattern': '^[0-9a-f]{64}$'},
}, 'additionalProperties': True}
JUDGMENT = {'type': 'object', 'required': ['statement', 'supportingClaims', 'conditions', 'counterArguments', 'unknowns'],
    'properties': {'statement': TEXT, 'supportingClaims': STRINGS, 'conditions': STRINGS,
                   'counterArguments': STRINGS, 'unknowns': STRINGS,
                   'scenarioCard': {'type': 'object'}}, 'additionalProperties': False}
ARGUMENT_SCHEMAS = {
    'analysis.source_deduplicate': {'type': 'object', 'required': ['sources'], 'properties': {
        'sources': {'type': 'array', 'maxItems': 100, 'items': SOURCE}}, 'additionalProperties': False},
    'analysis.citation_validate': {'type': 'object', 'required': ['claims'], 'properties': {
        'claims': {'type': 'array', 'maxItems': 100, 'items': CLAIM}}, 'additionalProperties': False},
    'analysis.event_normalize': {'type': 'object', 'required': ['events'], 'properties': {
        'events': {'type': 'array', 'maxItems': 1000, 'items': {'type': 'object', 'properties': {
            'date': TEXT, 'observedAt': TEXT, 'publishedAt': TEXT}, 'additionalProperties': True}}}, 'additionalProperties': False},
    'analysis.change_point': {'type': 'object', 'required': ['values'], 'properties': {
        'values': {'type': 'array', 'maxItems': 10000, 'items': {'type': 'number'}},
        'threshold': {'type': 'number', 'exclusiveMinimum': 0}}, 'additionalProperties': False},
    'analysis.scenario_structure': {'type': 'object', 'required': ['judgments'], 'properties': {
        'judgments': {'type': 'array', 'maxItems': 100, 'items': JUDGMENT}}, 'additionalProperties': False},
    'analysis.claim_audit': {'type': 'object', 'required': ['claims', 'judgments'], 'properties': {
        'claims': {'type': 'array', 'maxItems': 100, 'items': CLAIM},
        'judgments': {'type': 'array', 'maxItems': 100, 'items': JUDGMENT},
        'conflicts': {'type': 'array', 'maxItems': 100, 'items': {'type': 'object'}},
        'counterExamples': STRINGS}, 'additionalProperties': False},
}

# Fixed steps select only this tool's input properties; dynamic plans use the
# precise arguments contract so malformed candidates fail before execution.
TOOL_INPUT_SCHEMAS = {key: {'type': 'object', 'anyOf': [{'required': ['input', 'state']}, {'required': ['arguments']}],
    'properties': {'input': {'type': 'object'}, 'state': {'type': 'object'}, 'arguments': schema},
    'additionalProperties': False} for key, schema in ARGUMENT_SCHEMAS.items()}
RESULT_FIELDS = {
    'analysis.source_deduplicate': {'groups': {'type': 'array'}, 'uniqueCount': {'type': 'integer', 'minimum': 0}, 'duplicateCount': {'type': 'integer', 'minimum': 0}},
    'analysis.citation_validate': {'validations': {'type': 'array'}, 'acceptedCount': {'type': 'integer', 'minimum': 0}, 'rejectedCount': {'type': 'integer', 'minimum': 0}},
    'analysis.event_normalize': {'events': {'type': 'array'}, 'knownDateCount': {'type': 'integer', 'minimum': 0}, 'unknownDateCount': {'type': 'integer', 'minimum': 0}},
    'analysis.change_point': {'changes': {'type': 'array'}, 'sampleCount': {'type': 'integer', 'minimum': 0}, 'threshold': {'type': 'number', 'exclusiveMinimum': 0}},
    'analysis.scenario_structure': {'judgments': {'type': 'array'}},
    'analysis.claim_audit': {'counterExamples': STRINGS, 'unsupportedClaims': STRINGS, 'conflicts': {'type': 'array'}, 'uncheckedJudgments': STRINGS},
}
COMMON_FIELDS = {'evidence': {'type': 'array'}, 'gaps': {'type': 'array'}, 'toolKey': TEXT,
    'deterministic': {'const': True}, 'truthVerified': {'const': False},
    'provenance': {'type': 'object'}, 'artifactId': TEXT, 'artifactHash': TEXT}
TOOL_OUTPUT_SCHEMAS = {key: {'type': 'object', 'required': list(fields) + list(COMMON_FIELDS),
    'properties': {**fields, **COMMON_FIELDS}, 'additionalProperties': False} for key, fields in RESULT_FIELDS.items()}


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def _arguments(key, payload):
    validate(payload, TOOL_INPUT_SCHEMAS[key])
    properties = ARGUMENT_SCHEMAS[key]['properties']
    args = deepcopy(payload['arguments']) if 'arguments' in payload else {
        name: deepcopy(payload['input'][name]) for name in properties if name in payload['input']}
    validate(args, ARGUMENT_SCHEMAS[key])
    if len(json.dumps(args, allow_nan=False).encode()) > 4_000_000:
        raise ValueError('analysis input exceeds 4 MB')
    return args


def _citations(claims):
    validations, evidence, gaps = [], [], []
    identities = set()
    for index, claim in enumerate(claims):
        digest = body_hash(claim['body'])
        if claim.get('contentHash') and claim['contentHash'] != digest:
            raise ValueError('citation source hash differs from its body')
        check = validate_citation(claim['quote'], claim['body'], claim.get('locator'))
        identity = claim.get('claimId') or str(index)
        if identity in identities:
            raise ValueError('claim identities must be unique')
        identities.add(identity)
        validations.append({'claimId': identity, 'sourceUri': claim.get('sourceUri'), 'contentHash': digest, 'validation': check})
        if not check['match']:
            gaps.append({'claimId': identity, 'reason': check['reason']})
        elif not all(claim.get(name) for name in ('sourceUri', 'retrievedAt', 'locator')):
            gaps.append({'claimId': identity, 'reason': 'missing-source-metadata'})
        else:
            evidence.append({name: deepcopy(claim[name]) for name in ('quote', 'sourceUri', 'retrievedAt', 'locator')})
            evidence[-1].update(evidenceId=identity, claimId=identity,
                                sourceArtifactId=deepcopy(claim.get('sourceArtifactId')),
                                sourceArtifactHash=deepcopy(claim.get('sourceArtifactHash')),
                                publishedAt=deepcopy(claim.get('publishedAt')),
                                publicationDateStatus=claim.get('publicationDateStatus', 'known' if claim.get('publishedAt') else 'unknown'),
                                observedAt=deepcopy(claim.get('observedAt')),
                                effectiveAt=deepcopy(claim.get('effectiveAt')),
                                originSourceUri=deepcopy(claim.get('originSourceUri')),
                                sourceRelation=deepcopy(claim.get('sourceRelation', 'unknown')),
                                independenceStatus='unknown',
                                contentHash=digest, startOffset=check['start'], endOffset=check['end'],
                                dataTimestamp=deepcopy(claim.get('observedAt') or claim.get('effectiveAt') or claim.get('publishedAt')),
                                dataTimestampBasis=('observedAt' if claim.get('observedAt') else 'effectiveAt' if claim.get('effectiveAt') else 'publishedAt' if claim.get('publishedAt') else 'unknown'),
                                truthVerified=False, truthStatus='unverified', reviewStatus='pending')
    return validations, evidence, gaps


def calculate(key, args, *, separate_abstentions=False, measurement_quotes_only=False):
    evidence, gaps = [], []

    def risk_gap(reason, identity_key, identity_value, findings, **extra):
        """Keep one actionable gap per source/claim instead of one per token."""
        if not findings:
            return
        codes = sorted({item.get("code") for item in findings if item.get("code")})
        gaps.append({"reason": reason, identity_key: identity_value, "count": len(findings),
                     "codes": codes, "findings": deepcopy(findings[:32]), **extra})

    if key == 'analysis.source_deduplicate':
        result = deduplicate_sources(args['sources'])
        if not args['sources']: gaps.append({'reason': 'no-sources'})
        for source in args['sources']:
            body = source.get('body', source.get('content', ''))
            prompt_findings = detect_prompt_injection(body)
            # Years, addresses and section numbers are not measured quantities.
            numeric_findings = [] if measurement_quotes_only else detect_numeric_unit_risks(body)
            risk_gap('source-content-prompt-injection-signal', 'sourceUri', source.get('sourceUri'), prompt_findings)
            risk_gap('source-numeric-unit-review-required', 'sourceUri', source.get('sourceUri'), numeric_findings)
        gaps.extend({'reason': 'source-republication-or-origin-review-required', **item}
                     for item in detect_source_relations(args['sources']))
    elif key == 'analysis.citation_validate':
        rows, evidence, gaps = _citations(args['claims'])
        accepted = sum(item['validation']['match'] for item in rows)
        result = {'validations': rows, 'acceptedCount': accepted, 'rejectedCount': len(rows) - accepted}
        if not rows: gaps.append({'reason': 'no-claims'})
        for claim in args['claims']:
            body = claim.get('body', '')
            risk_gap('claim-source-prompt-injection-signal', 'claimId', claim.get('claimId'),
                     detect_prompt_injection(body))
            numeric_text = claim.get('quote', '') if measurement_quotes_only else body
            measurable = bool(claim.get('observation')) or bool(re.search(
                r'\b(?:endurance|payload|range|speed|mass|weight|duration|power|energy)\b|航时|载荷|航程|速度|质量|重量|功率|能耗',
                numeric_text, re.I))
            numeric_findings = detect_numeric_unit_risks(numeric_text) if not measurement_quotes_only or measurable else []
            risk_gap('claim-source-numeric-unit-review-required', 'claimId', claim.get('claimId'), numeric_findings)
    elif key == 'analysis.event_normalize':
        rows = normalize_events(args['events'])
        known = sum(item['dateStatus'] == 'known' for item in rows)
        result = {'events': rows, 'knownDateCount': known, 'unknownDateCount': len(rows) - known}
        if known != len(rows): gaps.append({'reason': 'missing-event-dates', 'count': len(rows) - known})
        gaps.extend({'reason': 'conflicting-event-dates', **item}
                     for item in detect_temporal_conflicts(args['events']))
    elif key == 'analysis.change_point':
        threshold = args.get('threshold', 2.0)
        result = {'changes': detect_change_points(args['values'], threshold=threshold), 'sampleCount': len(args['values']), 'threshold': threshold}
        if len(args['values']) < 2: gaps.append({'reason': 'insufficient-samples'})
    elif key == 'analysis.scenario_structure':
        rows = []
        for index, item in enumerate(args['judgments']):
            card = item.get('scenarioCard') if isinstance(item.get('scenarioCard'), dict) else {}
            # These are analytical projections of supplied fields. Empty
            # arrays are intentional: the deterministic Action never invents
            # an adversary response, trigger, effect, or counterfactual.
            rows.append({
                'scenarioId': f'scenario-{index + 1}',
                'status': 'candidate',
                'basis': {'supportingClaims': deepcopy(item.get('supportingClaims', [])),
                          'conditions': deepcopy(item.get('conditions', [])),
                          'unknowns': deepcopy(item.get('unknowns', []))},
                'drivers': deepcopy(card.get('drivers', item.get('conditions', []))),
                'adversaryResponses': deepcopy(card.get('adversaryResponses', item.get('counterArguments', []))),
                'deceptionAndEwCyber': deepcopy(card.get('deceptionAndEwCyber', {})),
                'systemDegradation': deepcopy(card.get('systemDegradation', [])),
                'logisticsConstraints': deepcopy(card.get('logisticsConstraints', [])),
                'triggerIndicators': deepcopy(card.get('triggerIndicators', [])),
                'counterfactuals': deepcopy(card.get('counterfactuals', [])),
                'candidateOutcome': item.get('statement', ''),
            })
        result = {'judgments': deepcopy(args['judgments']),
                  'scenarioStructure': {'scenarios': rows, 'method': 'evidence-bounded-projection',
                                        'truthStatus': 'unverified'}}
        gaps = [{'reason': 'judgment-needs-evidence-review', 'statement': item['statement']} for item in args['judgments']]
        gaps.extend({'reason': 'scenario-trigger-indicators-missing', 'scenarioId': row['scenarioId']}
                     for row in rows if not row['triggerIndicators'])
        gaps.extend({'reason': 'scenario-counterfactuals-missing', 'scenarioId': row['scenarioId']}
                     for row in rows if not row['counterfactuals'])
        gaps.extend({'reason': 'overconfident-judgment-language', **item}
                     for item in detect_over_inference(args['judgments'], []))
        if not args['judgments']: gaps.append({'reason': 'no-judgments'})
    else:
        rows, evidence, gaps = _citations(args['claims'])
        supported = {item['claimId'] for item in evidence}
        # Only exact Engine abstentions with no asserted supporting claims are
        # nonclaims. An unsupported substantive judgment must remain flagged.
        nonclaim=lambda item: separate_abstentions and not item['supportingClaims'] and item['statement'].strip() in {
            '该视角暂不形成判断','当前来源不足以支持该视角的判断'}
        judgments=[item for item in args['judgments'] if not nonclaim(item)]
        unsupported = [item['statement'] for item in judgments if not item['supportingClaims']
                       or not set(item['supportingClaims']).issubset(supported)]
        unchecked = [item['statement'] for item in judgments if not item['counterArguments']]
        result = {'counterExamples': deepcopy(args.get('counterExamples', [])), 'unsupportedClaims': unsupported,
                  'conflicts': deepcopy(args.get('conflicts', [])), 'uncheckedJudgments': unchecked}
        gaps.extend({'reason': 'unsupported-judgment', 'statement': statement} for statement in unsupported)
        gaps.extend({'reason': 'missing-counterargument', 'statement': statement} for statement in unchecked)
        gaps.extend({'reason': 'retained-conflict', 'conflict': item} for item in result['conflicts'])
        gaps.extend({'reason': 'overconfident-judgment-language', **item}
                     for item in detect_over_inference(args['judgments'], args['claims']))
        if not args['judgments']: gaps.append({'reason': 'no-judgments-to-audit'})
    return {**result, 'evidence': evidence, 'gaps': gaps, 'toolKey': key, 'deterministic': True, 'truthVerified': False}


class FutureBattlefieldTools:
    def __init__(self, repository): self.repository = repository

    async def invoke(self, key, payload, ctx):
        binding_version = ctx.profile.tools[key].version
        from .research import ResearchTools, SUPPORTED_TREND_VERSIONS
        if key == 'analysis.change_point' and binding_version in SUPPORTED_TREND_VERSIONS:
            return await ResearchTools(self.repository).invoke(key, payload, ctx)
        if binding_version not in SUPPORTED_VERSIONS:
            raise ValueError('unsupported frozen analysis tool version')
        args = _arguments(key, payload)
        result = calculate(key, args, separate_abstentions=binding_version in {'1.2.1', '1.2.2', '1.2.3', '1.2.4', '1.2.5', '1.2.6'},
                           measurement_quotes_only=binding_version in {'1.2.2', '1.2.3', '1.2.4', '1.2.5', '1.2.6'})
        if binding_version == '1.1.0':
            # Historical Profile snapshots use the 1.1 contract, whose
            # output schema intentionally has no scenarioStructure field.
            # Preserve replay compatibility while 1.2 Runs receive the new
            # evidence-bounded structure.
            result.pop('scenarioStructure', None)
        result['provenance'] = {'runId': ctx.run_id, 'version': binding_version, 'qualityRuleset': QUALITY_RULESET_VERSION,
                                'inputHash': _hash(args),
                                'inputAuthority': 'provided-input', 'reviewStatus': 'pending'}
        if binding_version in {'1.2.2', '1.2.3', '1.2.4', '1.2.5', '1.2.6'}:
            result['provenance']['numericValidationScope'] = 'cited-measurements-only; nonmeasurement document numbers not assessed'
        artifact_id = self.repository.add_artifact(ctx.run_id, 'analysis-result', result)
        return {**result, 'artifactId': artifact_id, 'artifactHash': _hash(result)}


def register_future_battlefield_tools(engine):
    runtime = FutureBattlefieldTools(engine.repo)
    for key in ARGUMENT_SCHEMAS:
        async def handler(payload, ctx, key=key):
            return await runtime.invoke(key, payload, ctx)
        engine.register_tool(key, handler, deepcopy(TOOL_INPUT_SCHEMAS[key]), deepcopy(TOOL_OUTPUT_SCHEMAS[key]))
    from .research import register_research_tools
    register_research_tools(engine)
    return runtime
