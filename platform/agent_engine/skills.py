"""Frozen declarative methods. Skills never grant authority or execute scripts."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re

from jsonschema import Draft202012Validator, validate, ValidationError
from psycopg2.extras import Json

from .snapshot_contracts import validate_credential_references

SCHEMA = 'agent-skill.v1'
MANIFEST_SCHEMA = 'agent-skill-manifest.v1'
IDENTITY = re.compile(r'[a-z][a-z0-9-]{0,99}')
VERSION = re.compile(r'[0-9]+\.[0-9]+\.[0-9]+')


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,
        separators=(',', ':'),allow_nan=False).encode()).hexdigest()


def installed(cursor):
    cursor.execute("SELECT to_regclass(current_schema() || '.agent_skill_revision') AS name")
    row = cursor.fetchone()
    return bool(row and row['name'])


def validate_package(package):
    if not isinstance(package,dict) or set(package) != {'manifest','instructions','inputContract','outputContract'}:
        raise ValueError('invalid-skill-package')
    manifest = package['manifest']
    fields = {'schemaVersion','skillKey','version','name','description','applicability','validatorRefs'}
    if not isinstance(manifest,dict) or set(manifest) != fields or manifest['schemaVersion'] != SCHEMA:
        raise ValueError('invalid-skill-manifest')
    if not IDENTITY.fullmatch(str(manifest['skillKey'])) or not VERSION.fullmatch(str(manifest['version'])):
        raise ValueError('invalid-skill-identity')
    if any(not isinstance(manifest[k],str) or not manifest[k].strip() for k in ('name','description')):
        raise ValueError('invalid-skill-description')
    applicability = manifest['applicability']
    if not isinstance(applicability,dict) or set(applicability) != {'agentKeys','stepKeys','stages'}:
        raise ValueError('invalid-skill-applicability')
    for values in applicability.values():
        if not isinstance(values,list) or not values or any(not isinstance(v,str) or not v for v in values):
            raise ValueError('invalid-skill-applicability')
    instructions = package['instructions']
    if not isinstance(instructions,list) or not instructions or any(not isinstance(v,str) or not v.strip() for v in instructions):
        raise ValueError('invalid-skill-instructions')
    for key in ('inputContract','outputContract'):
        Draft202012Validator.check_schema(package[key])
        # Contracts must be self-contained; runtime never fetches a remote schema.
        def refs(value):
            if isinstance(value,dict):
                for k,v in value.items():
                    if k in {'$ref','$dynamicRef'} and (not isinstance(v,str) or not v.startswith('#')):
                        raise ValueError('external-skill-schema-reference')
                    refs(v)
            elif isinstance(value,list):
                for item in value: refs(item)
        refs(package[key])
    if len(json.dumps(package,ensure_ascii=False).encode()) > 64000:
        raise ValueError('skill-package-too-large')
    validate_credential_references(package)
    return package


def load_package(directory):
    directory = Path(directory)
    manifest = json.loads((directory / 'manifest.json').read_text(encoding='utf-8'))
    source = (directory / 'SKILL.md').read_text(encoding='utf-8')
    if source.startswith('---\n'):
        source = source.split('---\n',2)[2]
    package = {'manifest':manifest,'instructions':[v.strip() for v in source.strip().split('\n\n') if v.strip()],
        'inputContract':json.loads((directory / 'input-schema.json').read_text(encoding='utf-8')),
        'outputContract':json.loads((directory / 'output-schema.json').read_text(encoding='utf-8'))}
    return validate_package(package)


def reference(binding):
    return {key:deepcopy(binding[key]) for key in
        ('stepKey','stage','skillKey','version','contentHash')}


def validate_manifest(manifest,agent_key,profile_version):
    if not manifest:
        return {}
    if (not isinstance(manifest,dict) or set(manifest) != {'schemaVersion','agentKey','profileVersion','bindings','contentHash'}
            or manifest['schemaVersion'] != MANIFEST_SCHEMA or manifest['agentKey'] != agent_key
            or manifest['profileVersion'] != profile_version
            or digest({k:v for k,v in manifest.items() if k != 'contentHash'}) != manifest['contentHash']):
        raise ValueError('skill-snapshot-hash-mismatch')
    seen = set()
    for binding in manifest['bindings']:
        if set(binding) != {'stepKey','stage','skillKey','version','contentHash','package'}:
            raise ValueError('invalid-skill-binding')
        package = validate_package(binding['package'])
        definition = package['manifest']
        applicability = definition['applicability']
        identity = (binding['stepKey'],binding['stage'])
        if (identity in seen or agent_key not in applicability['agentKeys']
                or binding['stepKey'] not in applicability['stepKeys'] or binding['stage'] not in applicability['stages']
                or binding['skillKey'] != definition['skillKey'] or binding['version'] != definition['version']
                or digest(package) != binding['contentHash']):
            raise ValueError('skill-binding-mismatch')
        seen.add(identity)
    return manifest


def validate_baseline_bindings(manifest,baseline):
    expected = [reference(b) for b in (manifest or {}).get('bindings',[])]
    actual = []
    for node in (baseline or {}).get('graph',{}).get('nodes',[]):
        for binding in node.get('skillBindings',[]):
            if binding.get('stepKey') != node.get('id'):
                raise ValueError('skill-baseline-node-mismatch')
            actual.append(binding)
    if sorted(expected,key=digest) != sorted(actual,key=digest):
        raise ValueError('skill-baseline-binding-mismatch')


def current_manifest(cursor,agent_key,profile_version):
    if not installed(cursor): return {}
    cursor.execute('''SELECT b.*,r.package_json FROM agent_step_skill_binding b JOIN agent_skill_revision r
        ON (r.skill_key,r.version,r.content_hash)=(b.skill_key,b.skill_version,b.content_hash)
        WHERE b.agent_key=%s AND b.profile_version=%s ORDER BY b.step_key,b.stage''',(agent_key,profile_version))
    rows = cursor.fetchall()
    if not rows: return {}
    bindings = [{'stepKey':r['step_key'],'stage':r['stage'],'skillKey':r['skill_key'],
        'version':r['skill_version'],'contentHash':r['content_hash'],'package':r['package_json']} for r in rows]
    manifest = {'schemaVersion':MANIFEST_SCHEMA,'agentKey':agent_key,'profileVersion':profile_version,'bindings':bindings}
    manifest['contentHash'] = digest(manifest)
    return validate_manifest(manifest,agent_key,profile_version)


def publication_manifest(cursor,agent_key,profile_version):
    """An ordinary Profile edit carries forward its reviewed method versions."""
    target = current_manifest(cursor,agent_key,profile_version)
    if target or not installed(cursor): return target
    cursor.execute('SELECT current_version FROM agent_definition WHERE agent_key=%s',(agent_key,))
    row = cursor.fetchone()
    return current_manifest(cursor,agent_key,row['current_version']) if row else {}


def carry_forward_bindings(cursor,agent_key,source_version,target_version,step_keys,created_by):
    source = current_manifest(cursor,agent_key,source_version)
    if not source: return
    if any(b['stepKey'] not in step_keys for b in source['bindings']):
        raise ValueError('skill-bound-step-removal-requires-method-migration')
    target = current_manifest(cursor,agent_key,target_version)
    if target and [reference(b) for b in target['bindings']] != [reference(b) for b in source['bindings']]:
        raise ValueError('skill-binding-change-requires-method-migration')
    if source_version == target_version: return
    for binding in source['bindings']:
        cursor.execute('''INSERT INTO agent_step_skill_binding(agent_key,profile_version,step_key,stage,
            skill_key,skill_version,content_hash,created_by) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT DO NOTHING''',(agent_key,target_version,binding['stepKey'],binding['stage'],
            binding['skillKey'],binding['version'],binding['contentHash'],created_by))


def resolve(profile,step_key,stage):
    manifest = getattr(profile,'skill_manifest',{}) or {}
    if not manifest: return None
    validate_manifest(manifest,profile.agent_key,profile.version)
    return next((deepcopy(b) for b in manifest['bindings'] if b['stepKey'] == step_key and b['stage'] == stage),None)


def method_messages(profile,step_key,stage,legacy):
    """Historical Runs retain their original method; new ones load the frozen package."""
    return [] if resolve(profile,step_key,stage) else legacy


def prepare(profile,step_key,stage,messages,output_schema):
    binding = resolve(profile,step_key,stage)
    if not binding: return None,messages
    package = binding['package']
    if digest(output_schema) != digest(package['outputContract']):
        raise ValueError('skill-output-contract-mismatch')
    # The first JSON user message is the original operation input. A separately
    # budgeted schema-repair message, if present, remains untrusted user data.
    inputs = []
    for message in messages:
        if message.get('role') != 'user': continue
        try: value = json.loads(message.get('content',''))
        except (ValueError,TypeError): continue
        inputs.append(value)
    if len(inputs) != 1:
        raise ValueError('skill-input-contract-mismatch')
    try: validate(inputs[0],package['inputContract'])
    except ValidationError:
        raise ValueError('skill-input-contract-mismatch') from None
    binding['inputHash'] = digest(inputs[0])
    return binding,[*[{'role':'system','content':text} for text in package['instructions']],*deepcopy(messages)]


class SkillRepository:
    def __init__(self,factory): self.factory = factory

    def start(self,ctx,binding,*,step_run_id,operation_key,context_id,manifest_hash,schema_hash):
        from .repository import new_id
        fields = {'step_key':binding['stepKey'],'stage':binding['stage'],'skill_key':binding['skillKey'],
            'skill_version':binding['version'],'content_hash':binding['contentHash'],'input_hash':binding['inputHash'],
            'schema_hash':schema_hash,'context_id':context_id,'manifest_hash':manifest_hash,'step_run_id':step_run_id}
        with self.factory() as con:
            with con.cursor() as c:
                c.execute('SELECT profile_json FROM agent_run_profile_snapshot WHERE run_id=%s',(ctx.run_id,))
                row = c.fetchone()
                if not row: raise ValueError('skill-run-snapshot-missing')
                saved = row['profile_json'].get('skillManifest') or {}
                if saved != getattr(ctx.profile,'skill_manifest',{}):
                    raise ValueError('skill-run-snapshot-mismatch')
                if step_run_id:
                    c.execute('SELECT run_id,step_key FROM agent_step_run WHERE step_run_id=%s',(step_run_id,))
                    step = c.fetchone()
                    if not step or step['run_id'] != ctx.run_id or step['step_key'] != binding['stepKey']:
                        raise ValueError('skill-step-ownership-mismatch')
                c.execute('''INSERT INTO agent_run_skill_invocation(invocation_id,run_id,operation_key,
                    step_key,stage,skill_key,skill_version,content_hash,input_hash,schema_hash,context_id,
                    manifest_hash,step_run_id,status) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'running')
                    ON CONFLICT(run_id,operation_key) DO NOTHING''',
                    (new_id('skill-invocation'),ctx.run_id,operation_key,*fields.values()))
                c.execute('SELECT * FROM agent_run_skill_invocation WHERE run_id=%s AND operation_key=%s FOR UPDATE',
                    (ctx.run_id,operation_key))
                row = c.fetchone()
                if any(row[k] != v for k,v in fields.items()):
                    raise ValueError('skill-invocation-replay-mismatch')
                return row['invocation_id']

    def finish(self,run_id,invocation_id,status,*,output_hash=None,error_code=None):
        with self.factory() as con:
            with con.cursor() as c:
                c.execute('SELECT * FROM agent_run_skill_invocation WHERE invocation_id=%s AND run_id=%s FOR UPDATE',
                    (invocation_id,run_id)); invocation = c.fetchone()
                if not invocation: raise ValueError('skill-invocation-missing')
                c.execute('''SELECT call_id,status,output_artifact_id FROM agent_model_call WHERE run_id=%s
                    AND operation_key=%s AND stage=%s AND manifest_hash IS NOT DISTINCT FROM %s ORDER BY created_at,call_id''',
                    (run_id,invocation['operation_key'],invocation['stage'],invocation['manifest_hash']))
                calls = c.fetchall()
                if status == 'succeeded':
                    successful = [call for call in calls if call['status'] == 'succeeded']
                    if not successful: raise ValueError('skill-success-model-proof-missing')
                    c.execute('SELECT content_json FROM agent_run_artifact WHERE artifact_id=%s AND run_id=%s',
                        (successful[-1]['output_artifact_id'],run_id))
                    artifact = c.fetchone()
                    if not artifact or digest(artifact['content_json'].get('output')) != output_hash:
                        raise ValueError('skill-success-output-proof-mismatch')
                if invocation['status'] == 'succeeded':
                    if status != 'succeeded' or invocation['output_hash'] != output_hash:
                        raise ValueError('skill-terminal-replay-mismatch')
                    return
                c.execute('''UPDATE agent_run_skill_invocation SET status=%s,model_call_ids=%s,output_hash=%s,
                    error_code=%s,completed_at=now() WHERE invocation_id=%s AND run_id=%s''',
                    (status,Json([v['call_id'] for v in calls]),output_hash,error_code,invocation_id,run_id))

    def list(self,run_id):
        with self.factory() as con:
            with con.cursor() as c:
                if not installed(c): return []
                c.execute('SELECT * FROM agent_run_skill_invocation WHERE run_id=%s ORDER BY created_at,invocation_id',(run_id,))
                return [dict(v) for v in c.fetchall()]
