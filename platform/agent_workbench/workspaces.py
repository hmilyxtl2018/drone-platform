"""Immutable research inputs and atomic, idempotent Engine run admission.

Run callbacks receive this service's cursor and must never commit it or open a
second write transaction. Root integration supplies create_run(cursor,agent_key,
input_data)->run_id and enqueue(cursor,run_id). Engine repository.create_run's
_cursor parameter is the supported boundary; engine.create_run/queue.enqueue
without the cursor cannot provide atomic admission.
"""
from __future__ import annotations

from copy import deepcopy
from contextlib import nullcontext
import hashlib
import json
import re

from psycopg2.extras import Json

from agent_admin.credentials import Principal, token_digest
from agent_admin.repository import reject_secrets
from agent_engine.evidence_repository import artifact_json_hash
from agent_engine.repository import new_id


MAX_MATERIAL_BYTES = 30_000
MAX_WORKSPACE_BYTES = 120_000
TERMINAL = frozenset({'completed','completed-with-gaps','requires-human-review','blocked','failed','cancelled'})


class WorkspaceConflict(ValueError):
    """The editor must reload before saving an outdated revision."""


def _json_bytes(value):
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                          separators=(',', ':')).encode('utf-8')
    except (TypeError, ValueError, RecursionError):
        raise ValueError('workspace input must be finite JSON') from None


def _hash(value):
    return hashlib.sha256(_json_bytes(value)).hexdigest()


def _text(value, name, maximum, *, empty=False):
    if not isinstance(value,str) or len(value.encode('utf-8'))>maximum or (not empty and not value.strip()):
        raise ValueError(name+' must be bounded text')
    return value.strip()


def _reject_input_secrets(value):
    reject_secrets(value)
    if isinstance(value,dict):
        for key,child in value.items():
            normalized=re.sub('[^a-z0-9]','',str(key).lower())
            if normalized in {'credentialref','secretref','clientsecret','bearertoken'}:
                raise ValueError('credentials and credential references are not research material')
            _reject_input_secrets(child)
    elif isinstance(value,list):
        for child in value: _reject_input_secrets(child)
    elif isinstance(value,str):
        if re.search(r'\baat_[A-Za-z0-9_-]{20,}|(?:api[_ -]?key|password|client[_ -]?secret|access[_ -]?token)\s*[:=]\s*\S+',value,re.I):
            raise ValueError('raw credentials are not accepted in research material')


def validate_content(payload):
    if not isinstance(payload,dict) or set(payload)-{'title','question','entityId','requirements','materials'}:
        raise ValueError('invalid workspace fields')
    _reject_input_secrets(payload)
    result={'title':_text(payload.get('title'),'title',240),
            'question':_text(payload.get('question'),'question',8000),
            'entityId':_text(payload.get('entityId'),'entityId',200),
            'requirements':deepcopy(payload.get('requirements',{})),'materials':[]}
    if not re.fullmatch(r'[A-Za-z0-9_.:-]{1,200}',result['entityId']):
        raise ValueError('invalid entity ID')
    if not isinstance(result['requirements'],dict) or len(result['requirements'])>100:
        raise ValueError('requirements must be an object with at most 100 metrics')
    if any(not isinstance(key,str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,100}',key)
           for key in result['requirements']):
        raise ValueError('invalid requirement metric name')
    if len(_json_bytes(result['requirements']))>30_000:
        raise ValueError('requirements exceed size limit')
    materials=payload.get('materials',[])
    if not isinstance(materials,list) or len(materials)>12:
        raise ValueError('materials must be a bounded list')
    for material in materials:
        if not isinstance(material,dict) or set(material)-{'title','text'}:
            raise ValueError('materials accept title/text only; trust and locations are server-controlled')
        title=_text(material.get('title'),'material title',240)
        text=_text(material.get('text'),'material text',MAX_MATERIAL_BYTES)
        digest=hashlib.sha256(text.encode('utf-8')).hexdigest()
        result['materials'].append({'materialId':'material-'+digest[:24],'title':title,'text':text,
                                    'contentHash':digest,'trust':'unverified-user-input','mediaType':'text/plain'})
    supplemental='\n\n'.join('UNVERIFIED USER MATERIAL: '+item['title']+'\n'+item['text'] for item in result['materials'])
    if len(supplemental)>MAX_MATERIAL_BYTES or len(supplemental.encode('utf-8'))>MAX_MATERIAL_BYTES:
        raise ValueError('combined supplemental material exceeds size limit')
    if len(_json_bytes(result))>MAX_WORKSPACE_BYTES:
        raise ValueError('workspace exceeds size limit')
    return result


class WorkspaceRepository:
    def __init__(self,connection_factory): self.connection_factory=connection_factory

    @staticmethod
    def _identity(cursor,actor,*,write=False,actor_token=None):
        if not isinstance(actor,Principal): raise PermissionError('authenticated human identity required')
        digest=token_digest(actor_token)
        if digest is None: raise PermissionError('server-issued actor token required')
        cursor.execute('SELECT i.principal_id,i.kind,i.enabled,i.roles_json FROM agent_admin_identity i '
                       'JOIN agent_admin_token t USING(principal_id) WHERE i.principal_id=%s AND t.token_hash=%s '
                       'AND t.revoked_at IS NULL AND t.expires_at>clock_timestamp() FOR SHARE OF i,t',
                       (actor.principal_id,digest))
        row=cursor.fetchone()
        if not row or not row['enabled'] or row['kind']!='human' or not set(row['roles_json']).intersection(
                {'admin','operator'} if write else {'admin','operator','reviewer'}):
            raise PermissionError('active human operator required' if write else 'active human identity required')
        return dict(row)

    @staticmethod
    def _workspace(cursor,workspace_id,identity,*,write=False):
        cursor.execute('SELECT * FROM agent_research_workspace WHERE workspace_id=%s'+(' FOR UPDATE' if write else ''),
                       (workspace_id,))
        row=cursor.fetchone()
        if not row: raise ValueError('workspace not found')
        if row['owner_id']!=identity['principal_id'] and 'admin' not in identity['roles_json']:
            raise PermissionError('workspace owner or administrator required')
        return dict(row)

    @staticmethod
    def _revision(cursor,workspace_id,revision):
        if type(revision) is not int or revision<1: raise ValueError('invalid workspace revision')
        cursor.execute('SELECT * FROM agent_workspace_revision WHERE workspace_id=%s AND revision=%s',
                       (workspace_id,revision))
        row=cursor.fetchone()
        if not row: raise ValueError('workspace revision not found')
        if _hash(row['content_json'])!=row['content_hash']: raise ValueError('workspace revision hash mismatch')
        return dict(row)

    def create(self,actor,payload,*,actor_token):
        content=validate_content(payload); workspace_id=new_id('workspace')
        with self.connection_factory() as con:
            with con.cursor() as c:
                identity=self._identity(c,actor,write=True,actor_token=actor_token)
                c.execute('INSERT INTO agent_research_workspace(workspace_id,owner_id,current_revision) VALUES(%s,%s,1)',
                          (workspace_id,identity['principal_id']))
                c.execute('INSERT INTO agent_workspace_revision(workspace_id,revision,content_json,content_hash,actor_id) '
                          'VALUES(%s,1,%s,%s,%s)',(workspace_id,Json(content),_hash(content),identity['principal_id']))
                return {'workspaceId':workspace_id,'revision':1,'contentHash':_hash(content),'content':content}

    def revise(self,actor,workspace_id,expected_revision,payload,*,actor_token):
        if type(expected_revision) is not int or expected_revision<1: raise ValueError('invalid expected revision')
        content=validate_content(payload)
        with self.connection_factory() as con:
            with con.cursor() as c:
                identity=self._identity(c,actor,write=True,actor_token=actor_token)
                workspace=self._workspace(c,workspace_id,identity,write=True)
                if workspace['current_revision']!=expected_revision: raise WorkspaceConflict('workspace changed; reload before saving')
                previous=self._revision(c,workspace_id,expected_revision)
                if previous['content_hash']==_hash(content):
                    return {'workspaceId':workspace_id,'revision':expected_revision,'contentHash':previous['content_hash'],'changed':False,'content':content}
                revision=expected_revision+1
                c.execute('UPDATE agent_research_workspace SET current_revision=%s,updated_at=now() WHERE workspace_id=%s',
                          (revision,workspace_id))
                c.execute('INSERT INTO agent_workspace_revision(workspace_id,revision,content_json,content_hash,actor_id) '
                          'VALUES(%s,%s,%s,%s,%s)',(workspace_id,revision,Json(content),_hash(content),identity['principal_id']))
                return {'workspaceId':workspace_id,'revision':revision,'contentHash':_hash(content),'changed':True,'content':content}

    def get(self,actor,workspace_id,revision=None,*,actor_token):
        with self.connection_factory() as con:
            with con.cursor() as c:
                identity=self._identity(c,actor,actor_token=actor_token); workspace=self._workspace(c,workspace_id,identity)
                version=self._revision(c,workspace_id,workspace['current_revision'] if revision is None else revision)
                return {'workspace':workspace,'revision':version,'trust':'unverified-user-input'}

    def list(self,actor,limit=50,*,actor_token):
        if type(limit) is not int or not 1<=limit<=200: raise ValueError('invalid list limit')
        with self.connection_factory() as con:
            with con.cursor() as c:
                identity=self._identity(c,actor,actor_token=actor_token)
                c.execute('SELECT w.*,r.content_json->>\'title\' AS title,r.content_json->>\'entityId\' AS entity_id '
                          'FROM agent_research_workspace w JOIN agent_workspace_revision r '
                          'ON r.workspace_id=w.workspace_id AND r.revision=w.current_revision '
                          "WHERE (w.owner_id=%s OR %s) ORDER BY w.updated_at DESC,w.workspace_id LIMIT %s",
                          (identity['principal_id'],'admin' in identity['roles_json'],limit))
                return [dict(row) for row in c.fetchall()]

    def revisions(self,actor,workspace_id,*,actor_token):
        with self.connection_factory() as con:
            with con.cursor() as c:
                identity=self._identity(c,actor,actor_token=actor_token); self._workspace(c,workspace_id,identity)
                c.execute('SELECT workspace_id,revision,content_hash,actor_id,created_at FROM agent_workspace_revision '
                          'WHERE workspace_id=%s ORDER BY revision',(workspace_id,))
                return [dict(row) for row in c.fetchall()]

    def bindings(self,actor,workspace_id,*,actor_token):
        with self.connection_factory() as con:
            with con.cursor() as c:
                identity=self._identity(c,actor,actor_token=actor_token); self._workspace(c,workspace_id,identity)
                c.execute('SELECT b.*,r.status,r.agent_key FROM agent_workspace_run b JOIN agent_run r USING(run_id) '
                          'WHERE workspace_id=%s ORDER BY b.created_at,b.binding_id',(workspace_id,))
                return [dict(row) for row in c.fetchall()]

    def compare(self,actor,workspace_id,left_run_id,right_run_id,*,actor_token):
        with self.connection_factory() as con:
            with con.cursor() as c:
                identity=self._identity(c,actor,actor_token=actor_token); self._workspace(c,workspace_id,identity)
                results=[]
                for run_id in (left_run_id,right_run_id):
                    c.execute('SELECT b.*,r.status,r.output_json FROM agent_workspace_run b JOIN agent_run r USING(run_id) '
                              'WHERE b.workspace_id=%s AND b.run_id=%s',(workspace_id,run_id))
                    binding=c.fetchone()
                    if not binding: raise ValueError('comparison Run is not bound to this workspace')
                    version=self._revision(c,workspace_id,binding['revision'])
                    c.execute("SELECT artifact_id,artifact_type,content_json,content_hash FROM agent_run_artifact "
                              "WHERE run_id=%s AND artifact_type='agent-output' ORDER BY artifact_id",(run_id,))
                    refs=[]
                    for artifact in c.fetchall():
                        if artifact_json_hash(artifact['content_json'])!=artifact['content_hash']:
                            raise ValueError('report artifact hash mismatch')
                        refs.append({'artifactId':artifact['artifact_id'],'contentHash':artifact['content_hash']})
                    results.append({'runId':run_id,'revision':binding['revision'],'revisionHash':binding['revision_hash'],
                                    'status':binding['status'],'input':version['content_json'],
                                    'output':binding['output_json'],'reportArtifacts':refs})
                left,right=results
                changed=[key for key in ('title','question','entityId','requirements','materials') if left['input'].get(key)!=right['input'].get(key)]
                return {'workspaceId':workspace_id,'left':left,'right':right,'changedInputFields':changed,
                        'outputChanged':left['output']!=right['output'],'comparison':'historical-results-not-new-verification'}


class WorkspaceRunService:
    def __init__(self,repository,create_run,enqueue,agent_key='uav-capability-analyst',admission_context=None):
        self.repository,self.create_run,self.enqueue,self.agent_key=repository,create_run,enqueue,agent_key
        self.admission_context=admission_context or nullcontext

    def start(self,actor,workspace_id,revision,idempotency_key,options=None,previous_run_id=None,reason=None,*,actor_token):
        if not isinstance(idempotency_key,str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,160}',idempotency_key):
            raise ValueError('stable workspace run operation key required')
        options={} if options is None else options
        if not isinstance(options,dict) or set(options)-{'collaborate','includeDesign','budgetLimits'}:
            raise ValueError('invalid workspace run options')
        _reject_input_secrets(options)
        for name in ('collaborate','includeDesign'):
            if name in options and type(options[name]) is not bool: raise ValueError(name+' must be boolean')
        if 'budgetLimits' in options:
            from agent_engine.phase3_repository import _counters
            _counters(options['budgetLimits'])
        if previous_run_id is not None: reason=_text(reason,'follow-up reason',4000)
        elif reason is not None: raise ValueError('follow-up reason requires a previous Run')
        request_hash=_hash({'revision':revision,'options':options,'previousRunId':previous_run_id,'reason':reason,'agentKey':self.agent_key})
        repo=self.repository
        with self.admission_context(),repo.connection_factory() as con:
            with con.cursor() as c:
                identity=repo._identity(c,actor,write=True,actor_token=actor_token)
                repo._workspace(c,workspace_id,identity,write=True)
                version=repo._revision(c,workspace_id,revision)
                c.execute('SELECT * FROM agent_workspace_run WHERE workspace_id=%s AND idempotency_key=%s',
                          (workspace_id,idempotency_key))
                saved=c.fetchone()
                if saved:
                    if saved['request_hash']!=request_hash or saved['actor_id']!=identity['principal_id']:
                        raise WorkspaceConflict('operation key identifies a different workspace run')
                    return {'workspaceId':workspace_id,'revision':saved['revision'],'runId':saved['run_id'],'created':False}
                content=version['content_json']; previous_refs=[]
                if previous_run_id:
                    c.execute('SELECT b.revision,r.status FROM agent_workspace_run b JOIN agent_run r USING(run_id) '
                              'WHERE b.workspace_id=%s AND b.run_id=%s',(workspace_id,previous_run_id))
                    previous=c.fetchone()
                    if not previous or previous['revision']>revision or previous['status'] not in TERMINAL:
                        raise ValueError('follow-up requires an earlier terminal Run in this workspace')
                    c.execute("SELECT artifact_id,content_json,content_hash FROM agent_run_artifact WHERE run_id=%s "
                              "AND artifact_type='agent-output' ORDER BY artifact_id",(previous_run_id,))
                    for artifact in c.fetchall():
                        if artifact_json_hash(artifact['content_json'])!=artifact['content_hash']: raise ValueError('previous report hash mismatch')
                        previous_refs.append({'artifactId':artifact['artifact_id'],'contentHash':artifact['content_hash']})
                data={'executionMode':'research','entityId':content['entityId'],'question':content['question'],
                      'requirements':deepcopy(content['requirements']),'workspaceId':workspace_id,'workspaceRevision':revision,
                      'workspaceRevisionHash':version['content_hash'],
                      'supplementalMaterial':'\n\n'.join('UNVERIFIED USER MATERIAL: '+item['title']+'\n'+item['text'] for item in content['materials']),
                      'workspaceMaterials':deepcopy(content['materials']),**deepcopy(options)}
                if previous_run_id: data['previousRunId']=previous_run_id
                from agent_engine.task_intent import admission_actor
                from agent_engine.run_origin import origin_context
                with admission_actor(actor), origin_context(actor, source_kind='workspace', source_ref=workspace_id):
                    run_id=self.create_run(c,self.agent_key,data)
                c.execute('INSERT INTO agent_workspace_run(binding_id,workspace_id,revision,run_id,idempotency_key,request_hash,'
                          'revision_hash,actor_id,previous_run_id) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                          (new_id('workspace-run'),workspace_id,revision,run_id,idempotency_key,request_hash,
                           version['content_hash'],identity['principal_id'],previous_run_id))
                if previous_run_id:
                    c.execute('INSERT INTO agent_run_followup(previous_run_id,run_id,idempotency_key,principal_id,reason,'
                              'previous_artifacts_json,request_hash) VALUES(%s,%s,%s,%s,%s,%s,%s)',
                              (previous_run_id,run_id,'workspace:'+idempotency_key,identity['principal_id'],reason,Json(previous_refs),request_hash))
                self.enqueue(c,run_id)
                return {'workspaceId':workspace_id,'revision':revision,'runId':run_id,'status':'queued','created':True}
