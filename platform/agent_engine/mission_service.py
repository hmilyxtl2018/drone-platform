"""Finite continuous public-research grants and a human decision inbox."""
from copy import deepcopy
from dataclasses import asdict, is_dataclass
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from psycopg2.extras import Json

from .autopilot import digest
from .intent_alignment import AlignmentService, envelope, text, key
from .mission_budget import authority, lock, usage
from .repository import new_id

DEFAULT_GOAL = {
    'purpose': '基于公开官方与研究来源，持续研判未来5年自主系统研究、人机协同的发展趋势与治理约束，支持专家审阅研究方向与证据可信性。',
    'successCriteria': '每次研究形成可审阅报告、本体关系与原文证据；区分公开实测、政策愿景和条件性假设，说明判断变化、适用条件及未解决缺口。',
    'scope': '公开资料与已授权只读研究数据；保持战略和通用技术研究层面，交付未发布草稿。',
    'priorities': '优先证据可追溯与判断可靠；预算内自动核验补证，仍不足时保留未知，不补造结论。',
    'escalation': '目标与价值冲突、扩大范围或数据权限、预算扩额、对外发布由人决定；技术故障、选Agent、规划和补证由系统在授权内处理。',
}


def profile_signature(profile):
    from .phase3_repository import profile_budget_limits
    if is_dataclass(profile):
        return digest(asdict(profile))
    return digest({'agent': profile.agent_key, 'version': profile.version,
        'baseline': profile.execution_baseline.get('graphHash'),
        'goal': (profile.goal_contract or {}).get('contentHash'),
        'limits': profile_budget_limits(profile),
        'tools': sorted(k for k, v in profile.tools.items() if v.enabled and v.access_mode == 'read_only')})


class MissionService:
    def __init__(self, factory, engine):
        self.factory, self.engine = factory, engine

    def preview(self, actor, body, *, delegated_goal=None):
        AlignmentService.operator(actor)
        envelope(body, ['alignmentRevisionId', 'agentProfile', 'options'])
        revision_id = body.get('alignmentRevisionId')
        if delegated_goal is None:
            with self.factory() as con, con.cursor() as c:
                confirmed = AlignmentService.latest(c, actor, confirmed=True)
                if not confirmed or confirmed['revision_id'] != revision_id:
                    raise HTTPException(409, '请先确认当前共同理解，再建立持续授权。')
                AlignmentService.project(confirmed)
                goal = {k: v['text'] for k, v in confirmed['document_json']['fields'].items()}
        else:
            goal = deepcopy(delegated_goal)
        if set(goal) != set(DEFAULT_GOAL):
            raise HTTPException(400, '目标需要包含五项共同理解。')
        goal = {k: text(v, 2000) for k, v in goal.items()}
        agent = body.get('agentProfile', 'future-battlefield-analyst')
        if agent not in ('future-battlefield-analyst', 'future-battlefield-codex'):
            raise HTTPException(409, '持续研究当前支持已就绪的未来战场研判基线。')
        profile = self.engine.loader.load(agent)
        from agent_tools.research import implementation_hash
        frozen = profile.model_policy.get('config_json', {}).get('researchImplementationHash')
        if frozen and frozen != implementation_hash():
            raise HTTPException(409, '当前运行代码与已发布基线不一致；请先完成修复发布，再建立持续授权。')
        from .phase3_repository import profile_budget_limits
        limits = profile_budget_limits(profile)
        options = body.get('options') or {}
        envelope(options, ['durationHours', 'dailyBudget', 'totalBudget', 'checkIntervalHours', 'maxRepairs'])
        hours, interval, repairs = options.get('durationHours', 24), options.get('checkIntervalHours', 6), options.get('maxRepairs', 2)
        if (type(hours) is not int or not 1 <= hours <= 168 or type(interval) is not int or
                not 1 <= interval <= 24 or type(repairs) is not int or not 0 <= repairs <= 2):
            raise HTTPException(400, '授权有效期1–168小时，检查间隔1–24小时，最多补证2轮。')
        daily, total = options.get('dailyBudget', limits), options.get('totalBudget', limits)
        for counters in (daily, total):
            if not isinstance(counters, dict) or set(counters) != set(limits) or any(
                    type(v) is not int or v < 0 or v > limits[k] for k, v in counters.items()):
                raise HTTPException(400, '持续预算须完整列出当前基线资源，且不超过基线配置额度。')
        from .dsh_api import _normalise_future_input
        request = {'question': goal['purpose'] + '\n交付标准：' + goal['successCriteria'] + '\n研究边界：' + goal['scope']
                   + '\n价值优先级：' + goal['priorities'] + '\n升级条件：' + goal['escalation'],
            'scope': {'layers': ['strategic', 'technical'], 'sourceTypes': ['official', 'research'],
                'geography': '全球公开研究与治理规范', 'summary': goal['purpose'],
                'constraints': goal['scope'] + '；限公开战略与通用研究，不涉及作战指令、目标选择或武器制造参数。'},
            'perspectives': [
                {'key': 'war-system', 'name': '体系形态的公开研究趋势', 'layer': 'strategic',
                 'question': '公开研究如何描述人机协同的组织与监督关系？只报告有直接引文支持的研究主张及适用条件。'},
                {'key': 'unmanned-autonomy', 'name': '人机协同及验证边界', 'layer': 'technical',
                 'question': '公开评测框架如何约束自主系统研究的能力声明、验证条件和人类监督？区分规范要求与已验证结果。'},
                {'key': 'law-escalation', 'name': '治理与责任边界', 'layer': 'strategic',
                 'question': 'NATO revised artificial intelligence strategy 对人类监督、责任与可追溯性提出哪些明确要求？只给出原文支持的有限主张。'}],
            'budget': {k: min(limits[k], daily[k], total[k]) for k in limits},
            'mode': 'unattended-draft', 'agentProfile': agent}
        data = _normalise_future_input(request['question'], request['scope'], request['perspectives'],
            request['budget'], request['mode'], agent, profile.version)
        from .runtime_contracts import validate_future_research_admission
        validate_future_research_admission(profile, data)
        result = {'schemaVersion': 'autopilot-grant.v1', 'goal': goal, 'alignmentRevisionId': revision_id,
            'request': request, 'agentName': profile.display_name, 'profileVersion': profile.version,
            'profileSignature': profile_signature(profile), 'durationHours': hours,
            'dailyBudget': daily, 'totalBudget': total, 'concurrency': 1,
            'checkIntervalHours': interval, 'maxRepairs': repairs,
            'allowedOperations': ['read-evidence', 'search-public', 'analyze', 'draft-report', 'delegate'],
            'publication': 'draft-only', 'automaticRenewal': False,
            'selectionPolicy': 'published-reviewed-baseline', 'authoritySource': 'explicit-user-delegation' if delegated_goal else 'human-ui'}
        result['previewHash'] = digest(result)
        return result

    def create(self, actor, body, *, delegation=None):
        AlignmentService.operator(actor)
        envelope(body, ['request', 'previewHash', 'idempotencyKey'], ['request', 'previewHash', 'idempotencyKey'])
        submission = key(body['idempotencyKey'])
        with self.factory() as con, con.cursor() as c:
            c.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', ('autopilot-owner:' + actor.principal_id,))
            c.execute('SELECT mission_id,authority_json FROM agent_autopilot_mission WHERE principal_id=%s AND submission_key=%s',
                (actor.principal_id, submission))
            existing = c.fetchone()
            if existing:
                if existing['authority_json']['submissionHash'] != digest(body):
                    raise HTTPException(409, '提交标识已用于不同的持续授权。')
                return {'missionId': existing['mission_id'], 'duplicate': True}
            # One live grant per person prevents parallel grants multiplying the
            # chosen daily ceiling. Renewal is explicit after stopping the old one.
            c.execute('''SELECT 1 FROM agent_autopilot_mission m JOIN agent_autopilot_mission_state s USING(mission_id)
                JOIN agent_autopilot_grant g ON g.grant_id=s.grant_id WHERE m.principal_id=%s
                AND s.status IN ('active','paused','needs-decision') AND g.expires_at>now()''', (actor.principal_id,))
            if c.fetchone():
                raise HTTPException(409, '已有持续授权，请先暂停并撤销，或等待到期后再建立新授权。')
            current = self.preview(actor, body['request'], delegated_goal=delegation['goal'] if delegation else None)
            if current['previewHash'] != body['previewHash']:
                raise HTTPException(409, '目标或基线已变化，请重新核对持续授权。')
            mid, gid = new_id('mission'), new_id('grant')
            proof = {'channel': 'user-interface', 'submissionHash': digest(body)}
            if delegation:
                proof.update(channel='codex-user-delegation', statement=text(delegation['statement'], 2000),
                             basis=deepcopy(delegation.get('basis') or []), agent='Codex')
            c.execute('''INSERT INTO agent_autopilot_mission(mission_id,principal_id,submission_key,alignment_revision_id,
                goal_json,goal_hash,authority_kind,authority_json) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)''',
                (mid, actor.principal_id, submission, current['alignmentRevisionId'], Json(current['goal']),
                 digest(current['goal']), current['authoritySource'], Json(proof)))
            deadline = datetime.now(timezone.utc) + timedelta(hours=current['durationHours'])
            c.execute('''INSERT INTO agent_autopilot_grant(grant_id,mission_id,revision,document_json,content_hash,expires_at)
                VALUES(%s,%s,1,%s,%s,%s)''', (gid, mid, Json(current), digest(current), deadline))
            c.execute("INSERT INTO agent_autopilot_mission_state(mission_id,grant_id,status) VALUES(%s,%s,'active')", (mid, gid))
            self.event(c, mid, gid, 'activated', 'authorized-delegate' if delegation else 'human',
                {'goalHash': digest(current['goal']), 'authoritySource': current['authoritySource']})
        return {'missionId': mid, 'grantId': gid, 'status': 'active', 'expiresAt': deadline}

    @staticmethod
    def event(c, mid, gid, kind, actor_kind, detail):
        c.execute('''INSERT INTO agent_autopilot_mission_event(mission_id,grant_id,event_type,actor_kind,detail_json)
            VALUES(%s,%s,%s,%s,%s)''', (mid, gid, kind, actor_kind, Json(detail)))

    @staticmethod
    def owned(c, actor, mid):
        c.execute('SELECT 1 FROM agent_autopilot_mission WHERE mission_id=%s AND principal_id=%s', (mid, actor.principal_id))
        if not c.fetchone():
            raise HTTPException(404, '未找到当前用户的持续目标。')

    def control(self, actor, mid, body, *, actor_kind='human'):
        AlignmentService.operator(actor)
        envelope(body, ['action', 'expectedRevision'], ['action', 'expectedRevision'])
        action = body['action']
        if action not in ('pause', 'resume', 'revoke'):
            raise HTTPException(400, '无效的持续授权操作。')
        with self.factory() as con, con.cursor() as c:
            self.owned(c, actor, mid)
            lock(c, mid)
            c.execute('SELECT * FROM agent_autopilot_mission_state WHERE mission_id=%s', (mid,))
            state = c.fetchone()
            target = {'pause': 'paused', 'resume': 'active', 'revoke': 'revoked'}[action]
            if state['status'] == target:
                return {'missionId': mid, 'status': target, 'duplicate': True}
            if type(body['expectedRevision']) is not int or state['revision'] != body['expectedRevision']:
                raise HTTPException(409, '授权状态已变化，请刷新后核对。')
            if state['status'] in ('revoked', 'expired', 'exhausted'):
                raise HTTPException(409, '此授权已终止，需要建立新的有限授权。')
            if action == 'resume':
                authority(c, mid, state['grant_id'], active=False)
                c.execute('SELECT expires_at>clock_timestamp() AS valid FROM agent_autopilot_grant WHERE grant_id=%s', (state['grant_id'],))
                if not c.fetchone()['valid']:
                    raise HTTPException(409, '授权已到期。')
                c.execute("UPDATE agent_autopilot_proposal SET status='proposed',updated_at=now() WHERE mission_id=%s AND status='failed' AND run_id IS NULL", (mid,))
            c.execute('''UPDATE agent_autopilot_mission_state SET status=%s,revision=revision+1,updated_at=now()
                WHERE mission_id=%s''', (target, mid))
            self.event(c, mid, state['grant_id'], {'pause': 'paused', 'resume': 'resumed', 'revoke': 'revoked'}[action], actor_kind, {})
        return {'missionId': mid, 'status': target}

    def view(self, actor):
        AlignmentService.operator(actor)
        with self.factory() as con, con.cursor() as c:
            c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            c.execute('''SELECT m.*,s.status,s.revision AS state_revision,s.next_check_at,g.grant_id,g.document_json,g.expires_at
                FROM agent_autopilot_mission m JOIN agent_autopilot_mission_state s USING(mission_id)
                JOIN agent_autopilot_grant g ON g.grant_id=s.grant_id WHERE m.principal_id=%s ORDER BY m.created_at DESC LIMIT 10''', (actor.principal_id,))
            missions = []
            for row in c.fetchall():
                mid = row['mission_id']
                c.execute('''SELECT p.proposal_id,p.status,p.run_id,p.attempt,p.error_code,p.created_at,t.kind,t.reference_json,
                    r.status AS run_status,a.document_json AS assessment FROM agent_autopilot_proposal p
                    JOIN agent_autopilot_trigger t USING(trigger_id) LEFT JOIN agent_run r ON r.run_id=p.run_id
                    LEFT JOIN agent_autopilot_assessment a USING(proposal_id) WHERE p.mission_id=%s ORDER BY p.created_at DESC LIMIT 20''', (mid,))
                proposals = list(c.fetchall())
                c.execute('''SELECT d.*,(SELECT jsonb_build_object('action',e.action,'createdAt',e.created_at) FROM agent_autopilot_decision_event e
                    WHERE e.decision_id=d.decision_id ORDER BY e.created_at DESC LIMIT 1) AS resolution
                    FROM agent_autopilot_decision d WHERE d.mission_id=%s ORDER BY d.created_at DESC LIMIT 20''', (mid,))
                decisions = list(c.fetchall())
                c.execute('SELECT event_type,actor_kind,detail_json,created_at FROM agent_autopilot_mission_event WHERE mission_id=%s ORDER BY event_id DESC LIMIT 20', (mid,))
                events = list(c.fetchall())
                from .mission_metrics import project
                metrics = project(c, mid, row['document_json'])
                missions.append({'missionId': mid, 'grantId': row['grant_id'], 'goal': row['goal_json'],
                    'authoritySource': row['authority_kind'], 'status': 'expired' if row['status'] in ('active','paused','needs-decision')
                        and row['expires_at'] <= datetime.now(timezone.utc) else row['status'],
                    'revision': row['state_revision'], 'expiresAt': row['expires_at'], 'nextCheckAt': row['next_check_at'],
                    'grant': row['document_json'], 'used': usage(c, mid), 'proposals': proposals, 'decisions': decisions,
                    'events': events, 'metrics': metrics})
        return {'schemaVersion': 'autopilot-supervision.v1', 'missions': missions, 'publication': 'draft-only',
                'capabilities': {'finiteGrants': True, 'autonomousResearch': True, 'humanDecisionInbox': True,
                                 'supportedProfiles': ['future-battlefield-analyst', 'future-battlefield-codex']}}

    def resolve(self, actor, decision_id, body):
        AlignmentService.operator(actor)
        envelope(body, ['action', 'contentHash', 'idempotencyKey'], ['action', 'contentHash', 'idempotencyKey'])
        action = body['action']
        if action not in ('keep-boundary', 'defer', 'request-new-grant'):
            raise HTTPException(400, '此决定不能直接扩大授权；请保持边界、暂缓或重新核对授权。')
        idem = key(body['idempotencyKey'])
        with self.factory() as con, con.cursor() as c:
            c.execute('''SELECT d.* FROM agent_autopilot_decision d JOIN agent_autopilot_mission m USING(mission_id)
                WHERE d.decision_id=%s AND m.principal_id=%s''', (decision_id, actor.principal_id))
            decision = c.fetchone()
            if not decision:
                raise HTTPException(404, '未找到此决定。')
            lock(c, decision['mission_id'])
            if decision['content_hash'] != body['contentHash']:
                raise HTTPException(409, '决定版本已变化。')
            c.execute('SELECT action FROM agent_autopilot_decision_event WHERE decision_id=%s ORDER BY created_at DESC LIMIT 1', (decision_id,))
            previous = c.fetchone()
            if previous and previous['action'] != 'defer':
                if previous['action'] == action:
                    return {'status': 'recorded', 'duplicate': True}
                raise HTTPException(409, '该版本已有决定。')
            c.execute('''INSERT INTO agent_autopilot_decision_event(event_id,decision_id,principal_id,action,idempotency_key)
                VALUES(%s,%s,%s,%s,%s) ON CONFLICT(decision_id,idempotency_key) DO NOTHING''',
                (new_id('decision-event'), decision_id, actor.principal_id, action, idem))
            self.event(c, decision['mission_id'], decision['grant_id'], 'decision-resolved', 'human', {'decisionId': decision_id, 'action': action})
        return {'status': 'recorded', 'authorizationExpanded': False, 'requiresNewGrant': action == 'request-new-grant'}


def create_mission_router(factory, engine, principal):
    service = MissionService(factory, engine)
    router = APIRouter(prefix='/autopilot', tags=['autopilot-missions'])

    def invoke(method, *args):
        try:
            return method(*args)
        except (ValueError, PermissionError) as error:
            raise HTTPException(409, '持续授权预检未通过，请核对已确认目标、执行基线与预算。') from None

    @router.get('/supervision')
    def supervision(actor=Depends(principal)):
        return invoke(service.view, actor)

    @router.post('/grants/preview')
    def preview(body: dict, actor=Depends(principal)):
        return invoke(service.preview, actor, body)

    @router.post('/grants', status_code=201)
    def create(body: dict, actor=Depends(principal)):
        return invoke(service.create, actor, body)

    @router.post('/missions/{mission_id}/control')
    def control(mission_id: str, body: dict, actor=Depends(principal)):
        return invoke(service.control, actor, mission_id, body)

    @router.post('/decisions/{decision_id}/resolve')
    def resolve(decision_id: str, body: dict, actor=Depends(principal)):
        return invoke(service.resolve, actor, decision_id, body)

    return router
