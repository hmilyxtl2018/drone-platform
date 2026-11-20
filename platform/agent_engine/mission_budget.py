"""Cross-Run authority and budget, including descendants and quality reviews.

Lock order is mission -> root budget -> TaskIntent -> evolution. Binding happens
in the same transaction as Run creation. No copied input can invent authority.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone

from .autopilot import digest

_dispatch = ContextVar('autopilot_dispatch', default=None)


def installed(c):
    c.execute("SELECT to_regclass(current_schema() || '.agent_autopilot_run_binding') AS name")
    row = c.fetchone()
    return bool(row and row.get('name'))


@contextmanager
def dispatch_context(mission_id, grant_id, proposal_id):
    token = _dispatch.set((mission_id, grant_id, proposal_id))
    try:
        yield
    finally:
        _dispatch.reset(token)


def lock(c, mission_id):
    c.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', ('autopilot:' + mission_id,))


def binding(c, run_id):
    if not installed(c):
        return None
    c.execute('SELECT * FROM agent_autopilot_run_binding WHERE run_id=%s', (run_id,))
    return c.fetchone()


def usage(c, mission_id, since=None):
    c.execute('''SELECT COALESCE(s.effective_json,r.charges_json) AS counters
        FROM agent_budget_reservation r JOIN agent_autopilot_run_binding b USING(run_id)
        LEFT JOIN LATERAL (SELECT effective_json FROM agent_budget_settlement
          WHERE reservation_id=r.reservation_id ORDER BY revision DESC LIMIT 1) s ON true
        WHERE b.mission_id=%s AND (%s::timestamptz IS NULL OR r.created_at>=%s)''',
        (mission_id, since, since))
    used = {}
    for row in c.fetchall():
        for key, value in row['counters'].items():
            used[key] = used.get(key, 0) + value
    return used


def authority(c, mission_id, grant_id, *, active=True):
    lock(c, mission_id)
    c.execute('''SELECT m.*,s.status,s.grant_id AS current_grant,s.revision AS state_revision,
        g.grant_id,g.document_json,g.content_hash,g.expires_at,g.created_at AS granted_at,i.enabled
        FROM agent_autopilot_mission m JOIN agent_autopilot_mission_state s USING(mission_id)
        JOIN agent_autopilot_grant g ON g.grant_id=%s
        JOIN agent_admin_identity i ON i.principal_id=m.principal_id
        WHERE m.mission_id=%s AND g.mission_id=m.mission_id''', (grant_id, mission_id))
    row = c.fetchone()
    if not row:
        raise PermissionError('autopilot-owner-unavailable')
    if digest(row['goal_json']) != row['goal_hash'] or digest(row['document_json']) != row['content_hash']:
        raise PermissionError('autopilot-authority-integrity')
    if row['document_json']['goal'] != row['goal_json']:
        raise PermissionError('autopilot-authority-integrity')
    if active:
        if not row['enabled']:
            raise PermissionError('autopilot-owner-unavailable')
        c.execute('SELECT clock_timestamp() AS now')
        if row['expires_at'] <= c.fetchone()['now']:
            raise PermissionError('autopilot-grant-expired')
        if row['status'] != 'active' or row['current_grant'] != grant_id:
            raise PermissionError('autopilot-grant-inactive')
        c.execute('''SELECT revision_id,created_at FROM agent_autopilot_alignment_revision WHERE principal_id=%s
            AND workspace_key='ontology' AND action='confirm-understanding' ORDER BY revision DESC LIMIT 1''', (row['principal_id'],))
        latest = c.fetchone()
        if (row['alignment_revision_id'] and (not latest or latest['revision_id'] != row['alignment_revision_id'])) or (
                not row['alignment_revision_id'] and latest and latest['created_at'] > row['created_at']):
            raise PermissionError('autopilot-goal-changed')
    return row


def assert_dispatch(c, run_id, *, charges=None):
    bound = binding(c, run_id)
    if not bound:
        return
    row = authority(c, bound['mission_id'], bound['grant_id'])
    if charges:
        from .phase3_repository import BudgetExceededError
        # Periods are anchored to this grant, not server/browser local time.
        now = datetime.now(timezone.utc)
        start = row['granted_at']
        from datetime import timedelta
        period_start = start + timedelta(days=max(0, (now - start).days))
        for limits, used in ((row['document_json']['totalBudget'], usage(c, bound['mission_id'])),
                             (row['document_json']['dailyBudget'], usage(c, bound['mission_id'], period_start))):
            if any(value > limits.get(key, 0) for key, value in used.items()) or any(
                    used.get(key, 0) + value > limits.get(key, 0) for key, value in charges.items()):
                raise BudgetExceededError('autopilot-shared-budget-exhausted')


def lock_settlement(c, run_id):
    bound = binding(c, run_id)
    if bound:
        lock(c, bound['mission_id'])


def bind(c, run_id, input_data):
    if not installed(c):
        if _dispatch.get():
            raise RuntimeError('autopilot migration 087 required')
        return
    selected = _dispatch.get()
    if not selected:
        for key in ('parentRunId', 'previousRunId', 'sourceRunId'):
            if input_data.get(key):
                parent = binding(c, input_data[key])
                if parent:
                    selected = tuple(parent[k] for k in ('mission_id', 'grant_id', 'proposal_id'))
                    break
    if selected:
        row = authority(c, selected[0], selected[1])
        c.execute('''SELECT mission_id,grant_id FROM agent_autopilot_proposal WHERE proposal_id=%s''', (selected[2],))
        proposal = c.fetchone()
        if not proposal or (proposal['mission_id'], proposal['grant_id']) != selected[:2]:
            raise PermissionError('autopilot-proposal-authority-mismatch')
        c.execute('INSERT INTO agent_autopilot_run_binding(run_id,mission_id,grant_id,proposal_id) VALUES(%s,%s,%s,%s)',
                  (run_id, *selected))
