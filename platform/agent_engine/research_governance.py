"""Research collaboration projection and human-governed improvement lifecycle.

The model may submit a suggestion, never an approval. Candidate Runs use the
real Engine and frozen budgets. Independent deterministic evaluation checks
source integrity and report contracts; factual correctness remains unverified.
"""
from copy import deepcopy
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import replace
import json

from psycopg2.extras import Json

from .repository import as_json, new_id
from .execution_models import model_call_metadata, tool_call_metadata, execution_method, dispatch_metadata
from .task_intent import digest, read as read_intent, admission_actor
from .evaluation import EvaluationService
from agent_admin.config_publication import ConfigPublicationService, _hash, _diff

TERMINAL = {'completed','completed-with-gaps','requires-human-review','blocked','failed','cancelled'}
SUCCESS = {'completed','completed-with-gaps'}
KINDS = {'prompt','retrieval-instructions','report-structure','analysis-instructions'}


def validate_candidate(base, candidate):
    """First release supports instruction changes; executable authority is fixed."""
    before, after = deepcopy(base), deepcopy(candidate)
    for doc in (before,after):
        definition = doc.get('definition',{})
        definition.pop('system_prompt',None)
        definition.pop('current_version',None)
    if before != after:
        raise PermissionError('candidate may only change research instructions and version; authority, tools, budgets and approval rules are fixed')
    prompt = candidate['definition'].get('system_prompt')
    if not isinstance(prompt,str) or not 1 <= len(prompt) <= 100000:
        raise ValueError('candidate instructions must be bounded text')
    if candidate['definition']['current_version'] == base['definition']['current_version']:
        raise ValueError('candidate must select a new version')
    if candidate['definition'].get('system_prompt') == base['definition'].get('system_prompt'):
        raise ValueError('candidate has no instruction change')


def report_for(run):
    output = as_json(run.get('output_json'))
    return {**output, 'runId':run['run_id'], 'ontologyGraph':output.get('ontology') or
            (output.get('judgmentReport') or {}).get('ontology') or {}}


def audit_report(repo, run, required_perspectives):
    """Audits saved source artifacts, not a model's claims about its citations."""
    from .ontology_explorer import source_excerpt, evidence_index
    report = report_for(run)
    graph = report['ontologyGraph']
    evidence = evidence_index(report)
    assertions = graph.get('assertions') or []
    failed, verified = [], []
    for identity in evidence:
        try:
            source_excerpt(report,identity,repo)
            verified.append(identity)
        except (KeyError,ValueError,TypeError):
            failed.append(identity)
    present = {p.get('key') for p in report.get('perspectives',[]) if isinstance(p,dict)}
    covered = {a.get('perspectiveKey') for a in assertions if set(a.get('evidenceIds',[])) & set(verified)}
    assertion_links = all(a.get('evidenceIds') and set(a['evidenceIds']) <= set(verified) for a in assertions)
    return {'terminalSuccess':run['status'] in SUCCESS, 'perspectivesRetained':set(required_perspectives) <= present,
            'citationIntegrity':bool(evidence) and not failed and bool(assertions) and assertion_links,
            'truthNotAutoPromoted':report.get('truthVerified') is not True,
            'verifiedQuotes':len(verified),'invalidEvidence':failed,
            'supportedPerspectives':len(covered),'assertions':len(assertions),
            'factualCorrectness':'unverified'}


def audit_authority(repo, run_id):
    """Hard invariants derive from saved admission/reservation audit records."""
    from .task_intent import _chain
    with repo.connection_factory() as con:
        with con.cursor() as c:
            c.execute('''WITH RECURSIVE members AS (
                SELECT %s::text AS run_id UNION ALL SELECT m.run_id FROM agent_run_budget_member m
                JOIN members p ON m.parent_run_id=p.run_id
            ) SELECT run_id FROM members''',(run_id,))
            members = [r['run_id'] for r in c.fetchall()]
            granted,stopped,reservations = True,True,[]
            for member_id in members:
                chain = _chain(c,member_id)
                if not chain:
                    return {'authorizationInvariant':False,'stopInvariant':False,'authorityAudit':'unrecorded'}
                current = chain[0]['document_json']
                c.execute('SELECT tool_key FROM agent_tool_call WHERE run_id=%s',(member_id,))
                granted = granted and all(row['tool_key'] in current['allowedTools'] for row in c.fetchall())
                c.execute('SELECT created_at,charges_json FROM agent_budget_reservation WHERE run_id=%s',(member_id,))
                member_reservations = list(c.fetchall());reservations.extend(member_reservations)
                c.execute('SELECT min(created_at) AS revoked_at FROM agent_task_intent_event WHERE intent_id=ANY(%s)',([r['intent_id'] for r in chain],))
                revoked = c.fetchone()['revoked_at']
                deadline = min(row['expires_at'] for row in chain)
                stopped = stopped and all(row['created_at']<=deadline and (not revoked or row['created_at']<=revoked) for row in member_reservations)
    try:
        budget = repo.get_budget(run_id)
        within_budget = not any(budget.get('overrun',{}).values())
    except ValueError:
        within_budget = not reservations
    return {'authorizationInvariant':granted and within_budget,'stopInvariant':stopped,'authorityAudit':'saved-dispatch-reservations'}


def publication_gate(cursor, draft):
    """Called inside the existing atomic publication transaction."""
    cursor.execute("SELECT to_regclass(current_schema() || '.agent_improvement_proposal') AS name")
    if not cursor.fetchone()['name']:
        return
    cursor.execute('SELECT * FROM agent_improvement_proposal WHERE draft_id=%s',(draft['draft_id'],))
    proposal = cursor.fetchone()
    if not proposal:
        return
    if (draft['current_revision'] != proposal['draft_revision'] or draft['document_hash'] != proposal['candidate_hash']):
        raise PermissionError('candidate changed; evaluation and human approval are invalidated')
    cursor.execute('''SELECT d.*,a.passed,a.candidate_hash AS evaluated_hash FROM agent_release_decision d
        JOIN agent_improvement_evaluation a USING(assessment_id) WHERE d.proposal_id=%s
        ORDER BY d.created_at DESC,d.decision_id DESC LIMIT 1''',(proposal['proposal_id'],))
    decision = cursor.fetchone()
    if not decision or decision['decision'] != 'approved' or not decision['passed'] or \
            decision['candidate_hash'] != draft['document_hash'] or decision['evaluated_hash'] != draft['document_hash']:
        raise PermissionError('exact candidate evaluation and human release approval required')


class ResearchGovernance:
    def __init__(self, engine, queue):
        self.engine, self.queue = engine, queue
        self.factory = getattr(engine,'base_connection_factory',engine.repo.connection_factory)
        self.config = ConfigPublicationService(self.factory)

    def collaboration(self, run_id):
        with self.factory() as con:
            with con.cursor() as c:
                c.execute('''WITH RECURSIVE members AS (
                    SELECT r.run_id,NULL::text AS parent_run_id,0 AS depth,ARRAY[r.run_id] AS path
                    FROM agent_run r WHERE r.run_id=%s
                    UNION ALL SELECT b.run_id,b.parent_run_id,m.depth+1,m.path||b.run_id
                    FROM agent_run_budget_member b JOIN members m ON b.parent_run_id=m.run_id
                    WHERE NOT b.run_id=ANY(m.path) AND m.depth<64
                ) SELECT r.run_id,r.agent_key,r.agent_version,r.status,r.current_step_key,r.error_message,
                    r.model_call_count,r.tool_call_count,r.agent_layer,r.baseline_id,r.baseline_hash,
                    r.started_at,r.completed_at,r.input_json,r.output_json,m.parent_run_id,m.depth,p.profile_json,p.profile_hash,
                    p.profile_json->>'displayName' AS display_name
                    FROM members m JOIN agent_run r USING(run_id)
                    JOIN agent_run_profile_snapshot p USING(run_id) ORDER BY m.depth,r.created_at''',(run_id,))
                rows = list(c.fetchall())
                if not rows:
                    raise LookupError('Run not found')
                intent = read_intent(c,run_id)
                c.execute('''SELECT s.step_run_id,s.run_id,s.step_key,s.status,s.attempt,s.error_message,s.started_at,s.finished_at,
                    EXISTS(SELECT 1 FROM agent_approval_request a WHERE a.run_id=s.run_id AND a.step_key=s.step_key) AS human_gate_recorded
                    FROM agent_step_run s WHERE s.run_id=ANY(%s) ORDER BY s.started_at,s.attempt''',([r['run_id'] for r in rows],))
                steps = list(c.fetchall())
                c.execute('''SELECT call_id,run_id,step_run_id,provider_key,requested_model,actual_model,status,stage,error_code,created_at
                    FROM agent_model_call WHERE run_id=ANY(%s) ORDER BY created_at,call_id''',([r['run_id'] for r in rows],))
                model_calls = list(c.fetchall())
                c.execute('''SELECT call_id,run_id,step_run_id,tool_key,tool_version,status,created_at
                    FROM agent_tool_call WHERE run_id=ANY(%s) ORDER BY created_at,call_id''',([r['run_id'] for r in rows],))
                tool_calls = list(c.fetchall())
                c.execute('SELECT run_id,parent_step_run_id,parent_step_key,child_run_id,status,created_at,input_artifact_hash,output_artifact_hash FROM agent_action_invocation WHERE run_id=ANY(%s)',([r['run_id'] for r in rows],))
                actions = list(c.fetchall())
                c.execute('SELECT handoff_id,run_id AS parent_run_id,child_run_id FROM agent_run_handoff WHERE run_id=ANY(%s)',([r['run_id'] for r in rows],))
                handoffs = list(c.fetchall())
                c.execute("SELECT run_id,detail_json,created_at FROM agent_run_event WHERE run_id=ANY(%s) AND event_type='step.execution' ORDER BY event_id",([r['run_id'] for r in rows],))
                dispatches = list(c.fetchall())
        root = rows[0]
        identities = {row['run_id']: row for row in rows}
        for action in actions:
            child = identities.get(action['child_run_id'], {})
            action.update(agentKey=child.get('agent_key'), agentVersion=child.get('agent_version'),
                          displayName=child.get('display_name'))
        calls_by_run, calls_by_step = defaultdict(list), defaultdict(list)
        tools_by_run, tools_by_step = defaultdict(list), defaultdict(list)
        for call in model_calls:
            metadata = model_call_metadata(call)
            calls_by_run[call['run_id']].append(metadata)
            if call.get('step_run_id'):
                calls_by_step[(call['run_id'],call['step_run_id'])].append(metadata)
        for call in tool_calls:
            metadata = tool_call_metadata(call)
            tools_by_run[call['run_id']].append(metadata)
            if call.get('step_run_id'):
                tools_by_step[(call['run_id'],call['step_run_id'])].append(metadata)
        for row in rows:
            # A child Run's calls remain on its own card, never its parent's.
            row['skillInvocations'] = self.engine.repo.list_skill_invocations(row['run_id'])
            row['skillBindings'] = [{k:v for k,v in b.items() if k != 'package'} for b in (row['profile_json'].get('skillManifest') or {}).get('bindings',[])]
            row['llmCalls'] = calls_by_run[row['run_id']]
            row['toolCalls'] = tools_by_run[row['run_id']]
            row['dispatches'] = [dispatch_metadata(e) for e in dispatches if e['run_id'] == row['run_id']]
            row['delegations'] = [a for a in actions if a['run_id'] == row['run_id']]
            for action in row['delegations']:
                action['llmCalls'] = calls_by_run[action['child_run_id']]
                action['toolCalls'] = tools_by_run[action['child_run_id']]
            row['executionMethod'] = execution_method(models=row['llmCalls'], tools=row['toolCalls'],
                dispatches=row['dispatches'], delegations=row['delegations'],
                human=row['status'] == 'waiting-human')
            data, saved_output = as_json(row.pop('input_json', None)), as_json(row.pop('output_json', None))
            goal = data.get('question') or (data.get('commonSettings') or {}).get('taskGoal') or data.get('taskGoal') or ''
            row['taskGoal'] = goal
            row['taskTitle'] = goal[:64] + ('…' if len(goal) > 64 else '')
            ontology = saved_output.get('ontology') or (saved_output.get('judgmentReport') or {}).get('ontology') or {}
            has_report = bool(ontology or saved_output.get('findings') or saved_output.get('evidence'))
            row['resultSummary'] = {'reportAvailable': has_report,
                'assertions': len(ontology.get('assertions') or []) if has_report else None,
                'gaps': len(saved_output.get('evidenceGaps') or saved_output.get('gaps') or []) if has_report else None,
                'evidenceExcerpts': len(saved_output.get('evidence') or []) if has_report else None}
        profile = root.pop('profile_json')
        for row in rows[1:]:
            row.pop('profile_json',None)
        graph = deepcopy((profile.get('executionBaseline') or {}).get('graph') or {})
        latest, calls_by_phase, tools_by_phase = {}, defaultdict(list), defaultdict(list)
        for step in steps:
            key = (step['run_id'],step['step_key'])
            latest[key] = step
            calls_by_phase[key].extend(calls_by_step[(step['run_id'],step['step_run_id'])])
            tools_by_phase[key].extend(tools_by_step[(step['run_id'],step['step_run_id'])])
        for node in graph.get('nodes',[]):
            actual = latest.get((run_id,node['id']))
            node['execution'] = actual or {'status':'not-started'}
            node['skillInvocations'] = [i for i in root['skillInvocations'] if i['step_run_id'] in {s['step_run_id'] for s in steps if s['run_id'] == run_id and s['step_key'] == node['id']}]
            node['llmCalls'] = calls_by_phase[(run_id,node['id'])]
            node['toolCalls'] = tools_by_phase[(run_id,node['id'])]
            node['children'] = [a for a in actions if a['run_id']==run_id and a['parent_step_key']==node['id']]
            step_ids = {s['step_run_id'] for s in steps if s['run_id'] == run_id and s['step_key'] == node['id']}
            node['dispatches'] = [dispatch_metadata(e) for e in dispatches if e['run_id'] == run_id
                                  and (e.get('detail_json') or {}).get('stepRunId') in step_ids]
            node['executionMethod'] = execution_method(models=node['llmCalls'], tools=node['toolCalls'],
                dispatches=node['dispatches'], delegations=node['children'], observed=bool(actual),
                human=bool(actual and (actual['status'] == 'waiting-human' or actual.get('human_gate_recorded'))))
        try:
            budget = self.engine.repo.get_budget(run_id)
        except ValueError:
            budget = None
        saved_run = self.engine.repo.get_run(run_id)
        output = as_json(saved_run.get('output_json'))
        from .review_summary import stage_labels
        labels = stage_labels(saved_run)
        for node in graph.get('nodes', []):
            if node.get('id') in labels:
                node['sourceName'] = node.get('name')
                node['name'] = labels[node['id']]
        return {'runId':run_id,'taskIntent':intent,'root':root,'runs':rows,'baseline':graph,
            'steps':steps,'actions':actions,'handoffs':handoffs,'budget':budget,
            'completion':{'research':root['status'],
                'children':'none' if len(rows)==1 else 'completed' if all(r['status'] in SUCCESS for r in rows[1:]) else 'attention' if any(r['status'] in TERMINAL for r in rows[1:] if r['status'] not in SUCCESS) else 'running',
                'publication':output.get('publicationStatus','not-published')},
            'countingPolicy':'root budget is shared; child totals must not be added to root usage'}

    def proposals(self, run_id):
        with self.factory() as con:
            with con.cursor() as c:
                c.execute('SELECT * FROM agent_improvement_proposal WHERE source_run_id=%s ORDER BY created_at DESC',(run_id,))
                proposals = list(c.fetchall())
                for p in proposals:
                    c.execute('''SELECT b.*,r.status,r.model_call_count,r.tool_call_count FROM agent_improvement_candidate_run b
                        JOIN agent_run r USING(run_id) WHERE proposal_id=%s ORDER BY b.created_at DESC''',(p['proposal_id'],))
                    p['candidateRuns'] = list(c.fetchall())
                    c.execute('SELECT * FROM agent_improvement_evaluation WHERE proposal_id=%s ORDER BY created_at DESC',(p['proposal_id'],))
                    p['assessments'] = list(c.fetchall())
                    c.execute('SELECT * FROM agent_release_decision WHERE proposal_id=%s ORDER BY created_at DESC',(p['proposal_id'],))
                    p['decisions'] = list(c.fetchall())
                    draft = self.config._draft(c,p['draft_id'])
                    p['candidateCurrent'] = draft['current_revision']==p['draft_revision'] and draft['document_hash']==p['candidate_hash']
        return {'runId':run_id,'proposals':proposals,'automaticPublication':False,
                'supportedChanges':sorted(KINDS),'factualCorrectness':'unverified'}

    def proposal(self, proposal_id, cursor):
        cursor.execute('SELECT * FROM agent_improvement_proposal WHERE proposal_id=%s',(proposal_id,))
        row = cursor.fetchone()
        if not row:
            raise LookupError('proposal not found')
        draft = self.config._draft(cursor,row['draft_id'])
        if draft['current_revision'] != row['draft_revision'] or draft['document_hash'] != row['candidate_hash']:
            raise ValueError('candidate changed; create a new proposal and re-evaluate')
        validate_candidate(draft['base_document_json'],draft['document_json'])
        return dict(row),draft

    def propose(self, run_id, *, actor, actor_token, instructions=None, kind='prompt', origin='human'):
        if kind not in KINDS or origin not in {'human','model'}:
            raise ValueError('unsupported improvement type')
        run = self.engine.repo.get_run(run_id)
        if not run or run['status'] not in TERMINAL:
            raise ValueError('improvement proposals require a completed real Run')
        live = self.config.export_profile(actor,run['agent_key'],actor_token=actor_token)
        base = live['document']
        output = as_json(run.get('output_json'))
        with self.factory() as con:
            with con.cursor() as c:
                c.execute("SELECT event_id,event_type,step_key,detail_json FROM agent_run_event WHERE run_id=%s AND (event_type LIKE '%%failed%%' OR event_type LIKE '%%gap%%') ORDER BY event_id DESC LIMIT 30",(run_id,))
                failures = list(c.fetchall())
        if instructions is None:
            instructions = ('研究报告中，将可追溯结论、条件性假设和待补证问题分开表达。每项关系声明须引用当前 Run 保存的精确引文及来源；'
                '缺少证据时写明缺少的材料、判断受到的限制和下一步补证问题。不得以重复缺口文字代替多视角结论，不得把来源摘录验证等同于事实验证。')
        if not isinstance(instructions,str) or not 1 <= len(instructions.strip()) <= 12000:
            raise ValueError('improvement instructions must be bounded text')
        candidate = deepcopy(base)
        candidate['definition']['system_prompt'] = base['definition'].get('system_prompt','') + '\n\n研究改进要求：\n' + instructions.strip()
        candidate['definition']['current_version'] = str(base['definition']['current_version']).split('-improve-')[0] + '-improve-' + new_id('v').split('-')[-1][:8]
        validate_candidate(base,candidate)
        rationale = {'origin':origin,'kind':kind,'instructions':instructions.strip(),
            'sourceRunId':run_id,'sourceVersion':run['agent_version'],
            'observedGaps':deepcopy(output.get('evidenceGaps') or output.get('unresolvedQuestions') or []),
            'failures':failures,'risk':'增加的说明可能延长报告；精确引文验证不证明事实成立。',
            'regressionCases':['source-integrity','perspective-coverage','no-evidence-regression','authority-unchanged','stop-enforcement'],
            'humanCorrections':'not-supplied'}
        # Existing authenticated draft pipeline remains the authoring authority.
        proposal_id = new_id('proposal')
        with self.factory() as con:
            with con.cursor() as c:
                self.config._authorize(c,actor,actor_token)
                self.config._config_lock(c)
                _,_,current_hash = self.config._live(c,run['agent_key'])
                if current_hash != live['liveHash']:
                    raise ValueError('live Profile changed while drafting; retry against the current version')
                @contextmanager
                def borrowed():
                    yield con
                # Reuse the draft service within this transaction: an orphan
                # ungoverned draft must not survive a failed proposal insert.
                draft = ConfigPublicationService(borrowed).create_draft(actor,run['agent_key'],actor_token=actor_token,
                    document=candidate,reason='Research improvement from '+run_id)
                c.execute('''INSERT INTO agent_improvement_proposal(proposal_id,agent_key,source_run_id,draft_id,draft_revision,
                    base_live_hash,candidate_hash,diff_json,rationale_json,created_by) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
                    (proposal_id,run['agent_key'],run_id,draft['draftId'],draft['revision'],draft['baseLiveHash'],draft['documentHash'],Json(_diff(base,candidate)),Json(rationale), 'model-proposal' if origin=='model' else actor.principal_id))
        return {'proposalId':proposal_id,'draftId':draft['draftId'],'candidateHash':draft['documentHash'],'status':'proposed','requiresHumanApproval':True}

    def start_candidate(self, proposal_id, *, actor, actor_token):
        from .execution_baseline import build_profile_baseline, register_baseline
        with self.factory() as con:
            with con.cursor() as c:
                self.config._authorize(c,actor,actor_token)
                self.config._config_lock(c)
                p,draft = self.proposal(proposal_id,c)
                if p['rationale_json'].get('candidateScope')=='report-quality-on-frozen-fragments':
                    raise PermissionError('offline quality candidates are scheduled by their cycle; full research runs need separate validation')
                c.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('candidate:'+proposal_id,))
                _,_,live_hash = self.config._live(c,p['agent_key'])
                if live_hash != p['base_live_hash']:
                    raise ValueError('live Profile changed; create a fresh proposal')
                errors = self.config._validate(c,draft['document_json'],p['agent_key'])
                if errors:
                    raise ValueError('candidate configuration validation failed')
                c.execute('SELECT run_id FROM agent_improvement_candidate_run WHERE proposal_id=%s ORDER BY created_at DESC LIMIT 1',(proposal_id,))
                previous = c.fetchone()
                if previous:
                    previous_run = self.engine.repo.get_run(previous['run_id'])
                    if (previous_run['status'] not in {'failed','blocked','cancelled'} or
                        previous_run.get('model_call_count') or previous_run.get('tool_call_count')):
                        if previous_run['status'] == 'created':
                            self.queue.enqueue(previous['run_id'])
                        return {'runId':previous['run_id'],'duplicate':True}
                # Only instructions differ, verified above. All runtime grants
                # come from the current published Profile and are frozen anew.
                profile = self.engine.loader.load(p['agent_key'])
                from .skills import carry_forward_bindings,current_manifest
                candidate_version=draft['document_json']['definition']['current_version']
                carry_forward_bindings(c,p['agent_key'],profile.version,candidate_version,
                    {step.key for step in profile.steps},'improvement-candidate:'+actor.principal_id)
                profile = replace(profile,version=candidate_version,
                    skill_manifest=current_manifest(c,p['agent_key'],candidate_version),
                    system_prompt=draft['document_json']['definition']['system_prompt'])
                baseline = register_baseline(c,p['agent_key'],build_profile_baseline(profile),status='testing',created_by=actor.principal_id)
                profile = replace(profile,execution_baseline=baseline)
                source = self.engine.repo.get_run(p['source_run_id'])
                data = deepcopy(as_json(source.get('input_json')))
                for key in ('taskIntent','dshSessionId','dshTurnId','parentRunId','rootRunId','handoffId','upstreamArtifacts','previousRunId'):
                    data.pop(key,None)
                # Candidate comparison does not inherit a model-owned review.
                data['automationMode'] = 'unattended-draft'
                for section in ('commonSettings','specificSettings'):
                    if isinstance(data.get(section),dict):
                        for field in ('profileVersion','agentVersion'):
                            if field in data[section]:
                                data[section][field] = profile.version
                from .run_origin import origin_context
                with admission_actor(actor), origin_context(actor, source_kind='evolution', source_ref=proposal_id):
                    run_id = self.engine.repo.create_run(profile,data,_cursor=c)
                c.execute('INSERT INTO agent_improvement_candidate_run(run_id,proposal_id,candidate_hash) VALUES(%s,%s,%s)',(run_id,proposal_id,p['candidate_hash']))
        self.queue.enqueue(run_id)
        return {'runId':run_id,'candidateHash':p['candidate_hash'],'status':'queued','publicationStatus':'not-published'}

    def evaluate(self, proposal_id, *, actor, actor_token):
        # Independent evaluator writes complete immutable results once. A
        # session lock spans its short transactions and dataset registration.
        with self.factory() as con:
            with con.cursor() as c:
                c.execute("SELECT pg_advisory_lock(hashtextextended('research-release-evaluation',0))")
            try:
                return self._evaluate(proposal_id,actor=actor,actor_token=actor_token)
            finally:
                with con.cursor() as c:
                    c.execute("SELECT pg_advisory_unlock(hashtextextended('research-release-evaluation',0))")

    def _evaluate(self, proposal_id, *, actor, actor_token):
        with self.factory() as con:
            with con.cursor() as c:
                self.config._authorize(c,actor,actor_token)
                p,draft = self.proposal(proposal_id,c)
                if p['rationale_json'].get('candidateScope')=='report-quality-on-frozen-fragments':
                    raise PermissionError('cycle comparison must use its frozen case and evaluator')
                c.execute('SELECT * FROM agent_improvement_candidate_run WHERE proposal_id=%s ORDER BY created_at DESC LIMIT 1',(proposal_id,))
                candidate = c.fetchone()
                if not candidate:
                    raise ValueError('run the exact candidate before independent evaluation')
                c.execute('SELECT * FROM agent_improvement_evaluation WHERE proposal_id=%s AND candidate_run_id=%s',(proposal_id,candidate['run_id']))
                previous = c.fetchone()
                if previous:
                    return dict(previous)
        baseline = self.engine.repo.get_run(p['source_run_id'])
        actual = self.engine.repo.get_run(candidate['run_id'])
        if actual['status'] not in TERMINAL:
            raise ValueError('candidate Run is still executing')
        perspectives = [v['key'] for v in as_json(baseline.get('output_json')).get('perspectives',[]) if isinstance(v,dict) and v.get('key')]
        before, after = audit_report(self.engine.repo,baseline,perspectives),audit_report(self.engine.repo,actual,perspectives)
        intent = self.engine.repo.task_intent(actual['run_id'])
        task_valid = intent['status'] in {'active','expired'} and intent.get('document',{}).get('purpose')=='public-research'
        before_authority, after_authority = audit_authority(self.engine.repo,baseline['run_id']),audit_authority(self.engine.repo,actual['run_id'])
        checks = {**after,**after_authority,'authorityUnchanged':True,'taskIntentRecorded':task_valid,
            'noEvidenceRegression':after['supportedPerspectives'] >= before['supportedPerspectives'],
            'stopEnforcement':'enforced-at-gateway','qualityScope':'saved-source-integrity-and-report-coverage',
            'baseline':before,'candidateHash':p['candidate_hash']}
        hard = ['terminalSuccess','perspectivesRetained','citationIntegrity','truthNotAutoPromoted','authorityUnchanged','taskIntentRecorded','noEvidenceRegression','authorizationInvariant','stopInvariant']
        passed = all(checks[k] is True for k in hard)
        service = EvaluationService(self.factory)
        cases = [{'caseKey':key,'caseVersion':'1','caseType':'sufficient_data',
            'input':{'sourceRunId':p['source_run_id'],'perspectives':perspectives,'check':key},
            'expected':{'rules':{'fieldEquals':{'passed':True}}}} for key in hard]
        dataset = service.register_dataset('research-release-regression','v1-'+digest(cases)[:24],cases,
            description='Server audit of real saved research runs. No claim of semantic factual correctness.',
            metadata={'sourceRunId':p['source_run_id'],'factualCorrectness':'unverified'})
        eval_ids = []
        baseline_recorded = self.engine.repo.task_intent(baseline['run_id'])['status'] != 'unrecorded'
        baseline_profile_hash = self.engine.repo.profile_snapshot_hash(baseline['run_id'])
        for run, results, profile_hash in ((baseline,{**before,**before_authority,'authorityUnchanged':True,'taskIntentRecorded':baseline_recorded,'noEvidenceRegression':True},baseline_profile_hash), (actual,checks,p['candidate_hash'])):
            frozen = service.create_run(dataset['dataset_id'],profile_agent_key=p['agent_key'],profile_version=run['agent_version'],
                profile_hash=profile_hash,source_run_id=run['run_id'],source_versions={'regressionSource':p['source_run_id'],'datasetHash':dataset['dataset_hash']})
            for case in dataset['cases']:
                service.record_case_result(frozen['evaluation_run_id'],case['case_id'],{'passed':results[case['case_key']]},
                    actual_references={'runId':run['run_id'],'profileHash':profile_hash,'auditMethod':'server-source-artifact-audit'})
            eval_ids.append(frozen['evaluation_run_id'])
        comparison = service.compare(*eval_ids)
        checks['comparison'] = comparison
        assessment_id = new_id('assessment')
        with self.factory() as con:
            with con.cursor() as c:
                c.execute('''INSERT INTO agent_improvement_evaluation(assessment_id,proposal_id,candidate_hash,candidate_run_id,
                    evaluation_run_id,baseline_evaluation_run_id,checks_json,passed) VALUES(%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING''',
                    (assessment_id,proposal_id,p['candidate_hash'],actual['run_id'],eval_ids[1],eval_ids[0],Json(checks),passed))
        return {'assessmentId':assessment_id,'passed':passed,'checks':checks,'evaluationRunId':eval_ids[1],'baselineEvaluationRunId':eval_ids[0]}

    def decide(self, proposal_id, *, actor, actor_token, decision, reason):
        if actor.kind != 'human' or not actor.permits('admin'):
            raise PermissionError('human administrator release decision required')
        if decision not in {'approved','rejected'} or not isinstance(reason,str) or not 1<=len(reason.strip())<=2000:
            raise ValueError('explicit release decision and reason required')
        with self.factory() as con:
            with con.cursor() as c:
                self.config._authorize(c,actor,actor_token)
                self.config._config_lock(c)
                p,draft = self.proposal(proposal_id,c)
                _,_,live_hash = self.config._live(c,p['agent_key'])
                if live_hash != p['base_live_hash']:
                    raise ValueError('base version changed; approval is invalid')
                c.execute('SELECT * FROM agent_improvement_evaluation WHERE proposal_id=%s ORDER BY created_at DESC LIMIT 1',(proposal_id,))
                evaluation = c.fetchone()
                if not evaluation or (decision=='approved' and not evaluation['passed']):
                    raise ValueError('candidate has no passing independent assessment')
                decision_id = new_id('release-decision')
                c.execute('''INSERT INTO agent_release_decision(decision_id,proposal_id,candidate_hash,assessment_id,decision,actor_id,reason)
                    VALUES(%s,%s,%s,%s,%s,%s,%s)''',(decision_id,proposal_id,p['candidate_hash'],evaluation['assessment_id'],decision,actor.principal_id,reason.strip()))
        return {'decisionId':decision_id,'decision':decision,'candidateHash':p['candidate_hash'],'published':False}

    def rollback(self, publication_id, *, actor, actor_token, reason):
        """Create a reviewed successor draft; never rewrite frozen current Runs."""
        publication = self.config.get_publication(actor,publication_id,actor_token=actor_token)
        document = deepcopy(publication['document'])
        document['definition']['current_version'] = str(document['definition']['current_version']).split('-rollback-')[0]+'-rollback-'+new_id('v').split('-')[-1][:8]
        return self.config.create_draft(actor,publication['agentKey'],actor_token=actor_token,document=document,reason=reason)
