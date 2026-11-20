"""Bounded object research runs; human publication is a separate operation."""
from __future__ import annotations

import json
import secrets
import threading
import time
from datetime import datetime, timezone

try:
    from capability_profile import CapabilityProfiles, REGISTRY, dump, decode
    from intelligence_store import IntelligenceConflict, IntelligenceNotFound, IntelligenceValidationError
except ModuleNotFoundError:
    from .capability_profile import CapabilityProfiles, REGISTRY, dump, decode
    from .intelligence_store import IntelligenceConflict, IntelligenceNotFound, IntelligenceValidationError


def now():
    return datetime.now(timezone.utc).isoformat()


class CapabilityWorkflow:
    def __init__(self, agent):
        self.agent,self.store=agent,agent.store

    def recover(self):
        # Only call once at service startup, before accepting new requests.
        with self.store._lock,self.store._connect() as con:
            rows=con.execute("SELECT run_id FROM ontology_capability_run WHERE status IN ('queued','running')").fetchall()
            for row in rows:
                self._event_tx(con,row[0],'recovery','interrupted',{'reason':'服务重启；已保留正文和候选，可继续执行'})
                con.execute("UPDATE ontology_capability_run SET status='interrupted',completed_at=? WHERE run_id=?",(now(),row[0]))
            batch_rows=con.execute("SELECT batch_id FROM ontology_capability_batch WHERE status IN ('queued','running')").fetchall()
            for row in batch_rows:
                self._batch_event_tx(con,row[0],None,'recovery','interrupted',{'reason':'服务重启；实体项状态和已保存候选已保留，可重新提交未完成实体'})
                con.execute("UPDATE ontology_capability_batch SET status='interrupted',completed_at=? WHERE batch_id=?",(now(),row[0]))
                con.execute("UPDATE ontology_capability_batch_item SET status='interrupted',updated_at=? WHERE batch_id=? AND status IN ('queued','running')",(now(),row[0]))
        return len(rows)+len(batch_rows)

    def get(self, run_id):
        with self.store._connect() as con:
            row=con.execute('SELECT * FROM ontology_capability_run WHERE run_id=?',(run_id,)).fetchone()
            if not row:raise IntelligenceNotFound('capability run not found')
            events=[{'eventId':r['event_id'],'stage':r['stage'],'status':r['status'],'detail':decode(r['detail_json'],{}),'createdAt':r['created_at']} for r in con.execute('SELECT * FROM ontology_capability_run_event WHERE run_id=? ORDER BY event_id',(run_id,))]
        return {'runId':row['run_id'],'entityId':row['entity_id'],'objectRevision':row['object_revision'],'status':row['status'],'request':decode(row['request_json']),'result':decode(row['result_json']),'createdAt':row['created_at'],'completedAt':row['completed_at'],'events':events}

    def list(self, entity_id):
        entity,_=self.agent._entity_document(entity_id)
        with self.store._connect() as con:
            ids=[r[0] for r in con.execute('SELECT run_id FROM ontology_capability_run WHERE entity_id=? ORDER BY created_at DESC LIMIT 20',(entity['entityId'],))]
        return [self.get(i) for i in ids]

    @staticmethod
    def _event_tx(con,run_id,stage,status,detail):
        con.execute('INSERT INTO ontology_capability_run_event(run_id,stage,status,detail_json,created_at) VALUES(?,?,?,?,?)',(run_id,stage,status,dump(detail),now()))

    def event(self,run_id,stage,status,detail):
        with self.store._lock,self.store._connect() as con:self._event_tx(con,run_id,stage,status,detail)

    @staticmethod
    def _normalize_request(payload):
        """Validate one bounded entity run and keep the budget auditable."""
        payload = payload or {}
        codes=payload.get('capabilityCodes') or ['platform']
        if not isinstance(codes,list) or not codes or any(c not in {g['id'] for g in REGISTRY['groups']} for c in codes):
            raise IntelligenceValidationError('select registered capabilityCodes')
        request={'capabilityCodes':list(dict.fromkeys(codes)),'maxJobs':payload.get('maxJobs',2),'maxModelCalls':payload.get('maxModelCalls',2),'maxElapsedSeconds':payload.get('maxElapsedSeconds',600),'maxFollowupRounds':payload.get('maxFollowupRounds',1),'maxResponseBytes':payload.get('maxResponseBytes',4000000),'force':bool(payload.get('force',False))}
        for key,upper in [('maxJobs',7),('maxModelCalls',8),('maxElapsedSeconds',1800)]:
            if type(request[key]) is not int or not 1<=request[key]<=upper:raise IntelligenceValidationError(f'{key} must be an integer from 1 to {upper}')
        if type(request['maxResponseBytes']) is not int or not 65536<=request['maxResponseBytes']<=4000000:raise IntelligenceValidationError('maxResponseBytes must be an integer from 65536 to 4000000')
        if type(request['maxFollowupRounds']) is not int or not 0<=request['maxFollowupRounds']<=2:raise IntelligenceValidationError('maxFollowupRounds must be 0 to 2')
        return request

    def submit(self,payload,entity_id,*,background=True):
        entity,_=self.agent._entity_document(entity_id)
        if entity['currentRevision']<1:raise IntelligenceConflict('entity has no archive revision; complete its identity archive first')
        request=self._normalize_request(payload)
        run_id='cap-run-'+secrets.token_hex(12)
        with self.store._lock,self.store._connect() as con:
            con.execute('BEGIN IMMEDIATE')
            active=con.execute("SELECT run_id FROM ontology_capability_run WHERE entity_id=? AND status IN ('queued','running')",(entity['entityId'],)).fetchone()
            if active:raise IntelligenceConflict('an object capability run is already active: '+active[0])
            con.execute('INSERT INTO ontology_capability_run(run_id,entity_id,object_revision,status,request_json,created_at) VALUES(?,?,?,\'queued\',?,?)',(run_id,entity['entityId'],entity['currentRevision'],dump(request),now()))
            self._event_tx(con,run_id,'plan','completed',{'budgets':request,'objectRevision':entity['currentRevision']})
        if background:
            threading.Thread(target=self.execute,args=(run_id,),daemon=True,name=run_id).start()
        else:self.execute(run_id)
        return self.get(run_id)

    def _batch_event_tx(self, con, batch_id, entity_id, stage, status, detail):
        con.execute(
            'INSERT INTO ontology_capability_batch_event(batch_id,entity_id,stage,status,detail_json,created_at) VALUES(?,?,?,?,?,?)',
            (batch_id, entity_id, stage, status, dump(detail), now()),
        )

    def get_batch(self, batch_id):
        with self.store._connect() as con:
            batch=con.execute('SELECT * FROM ontology_capability_batch WHERE batch_id=?',(batch_id,)).fetchone()
            if not batch: raise IntelligenceNotFound('capability batch not found')
            items=[]
            for row in con.execute('SELECT * FROM ontology_capability_batch_item WHERE batch_id=? ORDER BY created_at,entity_id',(batch_id,)):
                item=dict(row)
                for source,target in [('batch_id','batchId'),('entity_id','entityId'),('run_id','runId'),('current_scope','currentScope'),('max_items','maxItems'),('max_model_calls','maxModelCalls'),('max_elapsed_seconds','maxElapsedSeconds'),('max_response_bytes','maxResponseBytes'),('stop_reason','stopReason'),('candidate_count','candidateCount'),('conflict_count','conflictCount'),('error_message','error'),('created_at','createdAt'),('updated_at','updatedAt')]:
                    item[target]=item.pop(source)
                items.append(item)
            events=[{'eventId':row['event_id'],'entityId':row['entity_id'],'stage':row['stage'],'status':row['status'],'detail':decode(row['detail_json'],{}),'createdAt':row['created_at']} for row in con.execute('SELECT * FROM ontology_capability_batch_event WHERE batch_id=? ORDER BY event_id',(batch_id,))]
        return {'batchId':batch['batch_id'],'status':batch['status'],'request':decode(batch['request_json'],{}),'result':decode(batch['result_json'],{}),'createdAt':batch['created_at'],'startedAt':batch['started_at'],'completedAt':batch['completed_at'],'items':items,'events':events}

    def list_batches(self, *, entity_id=None, limit=20):
        limit=max(1,min(int(limit),100))
        with self.store._connect() as con:
            if entity_id:
                ids=[row[0] for row in con.execute('SELECT DISTINCT batch_id FROM ontology_capability_batch_item WHERE entity_id=? ORDER BY batch_id DESC LIMIT ?', (entity_id,limit))]
                return [self.get_batch(batch_id) for batch_id in ids]
            ids=[row[0] for row in con.execute('SELECT batch_id FROM ontology_capability_batch ORDER BY created_at DESC LIMIT ?', (limit,))]
        return [self.get_batch(batch_id) for batch_id in ids]

    def submit_batch(self, payload, entity_ids=None, *, background=True):
        """Queue independent entity runs with a shared, bounded policy.

        Entity errors are recorded on their item and do not cancel sibling
        runs. Publication remains a separate human operation on each profile.
        """
        payload=payload or {}
        request=self._normalize_request(payload)
        requested=entity_ids if entity_ids is not None else payload.get('entityIds')
        if requested is None and payload.get('allEntities'):
            requested=[item['entityId'] for item in self.agent._entities() if item.get('entityType') == 'EquipmentModel']
        if isinstance(requested,str): requested=[requested]
        if not isinstance(requested,list) or not requested:
            raise IntelligenceValidationError('entityIds must be a non-empty array, or set allEntities=true')
        if len(requested)>200: raise IntelligenceValidationError('entityIds cannot contain more than 200 entities per batch')
        resolved=[];seen=set()
        for value in requested:
            entity,_=self.agent._entity_document(str(value))
            if entity['entityId'] not in seen:
                seen.add(entity['entityId']);resolved.append(entity)
        batch_id='cap-batch-'+secrets.token_hex(12); created=now()
        batch_request={**request,'entityIds':[item['entityId'] for item in resolved]}
        with self.store._lock,self.store._connect() as con:
            con.execute('BEGIN IMMEDIATE')
            con.execute('INSERT INTO ontology_capability_batch(batch_id,request_json,status,created_at) VALUES(?,?,\'queued\',?)',(batch_id,dump(batch_request),created))
            for entity in resolved:
                con.execute('INSERT INTO ontology_capability_batch_item(batch_id,entity_id,status,max_items,max_model_calls,max_elapsed_seconds,max_response_bytes,created_at,updated_at) VALUES(?,?,\'queued\',?,?,?,?,?,?)',(batch_id,entity['entityId'],request['maxJobs'],request['maxModelCalls'],request['maxElapsedSeconds'],request['maxResponseBytes'],created,created))
            self._batch_event_tx(con,batch_id,None,'plan','completed',{'entityCount':len(resolved),'budgets':request})
        if background:
            threading.Thread(target=self.execute_batch,args=(batch_id,),daemon=True,name=batch_id).start()
        else:self.execute_batch(batch_id)
        return self.get_batch(batch_id)

    def execute_batch(self,batch_id):
        batch=self.get_batch(batch_id); request=batch['request']; started=now()
        with self.store._lock,self.store._connect() as con:
            con.execute("UPDATE ontology_capability_batch SET status='running',started_at=? WHERE batch_id=?",(started,batch_id))
        for item in batch['items']:
            entity_id=item['entityId']; updated=now()
            with self.store._lock,self.store._connect() as con:
                con.execute("UPDATE ontology_capability_batch_item SET status='running',updated_at=?,error_message=NULL WHERE batch_id=? AND entity_id=?",(updated,batch_id,entity_id))
                self._batch_event_tx(con,batch_id,entity_id,'entity','running',{'entityId':entity_id})
            try:
                run=self.submit(request,entity_id,background=False)
                result=run.get('result') or {}
                status=run.get('status') or 'failed'
                quality=result.get('quality') or {}
                events=run.get('events') or []
                scopes=[event.get('detail',{}).get('target') for event in events if event.get('detail',{}).get('target')]
                current_scope=scopes[-1] if scopes else None
                stop_reason=result.get('stopReason')
                candidates=sum(int(event.get('detail',{}).get('candidateCount') or 0) + int(event.get('detail',{}).get('relationCandidateCount') or 0) for event in events if event.get('stage')=='extraction')
                conflict_count=int(quality.get('conflictCount') or 0)
                with self.store._lock,self.store._connect() as con:
                    con.execute('UPDATE ontology_capability_batch_item SET run_id=?,status=?,current_scope=?,round=?,stop_reason=?,candidate_count=?,conflict_count=?,updated_at=? WHERE batch_id=? AND entity_id=?',(run['runId'],status,current_scope,int(result.get('followupRounds') or 0),stop_reason,candidates,conflict_count,now(),batch_id,entity_id))
                    self._batch_event_tx(con,batch_id,entity_id,'entity',status,{'runId':run['runId'],'stopReason':stop_reason,'candidateCount':candidates,'conflictCount':conflict_count})
            except Exception as exc:
                with self.store._lock,self.store._connect() as con:
                    con.execute("UPDATE ontology_capability_batch_item SET status='failed',error_message=?,stop_reason='entity-run-failed',updated_at=? WHERE batch_id=? AND entity_id=?",(str(exc)[:500],now(),batch_id,entity_id))
                    self._batch_event_tx(con,batch_id,entity_id,'entity','failed',{'error':str(exc)[:500]})
        final=self.get_batch(batch_id)
        statuses=[item['status'] for item in final['items']]
        failed=sum(status=='failed' for status in statuses)
        status='failed' if failed==len(statuses) else 'partial' if failed else 'completed'
        result={'entityCount':len(statuses),'completedCount':len(statuses)-failed,'failedCount':failed,'awaitingHumanReviewCount':sum(status=='awaiting-human-review' for status in statuses),'publication':'requires-explicit-human-review'}
        with self.store._lock,self.store._connect() as con:
            con.execute('UPDATE ontology_capability_batch SET status=?,result_json=?,completed_at=? WHERE batch_id=?',(status,dump(result),now(),batch_id))
            self._batch_event_tx(con,batch_id,None,'batch',status,result)
        return self.get_batch(batch_id)

    def execute(self,run_id):
        run=self.get(run_id);request=run['request'];started=time.monotonic()
        with self.store._lock,self.store._connect() as con:
            con.execute("UPDATE ontology_capability_run SET status='running' WHERE run_id=?",(run_id,))
        results=[];extractions=[];unresolved_model_errors=set();stop='selected-targets-processed'
        try:
            targets={g['target'] for g in REGISTRY['groups'] if g['id'] in request['capabilityCodes']}
            jobs=self.agent.queue.plan(run['entityId'],force=request['force'],target_types=targets)
            jobs=[j for j in jobs if j['targetType'] in targets]
            for index,job in enumerate(jobs[:request['maxJobs']]):
                if time.monotonic()-started>=request['maxElapsedSeconds']:stop='time-budget-exhausted';break
                self.event(run_id,'collection','running',{'target':job['targetType'],'jobId':job['verificationId'],'completedJobs':index,'totalJobs':min(len(jobs),request['maxJobs'])})
                result=job if job['status'] in {'completed','needs-review'} and not request['force'] else self.agent.queue.execute(job['verificationId'],deadline_monotonic=started+request['maxElapsedSeconds'])
                results.append(result)
                self.event(run_id,'collection','failed' if result['status']=='failed' else 'completed',{'jobId':job['verificationId'],'items':len((result.get('result') or {}).get('items',[])),'error':result.get('error'),'budget':(result.get('result') or {}).get('budget')})
            if len(jobs)>request['maxJobs']:stop='job-budget-exhausted'
            calls=0
            extraction_targets=sorted({j['targetType'] for j in results if (j.get('result') or {}).get('items')})
            for target_index,target in enumerate(extraction_targets):
                offset=0
                while calls<request['maxModelCalls'] and time.monotonic()-started<request['maxElapsedSeconds']:
                    self.event(run_id,'extraction','running',{'target':target,'batch':calls+1,'maxModelCalls':request['maxModelCalls'],'materialOffset':offset})
                    extracted=self.agent.extract_attribute_candidates(run['entityId'],{'scope':target,'materialOffset':offset,'modelTimeoutSeconds':max(1,request['maxElapsedSeconds']-(time.monotonic()-started))})
                    calls+=1;extractions.append(extracted)
                    failed_batch=extracted['status'] in {'extraction-failed','model-unavailable'}
                    if failed_batch:unresolved_model_errors.add((target,offset))
                    else:unresolved_model_errors.discard((target,offset))
                    self.event(run_id,'extraction',extracted['status'],{'target':target,'candidateCount':len(extracted.get('candidates',[])),'relationCandidateCount':len(extracted.get('relationCandidates',[])),'errorCode':extracted.get('errorCode'),'rejectedClaims':extracted.get('rejectedClaims',[]),'materialsRemaining':extracted.get('materialsRemaining',0)})
                    if extracted['status'] in {'extraction-failed','model-unavailable'} and calls<request['maxModelCalls'] and time.monotonic()-started<request['maxElapsedSeconds']:
                        self.event(run_id,'extraction','retrying',{'target':target,'attempt':calls+1,'reason':extracted['status']})
                        continue
                    if not extracted.get('materialsRemaining') or not extracted.get('materialsUsed') or extracted['status']=='extraction-failed':break
                    offset+=extracted['materialsUsed']
                if calls>=request['maxModelCalls'] and (target_index<len(extraction_targets)-1 or (extractions and extractions[-1].get('materialsRemaining'))):stop='model-budget-exhausted'
            followups=0
            wanted={key for g in REGISTRY['groups'] if g['id'] in request['capabilityCodes'] for key in g['keys']}
            while followups<request['maxFollowupRounds'] and len(results)<request['maxJobs'] and calls<request['maxModelCalls'] and time.monotonic()-started<request['maxElapsedSeconds']:
                draft=CapabilityProfiles(self.agent).read(run['entityId'])
                gap_keys=[m['attributeKey'] for c in draft['capabilities'] for m in c['metrics'] if m['attributeKey'] in wanted and m['claimState']=='unknown']
                if not gap_keys:break
                target=next(g['target'] for g in REGISTRY['groups'] if any(key in gap_keys for key in g['keys']))
                job=next(j for j in self.agent.queue.plan(run['entityId'],force=True,target_types={target}) if j['targetType']==target)
                followups+=1
                # The immutable source snapshots remain available even as the
                # queue's latest query is refined to a bounded gap subset.
                selected_keys=[key for key in gap_keys if key in job['attributeKeys']][:6]
                query=job['query']+' explicit specification '+ ' '.join(selected_keys)
                with self.store._lock,self.store._connect() as con:
                    con.execute('UPDATE ontology_verification_job SET query_text=?,attribute_keys_json=? WHERE verification_id=? AND status=\'planned\'',(query,dump(selected_keys),job['verificationId']))
                self.event(run_id,'followup','running',{'target':target,'gapKeys':selected_keys,'round':followups,'maxRounds':request['maxFollowupRounds']})
                result=self.agent.queue.execute(job['verificationId'],deadline_monotonic=started+request['maxElapsedSeconds']);results.append(result)
                self.event(run_id,'followup',result['status'],{'target':target,'items':len((result.get('result') or {}).get('items',[])),'gapKeys':selected_keys})
                if (result.get('result') or {}).get('items') and time.monotonic()-started<request['maxElapsedSeconds']:
                    extracted=self.agent.extract_attribute_candidates(run['entityId'],{'scope':target,'modelTimeoutSeconds':max(1,request['maxElapsedSeconds']-(time.monotonic()-started))})
                    calls+=1;extractions.append(extracted)
                    if extracted['status'] in {'extraction-failed','model-unavailable'}:unresolved_model_errors.add((target,0))
                    else:unresolved_model_errors.discard((target,0))
                    self.event(run_id,'extraction',extracted['status'],{'target':target,'candidateCount':len(extracted.get('candidates',[])),'relationCandidateCount':len(extracted.get('relationCandidates',[])),'errorCode':extracted.get('errorCode'),'followupRound':followups,'materialsRemaining':extracted.get('materialsRemaining',0)})
            if time.monotonic()-started>=request['maxElapsedSeconds']:stop='time-budget-exhausted'
            current,_=self.agent._entity_document(run['entityId'])
            if current['currentRevision']!=run['objectRevision']:raise IntelligenceConflict('object changed during research run')
            profile=CapabilityProfiles(self.agent).save(run['entityId'])['profile']
            quality=profile['quality']
            self.event(run_id,'validation','completed',{'quality':quality,'profileId':profile['profileId'],'conflicts':quality['conflictCount']})
            latest_collections={r['targetType']:r for r in results}
            failed=any(r['status']=='failed' for r in latest_collections.values()) or bool(unresolved_model_errors)
            pending=any(m['origin']=='candidate' and m['reviewStatus']!='published' for c in profile['capabilities'] for m in c['metrics'])
            scope_gaps=[m['attributeKey'] for c in profile['capabilities'] for m in c['metrics'] if m['attributeKey'] in wanted and m['claimState']=='unknown']
            if scope_gaps and stop=='selected-targets-processed':
                if calls>=request['maxModelCalls']:stop='model-budget-exhausted'
                elif len(results)>=request['maxJobs']:stop='job-budget-exhausted'
            if scope_gaps and followups>=request['maxFollowupRounds'] and request['maxFollowupRounds']>0:stop='followup-rounds-exhausted'
            status='failed' if failed else 'awaiting-human-review' if pending else 'completed-with-gaps' if scope_gaps else 'completed'
            result={'profileId':profile['profileId'],'quality':quality,'stopReason':stop,'completedJobs':len(results),'modelCalls':calls,'followupRounds':followups,'maxResponseBytes':request['maxResponseBytes'],'unresolvedModelErrors':[{'target':t,'materialOffset':o} for t,o in sorted(unresolved_model_errors)],'durationSeconds':round(time.monotonic()-started,2),'publication':'requires-explicit-human-review','remainingGapKeys':[m['attributeKey'] for c in profile['capabilities'] for m in c['metrics'] if m['claimState']=='unknown']}
            self.event(run_id,'profile',status,result)
        except Exception as exc:
            status='failed';result={'stopReason':'execution-failed','error':str(exc)[:400],'durationSeconds':round(time.monotonic()-started,2)}
            self.event(run_id,'failure','failed',result)
        with self.store._lock,self.store._connect() as con:
            con.execute('UPDATE ontology_capability_run SET status=?,result_json=?,completed_at=? WHERE run_id=?',(status,dump(result),now(),run_id))
