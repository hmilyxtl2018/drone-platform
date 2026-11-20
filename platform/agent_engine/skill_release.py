"""Explicit method migrations through the existing configuration publication gate."""
from copy import deepcopy
from psycopg2.extras import Json
from .skills import current_manifest, reference, digest
from .skill_trials import _artifact, validate_candidate


def validate_assessment(c, assessment):
    from .skill_trials import release_gate
    if not assessment.get('publicationEligible') or not release_gate(assessment.get('results',[]))[-1]:
        raise PermissionError('Skill candidate has insufficient independent validation')
    for result in assessment['results']:
        c.execute("SELECT artifact_id FROM agent_run_artifact WHERE run_id=%s AND artifact_type='skill-expert-label' AND content_json->>'caseHash'=%s ORDER BY created_at DESC LIMIT 1",(result['sourceRunId'],result['caseHash']))
        latest=c.fetchone()
        if not latest or latest['artifact_id']!=result['label'].get('artifactId'):
            raise PermissionError('expert labels changed; evaluate again before release')


def validate_release(c, live, draft):
    """Called inside the human-authorized publication transaction."""
    doc=draft['document_json'];migration=(doc.get('models',{}).get('config_json') or {}).get('skillMigration')
    if not migration or migration.get('fromVersion')!=live['definition']['current_version']:
        return False
    c.execute("SELECT content_json FROM agent_run_artifact WHERE artifact_id=%s AND artifact_type='skill-method-release'",(migration.get('authorizationArtifactId'),))
    row=c.fetchone()
    from agent_admin.config_publication import _hash
    if not row or row['content_json']['documentHash']!=_hash(doc) or row['content_json']['baseLiveHash']!=draft['base_live_hash']:
        raise PermissionError('method release draft differs from reviewed migration')
    authorization=row['content_json']
    if authorization['mode']=='promotion':
        c.execute("SELECT content_json FROM agent_run_artifact WHERE artifact_id=%s AND artifact_type='skill-trial-assessment'",(authorization['assessmentArtifactId'],))
        check=c.fetchone()
        if not check:
            raise PermissionError('Skill candidate has insufficient independent validation')
        validate_assessment(c,check['content_json'])
    elif authorization['mode']!='rollback':raise PermissionError('unknown method release mode')
    current=current_manifest(c,draft['agent_key'],migration['fromVersion'])
    target=current_manifest(c,draft['agent_key'],doc['definition']['current_version'])
    if [reference(b) for b in current.get('bindings',[])]!=authorization['fromBindings'] or [reference(b) for b in target.get('bindings',[])]!=authorization['toBindings']:
        raise PermissionError('method migration bindings changed')
    return True


class SkillReleaseService:
    def __init__(self, engine):self.engine=engine;self.factory=engine.repo.connection_factory

    def draft(self, rid, trial_id, body, actor):
        if actor.kind!='human' or not actor.permits('admin'):raise PermissionError('human administrator required')
        from agent_admin.config_publication import ConfigPublicationService as Config, _hash, _reason, _key
        reason=_reason(body.get('reason'));version=_key(body.get('profileVersion'),'Profile version')
        trial=self.engine.repo.get_artifact(rid,trial_id)
        if not trial or trial['artifact_type']!='skill-version-trial':raise LookupError('trial not found in this Run')
        trial=trial['content_json']
        with self.factory() as con:
            with con.cursor() as c:
                Config._config_lock(c)
                c.execute("SELECT artifact_id,content_json FROM agent_run_artifact WHERE run_id=%s AND artifact_type='skill-trial-assessment' AND content_json->>'trialId'=%s ORDER BY created_at DESC LIMIT 1",(rid,trial_id))
                assessment=c.fetchone()
                if not assessment or not assessment['content_json']['publicationEligible']:
                    raise PermissionError('Skill candidate has insufficient independent validation; retain the current method')
                validate_assessment(c,assessment['content_json'])
                agent=self.engine.repo.get_run(rid)['agent_key']
                live,_,live_hash=Config._live(c,agent)
                source=current_manifest(c,agent,live['definition']['current_version'])
                selected=[deepcopy(b) for b in source.get('bindings',[])]
                target=next((b for b in selected if b['skillKey']==trial['baseline']['skillKey']),None)
                if not target or target['contentHash']!=trial['baseline']['contentHash']:raise ValueError('live method differs from experiment baseline')
                c.execute('SELECT package_json FROM agent_skill_revision WHERE skill_key=%s AND version=%s AND content_hash=%s',
                    (trial['candidate']['skillKey'],trial['candidate']['version'],trial['candidate']['contentHash']))
                candidate=c.fetchone()['package_json'];validate_candidate(target['package'],candidate)
                target.update(version=trial['candidate']['version'],contentHash=trial['candidate']['contentHash'],package=candidate)
                return self._prepare(c,Config,agent,live,live_hash,source,selected,version,rid,
                    {'mode':'promotion','trialId':trial_id,'assessmentArtifactId':assessment['artifact_id']},actor,reason)

    def rollback(self, rid, publication_id, body, actor):
        if actor.kind!='human' or not actor.permits('admin'):raise PermissionError('human administrator required')
        from agent_admin.config_publication import ConfigPublicationService as Config, _reason, _key
        reason=_reason(body.get('reason'));version=_key(body.get('profileVersion'),'Profile version')
        agent=self.engine.repo.get_run(rid)['agent_key']
        with self.factory() as con:
            with con.cursor() as c:
                Config._config_lock(c)
                c.execute('SELECT * FROM agent_config_publication WHERE publication_id=%s AND agent_key=%s',(publication_id,agent))
                publication=c.fetchone()
                if not publication:raise LookupError('publication does not belong to this Agent')
                doc=publication['document_json'];migration=(doc['models'].get('config_json') or {}).get('skillMigration')
                if not migration:raise ValueError('publication is not a Skill migration')
                c.execute("SELECT content_json FROM agent_run_artifact WHERE artifact_id=%s AND artifact_type='skill-method-release'",(migration['authorizationArtifactId'],))
                original=c.fetchone()
                if not original:raise ValueError('method release authorization absent')
                live,_,live_hash=Config._live(c,agent)
                source=current_manifest(c,agent,live['definition']['current_version'])
                if [reference(b) for b in source.get('bindings',[])]!=original['content_json']['toBindings']:
                    raise ValueError('live method differs from selected release; review a new rollback target')
                selected=current_manifest(c,agent,migration['fromVersion'])['bindings']
                return self._prepare(c,Config,agent,live,live_hash,source,selected,version,rid,
                    {'mode':'rollback','publicationId':publication_id},actor,reason)

    @staticmethod
    def _prepare(c,Config,agent,live,live_hash,source,selected,version,rid,authorization,actor,reason):
        from .repository import new_id
        from agent_admin.config_publication import _hash
        if version==live['definition']['current_version']:raise ValueError('new Profile version required')
        if current_manifest(c,agent,version):raise ValueError('target Profile version already has immutable method bindings')
        for b in selected:
            c.execute('''INSERT INTO agent_step_skill_binding(agent_key,profile_version,step_key,stage,skill_key,
                skill_version,content_hash,created_by) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)''',
                (agent,version,b['stepKey'],b['stage'],b['skillKey'],b['version'],b['contentHash'],actor.principal_id))
        aid=new_id('artifact');doc=deepcopy(live);doc['definition']['current_version']=version
        doc['models']['config_json']['skillMigration']={'fromVersion':live['definition']['current_version'],'authorizationArtifactId':aid,'sourceRunId':rid}
        authorization.update(documentHash=_hash(doc),baseLiveHash=live_hash,createdBy=actor.principal_id,
            fromBindings=[reference(b) for b in source.get('bindings',[])],toBindings=[reference(b) for b in selected],reason=reason)
        _artifact(c,rid,'skill-method-release',authorization,aid)
        draft_id=Config._insert_draft(c,agent,live,live_hash,doc,actor.principal_id,reason)
        return {'draftId':draft_id,'revision':1,'profileVersion':version,'methodBindings':authorization['toBindings'],
                'agentKey':agent,
                'mode':authorization['mode'],'status':'draft','publicationRequired':True,'automaticPublication':False,
                'compositionUpdateRequired':'Application Agents keep their frozen child Profile reference; update that reference separately after review.'}
