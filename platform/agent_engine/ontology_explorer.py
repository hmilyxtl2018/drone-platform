"""Read-only, Run-scoped graph exploration over immutable report artifacts."""
from copy import deepcopy
import hashlib
import json
from urllib.parse import urlsplit

from .context_contract import content_hash
from .ontology_quality import source_coverage
from .ontology_semantics import identity_descriptors, relation_semantics


PROJECTION_VERSION = 'ontology-explorer.v2'
PREDICATES = {
    'depends-on': '依赖', 'constrains': '约束', 'enables': '支持形成',
    'changes': '影响', 'part-of': '组成', 'associated-with': '相关于',
}


def _edges(graph):
    result = []
    for index, edge in enumerate(graph.get('edges', [])):
        item = deepcopy(edge)
        item.update(source=edge.get('source', edge.get('subject')),
                    target=edge.get('target', edge.get('object')),
                    relation=edge.get('relation', edge.get('predicate')))
        item.setdefault('id', 'structure-' + content_hash([item['source'], item['relation'], item['target'], index])[:20])
        item.setdefault('kind', 'structure')
        result.append(item)
    return result


def evidence_index(report):
    result = {}
    for row in report.get('evidence', []):
        if not isinstance(row, dict):
            continue
        identity = row.get('evidenceId') or row.get('candidateId') or row.get('claimId')
        if identity:
            result[str(identity)] = deepcopy(row)
    # UAV projections also carry the evidence fields on their excerpt nodes.
    for node in report.get('ontologyGraph', {}).get('nodes', []):
        if node.get('type') == 'EvidenceExcerpt':
            identity = node.get('candidateId') or node.get('evidenceId')
            if identity:
                result[str(identity)] = {**node, **result.get(str(identity), {}), 'evidenceId': str(identity)}
    return result


def explorer_graph(report):
    """Enrich a projection, never rewrite the canonical stored graph or hash."""
    graph = deepcopy(report.get('ontologyGraph') or {})
    canonical_hash = graph.get('contentHash') or content_hash(graph)
    nodes = graph.get('nodes', [])
    edges = _edges(graph)
    by_id = {n['id']: n for n in nodes}
    if any(e['source'] not in by_id or e['target'] not in by_id for e in edges):
        raise ValueError('图谱包含无法解析的节点引用。')
    for assertion in graph.get('assertions', []):
        assertion['semantics'] = relation_semantics(assertion)
        assertion['auditTrace'] = deepcopy(report.get('explorerQuality', {}).get('reviewTraces', {}).get(assertion['id'],
            {'status': 'incomplete', 'reviewIndependence': 'not-established'}))
        if assertion.get('subject') not in by_id or assertion.get('object') not in by_id:
            raise ValueError('本体断言的主体或客体不存在。')
        edges.append({'id': 'domain:' + assertion['id'], 'source': assertion['subject'],
                      'target': assertion['object'], 'relation': assertion['predicate'],
                      'assertionId': assertion['id'], 'perspectiveKey': assertion.get('perspectiveKey'),
                      'kind': 'assertion', 'truthStatus': assertion.get('truthStatus', 'unverified')})
    perspectives = []
    for node in nodes:
        if node.get('type') != 'perspective':
            continue
        key = node['id'].removeprefix('perspective:')
        assertions = [a for a in graph.get('assertions', []) if a.get('perspectiveKey') == key]
        judgments = [e['target'] for e in edges if e['source'] == node['id'] and e['relation'] == 'has-judgment']
        judgment = judgments[0] if judgments else None
        related = {node['id']}
        related.update(judgments)
        related.update(n['id'] for n in nodes if n.get('perspectiveKey') == key)
        for a in assertions:
            related.update([a['id'], a['subject'], a['object']])
            related.update('evidence:' + str(v) for v in a.get('evidenceIds', []))
        # Include judgment conditions, counterarguments and source paths in
        # the perspective scope, even though they start collapsed in the UI.
        for e in edges:
            if e['source'] in judgments or e['target'] in judgments:
                related.update([e['source'], e['target']])
            if e['source'] == node['id']:
                related.add(e['target'])
        for e in edges:
            if e['relation'] == 'quoted-from' and e['source'] in related:
                related.add(e['target'])
        for identity in related:
            if identity in by_id:
                by_id[identity].setdefault('perspectiveKeys', []).append(key)
        perspectives.append({'key': key, 'label': node['label'], 'judgmentId': judgment, 'judgmentIds': judgments,
                             'assertionCount': len(assertions)})
    identities = identity_descriptors(nodes, report['runId'])
    for node in nodes:
        if node['id'] in identities:
            node['identity'] = identities[node['id']]
    graph.update(nodes=nodes, edges=edges, perspectives=perspectives,
                 runId=report['runId'], projectionVersion=PROJECTION_VERSION,
                 projectionScope='run-report', sourceGraphHash=canonical_hash,
                 sourceSchemaVersion=graph.get('schemaVersion'),
                 truthStatus=report.get('truthStatus', 'unverified'),
                 agentKey=report.get('agentKey'), runStatus=report.get('status'),
                 sourceCoverage=deepcopy(report.get('explorerQuality', {}).get('sourceCoverage') or {}),
                 reviewStatus=report.get('reviewStatus', 'pending'))
    graph.pop('contentHash', None)
    graph['contentHash'] = content_hash(graph)
    return graph


def selection_detail(report, kind, identity):
    from .decision_detail import gap_detail
    graph = explorer_graph(report)
    nodes = {n['id']: n for n in graph['nodes']}
    edges = {e['id']: e for e in graph['edges']}
    assertions = {a['id']: a for a in graph.get('assertions', [])}
    selected = (nodes if kind == 'node' else edges).get(identity)
    if selected is None:
        raise KeyError('所选对象不属于当前运行图谱。')
    assertion = assertions.get(selected.get('assertionId', identity))
    related_assertions = ([assertion] if assertion else
                          [a for a in assertions.values() if identity in {a['subject'], a['object'], a.get('judgmentId')}])
    evidence = evidence_index(report)
    ids = set()
    for a in related_assertions:
        ids.update(str(v) for v in a.get('evidenceIds', []))
    if kind == 'node':
        own = selected.get('evidenceId') or selected.get('candidateId')
        if own:
            ids.add(str(own))
        for edge in graph['edges']:
            if identity not in {edge['source'], edge['target']}:
                continue
            other = nodes[edge['target'] if edge['source'] == identity else edge['source']]
            if other.get('type') in {'evidence', 'EvidenceExcerpt'}:
                ids.add(str(other.get('evidenceId') or other.get('candidateId')))
        if selected.get('type') in {'source', 'SourceSnapshot'}:
            uri = selected.get('sourceUri') or selected.get('label')
            artifact_id = selected.get('sourceArtifactId') or selected.get('artifactId')
            ids.update(k for k, e in evidence.items()
                       if (uri and e.get('sourceUri') == uri)
                       or (not uri and artifact_id and e.get('sourceArtifactId') == artifact_id))
    elif not assertion:
        for endpoint in (selected['source'], selected['target']):
            n = nodes[endpoint]
            own = n.get('evidenceId') or n.get('candidateId')
            if own:
                ids.add(str(own))
    perspective = assertion.get('perspectiveKey') if assertion else selected.get('perspectiveKey')
    if not perspective and identity.startswith('judgment:'):
        perspective = identity.removeprefix('judgment:')
    if not perspective and identity.startswith(('gap:', 'perspective:')):
        perspective = identity.split(':', 1)[1]
    finding_id = (assertion or selected).get('findingId')
    finding = next((f for f in report.get('findings', []) if
                    (f.get('findingId') == finding_id if finding_id else perspective and f.get('perspectiveKey') == perspective)), {})
    work = selected.get('task') if selected.get('type') == 'verification-task' else None
    if work:
        ids.update(work.get('evidenceIds', []))
    relation_conditions = (assertion or {}).get('conditions') or []
    condition_scope = (assertion or {}).get('conditionScope')
    if condition_scope == 'perspective' or (not condition_scope and relation_conditions == finding.get('conditions')):
        relation_conditions = []
    if not related_assertions:
        ids.update(str(v) for v in finding.get('supportingClaims', []))
    def texts(*values):
        return list(dict.fromkeys(str(v) for group in values if isinstance(group, list) for v in group))
    title = selected.get('label', '')
    if assertion:
        title = f"{nodes[assertion['subject']]['label']} {PREDICATES.get(assertion['predicate'], assertion['predicate'])} {nodes[assertion['object']]['label']}"
    elif kind == 'edge':
        title = f"{nodes[selected['source']]['label']} → {nodes[selected['target']]['label']}"
    return {'runId': report['runId'], 'projectionHash': graph['contentHash'],
            'selection': {'kind': kind, 'id': identity}, 'title': title,
            'object': deepcopy(selected), 'assertion': assertion,
            'verificationTask': deepcopy(work),
            'perspectiveKey': perspective,
            'gapDetail': gap_detail(report, selected, perspective),
            'interpretation': finding.get('statement') or selected.get('interpretation', ''),
            'modality': assertion.get('modality', 'conditional-hypothesis') if assertion else 'report-projection',
            'truthStatus': (assertion or selected).get('truthStatus', report.get('truthStatus', 'unverified')),
            'supportReview': deepcopy((assertion or finding).get('supportReview') or {}),
            'auditTrace': deepcopy((assertion or {}).get('auditTrace') or {'status': 'incomplete'}),
            'relationConditions': deepcopy(relation_conditions),
            'perspectiveConditions': deepcopy(finding.get('conditions') or []),
            'conditions': texts((assertion or {}).get('conditions'), finding.get('conditions')),
            'counterArguments': (assertion or {}).get('counterArguments') or [],
            'unknowns': (assertion or {}).get('unknowns') or [],
            'perspectiveCounterArguments': finding.get('counterArguments') or [],
            'perspectiveUnknowns': finding.get('unknowns') or [],
            'sourceCoverage': source_coverage([evidence[k] for k in sorted(ids) if k in evidence]),
            'evidence': [{'evidenceId': k, **evidence[k],
                'citationRole': 'declared-support' if any(k in a.get('evidenceIds', []) for a in related_assertions) else 'context',
                'relationRoles': [{'assertionId': a['id'], 'role': 'support',
                    'reviewStatus': 'artifact-backed' if a.get('auditTrace', {}).get('status') == 'artifact-backed' else 'unverified'}
                    for a in related_assertions if k in a.get('evidenceIds', [])]}
                for k in sorted(ids) if k in evidence],
            'missingEvidenceIds': sorted(ids - evidence.keys()),
            'relations': [{'id': a['id'], 'subject': nodes[a['subject']]['label'],
                           'predicate': PREDICATES.get(a['predicate'], a['predicate']),
                           'object': nodes[a['object']]['label']} for a in related_assertions],
            'neighbors': [{'id': n, 'label': nodes[n]['label'], 'type': nodes[n]['type']}
                          for n in sorted({e['target'] if e['source'] == identity else e['source']
                                           for e in graph['edges'] if identity in {e['source'], e['target']}})]}


def source_excerpt(report, identity, repository):
    """Resolve exact stored text; no network fetch, model request or arbitrary artifact ID."""
    evidence = evidence_index(report).get(identity)
    if not evidence:
        raise KeyError('证据不属于当前运行。')
    run_id = report['runId']
    from .evidence_repository import artifact_source, artifact_json_hash
    source = None
    if evidence.get('sourceId'):
        source = repository.get_source_snapshot(run_id, evidence['sourceId'])
        if (source['artifactId'] != evidence.get('sourceArtifactId') or
                source['artifactHash'] != evidence.get('sourceArtifactHash')):
            raise ValueError('来源引用与证据保存的产物不一致。')
    else:
        artifact = repository.get_artifact(run_id, evidence.get('sourceArtifactId'))
        if not artifact or artifact.get('run_id') != run_id:
            raise KeyError('本次运行未保存该来源快照。')
        body = artifact.get('content_json', {})
        if isinstance(body, str):
            body = json.loads(body)
        if (artifact_json_hash(body) != artifact.get('content_hash') or
                artifact.get('content_hash') != evidence.get('sourceArtifactHash')):
            raise ValueError('证据产物的内容摘要不匹配。')
        if artifact.get('artifact_type') == 'future-claim-candidates':
            claim = next((c for c in body.get('claims', []) if str(c.get('claimId')) == identity
                          and c.get('quote') == evidence.get('quote') and c.get('contentHash') == evidence.get('contentHash')), None)
            if not claim:
                raise ValueError('证据与已保存的引文产物不一致。')
            original = repository.get_artifact(run_id, claim.get('sourceSnapshotId'))
            source = artifact_source(original, run_id)
            if source['contentHash'] != evidence.get('contentHash'):
                original_body = original['content_json']
                if isinstance(original_body, str):
                    original_body = json.loads(original_body)
                segment = next((s for s in original_body.get('segments', [])
                                if isinstance(s.get('markdown'), str)
                                and hashlib.sha256(s['markdown'].encode()).hexdigest() == evidence.get('contentHash')
                                and s['markdown'] == claim.get('body')), None)
                if segment:
                    source = {**source, 'markdown': segment['markdown'], 'contentHash': evidence['contentHash'],
                              'contentKind': 'web-segment', 'segmentIndex': segment.get('index'),
                              'segmentCount': segment.get('count')}
        else:
            source = artifact_source(artifact, run_id)
    text = source['markdown']
    if source['contentHash'] != evidence.get('contentHash'):
        raise ValueError('来源正文与证据内容摘要不匹配。')
    quote = evidence.get('quote', '')
    locator = evidence.get('locator') or {}
    start = evidence.get('startOffset', locator.get('start'))
    end = evidence.get('endOffset', locator.get('end'))
    status = 'exact-range'
    if start is None or end is None:
        start = text.find(quote) if quote and text.count(quote) == 1 else -1
        end = start + len(quote)
        status = 'unique-quote-match'
    if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(text) or text[start:end] != quote:
        raise ValueError('保存的摘录定位与来源正文不一致。')
    lo, hi = max(0, start - 1200), min(len(text), end + 1800)
    uri = source.get('sourceUri') or evidence.get('sourceUri', '')
    public_url = uri if urlsplit(uri).scheme in {'http', 'https'} else None
    return {'runId': run_id, 'evidenceId': identity, 'sourceArtifactId': source['artifactId'],
            'sourceArtifactHash': source['artifactHash'], 'contentHash': source['contentHash'],
            'title': source.get('metadata', {}).get('title') or (urlsplit(public_url).hostname if public_url else '本体来源摘录'),
            'sourceUri': public_url, 'contentKind': source['contentKind'],
            'segmentIndex': source.get('segmentIndex'), 'segmentCount': source.get('segmentCount'),
            'retrievedAt': source.get('retrievedAt'), 'publishedAt': source.get('publishedAt'),
            'text': text[lo:hi], 'windowStart': lo, 'windowEnd': hi, 'totalCharacters': len(text),
            'highlight': {'start': start - lo, 'end': end - lo, 'offsetUnit': 'unicode-codepoint'},
            'locatorStatus': status, 'truthVerified': False}
