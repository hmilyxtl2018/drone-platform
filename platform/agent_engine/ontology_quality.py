"""Conservative source grouping and artifact-backed review traces for projections."""
from copy import deepcopy
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

from .context_contract import content_hash
from .reasoning_gates import relation_review_binding


def source_identity(value):
    if not isinstance(value, str) or not value:
        return None
    parts = urlsplit(value)
    if not parts.scheme:
        return value
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if not k.lower().startswith('utm_') and k.lower() not in {'fbclid', 'gclid'}]
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, urlencode(sorted(query)), ''))


def source_coverage(evidence):
    """Groups indicate known shared origins, never independent corroboration."""
    rows = [e for e in evidence if isinstance(e, dict)]
    parents = list(range(len(rows)))
    def root(i):
        while parents[i] != i:
            parents[i] = parents[parents[i]]
            i = parents[i]
        return i
    seen = {}
    for i, row in enumerate(rows):
        keys = [('uri', source_identity(row.get(k))) for k in ('sourceUri', 'originSourceUri')]
        # A numeric ontology excerpt is not a complete source body.
        if str(row.get('sourceUri', '')).startswith(('http://', 'https://')):
            keys.append(('body', row.get('contentHash')))
        for kind, value in keys:
            if not value:
                continue
            key = (kind, value)
            if key in seen:
                parents[root(i)] = root(seen[key])
            else:
                seen[key] = i
    grouped = {}
    for i, row in enumerate(rows):
        grouped.setdefault(root(i), []).append(row)
    groups = []
    for members in grouped.values():
        ids = sorted(str(e.get('evidenceId') or e.get('candidateId') or e.get('claimId')) for e in members)
        uris = sorted({source_identity(e.get('sourceUri')) for e in members if source_identity(e.get('sourceUri'))})
        origins = sorted({source_identity(e.get('originSourceUri')) for e in members if source_identity(e.get('originSourceUri'))})
        groups.append({'id': 'source-group:' + content_hash([uris, origins, ids])[:20],
                       'evidenceIds': ids, 'sourceUris': uris, 'declaredOrigins': origins,
                       'independenceStatus': 'not-established'})
    return {'status': 'not-established', 'evidenceCount': len(rows), 'sourceGroupCount': len(groups),
            'verifiedIndependentSourceCount': 0, 'sourceGroups': groups,
            'notice': '来源分组只识别已知同源记录；不同网址、域名或分组不代表相互独立。'}


def enrich_quality(report, repository):
    """Read existing proofs without rewriting the report or upgrading truth status."""
    result = deepcopy(report)
    relations = []
    for finding in result.get('findings', []):
        for relation in finding.get('relations', []):
            relations.append({**relation, 'perspectiveKey': finding.get('perspectiveKey')})
    validator = getattr(repository, 'validate_support_reviews', None)
    proofs = validator(report['runId'], relations) if relations and callable(validator) else []
    verified = {p['bindingHash']: p for p in proofs if isinstance(p, dict) and p.get('bindingHash')}
    traces = {}
    for relation in relations:
        identity = 'assertion:' + str(relation.get('findingId') or relation.get('perspectiveKey')) + ':' + str(relation.get('id'))
        proof = verified.get(relation_review_binding(relation))
        traces[identity] = ({**proof, 'status': 'artifact-backed', 'resolution': 'existing-run-artifacts'}
                            if proof else {'status': 'incomplete', 'reviewIndependence': 'not-established'})
    result['explorerQuality'] = {'reviewTraces': traces, 'sourceCoverage': source_coverage(result.get('evidence', []))}
    return result
