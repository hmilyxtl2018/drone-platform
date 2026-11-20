"""Bounded public-research methods. Candidates and diagnostics never certify truth.

No network, generated observations, implicit distributions, or entity merges.
All methods have an explicit applicability result, including an empty input.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime, timezone
from difflib import SequenceMatcher
import hashlib
import json
import math
import random
import re
import statistics
import unicodedata

METHOD_VERSION = 'research-methods-1.0.3'


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False, default=str).encode()).hexdigest()


def result(method, data, *, count=0, missing=(), partial=False, invalid=False):
    reasons = list(dict.fromkeys(missing))
    status = 'invalid-input' if invalid else 'not-applicable' if not count else 'partial' if reasons or partial else 'assessed'
    return {'method': method, 'methodVersion': METHOD_VERSION, 'analysisStatus': status,
        'applicability': 'inapplicable' if not count or invalid else 'limited' if reasons or partial else 'applicable',
        'sampleCount': count, 'missingInputs': reasons, 'reasonCodes': reasons,
        'truthVerified': False, **data}


def norm(text):
    return re.sub(r'\s+', ' ', unicodedata.normalize('NFKC', str(text)).casefold()).strip()


def tokens(text):
    text = norm(text)
    # Latin word tokens and Chinese character bigrams; no unavailable embedding API.
    return re.findall(r'[a-z0-9]+', text) + [s[i:i+2] for s in re.findall(r'[\u3400-\u9fff]+', text) for i in range(max(1, len(s)-1))]


def evidence_id(row):
    return row.get('evidenceId') or row.get('claimId') or row.get('candidateId')


def source_dependencies(sources):
    rows, edges = [], []
    for index, s in enumerate(sources):
        body = norm(s.get('body', s.get('markdown', '')))
        grams = {body[i:i+5] for i in range(max(0, len(body)-4))}
        # A stable 64-bit SimHash supplies candidate similarity, not independence.
        vector = [0] * 64
        for token in grams:
            bits = int(hashlib.sha256(token.encode()).hexdigest()[:16], 16)
            for bit in range(64): vector[bit] += 1 if bits & (1 << bit) else -1
        signature = sum(1 << bit for bit, value in enumerate(vector) if value > 0)
        rows.append({'id': s.get('sourceUri') or str(index), 'body': body, 'grams': grams, 'signature': signature})
    for i, a in enumerate(rows):
        for j in range(i):
            b = rows[j]
            declared = (sources[i].get('originSourceUri') == b['id'] or sources[j].get('originSourceUri') == a['id'] or
                bool(sources[i].get('originSourceUri')) and sources[i].get('originSourceUri') == sources[j].get('originSourceUri'))
            exact = bool(a['body']) and a['body'] == b['body']
            union = a['grams'] | b['grams']
            similarity = len(a['grams'] & b['grams']) / len(union) if union else 0
            hamming = (a['signature'] ^ b['signature']).bit_count()
            near = min(len(a['body']), len(b['body'])) >= 120 and similarity >= .72 and hamming <= 16
            # An explicit linked citation is recorded, but does not make the whole page a republication.
            linked = b['id'].startswith(('https://','http://')) and bool(re.search(re.escape(b['id'])+r'(?=$|[\s)\]>"\x27])',sources[i].get('body','')))
            if declared or exact or near or linked:
                edges.append({'source': a['id'], 'target': b['id'],
                    'relation': 'declared-common-origin' if declared else 'exact-copy' if exact else 'near-duplicate-candidate' if near else 'cites',
                    'status': 'declared' if declared else 'detected' if exact or linked else 'candidate',
                    'jaccard': round(similarity, 5), 'simhashDistance': hamming, 'independenceEstablished': False})
    # Conservative dependence components include candidates for sensitivity only.
    parents = list(range(len(rows)))
    def root(i):
        while parents[i] != i: i = parents[i]
        return i
    ids = {r['id']: i for i, r in enumerate(rows)}
    for edge in edges:
        a, b = root(ids[edge['source']]), root(ids[edge['target']])
        parents[a] = b
    groups = defaultdict(list)
    for i, row in enumerate(rows): groups[root(i)].append(row['id'])
    return result('normalized-text-simhash-jaccard-and-explicit-provenance', {
        'relations': edges, 'dependencyGroups': [{'groupId': 'dependency-'+digest(sorted(v))[:12], 'sourceUris': v,
            'independenceStatus': 'not-established'} for v in groups.values()],
        'verifiedIndependentCount': 0, 'parameters': {'shingleWidth': 5, 'minCharacters': 120, 'jaccard': .72, 'maxHamming': 16, 'calibration':'candidate-thresholds-not-independently-calibrated'},
        'limitations': ['Translation and paraphrase dependence may remain undetected; unmatched sources are not independent.']},
        count=len(rows), missing=[] if rows else ['no-sources'])


def entity_candidates(mentions, registry):
    output = []
    for m in mentions:
        candidates = []
        for c in registry:
            if not c.get('id') or not m.get('type') or m.get('type') != c.get('type'): continue
            if any(m.get(k) is not None and c.get(k) is not None and m[k] != c[k] for k in ('variant', 'configuration', 'version')): continue
            labels = [c.get('label', ''), *c.get('aliases', [])]
            exact = bool(norm(m.get('label', ''))) and norm(m.get('label', '')) in [norm(x) for x in labels]
            registered = bool(m.get('registryId')) and m['registryId'] == c['id']
            score = 1.0 if registered else .95 if exact else max((SequenceMatcher(None, norm(m.get('label', '')), norm(x)).ratio() for x in labels), default=0)
            if score >= .8: candidates.append({'id': c['id'], 'score': round(score, 4), 'basis': 'registered-id' if registered else 'exact-label-or-alias' if exact else 'fuzzy-label'})
        candidates.sort(key=lambda c: (-c['score'], c['id']))
        # Even a unique match remains a proposed link; never merge canonical objects.
        unique = bool(candidates) and (len(candidates) == 1 or candidates[0]['score'] - candidates[1]['score'] >= .15)
        output.append({'mentionId': m.get('id'), 'candidates': candidates[:5],
            'status': 'candidate-link' if unique else 'abstained', 'canonicalMerge': False})
    return result('type-qualifier-alias-candidate-resolution', {'mentions': output, 'abstentionCount': sum(x['status']=='abstained' for x in output),
        'registryHash': digest(registry)}, count=len(mentions), missing=['identity-registry-not-supplied'] if not registry else [])


def series_build(observations):
    groups, rejected = defaultdict(list), []
    required = ('entityId', 'metric', 'unit', 'configuration', 'conditions', 'observedAt', 'sourceUri', 'sourceArtifactId', 'contentHash')
    for index, row in enumerate(observations):
        missing = [k for k in required if row.get(k) is None or row.get(k) == '']
        value = row.get('value')
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value): missing.append('finite-scalar-value')
        if row.get('qualifier', 'unknown') != 'exact': missing.append('exact-observation-required')
        if not row.get('citationValidated'): missing.append('validated-citation-required')
        try:
            date = datetime.fromisoformat(str(row.get('observedAt', '')).replace('Z', '+00:00'))
            date = date.astimezone(timezone.utc) if date.tzinfo else date.replace(tzinfo=timezone.utc)
        except ValueError: missing.append('valid-observation-date')
        if missing:
            rejected.append({'index': index, 'evidenceId': evidence_id(row), 'reasons': missing}); continue
        key = digest({k: row.get(k) for k in ('entityId', 'metric', 'unit', 'variant', 'configuration', 'conditions')})
        groups[key].append({'time': date.isoformat(), 'value': value, 'evidenceId': evidence_id(row),
            'sourceUri': row['sourceUri'], 'sourceArtifactId': row['sourceArtifactId'], 'contentHash': row['contentHash']})
    series = []
    for key, points in groups.items():
        by_time = defaultdict(list)
        for p in points: by_time[p['time']].append(p)
        conflicts = [t for t, ps in by_time.items() if len({p['value'] for p in ps}) > 1]
        # Keep all evidence for identical observations; never average conflicting values.
        clean = [{'time': t, 'value': ps[0]['value'], 'evidence': ps} for t, ps in sorted(by_time.items()) if t not in conflicts]
        series.append({'seriesId': key, 'points': clean, 'excludedConflictingTimes': conflicts, 'comparability': 'exact-declared-context'})
    count = sum(len(s['points']) for s in series)
    reasons = (['comparable-observation-series'] if not count else []) + (['rejected-observations'] if rejected else []) + (['conflicting-same-time-observations'] if any(s['excludedConflictingTimes'] for s in series) else [])
    return result('evidence-bound-comparable-observations', {'series': series, 'rejected': rejected,
        'publicationTimeUsedAsObservation': False}, count=count, missing=reasons)


def cusum(series, parameters):
    required = ('baselineCount', 'referenceSigma', 'allowanceSigma', 'decisionSigma', 'parameterSource')
    if any(k not in parameters for k in required):
        return result('two-sided-standardized-cusum', {'series': [], 'parameters': parameters}, missing=['documented-cusum-parameters'])
    n = parameters['baselineCount']; sigma = parameters['referenceSigma']; k = parameters['allowanceSigma']; h = parameters['decisionSigma']
    if isinstance(n, bool) or not isinstance(n, int) or n < 5 or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0 for v in (sigma,k,h)) or not parameters['parameterSource']:
        return result('two-sided-standardized-cusum', {'series': [], 'parameters': parameters}, missing=['valid-cusum-parameters'], invalid=True)
    rows, count, reasons = [], 0, []
    for s in series:
        points = s.get('points', [])
        if len(points) < n + 3:
            reasons.append('insufficient-monitoring-samples'); continue
        times = [datetime.fromisoformat(p['time'].replace('Z', '+00:00')) for p in points]
        deltas = [(b-a).total_seconds() for a,b in zip(times,times[1:])]
        if not deltas or min(deltas) <= 0 or max(deltas) != min(deltas):
            reasons.append('regular-observation-interval-required'); continue
        values = [p['value'] for p in points]
        if any(isinstance(v, bool) or not isinstance(v, (int,float)) or not math.isfinite(v) for v in values): raise ValueError('CUSUM requires finite values')
        mean = statistics.mean(values[:n]); high = low = 0; alarms = []
        for index in range(n, len(values)):
            z = (values[index]-mean)/sigma
            high = max(0, high+z-k); low = min(0, low+z+k)
            if high >= h or low <= -h:
                alarms.append({'index': index, 'time': points[index]['time'], 'direction': 'up' if high >= h else 'down', 'evidence': points[index].get('evidence', [])})
                high = low = 0
        rows.append({'seriesId': s.get('seriesId'), 'baselineMean': mean, 'alarms': alarms, 'sampleCount': len(points),
            'interpretation': 'Sequential monitoring alarms; not estimated break dates, significance or future forecasts.'})
        count += len(points)
    return result('two-sided-standardized-cusum', {'series': rows, 'parameters': parameters,
        'assumptions': ['stable baseline mean and documented reference sigma', 'regular sampling', 'dependence requires independent calibration']},
        count=count, missing=reasons or ([] if rows else ['comparable-observation-series']))


def claim_relations(claims):
    relations, incomplete = [], []
    fields = ('subject', 'predicate', 'object', 'time', 'conditions', 'configuration', 'modality', 'negated')
    for c in claims:
        if not all(k in c for k in fields) or not evidence_id(c) or type(c.get('negated')) is not bool: incomplete.append(evidence_id(c))
    valid = [c for c in claims if evidence_id(c) not in incomplete]
    for i, a in enumerate(valid):
        for b in valid[:i]:
            same_sp = all(a[k] == b[k] for k in ('subject', 'predicate'))
            if not same_sp: continue
            context = all(a[k] == b[k] and a[k] is not None and a[k] not in ('','unknown','unspecified') for k in ('time', 'conditions', 'configuration', 'modality'))
            same_object = a['object'] == b['object']
            relation = ('contradiction-candidate' if a['negated'] != b['negated'] else 'same-proposition') if context and same_object else 'unknown'
            relations.append({'left': evidence_id(a), 'right': evidence_id(b), 'relation': relation,
                'basis': 'exact-atomic-fields', 'adjudicated': False})
    return result('atomic-proposition-context-and-polarity', {'relations': relations, 'incompleteClaimIds': incomplete,
        'semanticReviewer': {'status': 'not-configured', 'crossLanguageEntailment': False},
        'limitations': ['Different wording is unknown; this rule method is not NLI or an independent semantic adjudicator.']},
        count=len(valid), missing=['atomic-claim-fields'] if incomplete or not valid else [])


def rank_evidence(query, sources, *, limit=10, diversity=.75):
    documents = [Counter(tokens(s.get('body', s.get('markdown', s.get('quote', ''))))) for s in sources]
    q = set(tokens(query)); lengths = [sum(d.values()) for d in documents]
    average = sum(lengths)/len(lengths) if lengths else 1
    df = Counter(t for d in documents for t in d)
    scored = []
    for i, d in enumerate(documents):
        score = sum(math.log(1+(len(documents)-df[t]+.5)/(df[t]+.5)) * d[t]*2.2/(d[t]+1.2*(.25+.75*lengths[i]/max(average,1))) for t in q if d[t])
        scored.append((i, score))
    maximum = max((s for _,s in scored), default=1) or 1
    selected, pending = [], list(scored)
    while pending and len(selected) < limit:
        def mmr(item):
            a = set(documents[item[0]])
            similarity = max((len(a & set(documents[j])) / max(1,len(a | set(documents[j]))) for j,_ in selected), default=0)
            return diversity*item[1]/maximum-(1-diversity)*similarity
        chosen = max(pending, key=lambda item:(mmr(item),-item[0])); pending.remove(chosen)
        if chosen[1] > 0: selected.append(chosen)
    return result('bm25-with-mmr', {'ranked': [{'index': i, 'sourceUri': sources[i].get('sourceUri'), 'evidenceId': evidence_id(sources[i]), 'score': round(s,6)} for i,s in selected],
        'parameters': {'k1': 1.2, 'b': .75, 'diversity': diversity, 'limit': limit},
        'embeddingStatus': 'not-configured', 'rerankerStatus': 'not-configured', 'rrfStatus': 'not-applicable-single-retriever',
        'relevanceVerified': False}, count=len(sources), missing=[] if q and sources else ['query-and-saved-corpus-required'])


def coverage(questions, evidence, findings, searches):
    ids = {str(evidence_id(e)) for e in evidence if evidence_id(e)}
    rows = []
    for q in questions:
        key = q.get('key'); fs = [f for f in findings if f.get('perspectiveKey') == key]
        supported = [f for f in fs if f.get('status') == 'candidate' and f.get('supportingClaims') and set(map(str,f['supportingClaims'])).issubset(ids)
                     and (not f.get('findingId') or 'question:' + str(key) in f.get('answeredQuestionIds', []))]
        relevant = rank_evidence(q.get('question', q.get('name','')), evidence)['ranked']
        attempts = [s for s in searches if s.get('perspectiveKey') == key]
        reasons={s.get('reason') for s in attempts}
        status = ('supported' if supported else 'not-supported' if evidence and relevant else
            'read-failed' if reasons & {'web-scrape-no-content','web-acquisition-failed'} else
            'no-retrieval-hit' if reasons & {'web-search-no-results','web-search-excluded-results'} else
            'budget-exhausted' if 'acquisition-budget-exhausted' in reasons else 'not-searched' if not attempts else 'unknown')
        rows.append({'questionKey': key, 'status': status, 'supportingEvidenceIds': sorted({x for f in supported for x in f['supportingClaims']}),
            'candidateEvidenceIds': [r['evidenceId'] for r in relevant], 'searchAttemptCount': len(attempts),
            'meaning': 'Lexical candidates are not evidence support.'})
    return result('question-evidence-support-matrix', {'matrix': rows,
        'supportedQuestionCount': sum(r['status']=='supported' for r in rows), 'questionCount': len(rows)}, count=len(rows),
        missing=['uncovered-questions'] if any(r['status']!='supported' for r in rows) else [])


def hypothesis_compare(hypotheses, assessments, evidence, dependency_groups=()):
    ids = {str(evidence_id(e)) for e in evidence if evidence_id(e)}; hs = {h['id'] for h in hypotheses}
    matrix, rejected = [], []
    for a in assessments:
        valid = a.get('hypothesisId') in hs and a.get('evidenceId') in ids and a.get('relation') in {'consistent','inconsistent','neutral','unknown'} and bool(a.get('rationale'))
        (matrix if valid else rejected).append(deepcopy(a))
    groups={uri:g['groupId'] for g in dependency_groups for uri in g.get('sourceUris',[])}
    sources={str(evidence_id(e)):groups.get(e.get('sourceUri'),'unverified-origin:'+str(e.get('sourceUri'))) for e in evidence}
    for a in matrix: a['dependencyGroup']=sources[a['evidenceId']]
    # No likelihoods, weighted probability or ranking from unadjudicated assessments.
    return result('ach-evidence-compatibility-matrix', {'hypotheses': hypotheses, 'matrix': matrix, 'rejected': rejected,
        'summary': [{'hypothesisId': h, 'consistentGroups': len({a['dependencyGroup'] for a in matrix if a['hypothesisId']==h and a['relation']=='consistent'}),
            'inconsistentGroups': len({a['dependencyGroup'] for a in matrix if a['hypothesisId']==h and a['relation']=='inconsistent'})} for h in sorted(hs)],
        'probabilitiesComputed': False, 'winnerSelected': False}, count=len(matrix),
        missing=['evidence-linked-hypothesis-assessments'] if not matrix else ['independence-not-established'] )


def scenario_consistency(scenarios, constraints):
    rows, evaluated = [], 0
    for scenario in scenarios:
        assignments = scenario.get('assignments', {})
        checks = []
        for rule in constraints:
            required = rule.get('when', {})
            if not required or not rule.get('evidenceIds'): continue
            if all(assignments.get(k) == v for k,v in required.items()):
                checks.append({'constraintId':rule.get('id'), 'status': 'incompatible' if rule.get('forbidden') else 'compatible', 'evidenceIds':rule['evidenceIds']}); evaluated += 1
        rows.append({'scenarioId':scenario.get('id'), 'checks':checks,
            'status':'incompatible' if any(c['status']=='incompatible' for c in checks) else 'unassessed' if not checks else 'no-declared-incompatibility'})
    return result('declared-cross-consistency-constraints', {'scenarios':rows, 'constraintCount':len(constraints),
        'causalityEstablished':False}, count=evaluated, missing=['evidence-bound-cross-consistency-constraints'] if not evaluated else ['constraint-coverage-incomplete'])


def robustness(findings, evidence, dependency_groups, series=(), parameters=None):
    by_id = {str(evidence_id(e)):e for e in evidence if evidence_id(e)}
    grouped = {uri:g['groupId'] for g in dependency_groups for uri in g.get('sourceUris',[])}
    rows = []
    for f in findings:
        support = set(map(str, f.get('supportingClaims',[]))) & by_id.keys()
        groups = defaultdict(set)
        for eid in support:
            uri = by_id[eid].get('sourceUri')
            groups[grouped.get(uri, 'unknown-origin:'+str(uri))].add(eid)
        variants = [{'removedGroup':g,'retainedEvidenceIds':sorted(support-ids),'citationCoverageRetained':bool(support-ids)} for g,ids in sorted(groups.items())]
        rows.append({'findingId':f.get('findingId') or f.get('perspectiveKey'), 'dependencyGroupCount':len(groups),
            'leaveOneGroupOut':variants, 'citationFragile': bool(variants) and any(not v['citationCoverageRetained'] for v in variants),
            'semanticConclusionReevaluated':False, 'independenceEstablished':False})
    perturbations=[]
    if series and parameters:
        for scale in (.8,1.0,1.2):
            if isinstance(parameters.get('decisionSigma'),(int,float)):
                changed={**parameters,'decisionSigma':parameters['decisionSigma']*scale}
                check=cusum(series,changed)
                perturbations.append({'kind':'decision-threshold','scale':scale,'analysisStatus':check['analysisStatus'],'series':check['series']})
        for fraction in (.8,1.0):
            truncated=[{**s,'points':s['points'][:max(0,int(len(s['points'])*fraction))]} for s in series]
            check=cusum(truncated,parameters)
            perturbations.append({'kind':'observation-window','fraction':fraction,'analysisStatus':check['analysisStatus'],'series':check['series']})
    return result('leave-one-dependency-group-out-citation-sensitivity', {'findings':rows,
        'modelCalls':0, 'semanticFlipStatus':'not-evaluated', 'timeWindowStatus':'evaluated-numeric-monitor-only' if perturbations else 'not-evaluated-no-applicable-series',
        'perturbations':perturbations,
        'limitations':['Remaining citations do not establish that the conclusion survives a semantic reevaluation.']},
        count=sum(bool(r['leaveOneGroupOut']) for r in rows), missing=['semantic-reevaluation-not-performed'])


def maturity(objects, criteria, evidence):
    ids = {str(evidence_id(e)) for e in evidence if evidence_id(e)}
    adopted = bool(criteria.get('standardId') and criteria.get('version') and criteria.get('sourceArtifactId') and criteria.get('contentHash') and criteria.get('criteria'))
    rows = []
    for obj in objects:
        verified, gaps = [], []
        for rule in criteria.get('criteria', []):
            supplied = [a for a in obj.get('assessments',[]) if a.get('criterionId')==rule.get('id')]
            ok = any(a.get('verdict')=='met' and a.get('reviewerId') and a.get('evidenceIds') and set(a['evidenceIds']).issubset(ids) and a.get('environment')==rule.get('environment') for a in supplied)
            (verified if ok else gaps).append(rule.get('id'))
        rows.append({'objectId':obj.get('id'), 'metCriteria':verified,'missingCriteria':gaps,
            'rating':None,'status':'candidate-checklist' if adopted else 'not-assessable'})
    return result('versioned-evidence-and-environment-maturity-checklist', {'objects':rows,
        'criteriaHash':digest(criteria), 'standardAdopted':adopted, 'trlOrMrlCertified':False,
        'empiricalModels':{'reliability':'requires-test-and-censoring-records','processCapability':'requires-stable-process-measurements'}},
        count=len(objects) if adopted else 0, missing=['independent-standard-and-item-review-required'] if adopted else ['versioned-authoritative-standard','criterion-evidence-and-validation-environment'])


def evaluate_methods(cases, baseline_version, candidate_version, *, seed=1701):
    accepted, rejected = [], []
    required = ('caseId','reportGroup','sourceGroup','inputHash','labelHash','adjudicatorId','labelledAt','frozenAt','baselineScore','candidateScore')
    seen = set()
    for c in cases:
        reasons = [k for k in required if c.get(k) is None]
        if c.get('caseId') in seen: reasons.append('duplicate-case')
        seen.add(c.get('caseId'))
        if not c.get('independentAdjudication') or c.get('split')!='holdout': reasons.append('independent-heldout-label-required')
        if c.get('baselineVersion')!=baseline_version or c.get('candidateVersion')!=candidate_version: reasons.append('exact-version-required')
        if c.get('usedForDevelopment'): reasons.append('development-leakage')
        for field in ('baselineScore','candidateScore'):
            v = c.get(field)
            if isinstance(v,bool) or not isinstance(v,(float,int)) or not math.isfinite(v) or not 0 <= v <= 1: reasons.append('normalized-finite-score-required')
        for field in ('inputHash','labelHash'):
            if not re.fullmatch(r'[a-f0-9]{64}',str(c.get(field,''))): reasons.append('sha256-required')
        try:
            if datetime.fromisoformat(c['labelledAt'].replace('Z','+00:00')) > datetime.fromisoformat(c['frozenAt'].replace('Z','+00:00')):
                reasons.append('labels-not-frozen-before-evaluation')
        except (KeyError,ValueError,TypeError): reasons.append('valid-adjudication-dates-required')
        (rejected if reasons else accepted).append({'caseId':c.get('caseId'),'reasons':reasons} if reasons else c)
    # Connected report/source groups are one cluster; never bootstrap fragments independently.
    components = []
    for c in accepted:
        keys = {'report:'+c['reportGroup'],'source:'+c['sourceGroup']}
        overlapping = [g for g in components if g['keys'] & keys]
        merged = {'keys':set(keys),'values':[c['candidateScore']-c['baselineScore']]}
        for g in overlapping: merged['keys'] |= g['keys']; merged['values'] += g['values']; components.remove(g)
        components.append(merged)
    deltas = [statistics.mean(g['values']) for g in components]
    enough = len(deltas) >= 20; interval = None
    if enough:
        rng = random.Random(seed)
        samples = sorted(statistics.mean(rng.choices(deltas,k=len(deltas))) for _ in range(2000))
        interval = [samples[49],samples[1949]]
    return result('paired-cluster-bootstrap-heldout-evaluation', {'acceptedCaseCount':len(accepted),'rejected':rejected,
        'independentClusterCount':len(deltas), 'meanDelta':statistics.mean(deltas) if deltas else None,
        'interval95':interval,'seed':seed,'resamples':2000 if enough else 0,
        'baselineVersion':baseline_version,'candidateVersion':candidate_version,
        'publicationEligible':False, 'qualityImprovementProven':False, 'positiveHeldoutDelta':bool(interval and interval[0] > 0),
        'publicationReason':'separate independent review, metric coverage, cost and regression gates required'},
        count=len(accepted), missing=[] if enough else ['at-least-20-independent-labelled-holdout-clusters'])


def retrieval_metrics(ranked_ids, relevance, k=10):
    """Use externally supplied graded labels; unlabelled candidates stay unjudged."""
    if not relevance: return {'analysisStatus':'not-applicable','reason':'independent-relevance-labels-required'}
    if len(ranked_ids)!=len(set(ranked_ids)): raise ValueError('ranked identities must be unique')
    if any(type(v) is not int or not 0 <= v <= 3 for v in relevance.values()): raise ValueError('relevance grades must be 0..3')
    selected=ranked_ids[:k]; relevant={key for key,v in relevance.items() if v>0}
    dcg=sum((2**relevance.get(key,0)-1)/math.log2(i+2) for i,key in enumerate(selected))
    ideal=sum((2**v-1)/math.log2(i+2) for i,v in enumerate(sorted(relevance.values(),reverse=True)[:k]))
    return {'recallAtK':len(set(selected)&relevant)/len(relevant) if relevant else None,
        'ndcgAtK':dcg/ideal if ideal else None,'k':k,'judgedCount':sum(key in relevance for key in selected),
        'unjudgedIds':[key for key in selected if key not in relevance], 'analysisStatus':'partial' if any(key not in relevance for key in selected) else 'assessed'}
