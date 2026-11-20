"""Durable, checkpointed object extraction jobs; no knowledge publication."""
from __future__ import annotations

from contextlib import contextmanager, nullcontext
from datetime import datetime, timezone
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import threading

try:
    from intelligence_store import IntelligenceConflict, IntelligenceNotFound, IntelligenceValidationError
    from research_trace import safe_error
except ModuleNotFoundError:
    from .intelligence_store import IntelligenceConflict, IntelligenceNotFound, IntelligenceValidationError
    from .research_trace import safe_error

SCHEMA = """
CREATE TABLE IF NOT EXISTS research_extraction_job(
 job_id TEXT PRIMARY KEY, assessment_id TEXT NOT NULL REFERENCES intelligence_assessment(assessment_id),
 status TEXT NOT NULL, request_payload TEXT NOT NULL, base_graph_revision_id TEXT,
 source_snapshot_hash TEXT NOT NULL, checkpoint TEXT NOT NULL DEFAULT '{}',
 progress TEXT NOT NULL DEFAULT '{}', result_graph_revision_id TEXT, error_code TEXT,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS research_extraction_job_assessment ON research_extraction_job(assessment_id,created_at);
CREATE UNIQUE INDEX IF NOT EXISTS research_extraction_active_job ON research_extraction_job(assessment_id)
 WHERE status IN ('queued','running','interrupted');
CREATE TRIGGER IF NOT EXISTS research_extraction_job_keep_input BEFORE UPDATE ON research_extraction_job
 WHEN OLD.assessment_id IS NOT NEW.assessment_id OR OLD.request_payload IS NOT NEW.request_payload
 OR OLD.base_graph_revision_id IS NOT NEW.base_graph_revision_id OR OLD.source_snapshot_hash IS NOT NEW.source_snapshot_hash
 BEGIN SELECT RAISE(ABORT,'extraction inputs are immutable'); END;
CREATE TRIGGER IF NOT EXISTS research_extraction_job_no_delete BEFORE DELETE ON research_extraction_job
 BEGIN SELECT RAISE(ABORT,'extraction jobs are retained'); END;
CREATE TABLE IF NOT EXISTS research_extraction_checkpoint(
 checkpoint_id INTEGER PRIMARY KEY AUTOINCREMENT,job_id TEXT NOT NULL REFERENCES research_extraction_job(job_id),
 phase TEXT NOT NULL,payload TEXT NOT NULL,content_hash TEXT NOT NULL,created_at TEXT NOT NULL);
CREATE TRIGGER IF NOT EXISTS research_extraction_checkpoint_no_update BEFORE UPDATE ON research_extraction_checkpoint
 BEGIN SELECT RAISE(ABORT,'extraction checkpoints are immutable'); END;
CREATE TRIGGER IF NOT EXISTS research_extraction_checkpoint_no_delete BEFORE DELETE ON research_extraction_checkpoint
 BEGIN SELECT RAISE(ABORT,'extraction checkpoints are retained'); END;
"""

def now():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')

def encoded(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))


@contextmanager
def job_lease(database_path, job_id):
    """Hold one kernel advisory lock across the entire worker operation.

    Kernel locks are released after process loss. Files remain as stable lock
    identities; deleting a file could let two workers lock different inodes.
    An unavailable lease is a normal competing worker, but other I/O failures
    propagate before this caller is allowed to change the job's status.
    """
    if not isinstance(job_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,160}', job_id):
        raise IntelligenceValidationError('invalid extraction job lease identity')
    folder = Path(database_path).resolve().parent / '.extraction-locks'
    folder.mkdir(exist_ok=True)
    with (folder / (job_id + '.lock')).open('a+b') as handle:
        if handle.seek(0, os.SEEK_END) == 0:
            handle.write(b'\0')
            handle.flush()
        handle.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                raise
            yield False
            return
        try:
            yield True
        finally:
            handle.seek(0)
            if os.name == 'nt':
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

class ExtractionJobManager:
    def __init__(self, agent, osint_store=None, *, runner=None):
        self.agent, self.store, self.osint = agent, agent.store, osint_store
        self.runner = runner or agent.build_assessment_graph
        self.workers = set()
        self.lock = threading.RLock()
        self.slots = threading.Semaphore(2)
        with self.store._connect() as con:
            con.executescript(SCHEMA)

    @staticmethod
    def _public(row):
        if row is None:return None
        return {'jobId':row['job_id'],'assessmentId':row['assessment_id'],'status':row['status'],
                'graphRevisionId':row['base_graph_revision_id'],'resultGraphRevisionId':row['result_graph_revision_id'],
                'progress':json.loads(row['progress']),'errorCode':row['error_code'],
                'request':json.loads(row['request_payload']), 'createdAt':row['created_at'],'updatedAt':row['updated_at'],
                'truthVerified':False,'publicationPerformed':False}

    def get(self, job_id):
        with self.store._connect() as con:
            row=con.execute('SELECT * FROM research_extraction_job WHERE job_id=?',(job_id,)).fetchone()
        if row is None:raise IntelligenceNotFound('extraction job not found')
        return self._public(row)

    def list(self, assessment_id):
        if self.store.get_assessment(assessment_id) is None:raise IntelligenceNotFound('assessment not found')
        with self.store._connect() as con:
            rows=con.execute('SELECT * FROM research_extraction_job WHERE assessment_id=? ORDER BY created_at DESC LIMIT 50',(assessment_id,)).fetchall()
        return [self._public(row) for row in rows]

    def submit(self, assessment_id, payload, *, start=True, reuse_existing=False):
        """Submit a job; automatic chaining reuses an earlier full extraction.

        ``reuse_existing`` is an internal coordinator option, never a browser
        payload field. Failed/partial jobs stay reviewable until an explicit
        manual retry; a duplicate assessment response must not spend again.
        """
        allowed={'expectedRevision','extractEntities','extractAttributes','objectIds','objectLabels'}
        if not isinstance(payload,dict) or set(payload)-allowed:raise IntelligenceValidationError('unsupported extraction job fields')
        if type(payload.get('expectedRevision')) is not int or payload['expectedRevision']<1:raise IntelligenceValidationError('expectedRevision must be a positive integer')
        for key in ('extractEntities','extractAttributes'):
            if type(payload.get(key,False)) is not bool:raise IntelligenceValidationError(key+' must be a boolean')
        if not payload.get('extractEntities') and not payload.get('extractAttributes'):raise IntelligenceValidationError('select object or attribute extraction')
        if reuse_existing and (not payload.get('extractEntities') or 'objectLabels' in payload or 'objectIds' in payload):
            raise IntelligenceValidationError('automatic extraction must include the full saved object set')
        assessment=self.store.get_assessment(assessment_id)
        if assessment is None:raise IntelligenceNotFound('assessment not found')
        if assessment.get('status')=='withdrawn':raise IntelligenceConflict('withdrawn assessment cannot create extraction jobs')
        if assessment['revision']!=payload['expectedRevision']:raise IntelligenceConflict('assessment revision changed')
        graph=self.store.get_graph(assessment_id)
        labels=payload.get('objectLabels')
        if labels is not None and (not payload.get('extractEntities') or not isinstance(labels,list) or not 1<=len(labels)<=8
                or any(not isinstance(label,str) or not 1<=len(label)<=240 or not any(label.casefold() in str(source.get('body') or '').casefold() for source in assessment['sourceSnapshot']) for label in labels)
                or len(set(labels))!=len(labels)):
            raise IntelligenceValidationError('objectLabels must select 1–8 distinct names literally present in saved source bodies')
        ids=payload.get('objectIds')
        known={n['id'] for n in (graph or {}).get('nodes',[]) if n.get('payload',{}).get('extractionMethod')=='llm'}
        if ids is not None:
            if payload.get('extractEntities') or not isinstance(ids,list) or not 1<=len(ids)<=8 or any(not isinstance(i,str) or i not in known for i in ids) or len(set(ids))!=len(ids):
                raise IntelligenceValidationError('objectIds must select 1–8 existing distinct objects for attribute extraction')
        if not payload.get('extractEntities') and not known:raise IntelligenceValidationError('extract source-backed object identities first')
        job_id='extraction-'+secrets.token_hex(12)
        stamp=now()
        progress={'phase':'queued','processedObjects':0,'totalObjects':len(ids or known) if not payload.get('extractEntities') else None,'currentObjectId':None}
        with self.lock,self.store._connect() as con:
            con.execute('BEGIN IMMEDIATE')
            if reuse_existing:
                # Query and insertion share SQLite's writer transaction, so
                # two service instances cannot enqueue two fast-finishing
                # extraction runs for the same immutable report.
                for row in con.execute('SELECT * FROM research_extraction_job WHERE assessment_id=? ORDER BY created_at DESC',(assessment_id,)):
                    request=json.loads(row['request_payload'])
                    if request.get('extractEntities') and 'objectLabels' not in request and 'objectIds' not in request:
                        if request.get('expectedRevision')!=payload['expectedRevision'] or row['source_snapshot_hash']!=self.store._snapshot_hash(assessment['sourceSnapshot']):
                            raise IntelligenceConflict('automatic extraction report or source version changed; manual retry required')
                        if row['status'] in {'completed','partial'}:
                            saved=con.execute('SELECT graph_revision_id FROM intelligence_graph_revision WHERE graph_revision_id=? AND assessment_id=?',
                                              (row['result_graph_revision_id'],assessment_id)).fetchone()
                            current=con.execute('SELECT graph_revision_id FROM intelligence_graph_revision WHERE assessment_id=? ORDER BY revision DESC LIMIT 1',
                                                (assessment_id,)).fetchone()
                            if not saved or not current or saved['graph_revision_id']!=current['graph_revision_id']:
                                raise IntelligenceConflict('automatic extraction result graph changed; manual retry required')
                        return {'job':self._public(row),'duplicate':True}
            previous=con.execute("SELECT * FROM research_extraction_job WHERE assessment_id=? AND status IN ('queued','running','interrupted')",(assessment_id,)).fetchone()
            if previous:
                if json.loads(previous['request_payload'])!=payload:raise IntelligenceConflict('an extraction job is already active for this assessment')
                return {'job':self._public(previous),'duplicate':True}
            if not getattr(self.agent.trend_provider,'configured',False):raise IntelligenceValidationError('DeepSeek is not configured')
            con.execute('INSERT INTO research_extraction_job(job_id,assessment_id,status,request_payload,base_graph_revision_id,source_snapshot_hash,progress,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)',
                        (job_id,assessment_id,'queued',encoded(payload),(graph or {}).get('graphRevisionId'),self.store._snapshot_hash(assessment['sourceSnapshot']),encoded(progress),stamp,stamp))
            con.commit()
        if start:
            try:self.start(job_id)
            except Exception:
                # Keep the durable record, but release its active-job slot
                # and expose a manual retry instead of an endless queued job.
                with self.lock:self.workers.discard(job_id)
                with self.store._connect() as con:
                    con.execute("UPDATE research_extraction_job SET status='failed',error_code='worker-start-failed',updated_at=? WHERE job_id=? AND status='queued'",(now(),job_id))
                raise
        return {'job':self.get(job_id),'duplicate':False}

    def checkpoint(self, job_id, phase, data):
        content=encoded(data)
        processed=data.get('processedObjectIds',[])
        total=len(data.get('selectedObjectIds') or data.get('nodes',[]))
        progress={'phase':phase,'processedObjects':len(processed),'totalObjects':total,'currentObjectId':data.get('currentObjectId')}
        with self.store._connect() as con:
            con.execute('BEGIN IMMEDIATE')
            con.execute('INSERT INTO research_extraction_checkpoint(job_id,phase,payload,content_hash,created_at) VALUES(?,?,?,?,?)',
                        (job_id,phase,content,hashlib.sha256(content.encode()).hexdigest(),now()))
            con.execute('UPDATE research_extraction_job SET checkpoint=?,progress=?,updated_at=? WHERE job_id=?',(content,encoded(progress),now(),job_id))
            con.commit()

    def start(self, job_id):
        with self.lock:
            if job_id in self.workers:return
            self.workers.add(job_id)
        threading.Thread(target=self.run,args=(job_id,),daemon=True,name='object-extraction-worker').start()

    @staticmethod
    def _result_status(graph):
        states=[n.get('payload',{}).get('attributeExtraction',{}).get('status') for n in graph.get('nodes',[]) if n.get('payload',{}).get('extractionMethod')=='llm']
        if any(s in {'failed','budget-skipped','skipped-budget','invalid-identity-evidence','invalid-existing-attributes'} for s in states):
            return 'partial'
        if any(n.get('payload',{}).get('attributeLinkMetadata',{}).get('status') == 'failed' for n in graph.get('nodes',[]) if n.get('type') == 'TrendHypothesis'):
            return 'partial'
        return 'completed'

    def run(self, job_id):
        try:
            with self.slots:
                with job_lease(self.store.path, job_id) as owned:
                    if not owned:
                        return
                    self._run_owned(job_id)
        finally:
            with self.lock:self.workers.discard(job_id)

    def _run_owned(self, job_id):
        try:
            with self.store._connect() as con:
                con.execute('BEGIN IMMEDIATE')
                row=con.execute('SELECT * FROM research_extraction_job WHERE job_id=?',(job_id,)).fetchone()
                if row is None or row['status'] not in {'queued','interrupted'}:return
                claimed=con.execute("UPDATE research_extraction_job SET status='running',updated_at=? WHERE job_id=? AND status IN ('queued','interrupted')",(now(),job_id))
                if claimed.rowcount!=1:return
            request=json.loads(row['request_payload'])
            request['expectedGraphRevisionId']=row['base_graph_revision_id']
            assessment=self.store.get_assessment(row['assessment_id'])
            if assessment is None or self.store._snapshot_hash(assessment['sourceSnapshot'])!=row['source_snapshot_hash']:raise IntelligenceConflict('source snapshot changed')
            restored=json.loads(row['checkpoint'])
            with self.store._connect() as con:
                saved=con.execute('SELECT payload,content_hash FROM research_extraction_checkpoint WHERE job_id=? ORDER BY checkpoint_id DESC LIMIT 1',(job_id,)).fetchone()
            if (saved and (saved['payload']!=row['checkpoint'] or hashlib.sha256(saved['payload'].encode()).hexdigest()!=saved['content_hash'])) or (not saved and row['checkpoint']!='{}'):
                raise IntelligenceValidationError('extraction checkpoint integrity failed')
            if not isinstance(restored, dict):
                raise IntelligenceValidationError('extraction checkpoint must be an object')
            current=self.store.get_graph(row['assessment_id'])
            final=restored.get('finalGraph')
            if isinstance(final,dict):
                if assessment['revision']!=request['expectedRevision'] or assessment.get('status')=='withdrawn':
                    raise IntelligenceConflict('report changed after extraction checkpoint')
                if current and current.get('graphRevisionId')!=row['base_graph_revision_id']:
                    if encoded(final)!=encoded({key:current[key] for key in ('nodes','edges')}):
                        raise IntelligenceConflict('graph changed after extraction checkpoint')
                else:
                    current=self.store.save_graph(row['assessment_id'],final,expected_revision=request['expectedRevision'],expected_graph_revision=row['base_graph_revision_id'])
                # The graph transaction committed before a process loss
                # prevented the job status update. Adopt only its exact
                # checkpointed projection; do not repeat provider calls.
                with self.store._connect() as con:
                    con.execute('UPDATE research_extraction_job SET status=?,result_graph_revision_id=?,updated_at=? WHERE job_id=?',
                                (self._result_status(current),current['graphRevisionId'],now(),job_id))
                return
            binder=getattr(self.osint,'execution_context',None)
            context=binder(task_id=assessment['taskId'],assessment_id=row['assessment_id'],stage='object-extraction',operation='extract-objects-and-attributes' if request.get('extractEntities') else 'extract-object-attributes',run_id=job_id) if callable(binder) else nullcontext()
            with context:
                graph=self.runner(row['assessment_id'],request,extraction_checkpoint=lambda phase,data:self.checkpoint(job_id,phase,data),restored_extraction=restored)
            status=self._result_status(graph)
            with self.store._connect() as con:
                con.execute('UPDATE research_extraction_job SET status=?,result_graph_revision_id=?,updated_at=? WHERE job_id=?',(status,graph.get('graphRevisionId'),now(),job_id))
        except Exception as exc:
            code='graph-or-report-version-changed' if isinstance(exc,IntelligenceConflict) else safe_error(exc)['code']
            with self.store._connect() as con:
                con.execute("UPDATE research_extraction_job SET status='failed',error_code=?,updated_at=? WHERE job_id=?",(code,now(),job_id))

    def recover(self, *, start=True):
        """Resume only jobs whose kernel lease has no active worker owner."""
        with self.store._connect() as con:
            pending=[row['job_id'] for row in con.execute("SELECT job_id FROM research_extraction_job WHERE status IN ('queued','running','interrupted') ORDER BY created_at")]
        ids=[]
        for job_id in pending:
            with job_lease(self.store.path, job_id) as owned:
                if not owned:
                    continue
                with self.store._connect() as con:
                    con.execute('BEGIN IMMEDIATE')
                    con.execute("UPDATE research_extraction_job SET status='interrupted',updated_at=? WHERE job_id=? AND status='running'",(now(),job_id))
                    row=con.execute('SELECT status FROM research_extraction_job WHERE job_id=?',(job_id,)).fetchone()
                    if row and row['status'] in {'queued','interrupted'}:
                        ids.append(job_id)
        if start:
            for job_id in ids:self.start(job_id)
        return ids
