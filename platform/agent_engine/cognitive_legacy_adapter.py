"""Explicit, evidence-preserving adapter to the registered extraction Action.

The legacy Action owns the model call and citation checks.  This projection
supplies its reviewed task contract without pretending that ontology-method
research supplies equipment measurements or dated real-world events.
"""
from copy import deepcopy
import json
from types import SimpleNamespace

from .cognitive_contracts import digest, validate_context
from .decision_semantics import compile_task


def _empty_cognitive_material(ctx, repo):
    """Recognize an empty input only after proving its real cognitive edge.

    A caller-provided applicability label is insufficient. Ordinary extraction
    runs retain the original handlers, including their blocking behavior.
    """
    applicability = (ctx.input.get('scope') or {}).get('toolApplicability') or {}
    if (ctx.profile.agent_key != 'evidence-extraction-action'
            or applicability.get('domain') != 'public-ontology-document-research'):
        return None
    bundle = ctx.input.get('sourceBundle')
    if not isinstance(bundle, dict) or not isinstance(bundle.get('sources'), list):
        raise ValueError('cognitive extraction requires an explicit source list')
    sources = bundle['sources']
    if bundle.get('claims') or bundle.get('events'):
        return None
    for source in sources:
        if not isinstance(source, dict):
            raise ValueError('cognitive extraction source must be an object')
        body = source.get('body') or source.get('content') or ''
        if not isinstance(body, str):
            raise ValueError('cognitive extraction source text must be a string')
        if body.strip():
            return None
    from .action_reasoning import child_reasoning
    from .cognitive_identity import APPLICATION_KEY
    from .cognitive_implementation import assert_profile_implementation
    from .decision_semantics import enabled, validate_handoff
    if not child_reasoning(ctx.profile) or not enabled(ctx.profile):
        raise PermissionError('empty cognitive extraction requires its reviewed child contract')
    validate_handoff(ctx)
    parent_id = ctx.input.get('parentRunId')
    parent = repo.get_run(parent_id) if parent_id else None
    parent_input = (parent or {}).get('input_json') or {}
    context = (ctx.input.get('scope') or {}).get('researchContext')
    if (not parent or parent.get('agent_key') != APPLICATION_KEY
            or parent_input.get('context') != context
            or parent_input.get('question') != ctx.input.get('question')):
        raise PermissionError('empty cognitive extraction is not delegated by its owning application')
    validate_context(context)
    if (context['scenarioRef']['kind'] != 'public-research'
            or context['ruleSetRef']['selection'] != 'not-applicable'
            or ctx.input['scope'].get('contextHash') != digest(context)):
        raise PermissionError('empty cognitive extraction lacks the frozen public research context')
    parent_profile = repo.load_profile_snapshot(parent_id)
    if parent_profile.agent_key != APPLICATION_KEY:
        raise PermissionError('empty cognitive extraction parent snapshot changed')
    assert_profile_implementation(parent_profile)
    node = next((node for node in parent_profile.execution_baseline.get('graph', {}).get('nodes', [])
                 if node.get('id') == 'extract-claims'), {})
    if (ctx.input.get('parentStepKey') != 'extract-claims'
            or node.get('childAgentKey') != ctx.profile.agent_key
            or not node.get('compositionEdgeId')
            or node['compositionEdgeId'] != ctx.input.get('compositionEdgeId')):
        raise PermissionError('empty cognitive extraction differs from its frozen composition edge')
    row = repo.get_artifact(parent_id, ctx.input.get('inputArtifactId'))
    if (not row or row.get('run_id') != parent_id or row.get('artifact_type') != 'action-input'
            or digest(row['content_json']) != row.get('content_hash')
            or row['content_hash'] != ctx.input.get('inputArtifactHash')
            or any(ctx.input.get(key) != value for key, value in row['content_json'].items())):
        raise PermissionError('empty cognitive extraction action input changed')
    with repo.connection_factory() as con:
        with con.cursor() as cursor:
            cursor.execute('SELECT input_artifact_id,input_artifact_hash,composition_edge_id,parent_step_key '
                           'FROM agent_action_invocation WHERE run_id=%s AND child_run_id=%s',
                           (parent_id, ctx.run_id))
            invocation = cursor.fetchone()
    if (not invocation or invocation['input_artifact_id'] != row['artifact_id']
            or invocation['input_artifact_hash'] != row['content_hash']
            or invocation['composition_edge_id'] != node['compositionEdgeId']
            or invocation['parent_step_key'] != 'extract-claims'):
        raise PermissionError('empty cognitive extraction has no matching recorded invocation')
    # Blank snapshots still require intact owned bytes; their existence must
    # never be treated as usable quotations or silently fabricated evidence.
    for source in sources:
        snapshot = repo.get_artifact(parent_id, source.get('artifactId'))
        if (not snapshot or snapshot.get('run_id') != parent_id
                or snapshot.get('artifact_type') != 'web-source-snapshot'
                or digest(snapshot['content_json']) != snapshot.get('content_hash')
                or snapshot['content_hash'] != source.get('artifactHash')
                or snapshot['content_json'].get('sourceUri') != source.get('sourceUri')
                or snapshot['content_json'].get('markdown') != (source.get('body') or source.get('content') or '')):
            raise PermissionError('empty cognitive source snapshot is not intact and owned')
    return {'reason': 'no-sources' if not sources else 'no-readable-source-material',
            'sourceCount': len(sources), 'sourceBundleHash': digest(bundle),
            'inputArtifactRef': {'runId': parent_id, 'artifactId': row['artifact_id'],
                                 'artifactType': 'action-input', 'contentHash': row['content_hash']}}


def _empty_execution_result(ctx, step, sid, repo, material):
    stage = 'extraction' if step.key == 'extract-source-claims' else 'citation-validation'
    receipt = {'schemaVersion': 'cognitive-empty-material.v1', 'runId': ctx.run_id,
               'parentRunId': ctx.input['parentRunId'], 'stepKey': step.key,
               'stage': stage, **material, 'status': 'not-executed',
               'decisionSpecHash': ctx.input['decisionSpec']['contentHash'],
               'claimsProduced': 0, 'evidenceProduced': 0,
               'providerCallCreated': False, 'truthVerified': False}
    identity = repo.add_artifact(ctx.run_id, 'cognitive-empty-material-execution', receipt, sid)
    execution = {'status': 'not-executed', 'reason': material['reason'],
                 'receiptRef': {'runId': ctx.run_id, 'artifactId': identity,
                                'artifactType': 'cognitive-empty-material-execution',
                                'contentHash': digest(receipt)}}
    return {'stepKey': step.key, 'status': 'partial', 'claims': [], 'facts': [], 'evidence': [],
            'toolResults': {}, 'decisionSpecHash': ctx.input['decisionSpec']['contentHash'],
            'modelExecution' if stage == 'extraction' else 'citationExecution': execution,
            'gaps': [{'reason': 'cognitive-empty-source-material',
                      'description': '没有可读取的授权来源与引文，抽取及引用核验未执行；不形成研究主张。',
                      'materialReason': material['reason'], 'truthVerified': False}]}


async def cognitive_action_extract_claims(ctx, step, sid, gateway, repo):
    material = _empty_cognitive_material(ctx, repo)
    if material is not None:
        return _empty_execution_result(ctx, step, sid, repo, material)
    from .future_battlefield_runtime import action_extract_claims
    return await action_extract_claims(ctx, step, sid, gateway, repo)


async def cognitive_action_validate_citations(ctx, step, sid, gateway, repo):
    material = _empty_cognitive_material(ctx, repo)
    if material is not None:
        previous = (ctx.state.get('steps') or {}).get('extract-source-claims') or {}
        execution = previous.get('modelExecution') or {}
        ref = execution.get('receiptRef') or {}
        row = repo.get_artifact(ctx.run_id, ref.get('artifactId'))
        if (previous.get('status') != 'partial' or previous.get('claims') != []
                or previous.get('evidence') != [] or execution.get('status') != 'not-executed'
                or not row or row.get('run_id') != ctx.run_id
                or row.get('artifact_type') != 'cognitive-empty-material-execution'
                or digest(row['content_json']) != row.get('content_hash')
                or row['content_hash'] != ref.get('contentHash')
                or row['content_json'].get('sourceBundleHash') != material['sourceBundleHash']
                or row['content_json'].get('stepKey') != 'extract-source-claims'
                or row['content_json'].get('decisionSpecHash') != ctx.input['decisionSpec']['contentHash']):
            raise PermissionError('empty citation validation requires the intact extraction abstention receipt')
        return _empty_execution_result(ctx, step, sid, repo, material)
    from .future_battlefield_runtime import action_validate_citations
    return await action_validate_citations(ctx, step, sid, gateway, repo)


def extraction_payload(ctx, source_bundle):
    context = deepcopy(ctx.input['context'])
    validate_context(context, ctx.state.get('_cognitiveContext', context))
    question = ctx.input['question']
    # The legacy semantic contract's per-question ceiling is stricter than
    # the root TaskInput ceiling. Never silently truncate a user's question.
    if not isinstance(question, str) or not question.strip() or len(question) > 2000:
        raise ValueError('legacy extraction research question requires 1-2000 characters')
    perspectives = [{'key': 'ontology-methods', 'name': '本体方法与证据治理', 'question': question}]
    applicability = {
        'domain': 'public-ontology-document-research',
        'citationValidation': 'exact-quote-and-source-integrity; not-factual-verification',
        'numericValidation': 'only-explicit-cited-measurements; document-section-and-version-numbers-excluded',
        'eventNormalization': 'not-applicable-without-explicit-research-events',
        'sourceDiagnostics': 'original-snapshots-preserved; derived-scrape-diagnostics-are-not-domain-findings',
    }
    scope = {'researchContext': context, 'contextHash': digest(context),
             'toolApplicability': applicability}
    task = SimpleNamespace(run_id=ctx.run_id, profile=ctx.profile,
                           input={'question': question, 'scope': scope})
    spec = compile_task(task, perspectives)
    # Retain the exact source bytes and immutable artifact identities. Scrape
    # diagnostics (such as unitNormalization over an entire standards page)
    # remain in the original artifact rather than becoming research claims.
    source_fields = ('sourceUri', 'body', 'content', 'title', 'artifactId', 'artifactHash',
                     'contentHash', 'retrievedAt', 'publishedAt', 'publicationDateStatus',
                     'perspectiveKey', 'originSourceUri', 'sourceRelation')
    sources = [{key: deepcopy(source[key]) for key in source_fields if key in source}
               for source in source_bundle.get('sources', [])]
    bundle = {'sources': sources, 'claims': [], 'events': []}
    for key in ('sourceArtifactId', 'sourceArtifactHash'):
        if source_bundle.get(key):
            bundle[key] = deepcopy(source_bundle[key])
    return {'claims': [], 'events': [], 'sourceBundle': bundle, 'question': question,
            'scope': scope, 'perspectives': perspectives, 'decisionSpec': spec}


def receive_extraction_result(repo, ctx, child_run_id, output, source_bundle):
    """Fail closed on legacy echo execution and restore source ownership."""
    expected = extraction_payload(ctx, source_bundle)['decisionSpec']['contentHash']
    if output.get('actionReasoningVersion') != 'child-v1' or not isinstance(output.get('claims'), list):
        raise RuntimeError('cognitive extraction requires the child-v1 reasoning ActionResult')
    if output.get('decisionSpecHash') != expected:
        raise ValueError('cognitive extraction result lost its frozen semantic task binding')
    from .action_reasoning import receive_child_result
    return receive_child_result(repo, ctx, child_run_id, output)


def _source_segments(source, selected):
    """Locate selected passage fragments in the unchanged saved source."""
    body = str(source.get('body') or source.get('content') or '')
    separator = '\n\n[... separate saved source passage ...]\n\n'
    passages = str(source.get('researchPassages') or body).split(separator)
    offset, segments, formatting = 0, [], []
    for index, passage in enumerate(passages):
        if offset >= len(selected):
            break
        text = selected[offset:offset + len(passage)]
        if not passage.startswith(text):
            raise ValueError('selected extraction passage differs from saved material')
        start = body.find(text) if text else 0
        if start < 0:
            raise ValueError('selected extraction passage is not present in the source snapshot')
        if text:
            segments.append({'selectedStart': offset, 'selectedEnd': offset + len(text),
                             'sourceStart': start, 'sourceEnd': start + len(text)})
        offset += len(text)
        if len(text) < len(passage):
            break
        if index + 1 < len(passages) and offset < len(selected):
            inserted = selected[offset:offset + len(separator)]
            if not separator.startswith(inserted):
                raise ValueError('unexpected extraction passage separator')
            formatting.append({'selectedStart': offset, 'selectedEnd': offset + len(inserted),
                               'kind': 'generated-passage-separator'})
            offset += len(inserted)
    if offset != len(selected):
        raise ValueError('extraction projection contains unmapped material')
    return segments, formatting


def budget_extraction_messages(ctx, step, sid, repo, messages, schema, sources):
    """Fit this adapter's model excerpts to real remaining frozen allowances.

    Only the cognitive legacy handoff enters this branch. Full evidence remains
    immutable; the saved projection records exact selected text and source
    offsets. The authoritative model gateway still reserves every actual call.
    """
    applicability = (ctx.input.get('scope') or {}).get('toolApplicability') or {}
    if (ctx.profile.agent_key != 'evidence-extraction-action'
            or applicability.get('domain') != 'public-ontology-document-research'):
        return messages
    from .configuration import model_gateway
    from .cognitive_runtime import _request_bound
    from .decision_material import extraction_material
    from .phase3_repository import BudgetExceededError
    node = next((node for node in (ctx.profile.execution_baseline or {}).get('graph', {}).get('nodes', [])
                 if node.get('id') == step.key), {})
    if not node.get('budget') or 'input_tokens' not in node['budget']:
        raise PermissionError('cognitive extraction requires a frozen node input allowance')
    with repo.connection_factory() as con:
        with con.cursor() as cursor:
            cursor.execute('SELECT used_json FROM agent_run_step_budget_usage WHERE run_id=%s AND step_key=%s',
                           (ctx.run_id, step.key))
            used = (cursor.fetchone() or {}).get('used_json') or {}
    node_remaining = max(0, int(node['budget']['input_tokens']) - int(used.get('input_tokens', 0)))
    shared_remaining = int(repo.get_budget(ctx.run_id)['remaining']['input_tokens'])
    allowance = min(node_remaining, shared_remaining)
    gateway = model_gateway(repo, ctx.profile)
    original = json.loads(messages[-1]['content'])
    stage = 'future-battlefield-extract-facts'

    def candidate(maximum):
        material, projection = extraction_material(sources, max_bytes=maximum)
        request = deepcopy(messages)
        request[-1]['content'] = json.dumps({**original, 'sources': material}, ensure_ascii=False)
        bound = _request_bound(gateway, ctx, repo, step, request, schema, stage=stage)
        return request, projection, material, bound

    empty = candidate(0)
    if empty[3] >= allowance:
        repo.add_artifact(ctx.run_id, 'cognitive-extraction-material', {
            'schemaVersion': 'cognitive-extraction-budget-v1', 'status': 'not-executed-budget',
            'effectiveInputAllowance': allowance, 'schemaAndContextInputBound': empty[3],
            'selectedMaterial': [], 'fullSourceSnapshotsPreserved': True}, sid)
        raise BudgetExceededError('cognitive extraction schema and frozen context exhaust its input allowance')
    low, high, chosen = 0, 18000, empty
    while low <= high:
        midpoint = (low + high) // 2
        proposal = candidate(midpoint)
        if proposal[3] <= allowance:
            chosen, low = proposal, midpoint + 1
        else:
            high = midpoint - 1
    selected, projection, material, bound = chosen
    receipts = []
    from .evidence_repository import artifact_json_hash
    for source, item in zip(sources, material):
        identity = source.get('artifactId')
        row = repo.get_artifact(ctx.run_id, identity)
        if (not row or row.get('run_id') != ctx.run_id or row.get('artifact_type') != 'web-source-snapshot'
                or artifact_json_hash(row['content_json']) != row['content_hash']
                or row['content_hash'] != source.get('artifactHash')):
            raise PermissionError('cognitive extraction projection lacks an intact owned source snapshot')
        raw = row['content_json']
        source_body = str(source.get('body') or source.get('content') or '')
        if raw.get('markdown') != source_body or raw.get('sourceUri') != source.get('sourceUri'):
            raise ValueError('cognitive extraction source projection differs from the saved snapshot')
        segments, formatting = _source_segments(source, item['body'])
        receipts.append({'sourceIndex': item['sourceIndex'], 'sourceUri': item['sourceUri'],
            'sourceArtifactRef': {'runId': ctx.run_id, 'artifactId': identity,
                'artifactType': row['artifact_type'], 'contentHash': row['content_hash']},
            'selectedText': item['body'], 'sourceSegments': segments, 'formatting': formatting})
    repo.add_artifact(ctx.run_id, 'cognitive-extraction-material', {
        'schemaVersion': 'cognitive-extraction-budget-v1', 'status': 'selected',
        'decisionSpecHash': ctx.input['decisionSpec']['contentHash'],
        'effectiveInputAllowance': allowance, 'nodeRemainingInput': node_remaining,
        'sharedRemainingInput': shared_remaining, 'schemaAndContextInputBound': empty[3],
        'preparedRequestInputBound': bound, 'projection': projection,
        'selectedMaterial': receipts, 'fullSourceSnapshotsPreserved': True}, sid)
    return selected
