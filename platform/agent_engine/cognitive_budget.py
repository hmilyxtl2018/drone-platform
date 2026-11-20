"""Frozen node ceilings for every request in a cognitive research lineage.

Called while the existing root budget row is locked. Settled token counts are
used when known; unknown and in-flight usage stays reserved. No second ledger
or allowance is created for delegated Actions or successor Runs.
"""
from .cognitive_identity import APPLICATION_KEY
from .repository import as_json


def _snapshot(cursor, run_id):
    cursor.execute('SELECT profile_json,profile_hash FROM agent_run_profile_snapshot WHERE run_id=%s', (run_id,))
    row = cursor.fetchone()
    if not row:
        raise ValueError('node budget requires a frozen Profile')
    from .cognitive_contracts import digest
    profile = as_json(row['profile_json'])
    if digest(profile) != row['profile_hash']:
        raise PermissionError('node budget Profile fingerprint mismatch')
    return profile


def _ceiling(profile, step_key):
    nodes = (profile.get('executionBaseline') or {}).get('graph', {}).get('nodes', [])
    return next((n.get('budget') or {} for n in nodes if n['id'] == step_key), None)


def assert_fits(ceiling, used, charges, step_key):
    from .phase3_repository import BudgetExceededError
    if ceiling is None:
        raise PermissionError('request has no frozen node budget: ' + step_key)
    for key, count in charges.items():
        # Missing counters deny use; an explicit zero is never unlimited.
        if used.get(key, 0) + count > int(ceiling.get(key, 0) or 0):
            raise BudgetExceededError('frozen cognitive node budget exceeded: ' + step_key + ':' + key)


def _used(cursor, run_id, step_key=None, *, subtree=False):
    if subtree:
        scope = '''WITH RECURSIVE members AS (
            SELECT run_id FROM agent_run_budget_member WHERE run_id=%s
            UNION ALL SELECT m.run_id FROM agent_run_budget_member m JOIN members a ON m.parent_run_id=a.run_id
        ) SELECT r.reservation_id,r.charges_json FROM agent_budget_reservation r JOIN members m USING(run_id)'''
        params = (run_id,)
    else:
        scope = 'SELECT reservation_id,charges_json FROM agent_budget_reservation WHERE run_id=%s AND step_key=%s'
        params = (run_id, step_key)
    cursor.execute(scope, params)
    reservations = list(cursor.fetchall())
    used = {}
    for row in reservations:
        cursor.execute('SELECT effective_json FROM agent_budget_settlement WHERE reservation_id=%s ORDER BY revision DESC LIMIT 1',
                       (row['reservation_id'],))
        settlement = cursor.fetchone()
        effective = settlement['effective_json'] if settlement else row['charges_json']
        for key, amount in effective.items():
            used[key] = used.get(key, 0) + amount
    return used


def assert_node_budgets(cursor, root_run_id, run_id, step_key, charges):
    cursor.execute('SELECT agent_key FROM agent_run WHERE run_id=%s', (root_run_id,))
    row = cursor.fetchone()
    if not row or row['agent_key'] != APPLICATION_KEY:
        return
    if not step_key:
        raise PermissionError('cognitive requests require a node identity')
    profile = _snapshot(cursor, run_id)
    assert_fits(_ceiling(profile, step_key), _used(cursor, run_id, step_key), charges, step_key)
    # Each actual request also consumes its parent's complete sub-Agent node
    # ceiling. A child with several internal nodes cannot multiply that grant.
    child_id, seen = run_id, set()
    while child_id != root_run_id:
        if child_id in seen or len(seen) > 16:
            raise PermissionError('invalid cognitive budget ancestry')
        seen.add(child_id)
        cursor.execute('SELECT run_id,parent_step_key FROM agent_action_invocation WHERE child_run_id=%s', (child_id,))
        invocation = cursor.fetchone()
        if invocation:
            parent_id, parent_step = invocation['run_id'], invocation['parent_step_key']
            parent = _snapshot(cursor, parent_id)
            assert_fits(_ceiling(parent, parent_step), _used(cursor, child_id, subtree=True), charges, parent_step)
        else:
            cursor.execute('SELECT parent_run_id FROM agent_run_budget_member WHERE run_id=%s', (child_id,))
            member = cursor.fetchone()
            if not member or not member['parent_run_id']:
                raise PermissionError('cognitive budget lineage is detached')
            parent_id = member['parent_run_id']
        child_id = parent_id
