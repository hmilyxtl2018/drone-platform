"""Conservative identity links and report-version comparisons, never entity merges."""
from collections import defaultdict
from copy import deepcopy
import unicodedata

from .context_contract import content_hash

DOMAIN_TYPES = {'system', 'capability', 'technology', 'constraint', 'context', 'outcome',
                'EquipmentModel', 'CapabilityClaim'}


def identity_descriptors(nodes, run_id):
    """Same labels offer navigation hints, not global/canonical identities."""
    groups = defaultdict(list)
    result = {}
    for node in nodes:
        if node.get('type') not in DOMAIN_TYPES:
            continue
        qualifiers = {k: deepcopy(node[k]) for k in ('variant', 'configuration', 'observedAt', 'validFrom', 'validTo') if node.get(k) is not None}
        signature = [node['type'], unicodedata.normalize('NFKC', node.get('label', '')).strip().casefold(), qualifiers]
        group = content_hash(signature)
        groups[group].append(node['id'])
        result[node['id']] = {'mentionId': f"{run_id}/{node['id']}", 'qualifiers': qualifiers,
                              'labelMatchKey': group, 'identityStatus': 'run-scoped-mention',
                              'aliases': deepcopy(node.get('aliases') or []), 'sameLabelMentions': [],
                              'notice': '同名仅用于定位；构型、时间或来源未核对时不合并为同一实体。'}
        if node.get('canonicalBinding'):
            result[node['id']]['canonicalBinding'] = deepcopy(node['canonicalBinding'])
    for desc in result.values():
        desc['sameLabelMentions'] = [n for n in groups[desc['labelMatchKey']] if f'{run_id}/{n}' != desc['mentionId']]
    return result


def relation_semantics(assertion):
    declared = assertion.get('epistemicKind')
    kinds = {'source-claim', 'analytical-inference', 'correlation', 'causal-hypothesis', 'conditional-hypothesis'}
    kind = declared if declared in kinds else ('correlation' if assertion.get('predicate') == 'associated-with' else 'analytical-inference')
    return {'kind': kind, 'origin': 'declared' if declared in kinds else 'conservative-legacy-projection',
            'causalityEstablished': False, 'truthVerified': False}


def compare_graphs(before, after, *, perspective_keys=None):
    """Compare report statements using exact labels/qualifiers, never fuzzy identity."""
    before_keys = {p['key'] for p in before.get('perspectives', [])}
    after_keys = {p['key'] for p in after.get('perspectives', [])}
    shared = before_keys & after_keys
    # Targeted successors retain gap placeholders for the other perspectives.
    # Their presence in the graph does not mean those perspectives were rerun.
    if perspective_keys is not None:
        shared &= set(perspective_keys)
    scoped = perspective_keys is not None or bool(before_keys or after_keys)
    def rows(graph):
        nodes = {n['id']: n for n in graph['nodes']}
        result = defaultdict(list)
        for a in graph.get('assertions', []):
            if scoped and a.get('perspectiveKey') not in shared:
                continue
            signature = []
            for endpoint in ('subject', 'object'):
                n = nodes[a[endpoint]]
                signature.append([n.get('type'), n.get('label'), {k: n[k] for k in
                    ('variant', 'configuration', 'observedAt', 'validFrom', 'validTo') if k in n}])
            key = content_hash([a.get('perspectiveKey'), signature, a['predicate']])
            result[key].append(a)
        return result
    old, new = rows(before), rows(after)
    added, removed, changed, ambiguous = [], [], [], []
    for key in sorted(old.keys() | new.keys()):
        left, right = old.get(key, []), new.get(key, [])
        if len(left) > 1 or len(right) > 1:
            ambiguous.append({'oldIds': [r['id'] for r in left], 'newIds': [r['id'] for r in right]})
        elif not left:
            added.append(right[0]['id'])
        elif not right:
            removed.append(left[0]['id'])
        else:
            def evidence_signature(graph, a):
                ids = set(a.get('evidenceIds', []))
                return sorted(content_hash([n.get('sourceUri'), (n.get('provenance') or {}).get('contentHash'), n.get('label')])
                              for n in graph['nodes'] if str(n.get('evidenceId') or n.get('candidateId')) in ids)
            fields = [f for f in ('conditions', 'conditionScope', 'counterArguments', 'unknowns', 'modality', 'epistemicKind')
                      if left[0].get(f) != right[0].get(f)]
            if evidence_signature(before, left[0]) != evidence_signature(after, right[0]):
                fields.append('evidence')
            if fields:
                changed.append({'oldId': left[0]['id'], 'newId': right[0]['id'], 'fields': fields})
    return {'beforeRunId': before['runId'], 'afterRunId': after['runId'],
            'beforeHash': before['contentHash'], 'afterHash': after['contentHash'],
            'added': added, 'removed': removed, 'changed': changed, 'ambiguous': ambiguous,
            'alignment': 'exact-type-label-qualifiers-within-perspective', 'comparedPerspectiveKeys': sorted(shared),
            'uncomparedPerspectiveKeys': sorted((before_keys | after_keys) - shared),
            'comparisonScope': 'targeted-perspective' if perspective_keys is not None else 'shared-perspectives', 'truthVerified': False,
            'notice': '比较的是报告投影的陈述和依据变化；标签对齐不是实体确证，也不表示现实能力发生变化。'}
