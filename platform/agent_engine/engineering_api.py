"""Authenticated manual calculations in independent, parent-linked Runs.

All execution passes through the frozen ToolGateway. Original Runs, budgets and
reviews are read only. Copied candidates start pending; a frozen parent review
snapshot is traceability, never authority to approve a new candidate.
"""
from agent_engine.application_identity import FUTURE_AGENT_KEYS
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
import re

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from psycopg2.extras import Json

from agent_admin.credentials import AdminAuth, Principal, token_digest
from agent_admin.repository import reject_secrets
from agent_tools.engineering_runtime import EngineeringRuntime, TOOL_INPUT_SCHEMAS
from .domain import RunContext
from .evidence_repository import artifact_json_hash, artifact_source, constrained_candidate
from .repository import PostgresRepository, new_id
from .review import ReviewService, classify_reviews
from .engineering_lifecycle import STAGES


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,allow_nan=False,separators=(',',':')).encode()).hexdigest()


class EngineeringConflictError(ValueError):
    pass


class ManualEngineeringService:
    def __init__(self,engine):
        self.engine=engine; self.repo=engine.repo

    @staticmethod
    def authorize(cursor,actor,token,write=False):
        if not isinstance(actor,Principal) or actor.kind!='human' or not token_digest(token):
            raise PermissionError('current human identity required')
        cursor.execute('SELECT i.principal_id,i.kind,i.roles_json FROM agent_admin_token t JOIN agent_admin_identity i USING(principal_id) '
                       'WHERE t.token_hash=%s AND t.revoked_at IS NULL AND t.expires_at>now() AND i.enabled=true FOR SHARE OF i,t',
                       (token_digest(token),))
        row=cursor.fetchone()
        if not row or row['principal_id']!=actor.principal_id or row['kind']!='human' or set(row['roles_json'])!=set(actor.roles):
            raise PermissionError('current human identity required')
        if write and not {'operator','admin'}.intersection(row['roles_json']):
            raise PermissionError('operator role required')

    def _parent(self,cursor,parent_id):
        cursor.execute('SELECT * FROM agent_run WHERE run_id=%s FOR SHARE',(parent_id,)); parent=cursor.fetchone()
        if not parent: raise LookupError('parent Run not found')
        if parent['input_json'].get('executionMode')=='engineering-manual-v1':
            raise ValueError('select the original research Run for another calculation')
        snapshot=ReviewService._snapshot(cursor,parent_id)
        cursor.execute('SELECT * FROM agent_human_review WHERE run_id=%s ORDER BY review_seq',(parent_id,))
        reviews=json.loads(json.dumps(classify_reviews([dict(row) for row in cursor.fetchall()],**snapshot),default=str))
        workspace=EngineeringRuntime(self.repo)._workspace(
            RunContext(parent_id,self.repo.load_profile_snapshot(parent_id),parent['input_json']),parent['input_json'])
        return dict(parent),snapshot,reviews,workspace

    def describe(self,parent_id,actor,token):
        with self.repo.connection_factory() as con:
            with con.cursor() as cursor:
                self.authorize(cursor,actor,token)
                parent,snapshot,reviews,workspace=self._parent(cursor,parent_id)
                cursor.execute('SELECT run_id,tool_key,created_at,frozen_input_hash FROM agent_engineering_run WHERE parent_run_id=%s ORDER BY created_at DESC LIMIT 100',(parent_id,))
                calculations=[dict(row) for row in cursor.fetchall()]
        profile=self.engine.loader.load(parent['agent_key'])
        candidates=self.repo.list_candidates(parent_id)
        return {'parentRunId':parent_id,'entityId':parent['input_json'].get('entityId') or parent['input_json'].get('uavId'),
                'requirements':deepcopy((workspace['content'] if workspace else parent['input_json']).get('requirements',{})),
                'workspace':{key:value for key,value in workspace.items() if key!='content'} if workspace else None,
                'candidates':candidates,'reviews':reviews,'parentSnapshot':snapshot,'calculations':calculations,
                'availableTools':[key for key in TOOL_INPUT_SCHEMAS if key in profile.tools and profile.tools[key].enabled],
                'truthVerified':False,'reviewedCompliance':'unknown'}

    def result(self,run_id,actor,token):
        with self.repo.connection_factory() as con:
            with con.cursor() as cursor:
                self.authorize(cursor,actor,token)
                cursor.execute('SELECT * FROM agent_engineering_run WHERE run_id=%s',(run_id,)); link=cursor.fetchone()
                if not link: raise LookupError('calculation Run not found')
        run=self.repo.get_run(run_id)
        return {'runId':run_id,'parentRunId':link['parent_run_id'],'status':run['status'],
                'toolKey':link['tool_key'],'frozenInputHash':link['frozen_input_hash'],
                'frozenInput':link['frozen_input_json'],'projectedPayload':link['projected_payload_json'],
                'candidateProjection':link['candidate_projection_json'],'projectionHash':link['projection_hash'],'budget':self.repo.get_budget(run_id),
                'toolCalls':self.repo.list_tool_calls(run_id),
                'artifacts':[item for item in self.repo.list_artifacts(run_id) if item['artifact_type']=='engineering-calculation'],
                'output':run.get('output_json'),'truthVerified':False,'reviewedCompliance':'unknown'}

    def create(self,parent_id,actor,token,body):
        if not isinstance(body,dict) or set(body)-{'toolKey','payload','idempotencyKey','requirements'} or not {'toolKey','payload','idempotencyKey'}<=set(body):
            raise ValueError('invalid manual calculation fields')
        key=body['toolKey']; operation=body['idempotencyKey']; payload=body['payload']
        if key not in TOOL_INPUT_SCHEMAS or not isinstance(operation,str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,160}',operation):
            raise ValueError('invalid engineering tool or operation key')
        encoded=json.dumps(body,ensure_ascii=False,allow_nan=False)
        if len(encoded.encode())>500_000: raise ValueError('calculation input exceeds limit')
        reject_secrets(body)
        request_hash=digest(body)
        with self.repo.connection_factory() as con:
            with con.cursor() as cursor:
                self.authorize(cursor,actor,token,True)
                cursor.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('engineering:'+parent_id+':'+operation,))
                cursor.execute('SELECT * FROM agent_engineering_run WHERE parent_run_id=%s AND idempotency_key=%s',(parent_id,operation))
                existing=cursor.fetchone()
                if existing:
                    if existing['request_hash']!=request_hash or existing['principal_id']!=actor.principal_id:
                        raise EngineeringConflictError('operation key belongs to another calculation')
                    return existing['run_id']
                parent,snapshot,reviews,workspace=self._parent(cursor,parent_id)
                # Future Battlefield research is admitted to deterministic
                # engineering tools only after the explicit lifecycle gate.
                # Legacy UAV roles retain their existing contract while they
                # migrate to the shared lifecycle service.
                if parent['agent_key'] in FUTURE_AGENT_KEYS:
                    cursor.execute("SELECT stage FROM future_battlefield_lifecycle WHERE run_id=%s ORDER BY revision DESC LIMIT 1", (parent_id,))
                    lifecycle = cursor.fetchone()
                    if not lifecycle or STAGES.index(lifecycle['stage']) < STAGES.index('engineering-input'):
                        raise PermissionError('Future Battlefield Run must pass decision-support and engineering-input gates before engineering calculations')
                # A current Profile can narrow permissions, never borrow permissions
                # from an old parent snapshot or a caller-provided binding.
                profile=self.engine.loader.load(parent['agent_key'])
                binding=profile.tools.get(key)
                if not binding or not binding.enabled: raise PermissionError('engineering tool is not enabled in the current Profile')
                if binding.version!='1.0.0' or binding.protocol!='python' or binding.access_mode!='read_only' or binding.approval_required or binding.risk_level!='low':
                    raise PermissionError('manual calculations require a low-risk, read-only frozen formula binding')
                configured=profile.resource_policy.get('budgetLimits',{})
                if configured.get('tool_calls',0)<1 or int(profile.convergence.get('max_tool_calls',0))<1:
                    raise PermissionError('configured budget does not permit a calculation')
                requirements=deepcopy((workspace['content'] if workspace else parent['input_json']).get('requirements',{}))
                if 'requirements' in body:
                    if workspace: raise ValueError('workspace requirements must be revised through the workspace')
                    if not isinstance(body['requirements'],dict) or len(body['requirements'])>100:
                        raise ValueError('requirements must be a bounded object')
                    requirements=deepcopy(body['requirements'])
                frozen={'parentRunId':parent_id,'parentSnapshot':snapshot,'parentReviews':reviews,
                        'parentWorkspace':{key:value for key,value in workspace.items() if key!='content'} if workspace else None,
                        'requirements':requirements,'requestedPayload':deepcopy(payload),'toolKey':key,
                        'requirementAuthority':'saved-workspace' if workspace else 'explicit-manual-input' if 'requirements' in body else 'parent-run-input',
                        'truthVerified':False,'reviewedCompliance':'unknown'}
                frozen_hash=digest(frozen)
                entity=parent['input_json'].get('entityId') or parent['input_json'].get('uavId')
                if not isinstance(entity,str) or not entity: raise ValueError('parent Run has no entity')
                scope=deepcopy(profile.scope_policy)
                if scope.get('allowedEntities') and entity not in scope['allowedEntities']: raise PermissionError('entity outside current Profile scope')
                scope['allowedEntities']=[entity]
                if not scope.get('allowedMetrics'): scope['allowedMetrics']=deepcopy(profile.model_policy.get('config_json',{}).get('allowedMetrics',[
                    'enduranceHours','payloadKg','maxSpeedMps','mtowKg','cruiseSpeedMps','rangeKm','ceilingM']))
                model=deepcopy(profile.model_policy); model.setdefault('config_json',{})['engineMode']='research-v1'
                limits={name:0 for name in ('model_calls','input_tokens','output_tokens','tool_calls','http_calls','search_calls','scrape_calls')}; limits['tool_calls']=1
                resources=deepcopy(profile.resource_policy); resources['budgetLimits']=limits; resources['max_retries_per_tool']=0
                profile=replace(profile,scope_policy=scope,model_policy=model,resource_policy=resources,
                                tools={key:replace(binding,retry_limit=0)})
                data={'executionMode':'engineering-manual-v1','entityId':entity,'requirements':requirements,
                      'engineeringParentRunId':parent_id,'engineeringInputHash':frozen_hash}
                from .task_intent import admission_actor
                from .run_origin import origin_context
                with admission_actor(actor), origin_context(actor, source_kind='engineering', source_ref=parent_id):
                    run_id=PostgresRepository.create_run(self.repo,profile,data,_cursor=cursor)
                cursor.execute('INSERT INTO agent_run_budget(root_run_id,limits_json) VALUES(%s,%s)',(run_id,Json(limits)))
                cursor.execute('INSERT INTO agent_run_budget_member(run_id,root_run_id,parent_run_id) VALUES(%s,%s,NULL)',(run_id,run_id))
                copied=self._copy_evidence(cursor,parent_id,run_id,profile)
                mapped=deepcopy(payload)
                if not isinstance(mapped,dict): raise ValueError('tool payload must be an object')
                # A calculation owns its projection, so the original workspace
                # hash is provenance only and cannot masquerade as a new binding.
                if 'workspaceRevisionHash' in mapped: raise ValueError('workspace reference is frozen by the server')
                if mapped.get('candidateId'):
                    if mapped['candidateId'] not in copied: raise ValueError('candidate is absent from parent Run')
                    mapped['candidateId']=copied[mapped['candidateId']]['candidateId']
                for item in mapped.get('candidateBindings',[]):
                    if not isinstance(item,dict) or item.get('candidateId') not in copied: raise ValueError('candidate binding is absent from parent Run')
                    record=copied[item['candidateId']]; item['candidateId']=record['candidateId']
                    quantity=self._quantity_at(mapped.get('calculation',{}),item.get('path'))
                    if not isinstance(quantity,dict): raise ValueError('candidate binding requires an existing quantity')
                    # Numeric equality, metric, conditions and boundary are
                    # checked again inside EngineeringRuntime through Gateway.
                    quantity['basis']={'kind':'reported-specification','sourceId':record['sourceId'],
                        'sourceVersion':record['sourceVersion'],'contentHash':record['contentHash']}
                projection_hash=digest({'payload':mapped,'candidates':copied})
                cursor.execute('INSERT INTO agent_engineering_run(run_id,parent_run_id,principal_id,idempotency_key,request_hash,tool_key,frozen_input_json,frozen_input_hash,projected_payload_json,candidate_projection_json,projection_hash) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                    (run_id,parent_id,actor.principal_id,operation,request_hash,key,Json(frozen),frozen_hash,Json(mapped),Json(copied),projection_hash))
                self.repo.event(cursor,run_id,'engineering.manual-created',None,{'parentRunId':parent_id,'principalId':actor.principal_id,'inputHash':frozen_hash,'projectionHash':projection_hash,'truthVerified':False})
                return run_id

    @staticmethod
    def _quantity_at(calculation,path):
        if path in {'energy','averagePower','massLimit'}: return calculation.get(path)
        match=re.fullmatch(r'components\.(\d{1,3})\.mass',path or '')
        if not match or int(match[1])>=len(calculation.get('components',[])): return None
        return calculation['components'][int(match[1])].get('mass')

    def _copy_evidence(self,cursor,parent_id,run_id,profile):
        cursor.execute('SELECT * FROM agent_evidence_candidate WHERE run_id=%s ORDER BY candidate_id',(parent_id,)); rows=cursor.fetchall()
        if len(rows)>500: raise ValueError('manual calculation source projection exceeds 500 candidates')
        sources={}; result={}
        for row in rows:
            if row['metric'] not in profile.scope_policy.get('allowedMetrics',[]): continue
            source_id=row['source_id']
            if source_id not in sources:
                original=self.repo._source(cursor,parent_id,source_id)
                artifact=self.repo._artifact(cursor,parent_id,original['artifactId'])
                artifact_id=new_id('artifact'); new_source=new_id('source')
                cursor.execute('INSERT INTO agent_run_artifact(artifact_id,run_id,artifact_type,content_json,content_hash) VALUES(%s,%s,%s,%s,%s) '
                               'ON CONFLICT(run_id,content_hash) DO NOTHING RETURNING artifact_id',
                               (artifact_id,run_id,artifact['artifact_type'],Json(artifact['content_json']),artifact['content_hash']))
                inserted=cursor.fetchone()
                if not inserted:
                    cursor.execute('SELECT artifact_id FROM agent_run_artifact WHERE run_id=%s AND content_hash=%s',(run_id,artifact['content_hash'])); artifact_id=cursor.fetchone()['artifact_id']
                fresh=artifact_source({**artifact,'run_id':run_id,'artifact_id':artifact_id},run_id,original['locator'].get('evidenceIndex'))
                cursor.execute('INSERT INTO agent_source_snapshot(source_id,run_id,artifact_id,artifact_hash,content_hash,source_uri,content_kind,locator_json,metadata_json) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                    (new_source,run_id,artifact_id,fresh['artifactHash'],fresh['contentHash'],fresh['sourceUri'],fresh['contentKind'],Json(fresh['locator']),Json(fresh['metadata'])))
                sources[source_id]={**fresh,'sourceId':new_source}
            source=sources[source_id]
            candidate=constrained_candidate(dict(row['candidate_json']),source,profile.scope_policy)
            cursor.execute('INSERT INTO agent_evidence_candidate(candidate_id,run_id,source_id,fingerprint,entity_id,metric,candidate_json,quote,start_offset,end_offset,citation_validated) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,true)',
                (candidate['candidateId'],run_id,source['sourceId'],candidate['fingerprint'],candidate['entityId'],candidate['metric'],Json(candidate),candidate['quote'],candidate['startOffset'],candidate['endOffset']))
            result[row['candidate_id']]={'candidateId':candidate['candidateId'],'sourceId':source['sourceId'],'parentSourceId':source_id,
                'parentFingerprint':row['fingerprint'],'fingerprint':candidate['fingerprint'],'contentHash':source['contentHash'],
                'sourceVersion':source['metadata'].get('sourceVersion') or source['artifactHash'],'parentArtifactHash':source['artifactHash'],
                'reviewStatus':'pending','truthVerified':False}
        return result

    async def execute(self,run_id,actor,token):
        with self.repo.execution_lock(run_id):
            with self.repo.connection_factory() as con:
                with con.cursor() as cursor:
                    self.authorize(cursor,actor,token,True)
                    cursor.execute('SELECT * FROM agent_engineering_run WHERE run_id=%s',(run_id,)); link=cursor.fetchone()
                    if not link or link['principal_id']!=actor.principal_id: raise PermissionError('calculation owner required')
                    cursor.execute('SELECT 1 FROM agent_run_queue WHERE run_id=%s',(run_id,))
                    if cursor.fetchone(): raise PermissionError('manual calculation cannot execute a worker-managed Run')
            run=self.repo.get_run(run_id)
            if run['status']!='created': return self.result(run_id,actor,token)
            profile=self.repo.load_profile_snapshot(run_id); ctx=RunContext(run_id,profile,run['input_json'])
            for status in ('queued','planning','running'): self.repo.transition(run_id,status)
            if digest(link['frozen_input_json'])!=link['frozen_input_hash'] or digest({'payload':link['projected_payload_json'],'candidates':link['candidate_projection_json']})!=link['projection_hash']:
                raise ValueError('frozen calculation hash mismatch')
            step_id=self.repo.step(run_id,'manual-engineering','running',link['projected_payload_json'])
            try:
                result=await self.engine.gateway.invoke(ctx,step_id,link['tool_key'],link['projected_payload_json'],'manual-engineering')
                self.repo.step(run_id,'manual-engineering','succeeded',output=result)
                self.repo.transition(run_id,'completed',output={'calculationArtifactId':result['artifactId'],'formulaVersion':result['formulaVersion'],
                    'inputHash':result['inputHash'],'status':result.get('status') or result.get('mathematicalResult'),
                    'parentRunId':link['parent_run_id'],'truthVerified':False,'reviewedCompliance':'unknown'})
            except Exception:
                self.repo.step(run_id,'manual-engineering','failed',error='calculation rejected; inspect audited input and tool call')
                self.repo.transition(run_id,'failed',error='calculation rejected; source, unit, contract or budget check failed')
            return self.result(run_id,actor,token)


def create_engineering_router(engine,*,auth=None):
    factory=getattr(engine,'base_connection_factory',engine.repo.connection_factory)
    auth=auth or AdminAuth(factory); service=ManualEngineeringService(engine)
    router=APIRouter(prefix='/api/agent-admin',tags=['engineering'])
    def access(request:Request,response:Response):
        response.headers['Cache-Control']='no-store'
        origin=request.headers.get('origin')
        if request.method not in {'GET','HEAD'} and origin and origin.rstrip('/')!=str(request.base_url).rstrip('/'):
            raise HTTPException(403,'cross-origin calculation rejected')
        bearer=request.headers.get('authorization',''); token=bearer[7:] if bearer.startswith('Bearer ') else request.cookies.get('agent_admin_session')
        actor=auth.authenticate(token)
        if actor is None: raise HTTPException(401,'authenticated operator required')
        return actor,token
    def failure(error):
        if isinstance(error,PermissionError): raise HTTPException(403,'current authorized operator required') from None
        if isinstance(error,LookupError): raise HTTPException(404,'Run or calculation not found') from None
        if isinstance(error,EngineeringConflictError): raise HTTPException(409,'operation key identifies another calculation') from None
        if isinstance(error,(ValueError,TypeError,KeyError)): raise HTTPException(400,'invalid calculation input or source binding') from None
        raise HTTPException(409,'calculation could not be applied; inspect persisted audit') from None
    @router.get('/runs/{run_id}/engineering')
    def describe(run_id:str,identity=Depends(access)):
        try: return service.describe(run_id,*identity)
        except Exception as error: failure(error)
    @router.post('/runs/{run_id}/engineering-calculations',status_code=201)
    async def calculate(run_id:str,body:dict,identity=Depends(access)):
        try:
            calculation_id=service.create(run_id,*identity,body)
            return await service.execute(calculation_id,*identity)
        except Exception as error: failure(error)
    @router.get('/engineering-calculations/{run_id}')
    def calculation(run_id:str,identity=Depends(access)):
        try: return service.result(run_id,*identity)
        except Exception as error: failure(error)
    return router
