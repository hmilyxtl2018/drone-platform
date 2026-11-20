"""Persistent task coordinator for bounded public-source OSINT collection."""
from __future__ import annotations
import hashlib, json, secrets, sqlite3, threading, os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
try:
    from osint_collector import OSINTCollector, SOURCES, FIRECRAWL_SOURCE, SEARCH_SCOPES, DEFAULT_SCOPE_IDS
    from osint_sources import public_search_scopes
    from intelligence_agent import IntelligenceAgent
    from research_trace import execution_context as bind_execution_context, trace_call, safe_details, active_execution_runs
except ModuleNotFoundError:
    from .osint_collector import OSINTCollector, SOURCES, FIRECRAWL_SOURCE, SEARCH_SCOPES, DEFAULT_SCOPE_IDS
    from .osint_sources import public_search_scopes
    from .intelligence_agent import IntelligenceAgent
    from .research_trace import execution_context as bind_execution_context, trace_call, safe_details, active_execution_runs

ROOT=Path(__file__).resolve().parent
DEFAULT_DB=ROOT.parent/"ontology"/"coast-raid-k"/"coast-raid-k-osint.sqlite"
SOURCE_BY_ID={s["id"]:dict(s) for s in [*SOURCES, *SEARCH_SCOPES, FIRECRAWL_SOURCE]}
def now(): return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00","Z")
def js(v): return json.dumps(v if v is not None else {},ensure_ascii=False,sort_keys=True,separators=(",",":"))
def dec(v):
    try:return json.loads(v)
    except (TypeError,ValueError):return v
def ident(prefix):return f"{prefix}-{secrets.token_hex(8)}"

def public_query_plan(value):
    """Keep planned public queries, never transport headers or credentials."""
    if not isinstance(value, list):return []
    allowed={"sourceId","searchType","round","requestedLimit","query","targetAnchors","relevanceGate","status","rawResultCount","retainedItemCount","excludedCounts","error"}
    return [{key:item[key] for key in allowed if key in item} for item in value[:36] if isinstance(item,dict) and item.get("sourceId") in SOURCE_BY_ID and isinstance(item.get("query"),str) and len(item["query"])<=4000]

SCHEMA="""PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS osint_task(
 task_id TEXT PRIMARY KEY,title TEXT NOT NULL,question TEXT NOT NULL,keywords TEXT NOT NULL,
 source_ids TEXT NOT NULL,since_days INTEGER NOT NULL,max_items INTEGER NOT NULL DEFAULT 20,
 trigger_mode TEXT NOT NULL DEFAULT 'manual',automation_options TEXT NOT NULL DEFAULT '{}',status TEXT NOT NULL,steps TEXT NOT NULL,
 errors TEXT NOT NULL,source_results TEXT NOT NULL DEFAULT '[]',brief TEXT,
 requested_by TEXT NOT NULL DEFAULT 'operator',created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
 started_at TEXT,completed_at TEXT,archived_at TEXT,archived_reason TEXT);
CREATE TABLE IF NOT EXISTS osint_item(
 item_id TEXT PRIMARY KEY,task_id TEXT NOT NULL REFERENCES osint_task(task_id),
 title TEXT NOT NULL,source_id TEXT NOT NULL,source_name TEXT NOT NULL,source_uri TEXT NOT NULL,
 item_uri TEXT,source_feed_uri TEXT,source_version TEXT,published_at TEXT,category TEXT,
 body TEXT NOT NULL,content_hash TEXT NOT NULL,matched_keywords TEXT NOT NULL,
 provenance TEXT NOT NULL DEFAULT '{}',review_state TEXT NOT NULL DEFAULT 'pending-review',created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS osint_item_task_idx ON osint_item(task_id,created_at);
CREATE INDEX IF NOT EXISTS osint_item_source_time_idx ON osint_item(source_id,published_at);
CREATE INDEX IF NOT EXISTS osint_item_hash_idx ON osint_item(content_hash);
CREATE TABLE IF NOT EXISTS research_execution_run(
 run_id TEXT PRIMARY KEY,task_id TEXT,assessment_id TEXT,created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS research_execution_task_idx ON research_execution_run(task_id);
CREATE INDEX IF NOT EXISTS research_execution_assessment_idx ON research_execution_run(assessment_id);
CREATE TABLE IF NOT EXISTS research_execution_event(
 event_id INTEGER PRIMARY KEY AUTOINCREMENT,run_id TEXT NOT NULL REFERENCES research_execution_run(run_id),
 call_id TEXT NOT NULL,stage TEXT NOT NULL,provider TEXT NOT NULL,model TEXT,operation TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('started','succeeded','failed')),timestamp TEXT NOT NULL,
 details TEXT NOT NULL DEFAULT '{}',error TEXT);
CREATE INDEX IF NOT EXISTS research_execution_run_events_idx ON research_execution_event(run_id,event_id);
CREATE TRIGGER IF NOT EXISTS research_execution_event_no_update BEFORE UPDATE ON research_execution_event BEGIN SELECT RAISE(ABORT,'execution events are immutable'); END;
CREATE TRIGGER IF NOT EXISTS research_execution_event_no_delete BEFORE DELETE ON research_execution_event BEGIN SELECT RAISE(ABORT,'execution events are retained'); END;
CREATE TRIGGER IF NOT EXISTS osint_task_no_delete BEFORE DELETE ON osint_task BEGIN SELECT RAISE(ABORT,'OSINT tasks are retained'); END;
CREATE TRIGGER IF NOT EXISTS osint_item_no_update BEFORE UPDATE ON osint_item BEGIN SELECT RAISE(ABORT,'OSINT items are immutable'); END;
CREATE TRIGGER IF NOT EXISTS osint_item_no_delete BEFORE DELETE ON osint_item BEGIN SELECT RAISE(ABORT,'OSINT items are retained'); END;
"""

class OSINTStoreError(RuntimeError): pass
class OSINTNotFound(OSINTStoreError): pass
class OSINTValidationError(OSINTStoreError): pass


class _Connection(sqlite3.Connection):
    """Close SQLite handles when a connection is used as a context manager.

    ``sqlite3.Connection.__exit__`` commits or rolls back but deliberately
    leaves the handle open.  The coordinator opens short lived connections
    from background threads; closing them here prevents Windows from holding
    the task database open after a test or process shutdown.
    """

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()

class OSINTTaskStore:
    def __init__(self,path=None,collector=None,intelligence=None):
        self.path=Path(path or DEFAULT_DB).expanduser()
        if not self.path.is_absolute(): self.path=(ROOT.parent/self.path).resolve()
        self.path.parent.mkdir(parents=True,exist_ok=True); self.lock=threading.RLock()
        self.collector=collector or OSINTCollector()
        self.intelligence=intelligence or IntelligenceAgent()
        with self.connect() as c:
            c.executescript(SCHEMA)
            self._migrate(c)
    def connect(self):
        c=sqlite3.connect(str(self.path),timeout=30,isolation_level=None,factory=_Connection)
        c.row_factory=sqlite3.Row;c.execute("PRAGMA foreign_keys=ON");c.execute("PRAGMA busy_timeout=30000");return c
    @staticmethod
    def _migrate(c):
        """Add DDL-aligned columns to databases created by the 12-column demo.

        SQLite cannot add a required column without a default to a populated
        table.  Legacy rows therefore receive a safe empty/default value and
        all new writes use the complete explicit column list below.
        """
        task_columns={row[1] for row in c.execute("PRAGMA table_info(osint_task)")}
        task_additions={
            "max_items": "INTEGER NOT NULL DEFAULT 20",
            "trigger_mode": "TEXT NOT NULL DEFAULT 'manual'",
            "automation_options": "TEXT NOT NULL DEFAULT '{}'",
            "source_results": "TEXT NOT NULL DEFAULT '[]'",
            "requested_by": "TEXT NOT NULL DEFAULT 'operator'",
            "started_at": "TEXT",
            "completed_at": "TEXT",
            "archived_at": "TEXT",
            "archived_reason": "TEXT",
        }
        for name,definition in task_additions.items():
            if name not in task_columns:
                c.execute(f"ALTER TABLE osint_task ADD COLUMN {name} {definition}")
        item_columns={row[1] for row in c.execute("PRAGMA table_info(osint_item)")}
        item_additions={
            "item_uri": "TEXT",
            "source_feed_uri": "TEXT",
            "category": "TEXT",
            "provenance": "TEXT NOT NULL DEFAULT '{}'",
            "review_state": "TEXT NOT NULL DEFAULT 'pending-review'",
        }
        for name,definition in item_additions.items():
            if name not in item_columns:
                c.execute(f"ALTER TABLE osint_item ADD COLUMN {name} {definition}")
    def _row(self,row,include_items=True):
        if row is None:return None
        stored_status=row["status"]
        stored_errors=dec(row["errors"])
        brief=dec(row["brief"]) if row["brief"] else None
        # A bounded search can exhaust its search budget with usable evidence
        # while falling short of the requested item count.  That is a
        # completed collection with a coverage gap, not a failed/partial
        # execution.  Normalize legacy rows here as well so old tasks stop
        # contradicting their completed source branches in the UI.
        target_gap=bool(isinstance(brief,dict) and isinstance(brief.get("collectionBudget"),dict)
                        and brief["collectionBudget"].get("targetReached") is False)
        only_target_warning=bool(stored_errors) and all(isinstance(item,dict) and item.get("code")=="material-target-not-met"
                                                         for item in stored_errors)
        normalized_status="completed" if stored_status=="partial" and target_gap and only_target_warning else stored_status
        steps=dec(row["steps"])
        if target_gap and isinstance(steps,list):
            steps=[dict(step, status="done", message=(str(step.get("message") or "").replace("目标 ","材料目标 ").replace("目标未满足","材料目标未满足") or "自动检索已结束，材料目标未满足"))
                   if isinstance(step,dict) and step.get("status")=="partial" else step for step in steps]
        with self.connect() as item_conn:
            item_count=item_conn.execute("SELECT COUNT(*) FROM osint_item WHERE task_id=?",(row["task_id"],)).fetchone()[0]
        completion={
            "state": "completed-with-gap" if target_gap and normalized_status in {"completed","succeeded"} else normalized_status,
            "label": "已完成 · 材料目标未满足" if target_gap and normalized_status in {"completed","succeeded"} else None,
            "targetReached": False if target_gap else None,
            "collected": item_count,
            "target": row["max_items"],
        }
        out={"id":row["task_id"],"type":"OSINTResearchTask","title":row["title"],"question":row["question"],
             "keywords":dec(row["keywords"]), "sourceIds":dec(row["source_ids"]), "sinceDays":row["since_days"],
             "maxItems":row["max_items"],"triggerMode":row["trigger_mode"],"workflowMode":row["trigger_mode"],"automation":dec(row["automation_options"]),"requestedBy":row["requested_by"],
             "status":normalized_status,"steps":steps,"errors":stored_errors,
             "sourceResults":dec(row["source_results"]),"brief":brief,"completion":completion,
             "createdAt":row["created_at"],"updatedAt":row["updated_at"],"startedAt":row["started_at"],"completedAt":row["completed_at"],
             "archivedAt":row["archived_at"],"archivedReason":row["archived_reason"]}
        if include_items:
            out["items"]=self._items_for_task(row["task_id"])
        return out
    def _items_for_task(self, task_id):
        with self.connect() as c:
            return [self._item(item) for item in c.execute("SELECT * FROM osint_item WHERE task_id=? ORDER BY published_at DESC,created_at DESC",(task_id,))]
    @staticmethod
    def _item(row):
        provenance=dec(row["provenance"])
        return {"id":row["item_id"],"type":"OSINTSourceItem","taskId":row["task_id"],"title":row["title"],"sourceId":row["source_id"],"sourceName":row["source_name"],"sourceUri":row["source_uri"],"itemUri":row["item_uri"],"sourceFeedUri":row["source_feed_uri"] or row["source_uri"],"sourceVersion":row["source_version"],"publishedAt":row["published_at"],"category":row["category"],"body":row["body"],"contentHash":row["content_hash"],"matchedKeywords":dec(row["matched_keywords"]),"provenance":provenance,"reviewState":row["review_state"],"createdAt":row["created_at"], **{key:provenance[key] for key in ("sourceCategory","sourceRole","contentNature","publisherDomain","verificationStatus","sourceReliability","classificationBasis","documentTypeVerified") if isinstance(provenance,dict) and key in provenance}}
    def list_tasks(self,limit=20,*,include_archived=False):
        query="SELECT * FROM osint_task"+("" if include_archived else " WHERE archived_at IS NULL")+" ORDER BY created_at DESC LIMIT ?"
        with self.connect() as c:return [self._row(r,include_items=True) for r in c.execute(query,(max(1,min(int(limit),100)),))]
    def archive_history(self,reason=None):
        """Clear completed task history without deleting retained source evidence.

        The write lock makes the active-task check and archive one operation.
        New tasks created afterward remain visible; internal task lookups keep
        resolving archived records used by published knowledge and audit trails.
        """
        reason=str(reason or "History cleared by operator").strip()[:1000] or "History cleared by operator"
        stamp=now()
        with self.lock,self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            active=c.execute("SELECT task_id FROM osint_task WHERE archived_at IS NULL AND LOWER(status) IN ('queued','running','processing','generating') LIMIT 1").fetchone()
            if active:
                raise OSINTValidationError("cannot clear task history while collection is active")
            count=c.execute("UPDATE osint_task SET archived_at=?,archived_reason=? WHERE archived_at IS NULL",(stamp,reason)).rowcount
        return {"archivedCount":count,"archivedAt":stamp,"reason":reason}
    def purge_history(self, reason=None):
        """Permanently remove all OSINT tasks and their unreviewed research data.

        This is an operator-only reset for disposable validation runs. It
        removes tasks, collected items, execution traces, assessments and
        extraction jobs, while leaving curated source configuration intact.
        """
        with self.lock,self.connect() as c:
            c.execute("PRAGMA foreign_keys=OFF")
            c.execute("BEGIN IMMEDIATE")
            task_ids=[row[0] for row in c.execute("SELECT task_id FROM osint_task").fetchall()]
            task_set=set(task_ids)
            has_assessments=bool(c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='intelligence_assessment'").fetchone())
            assessment_ids=[row[0] for row in c.execute("SELECT assessment_id FROM intelligence_assessment WHERE task_id IN (%s)" % ','.join('?'*len(task_ids),), task_ids).fetchall()] if task_ids and has_assessments else []
            names=[row[0] for row in c.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND (name LIKE '%no_delete' OR name LIKE '%retained')").fetchall()]
            for name in names:c.execute('DROP TRIGGER IF EXISTS "'+name.replace('"','""')+'"')
            table_rows=c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()
            deleted=0
            for (table,) in table_rows:
                cols={row[1] for row in c.execute(f'PRAGMA table_info("{table}")')}
                if 'task_id' in cols and task_ids:
                    deleted+=c.execute(f'DELETE FROM "{table}" WHERE task_id IN ({','.join('?'*len(task_ids))})',task_ids).rowcount
                elif 'assessment_id' in cols and assessment_ids:
                    deleted+=c.execute(f'DELETE FROM "{table}" WHERE assessment_id IN ({','.join('?'*len(assessment_ids))})',assessment_ids).rowcount
            if task_ids:
                deleted+=c.execute("DELETE FROM osint_item WHERE task_id IN (%s)" % ','.join('?'*len(task_ids)),task_ids).rowcount
                deleted+=c.execute("DELETE FROM osint_task WHERE task_id IN (%s)" % ','.join('?'*len(task_ids)),task_ids).rowcount
            c.commit()
            c.executescript(SCHEMA)
            c.execute("PRAGMA foreign_keys=ON")
        return {"purgedTaskCount":len(task_ids),"purgedAssessmentCount":len(assessment_ids),"deletedRowCount":deleted,"reason":str(reason or "Operator requested permanent reset")[:1000]}
    def get_task(self,task_id):
        with self.connect() as c:return self._row(c.execute("SELECT * FROM osint_task WHERE task_id=?",(str(task_id),)).fetchone(),True)
    def execution_context(self, *, task_id=None, assessment_id=None, stage="collection", operation="research", run_id=None):
        """Bind explicitly inside the worker/request; ContextVars do not leak threads."""
        return bind_execution_context(self.record_execution_event, task_id=task_id, assessment_id=assessment_id, stage=stage, operation=operation, run_id=run_id)
    def record_execution_event(self, event):
        with self.lock,self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            c.execute("INSERT INTO research_execution_run(run_id,task_id,assessment_id,created_at) VALUES(?,?,?,?) ON CONFLICT(run_id) DO NOTHING", (event["runId"],event.get("taskId"),event.get("assessmentId"),event["timestamp"]))
            c.execute("INSERT INTO research_execution_event(run_id,call_id,stage,provider,model,operation,status,timestamp,details,error) VALUES(?,?,?,?,?,?,?,?,?,?)", (event["runId"],event["callId"],event["stage"],event["provider"],event.get("model"),event["operation"],event["status"],event["timestamp"],js(safe_details(event.get("details"))),js(event["error"]) if event.get("error") else None))
    def bind_execution_assessment(self, run_id, assessment_id):
        """Associate the call group after the newly generated draft gets its ID."""
        with self.lock,self.connect() as c:
            row=c.execute("SELECT assessment_id FROM research_execution_run WHERE run_id=?",(run_id,)).fetchone()
            if row and row["assessment_id"] not in (None,assessment_id):
                raise OSINTValidationError("execution group already belongs to another assessment")
            c.execute("UPDATE research_execution_run SET assessment_id=? WHERE run_id=? AND assessment_id IS NULL",(str(assessment_id),run_id))
    def list_execution_trace(self, *, task_id=None, assessment_id=None, limit=1000):
        if not task_id and not assessment_id:
            raise OSINTValidationError("taskId or assessmentId is required for execution trace")
        self.recover_stale_execution_calls(task_id=task_id, assessment_id=assessment_id)
        clauses=[];params=[]
        for column,value in (("task_id",task_id),("assessment_id",assessment_id)):
            if value:
                clauses.append(f"r.{column}=?");params.append(str(value))
        params.append(max(1,min(int(limit),5000)))
        with self.connect() as c:
            rows=c.execute("SELECT e.*,r.task_id,r.assessment_id FROM research_execution_event e JOIN research_execution_run r ON r.run_id=e.run_id WHERE "+" AND ".join(clauses)+" ORDER BY e.event_id LIMIT ?",params).fetchall()
        return [{"id":f"execution-event-{row['event_id']}","callId":row["call_id"],"runId":row["run_id"],"taskId":row["task_id"],"assessmentId":row["assessment_id"],"stage":row["stage"],"provider":row["provider"],"model":row["model"],"operation":row["operation"],"status":row["status"],"timestamp":row["timestamp"],"details":dec(row["details"]),"error":dec(row["error"]) if row["error"] else None} for row in rows]
    def recover_stale_execution_calls(self, *, task_id=None, assessment_id=None, max_age_seconds=180):
        """Close expired calls only after their process-local run lease ends.

        Reading an execution trace must not mark a live background worker as
        abandoned. Leases vanish on restart, so genuinely interrupted calls
        remain reclaimable after their complete timeout/retry budget.
        """
        clauses=[];params=[]
        if task_id: clauses.append("r.task_id=?");params.append(str(task_id))
        if assessment_id: clauses.append("r.assessment_id=?");params.append(str(assessment_id))
        if not clauses:return 0
        now_dt=datetime.now(timezone.utc); recovered=0
        live_runs=active_execution_runs()
        with self.lock,self.connect() as c:
            rows=c.execute("SELECT e.*,r.task_id,r.assessment_id FROM research_execution_event e JOIN research_execution_run r ON r.run_id=e.run_id WHERE "+" AND ".join(clauses)+" ORDER BY e.event_id",params).fetchall()
            terminal={(row['run_id'],row['call_id']) for row in rows if row['status'] in {'succeeded','failed'}}
            for row in rows:
                key=(row['run_id'],row['call_id'])
                if row['status']!='started' or key in terminal or row['run_id'] in live_runs:continue
                try:started=datetime.fromisoformat(str(row['timestamp']).replace('Z','+00:00'))
                except ValueError:continue
                details=dec(row['details']) if isinstance(row['details'],str) else {}
                age=(now_dt-started).total_seconds(); timeout=float(details.get('timeoutSeconds') or max_age_seconds)
                request_budget=float(details.get('requestBudgetSeconds') or timeout)
                if age < max(max_age_seconds,request_budget+30):continue
                c.execute("INSERT INTO research_execution_event(run_id,call_id,stage,provider,model,operation,status,timestamp,details,error) VALUES(?,?,?,?,?,?,?,?,?,?)",(row['run_id'],row['call_id'],row['stage'],row['provider'],row['model'],row['operation'],'failed',now(),js({**details,'recovery':'stale-call-reaper','ageSeconds':round(age)}),js({'code':'abandoned-call'})))
                recovered+=1
        return recovered
    def _update(self,task_id,status=None,steps=None,errors=None,source_results=None,brief=None):
        with self.lock,self.connect() as c:
            old=c.execute("SELECT * FROM osint_task WHERE task_id=?",(task_id,)).fetchone()
            if old is None:raise OSINTNotFound(f"task not found: {task_id}")
            stamp=now()
            started=stamp if status=="running" and not old["started_at"] else old["started_at"]
            completed=stamp if status in {"completed","succeeded","partial","failed","cancelled"} else old["completed_at"]
            c.execute("UPDATE osint_task SET status=COALESCE(?,status),steps=COALESCE(?,steps),errors=COALESCE(?,errors),source_results=COALESCE(?,source_results),brief=COALESCE(?,brief),updated_at=?,started_at=?,completed_at=? WHERE task_id=?",(status,js(steps) if steps is not None else None,js(errors) if errors is not None else None,js(source_results) if source_results is not None else None,js(brief) if brief is not None else None,stamp,started,completed,task_id))
    def create_task(self,payload):
        title=str(payload.get("title") or "").strip(); question=str(payload.get("question") or "").strip()
        if not title or not question:raise OSINTValidationError("title and question are required")
        ids=payload.get("sourceIds"); ids=list(dict.fromkeys(str(x) for x in ids)) if isinstance(ids,list) else []
        if not ids:raise OSINTValidationError("at least one curated source is required")
        missing=[x for x in ids if x not in SOURCE_BY_ID]
        if missing:raise OSINTValidationError(f"source is not in curated allowlist: {missing}")
        try:since=int(payload.get("sinceDays",30))
        except (TypeError,ValueError) as exc:raise OSINTValidationError("sinceDays must be an integer") from exc
        if since<1 or since>365:raise OSINTValidationError("sinceDays must be between 1 and 365")
        keys=[str(x).strip() for x in (payload.get("keywords") or []) if str(x).strip()][:20]
        try:max_items=int(payload.get("maxItems",20))
        except (TypeError,ValueError) as exc:raise OSINTValidationError("maxItems must be an integer") from exc
        if max_items<1 or max_items>100:raise OSINTValidationError("maxItems must be between 1 and 100")
        requested_by=str(payload.get("requestedBy") or "operator").strip()[:200] or "operator"
        workflow_mode=str(payload.get("workflowMode") or payload.get("triggerMode") or "manual").strip().casefold()
        if workflow_mode not in {"manual","auto"}:
            raise OSINTValidationError("workflowMode must be manual or auto")
        requested_automation=payload.get("automation")
        if requested_automation is not None and not isinstance(requested_automation,dict):
            raise OSINTValidationError("automation must be an object")
        source_quotas=payload.get("sourceQuotas")
        if source_quotas is not None:
            if not isinstance(source_quotas,dict):
                raise OSINTValidationError("sourceQuotas must be an object")
            source_quotas={str(k):int(v) for k,v in source_quotas.items()}
            if any(v<0 or v>max_items for v in source_quotas.values()):
                raise OSINTValidationError("sourceQuotas values must be between 0 and maxItems")
            unknown_quota=set(source_quotas)-set(ids)-set(str(SOURCE_BY_ID.get(i,{}).get("sourceRole") or "") for i in ids)
            if unknown_quota:
                raise OSINTValidationError(f"source quota is not selected: {sorted(unknown_quota)}")
        automation={
            "autoSelectMaterials": workflow_mode == "auto",
            "autoStartAssessment": workflow_mode == "auto",
            "autoRunEvidenceCheck": False,
            "autoRunModelCritique": False,
            "autoCreateAdmissionCandidates": False,
        }
        for key in list(automation):
            if isinstance(requested_automation,dict) and key in requested_automation:
                if not isinstance(requested_automation[key],bool):
                    raise OSINTValidationError(f"automation.{key} must be boolean")
                automation[key]=requested_automation[key]
        if source_quotas:
            automation["sourceQuotas"]=source_quotas
        if workflow_mode == "manual":
            automation={key:False for key in automation}
        task_id=ident("osint"); stamp=now();steps=[{"label":"任务排队","status":"queued","message":"等待采集线程"},{"label":"读取公开来源","status":"queued","message":"尚未开始"},{"label":"去重与关键词匹配","status":"queued","message":"尚未开始"},{"label":"生成待核验材料","status":"queued","message":"尚未开始"}]
        with self.lock,self.connect() as c:
            c.execute("INSERT INTO osint_task (task_id,title,question,keywords,source_ids,since_days,max_items,trigger_mode,automation_options,status,steps,errors,source_results,brief,requested_by,created_at,updated_at,started_at,completed_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(task_id,title,question,js(keys),js(ids),since,max_items,workflow_mode,js(automation),"queued",js(steps),js([]),js([]),None,requested_by,stamp,stamp,None,None))
        threading.Thread(target=self._run,args=(task_id,title,question,ids,keys,since,max_items,source_quotas or {}),daemon=True,name=f"osint-{task_id}").start()
        return self.get_task(task_id)
    def _run(self,task_id,title,question,ids,keys,since,max_items,source_quotas=None):
        # Bind inside the background thread, not the originating HTTP thread.
        # The worker also records its own stages when a fixture/RSS collector
        # is used; it never labels those calls as Firecrawl.
        try:
            with self.execution_context(task_id=task_id,stage="collection",operation="collect-materials"):
                with trace_call(provider="osint-coordinator",details={"sourceScopes":ids,"requestedLimit":max_items,"sinceDays":since}) as trace:
                    result=self._run_collection(task_id,title,question,ids,keys,since,max_items,source_quotas or {})
                    trace.update(result)
                    if result["resultMode"]=="failed":
                        raise OSINTStoreError("collection completed without usable evidence")
        except Exception:
            # _run_collection already persists task failure and safe provider
            # events. Keep the daemon's exception out of terminal output.
            task=self.get_task(task_id)
            if task and task["status"] in {"queued","running"}:
                self._update(task_id,"failed",errors=[{"sourceId":"execution","error":"采编未完成，执行记录或采集阶段失败；请检查服务日志后重试。"}])
            return
    def _run_collection(self,task_id,title,question,ids,keys,since,max_items,source_quotas=None):
        steps=[{"label":"任务排队","status":"done","message":"采集任务已启动"},{"label":"读取公开来源","status":"running","message":"读取允许的 RSS/Atom 来源"},{"label":"去重与关键词匹配","status":"queued","message":"等待来源结果"},{"label":"生成待核验材料","status":"queued","message":"等待来源结果"}]
        if "firecrawl-search" in ids or any(x in DEFAULT_SCOPE_IDS or SOURCE_BY_ID.get(x,{}).get("engine")=="firecrawl" for x in ids) or (getattr(self.collector, "search_provider", None) and os.environ.get("CRK_SEARCH_PROVIDER", "").casefold()!="rss"):
            steps[1] = {"label":"Firecrawl 来源组检索","status":"running","message":"按所选公开发布者域名检索，过滤越界结果；总条数预算不会按组倍增"}
        self._update(task_id,"running",steps=steps)
        try:
            if isinstance(self.collector, OSINTCollector):
                result=self.collector.collect(ids,keys,since_days=since,max_items=max_items,question=question,ensure_target=True,source_quotas=source_quotas or {})
            else:
                result=self.collector.collect(ids,keys,since_days=since,max_items=max_items)
            errors=list(result.get("errors") or []);raw=result.get("items") or []
            engine = "Firecrawl 来源组检索" if result.get("collector")=="firecrawl-search" else "读取公开来源"
            target_unmet=result.get("collector")=="firecrawl-search" and result.get("targetReached") is False
            rounds=result.get("collectionRounds") or (result.get("budget") or {}).get("searchRounds")
            if target_unmet:
                errors.append({"sourceId":"collector","code":"material-target-not-met","error":f"已完成 {rounds or '有限'} 轮检索，保留 {len(raw)} / {max_items} 条材料；已达到循环预算上限。"})
            hard_errors=[item for item in errors if not (isinstance(item,dict) and item.get("code")=="material-target-not-met")]
            steps[1]={"label":engine,"status":"done" if raw or not hard_errors else "failed","message":f"发现 {len(raw)} 条公开材料"+(f" · 已循环 {rounds} 轮，材料目标未满足" if target_unmet else "")}
            steps[2]={"label":"去重与关键词匹配","status":"running","message":"正在整理来源摘要"};self._update(task_id,"running",steps=steps,errors=errors,source_results=result.get("sourceResults") or [])
            unique=[];seen=set()
            with trace_call(provider="osint-coordinator",operation="deduplicate-materials",details={"rawResultCount":len(raw)}) as trace:
                for item in raw:
                    if item["contentHash"] in seen:continue
                    seen.add(item["contentHash"]);unique.append(item)
                trace.update({"retainedItemCount":len(unique),"removedDuplicateCount":len(raw)-len(unique)})
            with self.lock,self.connect() as c:
                for item in unique:
                    item_id=ident("item")
                    article_uri=item.get("itemUri") or item.get("sourceUri")
                    feed_uri=item.get("sourceUri")
                    source_meta=SOURCE_BY_ID.get(item["sourceId"], {})
                    provenance=dict(item.get("provenance") or {}, collector=(item.get("provenance") or {}).get("collector", "bounded-rss-atom"), sourceId=item["sourceId"], sourceFeedUri=feed_uri)
                    body = (item.get("body") or "") if provenance.get("retrievalKind")=="search-discovery-only" or provenance.get("contentKind")=="image-search-metadata" else (item.get("body") or item.get("title") or "")
                    c.execute("""INSERT INTO osint_item
                        (item_id,task_id,title,source_id,source_name,source_uri,item_uri,source_feed_uri,source_version,published_at,category,body,content_hash,matched_keywords,provenance,review_state,created_at)
                        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (item_id,task_id,item.get("title") or "未命名材料",item["sourceId"],item["sourceName"],article_uri,item.get("itemUri"),feed_uri,item.get("sourceVersion"),item.get("publishedAt"),item.get("category") or source_meta.get("category"),body,item["contentHash"],js(item.get("matchedKeywords") or []),js(provenance),"pending-review",now()))
            steps[2]={"label":"去重与关键词匹配","status":"done","message":f"保留 {len(unique)} 条独立材料"}
            citations=[{"title":item.get("title"),"url":item.get("itemUri") or item.get("sourceUri")} for item in unique[:8]]
            lines=[f"来源材料，待分析核验：{item.get('title')}\n{item.get('body','')[:500]}" for item in unique[:8]]
            brief={"title":"公开来源材料摘要（待核验）","method":"extractive","text":"\n\n".join(lines) or "本次采集没有匹配到材料。","citations":citations,"collectionBudget":result.get("budget"),"queryPlan":public_query_plan(result.get("queryPlan")),"collector":result.get("collector") or "bounded-rss-atom","selectedScopes":result.get("selectedScopes") or ids}
            steps[3]={"label":"生成待核验材料","status":"done","message":"保留原文摘录与来源链接，等待人工核验"}
            # Target shortfall is retained as a warning in errors/brief, but
            # does not downgrade an otherwise successful automated run.
            final="partial" if hard_errors and unique else ("failed" if hard_errors and not unique else "completed")
            self._update(task_id,final,steps=steps,errors=errors,brief=brief)
            return {"rawResultCount":len(raw),"retainedItemCount":len(unique),"errorCount":len(errors),"resultMode":final}
        except Exception as exc:
            steps[1]={"label":"读取公开来源","status":"failed","message":str(exc)[:300]};self._update(task_id,"failed",steps=steps,errors=[{"sourceId":"collector","error":str(exc)[:500]}])
            raise
    def handoff(self,task_id,item_ids):
        with self.execution_context(task_id=task_id,stage="object-extraction",operation="propose-source-claims"):
            with trace_call(provider="ontology-engine",details={"sourceCount":len(item_ids)}) as trace:
                result=self._handoff(task_id,item_ids)
                trace.update({"candidateCount":result["candidateCount"]})
                return result
    def _handoff(self,task_id,item_ids):
        task=self.get_task(task_id)
        if task is None:raise OSINTNotFound(f"task not found: {task_id}")
        if task["status"] not in {"completed","partial"}:raise OSINTValidationError("task is not ready for evidence review")
        chosen=[item for item in task["items"] if item["id"] in set(str(x) for x in item_ids)]
        if not chosen:raise OSINTValidationError("select at least one discovered item")
        docs=[];count=0
        for item in chosen:
            reliability=(item.get("provenance") or {}).get("sourceReliability", "unknown")
            result=self.intelligence.ingest({"title":item["title"],"sourceUri":item["sourceUri"],"sourceVersion":item.get("sourceVersion") or item["contentHash"][:16],"body":item["body"],"accessBasis":"public","sourceReliability":reliability if reliability in {"primary","secondary","unknown"} else "unknown"})
            docs.append(result["document"]);count+=len(result.get("candidates") or [])
        return {"documents":docs,"candidateCount":count,"reviewUrl":"/intelligence.html?api"}
    def create_assessment(self, task_id, payload):
        """Generate a trend draft from immutable OSINT items selected by id.

        The browser may select item IDs and a horizon, but cannot supply or
        rewrite source text/citations.  All source metadata is reconstructed
        from this store before invoking the intelligence provider.
        """
        task = self.get_task(task_id)
        if task is None:
            raise OSINTNotFound(f"task not found: {task_id}")
        if task["status"] not in {"completed", "partial"}:
            raise OSINTValidationError("task is not ready for trend assessment")
        item_ids = payload.get("itemIds") if isinstance(payload, dict) else None
        if item_ids is None:
            selected = list(task["items"])
        else:
            if not isinstance(item_ids, list) or not item_ids:
                raise OSINTValidationError("itemIds must be a non-empty array when supplied")
            selected = [item for item in task["items"] if item["id"] in {str(x) for x in item_ids}]
        if not selected:
            raise OSINTValidationError("select at least one discovered item")
        try:
            horizon = int(payload.get("horizonYears", 5))
        except (TypeError, ValueError) as exc:
            raise OSINTValidationError("horizonYears must be an integer") from exc
        if horizon < 1 or horizon > 30:
            raise OSINTValidationError("horizonYears must be between 1 and 30")
        snapshot = [{
            "itemId": item["id"], "title": item["title"], "sourceId": item["sourceId"],
            "sourceName": item["sourceName"], "sourceUri": item["sourceUri"], "itemUri": item.get("itemUri"),
            "sourceVersion": item.get("sourceVersion") or item["contentHash"][:16], "publishedAt": item.get("publishedAt"),
            "body": item["body"], "contentHash": item["contentHash"], "matchedKeywords": item.get("matchedKeywords") or [],
            "category": item.get("category"), "provenance": item.get("provenance") or {},
            **{key:item[key] for key in ("sourceCategory","sourceRole","contentNature","publisherDomain","verificationStatus","sourceReliability","classificationBasis","documentTypeVerified") if key in item},
        } for item in selected]
        with self.execution_context(task_id=task_id,stage="trend-analysis",operation="generate-assessment") as context:
            with trace_call(provider="assessment-engine",details={"sourceCount":len(snapshot),"horizonYears":horizon}) as trace:
                result=self.intelligence.create_assessment({
                    "taskId": task_id, "title": str(payload.get("title") or task["title"]),
                    "question": str(payload.get("question") or task["question"]), "horizonYears": horizon,
                    "sourceSnapshot": snapshot,
                })
                assessment=result["assessment"]
                self.bind_execution_assessment(context.run_id,assessment["assessmentId"])
                trace.update({"resultMode":assessment.get("provider") or "unknown","findingCount":len((assessment.get("summary") or {}).get("findings") or [])})
        return result
def public_sources():return [dict(s) for s in SOURCES] if os.environ.get("CRK_SEARCH_PROVIDER", "").casefold()=="rss" else public_search_scopes()
__all__=["OSINTTaskStore","OSINTStoreError","OSINTNotFound","OSINTValidationError","public_sources","SOURCE_BY_ID"]
