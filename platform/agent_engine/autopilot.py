"""Human-authorized task entry over the existing DSH turns and Agent Runs.

This first workspace uses reviewed Agent baselines, not an unrestricted planner.
Preview is read-only. Starting rechecks current authority and serializes retries.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json

from fastapi import APIRouter, Depends, HTTPException, Query
from psycopg2.extras import Json


FUTURE_PROFILES = ('future-battlefield-codex', 'future-battlefield-analyst')
ATTENTION = {'failed', 'blocked', 'completed-with-gaps', 'requires-human-review', 'waiting-human'}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def task_projection(row):
    status = row.get('run_status') or row['status']
    error = row.get('error_message')
    public_error = (error if error in {'run-admission-rejected', 'run-enqueue-failed',
                                      'baseline-not-ready'} else 'run-request-failed') if error else None
    return {'turnId': row['turn_id'], 'sessionId': row['session_id'], 'runId': row.get('run_id'),
            'question': row['question'], 'title': ' '.join(row['question'].split())[:100],
            'agentKey': row.get('agent_key') or row.get('agent_profile'),
            'agentName': row.get('display_name'), 'status': status,
            'currentStep': row.get('current_step_key'), 'createdAt': row['created_at'],
            'updatedAt': row.get('run_updated_at') or row.get('updated_at'),
            'needsAttention': status in ATTENTION or row['status'] == 'failed',
            'origin': 'autopilot' if (row.get('metadata_json') or {}).get('client') == 'autopilot' else 'conversation',
            'errorCode': public_error}


def create_autopilot_router(*, factory, engine, principal, capabilities, create_turn, normalize):
    router = APIRouter(prefix='/autopilot', tags=['autopilot'])

    def profiles(actor):
        from .runtime_contracts import validate_future_research_admission
        from .uav_execution_contracts import readiness, AGENTS
        result = []
        for public in capabilities(actor)['profiles']:
            item = deepcopy(public)
            key = item['agent_key']
            item.pop('queriedAt', None)
            try:
                if key in AGENTS:
                    ready = readiness(engine.repo, key)
                    item['readiness'] = {'ready': ready['ready'], 'status': ready['status'],
                        'message': '需要先完成本体对象选择与资料预检。' if ready['ready'] else '执行基线或运行时尚未就绪。'}
                elif key in FUTURE_PROFILES:
                    profile = engine.loader.load(key)
                    data = normalize('公开资料研究与证据追溯', {}, [], {}, 'unattended-draft', key, profile.version)
                    validate_future_research_admission(profile, data)
                    item['readiness'] = {'ready': True, 'status': 'ready', 'message': '执行基线与运行依赖检查通过。'}
                else:
                    item['readiness'] = {'ready': False, 'status': 'unsupported', 'message': '当前入口尚未支持此任务类型。'}
            except Exception:
                # Never expose provider configuration, environment names or endpoints.
                item['readiness'] = {'ready': False, 'status': 'not-ready', 'message': '执行配置或运行依赖尚未就绪。'}
            item['launchSupported'] = key in FUTURE_PROFILES and item['readiness']['ready'] and 'unattended-draft' in item['allowedModes']
            result.append(item)
        return result

    @router.get('/catalog')
    def catalog(actor=Depends(principal)):
        available = profiles(actor)
        default = next((key for key in FUTURE_PROFILES if any(
            p['agent_key'] == key and p['launchSupported'] for p in available)), None)
        return {'schemaVersion': 'autopilot-catalog.v1', 'profiles': available, 'defaultAgentKey': default,
                'executionPolicy': 'reviewed-baseline', 'publication': 'draft-only'}

    def preview(body, actor):
        if not isinstance(body, dict) or set(body) - {'question', 'agentProfile', 'budget'}:
            raise HTTPException(400, '任务只接受目标、Agent 和资源预算。')
        question = body.get('question')
        if not isinstance(question, str) or not 1 <= len(question.strip()) <= 10000:
            raise HTTPException(400, '请填写不超过 10000 字的任务目标。')
        budget = body.get('budget', {})
        if not isinstance(budget, dict) or len(budget) > 20 or any(type(v) is not int or v < 0 for v in budget.values()):
            raise HTTPException(400, '预算必须是非负整数。')
        candidates = profiles(actor)
        requested = body.get('agentProfile', 'auto')
        if requested == 'auto':
            chosen = next((p for key in FUTURE_PROFILES for p in candidates if p['agent_key'] == key and p['launchSupported']), None)
        else:
            chosen = next((p for p in candidates if p['agent_key'] == requested and p['launchSupported']), None)
        if not chosen:
            raise HTTPException(409, {'code': 'agent-not-ready', 'message': '没有符合当前任务类型且已就绪的 Agent。请检查执行基线。'})
        limits, minimums = chosen['budgetLimits'], chosen.get('budgetMinimums') or {}
        if any(k not in limits or v > limits[k] or v < minimums.get(k, 0) for k, v in budget.items()):
            raise HTTPException(400, {'code': 'budget-outside-contract', 'message': '预算超出当前 Agent 允许的范围。', 'budgetLimits': limits, 'budgetMinimums': minimums})
        effective = {**limits, **budget}
        key = chosen['agent_key']
        data = normalize(question.strip(), {}, [], effective, 'unattended-draft', key, chosen['current_version'])
        from .runtime_contracts import validate_future_research_admission
        try:
            validate_future_research_admission(engine.loader.load(key), data)
        except Exception:
            raise HTTPException(409, {'code': 'agent-not-ready', 'message': '当前任务无法通过运行预检，请重新读取能力和预算。'}) from None
        request = {'question': question.strip(), 'agentProfile': key, 'budget': effective,
                   'mode': 'unattended-draft'}
        authorization = {'schemaVersion': 'autopilot-authorization.v1', 'request': request,
            'agentName': chosen['display_name'], 'agentVersion': chosen['current_version'],
            'contractFingerprint': chosen['contractFingerprint'], 'executionPolicy': 'reviewed-baseline',
            'selectionReason': '按公开资料研究任务类型匹配已就绪的应用 Agent；内部协作使用该 Agent 的已发布执行基线。',
            'deliverables': (chosen.get('goalContract') or {}).get('deliverables') or ['可审阅报告', '证据与本体关系', '未解决缺口'],
            'scope': data.get('scope') or data.get('analysisContext'),
            'perspectives': [p['name'] for p in data.get('perspectives', [])],
            'allowedOperations': ['read-evidence', 'search-public', 'analyze', 'draft-report', 'delegate'],
            'stopConditions': ['执行基线收敛', '预算耗尽', '授权到期（默认 24 小时）', '用户撤销'],
            'publication': 'draft-only'}
        authorization['previewHash'] = digest(authorization)
        return authorization

    @router.post('/preview')
    def preview_task(body: dict, actor=Depends(principal)):
        return preview(body, actor)

    @router.get('/tasks')
    def tasks(limit: int = Query(30, ge=1, le=50), offset: int = Query(0, ge=0, le=10000), actor=Depends(principal)):
        # Conversation and Autopilot entries read the same canonical turn/Run.
        with factory() as con:
            with con.cursor() as cursor:
                cursor.execute('''SELECT t.turn_id,t.session_id,t.run_id,t.question,t.status,t.error_message,
                    t.created_at,t.updated_at,s.agent_profile,s.metadata_json,r.agent_key,r.status AS run_status,
                    r.current_step_key,r.updated_at AS run_updated_at,d.display_name
                    FROM dsh_turn t JOIN dsh_session s USING(session_id)
                    LEFT JOIN agent_run r ON r.run_id=t.run_id
                    LEFT JOIN agent_definition d ON d.agent_key=COALESCE(r.agent_key,s.agent_profile)
                    WHERE s.principal_id=%s ORDER BY t.created_at DESC,t.turn_id DESC LIMIT %s OFFSET %s''',
                    (actor.principal_id, limit + 1, offset))
                rows = cursor.fetchall()
        return {'schemaVersion': 'autopilot-tasks.v1', 'tasks': [task_projection(dict(r)) for r in rows[:limit]],
                'nextOffset': offset + limit if len(rows) > limit else None}

    @router.post('/tasks', status_code=202)
    def start_task(body: dict, actor=Depends(principal)):
        if set(body) - {'request', 'previewHash', 'idempotencyKey', 'clientSessionId'}:
            raise HTTPException(400, '任务包含未支持的字段。')
        key, expected, client_session = body.get('idempotencyKey'), body.get('previewHash'), body.get('clientSessionId')
        if not isinstance(key, str) or not 1 <= len(key) <= 128 or not isinstance(expected, str) or len(expected) != 64:
            raise HTTPException(400, '任务缺少授权指纹或稳定的提交标识。')
        if client_session is not None and (not isinstance(client_session, str) or len(client_session) > 200):
            raise HTTPException(400, '对话标识无效。')
        session_id = 'dsh-session-ap-' + digest([actor.principal_id, key])[:40]
        request_hash = digest(body.get('request'))
        # A retry across browser reloads must not create a second paid Run.
        with factory() as lock_con:
            with lock_con.cursor() as lock:
                lock.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', ('autopilot:' + session_id,))
                with factory() as con:
                    with con.cursor() as cursor:
                        cursor.execute('SELECT metadata_json FROM dsh_session WHERE session_id=%s AND principal_id=%s', (session_id, actor.principal_id))
                        existing = cursor.fetchone()
                        if existing:
                            metadata = existing['metadata_json']
                            if metadata.get('previewHash') != expected or metadata.get('requestHash') != request_hash:
                                raise HTTPException(409, '提交标识已用于另一份授权，请重新核对任务。')
                            cursor.execute('SELECT turn_id,run_id,status FROM dsh_turn WHERE session_id=%s AND idempotency_key=%s', (session_id, key))
                            turn = cursor.fetchone()
                            if turn:
                                return {'sessionId': session_id, 'turnId': turn['turn_id'], 'runId': turn['run_id'], 'status': turn['status'], 'duplicate': True}
                request_body = body.get('request')
                if not isinstance(request_body, dict) or set(request_body) != {'question', 'agentProfile', 'budget', 'mode'} or request_body['mode'] != 'unattended-draft':
                    raise HTTPException(400, '授权任务格式无效。')
                # Keep the reviewed configuration stable through actual Run
                # admission. Admin publication uses the matching exclusive lock.
                lock.execute("SELECT pg_advisory_xact_lock_shared(hashtextextended('agent-admin-profile-config',0))")
                current = preview({k: request_body[k] for k in ('question', 'agentProfile', 'budget')}, actor)
                if current['previewHash'] != expected:
                    raise HTTPException(409, {'code': 'authorization-changed', 'message': '目标、预算或 Agent 契约已变化，请重新核对授权。'})
                with factory() as con:
                    with con.cursor() as cursor:
                        cursor.execute('''INSERT INTO dsh_session(session_id,principal_id,tenant_id,agent_profile,metadata_json)
                            VALUES(%s,%s,'local',%s,%s) ON CONFLICT(session_id) DO NOTHING''',
                            (session_id, actor.principal_id, current['request']['agentProfile'],
                             Json({'client': 'autopilot', 'previewHash': expected, 'requestHash': request_hash,
                                   'clientSessionId': client_session, 'authorization': current})))
                result = create_turn(session_id, {**current['request'], 'idempotencyKey': key}, actor)
        return {'sessionId': session_id, **result}

    return router
