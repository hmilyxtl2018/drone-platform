"""Durable bounded discovery/planning/execution over published Agent baselines.

The coordinator uses deterministic scheduling and recorded gaps. Search and LLM
work are real Engine nodes and share the grant ledger. Timer triggers are labelled
freshness checks, never fabricated observations that a source actually changed.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from psycopg2.extras import Json

from .autopilot import digest
from .mission_budget import installed, authority, usage, lock, dispatch_context
from .mission_service import MissionService, profile_signature
from .repository import new_id
from .task_intent import admission_actor

TERMINAL = ('completed', 'completed-with-gaps', 'failed', 'blocked', 'cancelled', 'requires-human-review')
PUBLIC_RESEARCH_QUERIES = {
    'war-system': ['human AI teaming assurance framework public research', 'autonomous systems assurance evidence evaluation research'],
    'unmanned-autonomy': ['NIST AI RMF human oversight testing evaluation', 'human AI collaboration evaluation transparency research'],
    'law-escalation': ['NATO revised artificial intelligence strategy 2024 responsible use principles', 'NIST AI risk management framework accountability transparency human oversight'],
}


def graph_projection(output):
    graph = output.get('ontology') or (output.get('judgmentReport') or {}).get('ontology') or {}
    nodes = {n.get('id'): n for n in graph.get('nodes', []) if isinstance(n, dict)}
    def node_ref(identity):
        node = nodes.get(identity, {})
        return {'id': identity, 'label': node.get('label', identity), 'type': node.get('type')}
    result = []
    for assertion in graph.get('assertions', []):
        if not isinstance(assertion, dict):
            continue
        result.append({'assertionId': assertion.get('id'), 'subject': node_ref(assertion.get('subject')),
            'relation': assertion.get('predicate') or assertion.get('relation'),
            'object': node_ref(assertion.get('object')), 'perspectiveKey': assertion.get('perspectiveKey'),
            'conditions': assertion.get('conditions') or [], 'modality': assertion.get('modality'),
            'evidenceIds': assertion.get('evidenceIds') or []})
    return result


def assess(run, previous=None):
    output = run.get('output_json') or {}
    graph = graph_projection(output)
    evidence = {e.get('evidenceId') or e.get('candidateId') for e in output.get('evidence', []) if isinstance(e, dict)} - {None}
    supported = []
    gaps = []
    for finding in output.get('findings') or []:
        if not isinstance(finding, dict):
            continue
        perspective = finding.get('perspectiveKey')
        ids = finding.get('evidenceIds') or finding.get('supportingClaims') or []
        if (finding.get('supportReview') or {}).get('verdict') == 'supported' and ids and set(ids) <= evidence:
            supported.append(perspective)
        else:
            gaps.append(perspective)
    for gap in output.get('evidenceGaps') or []:
        if isinstance(gap, dict) and gap.get('perspectiveKey'):
            gaps.append(gap['perspectiveKey'])
    expected = [p['key'] for p in (run.get('input_json') or {}).get('perspectives', [])]
    gaps = list(dict.fromkeys(p for p in gaps + [p for p in expected if p not in supported] if p in expected))
    previous_graph = graph_projection(previous.get('output_json') or {}) if previous else []
    focus = (run.get('input_json') or {}).get('followupTarget', {}).get('perspectiveKey')
    compared_graph = [a for a in graph if not focus or a['perspectiveKey'] == focus]
    previous_graph = [a for a in previous_graph if not focus or a['perspectiveKey'] == focus]
    # These are differences of report statements, not entity identity alignment
    # or changes in reality. Keep qualifiers and Run-specific IDs in the record.
    def statement_key(item):
        return digest([item['subject']['label'], item['relation'], item['object']['label'],
            item['conditions'], item['modality'], item['perspectiveKey']])
    before = {statement_key(a): a for a in previous_graph}
    after = {statement_key(a): a for a in compared_graph}
    intact = bool(graph) and all(a['evidenceIds'] and set(a['evidenceIds']) <= evidence for a in graph)
    def source_versions(document):
        return {e['sourceUri']: {'contentHash': e.get('contentHash'),
                    'evidenceId': e.get('evidenceId') or e.get('candidateId')}
                for e in document.get('evidence', []) if isinstance(e, dict) and e.get('sourceUri')
                and (e.get('contentHash'))}
    old_sources, new_sources = source_versions(previous.get('output_json') or {}) if previous else {}, source_versions(output)
    source_changes = [{'sourceUri': uri, 'before': old_sources[uri], 'after': item}
        for uri, item in new_sources.items() if uri in old_sources and item['contentHash'] != old_sources[uri]['contentHash']]
    return {'schemaVersion': 'autopilot-assessment.v1', 'runId': run['run_id'],
        'executionStatus': run['status'], 'reportAvailable': bool(output), 'reportHash': digest(output),
        'evidenceCount': len(evidence), 'relationCount': len(graph), 'supportedPerspectives': supported,
        'unresolvedPerspectives': gaps, 'evidenceLinksComplete': intact,
        'structuralCheckPassed': run['status'] == 'completed' and intact and not gaps,
        'factualTruthVerified': False, 'evaluationScope': 'saved-report-structure-and-evidence-links',
        'previousRunId': previous['run_id'] if previous else None, 'sourceChanges': source_changes,
        'changes': {'scope': 'report-statement-difference', 'perspectiveKey': focus, 'realityChangeVerified': False,
            'added': [v for k, v in after.items() if k not in before][:30],
            'absent': [v for k, v in before.items() if k not in after][:30],
            'addedCount': len(set(after) - set(before)), 'absentCount': len(set(before) - set(after)),
            'truncated': len(set(after) - set(before)) > 30 or len(set(before) - set(after)) > 30}}


class MissionController:
    def __init__(self, engine, queue):
        self.engine, self.queue = engine, queue
        self.factory = getattr(engine, 'base_connection_factory', engine.repo.connection_factory)

    @staticmethod
    def transition(c, row, status, event, detail):
        c.execute('''UPDATE agent_autopilot_mission_state SET status=%s,revision=revision+1,updated_at=now()
            WHERE mission_id=%s''', (status, row['mission_id']))
        MissionService.event(c, row['mission_id'], row['grant_id'], event, 'system', detail)

    @staticmethod
    def decision(c, row, kind, why, proposal_id=None):
        c.execute('''SELECT p.proposal_id,p.attempt,p.run_id,p.status,r.status AS run_status
            FROM agent_autopilot_proposal p LEFT JOIN agent_run r USING(run_id)
            WHERE p.mission_id=%s ORDER BY p.created_at LIMIT 10''', (row['mission_id'],))
        attempts = [dict(item) for item in c.fetchall()]
        document = {'whyNow': why, 'goal': row['goal_json']['purpose'], 'recommendation': '保持当前边界，保留已完成结果；需要继续时另建有限授权。',
            'alternatives': ['keep-boundary', 'defer', 'request-new-grant'],
            'effectScope': {'missionId': row['mission_id'], 'grantId': row['grant_id'], 'proposalId': proposal_id},
            'noResponse': '不会扩权或自动批准；已授权且未受阻的工作可继续。',
            'attempts': '已记录 %s 次调度提议；运行和消耗见以下冻结依据。' % len(attempts),
            'runAttempts': attempts, 'budgetUsed': usage(c,row['mission_id']),
            'budgetLimits': row['document_json']['totalBudget'],
            'profileVersion': row['document_json']['profileVersion']}
        c.execute('''INSERT INTO agent_autopilot_decision(decision_id,mission_id,grant_id,proposal_id,kind,
            dedup_key,document_json,content_hash) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT(grant_id,dedup_key) DO NOTHING''',
            (new_id('decision'), row['mission_id'], row['grant_id'], proposal_id, kind, kind + ':' + (proposal_id or ''), Json(document), digest(document)))

    @staticmethod
    def propose(c, row, kind, reference, request, *, parent=None, attempt=0, dedup=None):
        dedup = dedup or kind + ':' + digest(reference)
        tid = new_id('trigger')
        c.execute('''INSERT INTO agent_autopilot_trigger(trigger_id,mission_id,grant_id,kind,dedup_key,reference_json,content_hash)
            VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(grant_id,dedup_key) DO NOTHING RETURNING trigger_id''',
            (tid, row['mission_id'], row['grant_id'], kind, dedup, Json(reference), digest(reference)))
        if not c.fetchone():
            return False
        c.execute('''INSERT INTO agent_autopilot_proposal(proposal_id,trigger_id,mission_id,grant_id,parent_proposal_id,
            attempt,request_json,content_hash,status) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,'proposed')''',
            (new_id('proposal'), tid, row['mission_id'], row['grant_id'], parent, attempt, Json(request), digest(request)))
        return True

    def evaluate(self, c, row):
        c.execute('''SELECT p.*,r.status AS run_status,r.output_json,r.input_json FROM agent_autopilot_proposal p
            JOIN agent_run r ON r.run_id=p.run_id WHERE p.mission_id=%s AND (p.status='queued' OR
                (p.status='evaluated' AND (r.status IN ('failed','blocked') OR EXISTS(
                    SELECT 1 FROM agent_autopilot_assessment a WHERE a.proposal_id=p.proposal_id
                    AND (jsonb_array_length(a.document_json->'unresolvedPerspectives')>0 OR jsonb_array_length(a.document_json->'sourceChanges')>0)))
                 AND p.attempt<%s
                 AND NOT EXISTS(SELECT 1 FROM agent_autopilot_proposal child WHERE child.parent_proposal_id=p.proposal_id)))
            AND r.status=ANY(%s) ORDER BY p.created_at''', (row['mission_id'], row['document_json']['maxRepairs'], list(TERMINAL)))
        for proposal in c.fetchall():
            c.execute('''SELECT r.* FROM agent_autopilot_proposal p JOIN agent_run r ON r.run_id=p.run_id
                WHERE p.mission_id=%s AND p.status='evaluated' AND p.run_id<>%s
                ORDER BY p.created_at DESC LIMIT 1''', (row['mission_id'], proposal['run_id']))
            previous = c.fetchone()
            run = {'run_id': proposal['run_id'], 'status': proposal['run_status'],
                'output_json': proposal['output_json'], 'input_json': proposal['input_json']}
            assessment = assess(run, previous)
            c.execute('''INSERT INTO agent_autopilot_assessment(assessment_id,proposal_id,run_id,report_hash,document_json,content_hash)
                VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT(proposal_id) DO NOTHING''',
                (new_id('assessment'), proposal['proposal_id'], proposal['run_id'], assessment['reportHash'], Json(assessment), digest(assessment)))
            c.execute("UPDATE agent_autopilot_proposal SET status='evaluated',updated_at=now() WHERE proposal_id=%s", (proposal['proposal_id'],))
            if row['status'] != 'active' or proposal['attempt'] >= row['document_json']['maxRepairs']:
                continue
            target = next(iter(assessment['unresolvedPerspectives']), None)
            if target and proposal['run_status'] in ('completed', 'completed-with-gaps'):
                request = deepcopy(proposal['request_json'])
                request['previousRunId'] = proposal['run_id']
                request['followupTarget'] = {'perspectiveKey': target}
                self.propose(c, row, 'evidence-gap', {'runId': proposal['run_id'], 'reportHash': assessment['reportHash'], 'perspectiveKey': target},
                    request, parent=proposal['proposal_id'], attempt=proposal['attempt'] + 1)
            elif proposal['run_status'] in ('failed', 'blocked'):
                # One automatic retry. Deterministic contract/admission errors
                # never enter this branch because they have no admitted Run.
                request = deepcopy(proposal['request_json'])
                request['previousRunId'] = proposal['run_id']
                keys = [p['key'] for p in request['perspectives']]
                target = 'law-escalation' if 'law-escalation' in keys else keys[0]
                request['followupTarget'] = {'perspectiveKey': target, 'queries': PUBLIC_RESEARCH_QUERIES.get(target, [])}
                self.propose(c, row, 'technical-retry', {'runId': proposal['run_id'], 'reportHash': assessment['reportHash']},
                    request, parent=proposal['proposal_id'], attempt=proposal['attempt'] + 1)
            elif assessment['sourceChanges']:
                # A saved source hash difference is an observation, not a change
                # in reality. A changed version is reviewed once per grant.
                change = assessment['sourceChanges'][0]
                request = deepcopy(proposal['request_json'])
                request['previousRunId'] = proposal['run_id']
                self.propose(c, row, 'source-change', {**change, 'runId': proposal['run_id'], 'previousRunId': assessment['previousRunId']},
                    request, parent=proposal['proposal_id'], attempt=proposal['attempt'] + 1,
                    dedup='source:' + digest([change['sourceUri'], change['after']['contentHash']]))

    def dispatch(self, c, row, proposal):
        from .runtime_contracts import future_budget_minimums
        from .dsh_api import _normalise_future_input
        from .task_contracts import validate_task_envelope
        doc = row['document_json']
        if digest(proposal['request_json']) != proposal['content_hash']:
            raise PermissionError('autopilot-proposal-integrity')
        profile = self.engine.loader.load(doc['request']['agentProfile'])
        from agent_tools.research import implementation_hash
        frozen = profile.model_policy.get('config_json', {}).get('researchImplementationHash')
        if profile_signature(profile) != doc['profileSignature'] or (frozen and frozen != implementation_hash()):
            self.decision(c, row, 'contract-change', '已发布 Agent 的基线或能力契约发生变化，旧授权不会自动继承新版本。', proposal['proposal_id'])
            self.transition(c, row, 'needs-decision', 'dispatch-limited', {'reason': 'contract-changed'})
            return
        now = datetime.now(timezone.utc)
        period_start = row['granted_at'] + timedelta(days=max(0, (now - row['granted_at']).days))
        total, daily = usage(c, row['mission_id']), usage(c, row['mission_id'], period_start)
        remaining = {k: min(v - total.get(k, 0), doc['dailyBudget'].get(k, 0) - daily.get(k, 0)) for k, v in doc['totalBudget'].items()}
        minimums = future_budget_minimums(profile)
        # A budget stop never marks a research result successful.
        if any(remaining.get(k, 0) < value for k, value in minimums.items()) or remaining.get('model_calls', 0) <= 0:
            self.decision(c, row, 'budget', '剩余累计资源无法覆盖一次完整基线；系统已停止新增研究，现有结果与缺口保留。', proposal['proposal_id'])
            c.execute("UPDATE agent_autopilot_proposal SET status='limited',error_code='grant-budget-insufficient' WHERE proposal_id=%s", (proposal['proposal_id'],))
            self.transition(c, row, 'exhausted', 'exhausted', {'reason': 'below-baseline-minimum'})
            return
        request = proposal['request_json']
        budget = {k: min(v, remaining.get(k, 0)) for k, v in doc['request']['budget'].items()}
        scope = deepcopy(request['scope'])
        scope.setdefault('geography', '全球公开研究与治理规范')
        scope.setdefault('summary', row['goal_json']['purpose'])
        scope.setdefault('constraints', row['goal_json']['scope'] + '；仅公开研究与治理，不形成作战指令或武器制造参数。')
        question = request['question']
        data = _normalise_future_input(question, scope, request['perspectives'], budget,
            'unattended-draft', profile.agent_key, profile.version)
        data['taskIntent'] = {'expiresAt': min(row['expires_at'], now + timedelta(hours=23)).isoformat(), 'budgets': budget}
        if request.get('previousRunId'):
            data.update(previousRunId=request['previousRunId'], followupMode='targeted-evidence' if request.get('followupTarget') else 'technical-retry')
        if request.get('followupTarget'):
            data['followupTarget'] = deepcopy(request['followupTarget'])
        session, turn = 'dsh-' + row['mission_id'], 'dsh-' + proposal['proposal_id']
        data.update(dshSessionId=session, dshTurnId=turn)
        validate_task_envelope(profile.agent_key, profile, data)
        self.engine.plan_validator.validate_runtime(profile)
        c.execute('''INSERT INTO dsh_session(session_id,principal_id,tenant_id,agent_profile,metadata_json)
            VALUES(%s,%s,'local',%s,%s) ON CONFLICT(session_id) DO NOTHING''',
            (session, row['principal_id'], profile.agent_key, Json({'client': 'autopilot', 'missionId': row['mission_id'], 'grantId': row['grant_id']})))
        from .run_origin import origin_context
        # The mission owner is derived from the already validated Mission row;
        # the system dispatcher itself is never recorded as a human owner.
        with dispatch_context(row['mission_id'], row['grant_id'], proposal['proposal_id']), admission_actor(
                SimpleNamespace(principal_id=row['grant_id'], kind='system')), origin_context(
                    source_kind='autopilot', source_ref=row['mission_id']):
            rid = self.engine.repo.create_run(profile, data, _cursor=c)
        c.execute('''INSERT INTO dsh_turn(turn_id,session_id,run_id,idempotency_key,question,input_json,status)
            VALUES(%s,%s,%s,%s,%s,%s,'queued')''', (turn, session, rid, proposal['proposal_id'], request['question'], Json(data)))
        c.execute("UPDATE agent_autopilot_proposal SET status='queued',run_id=%s,error_code=NULL,updated_at=now() WHERE proposal_id=%s", (rid, proposal['proposal_id']))
        self.engine.repo.event(c, rid, 'autopilot.dispatched', None, {'missionId': row['mission_id'], 'grantId': row['grant_id'],
            'triggerId': proposal['trigger_id'], 'proposalId': proposal['proposal_id'], 'automatic': True,
            'queryPlanning': {'method': 'frozen-profile-source-plan', 'literalGoalHash': row['goal_hash'],
                'questionPreserved': question == request['question']}})
        c.execute('INSERT INTO agent_run_queue(run_id,pool_key) VALUES(%s,%s)', (rid, self.queue.pool_key))

    def tick(self):
        with self.factory() as con, con.cursor() as c:
            if not installed(c):
                return False
            c.execute("SELECT pg_try_advisory_xact_lock(hashtextextended('autopilot-controller',0)) AS acquired")
            if not c.fetchone()['acquired']:
                return False
            c.execute('''SELECT s.mission_id,s.grant_id FROM agent_autopilot_mission_state s
                WHERE s.status='active' OR EXISTS(SELECT 1 FROM agent_autopilot_proposal p WHERE p.mission_id=s.mission_id AND p.status='queued')
                ORDER BY s.updated_at ASC LIMIT 20''')
            for state in c.fetchall():
                c.execute('SAVEPOINT autopilot_mission')
                try:
                    self.process_mission(c, state)
                    c.execute('RELEASE SAVEPOINT autopilot_mission')
                except Exception as error:
                    c.execute('ROLLBACK TO SAVEPOINT autopilot_mission')
                    c.execute("UPDATE agent_autopilot_mission_state SET status='paused',revision=revision+1,updated_at=now() WHERE mission_id=%s", (state['mission_id'],))
                    MissionService.event(c, state['mission_id'], state['grant_id'], 'dispatch-limited', 'system',
                        {'reason': 'coordinator-check-failed', 'errorType': type(error).__name__})
                    c.execute('RELEASE SAVEPOINT autopilot_mission')
            return True

    def process_mission(self, c, state):
        row = authority(c, state['mission_id'], state['grant_id'], active=False)
        self.evaluate(c, row)
        if row['status'] != 'active':
            return
        try:
            row = authority(c, state['mission_id'], state['grant_id'])
        except PermissionError as error:
            reason = str(error)
            if reason == 'autopilot-goal-changed':
                self.decision(c, row, 'goal-change', '共同理解出现新的人确认版本，需要重新核对持续授权。')
                self.transition(c, row, 'needs-decision', 'dispatch-limited', {'reason': reason})
            else:
                self.transition(c, row, 'expired', 'expired', {'reason': reason})
            return
        c.execute('''SELECT count(*) AS total FROM agent_autopilot_run_binding b JOIN agent_run r USING(run_id)
            WHERE b.mission_id=%s AND r.status<>ALL(%s)''', (row['mission_id'], list(TERMINAL)))
        if c.fetchone()['total']:
            return
        # Do not wait for the next six-hour timer to expose an already-spent
        # grant. In-flight requests may still settle before this check runs.
        from .runtime_contracts import future_budget_minimums
        now = datetime.now(timezone.utc)
        start = row['granted_at'] + timedelta(days=max(0, (now - row['granted_at']).days))
        total, daily = usage(c, row['mission_id']), usage(c, row['mission_id'], start)
        remaining = {k: min(v - total.get(k, 0), row['document_json']['dailyBudget'][k] - daily.get(k, 0))
            for k, v in row['document_json']['totalBudget'].items()}
        minimums = future_budget_minimums(self.engine.loader.load(row['document_json']['request']['agentProfile']))
        if any(remaining.get(k, 0) < value for k, value in minimums.items()) or remaining.get('model_calls', 0) <= 0:
            self.decision(c, row, 'budget', '剩余累计资源无法覆盖一次完整基线；系统已停止新增研究，现有结果与缺口保留。')
            self.transition(c, row, 'exhausted', 'exhausted', {'reason': 'below-baseline-minimum'})
            return
        c.execute('SELECT count(*) AS total FROM agent_autopilot_proposal WHERE mission_id=%s', (row['mission_id'],))
        if c.fetchone()['total'] == 0:
            self.propose(c, row, 'goal-activated', {'goalHash': row['goal_hash'], 'grantId': row['grant_id']}, row['document_json']['request'])
        from .connectors.events import propose_saved_changes
        propose_saved_changes(c, self, row)
        c.execute('SELECT next_check_at<=now() AS due FROM agent_autopilot_mission_state WHERE mission_id=%s', (row['mission_id'],))
        due = c.fetchone()['due']
        c.execute("SELECT * FROM agent_autopilot_proposal WHERE mission_id=%s AND status='proposed' ORDER BY created_at LIMIT 1", (row['mission_id'],))
        proposal = c.fetchone()
        if not proposal and due:
            interval = row['document_json']['checkIntervalHours']
            slot = int((datetime.now(timezone.utc) - row['granted_at']).total_seconds() // (interval * 3600))
            if slot > 0:
                self.propose(c, row, 'freshness-check', {'intervalSlot': slot, 'goalHash': row['goal_hash'], 'reason': 'scheduled-source-freshness-check'},
                    row['document_json']['request'], dedup='freshness:' + str(slot))
                c.execute("SELECT * FROM agent_autopilot_proposal WHERE mission_id=%s AND status='proposed' ORDER BY created_at LIMIT 1", (row['mission_id'],))
                proposal = c.fetchone()
        if proposal:
            c.execute('SAVEPOINT autopilot_admission')
            try:
                self.dispatch(c, row, proposal)
                c.execute('RELEASE SAVEPOINT autopilot_admission')
            except Exception as error:
                c.execute('ROLLBACK TO SAVEPOINT autopilot_admission')
                c.execute("UPDATE agent_autopilot_proposal SET status='failed',error_code='baseline-admission-failed' WHERE proposal_id=%s", (proposal['proposal_id'],))
                self.transition(c, row, 'paused', 'dispatch-limited', {'reason': 'baseline-admission-failed',
                    'proposalId': proposal['proposal_id'], 'errorType': type(error).__name__})
        c.execute("UPDATE agent_autopilot_mission_state SET next_check_at=now()+(%s * interval '1 hour') WHERE mission_id=%s", (row['document_json']['checkIntervalHours'], row['mission_id']))
