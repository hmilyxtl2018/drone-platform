"""Read-only supervision projection, using the caller's owner-scoped DB snapshot.

No providers, task creation, inferred grant lineage, or synthetic quality score.
Calls are counted once by call ID; budgets use latest settlement per reservation.
"""
from collections import Counter, defaultdict
from .ontology_explorer import evidence_index, explorer_graph
from .reasoning_repository import ReasoningRepository
from .reasoning_gates import relation_review_binding

DONE = {'succeeded', 'completed', 'completed-with-gaps', 'requires-human-review'}
TERMINAL = DONE | {'failed', 'blocked', 'cancelled'}


def _tables(c):
    c.execute('SELECT table_name FROM information_schema.tables WHERE table_schema=current_schema()')
    return {r['table_name'] for r in c.fetchall()}


def _seconds(start, end):
    return max(0, round((end - start).total_seconds())) if start and end else None


def _calls(rows, available):
    if not available:
        return {'total': None, 'succeeded': None, 'failed': None, 'retries': None}
    unique = {r['call_id']: r for r in rows}.values()
    counts = Counter(r.get('status') for r in unique)
    return {'total': len(unique), 'succeeded': counts['succeeded'], 'failed': counts['failed'],
            'retries': sum(r.get('retry_count') or 0 for r in unique)}


def _outcome(c, run, tables):
    output = run.get('output_json') or {}
    report = output.get('judgmentReport') or output
    if not (report.get('findings') or report.get('evidence') or report.get('ontology')):
        return None
    rid = run['run_id']
    graph = explorer_graph({**report, 'runId': rid, 'ontologyGraph': report.get('ontology') or {}})
    evidence = evidence_index(report)
    findings = [f for f in report.get('findings', []) if isinstance(f, dict)]
    relations = [{**r, 'perspectiveKey': f.get('perspectiveKey'),
                  **({'findingId': f['findingId']} if f.get('findingId') else {})}
                 for f in findings for r in f.get('relations', [])]
    proof_available = {'agent_model_call', 'agent_run_artifact', 'agent_action_invocation'} <= tables
    repo = ReasoningRepository()
    finding_proofs = repo.validate_finding_reviews(rid, findings, cursor=c) if proof_available else []
    relation_proofs = repo.validate_support_reviews(rid, relations, cursor=c) if proof_available else []
    # The repository binds proofs to immutable generation/review artifacts.
    supported = {(p.get('perspectiveKey'), p.get('findingId') or p.get('statement')) for p in finding_proofs}
    proof_hashes = {p['bindingHash'] for p in relation_proofs}
    reviewed = {relation_review_binding(r): r for r in relations if relation_review_binding(r) in proof_hashes}
    perspectives = report.get('perspectives') or (run.get('input_json') or {}).get('perspectives') or []
    rows = []
    for p in perspectives:
        key = p['key']
        coverage = [q for q in report.get('questionCoverage', []) if q.get('perspectiveKey') == key]
        questions = {q['questionId'] for q in coverage if q.get('questionId')} or {'question:' + key}
        proofs = [f for f in finding_proofs if f.get('perspectiveKey') == key]
        answered = {q['questionId'] for f in proofs for q in f.get('questionVerdicts', [])
                    if q.get('verdict') == 'answered'} & questions
        relevant = {eid for f in findings if f.get('perspectiveKey') == key
                    for eid in (f.get('supportingClaims') or f.get('evidenceIds') or []) if eid in evidence}
        gaps = [g for g in report.get('evidenceGaps', []) if g.get('perspectiveKey') == key]
        nodes = [n for n in graph.get('nodes', []) if n.get('perspectiveKey') == key or
                 key in n.get('perspectiveKeys', []) or n['id'] in ('question:' + key, 'gap:' + key)]
        target = next((n for n in nodes if n['id'] == 'question:' + key), next(iter(nodes), None))
        gap_node = next((n for n in nodes if n.get('type') in {'unknown', 'evidence-gap', 'EvidenceGap'}), None)
        rows.append({'key': key, 'name': p.get('name') or key, 'question': p.get('question'),
            'questionCount': len(questions), 'answeredQuestions': len(answered) if proof_available else None,
            'supportedFindings': sum(k == key for k, _ in supported) if proof_available else None,
            'reviewedRelations': sum(v.get('perspectiveKey') == key for v in reviewed.values()) if proof_available else None,
            'linkedEvidence': len(relevant), 'gapCount': len(gaps),
            'objectRef': {'kind': 'node', 'id': target['id']} if target else None,
            'gapRef': {'kind': 'node', 'id': gap_node['id']} if gap_node else None})
    return {'runId': rid, 'evidenceExcerpts': len(evidence),
        'structuralEdges': sum(e.get('kind') != 'assertion' for e in graph.get('edges', [])),
        'assertions': len(graph.get('assertions', [])),
        'supportedFindings': len(supported) if proof_available else None,
        'reviewedRelations': len(reviewed) if proof_available else None,
        'supportedPerspectives': sum(bool(p['supportedFindings']) for p in rows) if proof_available else None,
        'perspectiveCount': len(rows), 'gapCount': sum(p['gapCount'] for p in rows),
        'perspectives': rows, 'factVerification': 'unverified', 'humanReview': 'see-report-opinions',
        'proofAvailable': proof_available}


def project(c, mission_id, grant):
    """Only call after owner selection. All SELECTs stay in the same snapshot."""
    tables = _tables(c)
    c.execute('SELECT transaction_timestamp() AS at')
    now = c.fetchone()['at']
    c.execute('''SELECT r.*,b.proposal_id FROM agent_run r
        JOIN agent_autopilot_run_binding b USING(run_id) WHERE b.mission_id=%s''', (mission_id,))
    runs = {r['run_id']: dict(r) for r in c.fetchall()}
    c.execute('''SELECT proposal_id,run_id,attempt,created_at FROM agent_autopilot_proposal
        WHERE mission_id=%s AND run_id IS NOT NULL ORDER BY created_at,proposal_id''', (mission_id,))
    roots = [dict(p) for p in c.fetchall() if p['run_id'] in runs]
    result = {'schemaVersion': 'mission-metrics.v1', 'available': bool(roots), 'queriedAt': now,
        'scope': {'kind': 'current-mission', 'missionId': mission_id, 'includesBoundChildren': True,
                  'from': min((r.get('created_at') for r in runs.values() if r.get('created_at')), default=None),
                  'to': now, 'lineageAggregated': False}, 'rounds': []}
    if not roots:
        return result
    run_ids = list(runs)
    steps_available, models_available, tools_available = (t in tables for t in
        ('agent_step_run', 'agent_model_call', 'agent_tool_call'))
    steps, models, tools, actions = [], [], [], []
    if steps_available:
        c.execute('''SELECT step_run_id,run_id,step_key,status,attempt,started_at,finished_at,created_at
            FROM agent_step_run WHERE run_id=ANY(%s) ORDER BY attempt,created_at,step_run_id''', (run_ids,))
        steps = list(c.fetchall())
    if models_available:
        c.execute('''SELECT call_id,run_id,step_run_id,status,retry_count FROM agent_model_call
            WHERE run_id=ANY(%s)''', (run_ids,))
        models = list(c.fetchall())
    if tools_available:
        c.execute('''SELECT call_id,run_id,step_run_id,status,retry_count FROM agent_tool_call
            WHERE run_id=ANY(%s)''', (run_ids,))
        tools = list(c.fetchall())
    if 'agent_action_invocation' in tables:
        c.execute('''SELECT run_id,parent_step_key,child_run_id FROM agent_action_invocation
            WHERE run_id=ANY(%s)''', (run_ids,))
        actions = list(c.fetchall())
    c.execute('''SELECT b.proposal_id,COALESCE(s.effective_json,r.charges_json) AS counters
        FROM agent_budget_reservation r JOIN agent_autopilot_run_binding b USING(run_id)
        LEFT JOIN LATERAL (SELECT effective_json FROM agent_budget_settlement
            WHERE reservation_id=r.reservation_id ORDER BY revision DESC LIMIT 1) s ON true
        WHERE b.mission_id=%s''', (mission_id,))
    budgets = defaultdict(Counter)
    for row in c.fetchall():
        budgets[row['proposal_id']].update(row['counters'])
    for number, proposal in enumerate(roots, 1):
        run = runs[proposal['run_id']]
        result['rounds'].append({'number': number, 'runId': run['run_id'], 'status': run['status'],
            'startedAt': run.get('started_at'), 'completedAt': run.get('completed_at'),
            'used': dict(budgets[proposal['proposal_id']]), 'outcome': _outcome(c, run, tables)})
    root = runs[roots[-1]['run_id']]
    scope_ids = {r['run_id'] for r in runs.values() if r['proposal_id'] == roots[-1]['proposal_id']}
    latest_steps = [s for s in steps if s['run_id'] == root['run_id']]
    profile = {}
    if 'agent_run_profile_snapshot' in tables:
        c.execute('SELECT profile_json FROM agent_run_profile_snapshot WHERE run_id=%s', (root['run_id'],))
        saved = c.fetchone()
        profile = (saved or {}).get('profile_json') or {}
    baseline = (profile.get('executionBaseline') or {}).get('graph') or {}
    specs = baseline.get('nodes') or [{'id': s['key'], 'name': s.get('name')} for s in profile.get('steps', [])]
    if not specs:
        specs = [{'id': k, 'name': k} for k in dict.fromkeys(s['step_key'] for s in latest_steps)]
    # Map delegated descendants to their root stage; never add shared-root totals.
    parents = defaultdict(set)
    for a in actions:
        if a['run_id'] == root['run_id'] and a['child_run_id'] in scope_ids:
            parents[a['child_run_id']].add(a['parent_step_key'])
    for _ in range(len(scope_ids)):
        changed = False
        for a in actions:
            if a['run_id'] in parents and a['child_run_id'] in scope_ids:
                before = len(parents[a['child_run_id']])
                parents[a['child_run_id']].update(parents[a['run_id']])
                changed |= before != len(parents[a['child_run_id']])
        if not changed:
            break
    by_step_id = {s['step_run_id']: s['step_key'] for s in latest_steps}
    def at_stage(call, key):
        if call['run_id'] == root['run_id']:
            return by_step_id.get(call['step_run_id']) == key
        return parents.get(call['run_id']) == {key}
    stages = []
    for spec in specs:
        key = spec['id']
        attempts = [s for s in latest_steps if s['step_key'] == key]
        latest = attempts[-1] if attempts else {}
        start = latest.get('started_at')
        name = '发布准备' if key == 'human-review' and (root.get('input_json') or {}).get('automationMode') in {'expert-draft', 'unattended-draft'} else spec.get('name') or key
        stages.append({'key': key, 'name': name, 'status': latest.get('status', 'not-recorded'),
            'elapsedSeconds': _seconds(start, latest.get('finished_at') or (now if latest.get('status') == 'running' else None)),
            'attempts': len(attempts) if steps_available else None,
            'repeats': max(0, len(attempts) - 1) if steps_available else None,
            'models': _calls([m for m in models if at_stage(m, key)], models_available),
            'tools': _calls([t for t in tools if at_stage(t, key)], tools_available),
            'childFailures': sum(runs[r]['status'] in {'failed', 'blocked'} for r in parents if parents[r] == {key})})
    stage_models = sum(s['models']['total'] or 0 for s in stages)
    stage_tools = sum(s['tools']['total'] or 0 for s in stages)
    scoped_models = _calls([m for m in models if m['run_id'] in scope_ids], models_available)
    scoped_tools = _calls([t for t in tools if t['run_id'] in scope_ids], tools_available)
    current = root.get('current_step_key')
    result.update(latestRunId=root['run_id'], outcome=result['rounds'][-1]['outcome'], stages=stages,
        execution={'status': root['status'], 'currentStepKey': current,
            'currentStepName': next((s['name'] for s in stages if s['key'] == current), current),
            'elapsedSeconds': _seconds(root.get('started_at'), root.get('completed_at') or (now if root['status'] == 'running' else None)),
            'updatedAt': root.get('updated_at'), 'completedStages': sum(s['status'] in DONE for s in stages) if steps_available else None,
            'stageCount': len(specs) or None, 'stagesWithGaps': sum(s['status'] == 'completed-with-gaps' for s in stages) if steps_available else None,
            'boundChildCount': len(scope_ids) - 1,
            'childFailures': sum(runs[r]['status'] in {'failed', 'blocked'} for r in scope_ids if r != root['run_id']),
            'models': scoped_models, 'tools': scoped_tools,
            'unattributedModels': None if not models_available else scoped_models['total'] - stage_models,
            'unattributedTools': None if not tools_available else scoped_tools['total'] - stage_tools})
    return result
