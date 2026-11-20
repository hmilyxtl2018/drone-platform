"""Bounded event coordinator. All actual model execution remains in Engine."""
from datetime import datetime,timedelta,timezone
import json
from types import SimpleNamespace
from psycopg2.extras import Json
from .evolution_repository import EvolutionRepository,installed
from .task_intent import read,assert_dispatch,digest,admission_actor,parse_deadline
from .repository import new_id


def current_output(cursor,run_id):
    cursor.execute('SELECT output_json FROM agent_run WHERE run_id=%s',(run_id,))
    return cursor.fetchone()['output_json'] or {}


def audit_bundle(cursor,outbox):
    """Small deterministic projection of actual saved outputs and records."""
    run_id=outbox['source_run_id'];output=outbox['source_output_json']
    refs=[{'refId':'source-output','runId':run_id,'kind':'agent-output','contentHash':outbox['source_output_hash'],
        'path':'/','excerpt':'Frozen source report; see the selected report fields below.'}]
    graph=output.get('ontology') or (output.get('judgmentReport') or {}).get('ontology') or {}
    assertions=graph.get('assertions') or []
    evidence=output.get('evidence') or []
    summary={'status':outbox['source_status'],'perspectives':[
        {k:str(p[k])[:80] for k in ('key','name','title','status','evidenceStatus') if k in p}
        for p in (output.get('perspectives') or [])[:12] if isinstance(p,dict)],
        'savedEvidenceCount':len(evidence),'assertionCount':len(assertions),
        'assertionsWithEvidence':sum(bool(a.get('evidenceIds')) for a in assertions if isinstance(a,dict)),
        'truthVerified':output.get('truthVerified',False),
        'evidenceGapCount':len(output.get('evidenceGaps') or []),
        'findings':[{k:f.get(k) for k in ('perspectiveKey','status')} | {
            'evidenceIdCount':len(f.get('evidenceIds') or []),
            'relationCount':len(f.get('relations') or []),
            'relationsWithEvidence':sum(bool(r.get('evidenceIds')) for r in f.get('relations',[]) if isinstance(r,dict)),
            'relationsWithConditions':sum(bool(r.get('conditions')) for r in f.get('relations',[]) if isinstance(r,dict))}
            for f in output.get('findings',[]) if isinstance(f,dict)],
        'projectionScope':'selected report fields and counts, not a complete factual audit'}
    # Recorded source bindings prevent the reviewer from interpreting an
    # unshown original as proof that a search snippet was used as a citation.
    source_bindings=[]
    from .ontology_explorer import source_excerpt
    from .evidence_repository import EvidenceRepository
    class AuditReader(EvidenceRepository):
        def get_artifact(self,run_id,artifact_id):
            cursor.execute('SELECT * FROM agent_run_artifact WHERE run_id=%s AND artifact_id=%s',(run_id,artifact_id))
            return cursor.fetchone()
        def get_source_snapshot(self,run_id,source_id):
            return self._source(cursor,run_id,source_id)
    for item in evidence[:12]:
        if not isinstance(item,dict):continue
        binding={k:item[k] for k in ('evidenceId','candidateId','sourceUri') if k in item}
        try:
            resolved=source_excerpt({**output,'runId':run_id},item['evidenceId'],AuditReader(None))
            binding.update({k:resolved[k] for k in ('sourceArtifactId','sourceArtifactHash','contentKind','locatorStatus')})
            binding['exactQuoteVerified']=True
            refs.append({'refId':resolved['sourceArtifactId'],'runId':run_id,'kind':'saved-original-source',
                'contentHash':resolved['sourceArtifactHash']})
        except (KeyError,ValueError,TypeError):
            binding.update(exactQuoteVerified=False,resolutionStatus='unavailable')
        source_bindings.append({**binding,'factualTruthVerified':False})
    refs=list({r['refId']:r for r in refs}.values())
    summary['sourceBindings']=source_bindings
    fragments=[]
    for field in ['findings','evidenceGaps','unknowns','conflicts','redTeam','capabilityGaps']:
        values=output.get(field) or []
        is_list=isinstance(values,list)
        if not is_list:values=[values]
        for index,item in enumerate(values[:6 if field=='findings' else 2]):
            # Project complete review fields before bounding text. Cutting the
            # first bytes of a finding hides citations that occur after entities.
            if isinstance(item,dict) and field=='findings':
                item={k:item[k] for k in ('perspectiveKey','status','statement','evidenceIds',
                    'conditions','unknowns') if k in item} | {
                    'supportReview':{k:(item.get('supportReview') or {}).get(k) for k in ('verdict','truthVerified')},
                    'relations':[{k:r[k] for k in ('id','evidenceIds','conditions','modality') if k in r}
                        for r in item.get('relations',[]) if isinstance(r,dict)]}
            elif isinstance(item,dict) and field=='evidenceGaps':
                item={k:item[k] for k in ('reason','summary','perspectiveKey','exclusionRule',
                    'exclusionReason','ruleReference') if k in item} | {
                    'attemptCount':len(item.get('attempts',[])),
                    'attemptsAreDiscoveryOnly':True}
            encoded=json.dumps(item,ensure_ascii=False,default=str,separators=(',',':'))
            # Metadata is held once; references resolve to the separately bound
            # fragment. Never repeat the same excerpt in the designer request.
            excerpt=encoded[:3000]
            ref={'refId':f'report:{field}:{index}','runId':run_id,'kind':'report-field',
                'contentHash':outbox['source_output_hash'],'path':f'/{field}/{index}' if is_list else f'/{field}',
                'truncated':len(encoded)>3000,'projection':'selected-quality-fields'}
            refs.append(ref);fragments.append({'refId':ref['refId'],'excerpt':excerpt,'truncated':ref['truncated']})
    cursor.execute('SELECT artifact_id,artifact_type,content_hash FROM agent_run_artifact WHERE run_id=%s ORDER BY created_at DESC LIMIT 8',(run_id,))
    for r in cursor.fetchall():refs.append({'refId':r['artifact_id'],'runId':run_id,'kind':r['artifact_type'],'contentHash':r['content_hash']})
    cursor.execute('SELECT event_id,event_type,step_key,detail_json FROM agent_run_event WHERE run_id=%s AND (event_type LIKE %s OR event_type LIKE %s) ORDER BY event_id DESC LIMIT 6',
        (run_id,'%failed%','%gap%'))
    events=[]
    for row in cursor.fetchall():
        identity='event:'+str(row['event_id'])
        refs.append({'refId':identity,'runId':run_id,'kind':row['event_type'],'contentHash':digest(row['detail_json'])})
        events.append({'refId':identity,'type':row['event_type'],'step':row['step_key']})
    return {'sourceRunId':run_id,'reportAudit':summary,'reportFragments':fragments,'events':events,'references':refs,
        'limitations':['These are saved report metadata, not independent factual verification.',
            'Omitted or truncated fields are unavailable, not proven missing; cause must remain unknown.',
            'Search snippets are discovery records, never original-source quotes or factual evidence.',
            'A genuine evidence gap does not justify creating unsupported relations.']}


class EvolutionController:
    def __init__(self,engine,queue):
        self.engine,self.queue=engine,queue
        self.factory=getattr(engine,'base_connection_factory',engine.repo.connection_factory)
        self.repo=EvolutionRepository(self.factory)

    def tick(self):
        """One transaction claims and dispatches one event; rollback releases it."""
        with self.factory() as con:
            with con.cursor() as c:
                if not installed(c):return False
                c.execute("SELECT pg_try_advisory_xact_lock(hashtextextended('evolution-controller-dispatch',0)) AS acquired")
                if not c.fetchone()['acquired']:return False
                # Source task locks must precede outbox locks to match completion.
                c.execute("SELECT * FROM agent_run_terminal_outbox WHERE status='pending' ORDER BY created_at LIMIT 1")
                event=c.fetchone()
                if not event:
                    self._reconcile(c)
                    from .evolution_trial import advance
                    return advance(self,c)
                try:assert_dispatch(c,event['source_run_id'],operation='read-evidence')
                except PermissionError:
                    c.execute("UPDATE agent_run_terminal_outbox SET status='skipped',reason='source-authority-inactive',processed_at=now() WHERE outbox_id=%s",(event['outbox_id'],))
                    return True
                c.execute("SELECT * FROM agent_run_terminal_outbox WHERE outbox_id=%s AND status='pending' FOR UPDATE SKIP LOCKED",(event['outbox_id'],))
                event=c.fetchone()
                if not event:return False
                c.execute('SELECT document_json,content_hash FROM agent_evolution_policy_revision WHERE policy_id=%s',(event['policy_id'],))
                record=c.fetchone();policy=record['document_json']
                c.execute('''SELECT r.status,r.error_message,s.profile_hash FROM agent_run r
                    JOIN agent_run_profile_snapshot s USING(run_id) WHERE r.run_id=%s''',(event['source_run_id'],))
                current=c.fetchone()
                c.execute('''SELECT p.document_json FROM agent_evolution_policy a JOIN agent_evolution_policy_revision p USING(policy_id)
                    WHERE a.agent_key=%s''',(policy['agentKey'],));active=c.fetchone()
                reason=None
                if digest(policy)!=record['content_hash']:reason='policy-integrity-mismatch'
                elif not active or not active['document_json']['enabled']:reason='policy-disabled'
                elif current['status'] not in {'completed','completed-with-gaps','requires-human-review'}:reason='source-not-delivered'
                elif current['profile_hash']!=event['source_profile_hash']:reason='source-profile-changed'
                elif digest(current_output(c,event['source_run_id']))!=event['source_output_hash']:reason='source-output-changed'
                if reason:
                    c.execute("UPDATE agent_run_terminal_outbox SET status='skipped',reason=%s,processed_at=now() WHERE outbox_id=%s",(reason,event['outbox_id']))
                    return True
                c.execute('SAVEPOINT evolution_source_admission')
                try:
                    self._dispatch(c,event,policy,record)
                except (ValueError,PermissionError,KeyError,TypeError) as exc:
                    c.execute('ROLLBACK TO SAVEPOINT evolution_source_admission')
                    c.execute("UPDATE agent_run_terminal_outbox SET status='skipped',reason=%s,processed_at=now() WHERE outbox_id=%s",
                        ('cycle-admission-'+type(exc).__name__,event['outbox_id']))
                finally:
                    c.execute('RELEASE SAVEPOINT evolution_source_admission')
                self._reconcile(c)
                from .evolution_trial import advance
                advance(self,c)
                return True

    def _dispatch(self,c,event,policy,record):
        from .research_reflection import reflection_profile
        source_profile=self.engine.loader.load_for_run(event['source_run_id'])
        profile=reflection_profile(c,source_profile,policy)
        bundle=audit_bundle(c,event)
        # Bound context bytes independently from report size.
        while len(json.dumps(bundle,ensure_ascii=False,default=str).encode())>16000 and len(bundle['reportFragments'])>2:
            removed=bundle['reportFragments'].pop()
            bundle['references']=[r for r in bundle['references'] if r['refId']!=removed['refId']]
        if len(json.dumps(bundle,ensure_ascii=False,default=str).encode())>16000:
            raise ValueError('reflection audit projection exceeds bounded input')
        cycle_id=new_id('evolution')
        deadline=min(parse_deadline(read(c,event['source_run_id'])['document']['expiresAt']),
            datetime.now(timezone.utc)+timedelta(seconds=policy['maxRuntimeSeconds']))
        max_trials=policy.get('maxTrials',0)
        c.execute('''INSERT INTO agent_evolution_cycle(cycle_id,outbox_id,source_run_id,policy_id,policy_hash,
            source_profile_hash,source_output_hash,status,max_trials) VALUES(%s,%s,%s,%s,%s,%s,%s,'reflecting',%s)''',
            (cycle_id,event['outbox_id'],event['source_run_id'],event['policy_id'],record['content_hash'],event['source_profile_hash'],event['source_output_hash'],max_trials))
        c.execute('INSERT INTO agent_evolution_budget(cycle_id,limits_json,expires_at) VALUES(%s,%s,%s)',
            (cycle_id,Json(policy['budgetLimits']),deadline))
        if policy.get('mode')=='auto-trial':
            from .evolution_trial import prepare
            prepare(c,cycle_id,event,source_profile)
        inputs={'question':'复盘已保存的研究报告质量，不改写研究结论。','sourceRunId':event['source_run_id'],
            'sourceOutputHash':event['source_output_hash'],'auditBundle':bundle,'scope':{'sourceRunId':event['source_run_id'],'purpose':'report-quality-only'},
            'taskIntent':{'allowedOperations':['read-evidence','analyze'],'allowedTools':[],
                'budgets':profile.resource_policy['budgetLimits'],'expiresAt':deadline.isoformat()}}
        from .run_origin import origin_context
        with admission_actor(SimpleNamespace(principal_id='research-evolution-controller',kind='system')), \
                origin_context(source_kind='evolution', source_ref=cycle_id):
            rid=self.engine.repo.create_run(profile,inputs,_cursor=c)
        c.execute('INSERT INTO agent_evolution_run_binding(run_id,cycle_id,role,input_hash) VALUES(%s,%s,\'reflection\',%s)',(rid,cycle_id,digest(inputs)))
        c.execute('''INSERT INTO agent_run_queue(run_id,pool_key,priority) VALUES(%s,%s,0) ON CONFLICT DO NOTHING''',(rid,self.queue.pool_key))
        c.execute("UPDATE agent_run_terminal_outbox SET status='processed',processed_at=now() WHERE outbox_id=%s",(event['outbox_id'],))
        self.repo.event(c,cycle_id,'reflection.queued',{'runId':rid,'sourceRunId':event['source_run_id'],'policyHash':record['content_hash']})

    def _reconcile(self,c):
        c.execute('''SELECT e.cycle_id,r.run_id,r.status FROM agent_evolution_cycle e
            JOIN agent_evolution_run_binding b USING(cycle_id) JOIN agent_run r ON r.run_id=b.run_id
            WHERE e.status='reflecting' AND e.max_trials=0 AND r.status IN ('completed','failed','cancelled','blocked')''')
        for row in c.fetchall():
            target='reflected' if row['status']=='completed' else 'failed'
            c.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('evolution:'+row['cycle_id'],))
            c.execute("UPDATE agent_evolution_cycle SET status=%s,revision=revision+1,updated_at=now(),stop_reason=%s WHERE cycle_id=%s AND status='reflecting' RETURNING cycle_id",
                (target,None if target=='reflected' else 'reflection-run-'+row['status'],row['cycle_id']))
            if c.fetchone():self.repo.event(c,row['cycle_id'],'reflection.'+target,{'runId':row['run_id']})
