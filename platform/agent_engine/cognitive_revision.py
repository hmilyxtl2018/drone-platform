"""One evidence-targeted successor using its original frozen authority/budget.

The service derives all input from trusted server records. It does not accept
caller patches, reset counters, extend authority, copy previous conclusions, or
publish anything. An honest unknown or not-applicable result is not a retry
request. The worker can dispatch the returned created Run in its existing pool.
"""
from __future__ import annotations

from copy import deepcopy
import math
import re

from psycopg2.extras import Json

from .cognitive_identity import APPLICATION_KEY
from .cognitive_contracts import digest, validate_schema, validate_task_input
from .repository import PostgresRepository, as_json


EVIDENCE_GAP_KINDS = frozenset({'missing-source', 'insufficient-coverage', 'unsupported-relation'})
NON_REPAIRABLE = re.compile(r'(?i)(not[- ]applicable|不适用|无需适用|时间.{0,12}未知|日期.{0,12}未知|'
                           r'有效期.{0,12}未知|unknown.{0,12}(time|date|applicability)|unexecuted[- ]check|'
                           r'\bno\s+valid\s+time\s+scope\b|'
                           r'\bno\s+compatible\s+records\s+were\s+available\s+for\s+this\s+installed\s+check\b|'
                           r'\bsupply\s+effective\s+(?:and\s+)?observation\s+times\b)')


def repairable_gaps(output):
    """Select source-retrievable deficiencies, retaining typed impact/claim IDs."""
    result = []
    for gap in (output or {}).get('gaps', []):
        if not isinstance(gap, dict) or gap.get('kind') not in EVIDENCE_GAP_KINDS:
            continue
        validate_schema('Gap', gap)
        text = ' '.join(str(gap.get(k, '')) for k in ('description', 'suggestedNextStep'))
        if NON_REPAIRABLE.search(text):
            continue
        result.append(deepcopy(gap))
    return result[:100]


def _document(value):
    return as_json(value) or {}


def traversal_minimum(profile):
    """Conservative one-pass floor derived from the frozen required DAG.

    No retries are budgeted as mandatory. Professional branches use their
    captured Action manifests rather than today's Profile. Per-call token
    estimates use the frozen node ceiling; actual dispatch still reserves its
    real request bound and can stop when the root remainder is exhausted.
    """
    graph = (profile.execution_baseline or {}).get('graph') or {}
    actions = (profile.capability_graph or {}).get('actions') or {}
    result = dict(model_calls=0, tool_calls=0, http_calls=0,
                  search_calls=1, scrape_calls=1, input_tokens=0, output_tokens=0)
    for node in graph.get('nodes', []):
        if not node.get('required', True):
            continue
        budget = node.get('budget') or {}
        model_ceiling = int(budget.get('model_calls', 0))
        calls = 1 if node.get('executionKind') == 'llm' else 0
        child = actions.get(node.get('childAgentKey')) or {}
        if child:
            policies = _document((child.get('policies') or {}).get('agent_model_policy', {}).get('config_json'))
            node_policies = (policies.get('executionBaseline') or {}).get('nodePolicies') or {}
            calls = sum(p.get('executionKind') == 'llm' for p in node_policies.values())
            if not calls and model_ceiling:
                calls = 1  # Captured legacy extraction Action has one model stage.
            result['tool_calls'] += len(child.get('links') or [])
        else:
            result['tool_calls'] += len(node.get('tools') or [])
            if node.get('childAgentKey') and model_ceiling:
                # Fail conservatively for a historical snapshot whose graph
                # predates the complete evidence Action manifests.
                calls = max(calls, 1)
        calls = min(calls, model_ceiling)
        result['model_calls'] += calls
        for counter in ('input_tokens', 'output_tokens'):
            if calls:
                result[counter] += math.ceil(int(budget.get(counter, 0)) / max(1, model_ceiling)) * calls
    result['http_calls'] = result['model_calls'] + result['search_calls'] + result['scrape_calls']
    if result['model_calls'] == 0 or result['tool_calls'] == 0:
        raise ValueError('cognitive successor requires a complete frozen DAG')
    return result


def _skip(reason, run_id=None):
    return {'created': False, 'runId': run_id, 'reason': reason}


def create_successor(repo, run_id):
    """Create at most one shared-budget successor; never dispatch a new root."""
    if not isinstance(run_id, str) or not run_id:
        raise ValueError('source Run identity required')
    # The loader verifies immutable bytes and baseline/skill hashes. No live
    # Agent registration or caller-supplied Profile participates in this path.
    profile = repo.load_profile_snapshot(run_id)
    if profile.agent_key != APPLICATION_KEY:
        return _skip('not-cognitive-application')
    minimum = traversal_minimum(profile)
    with repo.connection_factory() as con:
        with con.cursor() as c:
            c.execute('SELECT r.*,m.root_run_id,m.parent_run_id FROM agent_run r '
                      'LEFT JOIN agent_run_budget_member m USING(run_id) WHERE r.run_id=%s', (run_id,))
            parent = c.fetchone()
            if not parent or not parent.get('root_run_id'):
                return _skip('source-budget-unavailable')
            root = parent['root_run_id']
            c.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', ('cognitive-revision:' + root,))
            c.execute('SELECT child_run_id FROM agent_cognitive_revision WHERE root_run_id=%s', (root,))
            prior = c.fetchone()
            if prior:
                return _skip('successor-already-created', prior['child_run_id'])
            if root != run_id or parent.get('parent_run_id'):
                return _skip('successor-is-not-a-new-root')
            if parent['status'] != 'completed-with-gaps':
                return _skip('source-has-no-delivered-gaps')
            gaps = repairable_gaps(_document(parent.get('output_json')))
            if not gaps:
                return _skip('no-source-repairable-gaps')
            original = _document(parent['input_json'])
            if original.get('revisionAttempt') or original.get('cognitiveRevisionOf'):
                return _skip('revision-limit-reached')
            from .cognitive_admission import runtime_payload
            validate_task_input(runtime_payload(original))
            c.execute('SELECT a.principal_id,a.input_json,a.content_hash FROM agent_cognitive_admission a '
                      'JOIN agent_cognitive_admission_use u USING(admission_id) WHERE u.run_id=%s', (root,))
            admission = c.fetchone()
            if (not admission or digest(admission['input_json']) != admission['content_hash']
                    or digest(original) != admission['content_hash']
                    or admission['principal_id'] != original['budgetRef']['ownerId']):
                raise PermissionError('source cognitive admission is not authentic')
            c.execute('SELECT owner_principal_id,attribution_status FROM agent_run_origin WHERE run_id=%s', (root,))
            owner = c.fetchone()
            if (not owner or owner['owner_principal_id'] != admission['principal_id']
                    or owner['attribution_status'] not in {'recorded', 'derived'}):
                raise PermissionError('source cognitive ownership is not recorded')
            # Maintain the global lock order mission -> root budget -> intent.
            from .mission_budget import lock_settlement
            lock_settlement(c, root)
            c.execute('SELECT limits_json,used_json FROM agent_run_budget WHERE root_run_id=%s FOR NO KEY UPDATE', (root,))
            budget = c.fetchone()
            limits, used = _document(budget['limits_json']), _document(budget['used_json'])
            if any(int(limits.get(k, 0)) - int(used.get(k, 0)) < v for k, v in minimum.items()):
                return _skip('remaining-shared-budget-below-traversal-minimum')
            from .task_intent import assert_dispatch
            from .phase3_repository import BudgetExceededError
            try:
                chain = assert_dispatch(c, root, operation='delegate', charges=minimum)
                if not chain:
                    return _skip('task-intent-unrecorded')
                assert_dispatch(c, root, operation='analyze')
                assert_dispatch(c, root, operation='search-public')
            except (PermissionError, BudgetExceededError):
                return _skip('task-intent-cannot-dispatch-successor')
            c.execute('SELECT profile_hash FROM agent_run_profile_snapshot WHERE run_id=%s', (run_id,))
            parent_hash = c.fetchone()['profile_hash']
            data = deepcopy(original)
            data.update(parentRunId=run_id, rootRunId=root, cognitiveRevisionOf=run_id,
                        revisionAttempt=1, previousGaps=gaps)
            # This Run-owned event also acquires the normal worker fence for
            # the original lease/control context until the transaction commits.
            # A stale worker cannot create a descendant after lease revocation.
            repo.event(c, root, 'cognitive.successor-admitted', None,
                       {'revisionAttempt': 1, 'gapIds': [g['gapId'] for g in gaps],
                        'sharedRootRunId': root, 'minimumTraversalBudget': minimum})
            # PostgresRepository is intentional: Phase3Repository's application
            # path creates an independent budget/admission. We instead join the
            # original account in this same transaction, keeping all snapshots,
            # task/goal ancestry and origin records from normal Run persistence.
            from .run_origin import origin_context
            with origin_context(source_kind='delegation', source_ref=run_id, parent_run_id=run_id):
                child = PostgresRepository.create_run(repo, profile, data, _cursor=c)
            c.execute('SELECT profile_hash FROM agent_run_profile_snapshot WHERE run_id=%s', (child,))
            if c.fetchone()['profile_hash'] != parent_hash:
                raise PermissionError('successor changed its frozen Profile')
            c.execute('INSERT INTO agent_run_budget_member(run_id,root_run_id,parent_run_id) VALUES(%s,%s,%s)',
                      (child, root, run_id))
            c.execute('INSERT INTO agent_cognitive_revision(root_run_id,parent_run_id,child_run_id,'
                      'parent_profile_hash,input_json,content_hash) VALUES(%s,%s,%s,%s,%s,%s)',
                      (root, run_id, child, parent_hash, Json(data), digest(data)))
            repo.event(c, child, 'cognitive.evidence-successor-created', None,
                       {'parentRunId': run_id, 'rootRunId': root, 'revisionAttempt': 1,
                        'gapIds': [g['gapId'] for g in gaps], 'budgetsReset': False})
            # The parent worker's recorded pool is the scheduling authority.
            # Commit its successor and queue entry together so interruption
            # after delivery cannot leave a newly created Run undispatched.
            c.execute('SELECT pool_key FROM agent_run_queue WHERE run_id=%s', (run_id,))
            parent_queue = c.fetchone()
            if parent_queue:
                c.execute('INSERT INTO agent_run_queue(run_id,pool_key) VALUES(%s,%s) ON CONFLICT(run_id) DO NOTHING',
                          (child, parent_queue['pool_key']))
            return {'created': True, 'runId': child, 'reason': 'targeted-evidence-successor'}


def validate_successor(cursor, run_id, data):
    """Verify a sealed successor at runtime; return its immutable binding."""
    cursor.execute('''SELECT v.*,p.input_json AS parent_input,p.agent_key AS parent_agent,
        p.status AS parent_status,ps.profile_hash AS source_profile_hash,
        cs.profile_hash AS child_profile_hash,m.root_run_id AS member_root,m.parent_run_id AS member_parent
        FROM agent_cognitive_revision v JOIN agent_run p ON p.run_id=v.parent_run_id
        JOIN agent_run_profile_snapshot ps ON ps.run_id=v.parent_run_id
        JOIN agent_run_profile_snapshot cs ON cs.run_id=v.child_run_id
        JOIN agent_run_budget_member m ON m.run_id=v.child_run_id WHERE v.child_run_id=%s''', (run_id,))
    row = cursor.fetchone()
    if (not row or digest(row['input_json']) != row['content_hash']
            or digest(data) != row['content_hash'] or data.get('revisionAttempt') != 1
            or row['parent_agent'] != APPLICATION_KEY or row['parent_status'] != 'completed-with-gaps'
            or row['root_run_id'] != row['parent_run_id'] or row['member_root'] != row['root_run_id']
            or row['member_parent'] != row['parent_run_id'] or data.get('parentRunId') != row['parent_run_id']
            or data.get('rootRunId') != row['root_run_id'] or data.get('cognitiveRevisionOf') != row['parent_run_id']
            or row['parent_profile_hash'] != row['source_profile_hash']
            or row['parent_profile_hash'] != row['child_profile_hash']):
        raise PermissionError('cognitive successor is not bound to its frozen authority')
    original = _document(row['parent_input'])
    expected = deepcopy(original)
    expected.update(parentRunId=row['parent_run_id'], rootRunId=row['root_run_id'],
                    cognitiveRevisionOf=row['parent_run_id'], revisionAttempt=1,
                    previousGaps=deepcopy(data.get('previousGaps', [])))
    if expected != data or not data.get('previousGaps'):
        raise PermissionError('cognitive successor expanded the original task input')
    return dict(row)
