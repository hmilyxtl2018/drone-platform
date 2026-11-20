"""Versioned method experiments over saved inputs, using normal Run/model ledgers.

Experiments cannot change contracts, validators or permissions. A successful
provider receipt is not an expert quality label or permission to release.
"""
from copy import deepcopy
from dataclasses import replace
import asyncio
import json
import time
from psycopg2.extras import Json
from jsonschema import validate, ValidationError
from .skills import digest, validate_package, MANIFEST_SCHEMA, current_manifest
from .skill_views import invocation_detail
from .repository import new_id
from .domain import RunContext

CATEGORIES = {'sufficient_data','missing_data','conflicting_data','out_of_scope'}


def validate_candidate(base, candidate):
    validate_package(candidate)
    before, after = deepcopy(base), deepcopy(candidate)
    for doc in (before, after):
        doc.pop('instructions'); doc['manifest'].pop('version')
    if before != after or base['manifest']['version'] == candidate['manifest']['version']:
        raise ValueError('Skill candidate may change only instructions and version')
    if base['instructions'] == candidate['instructions']:
        raise ValueError('Skill candidate has no method change')


def _artifact(c, rid, kind, value, aid=None):
    from .evidence_repository import artifact_json_hash
    aid = aid or new_id('artifact')
    c.execute('''INSERT INTO agent_run_artifact(artifact_id,run_id,artifact_type,content_json,content_hash)
        VALUES(%s,%s,%s,%s,%s)''',(aid,rid,kind,Json(value),artifact_json_hash(value)))
    return aid


def _profile(c, source, binding, package, limits, trial_id, role):
    from .execution_baseline import build_baseline, register_baseline, profile_document
    version = 'skill-trial-'+trial_id.removeprefix('artifact-')+'-'+role
    b={**{k:binding[k] for k in ('stepKey','stage','skillKey')},'package':package,'version':package['manifest']['version'],'contentHash':digest(package)}
    manifest={'schemaVersion':MANIFEST_SCHEMA,'agentKey':source.agent_key,'profileVersion':version,'bindings':[b]}
    manifest['contentHash']=digest(manifest)
    model=deepcopy(source.model_policy)
    model['max_output_tokens']=limits['output_tokens']
    model['config_json']={'engineMode':'skill-trial-v1','retryLimit':0,'budgetLimits':limits}
    step=next(s for s in source.steps if s.key==b['stepKey'])
    step=replace(step,tools=(),dependencies=(),retry_limit=0,input_contract=package['inputContract'],output_contract=package['outputContract'])
    contract={'role':'skill-trial-v1','allowedTools':[],'allowedOperations':['read-evidence','analyze'],
              'skillRef':{k:b[k] for k in ('skillKey','version','contentHash')},'trialId':trial_id,'limits':limits}
    ch=digest(contract);hid=new_id('harness')
    c.execute('''INSERT INTO agent_harness_contract(harness_contract_id,agent_key,profile_version,contract_version,
        agent_layer,runtime_kind,invocation_mode,default_deny,max_child_depth,contract_json,contract_hash,status)
        VALUES(%s,%s,%s,%s,'action','llm_loop','scheduled',true,0,%s,%s,'testing')''',
        (hid,source.agent_key,version,ch,Json(contract),ch))
    c.execute('''INSERT INTO agent_step_skill_binding(agent_key,profile_version,step_key,stage,skill_key,
        skill_version,content_hash,created_by) VALUES(%s,%s,%s,%s,%s,%s,%s,'skill-trial-service')''',
        (source.agent_key,version,b['stepKey'],b['stage'],b['skillKey'],b['version'],b['contentHash']))
    profile=replace(source,version=version,steps=(step,),tools={},loop_rules=(),approval_policy={},reasoning_policy={},
        model_policy=model,skill_manifest=manifest,capability_graph={},
        convergence={'max_model_calls':1,'max_tool_calls':0,'max_runtime_seconds':300,'max_iterations':1},
        resource_policy={'budgetLimits':limits},output_contract={'schema_json':package['outputContract']},
        harness_contract={'harnessContractId':hid,'contractHash':ch,'contractVersion':ch,'contract':contract,
            'toolGrants':[],'dataGrants':[],'networkGrants':[],'compositionEdges':[],'stepBudgets':[]},
        execution_baseline={},agent_layer='action')
    baseline=register_baseline(c,source.agent_key,build_baseline(source.agent_key,profile_document(profile),
        {'skillBindings':[{k:v for k,v in b.items() if k!='package'}]}),status='testing',created_by='skill-trial-service')
    return replace(profile,execution_baseline=baseline)


class SkillTrialService:
    def __init__(self, engine, queue):
        self.engine,self.queue=engine,queue
        self.factory=engine.repo.connection_factory

    def list(self, source_run_id):
        with self.factory() as con:
            with con.cursor() as c:
                c.execute("SELECT artifact_id,content_json,created_at FROM agent_run_artifact WHERE run_id=%s AND artifact_type='skill-version-trial' ORDER BY created_at",(source_run_id,))
                trials=[dict(r) for r in c.fetchall()]
        for t in trials:
            t['runs']=[{'runId':rid,'status':self.engine.repo.get_run(rid)['status']} for p in t['content_json']['pairs'] for rid in (p['baselineRunId'],p['candidateRunId'])]
            with self.factory() as con:
                with con.cursor() as c:
                    c.execute("SELECT content_json FROM agent_run_artifact WHERE run_id=%s AND artifact_type='skill-trial-assessment' AND content_json->>'trialId'=%s ORDER BY created_at DESC LIMIT 1",(source_run_id,t['artifact_id']))
                    row=c.fetchone();t['assessment']=row['content_json'] if row else None
        return {'runId':source_run_id,'trials':trials,'automaticPublication':False}

    def create(self, source_run_id, body, actor):
        if actor.kind!='human' or not actor.permits('operator'):raise PermissionError('human operator required')
        if set(body)-{'invocationId','samples','candidateVersion','instructions','hypothesis','idempotencyKey'}:
            raise ValueError('unknown Skill trial field')
        hypothesis=body.get('hypothesis');op=body.get('idempotencyKey')
        if not isinstance(hypothesis,str) or not 1<=len(hypothesis)<=2000 or not isinstance(op,str) or not 1<=len(op)<=160:
            raise ValueError('bounded hypothesis and idempotencyKey required')
        samples=body.get('samples') or [{'runId':source_run_id,'invocationId':body.get('invocationId')}]
        if not isinstance(samples,list) or not 1<=len(samples)<=8:raise ValueError('one to eight saved samples required')
        # Each additional sample must already belong to the same Agent identity;
        # this service is admin-only; DSH mutation is intentionally not exposed.
        saved=[]
        for sample in samples:
            d=invocation_detail(self.engine,sample['runId'],sample['invocationId'])
            if not d['validation']['receiptVerified']:raise ValueError('source Skill receipt must be verified')
            package=d['binding']['package'];inv=d['invocation']
            material=[]
            for call in d['calls']:
                for m in ((call.get('input') or {}).get('request') or {}).get('messages',[]):
                    if m.get('role')!='user':continue
                    try:v=json.loads(m['content'])
                    except (ValueError,TypeError):continue
                    if digest(v)==inv['input_hash']:material.append(v)
            if not material or any(v!=material[0] for v in material):raise ValueError('saved input hash mismatch')
            validate(material[0],package['inputContract'])
            source=self.engine.loader.load_for_run(sample['runId'])
            saved.append((d,source,material[0]))
        first,source,_=saved[0];base=first['binding']['package']
        if base['manifest']['skillKey']!='citation-support-review':
            raise ValueError('this evaluation adapter supports only citation-support-review')
        if len({digest(m) for _,_,m in saved})!=len(saved):
            raise ValueError('duplicate saved materials do not form independent cases')
        if samples[0]['runId']!=source_run_id:raise ValueError('trial must be owned by first source Run')
        candidate=deepcopy(base);candidate['manifest']['version']=body.get('candidateVersion');candidate['instructions']=body.get('instructions')
        validate_candidate(base,candidate)
        if any(d['binding']['contentHash']!=digest(base) or p.agent_key!=source.agent_key or p.model_policy!=source.model_policy for d,p,_ in saved):
            raise ValueError('samples must use the same frozen Skill and model policy')
        limits={k:0 for k in ('model_calls','http_calls','tool_calls','search_calls','scrape_calls','input_tokens','output_tokens')}
        cap=source.resource_policy.get('budgetLimits',{})
        limits.update(model_calls=1,http_calls=1,input_tokens=min(60000,cap.get('input_tokens',60000)),
                      output_tokens=min(source.model_policy.get('max_output_tokens',4096),cap.get('output_tokens',4096)))
        for k in ('model_calls','http_calls'):
            if cap.get(k,0)<1:raise ValueError('source has no model budget')
        spec={'hypothesis':hypothesis,'samples':samples,'candidate':candidate,'limits':limits}
        with self.factory() as con:
            with con.cursor() as c:
                c.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('skill-trial:'+source_run_id+':'+op,))
                c.execute("SELECT artifact_id,content_json FROM agent_run_artifact WHERE run_id=%s AND artifact_type='skill-version-trial' AND content_json->>'operationKey'=%s",(source_run_id,op))
                old=c.fetchone()
                if old:
                    if old['content_json']['specHash']!=digest(spec):raise ValueError('idempotency key conflicts with another trial')
                    return {'trialId':old['artifact_id'],**old['content_json']}
                info=candidate['manifest'];ch=digest(candidate)
                c.execute('''INSERT INTO agent_skill_revision(skill_key,version,content_hash,package_json,created_by)
                    VALUES(%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING''',(info['skillKey'],info['version'],ch,Json(candidate),actor.principal_id))
                c.execute('SELECT content_hash FROM agent_skill_revision WHERE skill_key=%s AND version=%s',(info['skillKey'],info['version']))
                if c.fetchone()['content_hash']!=ch:raise ValueError('registered candidate version is immutable; choose a new version')
                tid=new_id('artifact');pairs=[]
                for index,(d,p,material) in enumerate(saved):
                    pair={'sourceRunId':samples[index]['runId'],'sourceInvocationId':samples[index]['invocationId'],
                          'caseHash':digest(material),'input':material,'runs':{}}
                    for role,pkg in [('baseline',base),('candidate',candidate)]:
                        profile=_profile(c,p,d['binding'],pkg,limits,tid,role+str(index))
                        data={'trialId':tid,'sourceRunId':source_run_id,'role':role,'caseIndex':index,
                            'caseHash':pair['caseHash'],'modelInput':material,
                            'taskIntent':{'allowedOperations':['read-evidence','analyze'],'allowedTools':[],'budgets':limits}}
                        from .task_intent import admission_actor
                        with admission_actor(actor):rid=self.engine.repo.create_run(profile,data,_cursor=c)
                        c.execute('INSERT INTO agent_run_queue(run_id,pool_key,priority) VALUES(%s,%s,0)',(rid,self.queue.pool_key))
                        pair[role+'RunId']=rid
                    pair.pop('runs');pairs.append(pair)
                result={'operationKey':op,'specHash':digest(spec),'hypothesis':hypothesis,'pairs':pairs,
                    'baseline':{k:first['binding'][k] for k in ('skillKey','version','contentHash')},
                    'candidate':{'skillKey':info['skillKey'],'version':info['version'],'contentHash':ch},
                    'limitsPerRun':limits,'createdBy':actor.principal_id,'automaticPublication':False}
                _artifact(c,source_run_id,'skill-version-trial',result,tid)
        return {'trialId':tid,**result}

    def evaluate(self, source_run_id, trial_id):
        trial=self.engine.repo.get_artifact(source_run_id,trial_id)
        if not trial or trial['artifact_type']!='skill-version-trial':raise LookupError('trial not found in this Run')
        value=trial['content_json'];results=[]
        for pair in value['pairs']:
            outputs={};calls={}
            for role in ('baseline','candidate'):
                rid=pair[role+'RunId'];run=self.engine.repo.get_run(rid)
                if run['status'] not in {'completed','failed','blocked','cancelled'}:raise ValueError('both Runs must finish before evaluation')
                inv=self.engine.repo.list_skill_invocations(rid)
                outputs[role]=run.get('output_json') or {}
                calls[role]=self.engine.repo.list_model_calls(rid)
                if inv and any(i['input_hash']!=pair['caseHash'] for i in inv):raise ValueError('paired Skill input proof mismatch')
            model=lambda role:[(c['provider_key'],c['requested_model'],c.get('actual_model')) for c in calls[role]]
            profiles=[self.engine.loader.load_for_run(pair[r+'RunId']) for r in ('baseline','candidate')]
            same=profiles[0].model_policy==profiles[1].model_policy and profiles[0].resource_policy==profiles[1].resource_policy and model('baseline')==model('candidate')
            metrics={r:measure(pair['input'],outputs[r],profiles[n].skill_manifest['bindings'][0]['package']['outputContract']) for n,r in enumerate(('baseline','candidate'))}
            receipts={}
            for role in ('baseline','candidate'):
                invs=self.engine.repo.list_skill_invocations(pair[role+'RunId'])
                receipts[role]=len(invs)==1 and len(calls[role])==1 and all(
                    invocation_detail(self.engine,pair[role+'RunId'],i['invocation_id'])['validation']['receiptVerified']
                    and i['content_hash']==value[role]['contentHash'] for i in invs)
            label=self._label(pair['sourceRunId'],pair['caseHash'])
            for role in metrics:
                metrics[role]['expertErrors']=label_errors(outputs[role],label['expected'],label.get('expectedRelations'),label.get('expectedScenarios')) if label else None
            usage={r:{'modelCalls':len(calls[r]),'inputTokens':known_sum(calls[r],'input_tokens'),
                      'outputTokens':known_sum(calls[r],'output_tokens'),'latencyMs':known_sum(calls[r],'latency_ms'),'cost':None} for r in calls}
            results.append({'caseHash':pair['caseHash'],'materialHash':evidence_material_hash(pair['input']),'sourceRunId':pair['sourceRunId'],'sameModelAndBudget':same,
                'metrics':metrics,'usage':usage,'label':label,'receiptsVerified':receipts,
                'outputs':outputs,
                'baselineRunId':pair['baselineRunId'],'candidateRunId':pair['candidateRunId']})
        categories,independent,valid,complete,eligible=release_gate(results)
        result={'trialId':trial_id,'results':results,'outcome':'eligible-for-human-review' if eligible else 'insufficient-validation' if not complete else 'not-improved',
            'publicationEligible':eligible,'coverage':sorted(categories),'missingCoverage':sorted(CATEGORIES-categories),
            'independentSamples':independent,'pairedInvariantsValid':valid,'truthVerified':False,
            'limitations':['同一真实材料的重复调用只验证方法差异，不能代替独立专家标签。','Token 与耗时来自模型账本；金额未配置，保持未知。']}
        result['assessmentHash']=digest(result)
        # Serialize repeats without holding locks on evaluation rows. Interrupted
        # evaluation resumes its saved records; unchanged results never create new ones.
        with self.factory() as con:
            with con.cursor() as c:
                c.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('skill-assessment:'+trial_id,))
                c.execute("SELECT content_json FROM agent_run_artifact WHERE run_id=%s AND artifact_type='skill-trial-assessment' AND content_json->>'assessmentHash'=%s",(source_run_id,result['assessmentHash']))
                old=c.fetchone()
                if old:return old['content_json']
                result['evaluationRefs']=self._evaluation_records(value,results,trial_id,result['assessmentHash'])
                _artifact(c,source_run_id,'skill-trial-assessment',result)
        return result

    def _label(self, rid, case_hash):
        with self.factory() as con:
            with con.cursor() as c:
                c.execute("SELECT artifact_id,content_json FROM agent_run_artifact WHERE run_id=%s AND artifact_type='skill-expert-label' AND content_json->>'caseHash'=%s ORDER BY created_at DESC LIMIT 1",(rid,case_hash))
                row=c.fetchone();return {**row['content_json'],'artifactId':row['artifact_id']} if row else None

    def label(self, rid, invocation_id, body, actor):
        if actor.kind!='human' or not actor.permits('reviewer'):raise PermissionError('human reviewer required')
        d=invocation_detail(self.engine,rid,invocation_id)
        if not d['validation']['receiptVerified'] or d['binding']['skillKey']!='citation-support-review':
            raise ValueError('expert labels require a verified citation review receipt')
        if set(body)-{'caseType','expected','expectedRelations','expectedScenarios','rationale'}:raise ValueError('unknown expert label field')
        if body.get('caseType') not in CATEGORIES or not isinstance(body.get('expected'),dict) or not body['expected'] or not body.get('rationale'):
            raise ValueError('case category, expected verdicts and expert rationale required')
        if any(v not in {'supported','unsupported'} for v in body['expected'].values()):raise ValueError('invalid expert verdict')
        materials=[json.loads(m['content']) for call in d['calls'] for m in ((call.get('input') or {}).get('request') or {}).get('messages',[]) if m.get('role')=='user']
        material=next((m for m in materials if digest(m)==d['invocation']['input_hash']),None)
        if not material or set(body['expected'])!={p['proposal']['perspectiveKey'] for p in material['proposals']}:
            raise ValueError('expert label must cover exactly the saved perspectives')
        for field,key in (('relations','expectedRelations'),('scenarioAssessments','expectedScenarios')):
            wanted={p['proposal']['perspectiveKey']:{r['id'] for r in p['proposal'].get(field,[])} for p in material['proposals'] if p['proposal'].get(field)}
            labelled=body.get(key,{})
            if not isinstance(labelled,dict) or set(labelled)!=set(wanted) or any(
                not isinstance(labelled[p],dict) or set(labelled[p])!=ids or any(v not in {'supported','unsupported'} for v in labelled[p].values()) for p,ids in wanted.items()):
                raise ValueError('expert label must cover every saved relation and scenario assessment')
        if not isinstance(body['rationale'],str) or not 1<=len(body['rationale'].strip())<=4000:
            raise ValueError('bounded expert rationale required')
        value={**body,'caseHash':d['invocation']['input_hash'],'invocationId':invocation_id,'reviewer':actor.principal_id,'origin':'human-review'}
        from agent_admin.config_publication import ConfigPublicationService
        with self.factory() as con:
            with con.cursor() as c:
                ConfigPublicationService._config_lock(c)
                aid=_artifact(c,rid,'skill-expert-label',value)
        return {'artifactId':aid,**value}

    def _evaluation_records(self, trial, results, tid, assessment_hash):
        from .evaluation import EvaluationService
        service=EvaluationService(self.factory)
        dataset=service.register_dataset('skill-trial-'+tid,assessment_hash,[
            {'caseKey':r['caseHash'],'caseVersion':'1','caseType':r['label']['caseType'] if r['label'] else 'unlabelled',
             'input':{'material':p['input'],'caseHash':r['caseHash'],'labelStatus':'expert-reviewed' if r['label'] else 'unlabelled'},
             'expected':{'fieldEquals':{'contractValid':True,'unsupportedEvidenceIds':0,'referenceErrors':0},'expertLabel':r['label']}}
            for p,r in zip(trial['pairs'],results)],metadata={'scope':'structural method comparison; no factual correctness claim','assessmentHash':assessment_hash})
        refs=[]
        for role in ('baseline','candidate'):
            with self.factory() as con:
                with con.cursor() as c:
                    c.execute("SELECT evaluation_run_id FROM agent_evaluation_run WHERE source_versions_json->>'assessmentHash'=%s AND source_versions_json->>'role'=%s",(assessment_hash,role))
                    old=c.fetchone()
            ev=service.run(old['evaluation_run_id']) if old else service.create_run(dataset['dataset_id'],profile_agent_key=trial[role]['skillKey'],profile_version=trial[role]['version'],
                profile_hash=trial[role]['contentHash'],prompt_version=trial[role]['version'],prompt_hash=trial[role]['contentHash'],execution_mode='paid_model',
                source_versions={'skill':trial[role],'trialId':tid,'assessmentHash':assessment_hash,'role':role})
            for r in results:
                case=next(c for c in dataset['cases'] if c['case_key']==r['caseHash'])
                persisted=next((old for old in ev['results'] if old['case_id']==case['case_id']),None)
                if persisted:
                    service.collect_usage(ev['evaluation_run_id'],r[role+'RunId'],result_id=persisted['result_id'])
                    continue
                service.record_case_result(ev['evaluation_run_id'],case['case_id'],r['metrics'][role],
                    actual_references={'trialId':tid,'runId':r[role+'RunId'],'expertLabelled':bool(r['label'])},usage_run_id=r[role+'RunId'])
            refs.append(ev['evaluation_run_id'])
        return refs


def known_sum(calls, field):
    return sum(c[field] for c in calls) if calls and all(c.get(field) is not None for c in calls) else None


def label_errors(output, expected, relations=None, scenarios=None):
    rows=output.get('reviews',[]) if isinstance(output,dict) else []
    actual={r.get('perspectiveKey'):r.get('verdict') for r in rows if isinstance(r,dict) and isinstance(r.get('perspectiveKey'),str)} if isinstance(rows,list) else {}
    errors=sum(actual.get(k)!=v for k,v in expected.items())
    by_key={r['perspectiveKey']:r for r in rows if isinstance(r,dict) and isinstance(r.get('perspectiveKey'),str)} if isinstance(rows,list) else {}
    for field,labels in (('relations',relations or {}),('scenarioAssessments',scenarios or {})):
        for key,wanted in labels.items():
            values=by_key.get(key,{}).get(field,[])
            found={r.get('id'):r.get('verdict') for r in values if isinstance(r,dict) and isinstance(r.get('id'),str)} if isinstance(values,list) else {}
            errors+=sum(found.get(k)!=v for k,v in wanted.items())
    return errors


def evidence_material_hash(material):
    # Rewording the same proposals or changing evidence IDs cannot manufacture
    # independent validation materials.
    items={json.dumps({k:e.get(k) for k in ('sourceUri','quote')},sort_keys=True,ensure_ascii=False)
           for p in material.get('proposals',[]) for e in p.get('evidence',[])}
    return digest(sorted(items))


def release_gate(results):
    categories={r['label']['caseType'] for r in results if r['label']}
    independent=len({r['sourceRunId'] for r in results})>=4 and len({r['caseHash'] for r in results})>=4 and len({r.get('materialHash') for r in results if r.get('materialHash')})>=4
    valid=bool(results) and all(r['sameModelAndBudget'] and all(r['receiptsVerified'][k] and r['metrics'][k]['contractValid'] and r['metrics'][k]['referenceErrors']==0 and r['metrics'][k]['unsupportedEvidenceIds']==0 for k in ('baseline','candidate')) for r in results)
    complete=categories==CATEGORIES and independent and all(r['label'] for r in results)
    eligible=valid and complete and all(r['metrics']['candidate']['expertErrors']<=r['metrics']['baseline']['expertErrors'] for r in results) and sum(r['metrics']['candidate']['expertErrors'] for r in results)<sum(r['metrics']['baseline']['expertErrors'] for r in results)
    return categories,independent,valid,complete,eligible


def measure(material, output, schema=None):
    if not isinstance(output,dict):output={}
    reviews=output.get('reviews')
    contract_valid=isinstance(reviews,list)
    if schema:
        try:validate(output,schema)
        except ValidationError:contract_valid=False
    reviews=[r for r in reviews or [] if isinstance(r,dict)] if isinstance(reviews,list) else []
    proposals={p.get('proposal',{}).get('perspectiveKey'):p.get('proposal',{}) for p in material.get('proposals',[])}
    keys=[r.get('perspectiveKey') if isinstance(r.get('perspectiveKey'),str) else None for r in reviews]
    reference_errors=len(set(keys)^set(proposals))+len(keys)-len(set(keys))
    for key,r in zip(keys,reviews):
        p=proposals.get(key,{})
        for field in ('relations','scenarioAssessments'):
            expected={v.get('id') for v in p.get(field,[])}
            values=r.get(field,[])
            actual=[v.get('id') if isinstance(v.get('id'),str) else None for v in values if isinstance(v,dict)] if isinstance(values,list) else []
            reference_errors+=len(set(actual)^expected)+len(actual)-len(set(actual))
    known={e['evidenceId'] for p in material.get('proposals',[]) for e in p.get('evidence',[]) if e.get('evidenceId')}
    def foreign(value):
        if isinstance(value,dict):return sum(sum(not isinstance(i,str) or i not in known for i in v) if k=='evidenceIds' and isinstance(v,list) else foreign(v) for k,v in value.items())
        if isinstance(value,list):return sum(foreign(v) for v in value)
        return 0
    return {'contractValid':contract_valid,'referenceErrors':reference_errors,'supported':sum(r.get('verdict')=='supported' for r in reviews),
        'abstentions':sum(r.get('verdict')=='unsupported' for r in reviews or []),
        'unsupportedEvidenceIds':foreign(output),'expertErrors':None,
        'citationMismatch':'unverified-without-original-quote-comparison'}


async def execute_trial(engine, rid):
    repo=engine.repo;run=repo.get_run(rid)
    if run['status'] in {'completed','failed','blocked','cancelled'}:return run
    profile=engine.loader.load_for_run(rid);data=run['input_json']
    trial=repo.get_artifact(data.get('sourceRunId'),data.get('trialId'))
    try:
        if not trial or trial['artifact_type']!='skill-version-trial':raise PermissionError('trial admission binding absent')
        pair=trial['content_json']['pairs'][data['caseIndex']]
        if pair[data['role']+'RunId']!=rid or pair['input']!=data['modelInput'] or pair['caseHash']!=digest(data['modelInput']):
            raise PermissionError('trial input binding mismatch')
        b=profile.skill_manifest['bindings'][0]
        expected=trial['content_json'][data['role']]
        if any(b[k]!=expected[k] for k in ('skillKey','version','contentHash')):raise PermissionError('trial target binding mismatch')
        if run['status']=='created':repo.transition(rid,'queued')
        repo.transition(rid,'planning');repo.transition(rid,'running')
        repo.initialize_budget(rid,profile.resource_policy['budgetLimits'])
        ctx=RunContext(rid,profile,data,started_at=time.monotonic())
        steps=repo.list_steps(rid)
        active=next((s for s in steps if s['status']=='running'),None)
        attempt=active['attempt'] if active else repo.next_step_attempt(rid,b['stepKey'])
        sid=active['step_run_id'] if active else repo.step(rid,b['stepKey'],'running',input_data=data['modelInput'],attempt=attempt)
        ctx.current_step_key=b['stepKey'];ctx.current_step_run_id=sid
        from .configuration import model_gateway
        output=await asyncio.wait_for(model_gateway(repo,profile).invoke(ctx,
            [{'role':'user','content':json.dumps(data['modelInput'],ensure_ascii=False)}],b['package']['outputContract'],
            stage=b['stage'],step_key=b['stepKey'],step_run_id=sid,operation_key='skill-trial:'+str(data['caseIndex'])),timeout=300)
        repo.step(rid,b['stepKey'],'succeeded',output=output,attempt=attempt)
        repo.transition(rid,'completed',output=output)
    except asyncio.CancelledError:raise
    except Exception as exc:
        code=getattr(exc,'code',None) or type(exc).__name__
        if 'sid' in locals():repo.step(rid,b['stepKey'],'failed',error='skill-trial-'+code,attempt=attempt)
        if repo.get_run(rid)['status'] not in {'cancelled','failed'}:repo.transition(rid,'failed',error='skill-trial-'+code)
    return repo.get_run(rid)

