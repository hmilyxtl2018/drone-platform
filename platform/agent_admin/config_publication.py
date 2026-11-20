"""Immutable Agent configuration drafts and atomic, reviewed publications.

This service never calls providers, changes tool/provider/source registrations,
or rewrites a saved Run profile. API integration supplies the authenticated
Principal AND its actual current token; browser-supplied identity is insufficient.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
import re

from jsonschema import Draft202012Validator, SchemaError, ValidationError as SchemaValidationError
from psycopg2 import sql
from psycopg2.extras import Json

from agent_engine.repository import new_id
from agent_engine.capabilities import graph_snapshot, graph_errors
from agent_engine.capabilities import load_graph, digest as capability_digest
from agent_engine.execution_baseline import (baseline_installed, build_baseline, read_baseline,
                                             register_baseline, validate_baseline)
from .identity_management import IdentityManagementService
from .repository import reject_secrets


class ConfigNotFoundError(LookupError):
    pass


class ConfigConflictError(ValueError):
    pass


class ConfigValidationError(ValueError):
    def __init__(self, errors):
        self.errors = errors
        super().__init__('configuration validation failed')


SECTIONS = {
    'definition': ('agent_definition', ('display_name','description','domain','status','current_version','system_prompt')),
    'scope': ('agent_scope', ('responsibility_json','input_entities_json','output_entities_json','allowed_write_json','forbidden_actions_json')),
    'models': ('agent_model_policy', ('model_provider','model_name','temperature','max_output_tokens','structured_output_required','planner_enabled','prompt_version','config_json')),
    'resources': ('agent_resource_policy', ('max_parallel_tools','max_retries_per_tool','permitted_risk_levels_json')),
    'convergence': ('agent_convergence_policy', ('completion_conditions_json','allowed_terminal_states_json','max_tool_calls','max_model_calls','max_runtime_seconds','no_new_finding_rounds')),
    'output': ('agent_output_contract', ('schema_json','required_evidence','required_assumptions','required_confidence')),
    'evidence': ('agent_evidence_policy', ('minimum_evidence_count','require_source_uri','require_data_timestamp','allow_inference','inference_disclosure_required','conflict_action','policy_json')),
    'approval': ('agent_approval_policy', ('approval_points_json','approver_roles_json')),
    'observability': ('agent_observability_policy', ('trace_enabled','capture_tool_inputs','capture_tool_outputs','retain_days','emit_step_events','emit_metrics','policy_json')),
}
STEP_FIELDS = ('step_key','name','description','sequence_no','input_contract','output_contract','parallel_group','required','retry_limit','dependencies','tools')
BINDING_FIELDS = ('tool_key','tool_version','enabled','approval_required','config_json')
LOOP_FIELDS = ('loop_id','trigger_condition','return_step_key','max_iterations','no_progress_limit','stop_action')
RULE_FIELDS = ('rule_id','rule_type','expression','severity')
JSON_FIELDS = {field for _, fields in SECTIONS.values() for field in fields if field.endswith('_json')} | {'input_contract','output_contract'}
BOOL_FIELDS = {'structured_output_required','planner_enabled','required_evidence','required_assumptions','required_confidence',
               'require_source_uri','require_data_timestamp','allow_inference','inference_disclosure_required',
               'trace_enabled','capture_tool_inputs','capture_tool_outputs','emit_step_events','emit_metrics','required','enabled','approval_required'}
COUNTERS = {'model_calls','input_tokens','output_tokens','tool_calls','http_calls','search_calls','scrape_calls'}
TERMINAL = {'completed','completed-with-gaps','requires-human-review','blocked','failed','cancelled'}
KEY = re.compile(r'[A-Za-z0-9_.:-]{1,128}')
SECRET = re.compile(r'\baat_[A-Za-z0-9_-]{8,}\b|\bBearer\s+\S+', re.I)


def _hash(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,allow_nan=False,separators=(',',':')).encode()).hexdigest()


def _key(value, label='key'):
    if not isinstance(value,str) or not KEY.fullmatch(value):
        raise ValueError('invalid ' + label)
    return value


def _reason(value):
    if not isinstance(value,str) or not 1 <= len(value.strip()) <= 2000 or SECRET.search(value):
        raise ValueError('a credential-free reason of 1 to 2000 characters is required')
    reject_secrets(value)
    return value.strip()


def _decode(row, fields):
    result = {}
    for field in fields:
        value = row[field]
        if field in JSON_FIELDS and isinstance(value,str):
            try:
                value = json.loads(value)
            except ValueError:
                raise ConfigValidationError([{'path':field,'message':'stored JSON is invalid'}]) from None
        if field in BOOL_FIELDS:
            value = bool(value)
        result[field] = value
    return result


def _document(value):
    if not isinstance(value,dict):
        raise ValueError('configuration document must be an object')
    try:
        encoded = json.dumps(value,ensure_ascii=False,allow_nan=False)
    except (ValueError,TypeError):
        raise ValueError('configuration must be finite JSON') from None
    if len(encoded.encode()) > 1_000_000:
        raise ValueError('configuration exceeds 1 MB')
    reject_secrets(value)
    if SECRET.search(encoded):
        raise ValueError('raw credentials are not accepted')
    result=deepcopy(value)
    for section,key in (('steps','sequence_no'),('bindings','tool_key'),('loops','loop_id'),('validationRules','rule_id')):
        values=result.get(section)
        if isinstance(values,list) and all(isinstance(item,dict) and key in item for item in values):
            try:
                values.sort(key=lambda item:(item[key],item.get('step_key','')))
            except TypeError:
                pass  # Invalid field types are reported by validation, not coerced.
    if isinstance(result.get('steps'),list):
        for item in result['steps']:
            if isinstance(item,dict):
                for field in ('tools','dependencies'):
                    if isinstance(item.get(field),list) and all(isinstance(v,str) for v in item[field]):
                        item[field].sort()
    return result


def _diff(before,after,path=''):
    if before == after:
        return []
    if isinstance(before,dict) and isinstance(after,dict):
        result=[]
        for key in sorted(before.keys() | after.keys()):
            pointer=path+'/'+str(key).replace('~','~0').replace('/','~1')
            if key not in before:
                result.append({'path':pointer,'operation':'add','after':after[key]})
            elif key not in after:
                result.append({'path':pointer,'operation':'remove','before':before[key]})
            else:
                result.extend(_diff(before[key],after[key],pointer))
        return result
    return [{'path':path or '/','operation':'replace','before':before,'after':after}]


class ConfigPublicationService:
    def __init__(self,connection_factory):
        self.connection_factory=connection_factory

    @staticmethod
    def _baseline_harness(cursor, agent_key):
        from agent_engine.repository import PostgresRepository
        if not baseline_installed(cursor):
            return {}
        cursor.execute('SELECT current_version FROM agent_definition WHERE agent_key=%s', (agent_key,))
        row = cursor.fetchone()
        if not row:
            return {}
        harness = PostgresRepository(None)._load_harness_contract(cursor, agent_key, row['current_version'])[2]
        from agent_engine.skills import current_manifest, reference
        skills = current_manifest(cursor,agent_key,row['current_version'])
        return {**harness,'skillBindings':[reference(b) for b in skills.get('bindings',[])]}

    @classmethod
    def _baseline_view(cls, cursor, agent_key, document):
        graph = build_baseline(agent_key, document, cls._baseline_harness(cursor, agent_key))
        return read_baseline(cursor, agent_key, graph)

    @staticmethod
    def _authorize(cursor,actor,token):
        IdentityManagementService._authorize(cursor,actor,token)

    @staticmethod
    def _config_lock(cursor,exclusive=False):
        function='pg_advisory_xact_lock' if exclusive else 'pg_advisory_xact_lock_shared'
        cursor.execute('SELECT '+function+"(hashtextextended('agent-admin-profile-config',0))")

    @staticmethod
    def _export(cursor,agent_key):
        document={}
        for section,(table,fields) in SECTIONS.items():
            cursor.execute(sql.SQL('SELECT {} FROM {} WHERE agent_key=%s').format(
                sql.SQL(',').join(map(sql.Identifier,fields)),sql.Identifier(table)),(agent_key,))
            row=cursor.fetchone()
            if row is None:
                if section=='definition':
                    raise ConfigNotFoundError('Agent not found')
                document[section]={}
            else:
                document[section]=_decode(row,fields)
        cursor.execute('SELECT * FROM agent_step WHERE agent_key=%s ORDER BY sequence_no,step_key',(agent_key,))
        document['steps']=[]
        for row in cursor.fetchall():
            item=_decode(row,tuple(field for field in STEP_FIELDS if field not in {'dependencies','tools'}))
            item['dependencies']=row['dependencies_json']
            cursor.execute('SELECT tool_key FROM agent_step_tool WHERE step_id=%s ORDER BY tool_key',(row['step_id'],))
            item['tools']=[r['tool_key'] for r in cursor.fetchall()]
            document['steps'].append(item)
        cursor.execute('SELECT * FROM agent_tool_binding WHERE agent_key=%s ORDER BY tool_key',(agent_key,))
        document['bindings']=[_decode(row,BINDING_FIELDS) for row in cursor.fetchall()]
        cursor.execute('SELECT * FROM agent_loop_rule WHERE agent_key=%s ORDER BY loop_id',(agent_key,))
        document['loops']=[_decode(row,LOOP_FIELDS) for row in cursor.fetchall()]
        cursor.execute('SELECT * FROM agent_validation_rule WHERE agent_key=%s ORDER BY rule_id',(agent_key,))
        document['validationRules']=[_decode(row,RULE_FIELDS) for row in cursor.fetchall()]
        return document

    @staticmethod
    def _dependencies(cursor,document,agent_key=None):
        """Freeze actual referenced registries in base hash; no hidden live edits."""
        result={'tools':[],'providers':[],'sources':[]}
        bindings=document.get('bindings',[])
        if not isinstance(bindings,list): bindings=[]
        for binding in bindings:
            if not isinstance(binding,dict) or not isinstance(binding.get('tool_key'),str) or not isinstance(binding.get('tool_version'),str):
                continue
            cursor.execute('SELECT tool_key,protocol,input_schema,output_schema,access_mode,risk_level,status,current_version,endpoint '
                           'FROM tool_registry WHERE tool_key=%s',(binding.get('tool_key'),))
            registry=cursor.fetchone()
            cursor.execute("SELECT tool_key,version,endpoint,input_schema,output_schema,status, "
                           "to_jsonb(v)->>'execution_transport' AS execution_transport, "
                           "to_jsonb(v)->>'adapter_version' AS adapter_version "
                           'FROM tool_version v WHERE tool_key=%s AND version=%s',
                           (binding.get('tool_key'),binding.get('tool_version')))
            version=cursor.fetchone()
            result['tools'].append({'toolKey':binding.get('tool_key'),'registry':dict(registry) if registry else None,
                                    'version':dict(version) if version else None})
        model=document.get('models') or {}
        if not isinstance(model,dict): model={}
        config=model.get('config_json') or {}
        if not isinstance(config,dict):
            config={}
        for provider_key in sorted({str(model.get('model_provider') or ''),str(config.get('webProviderKey','firecrawl'))}):
            cursor.execute('SELECT provider_key,provider_type,base_url,credential_ref,enabled,version,config_json '
                           'FROM provider_registry WHERE provider_key=%s',(provider_key,))
            row=cursor.fetchone()
            result['providers'].append({'providerKey':provider_key,'registry':dict(row) if row else None})
        source_key=config.get('ontologySourceKey','uav-ontology')
        if not isinstance(source_key,str): source_key=''
        cursor.execute('SELECT source_key,connector,root_uri,credential_ref,read_only,enabled,policy_version,config_json '
                       'FROM data_source_registry WHERE source_key=%s',(source_key,))
        row=cursor.fetchone()
        result['sources'].append({'sourceKey':source_key,'registry':dict(row) if row else None})
        document_keys = config.get('documentSourceKeys', [])
        result['documentSources'] = []
        if isinstance(document_keys, list):
            for key in sorted({item for item in document_keys if isinstance(item, str)}):
                cursor.execute('SELECT source_key,connector,root_uri,credential_ref,read_only,enabled,policy_version,config_json '
                               'FROM data_source_registry WHERE source_key=%s', (key,))
                item = cursor.fetchone()
                result['documentSources'].append({'sourceKey': key, 'registry': dict(item) if item else None})
        if config.get('capabilityGraphPath') is not None:
            try:
                result['capabilityGraph'] = graph_snapshot(cursor, config)
            except (OSError, ValueError, TypeError, SchemaValidationError):
                result['capabilityGraph'] = {'error': 'capability graph or required Action is unavailable'}
        from agent_engine.goal_contract import current
        goal = current(cursor, agent_key) if agent_key else {}
        if goal:
            result['goalContract'] = {key: goal[key] for key in ('contractId','revision','contentHash')}
        if agent_key:
            from agent_engine.skills import publication_manifest, reference
            skills = publication_manifest(cursor,agent_key,(document.get('definition') or {}).get('current_version'))
            if skills:
                result['skills'] = [reference(b) for b in skills['bindings']]
        return result

    @classmethod
    def _live(cls,cursor,agent_key):
        document=cls._export(cursor,agent_key)
        dependencies=cls._dependencies(cursor,document,agent_key)
        return document,dependencies,_hash({'document':document,'dependencies':dependencies})

    def export_profile(self,actor,agent_key,*,actor_token):
        _key(agent_key,'Agent key')
        with self.connection_factory() as con:
            with con.cursor() as cursor:
                self._authorize(cursor,actor,actor_token)
                self._config_lock(cursor)
                document,dependencies,live_hash=self._live(cursor,agent_key)
                from agent_engine.uav_baseline_drafts import read_draft_baseline
                return {'agentKey':agent_key,'document':document,'liveHash':live_hash,'dependencies':dependencies,
                        'executionBaselineDraft': read_draft_baseline(cursor, agent_key, document),
                        'executionBaseline': self._baseline_view(cursor, agent_key, document)}

    def read_profile_view(self,agent_key):
        """Return operational profile fields without prompts or infrastructure secrets."""
        _key(agent_key,'Agent key')
        with self.connection_factory() as con:
            with con.cursor() as cursor:
                self._config_lock(cursor)
                source=self._export(cursor,agent_key)
                execution_baseline=self._baseline_view(cursor,agent_key,source)
                from agent_engine.uav_baseline_drafts import read_draft_baseline
                execution_baseline_draft=read_draft_baseline(cursor,agent_key,source)
        definition=source.get('definition') or {}
        models=source.get('models') or {}
        model_config=models.get('config_json') or {}
        capability_view={}
        capability_path=model_config.get('capabilityGraphPath') if isinstance(model_config,dict) else None
        if capability_path:
            try:
                graph=load_graph(capability_path, agent_key)
                capability_view={'graphId':graph['graphId'],'version':graph['version'],
                    'graphHash':capability_digest(graph),
                    'capabilities':[{'key':item['key'],'name':item['name'],'requiredActions':item['requiredActions'],
                                     'requiredEvidence':item['requiredEvidence'],'acceptance':item['acceptance']}
                                    for item in graph['capabilities']],
                    'forbiddenCapabilities':graph.get('forbiddenCapabilities',[])}
            except (OSError,ValueError,TypeError,SchemaValidationError):
                capability_view={'status':'invalid-or-unavailable'}
        research_steps=[]
        configured=model_config.get('researchSteps',[])
        for step in configured if isinstance(configured,list) else []:
            if not isinstance(step,dict) or not isinstance(step.get('key'),str):
                continue
            item={key:step[key] for key in ('key','name','description','executor') if isinstance(step.get(key),str)}
            if isinstance(step.get('requiredTypes'),list):
                item['requiredTypes']=[value for value in step['requiredTypes'] if isinstance(value,str)]
            research_steps.append(item)
        visible={
            'definition':{key:definition.get(key) for key in ('display_name','description','domain','status','current_version')},
            'scope':source.get('scope') or {},
            'models':{key:models.get(key) for key in ('model_provider','model_name','temperature','max_output_tokens',
                'structured_output_required','planner_enabled','prompt_version')},
            'resources':source.get('resources') or {},
            'convergence':source.get('convergence') or {},
            'output':source.get('output') or {},
            'evidence':source.get('evidence') or {},
            'approval':source.get('approval') or {},
            'observability':source.get('observability') or {},
            'steps':[{key:step.get(key) for key in ('step_key','name','description','sequence_no','parallel_group',
                'required','retry_limit','dependencies','tools')} for step in source.get('steps',[])],
            'researchSteps':research_steps,
            'bindings':[{key:binding.get(key) for key in ('tool_key','tool_version','enabled','approval_required')}
                for binding in source.get('bindings',[])],
            'loops':source.get('loops') or [],
            'validationRules':source.get('validationRules') or [],
            'dataSourceKey':model_config.get('ontologySourceKey','uav-ontology'),
            'agentLayer':model_config.get('agentLayer','application'),
            'capabilityGraph':capability_view,
            'capabilityActions':model_config.get('capabilityActions',[]),
        }
        return {'agentKey':agent_key,'document':visible,'executionBaseline':execution_baseline,
                'executionBaselineDraft':execution_baseline_draft}

    @staticmethod
    def _draft(cursor,draft_id,revision=None,lock=False):
        _key(draft_id,'draft ID')
        cursor.execute('SELECT * FROM agent_config_draft WHERE draft_id=%s'+(' FOR UPDATE' if lock else ''),(draft_id,))
        head=cursor.fetchone()
        if head is None:
            raise ConfigNotFoundError('draft not found')
        revision=revision if revision is not None else head['current_revision']
        if type(revision) is not int or revision < 1:
            raise ValueError('invalid draft revision')
        cursor.execute('SELECT * FROM agent_config_draft_revision WHERE draft_id=%s AND revision=%s',(draft_id,revision))
        row=cursor.fetchone()
        if row is None:
            raise ConfigNotFoundError('draft revision not found')
        if _hash(row['document_json']) != row['document_hash']:
            raise ConfigConflictError('saved draft hash mismatch')
        return {**dict(head),'revision':revision,'document_json':row['document_json'],
                'dependency_snapshot_json':row['dependency_snapshot_json'],
                'document_hash':row['document_hash'],'reason':row['reason'],'actor_id':row['actor_id'],'saved_at':row['created_at']}

    @staticmethod
    def _draft_result(row):
        return {'draftId':row['draft_id'],'agentKey':row['agent_key'],'revision':row['revision'],
                'currentRevision':row['current_revision'],'baseLiveHash':row['base_live_hash'],
                'documentHash':row['document_hash'],'document':row['document_json'],'reason':row['reason'],
                'dependencies':row['dependency_snapshot_json'],
                'actorId':row['actor_id'],'savedAt':row['saved_at'],'createdAt':row['created_at']}

    def create_draft(self,actor,agent_key,*,actor_token,document=None,reason):
        _key(agent_key,'Agent key')
        reason=_reason(reason)
        document=_document(document) if document is not None else None
        with self.connection_factory() as con:
            with con.cursor() as cursor:
                self._authorize(cursor,actor,actor_token)
                self._config_lock(cursor)
                live,dependencies,live_hash=self._live(cursor,agent_key)
                value=document if document is not None else live
                draft_id=self._insert_draft(cursor,agent_key,live,live_hash,value,actor.principal_id,reason)
                return self._draft_result(self._draft(cursor,draft_id))

    @classmethod
    def _insert_draft(cls,cursor,agent_key,live,live_hash,value,actor_id,reason):
        """Storage primitive. Callers must authorize their exact operation first."""
        value=_document(value);reason=_reason(reason)
        draft_id=new_id('config-draft')
        cursor.execute('INSERT INTO agent_config_draft(draft_id,agent_key,base_live_hash,base_document_json,created_by) '
                       'VALUES(%s,%s,%s,%s,%s)',(draft_id,agent_key,live_hash,Json(live),actor_id))
        cursor.execute('INSERT INTO agent_config_draft_revision(draft_id,revision,document_json,document_hash,dependency_snapshot_json,reason,actor_id) '
                       'VALUES(%s,1,%s,%s,%s,%s,%s)',(draft_id,Json(value),_hash(value),Json(cls._dependencies(cursor,value,agent_key)),reason,actor_id))
        return draft_id

    def save_draft(self,actor,draft_id,document,*,actor_token,expected_revision,reason):
        value=_document(document)
        reason=_reason(reason)
        if type(expected_revision) is not int or not 1 <= expected_revision < 2**63-1:
            raise ValueError('invalid expected revision')
        with self.connection_factory() as con:
            with con.cursor() as cursor:
                self._authorize(cursor,actor,actor_token)
                self._config_lock(cursor)
                draft=self._draft(cursor,draft_id,lock=True)
                if draft['current_revision']!=expected_revision:
                    raise ConfigConflictError('draft changed; reload its current revision')
                revision=expected_revision+1
                cursor.execute('INSERT INTO agent_config_draft_revision(draft_id,revision,document_json,document_hash,dependency_snapshot_json,reason,actor_id) '
                               'VALUES(%s,%s,%s,%s,%s,%s,%s)',(draft_id,revision,Json(value),_hash(value),Json(self._dependencies(cursor,value,draft['agent_key'])),reason,actor.principal_id))
                cursor.execute('UPDATE agent_config_draft SET current_revision=%s WHERE draft_id=%s',(revision,draft_id))
                return self._draft_result(self._draft(cursor,draft_id))

    def get_draft(self,actor,draft_id,*,actor_token,revision=None):
        with self.connection_factory() as con:
            with con.cursor() as cursor:
                self._authorize(cursor,actor,actor_token)
                return self._draft_result(self._draft(cursor,draft_id,revision))

    def list_drafts(self,actor,agent_key,*,actor_token,limit=100):
        _key(agent_key,'Agent key')
        if type(limit) is not int or not 1 <= limit <= 200:
            raise ValueError('invalid limit')
        with self.connection_factory() as con:
            with con.cursor() as cursor:
                self._authorize(cursor,actor,actor_token)
                cursor.execute('SELECT draft_id FROM agent_config_draft WHERE agent_key=%s ORDER BY created_at DESC,draft_id LIMIT %s',
                               (agent_key,limit))
                return [self._draft_result(self._draft(cursor,row['draft_id'])) for row in cursor.fetchall()]

    def validate_draft(self,actor,draft_id,*,actor_token,revision=None):
        with self.connection_factory() as con:
            with con.cursor() as cursor:
                self._authorize(cursor,actor,actor_token)
                self._config_lock(cursor)
                draft=self._draft(cursor,draft_id,revision)
                live,dependencies,live_hash=self._live(cursor,draft['agent_key'])
                errors=self._validate(cursor,draft['document_json'],draft['agent_key'])
                return {'valid':not errors,'errors':errors,'warnings':[],
                        'revision':draft['revision'],'baseLiveHash':draft['base_live_hash'],'liveHash':live_hash,
                        'liveChanged':live_hash!=draft['base_live_hash'],
                        'dependenciesChanged':self._dependencies(cursor,draft['document_json'],draft['agent_key'])!=draft['dependency_snapshot_json'],
                        'diff':_diff(draft['base_document_json'],draft['document_json']),
                        'liveDiff':_diff(live,draft['document_json'])}

    @staticmethod
    def _validate(cursor,document,agent_key=None):
        errors=[]
        def bad(path,message):
            errors.append({'path':path,'message':message})
        def integer(value,path,minimum=0,maximum=2**31-1):
            if type(value) is not int or not minimum<=value<=maximum:
                bad(path,'must be an integer between '+str(minimum)+' and '+str(maximum))
                return False
            return True
        def text(value,path,maximum=4000,empty=False):
            if not isinstance(value,str) or (not empty and not value.strip()) or len(value)>maximum:
                bad(path,'must be bounded text'+('' if empty else ' and nonempty'))
                return False
            return True
        def strings(value,path,maximum=200):
            if not isinstance(value,list) or len(value)>maximum or any(not isinstance(item,str) or not item for item in value):
                bad(path,'must be a bounded list of nonempty strings')
                return False
            if len(value)!=len(set(value)):
                bad(path,'must not contain duplicate values')
                return False
            return True
        def schema(value,path):
            def refs(item):
                if isinstance(item,dict):
                    if '$ref' in item or '$dynamicRef' in item:
                        raise ValueError()
                    for child in item.values(): refs(child)
                elif isinstance(item,list):
                    for child in item: refs(child)
            try:
                if not isinstance(value,dict): raise ValueError()
                refs(value)
                Draft202012Validator.check_schema(value)
            except (SchemaError,ValueError,TypeError,RecursionError):
                bad(path,'must be an inline JSON Schema without external references')
        expected=set(SECTIONS)|{'steps','bindings','loops','validationRules'}
        if set(document)!=expected:
            bad('/','document must contain exactly the complete exported Profile sections')
        for section,(_,fields) in SECTIONS.items():
            value=document.get(section)
            if not isinstance(value,dict) or set(value)!=set(fields):
                bad('/'+section,'must contain exactly its exported policy fields')
        for section,fields,maximum in (('steps',STEP_FIELDS,100),('bindings',BINDING_FIELDS,200),
                                       ('loops',LOOP_FIELDS,20),('validationRules',RULE_FIELDS,100)):
            value=document.get(section)
            if not isinstance(value,list) or len(value)>maximum or any(not isinstance(item,dict) or set(item)!=set(fields) for item in value):
                bad('/'+section,'must be a bounded list of complete exported records')
        if errors:
            return errors
        # Reject wrong primitive/container types before deeper checks or SQL.
        # Invalid editable data remains saveable as a draft and is reported as
        # validation errors, rather than raising incidental TypeError/DB errors.
        arrays={'input_entities_json','output_entities_json','allowed_write_json','forbidden_actions_json',
                'permitted_risk_levels_json','completion_conditions_json','allowed_terminal_states_json',
                'approval_points_json','approver_roles_json','dependencies','tools'}
        objects={'responsibility_json','config_json','policy_json','schema_json','input_contract','output_contract'}
        numbers={'sequence_no','retry_limit','max_output_tokens','max_parallel_tools','max_retries_per_tool',
                 'max_tool_calls','max_model_calls','max_runtime_seconds','no_new_finding_rounds',
                 'minimum_evidence_count','retain_days','max_iterations','no_progress_limit'}
        records=[('/'+section,document[section]) for section in SECTIONS]
        records.extend(('/'+section+'/'+str(index),item) for section in ('steps','bindings','loops','validationRules')
                       for index,item in enumerate(document[section]))
        for path,record in records:
            for field,value in record.items():
                if field=='parallel_group' and value is None: continue
                expected=list if field in arrays else dict if field in objects else bool if field in BOOL_FIELDS else \
                         int if field in numbers else (int,float) if field=='temperature' else str
                if type(value) not in (expected if isinstance(expected,tuple) else (expected,)):
                    bad(path+'/'+field,'has an invalid field type')
        if errors:
            return errors
        for section,(_,fields) in SECTIONS.items():
            for field in fields:
                if field in BOOL_FIELDS and type(document[section][field]) is not bool:
                    bad('/'+section+'/'+field,'must be boolean')
        definition=document['definition']
        for key in ('display_name','description','domain','current_version','system_prompt'):
            text(definition[key],'/definition/'+key,50000 if key=='system_prompt' else 4000)
        if definition['status'] not in {'draft','testing','published','deprecated','retired','disabled','active'}:
            bad('/definition/status','unsupported Agent status')
        if ConfigPublicationService._harness_installed(cursor):
            config=(document.get('models') or {}).get('config_json') or {}
            layer=config.get('agentLayer','application') if isinstance(config,dict) else 'application'
            runtime=config.get('runtimeKind') if isinstance(config,dict) else None
            runtime=runtime or ('deterministic_action' if layer=='action' else 'llm_loop')
            if layer not in {'application','action'}:
                bad('/models/config_json/agentLayer','must be application or action')
            if runtime not in {'llm_loop','deterministic_action'}:
                bad('/models/config_json/runtimeKind','must be llm_loop or deterministic_action')
            cursor.execute('SELECT agent_layer,runtime_kind FROM agent_definition WHERE agent_key=%s',(agent_key,))
            current=cursor.fetchone()
            if current and current.get('agent_layer')=='action' and layer=='application':
                bad('/models/config_json/agentLayer','an Action Agent cannot be promoted to application in place')
            if layer=='action' and runtime!='deterministic_action':
                bad('/models/config_json/runtimeKind','Action Agents require deterministic_action runtime')
        for key in ('input_entities_json','output_entities_json','allowed_write_json','forbidden_actions_json'):
            strings(document['scope'][key],'/scope/'+key)
        if not isinstance(document['scope']['responsibility_json'],dict):
            bad('/scope/responsibility_json','must be an object')
        model=document['models']
        if model['model_provider']!='deepseek' or model['model_name']!='deepseek-v4-pro':
            bad('/models','this installation requires deepseek / deepseek-v4-pro')
        if type(model['temperature']) not in (int,float) or not math.isfinite(model['temperature']) or not 0<=model['temperature']<=2:
            bad('/models/temperature','must be finite and between 0 and 2')
        integer(model['max_output_tokens'],'/models/max_output_tokens',1,8192)
        text(model['prompt_version'],'/models/prompt_version',128)
        if model['structured_output_required'] is not True:
            bad('/models/structured_output_required','structured output is required')
        config=model['config_json']
        if not isinstance(config,dict):
            bad('/models/config_json','must be an object')
            config={}
        if config.get('researchModeAvailable') is not True:
            bad('/models/config_json/researchModeAvailable','research mode must remain available')
        for field,default in (('ontologySourceKey','uav-ontology'),('webProviderKey','firecrawl')):
            if not isinstance(config.get(field,default),str) or not KEY.fullmatch(config.get(field,default)):
                bad('/models/config_json/'+field,'must reference an existing registered key')
        if any(key in config for key in ('providerSnapshot','engineMode','ontologySource','firecrawlProvider')):
            bad('/models/config_json','runtime snapshots are server-controlled')
        if 'retryLimit' in config:
            integer(config['retryLimit'],'/models/config_json/retryLimit',0,1)
        budgets=config.get('budgetLimits')
        if not isinstance(budgets,dict) or set(budgets)!=COUNTERS:
            bad('/models/config_json/budgetLimits','all seven budget resources must be explicit')
            budgets={}
        for key,value in budgets.items():
            integer(value,'/models/config_json/budgetLimits/'+key,0,2**63-1)
        if errors:
            return errors
        if budgets and (budgets.get('model_calls',0)<1 or budgets.get('tool_calls',0)<1 or budgets.get('http_calls',0)<1):
            bad('/models/config_json/budgetLimits','model, tool and HTTP budgets must allow at least one call')
        if errors:
            return errors
        resources=document['resources']
        integer(resources['max_parallel_tools'],'/resources/max_parallel_tools',1,32)
        integer(resources['max_retries_per_tool'],'/resources/max_retries_per_tool',0,10)
        if strings(resources['permitted_risk_levels_json'],'/resources/permitted_risk_levels_json') and \
                (not resources['permitted_risk_levels_json'] or set(resources['permitted_risk_levels_json'])-{'low','medium','high','critical'}):
            bad('/resources/permitted_risk_levels_json','must contain supported risk levels')
        convergence=document['convergence']
        strings(convergence['completion_conditions_json'],'/convergence/completion_conditions_json')
        if strings(convergence['allowed_terminal_states_json'],'/convergence/allowed_terminal_states_json') and \
                (not convergence['allowed_terminal_states_json'] or set(convergence['allowed_terminal_states_json'])-TERMINAL):
            bad('/convergence/allowed_terminal_states_json','must contain supported terminal states')
        for key in ('max_tool_calls','max_model_calls','max_runtime_seconds','no_new_finding_rounds'):
            integer(convergence[key],'/convergence/'+key,1)
        for field in ('schema_json',):
            schema(document['output'][field],'/output/'+field)
        if not isinstance(document['output']['schema_json'],dict) or not document['output']['schema_json'].get('required'):
            bad('/output/schema_json','an output contract must require its result fields')
        for key in ('required_evidence','required_assumptions','required_confidence'):
            if document['output'][key] is not True:
                bad('/output/'+key,'evidence, assumptions and confidence declarations remain required')
        evidence=document['evidence']
        deterministic_action=config.get('agentLayer')=='action' and config.get('actionExecutor')=='deterministic-v1'
        integer(evidence['minimum_evidence_count'],'/evidence/minimum_evidence_count',0 if deterministic_action else 1,1000)
        if evidence['conflict_action'] not in {'retain-and-escalate','reject'}:
            bad('/evidence/conflict_action','conflicts must be retained/escalated or rejected, never auto-preferred')
        for key in ('require_source_uri','inference_disclosure_required'):
            if evidence[key] is not True:
                bad('/evidence/'+key,'source attribution and disclosed inference are mandatory')
        for section in ('evidence','observability'):
            if not isinstance(document[section]['policy_json'],dict):
                bad('/'+section+'/policy_json','must be an object')
        integer(document['observability']['retain_days'],'/observability/retain_days',1,36500)
        if document['observability']['trace_enabled'] is not True:
            bad('/observability/trace_enabled','audit tracing is required')
        for key in ('approval_points_json','approver_roles_json'):
            strings(document['approval'][key],'/approval/'+key)
        steps=document['steps']
        if not steps and not config.get('researchModeAvailable'):
            bad('/steps','at least one executable step or a bounded research runtime is required')
        keys,orders={},set()
        for index,step in enumerate(steps):
            path='/steps/'+str(index)
            if not isinstance(step['step_key'],str) or not KEY.fullmatch(step['step_key']):
                bad(path+'/step_key','must be a valid step key')
            elif step['step_key'] in keys:
                bad(path+'/step_key','duplicate step key')
            else: keys[step['step_key']]=step
            if integer(step['sequence_no'],path+'/sequence_no',1) and step['sequence_no'] in orders:
                bad(path+'/sequence_no','sequence numbers must be unique')
            if type(step['sequence_no']) is int: orders.add(step['sequence_no'])
            text(step['name'],path+'/name',200)
            text(step['description'],path+'/description',4000,True)
            if type(step['required']) is not bool: bad(path+'/required','must be boolean')
            integer(step['retry_limit'],path+'/retry_limit',0,10)
            if step['parallel_group'] is not None: text(step['parallel_group'],path+'/parallel_group',128)
            schema(step['input_contract'],path+'/input_contract')
            schema(step['output_contract'],path+'/output_contract')
            strings(step['dependencies'],path+'/dependencies',100)
            strings(step['tools'],path+'/tools',200)
        graph={}
        for index,step in enumerate(steps):
            if not isinstance(step['step_key'],str): continue
            graph[step['step_key']]=step['dependencies'] if isinstance(step['dependencies'],list) else []
            for dep in graph[step['step_key']]:
                if not isinstance(dep,str) or dep not in keys:
                    bad('/steps/'+str(index)+'/dependencies','dependency does not identify an existing step')
        visiting,visited=set(),set()
        def visit(key):
            if key in visiting: return False
            if key in visited: return True
            visiting.add(key)
            for dep in graph.get(key,[]):
                if isinstance(dep,str) and dep in graph and not visit(dep): return False
            visiting.remove(key);visited.add(key)
            return True
        if any(not visit(key) for key in graph): bad('/steps','step dependency graph contains a cycle')
        bound={}
        for index,binding in enumerate(document['bindings']):
            path='/bindings/'+str(index)
            tool_key=binding['tool_key']
            if not isinstance(tool_key,str) or not KEY.fullmatch(tool_key):
                bad(path+'/tool_key','invalid tool key');continue
            if tool_key in bound: bad(path+'/tool_key','duplicate tool binding')
            bound[tool_key]=binding
            if not isinstance(binding['tool_version'],str) or not KEY.fullmatch(binding['tool_version']):
                bad(path+'/tool_version','invalid tool version');continue
            for flag in ('enabled','approval_required'):
                if type(binding[flag]) is not bool: bad(path+'/'+flag,'must be boolean')
            if not isinstance(binding['config_json'],dict): bad(path+'/config_json','must be an object')
            else:
                for field,minimum,maximum in (('retryLimit',0,10),('timeoutSeconds',1,300)):
                    if field in binding['config_json']: integer(binding['config_json'][field],path+'/config_json/'+field,minimum,maximum)
            cursor.execute("SELECT t.status,t.access_mode,t.risk_level,t.protocol,v.endpoint, "
                           "to_jsonb(v)->>'execution_transport' AS execution_transport, "
                           'v.status AS version_status,v.input_schema,v.output_schema '
                           'FROM tool_registry t JOIN tool_version v USING(tool_key) WHERE t.tool_key=%s AND v.version=%s',
                           (tool_key,binding['tool_version']))
            tool=cursor.fetchone()
            if tool is None:
                bad(path,'binding requires a registered immutable tool version');continue
            if binding['enabled'] is True:
                if tool['status'] in {'disabled','inactive','retired'} or tool['version_status'] in {'disabled','inactive','retired'}:
                    bad(path,'enabled binding refers to a disabled tool/version')
                if tool['risk_level'] not in resources['permitted_risk_levels_json']:
                    bad(path,'enabled tool risk is outside the permitted resource policy')
                if tool['access_mode']!='read_only' and binding['approval_required'] is not True:
                    bad(path,'non-read-only tools require explicit human approval')
                schema(tool['input_schema'],path+'/registered_input_schema')
                schema(tool['output_schema'],path+'/registered_output_schema')
                from .connector_validation import validate_tool_connection
                try:
                    validate_tool_connection(tool_key, tool, tool, binding['config_json'])
                except ValueError as exc:
                    bad(path+'/connection', str(exc))
                if tool_key == 'document.read' and binding['config_json'].get('sourceKey') not in model['config_json'].get('documentSourceKeys', []):
                    bad(path+'/connection', 'document tool requires an explicitly authorized document source')
        for index,step in enumerate(steps):
            if not isinstance(step['tools'],list): continue
            for key in step['tools']:
                if not isinstance(key,str) or key not in bound or bound[key]['enabled'] is not True:
                    bad('/steps/'+str(index)+'/tools','step requires an enabled registered binding')
        for section,idfield,fields in (('loops','loop_id',('trigger_condition','stop_action')),
                                      ('validationRules','rule_id',('rule_type','expression','severity'))):
            seen=set()
            for index,item in enumerate(document[section]):
                path='/'+section+'/'+str(index)
                if not isinstance(item[idfield],str) or not KEY.fullmatch(item[idfield]):
                    bad(path+'/'+idfield,'a unique stable ID is required')
                elif item[idfield] in seen: bad(path+'/'+idfield,'duplicate ID')
                else: seen.add(item[idfield])
                for field in fields: text(item[field],path+'/'+field)
                if section=='loops':
                    if item['return_step_key'] not in keys: bad(path+'/return_step_key','loop target must identify a step')
                    for field in ('max_iterations','no_progress_limit'): integer(item[field],path+'/'+field,1,100)
                    if item['stop_action'] not in TERMINAL: bad(path+'/stop_action','unsupported terminal action')
                elif item['severity'] not in {'warning','error','blocking'}: bad(path+'/severity','unsupported rule severity')
        if errors:
            return errors
        dependencies=ConfigPublicationService._dependencies(cursor,document,agent_key)
        document_keys = model['config_json'].get('documentSourceKeys', [])
        if not isinstance(document_keys, list) or len(document_keys) > 100 or any(not isinstance(key, str) or not KEY.fullmatch(key) for key in document_keys):
            bad('/models/config_json/documentSourceKeys', 'document sources must be registered keys')
        for item in dependencies.get('documentSources', []):
            registered = item['registry']
            if not registered or not registered['enabled'] or not registered['read_only'] or registered['connector'] != 'files':
                bad('/models/config_json/documentSourceKeys', 'an enabled read-only registered file source is required')
        provider=next((item['registry'] for item in dependencies['providers'] if item['providerKey']=='deepseek'),None)
        if not provider or not provider['enabled'] or provider['provider_type']!='model' or provider['base_url']!='https://api.deepseek.com':
            bad('/models/model_provider','enabled official DeepSeek provider is required')
        elif not isinstance(provider['credential_ref'],str) or not re.fullmatch(r'env:[A-Za-z_][A-Za-z0-9_]{0,127}',provider['credential_ref']):
            bad('/models/model_provider','provider must use a server credential reference')
        source=dependencies['sources'][0]['registry']
        if not source or not source['enabled'] or source['read_only'] is not True or source['connector']!='sqlite' or \
                not source['root_uri'].startswith('file:'):
            bad('/models/config_json/ontologySourceKey','an enabled registered read-only SQLite source is required')
        if any(key.startswith('web.') and item['enabled'] is True for key,item in bound.items()):
            web=next((item['registry'] for item in dependencies['providers'] if item['providerKey']==config.get('webProviderKey','firecrawl')),None)
            if not web or not web['enabled'] or web['provider_type']!='web' or web['base_url']!='https://api.firecrawl.dev/v2':
                bad('/models/config_json/webProviderKey','enabled registered Firecrawl provider is required')
            policy=config.get('sourcePolicy')
            if not isinstance(policy,dict): bad('/models/config_json/sourcePolicy','bounded source policy is required')
            else:
                domains=policy.get('allowedDomains')
                if not strings(domains,'/models/config_json/sourcePolicy/allowedDomains',100) or not domains:
                    bad('/models/config_json/sourcePolicy/allowedDomains','at least one allowed domain is required')
                elif any(not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?',domain) or '.' not in domain for domain in domains):
                    bad('/models/config_json/sourcePolicy/allowedDomains','domains cannot contain URLs, paths or wildcards')
                pages=policy.get('maxPages',8)
                if integer(pages,'/models/config_json/sourcePolicy/maxPages',1,100) and type(budgets.get('scrape_calls')) is int and budgets['scrape_calls']>pages:
                    bad('/models/config_json/budgetLimits/scrape_calls','scrape budget exceeds source page limit')
        capability_path = config.get('capabilityGraphPath')
        if agent_key == 'future-battlefield-analyst' and not capability_path:
            bad('/models/config_json/capabilityGraphPath', 'this Agent requires its capability graph')
        if capability_path is not None:
            ConfigPublicationService._validate_capability_graph(cursor, document, capability_path, bad, agent_key)
        if not errors:
            config_policy = config.get('executionBaseline')
            if config_policy is not None and (not isinstance(config_policy,dict) or set(config_policy)-{'nodePolicies'}):
                bad('/models/config_json/executionBaseline','must contain only nodePolicies')
            elif config_policy is not None and (not isinstance(config_policy.get('nodePolicies'),dict) or
                                                 set(config_policy['nodePolicies'])-(set(keys) or {'research-envelope'})):
                bad('/models/config_json/executionBaseline/nodePolicies','policies must identify existing nodes')
            else:
                try:
                    baseline = build_baseline(agent_key, document,
                        ConfigPublicationService._baseline_harness(cursor,agent_key))
                    for error in validate_baseline(baseline, {key for key,item in bound.items() if item['enabled']}):
                        bad('/executionBaseline'+error['path'],error['message'])
                    from agent_engine.uav_execution_contracts import publication_issues
                    for issue in publication_issues(agent_key, document, baseline):
                        bad('/executionBaseline/' + issue.get('nodeKey', 'role-contract'), issue['message'])
                except (TypeError, ValueError, KeyError, AttributeError):
                    bad('/models/config_json/executionBaseline','invalid execution baseline node policies')
        return errors

    @staticmethod
    def _validate_capability_graph(cursor, document, capability_path, bad, agent_key=None):
        """Check reviewed capability metadata against the executable Profile."""
        try:
            snapshot = graph_snapshot(cursor, document.get('models', {}).get('config_json', {}), agent_key)
            tool_schemas=[]
            for binding in document['bindings']:
                if binding['enabled']:
                    cursor.execute('SELECT input_schema FROM tool_version WHERE tool_key=%s AND version=%s',
                                   (binding['tool_key'],binding['tool_version']))
                    row=cursor.fetchone()
                    if row: tool_schemas.append(row['input_schema'])
            errors = graph_errors(snapshot, document, tool_schemas)
            for error in errors:
                bad(error['path'], error['message'])
        except (OSError, ValueError, TypeError, KeyError, SchemaValidationError):
            bad('/models/config_json/capabilityGraphPath', 'capability graph file is unavailable or invalid')

    @staticmethod
    def _write(cursor,table,agent_key,record,key_column='agent_key',key_value=None):
        key_value=agent_key if key_value is None else key_value
        columns=list(record)
        values=[Json(record[key]) if key in JSON_FIELDS or key=='dependencies_json' else
                int(record[key]) if key in BOOL_FIELDS else record[key] for key in columns]
        cursor.execute(sql.SQL('INSERT INTO {}({}) VALUES({}) ON CONFLICT({}) DO UPDATE SET {}').format(
            sql.Identifier(table),sql.SQL(',').join(map(sql.Identifier,[key_column,*columns])),
            sql.SQL(',').join(sql.Placeholder() for _ in range(len(columns)+1)),sql.Identifier(key_column),
            sql.SQL(',').join(sql.SQL('{}=EXCLUDED.{}').format(sql.Identifier(key),sql.Identifier(key)) for key in columns)),
            (key_value,*values))

    @classmethod
    def _apply(cls,cursor,agent_key,document):
        for section,(table,fields) in SECTIONS.items():
            cls._write(cursor,table,agent_key,document[section])
        # 053 promotes the authoring Profile's layer/runtime metadata to the
        # database authority. Older fixtures do not have these columns.
        if cls._harness_installed(cursor):
            config = document.get('models', {}).get('config_json', {})
            layer = config.get('agentLayer', 'application') if isinstance(config, dict) else 'application'
            runtime = config.get('runtimeKind') if isinstance(config, dict) else None
            runtime = runtime or ('deterministic_action' if layer == 'action' else 'llm_loop')
            cursor.execute('UPDATE agent_definition SET agent_layer=%s,runtime_kind=%s WHERE agent_key=%s',
                           (layer, runtime, agent_key))
        cursor.execute('SELECT step_id,step_key FROM agent_step WHERE agent_key=%s',(agent_key,))
        previous={row['step_key']:row['step_id'] for row in cursor.fetchall()}
        cursor.execute('DELETE FROM agent_step_tool WHERE step_id IN (SELECT step_id FROM agent_step WHERE agent_key=%s)',(agent_key,))
        # Existing step IDs are stable; new keys receive server-generated IDs.
        keys=[step['step_key'] for step in document['steps']]
        cursor.execute('DELETE FROM agent_step WHERE agent_key=%s AND NOT(step_key=ANY(%s))',(agent_key,keys))
        for step in document['steps']:
            record={key:value for key,value in step.items() if key not in {'tools','dependencies'}}
            record.update(agent_key=agent_key,dependencies_json=step['dependencies'])
            step_id=previous.get(step['step_key']) or new_id('step')
            cls._write(cursor,'agent_step',agent_key,record,'step_id',step_id)
            for tool_key in step['tools']:
                cursor.execute('INSERT INTO agent_step_tool(step_id,tool_key) VALUES(%s,%s)',(step_id,tool_key))
        cursor.execute('DELETE FROM agent_tool_binding WHERE agent_key=%s',(agent_key,))
        for binding in document['bindings']:
            cursor.execute('INSERT INTO agent_tool_binding(agent_key,tool_key,tool_version,enabled,approval_required,config_json) '
                           'VALUES(%s,%s,%s,%s,%s,%s)',(agent_key,binding['tool_key'],binding['tool_version'],
                           int(binding['enabled']),int(binding['approval_required']),Json(binding['config_json'])))
        for section,table,idfield in (('loops','agent_loop_rule','loop_id'),('validationRules','agent_validation_rule','rule_id')):
            ids=[item[idfield] for item in document[section]]
            if section=='loops':
                cursor.execute('SELECT 1 FROM agent_loop_run h JOIN agent_loop_rule r USING(loop_id) '
                               'WHERE r.agent_key=%s AND NOT(r.loop_id=ANY(%s)) LIMIT 1',(agent_key,ids))
                if cursor.fetchone():
                    raise ConfigConflictError('a loop with historical executions must be retained under its stable ID')
            cursor.execute(sql.SQL('DELETE FROM {} WHERE agent_key=%s AND NOT({}=ANY(%s))').format(sql.Identifier(table),sql.Identifier(idfield)),
                           (agent_key,ids))
            for item in document[section]:
                cursor.execute(sql.SQL('SELECT agent_key FROM {} WHERE {}=%s').format(sql.Identifier(table),sql.Identifier(idfield)),(item[idfield],))
                old=cursor.fetchone()
                if old and old['agent_key']!=agent_key:
                    raise ConfigConflictError('policy record ID belongs to another Agent')
                cls._write(cursor,table,agent_key,{**{key:value for key,value in item.items() if key!=idfield},'agent_key':agent_key},idfield,item[idfield])

    @staticmethod
    def _harness_installed(cursor):
        cursor.execute("SELECT to_regclass(current_schema() || '.agent_harness_contract') AS table_name")
        row=cursor.fetchone()
        return bool(row and (row.get('table_name') if isinstance(row, dict) else row[0]))

    @classmethod
    def _harness_metadata(cls, cursor, agent_key, document):
        """Resolve and validate the explicit runtime boundary for a Profile."""
        config=(document.get('models') or {}).get('config_json') or {}
        requested_layer=config.get('agentLayer', 'application') if isinstance(config,dict) else 'application'
        requested_runtime=config.get('runtimeKind') if isinstance(config,dict) else None
        requested_runtime=requested_runtime or ('deterministic_action' if requested_layer=='action' else 'llm_loop')
        cursor.execute('SELECT agent_layer,runtime_kind FROM agent_definition WHERE agent_key=%s',(agent_key,))
        row=cursor.fetchone() or {}
        return requested_layer, requested_runtime, row

    @classmethod
    def _create_harness_contract(cls, cursor, agent_key, document, previous_version=None):
        """Publish an immutable Harness authority for the newly applied Profile."""
        if not cls._harness_installed(cursor):
            return None
        layer, runtime, _ = cls._harness_metadata(cursor, agent_key, document)
        profile_version=document['definition']['current_version']
        from agent_engine.repository import PostgresRepository
        prior_harness=PostgresRepository(None)._load_harness_contract(
            cursor,agent_key,previous_version or profile_version)[2]
        cursor.execute('''SELECT contract_version FROM agent_harness_contract
                          WHERE agent_key=%s AND profile_version=%s
                          ORDER BY created_at DESC,harness_contract_id DESC LIMIT 1''',
                       (agent_key,profile_version))
        previous_contract=cursor.fetchone()
        version='1.0.0'
        if previous_contract and isinstance(previous_contract.get('contract_version'),str):
            parts=previous_contract['contract_version'].split('.')
            if len(parts)==3 and all(part.isdigit() for part in parts):
                version=f'{int(parts[0])}.{int(parts[1])}.{int(parts[2])+1}'
        config=(document.get('models') or {}).get('config_json') or {}
        budget=dict(config.get('budgetLimits') or {}) if isinstance(config,dict) else {}
        steps=document.get('steps') or []
        if not steps and config.get('researchModeAvailable'):
            steps=[{'step_key':'research-envelope'}]
        # Distribute the Agent ceiling across nodes. The durable Run budget
        # remains the aggregate guard; these are per-node hard ceilings.
        count=max(len(steps),1)
        def node_budget(key):
            total=int(budget.get(key,0) or 0)
            return min(total, max(1, (total + count - 1)//count)) if total else 0
        max_runtime=int((document.get('convergence') or {}).get('max_runtime_seconds',300) or 300)
        max_parallel=max(1,int((document.get('resources') or {}).get('max_parallel_tools',1) or 1))
        max_depth=1 if layer=='application' else 0
        contract={
            'schemaVersion':'harness.v1','agentKey':agent_key,'profileVersion':profile_version,
            'contractVersion':version,'agentLayer':layer,'runtimeKind':runtime,
            'invocationMode':'task','defaultDeny':True,'maxChildDepth':max_depth,
            'lifecyclePolicy':{'allowPause':True,'allowResume':True,'allowCancel':True,'allowHumanTakeover':True},
            'failurePolicy':{'retryable':['timeout','provider_unavailable'],'escalateAfterAttempts':2},
            'memoryPolicy':{'workingMemory':'run','crossRun':False,'retentionDays':30},
            'sideEffectPolicy':{'default':'deny','allowExternalWrite':False,'requireIdempotencyKey':True},
        }
        contract_hash=_hash(contract)
        contract_id=new_id('harness')
        cursor.execute('''INSERT INTO agent_harness_contract(
            harness_contract_id,agent_key,profile_version,contract_version,agent_layer,runtime_kind,
            invocation_mode,default_deny,max_child_depth,lifecycle_policy_json,failure_policy_json,
            memory_policy_json,side_effect_policy_json,contract_json,contract_hash,status,published_at)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'published',now())''',
            (contract_id,agent_key,profile_version,version,layer,runtime,contract['invocationMode'],True,max_depth,
             Json(contract['lifecyclePolicy']),Json(contract['failurePolicy']),Json(contract['memoryPolicy']),
             Json(contract['sideEffectPolicy']),Json(contract),contract_hash))

        cursor.execute('''SELECT b.tool_key,b.tool_version,b.approval_required,b.config_json,
                                 t.access_mode,t.risk_level
                            FROM agent_tool_binding b JOIN tool_registry t USING(tool_key)
                           WHERE b.agent_key=%s AND b.enabled<>0 ORDER BY b.tool_key''',(agent_key,))
        tool_grants=[]
        for row in cursor.fetchall():
            permission='write' if row['access_mode']=='write' else 'invoke'
            approval=bool(row['approval_required']) or row['access_mode']!='read_only' or row['risk_level'] in {'high','critical'}
            tool_grants.append({'toolKey':row['tool_key'],'toolVersion':row['tool_version'],
                                'permission':permission,'approvalRequired':approval})
            cursor.execute('''INSERT INTO agent_harness_tool_grant(
                harness_contract_id,tool_key,tool_version,permission,approval_required,grant_config_json)
                VALUES(%s,%s,%s,%s,%s,%s)''',
                (contract_id,row['tool_key'],row['tool_version'],permission,approval,Json(row['config_json'] or {})))

        source_key=config.get('ontologySourceKey','uav-ontology') if isinstance(config,dict) else 'uav-ontology'
        cursor.execute('''INSERT INTO agent_harness_data_grant(
            grant_id,harness_contract_id,resource_type,resource_pattern,access_mode,sensitivity_level)
            VALUES(%s,%s,'source',%s,'read','public')
            ON CONFLICT(harness_contract_id,resource_type,resource_pattern,access_mode) DO NOTHING''',
            (new_id('data-grant'),contract_id,source_key))
        domains=((config.get('sourcePolicy') or {}).get('allowedDomains') or []) if isinstance(config,dict) else []
        for domain in sorted(set(domains)):
            cursor.execute('''INSERT INTO agent_harness_network_grant(
                network_grant_id,harness_contract_id,domain_pattern,methods_json)
                VALUES(%s,%s,%s,'["GET"]'::jsonb)
                ON CONFLICT(harness_contract_id,domain_pattern) DO NOTHING''',
                (new_id('net-grant'),contract_id,domain))

        node_budgets=[]
        prior_budgets = {row['step_key']:row for row in prior_harness.get('stepBudgets',[])}
        node_policies = (config.get('executionBaseline') or {}).get('nodePolicies') or {}
        for step in steps:
            key=step['step_key']
            configured = node_policies.get(key,{}).get('budget') or {}
            old = prior_budgets.get(key,{})
            def ceiling(field, fallback):
                value=configured.get(field,old.get(field,fallback))
                if field in budget:
                    value=min(value,int(budget[field]))
                return value
            values=(contract_id,key,ceiling('max_rounds',1),
                    *(ceiling(field,node_budget(field)) for field in
                      ('model_calls','tool_calls','http_calls','search_calls','scrape_calls','input_tokens','output_tokens')),
                    ceiling('max_parallelism',max_parallel),ceiling('max_runtime_seconds',max_runtime))
            cursor.execute('''INSERT INTO agent_harness_step_budget(
                harness_contract_id,step_key,max_rounds,model_calls,tool_calls,http_calls,search_calls,
                scrape_calls,input_tokens,output_tokens,max_parallelism,max_runtime_seconds)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''',values)
            node_budgets.append({'stepKey':key,'modelCalls':values[3],'toolCalls':values[4],
                                 'httpCalls':values[5],'searchCalls':values[6],'scrapeCalls':values[7],
                                 'inputTokens':values[8],'outputTokens':values[9]})

        # Composition edges are append-only too. Clone the prior graph under the
        # newly published parent version, so old Runs retain their exact DAG.
        if previous_version and previous_version != profile_version:
            cursor.execute('''SELECT parent_agent_key,child_agent_key,child_profile_version,
                                     handoff_schema_json,allowed_artifact_types_json,budget_mode,max_depth,approval_required
                                FROM agent_composition_edge
                               WHERE parent_agent_key=%s AND parent_profile_version=%s AND enabled=true''',
                           (agent_key,previous_version))
            for edge in cursor.fetchall():
                cursor.execute('''INSERT INTO agent_composition_edge(
                    edge_id,parent_agent_key,child_agent_key,parent_profile_version,child_profile_version,
                    handoff_schema_json,allowed_artifact_types_json,budget_mode,max_depth,approval_required)
                    VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT(parent_agent_key,child_agent_key,parent_profile_version) DO NOTHING''',
                    (new_id('edge'),edge['parent_agent_key'],edge['child_agent_key'],profile_version,
                     edge['child_profile_version'],edge['handoff_schema_json'],edge['allowed_artifact_types_json'],
                     edge['budget_mode'],edge['max_depth'],edge['approval_required']))
        cursor.execute('UPDATE agent_definition SET default_harness_contract_id=%s WHERE agent_key=%s',
                       (contract_id,agent_key))
        return {'harnessContractId':contract_id,'contractVersion':version,'contractHash':contract_hash,
                'toolGrants':tool_grants,'stepBudgets':node_budgets}

    @staticmethod
    def _publication(row):
        return {'publicationId':row['publication_id'],'publicationNo':row['publication_no'],
                'agentKey':row['agent_key'],'draftId':row['draft_id'],'revision':row['revision'],
                'baseLiveHash':row['base_live_hash'],'liveHash':row['live_hash'],'document':row['document_json'],
                'dependencies':row['dependency_snapshot_json'],'diff':row['diff_json'],'actorId':row['actor_id'],
                'reason':row['reason'],'publishedAt':row['published_at']}

    def list_publications(self,actor,agent_key,*,actor_token,limit=100):
        _key(agent_key,'Agent key')
        if type(limit) is not int or not 1 <= limit <= 200:
            raise ValueError('invalid limit')
        with self.connection_factory() as con:
            with con.cursor() as cursor:
                self._authorize(cursor,actor,actor_token)
                cursor.execute('SELECT * FROM agent_config_publication WHERE agent_key=%s ORDER BY publication_no DESC LIMIT %s',
                               (agent_key,limit))
                return [self._publication(row) for row in cursor.fetchall()]

    def get_publication(self,actor,publication_id,*,actor_token):
        _key(publication_id,'publication ID')
        with self.connection_factory() as con:
            with con.cursor() as cursor:
                self._authorize(cursor,actor,actor_token)
                cursor.execute('SELECT * FROM agent_config_publication WHERE publication_id=%s',(publication_id,))
                row=cursor.fetchone()
                if row is None:
                    raise ConfigNotFoundError('publication not found')
                return self._publication(row)

    def publish_draft(self,actor,draft_id,*,actor_token,expected_revision,idempotency_key,reason):
        if type(expected_revision) is not int or expected_revision < 1:
            raise ValueError('invalid expected revision')
        if not isinstance(idempotency_key,str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,160}',idempotency_key):
            raise ValueError('invalid publication operation key')
        reason=_reason(reason)
        with self.connection_factory() as con:
            with con.cursor() as cursor:
                self._authorize(cursor,actor,actor_token)
                self._config_lock(cursor,True)
                draft=self._draft(cursor,draft_id,lock=True)
                cursor.execute('SELECT * FROM agent_config_publication WHERE agent_key=%s AND idempotency_key=%s',
                               (draft['agent_key'],idempotency_key))
                existing=cursor.fetchone()
                if existing:
                    if (existing['draft_id'],existing['revision'],existing['actor_id'],existing['reason']) != \
                            (draft_id,expected_revision,actor.principal_id,reason):
                        raise ConfigConflictError('operation key already identifies a different publication')
                    return self._publication(existing)
                if draft['current_revision']!=expected_revision:
                    raise ConfigConflictError('draft changed; reload its current revision')
                cursor.execute('SELECT 1 FROM agent_config_publication WHERE draft_id=%s AND revision=%s',(draft_id,expected_revision))
                if cursor.fetchone():
                    raise ConfigConflictError('this draft revision has already been published')
                live,dependencies,live_hash=self._live(cursor,draft['agent_key'])
                if live_hash!=draft['base_live_hash']:
                    raise ConfigConflictError('live configuration changed; create a fresh draft and review its differences')
                if self._dependencies(cursor,draft['document_json'],draft['agent_key'])!=draft['dependency_snapshot_json']:
                    raise ConfigConflictError('a selected tool/provider/source changed; save a new revision and review it')
                errors=self._validate(cursor,draft['document_json'],draft['agent_key'])
                if errors:
                    raise ConfigValidationError(errors)
                from agent_engine.research_governance import publication_gate
                publication_gate(cursor,draft)
                from agent_engine.skill_release import validate_release
                method_migration=validate_release(cursor,live,draft)
                previous_version=(live.get('definition') or {}).get('current_version')
                self._apply(cursor,draft['agent_key'],draft['document_json'])
                from agent_engine.skills import carry_forward_bindings
                if not method_migration: carry_forward_bindings(cursor,draft['agent_key'],previous_version,
                    draft['document_json']['definition']['current_version'],
                    {step['step_key'] for step in draft['document_json']['steps']},
                    'config-publication:'+actor.principal_id)
                harness_publication=self._create_harness_contract(
                    cursor,draft['agent_key'],draft['document_json'],previous_version=previous_version)
                baseline_publication=None
                if baseline_installed(cursor):
                    graph=build_baseline(draft['agent_key'],draft['document_json'],
                                         self._baseline_harness(cursor,draft['agent_key']))
                    baseline_publication=register_baseline(cursor,draft['agent_key'],graph,status='published',
                                                          created_by=actor.principal_id)
                published,published_dependencies,published_hash=self._live(cursor,draft['agent_key'])
                if published!=draft['document_json']:
                    raise ConfigConflictError('published profile differs from the reviewed document')
                cursor.execute('SELECT COALESCE(max(publication_no),0)+1 AS next FROM agent_config_publication WHERE agent_key=%s',
                               (draft['agent_key'],))
                sequence=cursor.fetchone()['next']
                cursor.execute('INSERT INTO agent_config_publication(publication_id,agent_key,publication_no,draft_id,revision,'
                               'base_live_hash,live_hash,document_json,dependency_snapshot_json,diff_json,actor_id,idempotency_key,reason) '
                               'VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *',
                               (new_id('config-publication'),draft['agent_key'],sequence,draft_id,expected_revision,live_hash,
                                published_hash,Json(published),Json(published_dependencies),Json(_diff(live,published)),
                                actor.principal_id,idempotency_key,reason))
                result=self._publication(cursor.fetchone())
                if harness_publication:
                    result['harness']=harness_publication
                if baseline_publication:
                    result['executionBaseline']=baseline_publication
                return result
