"""Trusted, cycle-scoped draft authoring and a single frozen-material smoke pair.

This service never impersonates an administrator. Its credential-free identity
can write only the exact catalog-rule annex justified by the bound designer Run.
Public configuration, approval and publication services retain human-only auth.
"""
from contextlib import contextmanager
from copy import deepcopy
from types import SimpleNamespace
from psycopg2.extras import Json
from agent_admin.config_publication import ConfigPublicationService,_diff,_hash
from .repository import new_id
from .task_intent import digest,admission_actor
from .research_candidate import (DESIGNER,QUALITY_AGENT,SERVICE,DESIGN_PROMPT,DESIGN_SCHEMA,
    QUALITY_PROMPT,QUALITY_SCHEMA,RULES,instructions,validate_design,frozen_case,score_case)
from .research_reflection import reflection_profile

EVALUATOR='report-quality-contract.v1'
ACTIVE={'reflecting','designing','trial-running','evaluating'}


def installed(c):
    c.execute("SELECT to_regclass(current_schema() || '.agent_evolution_trial') AS name")
    return bool(c.fetchone()['name'])


def prepare(c,cycle_id,event,source_profile):
    ConfigPublicationService._config_lock(c)
    base,_,live_hash=ConfigPublicationService._live(c,source_profile.agent_key)
    if (base['definition']['system_prompt']!=source_profile.system_prompt or
        base['definition']['current_version']!=source_profile.version):
        raise PermissionError('source Profile is no longer the published baseline')
    case=frozen_case(event['source_run_id'],event['source_output_json'],event['source_output_hash'])
    c.execute('''INSERT INTO agent_evolution_trial(cycle_id,agent_key,base_live_hash,base_document_json,
        case_json,case_hash,evaluator_version) VALUES(%s,%s,%s,%s,%s,%s,%s)''',
        (cycle_id,source_profile.agent_key,live_hash,Json(base),Json(case),digest(case),EVALUATOR))


def _submit(controller,c,cycle,role,profile,data):
    from .task_intent import assert_dispatch
    # Source authority, current policy, live Profile and cycle stop have already
    # been checked under locks. Creation, binding and queue admission are atomic.
    assert_dispatch(c,cycle['source_run_id'],operation='read-evidence')
    data={**data,'sourceRunId':cycle['source_run_id'],'scope':{'purpose':'report-quality-only'},
        'taskIntent':{'allowedOperations':['read-evidence','analyze'],'allowedTools':[],
            'budgets':profile.resource_policy['budgetLimits'],'expiresAt':cycle['expires_at'].isoformat()}}
    from .run_origin import origin_context
    with admission_actor(SimpleNamespace(principal_id=SERVICE,kind='system')), \
            origin_context(source_kind='evolution', source_ref=cycle['cycle_id']):
        rid=controller.engine.repo.create_run(profile,data,_cursor=c)
    c.execute('INSERT INTO agent_evolution_run_binding(run_id,cycle_id,role,input_hash) VALUES(%s,%s,%s,%s)',
        (rid,cycle['cycle_id'],role,digest(data)))
    c.execute('INSERT INTO agent_run_queue(run_id,pool_key,priority) VALUES(%s,%s,0)',(rid,controller.queue.pool_key))
    controller.repo.event(c,cycle['cycle_id'],role+'.queued',{'runId':rid,'caseHash':data.get('caseHash')})
    return rid


def _roles(c,cycle_id):
    c.execute('''SELECT b.role,b.run_id,b.input_hash,r.status,r.output_json,r.input_json,p.profile_json
        FROM agent_evolution_run_binding b JOIN agent_run r USING(run_id)
        JOIN agent_run_profile_snapshot p USING(run_id) WHERE cycle_id=%s''',(cycle_id,))
    return {r['role']:dict(r) for r in c.fetchall()}


def _status(controller,c,cycle,status,reason=None):
    c.execute('''UPDATE agent_evolution_cycle SET status=%s,stop_reason=%s,revision=revision+1,updated_at=now()
        WHERE cycle_id=%s''',(status,reason,cycle['cycle_id']))
    controller.repo.event(c,cycle['cycle_id'],'trial.'+status,{'reason':reason})


def _proposal(controller,c,cycle,trial,designer):
    from .research_governance import validate_candidate
    from .evolution_budget import assert_dispatch
    assert_dispatch(c,designer['run_id'],operation='analyze')
    design=validate_design({k:designer['output_json'][k] for k in ['summary','selectedRules']},
        designer['input_json']['modelInput']['references'])
    if not design['selectedRules']:return None
    base=trial['base_document_json'];annex=instructions(design)
    if annex.strip() in base['definition']['system_prompt']:return None
    candidate=deepcopy(base)
    candidate['definition']['system_prompt']+=annex
    candidate['definition']['current_version']=base['definition']['current_version'].split('-quality-')[0]+'-quality-'+digest(design)[:10]
    validate_candidate(base,candidate)
    if ConfigPublicationService._live(c,trial['agent_key'])[2]!=trial['base_live_hash']:
        raise PermissionError('baseline changed before candidate authoring')
    c.execute("SELECT kind,enabled,roles_json FROM agent_admin_identity WHERE principal_id=%s",(SERVICE,));service=c.fetchone()
    if not service or service['kind']!='service' or not service['enabled'] or service['roles_json']:
        raise PermissionError('restricted draft service is unavailable')
    # Exact executable diff is constructed here, not accepted from a model/API.
    draft_id=ConfigPublicationService._insert_draft(c,trial['agent_key'],base,trial['base_live_hash'],candidate,
        SERVICE,'Report quality candidate for '+cycle['cycle_id'])
    pid=new_id('proposal')
    rationale={'origin':'cycle-service','kind':'report-structure','instructions':annex.strip(),
        'candidateScope':'report-quality-on-frozen-fragments','cycleId':cycle['cycle_id'],
        'designerRunId':designer['run_id'],'selectedRules':design['selectedRules'],
        'sourceRunId':cycle['source_run_id'],'caseHash':trial['case_hash'],
        'risk':'仅验证固定报告片段的质量契约；未证明完整研究效果或事实真实性。',
        'observedGaps':[],'failures':[],'publicationEligible':False}
    c.execute('''INSERT INTO agent_improvement_proposal(proposal_id,agent_key,source_run_id,draft_id,draft_revision,
        base_live_hash,candidate_hash,diff_json,rationale_json,created_by)
        VALUES(%s,%s,%s,%s,1,%s,%s,%s,%s,%s)''',
        (pid,trial['agent_key'],cycle['source_run_id'],draft_id,trial['base_live_hash'],_hash(candidate),
            Json(_diff(base,candidate)),Json(rationale),SERVICE))
    c.execute('INSERT INTO agent_evolution_proposal_binding(cycle_id,proposal_id,designer_run_id) VALUES(%s,%s,%s)',
        (cycle['cycle_id'],pid,designer['run_id']))
    controller.repo.event(c,cycle['cycle_id'],'candidate.authored',{'proposalId':pid,'candidateHash':_hash(candidate),
        'ruleKeys':[r['key'] for r in design['selectedRules']],'automaticPublication':False})
    return {'proposalId':pid,'document':candidate,'candidateHash':_hash(candidate)}


def _evaluate(controller,c,cycle,trial,roles):
    from .evaluation import EvaluationService
    from .evolution_budget import usage,assert_dispatch
    from .research_governance import validate_candidate
    assert_dispatch(c,roles['candidate']['run_id'],operation='analyze')
    c.execute('''SELECT p.* FROM agent_evolution_proposal_binding b JOIN agent_improvement_proposal p USING(proposal_id)
        WHERE b.cycle_id=%s''',(cycle['cycle_id'],));proposal=c.fetchone()
    draft=ConfigPublicationService._draft(c,proposal['draft_id'])
    if draft['current_revision']!=proposal['draft_revision'] or draft['document_hash']!=proposal['candidate_hash']:
        raise PermissionError('candidate draft changed; comparison invalidated')
    validate_candidate(trial['base_document_json'],draft['document_json'])
    if digest(trial['case_json'])!=trial['case_hash']:
        raise PermissionError('trial case changed; comparison invalidated')
    case=trial['case_json']
    before=score_case(case,{'reviews':roles['baseline']['output_json'].get('reviews')})
    after=score_case(case,{'reviews':roles['candidate']['output_json'].get('reviews')})
    paired=(roles['baseline']['input_json']['modelInput']==roles['candidate']['input_json']['modelInput'] and
        roles['baseline']['input_json']['caseHash']==roles['candidate']['input_json']['caseHash']==trial['case_hash'])
    model=lambda row:{k:row['profile_json'].get('modelPolicy',{}).get(k) for k in
        ['model_provider','model_name','temperature','max_output_tokens','providerSnapshot']}
    same_model=model(roles['baseline'])==model(roles['candidate'])
    inputs_intact=all(digest(roles[key]['input_json'])==roles[key]['input_hash'] for key in ['baseline','candidate'])
    targets_intact=all(roles[key]['input_json']['reportInstructions']==doc['definition']['system_prompt'] and
        roles[key]['input_json']['targetProfileHash']==_hash(doc) and
        roles[key]['input_json']['targetVersion']==doc['definition']['current_version']
        for key,doc in [('baseline',trial['base_document_json']),('candidate',draft['document_json'])])
    used=usage(c,cycle['cycle_id'])
    within=all(v<=cycle['limits_json'].get(k,0) for k,v in used.items())
    improved=after['errorCount']<before['errorCount'] and after['contractValid']
    # This batch records a genuine smoke pair. A single source cannot silently
    # stand in for a sealed four-category, independent release regression set.
    if not all([paired,same_model,within,inputs_intact,targets_intact,before['contractValid'],after['contractValid']]):
        raise PermissionError('paired comparison invariants failed')
    # Promotion is deliberately unavailable in this smoke-only evaluator, even
    # if a stored case incorrectly claims complete regression coverage.
    passed=False
    outcome='insufficient-validation' if improved else 'not-improved'
    checks={'comparisonScope':case['scope'],'evaluatorVersion':EVALUATOR,'pairedInput':paired,'sameModel':same_model,
        'budgetWithinLimits':within,'authorityUnchanged':True,'inputsIntact':inputs_intact,'targetsIntact':targets_intact,
        'contractValid':after['contractValid'],
        'benefitObserved':improved,'validationCoverageComplete':False,'publicationEligible':False,
        'sourceCaseCount':1,'caseTypes':case['caseTypes'],'missingCoverage':case['missingCoverage'],
        'baseline':before,'candidate':after,'primaryMetric':'report-contract-error-count',
        'truthVerified':False,'candidateHash':proposal['candidate_hash'],'caseHash':trial['case_hash'],
        'cycleUsage':used,'outcome':outcome,'limitations':case['limitations']}
    @contextmanager
    def borrowed():
        # Never enter the psycopg connection's own context here: that would
        # commit/close the controller transaction between evaluation methods.
        yield c.connection
    evaluator=EvaluationService(borrowed)
    cases=[{'caseKey':ref,'caseVersion':EVALUATOR,'caseType':case['caseTypes'][0],
        'input':{'sourceRunId':cycle['source_run_id'],'sourceOutputHash':cycle['source_output_hash'],
            'caseHash':trial['case_hash'],'fieldPath':ref},
        'expected':{'rules':{'fieldEquals':{'classification':expected}}}} for ref,expected in case['expected'].items()]
    dataset=evaluator.register_dataset('report-quality-smoke','v1-'+trial['case_hash'][:24],cases,
        description='Real saved report fields; deterministic contract labels; no factual truth scoring.',
        metadata={'scope':case['scope'],'coverageComplete':False,'sourceRunId':cycle['source_run_id']})
    eval_ids=[]
    for role in ['baseline','candidate']:
        run=roles[role];prompt=run['input_json']['reportInstructions']
        frozen=evaluator.create_run(dataset['dataset_id'],profile_agent_key=trial['agent_key'],
            profile_version=run['input_json']['targetVersion'],profile_hash=run['input_json']['targetProfileHash'],
            prompt=prompt,source_run_id=run['run_id'],source_versions={'caseHash':trial['case_hash'],
                'sourceOutputHash':cycle['source_output_hash'],'evaluatorVersion':EVALUATOR},
            execution_mode='paid_model',provider_key=run['profile_json'].get('modelPolicy',{}).get('model_provider'),
            model_name=run['profile_json'].get('modelPolicy',{}).get('model_name'))
        answers={r['refId']:r['classification'] for r in run['output_json']['reviews']}
        for item in dataset['cases']:
            evaluator.record_case_result(frozen['evaluation_run_id'],item['case_id'],
                {'classification':answers.get(item['case_key'])},actual_references={'runId':run['run_id'],
                    'caseHash':trial['case_hash'],'fieldPath':item['case_key']})
        evaluator.collect_usage(frozen['evaluation_run_id'],run['run_id'])
        eval_ids.append(frozen['evaluation_run_id'])
    checks['comparison']=evaluator.compare(*eval_ids)
    aid=new_id('assessment')
    c.execute('''INSERT INTO agent_improvement_evaluation(assessment_id,proposal_id,candidate_hash,candidate_run_id,
        evaluation_run_id,baseline_evaluation_run_id,checks_json,passed) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)''',
        (aid,proposal['proposal_id'],proposal['candidate_hash'],roles['candidate']['run_id'],eval_ids[1],eval_ids[0],Json(checks),passed))
    controller.repo.event(c,cycle['cycle_id'],'trial.evaluated',{'assessmentId':aid,'outcome':outcome,
        'baselineErrors':before['errorCount'],'candidateErrors':after['errorCount'],'automaticPublication':False})
    _status(controller,c,cycle,outcome,'收益未证实：缺少封存回归验证' if improved and not passed else
        '当前固定材料未观察到质量改善' if not improved else None)


def advance(controller,c):
    if not installed(c):return False
    c.execute('''SELECT e.cycle_id FROM agent_evolution_cycle e WHERE e.max_trials=1
        AND e.status=ANY(%s) ORDER BY e.created_at LIMIT 10''',(sorted(ACTIVE),))
    ids=[r['cycle_id'] for r in c.fetchall()]
    for cid in ids:
        c.execute('''SELECT e.*,b.expires_at,b.limits_json,p.document_json AS policy_json
            FROM agent_evolution_cycle e JOIN agent_evolution_budget b USING(cycle_id)
            JOIN agent_evolution_policy_revision p USING(policy_id) WHERE e.cycle_id=%s''',(cid,))
        cycle=dict(c.fetchone());roles=_roles(c,cid)
        from .evolution_budget import assert_dispatch
        c.execute('SAVEPOINT evolution_trial_stage')
        try:
            # Acquires source -> policy -> cycle -> configuration locks in the
            # same order as provider admission. A stop wins before new work.
            assert_dispatch(c,roles['reflection']['run_id'],operation='analyze')
            c.execute('SELECT status FROM agent_evolution_cycle WHERE cycle_id=%s',(cid,))
            if c.fetchone()['status']!=cycle['status']:continue
            c.execute('SELECT * FROM agent_evolution_trial WHERE cycle_id=%s',(cid,));trial=dict(c.fetchone())
            source=controller.engine.loader.load_for_run(cycle['source_run_id'])
            if cycle['status']=='reflecting':
                reflection=roles['reflection']
                if reflection['status'] not in {'completed','failed','blocked','cancelled'}:continue
                if reflection['status']!='completed':raise ValueError('reflection did not complete')
                if not reflection['output_json'].get('issues'):
                    _status(controller,c,cycle,'not-improved','复盘没有支持候选改动的问题');return True
                data={'question':'设计一个最小报告质量候选','modelInput':{
                    'reflection':{k:reflection['output_json'][k] for k in ('summary','issues','limitations')},'ruleCatalog':RULES,
                    'references':reflection['output_json']['references']},'caseHash':trial['case_hash']}
                profile=reflection_profile(c,source,cycle['policy_json'],agent_key=DESIGNER,
                    name='报告质量候选设计 Agent',prompt=DESIGN_PROMPT,schema=DESIGN_SCHEMA,engine_mode='quality-trial-v1')
                _submit(controller,c,cycle,'designer',profile,data)
                _status(controller,c,cycle,'designing');return True
            if cycle['status']=='designing':
                designer=roles['designer']
                if designer['status'] not in {'completed','failed','blocked','cancelled'}:continue
                if designer['status']!='completed':raise ValueError('designer did not complete')
                proposal=_proposal(controller,c,cycle,trial,designer)
                if not proposal:
                    _status(controller,c,cycle,'not-improved','没有新的、受支持的指令差异');return True
                for role,doc in [('baseline',trial['base_document_json']),('candidate',proposal['document'])]:
                    profile=reflection_profile(c,source,cycle['policy_json'],agent_key=QUALITY_AGENT,
                        name='报告质量'+('基线' if role=='baseline' else '候选')+'验证 Agent',
                        prompt=QUALITY_PROMPT,schema=QUALITY_SCHEMA,engine_mode='quality-trial-v1')
                    data={'question':'检查固定报告字段的质量契约','modelInput':trial['case_json']['modelInput'],
                        'caseHash':trial['case_hash'],'reportInstructions':doc['definition']['system_prompt'],
                        'targetProfileHash':_hash(doc),'targetVersion':doc['definition']['current_version']}
                    rid=_submit(controller,c,cycle,role,profile,data)
                    if role=='candidate':
                        c.execute('INSERT INTO agent_improvement_candidate_run(run_id,proposal_id,candidate_hash) VALUES(%s,%s,%s)',
                            (rid,proposal['proposalId'],proposal['candidateHash']))
                _status(controller,c,cycle,'trial-running');return True
            if cycle['status']=='trial-running':
                pair=[roles[key] for key in ['baseline','candidate']]
                if not all(r['status'] in {'completed','failed','blocked','cancelled'} for r in pair):continue
                if not all(r['status']=='completed' for r in pair):raise ValueError('paired run did not complete')
                _status(controller,c,cycle,'evaluating');return True
            if cycle['status']=='evaluating':_evaluate(controller,c,cycle,trial,roles);return True
        except Exception as exc:
            c.execute('ROLLBACK TO SAVEPOINT evolution_trial_stage')
            _status(controller,c,cycle,'stopped' if isinstance(exc,PermissionError) else 'failed',
                'trial-'+type(exc).__name__)
            return True
        finally:
            c.execute('RELEASE SAVEPOINT evolution_trial_stage')
    return False
