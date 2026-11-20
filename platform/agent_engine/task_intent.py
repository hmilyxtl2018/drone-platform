"""Immutable public-research authority; checked at actual dispatch boundaries.

Revocation linearizes against admission/reservation on an advisory transaction
lock. An already admitted external request may finish and keeps its true result.
No historical authority is inferred from old inputs.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json

from psycopg2.extras import Json

_issuer = ContextVar('research_intent_issuer', default=('engine-admission', 'system'))
COUNTERS = {'model_calls','tool_calls','http_calls','search_calls','scrape_calls',
            'input_tokens','output_tokens','reasoning_rounds','gate_checks','relation_reviews'}
OPERATIONS = {'read-evidence','search-public','analyze','draft-report','delegate'}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        allow_nan=False, separators=(',', ':'), default=str).encode()).hexdigest()


@contextmanager
def admission_actor(actor):
    token = _issuer.set((actor.principal_id, actor.kind))
    try:
        yield
    finally:
        _issuer.reset(token)


def installed(cursor):
    cursor.execute("SELECT to_regclass(current_schema() || '.agent_run_task_intent') AS name")
    return bool(cursor.fetchone()['name'])


def parse_deadline(value):
    try:
        parsed = datetime.fromisoformat(value.replace('Z','+00:00'))
        if parsed.tzinfo is None:
            raise ValueError()
        return parsed.astimezone(timezone.utc)
    except (AttributeError, TypeError, ValueError):
        raise ValueError('expiresAt must be an ISO timestamp with timezone') from None


def narrow(parent, requested):
    """Only a subset of authority, quotas and lifetime can be delegated."""
    result = deepcopy(parent)
    if set(requested) - {'allowedOperations','allowedTools','budgets','expiresAt'}:
        raise ValueError('child TaskIntent can only narrow operations, tools, budgets and expiry')
    for key in ('allowedOperations','allowedTools'):
        if key in requested:
            if not isinstance(requested[key], list) or any(not isinstance(v,str) for v in requested[key]):
                raise ValueError(key + ' must be a string list')
            if not set(requested[key]) <= set(parent[key]):
                raise PermissionError('child TaskIntent cannot expand ' + key)
            result[key] = sorted(set(requested[key]))
    if 'budgets' in requested:
        if not isinstance(requested['budgets'], dict):
            raise ValueError('budgets must be an object')
        for key, value in requested['budgets'].items():
            if key not in COUNTERS or type(value) is not int or value < 0:
                raise ValueError('invalid TaskIntent budget')
            if value > parent['budgets'].get(key, 0):
                raise PermissionError('child TaskIntent cannot expand budgets')
            result['budgets'][key] = value
    if 'expiresAt' in requested:
        if parse_deadline(requested['expiresAt']) > parse_deadline(parent['expiresAt']):
            raise PermissionError('child TaskIntent cannot extend expiry')
        result['expiresAt'] = parse_deadline(requested['expiresAt']).isoformat()
    return result


def _row(cursor, run_id):
    cursor.execute('''SELECT b.*,r.document_json,r.expires_at FROM agent_run_task_intent b
        JOIN agent_task_intent_revision r USING(intent_id) WHERE b.run_id=%s''', (run_id,))
    row = cursor.fetchone()
    if row and digest(row['document_json']) != row['content_hash']:
        raise PermissionError('TaskIntent snapshot hash mismatch')
    return row


def _chain(cursor, run_id):
    rows, seen = [], set()
    while run_id:
        if run_id in seen or len(rows) >= 128:
            raise PermissionError('invalid TaskIntent ancestry')
        seen.add(run_id)
        row = _row(cursor, run_id)
        if not row:
            if rows:
                raise PermissionError('TaskIntent ancestor is missing')
            break
        rows.append(row)
        run_id = row['parent_run_id']
    return rows


def assert_dispatch(cursor, run_id, *, operation=None, tool=None, charges=None):
    from .mission_budget import assert_dispatch as mission_dispatch
    mission_dispatch(cursor, run_id, charges=charges)
    if not installed(cursor):
        return []
    chain = _chain(cursor, run_id)
    # Root-to-child lock order also serializes concurrent descendant admission.
    for row in reversed(chain):
        cursor.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',
                       ('task-intent:' + row['intent_id'],))
        cursor.execute("SELECT 1 FROM agent_task_intent_event WHERE intent_id=%s AND event_type='revoked'", (row['intent_id'],))
        if cursor.fetchone():
            raise PermissionError('TaskIntent revoked; no new dispatch is allowed')
        cursor.execute('SELECT clock_timestamp() AS now')
        if row['expires_at'] <= cursor.fetchone()['now']:
            raise PermissionError('TaskIntent expired; no new dispatch is allowed')
        doc = row['document_json']
        if operation and operation not in doc['allowedOperations']:
            raise PermissionError('operation is outside TaskIntent: ' + operation)
        if tool and tool not in doc['allowedTools']:
            raise PermissionError('tool is outside TaskIntent: ' + tool)
        if charges:
            # Match the root ledger: confirmed token usage releases unused
            # reservation, while unknown/in-flight dimensions remain reserved.
            # The settlement's effective_json also retains attempted call
            # counts and records overruns; authority limits stay unchanged.
            cursor.execute('''WITH RECURSIVE subtree AS (
                SELECT run_id FROM agent_run_task_intent WHERE run_id=%s
                UNION ALL SELECT b.run_id FROM agent_run_task_intent b JOIN subtree s ON b.parent_run_id=s.run_id
            ) SELECT COALESCE(latest.effective_json,r.charges_json) AS charges_json
                FROM agent_budget_reservation r JOIN subtree s USING(run_id)
                LEFT JOIN LATERAL (SELECT effective_json FROM agent_budget_settlement
                    WHERE reservation_id=r.reservation_id ORDER BY revision DESC LIMIT 1) latest ON true''', (row['run_id'],))
            used = {}
            for reservation in cursor.fetchall():
                for key, value in reservation['charges_json'].items():
                    used[key] = used.get(key,0) + value
            for key, value in charges.items():
                if used.get(key,0) + value > doc['budgets'].get(key,0):
                    from .phase3_repository import BudgetExceededError
                    raise BudgetExceededError('TaskIntent budget exceeded: ' + key)
    return chain


def bind(cursor, run_id, profile, input_data):
    if not installed(cursor):
        if input_data.get('taskIntent') is not None:
            raise RuntimeError('research governance migration 076 is required')
        return
    from .repository import new_id
    parent_id = input_data.get('parentRunId')
    requested = input_data.get('taskIntent') or {}
    if not isinstance(requested, dict):
        raise ValueError('taskIntent must be an object')
    parent = None
    if parent_id:
        ancestors = assert_dispatch(cursor, parent_id, operation='delegate')
        if not ancestors:
            # Legacy Runs retain an explicit absence of authorization history.
            if requested:
                raise PermissionError('cannot delegate an unrecorded historical TaskIntent')
            return
        parent = ancestors[0]
        doc = narrow(parent['document_json'], requested)
        issuer_id, issuer_kind = parent['intent_id'], 'delegated'
    else:
        if set(requested) - {'allowedOperations','allowedTools','budgets','expiresAt'}:
            raise ValueError('TaskIntent issuer, objective, scope and source policy are server-bound')
        config = profile.model_policy.get('config_json') or {}
        limits = {**config.get('budgetLimits',{}), **(profile.reasoning_policy or {}).get('budgetLimits',{}),
                  **profile.resource_policy.get('budgetLimits',{})}
        limits = {k:v for k,v in limits.items() if k in COUNTERS}
        # Older deterministic Profiles may have no durable resource counters.
        limits.setdefault('tool_calls', int(profile.convergence.get('max_tool_calls') or 0))
        limits.setdefault('model_calls', int(profile.convergence.get('max_model_calls') or 0))
        now = datetime.now(timezone.utc)
        doc = {'schemaVersion':'task-intent.v1', 'purpose':'public-research',
            'objective':str(input_data.get('question') or input_data.get('task') or profile.display_name)[:10000],
            'scope':deepcopy(input_data.get('scope') or {'entityId':input_data.get('entityId') or input_data.get('uavId')}),
            'allowedOperations':sorted(OPERATIONS),
            'allowedTools':sorted(k for k,v in profile.tools.items() if v.enabled and v.access_mode == 'read_only'),
            'sourcePolicy':{'mode':'public-or-registered-read-only','profilePolicy':deepcopy(profile.source_policy)},
            'budgets':limits, 'stopConditions':['revoked','expired','budget-exhausted','profile-invariant-failed'],
            'expiresAt':(now + timedelta(hours=24)).isoformat()}
        doc = narrow(doc, requested)
        issuer_id, issuer_kind = _issuer.get()
    # The child Profile itself can further narrow tool and budget authority.
    doc['allowedTools'] = sorted(set(doc['allowedTools']) & {
        k for k,v in profile.tools.items() if v.enabled and v.access_mode == 'read_only'})
    child_limits = profile.resource_policy.get('budgetLimits', {})
    for key in doc['budgets']:
        if key in child_limits:
            doc['budgets'][key] = min(doc['budgets'][key], child_limits[key])
    if parse_deadline(doc['expiresAt']) <= datetime.now(timezone.utc):
        raise PermissionError('TaskIntent must not already be expired')
    intent_id = new_id('intent')
    doc.update(issuer={'id':issuer_id,'kind':issuer_kind}, agentKey=profile.agent_key,
               parentIntentHash=parent['content_hash'] if parent else None)
    content_hash = digest(doc)
    cursor.execute('''INSERT INTO agent_task_intent_revision(intent_id,task_id,revision,parent_intent_id,
        document_json,content_hash,issuer_id,issuer_kind,expires_at) VALUES(%s,%s,1,%s,%s,%s,%s,%s,%s)''',
        (intent_id,intent_id,parent['intent_id'] if parent else None,Json(doc),content_hash,issuer_id,issuer_kind,parse_deadline(doc['expiresAt'])))
    cursor.execute('INSERT INTO agent_run_task_intent(run_id,intent_id,content_hash,parent_run_id) VALUES(%s,%s,%s,%s)',
                   (run_id,intent_id,content_hash,parent_id))


def check(repository, run_id, **kwargs):
    with repository.connection_factory() as con:
        with con.cursor() as cursor:
            return assert_dispatch(cursor,run_id,**kwargs)


def read(cursor, run_id):
    if not installed(cursor):
        return {'status':'unrecorded','label':'未记录任务契约'}
    chain = _chain(cursor,run_id)
    if not chain:
        return {'status':'unrecorded','label':'未记录任务契约'}
    current = chain[0]
    cursor.execute('SELECT intent_id,actor_id,reason,created_at FROM agent_task_intent_event WHERE intent_id=ANY(%s) ORDER BY event_id',
                   ([row['intent_id'] for row in chain],))
    events = list(cursor.fetchall())
    status = 'revoked' if events else 'expired' if any(row['expires_at'] <= datetime.now(timezone.utc) for row in chain) else 'active'
    return {'status':status,'intentId':current['intent_id'],'contentHash':current['content_hash'],
            'document':current['document_json'],'events':events,'parentRunId':current['parent_run_id']}


def revoke(factory, run_id, actor, reason):
    if actor.kind != 'human' or not actor.permits('operator'):
        raise PermissionError('human operator required')
    if not isinstance(reason,str) or not 1 <= len(reason.strip()) <= 2000:
        raise ValueError('revocation reason required')
    with factory() as con:
        with con.cursor() as cursor:
            row = _row(cursor,run_id)
            if not row:
                raise ValueError('historical Run has no TaskIntent to revoke')
            chain = _chain(cursor,run_id)
            for item in reversed(chain):
                cursor.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('task-intent:'+item['intent_id'],))
            cursor.execute("INSERT INTO agent_task_intent_event(intent_id,event_type,actor_id,reason) VALUES(%s,'revoked',%s,%s) ON CONFLICT DO NOTHING",
                           (row['intent_id'],actor.principal_id,reason.strip()))
            return read(cursor,run_id)
