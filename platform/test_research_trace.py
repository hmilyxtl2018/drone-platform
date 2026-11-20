"""Observed calls, attribution, redaction and additive durable storage tests."""
from __future__ import annotations

from contextlib import ExitStack
from datetime import datetime, timezone, timedelta
import importlib
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

try:
    from research_trace import execution_context, trace_call, active_execution_runs
    from osint_store import OSINTTaskStore
    from osint_collector import FirecrawlSearchProvider
    from intelligence_agent import IntelligenceAgent, DeterministicClaimProvider, DeterministicTrendProvider, DeepSeekClaimProvider
    from intelligence_store import IntelligenceStore
except ModuleNotFoundError:
    from .research_trace import execution_context, trace_call, active_execution_runs
    from .osint_store import OSINTTaskStore
    from .osint_collector import FirecrawlSearchProvider
    from .intelligence_agent import IntelligenceAgent, DeterministicClaimProvider, DeterministicTrendProvider, DeepSeekClaimProvider
    from .intelligence_store import IntelligenceStore


class ResearchTraceTests(unittest.TestCase):
    def setUp(self):
        self.stack=ExitStack()
        self.directory=Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.agent=IntelligenceAgent(IntelligenceStore(self.directory/"intelligence.sqlite"),provider=DeterministicClaimProvider(),trend_provider=DeterministicTrendProvider())
        self.store=OSINTTaskStore(self.directory/"osint.sqlite",intelligence=self.agent)

    def tearDown(self):
        self.stack.close()

    def test_events_persist_and_late_assessment_binding_preserves_call_group(self):
        with self.store.execution_context(task_id="task-a",stage="trend-analysis",operation="generate-assessment") as group:
            with trace_call(provider="deepseek",model="deepseek-v4-pro",operation="chat-completions",details={"inputCharacters":120,"headers":{"Authorization":"private"},"prompt":"private"}) as stats:
                stats.update({"outputTokens":30,"actualModel":"deepseek-v4-pro","modelConfirmed":True,"body":"private"})
            self.store.bind_execution_assessment(group.run_id,"assessment-a")
        reopened=OSINTTaskStore(self.directory/"osint.sqlite",intelligence=self.agent)
        events=reopened.list_execution_trace(assessment_id="assessment-a")
        self.assertEqual([e["status"] for e in events],["started","succeeded"])
        self.assertEqual(events[0]["callId"],events[1]["callId"])
        self.assertTrue(all(e["taskId"]=="task-a" and e["assessmentId"]=="assessment-a" for e in events))
        self.assertNotIn("private",json.dumps(events))
        self.assertEqual(events[-1]["details"]["actualModel"],"deepseek-v4-pro")
        self.assertTrue(events[-1]["details"]["modelConfirmed"])
        self.assertEqual(reopened.list_execution_trace(task_id="legacy-task"),[])
        with reopened.connect() as connection:
            with self.assertRaises(sqlite3.DatabaseError):
                connection.execute("UPDATE research_execution_event SET status='failed'")

    def test_additive_legacy_migration_does_not_invent_tool_calls(self):
        legacy=self.directory/"legacy.sqlite"
        connection=sqlite3.connect(legacy)
        try:
            connection.execute("CREATE TABLE osint_task(task_id TEXT PRIMARY KEY,title TEXT,question TEXT,keywords TEXT,source_ids TEXT,since_days INTEGER,status TEXT,steps TEXT,errors TEXT,brief TEXT,created_at TEXT,updated_at TEXT)")
            connection.execute("INSERT INTO osint_task VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",("legacy-task","Legacy research","What is documented?","[]","[]",30,"completed","[]","[]",None,"2026-09-01T00:00:00Z","2026-09-01T00:00:00Z"))
            connection.commit()
        finally:
            connection.close()
        migrated=OSINTTaskStore(legacy,intelligence=self.agent)
        self.assertEqual(migrated.get_task("legacy-task")["status"],"completed")
        self.assertEqual(migrated.get_task("legacy-task")["maxItems"],20)
        self.assertEqual(migrated.list_execution_trace(task_id="legacy-task"),[])

    def test_error_does_not_store_exception_text_or_credential_and_resets_context(self):
        secret="Bearer very-private-token"
        with self.assertRaises(HTTPError) as raised:
            with self.store.execution_context(task_id="task-error"):
                with trace_call(provider="deepseek",model="deepseek-v4-pro"):
                    raise HTTPError("https://private.invalid",401,secret,{},io.BytesIO(secret.encode()))
        raised.exception.close()
        events=self.store.list_execution_trace(task_id="task-error")
        self.assertEqual(events[-1]["status"],"failed")
        self.assertEqual(events[-1]["error"],{"code":"provider-http-error","httpStatus":401})
        self.assertNotIn(secret,json.dumps(events))
        with trace_call(provider="deepseek"):
            pass
        self.assertEqual(len(self.store.list_execution_trace(task_id="task-error")),2)

    def test_parallel_workers_cannot_share_attribution(self):
        barrier=threading.Barrier(2)
        failures=[]
        def worker(task_id):
            try:
                with self.store.execution_context(task_id=task_id,assessment_id=task_id+"-assessment"):
                    barrier.wait(timeout=3)
                    with trace_call(provider="firecrawl",details={"sourceId":task_id}):
                        barrier.wait(timeout=3)
            except Exception as error:
                failures.append(error)
        workers=[threading.Thread(target=worker,args=(name,)) for name in ("task-a","task-b")]
        for worker_thread in workers:worker_thread.start()
        for worker_thread in workers:worker_thread.join(timeout=5)
        self.assertEqual(failures,[])
        for name in ("task-a","task-b"):
            events=self.store.list_execution_trace(task_id=name)
            self.assertEqual(len(events),2)
            self.assertTrue(all(e["assessmentId"]==name+"-assessment" and e["details"]["sourceId"]==name for e in events))

    def test_nested_stage_override_reaches_transport_and_resets_after_operation(self):
        with self.store.execution_context(task_id="stage-task",stage="object-extraction"):
            with trace_call(provider="deepseek",stage="finding-attribute-links",operation="link-finding-attributes"):
                with trace_call(provider="deepseek",operation="chat-completions"):
                    pass
            with trace_call(provider="deepseek",operation="chat-completions"):
                pass
        completed=[event for event in self.store.list_execution_trace(task_id="stage-task") if event["status"]=="succeeded"]
        transports=[event for event in completed if event["operation"]=="chat-completions"]
        self.assertEqual([event["stage"] for event in transports],["finding-attribute-links","object-extraction"])

    def test_trace_read_does_not_reap_live_worker_even_after_timeout(self):
        module=importlib.import_module(trace_call.__module__)
        started=threading.Event();release=threading.Event();failures=[]
        def worker():
            try:
                with self.store.execution_context(task_id="live-worker",run_id="live-run",stage="trend-analysis"):
                    with trace_call(provider="assessment-engine",operation="generate-assessment"):
                        with trace_call(provider="deepseek",operation="chat-completions",details={"timeoutSeconds":1}):
                            started.set()
                            if not release.wait(timeout=5):raise RuntimeError("test worker was not released")
            except Exception as error:
                failures.append(error)
        with patch.object(module,"_now",return_value="2000-01-01T00:00:00Z"):
            thread=threading.Thread(target=worker)
            thread.start()
            try:
                self.assertTrue(started.wait(timeout=3))
                # A second store models a separate HTTP reader of the same
                # database. Its ContextVar has no worker context of its own.
                reader=OSINTTaskStore(self.directory/"osint.sqlite",intelligence=self.agent)
                events=reader.list_execution_trace(task_id="live-worker")
                self.assertEqual([event["status"] for event in events],["started","started"])
                self.assertIn("live-run",active_execution_runs())
            finally:
                release.set();thread.join(timeout=5)
        self.assertEqual(failures,[])
        self.assertNotIn("live-run",active_execution_runs())
        events=self.store.list_execution_trace(task_id="live-worker")
        self.assertEqual([event["status"] for event in events],["started","started","succeeded","succeeded"])

    def test_abandoned_transport_respects_complete_retry_budget_then_is_reaped(self):
        stamp=datetime.now(timezone.utc)
        def orphan(call_id,age):
            self.store.record_execution_event({"runId":"interrupted-run","callId":call_id,"taskId":"interrupted-task",
                "assessmentId":None,"stage":"capability-analysis","provider":"deepseek","model":"deepseek-v4-pro",
                "operation":"chat-completions","status":"started",
                "timestamp":(stamp-timedelta(seconds=age)).isoformat(),
                "details":{"timeoutSeconds":240,"retryLimit":1,"requestBudgetSeconds":480.5},"error":None})
        orphan("between-first-timeout-and-retry-budget",350)
        orphan("retry-budget-expired",520)
        events=self.store.list_execution_trace(task_id="interrupted-task")
        failures=[event for event in events if event["status"]=="failed"]
        self.assertEqual([event["callId"] for event in failures],["retry-budget-expired"])
        self.assertEqual(failures[0]["error"],{"code":"abandoned-call"})

    def test_nested_run_lease_and_retry_budget_cleanup_on_exception(self):
        with patch.dict("os.environ",{"DEEPSEEK_RETRY_COUNT":"1","DEEPSEEK_RETRY_BACKOFF_SECONDS":"0.5"}):
            with self.store.execution_context(task_id="lease-task",run_id="nested-live"):
                with self.store.execution_context(task_id="lease-task",run_id="nested-live"):
                    self.assertIn("nested-live",active_execution_runs())
                self.assertIn("nested-live",active_execution_runs())
                with self.assertRaises(ValueError):
                    with trace_call(provider="deepseek",operation="chat-completions",details={"timeoutSeconds":240}):
                        raise ValueError("mock failure")
        self.assertNotIn("nested-live",active_execution_runs())
        events=self.store.list_execution_trace(task_id="lease-task")
        self.assertEqual([event["status"] for event in events],["started","failed"])
        self.assertEqual(events[0]["details"]["retryLimit"],1)
        self.assertEqual(events[0]["details"]["requestBudgetSeconds"],480.5)

    def test_firecrawl_records_actual_mocked_scope_call_and_bounded_statistics(self):
        calls=[]
        secret="test-firecrawl-secret"
        def opener(request,timeout):
            calls.append(json.loads(request.data))
            return io.BytesIO(json.dumps({"success":True,"data":[{"url":"https://www.army.mil/article/12345","title":"Public program review","markdown":"Public autonomous aviation research evaluation."}]}).encode())
        provider=FirecrawlSearchProvider(api_key=secret,opener=opener)
        with self.store.execution_context(task_id="firecrawl-task",stage="collection"):
            result=provider.search("public aviation research",[],since_days=30,max_items=2,source_ids=["defense-official"],now=datetime(2026,10,7,tzinfo=timezone.utc))
        events=self.store.list_execution_trace(task_id="firecrawl-task")
        # The collector plans three bounded rounds, but skips identical query
        # expressions after the first real request. This preserves the finite
        # round budget without hammering a source with duplicate requests.
        self.assertEqual(len(calls),1)
        self.assertFalse(result["targetReached"])
        self.assertEqual(result["budget"]["searchRounds"],3)
        self.assertEqual(result["budget"]["executedRequests"],1)
        self.assertEqual([plan["status"] for plan in result["queryPlan"]],
                         ["completed","skipped-duplicate-query","skipped-duplicate-query"])
        self.assertEqual(len(events),2)
        self.assertEqual(events[-1]["status"],"succeeded")
        self.assertEqual(events[-1]["details"]["sourceId"],"defense-official")
        self.assertEqual(events[-1]["details"]["retainedItemCount"],len(result["items"]))
        self.assertGreater(events[-1]["details"]["responseBytes"],0)
        self.assertNotIn(secret,json.dumps(events))
        self.assertNotIn("markdown",json.dumps(events))

    def test_deepseek_transport_records_requested_and_actual_model_without_text(self):
        module=importlib.import_module(DeepSeekClaimProvider.__module__)
        class Opener:
            def open(self,request,timeout):
                return io.BytesIO(json.dumps({"model":"deepseek-v4-pro","choices":[{"message":{"content":"{\"claims\":[]}"}}],"usage":{"prompt_tokens":12,"completion_tokens":4}}).encode())
        provider=DeepSeekClaimProvider(api_key="private-test-key",model="deepseek-v4-pro")
        with patch.object(module.urllib.request,"build_opener",return_value=Opener()):
            with self.store.execution_context(task_id="deepseek-task",stage="object-extraction"):
                provider._request_json([{"role":"user","content":"private-source-text"}])
        events=self.store.list_execution_trace(task_id="deepseek-task")
        self.assertEqual([e["status"] for e in events],["started","succeeded"])
        self.assertEqual(events[-1]["details"]["actualModel"],"deepseek-v4-pro")
        self.assertEqual(events[-1]["details"]["inputTokens"],12)
        self.assertNotIn("private",json.dumps(events))

    def test_v4_thinking_modes_persist_only_bounded_configuration_and_actual_model(self):
        module=importlib.import_module(DeepSeekClaimProvider.__module__)
        calls=[]
        class Opener:
            def open(self,request,timeout):
                calls.append(json.loads(request.data))
                return io.BytesIO(json.dumps({"model":"deepseek-v4-pro","choices":[{"finish_reason":"stop","message":{"content":"{\"ok\":true}"}}]}).encode())
        provider=DeepSeekClaimProvider(api_key="private-test-key",model="deepseek-v4-pro")
        with patch.object(module.urllib.request,"build_opener",return_value=Opener()):
            for mode in (True,False):
                task_id="thinking-on" if mode else "thinking-off"
                with self.store.execution_context(task_id=task_id,stage="trend-analysis" if mode else "object-extraction"):
                    provider._request_json([{"role":"user","content":"private-source-text"}],thinking=mode)
                events=self.store.list_execution_trace(task_id=task_id)
                self.assertEqual(events[-1]["status"],"succeeded")
                self.assertEqual(events[-1]["details"]["thinkingMode"],"enabled-low" if mode else "disabled")
                self.assertEqual(events[-1]["details"]["actualModel"],"deepseek-v4-pro")
                self.assertTrue(events[-1]["details"]["modelConfirmed"])
                self.assertNotIn("private",json.dumps(events))
        self.assertEqual(calls[0]["thinking"],{"type":"enabled"})
        self.assertEqual(calls[0]["reasoning_effort"],"low")
        self.assertEqual(calls[1]["thinking"],{"type":"disabled"})
        self.assertNotIn("reasoning_effort",calls[1])

    def test_truncated_deepseek_json_retains_usage_and_model_in_failed_trace(self):
        module=importlib.import_module(DeepSeekClaimProvider.__module__)
        captured=[]
        class Opener:
            def open(self,request,timeout):
                captured.append(json.loads(request.data))
                # A valid provider envelope containing output cut off mid-JSON.
                return io.BytesIO(json.dumps({"model":"deepseek-v4-pro","choices":[{"finish_reason":"stop","message":{"content":"{\"nodes\":[{\"private-source-text\":"}}],"usage":{"prompt_tokens":234,"completion_tokens":8000}}).encode())
        provider=DeepSeekClaimProvider(api_key="private-test-key",model="deepseek-v4-pro")
        with patch.object(module.urllib.request,"build_opener",return_value=Opener()):
            with self.store.execution_context(task_id="truncated-task",stage="object-extraction"):
                with self.assertRaises(json.JSONDecodeError):
                    provider._request_json([{"role":"user","content":"private-source-text"}])
        self.assertEqual(captured[0]["max_tokens"],8192)
        events=self.store.list_execution_trace(task_id="truncated-task")
        self.assertEqual([event["status"] for event in events],["started","failed"])
        details=events[-1]["details"]
        self.assertEqual(details["outputTokenLimit"],8192)
        self.assertEqual(details["actualModel"],"deepseek-v4-pro")
        self.assertTrue(details["modelConfirmed"])
        self.assertEqual(details["inputTokens"],234)
        self.assertEqual(details["outputTokens"],8000)
        self.assertEqual(details["finishReason"],"stop")
        self.assertEqual(events[-1]["error"],{"code":"invalid-request-or-response"})
        self.assertNotIn("private",json.dumps(events))
        self.assertEqual(provider.last_call["actualModel"],"deepseek-v4-pro")

    def test_length_finish_cannot_be_accepted_even_when_inner_json_is_parseable(self):
        module=importlib.import_module(DeepSeekClaimProvider.__module__)
        class Opener:
            def open(self,request,timeout):
                return io.BytesIO(json.dumps({"model":"deepseek-v4-pro","choices":[{"finish_reason":"length","message":{"content":"{\"nodes\":[]}"}}],"usage":{"prompt_tokens":234,"completion_tokens":8192}}).encode())
        provider=DeepSeekClaimProvider(api_key="private-test-key",model="deepseek-v4-pro")
        with patch.object(module.urllib.request,"build_opener",return_value=Opener()):
            with self.store.execution_context(task_id="length-task",stage="object-extraction"):
                with self.assertRaises(ValueError):
                    provider._request_json([{"role":"user","content":"private-source-text"}])
        events=self.store.list_execution_trace(task_id="length-task")
        self.assertEqual(events[-1]["status"],"failed")
        self.assertEqual(events[-1]["details"]["finishReason"],"length")
        self.assertEqual(events[-1]["details"]["actualModel"],"deepseek-v4-pro")
        self.assertEqual(events[-1]["details"]["outputTokens"],8192)
        self.assertNotIn("private",json.dumps(events))

    def test_http_trace_is_readable_under_both_assessment_aliases(self):
        prefix="demo." if IntelligenceAgent.__module__.startswith("demo.") else ""
        secrets=importlib.import_module(prefix+"provider_secrets")
        agent_module=importlib.import_module(prefix+"intelligence_agent")
        runtime=importlib.import_module(prefix+"runtime_store")
        osint=importlib.import_module(prefix+"osint_store")
        with patch.object(secrets,"load_provider_environment"),patch.object(agent_module,"IntelligenceAgent",return_value=self.agent),patch.object(runtime,"RuntimeStore",return_value=object()),patch.object(osint,"OSINTTaskStore",return_value=self.store):
            serve=importlib.import_module(prefix+"serve")
        self.stack.enter_context(patch.object(serve,"INTELLIGENCE_AGENT",self.agent))
        self.stack.enter_context(patch.object(serve,"OSINT_STORE",self.store))
        self.stack.enter_context(patch.object(serve.Handler,"log_message",lambda *_args:None))
        source={"itemId":"item-public","title":"Public review","sourceUri":"https://example.test/review","sourceVersion":"v1","contentHash":"a"*64,"body":"Public research findings."}
        assessment=self.agent.create_assessment({"taskId":"trace-http-task","title":"Research","question":"What was published?","sourceSnapshot":[source]})["assessment"]
        aid=assessment["assessmentId"]
        server=serve.ThreadingHTTPServer(("127.0.0.1",0),serve.Handler)
        worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
        try:
            request=Request(f"http://127.0.0.1:{server.server_address[1]}/api/osint/assessments/{aid}/graph",data=json.dumps({"expectedRevision":1}).encode(),headers={"Content-Type":"application/json"},method="POST")
            with urlopen(request,timeout=3) as response:
                self.assertEqual(response.status,201)
            for root in ("osint","intelligence"):
                with urlopen(f"http://127.0.0.1:{server.server_address[1]}/api/{root}/assessments/{aid}/execution",timeout=3) as response:
                    payload=json.load(response)
                self.assertEqual(payload["traceCoverage"],"recorded")
                self.assertEqual(len(payload["executionTrace"]),2)
                self.assertEqual(payload["executionTrace"][-1]["stage"],"graph-linking")
                self.assertGreater(payload["executionTrace"][-1]["details"]["nodeCount"],0)
        finally:
            server.shutdown();server.server_close();worker.join(timeout=3)


if __name__=="__main__":unittest.main()
