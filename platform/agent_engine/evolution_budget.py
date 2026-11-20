"""Cycle budget checks share the Run reservation transaction and lock order."""
from .task_intent import digest

ACTIVE={'reflecting','designing','trial-running','evaluating'}


def installed(cursor):
    cursor.execute("SELECT to_regclass(current_schema() || '.agent_evolution_budget') AS name")
    return bool(cursor.fetchone()['name'])


def usage(cursor, cycle_id):
    cursor.execute('''SELECT COALESCE(s.effective_json,r.charges_json) AS counters
        FROM agent_budget_reservation r JOIN agent_evolution_run_binding b USING(run_id)
        LEFT JOIN LATERAL (SELECT effective_json FROM agent_budget_settlement
            WHERE reservation_id=r.reservation_id ORDER BY revision DESC LIMIT 1) s ON true
        WHERE b.cycle_id=%s''',(cycle_id,))
    used = {}
    for row in cursor.fetchall():
        for key, count in row['counters'].items():
            used[key] = used.get(key,0)+count
    return used


def scope(cursor, run_id):
    if not installed(cursor):
        return None
    cursor.execute('''SELECT c.*,b.limits_json,b.expires_at,p.document_json AS policy_json,
        p.content_hash AS policy_content_hash FROM agent_evolution_run_binding r
        JOIN agent_evolution_cycle c USING(cycle_id) JOIN agent_evolution_budget b USING(cycle_id)
        JOIN agent_evolution_policy_revision p USING(policy_id) WHERE r.run_id=%s''',(run_id,))
    return cursor.fetchone()


def assert_dispatch(cursor, run_id, *, operation=None, tool=None, charges=None):
    if not installed(cursor):return
    cycle = scope(cursor,run_id)
    if not cycle:
        # An unbound reflection run must never acquire execution authority.
        cursor.execute('SELECT agent_key FROM agent_run WHERE run_id=%s',(run_id,))
        row=cursor.fetchone()
        if row and row['agent_key'] in {'research-reflection-agent','research-candidate-designer-agent','research-report-quality-agent'}:
            raise PermissionError('reflection is missing its frozen cycle authority')
        return
    from .task_intent import assert_dispatch as source_dispatch
    source_dispatch(cursor,cycle['source_run_id'],operation='read-evidence')
    cursor.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('evolution-policy:'+cycle['policy_json']['agentKey'],))
    # Source intent lock precedes cycle lock; stop only takes the cycle lock.
    cursor.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('evolution:'+cycle['cycle_id'],))
    cursor.execute('SELECT status FROM agent_evolution_cycle WHERE cycle_id=%s',(cycle['cycle_id'],))
    if cursor.fetchone()['status'] not in ACTIVE:
        raise PermissionError('evolution cycle stopped; no new dispatch is allowed')
    cursor.execute('SELECT clock_timestamp() AS now')
    if cursor.fetchone()['now'] >= cycle['expires_at']:
        raise PermissionError('evolution cycle expired')
    if digest(cycle['policy_json']) != cycle['policy_hash'] or cycle['policy_hash'] != cycle['policy_content_hash']:
        raise PermissionError('evolution policy integrity mismatch')
    cursor.execute('''SELECT p.document_json FROM agent_evolution_policy a
        JOIN agent_evolution_policy_revision p USING(policy_id) WHERE a.agent_key=%s''',
        (cycle['policy_json'].get('agentKey'),))
    active=cursor.fetchone()
    if not active or not active['document_json'].get('enabled'):
        raise PermissionError('automatic reflection disabled')
    if cycle['policy_json']['mode']=='auto-trial':
        if active['document_json'].get('mode')!='auto-trial':raise PermissionError('automatic trial disabled')
        cursor.execute('SELECT base_live_hash,agent_key,case_json,case_hash FROM agent_evolution_trial WHERE cycle_id=%s',(cycle['cycle_id'],))
        trial=cursor.fetchone()
        if not trial or digest(trial['case_json'])!=trial['case_hash']:raise PermissionError('trial snapshot integrity mismatch')
        from agent_admin.config_publication import ConfigPublicationService
        ConfigPublicationService._config_lock(cursor)
        if ConfigPublicationService._live(cursor,trial['agent_key'])[2]!=trial['base_live_hash']:
            raise PermissionError('published baseline changed; trial stopped')
    if tool or operation and operation not in cycle['policy_json']['allowedOperations']:
        raise PermissionError('reflection authority is read-only analysis without tools')
    if charges:
        from .phase3_repository import BudgetExceededError
        used=usage(cursor,cycle['cycle_id'])
        for key,count in used.items():
            if count>cycle['limits_json'].get(key,0):
                raise BudgetExceededError('cycle actual usage exceeds budget: '+key)
        for key,count in charges.items():
            if used.get(key,0)+count>cycle['limits_json'].get(key,0):
                raise BudgetExceededError('cycle budget exceeded: '+key)


def lock_settlement(cursor, run_id):
    cycle=scope(cursor,run_id)
    if cycle:
        cursor.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('evolution:'+cycle['cycle_id'],))
