"""Engine adapters for deterministic concept calculations, never certification.

Register through register_engineering_tools(engine). ToolGateway remains the
only runtime entry: its frozen binding, budget and audit apply unchanged.
Quantities marked as sourced must match a persisted same-Run candidate and its
original text hash. User assumptions remain explicit and unverified.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json

from agent_admin.repository import reject_secrets
from agent_engine.evidence_repository import artifact_json_hash
from agent_tools.evidence import candidate_fingerprint
from .engineering import (FORMULA_VERSION, TOOL_INPUT_SCHEMAS as PURE_INPUT_SCHEMAS,
                          TOOL_OUTPUT_SCHEMAS as PURE_OUTPUT_SCHEMAS, compare_quantities,
                          endurance_budget, evaluate_requirement, mass_budget)


_ID={'type':'string','minLength':1,'maxLength':200}
_HASH={'type':'string','pattern':'^[0-9a-f]{64}$'}
_BINDING={'type':'object','required':['path','candidateId'],'properties':{
    'path':{'type':'string','pattern':r'^(energy|averagePower|massLimit|components\.[0-9]{1,3}\.mass)$'},
    'candidateId':deepcopy(_ID)},'additionalProperties':False}
TOOL_INPUT_SCHEMAS={
    'engineering.evaluate_requirement':{'type':'object','required':['requirementMetric'],'properties':{
        'requirementMetric':deepcopy(_ID),'candidateId':deepcopy(_ID),'workspaceRevisionHash':deepcopy(_HASH)},'additionalProperties':False},
    **{key:{'type':'object','required':['calculation'],'properties':{
        'calculation':deepcopy(PURE_INPUT_SCHEMAS[key]),
        'candidateBindings':{'type':'array','maxItems':1000,'items':deepcopy(_BINDING)},
        'workspaceRevisionHash':deepcopy(_HASH)},'additionalProperties':False}
       for key in ('engineering.mass_budget','engineering.endurance_budget')},
}
TOOL_OUTPUT_SCHEMAS={key:{**deepcopy(schema),'required':schema['required']+['artifactId','artifactHash','serverProvenance']}
                     for key,schema in PURE_OUTPUT_SCHEMAS.items()}


def _json_hash(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,allow_nan=False,separators=(',',':')).encode()).hexdigest()


def _candidate_quantity(candidate):
    qualifier=candidate.get('qualifier')
    if qualifier=='exact' and type(candidate.get('value')) in (int,float):
        return {'value':candidate['value'],'unit':candidate.get('unit')}
    if qualifier=='range' and isinstance(candidate.get('range'),dict) and all(
            type(candidate['range'].get(key)) in (int,float) for key in ('min','max')):
        return {'min':candidate['range']['min'],'max':candidate['range']['max'],'unit':candidate.get('unit')}
    return None


class EngineeringRuntime:
    def __init__(self,repository): self.repository=repository

    def _run_input(self,ctx):
        run=self.repository.get_run(ctx.run_id)
        if not run: raise ValueError('engineering Run not found')
        return run.get('input_json') or {}

    def _workspace(self,ctx,data,expected_hash=None):
        workspace_id=data.get('workspaceId')
        if not workspace_id:
            if expected_hash is not None: raise ValueError('Run has no bound workspace revision')
            return None
        revision=data.get('workspaceRevision'); digest=data.get('workspaceRevisionHash')
        if type(revision) is not int or not isinstance(digest,str): raise ValueError('invalid frozen workspace reference')
        with self.repository.connection_factory() as con:
            with con.cursor() as c:
                c.execute('SELECT content_json,content_hash FROM agent_workspace_revision WHERE workspace_id=%s AND revision=%s',
                          (workspace_id,revision))
                row=c.fetchone()
                if not row or row['content_hash']!=digest or _json_hash(row['content_json'])!=digest:
                    raise ValueError('frozen workspace revision hash mismatch')
                # Handoff descendants inherit exact workspace metadata but are
                # admitted through an immutable parent chain, not a new binding.
                current=ctx.run_id; visited=set(); admitted=False
                for _ in range(4):
                    if current in visited: raise ValueError('invalid workspace handoff cycle')
                    visited.add(current)
                    c.execute('SELECT revision_hash FROM agent_workspace_run WHERE run_id=%s AND workspace_id=%s AND revision=%s',
                              (current,workspace_id,revision))
                    binding=c.fetchone()
                    if binding:
                        admitted=binding['revision_hash']==digest
                        break
                    c.execute('SELECT run_id FROM agent_run_handoff WHERE child_run_id=%s',(current,))
                    parent=c.fetchone()
                    if not parent: break
                    current=parent['run_id']
                if not admitted: raise ValueError('Run is not bound to this workspace revision')
                if expected_hash is not None and expected_hash!=digest: raise ValueError('caller workspace hash mismatch')
                return {'workspaceId':workspace_id,'revision':revision,'contentHash':digest,'content':deepcopy(row['content_json'])}

    def _candidate(self,ctx,candidate_id,entity_id):
        matches=[item for item in self.repository.list_candidates(ctx.run_id) if item['candidateId']==candidate_id]
        if len(matches)!=1: raise ValueError('engineering candidate must belong to this Run')
        candidate=deepcopy(matches[0])
        if candidate.get('entityId')!=entity_id or candidate_fingerprint(candidate)!=candidate.get('fingerprint'):
            raise ValueError('engineering candidate entity or fingerprint mismatch')
        source=self.repository.get_source_snapshot(ctx.run_id,candidate['sourceId'])
        if (candidate.get('contentHash')!=source['contentHash'] or candidate.get('sourceArtifactHash')!=source['artifactHash']
                or candidate.get('quote')!=source['markdown'][candidate['startOffset']:candidate['endOffset']]):
            raise ValueError('engineering candidate source hash or quote mismatch')
        return candidate,source

    def _requirement(self,ctx,payload,data,workspace,provenance):
        metric=payload['requirementMetric']; entity=data.get('entityId') or data.get('uavId')
        requirements=workspace['content'].get('requirements',{}) if workspace else data.get('requirements',{})
        value=requirements.get(metric) if isinstance(requirements,dict) else None
        if not isinstance(value,dict):
            value={'operator':'>=','quantity':None}
        quantity=value.get('quantity')
        if quantity is None and value.get('unit'):
            if 'value' in value: quantity={'value':value['value'],'unit':value['unit']}
            elif isinstance(value.get('range'),dict): quantity={**deepcopy(value['range']),'unit':value['unit']}
        requirement={'requirementId':value.get('requirementId','requirement:'+metric),'entityId':entity,
                     'metric':metric,'inputVersion':workspace['revision'] if workspace else 1,
                     'operator':value.get('operator','>='),'quantity':deepcopy(quantity),'conditions':deepcopy(value.get('conditions'))}
        observation={'observationId':'missing:'+metric,'entityId':entity,'metric':metric,'inputVersion':1,
                     'quantity':None,'conditions':None,'evidenceRefs':[],'truthVerified':False,'reviewStatus':'pending'}
        if payload.get('candidateId'):
            candidate,source=self._candidate(ctx,payload['candidateId'],entity)
            if candidate['metric']!=metric: raise ValueError('candidate metric differs from requirement')
            observation.update(observationId=candidate['candidateId'],quantity=_candidate_quantity(candidate),
                               conditions=deepcopy(candidate.get('conditions')) if isinstance(candidate.get('conditions'),dict) else None,
                               claimState=candidate.get('qualifier'),
                               inputVersion=candidate['fingerprint'],evidenceRefs=[{'sourceId':candidate['sourceId'],
                                   'sourceVersion':source['metadata'].get('sourceVersion') or source['artifactHash'],
                                   'contentHash':source['contentHash'],'artifactId':source['artifactId'],'artifactHash':source['artifactHash']}])
            bundle=self.repository.evidence_bundle(ctx.run_id)
            observation['conflicts']=[item for item in bundle.get('conflicts',[]) if candidate['candidateId'] in item.get('candidateIds',[])]
            provenance['candidateIds'].append(candidate['candidateId'])
        return evaluate_requirement({'requirement':requirement,'observation':observation},formula_version=FORMULA_VERSION)

    def _calculation(self,ctx,key,payload,data,workspace,provenance):
        calculation=deepcopy(payload['calculation']); bindings={}
        for item in payload.get('candidateBindings',[]):
            if item['path'] in bindings: raise ValueError('duplicate engineering candidate binding')
            bindings[item['path']]=item['candidateId']
        entity=data.get('entityId') or data.get('uavId'); consumed=set()
        metric_names={'energy':{'energyWh','batteryEnergyWh','energy'},'averagePower':{'averagePowerW','averagePower'},
                      'massLimit':{'mtowKg','massLimitKg','massLimit'}}
        quantities=[]
        if key=='engineering.mass_budget':
            quantities=[('massLimit',calculation.get('massLimit'))]
            quantities.extend((f'components.{index}.mass',component.get('mass')) for index,component in enumerate(calculation.get('components',[])))
        else: quantities=[('energy',calculation.get('energy')),('averagePower',calculation.get('averagePower'))]
        for path,quantity in quantities:
            if quantity is None: continue
            basis=quantity.get('basis')
            if not basis: continue # Pure formula returns explicit missing basis.
            if basis.get('kind')=='assumption':
                if path in bindings: raise ValueError('assumption cannot masquerade as a source binding')
                provenance['unverifiedAssumptions'].append({'path':path,'description':basis.get('description')})
                continue
            candidate_id=bindings.get(path)
            if not candidate_id: raise ValueError('sourced quantity requires an explicit same-Run candidate binding')
            consumed.add(path); candidate,source=self._candidate(ctx,candidate_id,entity)
            if path.startswith('components.'):
                allowed={'massKg','componentMassKg','mass'}
            else: allowed=metric_names[path]
            if candidate['metric'] not in allowed: raise ValueError('candidate metric does not match calculation input')
            expected=_candidate_quantity(candidate)
            if expected is None: raise ValueError('source does not provide an exact or closed-interval quantity')
            if compare_quantities(quantity,'==',expected)['result']!='satisfied':
                # Identical closed intervals are matching inputs even though a
                # universal equality requirement over intervals stays unknown.
                from .engineering import normalize_quantity
                if normalize_quantity(quantity)!=normalize_quantity(expected): raise ValueError('calculation input differs from source quantity')
            source_version=source['metadata'].get('sourceVersion') or source['artifactHash']
            if basis.get('sourceId')!=source['sourceId'] or basis.get('contentHash')!=source['contentHash'] or basis.get('sourceVersion')!=source_version:
                raise ValueError('calculation source identity/version/hash mismatch')
            if candidate.get('conditions')!=calculation.get('conditions'):
                raise ValueError('calculation conditions differ from source conditions')
            if key=='engineering.endurance_budget':
                boundary=candidate.get('systemBoundary')
                if not boundary and isinstance(candidate.get('conditions'),dict):
                    boundary=candidate['conditions'].get('systemBoundary')
                if boundary is None:
                    calculation.pop('systemBoundary',None)
                    provenance.setdefault('missingSourceInputs',[]).append(path+'.systemBoundary')
                elif boundary!=calculation.get('systemBoundary'):
                    raise ValueError('calculation system boundary differs from source')
            quantity['basis']={'kind':'reported-specification','sourceId':source['sourceId'],
                               'sourceVersion':source_version,'contentHash':source['contentHash']}
            provenance['candidateIds'].append(candidate_id)
        if set(bindings)!=consumed: raise ValueError('unused engineering candidate binding')
        calculation['inputVersion']=workspace['revision'] if workspace else 'run:'+ctx.run_id
        function=mass_budget if key=='engineering.mass_budget' else endurance_budget
        return function(calculation,formula_version=FORMULA_VERSION)

    async def invoke(self,key,payload,ctx):
        if ctx.profile.tools[key].version!=FORMULA_VERSION: raise ValueError('unsupported frozen engineering tool version')
        reject_secrets(payload)
        data=self._run_input(ctx); workspace=self._workspace(ctx,data,payload.get('workspaceRevisionHash'))
        provenance={'runId':ctx.run_id,'workspace':{k:v for k,v in workspace.items() if k!='content'} if workspace else None,
                    'candidateIds':[],'unverifiedAssumptions':[],'reviewStatus':'pending','truthVerified':False,
                    'inputAuthority':'same-run-source-or-explicit-assumption'}
        result=self._requirement(ctx,payload,data,workspace,provenance) if key=='engineering.evaluate_requirement' \
               else self._calculation(ctx,key,payload,data,workspace,provenance)
        result['serverProvenance']=provenance
        result['runInputSnapshot']={'runId':ctx.run_id,'toolKey':key,'formulaVersion':FORMULA_VERSION,
                                    'formulaInputHash':result['inputHash'],'provenance':deepcopy(provenance)}
        result['runInputHash']=_json_hash(result['runInputSnapshot'])
        result['toolKey']=key
        artifact_id=self.repository.add_artifact(ctx.run_id,'engineering-calculation',result)
        return {**result,'artifactId':artifact_id,'artifactHash':artifact_json_hash(result)}


def register_engineering_tools(engine):
    runtime=EngineeringRuntime(engine.repo)
    for key in TOOL_INPUT_SCHEMAS:
        async def handler(payload,ctx,key=key): return await runtime.invoke(key,payload,ctx)
        engine.register_tool(key,handler,deepcopy(TOOL_INPUT_SCHEMAS[key]),deepcopy(TOOL_OUTPUT_SCHEMAS[key]))
    return runtime
