"""Frozen object-directed collection plans and exact cross-task imagery links."""
from copy import deepcopy
import hashlib
import importlib
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

try:
    from intelligence_agent import IntelligenceAgent, DeterministicClaimProvider, DeterministicTrendProvider
    from intelligence_store import IntelligenceStore, IntelligenceConflict, IntelligenceValidationError
    from imagery_evidence import ImageryEvidenceRegistry
    from osint_store import OSINTTaskStore, OSINTValidationError
    from research_followups import ResearchFollowups
    from osint_collector import FirecrawlSearchProvider
    from osint_sources import SEARCH_SCOPE_BY_ID
except ModuleNotFoundError:
    from .intelligence_agent import IntelligenceAgent, DeterministicClaimProvider, DeterministicTrendProvider
    from .intelligence_store import IntelligenceStore, IntelligenceConflict, IntelligenceValidationError
    from .imagery_evidence import ImageryEvidenceRegistry
    from .osint_store import OSINTTaskStore, OSINTValidationError
    from .research_followups import ResearchFollowups
    from .osint_collector import FirecrawlSearchProvider
    from .osint_sources import SEARCH_SCOPE_BY_ID


class BoundedCollector:
    def __init__(self):
        self.calls = []
        self.lock = threading.Lock()

    def collect(self, source_ids, keywords, *, since_days, max_items):
        with self.lock:
            self.calls.append({"sourceIds": source_ids, "keywords": keywords, "sinceDays": since_days, "maxItems": max_items})
        return {"items": [], "errors": [], "sourceResults": [{"sourceId": sid, "status": "ok", "itemCount": 0} for sid in source_ids]}


class ResearchFollowupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.store = IntelligenceStore(root / "intelligence.sqlite")
        self.agent = IntelligenceAgent(self.store, provider=DeterministicClaimProvider(), trend_provider=DeterministicTrendProvider())
        self.collector = BoundedCollector()
        self.osint = OSINTTaskStore(root / "osint.sqlite", collector=self.collector, intelligence=self.agent)
        self.parent = "parent-followup-task"
        with self.osint.connect() as con:
            con.execute("INSERT INTO osint_task(task_id,title,question,keywords,source_ids,since_days,status,steps,errors,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                        (self.parent, "Public camera research", "What is documented?", "[]", "[]", 365, "completed", "[]", "[]", "2026-10-01T00:00:00Z", "2026-10-01T00:00:00Z"))
        body = "On 2026-09-20, Camera X and Camera Y were shown in a public laboratory test. Camera Y used an open interface."
        self.source = {"itemId": "source-a", "sourceUri": "https://www.nasa.gov/public-test", "sourceVersion": "v1",
                       "contentHash": hashlib.sha256(body.encode()).hexdigest(), "body": body, "title": "Public camera demonstration",
                       "sourceRole": "manufacturer", "contentNature": "manufacturer-claim"}
        draft = DeterministicTrendProvider().generate(task_id=self.parent, title="Public camera research", question="What is documented?", horizon_years=3, materials=[self.source])
        self.assessment = self.store.create_assessment(draft)["assessment"]
        self.aid = self.assessment["assessmentId"]
        ref = {key: self.source[key] for key in ("itemId", "sourceUri", "sourceVersion", "contentHash")}
        ref.update(quote=body, startChar=0, endChar=len(body))
        def node(id, kind, label, **extra):
            return {"id": id, "type": kind, "label": label, "evidenceIds": ["source-a"],
                    "payload": {"extractionMethod": "llm", "evidenceRefs": [deepcopy(ref)], "attributes": [], **extra}}
        self.data = {"nodes": [node("camera-x", "EquipmentModel", "Camera X"),
                               node("camera-y", "EquipmentModel", "Camera Y", attributes=[{"name": "接口", "value": "open interface", "evidenceRefs": [deepcopy(ref)]}]),
                               node("test", "EventRecord", "公开相机实验", eventKind="TestEvent")],
                     "edges": [{"id": "test-camera", "from": "test", "to": "camera-x", "relation": "testsEquipment", "evidenceIds": ["source-a"],
                                "payload": {"extractionMethod": "llm", "evidenceRefs": [deepcopy(ref)]}}]}
        self.graph = self.store.save_graph(self.aid, self.data)
        self.followups = ResearchFollowups(self.osint, self.store)
        self.imagery = ImageryEvidenceRegistry(self.osint, self.store)

    def wait_task(self, task_id):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            task = self.osint.get_task(task_id)
            if task["status"] in {"completed", "partial", "failed"}:
                return task
            time.sleep(.02)
        self.fail("bounded fixture task did not finish")

    def execute(self, node="camera-x", target="property"):
        result = self.followups.execute_target(self.aid, {"graphRevisionId": self.graph["graphRevisionId"], "objectId": node, "targetType": target})
        if result["task"]:
            self.wait_task(result["task"]["id"])
        return result

    def create_payload(self, **changes):
        return {"graphRevisionId": self.graph["graphRevisionId"], "graphElementId": "camera-x", "purpose": "attribute-gap",
                "query": '"Camera X" public interface evaluation', "rationale": "Find explicit interface description and constraints", **changes}

    def imagery_payload(self):
        return {"title": "Public archive candidate", "referenceType": "archive", "mediaKind": "satellite-image",
                "sourceUrl": "https://www.nasa.gov/public-test", "imageUrl": "https://www.nasa.gov/public-test/image.jpg",
                "provider": "Public archive", "captureDate": None, "publishedDate": None,
                "supportScope": "public-test-context", "observedContent": "Unexamined public image reference", "interpretation": "Historical context only",
                "supportStatement": "Potential published event context", "limitations": "Date, image type and independence unverified",
                "assessmentId": self.aid, "graphRevisionId": self.graph["graphRevisionId"], "graphElementId": "camera-x"}

    def test_plan_and_execution_are_one_object_official_first_and_fixed_scope(self):
        plan = self.followups.plan(self.aid)
        self.assertEqual({key: plan["budget"][key] for key in ("maxTasks", "maxItems", "maxItemsPerTask")},
                         {"maxTasks": 4, "maxItems": 32, "maxItemsPerTask": 8})
        self.assertEqual(plan["budget"]["remainingTasks"], 4)
        self.assertEqual(plan["budget"]["remainingItems"], 32)
        self.assertFalse(plan["budget"]["exhausted"])
        self.assertEqual(plan["planningMethod"], "deterministic-object-assertion-templates")
        self.assertFalse(plan["modelJudgment"])
        self.assertFalse(plan["truthVerified"])
        target = next(t for t in plan["targets"] if t["objectId"] == "camera-x" and t["targetType"] == "property")
        self.assertEqual(target["sourceIds"], ["defense-official", "test-evaluation"])
        self.assertTrue(target["question"].startswith('"Camera X"'))
        self.assertNotIn("Camera Y", target["question"])
        result = self.execute()
        task = self.osint.get_task(result["task"]["id"])
        self.assertEqual(task["question"], target["question"])
        self.assertEqual(task["keywords"], ['"Camera X"', target["query"][len('"Camera X"'):].strip()])
        self.assertEqual(self.collector.calls[0]["maxItems"], 8)
        self.assertEqual(task["sourceIds"], target["sourceIds"])
        self.assertEqual(result["followup"]["graphContentHash"], self.graph["contentHash"])
        self.assertEqual(result["followup"]["sourceSnapshotHash"], self.store._snapshot_hash(self.assessment["sourceSnapshot"]))
        self.assertEqual(self.store.get_assessment(self.aid), self.assessment)

    def test_duplicate_target_does_not_launch_another_call_or_spend_budget(self):
        first = self.execute()
        again = self.execute()
        self.assertTrue(again["duplicate"])
        self.assertEqual(again["task"]["id"], first["task"]["id"])
        self.assertEqual(len(self.collector.calls), 1)
        self.assertEqual(len(self.followups.list(self.aid)), 1)
        plan = self.followups.plan(self.aid)
        executed = next(target for target in plan["targets"] if target["targetId"] == first["followup"]["targetId"])
        self.assertFalse(executed["executable"])
        self.assertEqual(executed["executionStatus"], "completed")
        self.assertEqual(executed["followupTaskId"], first["task"]["id"])
        self.assertEqual(executed["collectedItems"], 0)
        self.assertEqual(plan["budget"]["usedTasks"], 1)
        self.assertEqual(plan["budget"]["remainingTasks"], 3)
        self.assertEqual(plan["budget"]["remainingItems"], 24)

    def test_finite_task_and_item_budget_stops_without_expanding_search(self):
        for payload in [self.create_payload(query=f'"Camera X" public evaluation version {i}', maxItems=8) for i in range(4)]:
            result = self.followups.create(self.aid, payload)
            self.wait_task(result["task"]["id"])
        with self.assertRaises(IntelligenceConflict):
            self.followups.create(self.aid, self.create_payload(query='"Camera X" another public evaluation'))
        for limit in (9, 0, True):
            with self.subTest(limit=limit), self.assertRaises(OSINTValidationError):
                self.followups.create(self.aid, self.create_payload(maxItems=limit))
        self.assertEqual(len(self.collector.calls), 4)
        self.assertEqual(sum(call["maxItems"] for call in self.collector.calls), 32)
        plan = self.followups.plan(self.aid)
        self.assertTrue(plan["budget"]["exhausted"])
        self.assertEqual(plan["budget"]["remainingTasks"], 0)
        self.assertEqual(plan["budget"]["remainingItems"], 0)
        self.assertTrue(all(not target["executable"] for target in plan["targets"]))

    def test_specific_field_target_selects_its_query_and_is_frozen(self):
        targets = [target for target in self.followups.plan(self.aid)["targets"]
                   if target["objectId"] == "camera-x" and target["targetType"] == "property"]
        self.assertGreaterEqual(len(targets), 2)
        first = next(target for target in targets if target["attributeKey"] == "category")
        second = next(target for target in targets if target["attributeKey"] == "interface-standard")
        self.assertNotEqual(first["targetId"], second["targetId"])
        self.assertNotEqual(first["query"], second["query"])
        self.assertEqual(second["fieldState"], "not-extracted")
        selection = {"graphRevisionId": self.graph["graphRevisionId"], "objectId": "camera-x", "targetType": "property", "targetId": second["targetId"]}
        result = self.followups.execute_target(self.aid, selection)
        task = self.wait_task(result["task"]["id"])
        self.assertEqual(task["question"], second["question"])
        self.assertEqual(task["keywords"][1], second["query"][len('"Camera X"'):].strip())
        self.assertIn("protocol", task["keywords"][1])
        self.assertEqual(result["followup"]["attributeKey"], "interface-standard")
        self.assertEqual(result["followup"]["targetId"], second["targetId"])
        self.assertEqual(result["followup"]["sourceContext"], second["sourceContext"])
        query = FirecrawlSearchProvider._query(task["question"], task["keywords"], scope=SEARCH_SCOPE_BY_ID["defense-official"], since_days=365)
        self.assertIn("protocol", query)
        self.assertNotIn("当前来源性质", query)

    def test_existing_assertion_keeps_property_path_and_snapshot_source_context(self):
        graph = deepcopy(self.graph)
        graph["nodes"][1]["payload"]["attributes"][0]["evidenceRefs"][0].update(
            contentNature="independent-test", publishedAt="2099-01-01", sourceUri="https://forged.invalid", contentHash="forged")
        with patch.object(self.store, "get_graph", return_value=graph):
            target = next(target for target in self.followups.plan(self.aid)["targets"]
                          if target["objectId"] == "camera-y" and target["targetType"] == "identity")
        self.assertEqual(target["propertyPath"], "payload.attributes[0].value")
        self.assertTrue(target["assertionId"].startswith("assertion-"))
        self.assertEqual(target["rawValue"], "open interface")
        context = target["sourceContext"][0]
        self.assertEqual(context["contentNature"], "manufacturer-claim")
        self.assertEqual(context["sourceUri"], self.source["sourceUri"])
        self.assertEqual(context["contentHash"], self.source["contentHash"])
        self.assertTrue(context["publicationDateMissing"])
        self.assertIsNone(context["publishedAt"])
        self.assertIn("manufacturer-claim", target["question"])
        self.assertIn("资料发布日期：未记录", target["question"])
        self.assertNotIn("2099-01-01", target["question"])
        self.assertNotIn("open interface", target["query"])

    def test_target_id_cannot_be_swapped_or_fields_rewritten(self):
        target = next(target for target in self.followups.plan(self.aid)["targets"]
                      if target["objectId"] == "camera-x" and target["targetType"] == "property")
        selection = {"graphRevisionId": self.graph["graphRevisionId"], "objectId": "camera-x", "targetType": "property", "targetId": target["targetId"]}
        for edit in ({"targetId": "foreign"}, {"objectId": "camera-y"}, {"targetType": "identity"}):
            with self.subTest(edit=edit), self.assertRaises(IntelligenceValidationError):
                self.followups.execute_target(self.aid, {**selection, **edit})
        payload = {"graphRevisionId": self.graph["graphRevisionId"], "graphElementId": "camera-x", "targetId": target["targetId"],
                   **{key: target[key] for key in ("purpose", "query", "sourceIds", "maxItems", "sinceDays", "rationale")}}
        for edit in ({"query": '"Camera X" broad generic search'}, {"sourceIds": ["test-evaluation"]}, {"maxItems": 7}, {"rationale": "different gap"}):
            with self.subTest(edit=edit), self.assertRaises(IntelligenceValidationError):
                self.followups.create(self.aid, {**payload, **edit})
        self.assertEqual(self.collector.calls, [])

    def test_imagery_target_uses_public_event_context_and_date_assertion(self):
        data = deepcopy(self.data)
        # Event time is explicit source calendar text, independent of a
        # publication timestamp.
        data["nodes"][2]["payload"].update(date="2026-09-20", dateEvidenceRefs=deepcopy(data["nodes"][2]["payload"]["evidenceRefs"]))
        self.graph = self.store.save_graph(self.aid, data)
        plan = self.followups.plan(self.aid)
        image = next(target for target in plan["targets"] if target["objectId"] == "camera-x" and target["targetType"] == "public-event-imagery")
        self.assertEqual(image["eventId"], "test")
        self.assertEqual(image["eventDate"], "2026-09-20")
        self.assertEqual(image["eventLabel"], "公开相机实验")
        self.assertNotIn("protocol", image["query"])
        self.assertNotIn("interface", image["query"])
        date = next(target for target in plan["targets"] if target["objectId"] == "test" and target.get("propertyPath") == "payload.date")
        self.assertEqual(date["attributeKey"], "event-date")
        self.assertEqual(date["rawValue"], "2026-09-20")
        self.assertTrue(date["assertionId"].startswith("assertion-"))

    def test_target_selection_and_anchor_cannot_be_overridden_by_browser(self):
        for payload in ({"graphRevisionId": self.graph["graphRevisionId"], "objectId": "camera-x", "targetType": "property", "query": '"Camera Y"'},
                        {"graphRevisionId": self.graph["graphRevisionId"], "objectId": "foreign", "targetType": "property"}):
            with self.assertRaises(IntelligenceValidationError):
                self.followups.execute_target(self.aid, payload)
        for query in ('"Camera Y" public evaluation', 'generic topic "Camera X" public evaluation'):
            with self.subTest(query=query), self.assertRaises(IntelligenceValidationError):
                self.followups.create(self.aid, self.create_payload(query=query))
        self.assertEqual(self.collector.calls, [])

    def test_images_are_separate_and_empty_results_remain_honest_zero(self):
        result = self.execute(target="public-event-imagery")
        task = self.osint.get_task(result["task"]["id"])
        self.assertEqual(task["sourceIds"], ["public-imagery"])
        self.assertEqual(task["items"], [])
        self.assertEqual(result["followup"]["collectedItems"], 0)
        self.assertFalse(result["followup"]["truthVerified"])
        self.assertFalse(result["followup"]["confidencePromotion"])
        self.assertIn("没有匹配", task["brief"]["text"])
        for source_ids in (["public-imagery", "defense-official"], ["defense-official"]):
            with self.subTest(source_ids=source_ids), self.assertRaises(OSINTValidationError):
                self.followups.create(self.aid, self.create_payload(purpose="public-event-imagery", sourceIds=source_ids))
        query = FirecrawlSearchProvider._query(task["question"], task["keywords"], scope=SEARCH_SCOPE_BY_ID["public-imagery"], since_days=365)
        self.assertIn('"Camera X"', query)
        self.assertNotIn("historical publication", query)

    def test_event_anchor_needs_direct_source_named_relation(self):
        plan = self.followups.plan(self.aid)
        event = next(t for t in plan["targets"] if t["objectId"] == "test")
        self.assertTrue(event["query"].startswith('"Camera X"'))
        for mutation in ("no-edge", "generic-relation", "no-name-in-event"):
            data = deepcopy(self.data)
            if mutation == "no-edge": data["edges"] = []
            if mutation == "generic-relation": data["edges"][0]["relation"] = "relatedTo"
            if mutation == "no-name-in-event":
                quote = "public laboratory test"
                start = self.source["body"].index(quote)
                data["nodes"][2]["payload"]["evidenceRefs"][0].update(quote=quote, startChar=start, endChar=start + len(quote))
            self.store.save_graph(self.aid, data)
            self.assertFalse(any(t["objectId"] == "test" for t in self.followups.plan(self.aid)["targets"]))

    def test_stale_graph_cannot_execute_and_saved_target_task_link_is_immutable(self):
        result = self.execute()
        payload = self.create_payload()
        changed = deepcopy(self.data)
        changed["nodes"][0]["payload"]["confidence"] = "medium"
        self.store.save_graph(self.aid, changed)
        with self.assertRaises(IntelligenceConflict):
            self.followups.create(self.aid, payload)
        with self.assertRaises(IntelligenceConflict):
            self.execute()
        self.assertTrue(self.followups.list(self.aid)[0]["staleGraph"])
        with self.osint.connect() as con:
            for sql in ("UPDATE research_followup SET graph_element_id='foreign'", "UPDATE research_followup SET payload='{}'",
                        "UPDATE research_followup SET task_id=NULL", "DELETE FROM research_followup"):
                with self.subTest(sql=sql), self.assertRaises(sqlite3.DatabaseError):
                    con.execute(sql)
        self.assertEqual(len(self.collector.calls), 1)
        self.assertEqual(result["followup"]["objectLabel"], "Camera X")

    def test_exact_frozen_imagery_followup_links_to_parent_only_its_target(self):
        result = self.execute(target="public-event-imagery")
        task_id = result["task"]["id"]
        attached = self.imagery.attach(task_id, self.imagery_payload())["imageryEvidence"]
        self.assertEqual(attached["taskId"], task_id)
        self.assertEqual(attached["assessmentId"], self.aid)
        self.assertEqual(attached["graphElementId"], "camera-x")
        self.assertFalse(attached["truthVerified"])
        for edit in ({"graphElementId": "camera-y"}, {"graphRevisionId": "foreign-revision"}):
            with self.subTest(edit=edit), self.assertRaises(OSINTValidationError):
                self.imagery.attach(task_id, {**self.imagery_payload(), **edit})
        web = self.execute()
        with self.assertRaises(OSINTValidationError):
            self.imagery.attach(web["task"]["id"], self.imagery_payload())
        changed = deepcopy(self.data)
        changed["nodes"][0]["payload"]["confidence"] = "medium"
        self.store.save_graph(self.aid, changed)
        with self.assertRaises(IntelligenceConflict):
            self.imagery.attach(task_id, self.imagery_payload())
        self.assertTrue(self.imagery.list(task_id)[0]["staleGraph"])

    def test_http_plan_target_replay_catalog_and_frozen_imagery_contract(self):
        prefix = "demo." if IntelligenceAgent.__module__.startswith("demo.") else ""
        secrets_module = importlib.import_module(prefix + "provider_secrets")
        agent_module = importlib.import_module(prefix + "intelligence_agent")
        runtime_module = importlib.import_module(prefix + "runtime_store")
        osint_module = importlib.import_module(prefix + "osint_store")
        job_module = importlib.import_module(prefix + "extraction_jobs")
        with patch.object(secrets_module, "load_provider_environment"), patch.object(agent_module, "IntelligenceAgent", return_value=self.agent), patch.object(runtime_module, "RuntimeStore", return_value=object()), patch.object(osint_module, "OSINTTaskStore", return_value=self.osint), patch.object(job_module, "ExtractionJobManager", return_value=object()):
            serve = importlib.import_module(prefix + "serve")
        with patch.object(serve, "INTELLIGENCE_AGENT", self.agent), patch.object(serve, "OSINT_STORE", self.osint), patch.object(serve.Handler, "log_message", lambda *_: None):
            server = serve.ThreadingHTTPServer(("127.0.0.1", 0), serve.Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_address[1]}"
            def request(path, payload=None):
                req = Request(base + path, data=json.dumps(payload).encode() if payload is not None else None,
                              headers={"Content-Type": "application/json"})
                try:
                    with urlopen(req, timeout=5) as response:
                        return response.status, json.load(response)
                except HTTPError as exc:
                    with exc:
                        return exc.code, json.load(exc)
            try:
                path = f"/api/intelligence/assessments/{self.aid}/follow-up-plan"
                code, result = request(path)
                self.assertEqual(code, 200)
                target = next(t for t in result["plan"]["targets"] if t["objectId"] == "camera-x" and t["targetType"] == "public-event-imagery")
                body = {"graphRevisionId": result["plan"]["graphRevisionId"], "objectId": target["objectId"], "targetType": target["targetType"]}
                code, result = request(path, body)
                self.assertEqual(code, 202)
                task_id = result["task"]["id"]
                self.wait_task(task_id)
                self.assertEqual(result["followup"]["graphElementId"], body["objectId"])
                code, replay = request(path, body)
                self.assertEqual(code, 200)
                self.assertTrue(replay["duplicate"])
                self.assertEqual(replay["task"]["id"], task_id)
                code, records = request(f"/api/intelligence/assessments/{self.aid}/followups")
                self.assertEqual(code, 200)
                self.assertEqual(records["followups"][0]["followupTaskId"], task_id)
                code, catalog = request("/api/intelligence/attribute-predicates")
                self.assertEqual(code, 200)
                self.assertEqual(catalog["attributes"], catalog["attributePredicates"])
                self.assertTrue(all(row["ontologyPredicate"].endswith("#" + row["id"]) for row in catalog["attributePredicates"]))
                endpoint = f"/api/osint/tasks/{task_id}/imagery-evidence"
                code, attached = request(endpoint, self.imagery_payload())
                self.assertEqual(code, 201)
                self.assertFalse(attached["confidencePromotion"])
                code, read = request(endpoint)
                self.assertEqual(code, 200)
                self.assertEqual(read["imageryEvidence"][0]["id"], attached["imageryEvidence"]["id"])
                code, _ = request(endpoint, {**self.imagery_payload(), "graphElementId": "camera-y"})
                self.assertEqual(code, 400)
                code, _ = request(path, {**body, "query": "client-rewritten-query"})
                self.assertEqual(code, 400)
                code, _ = request("/api/intelligence/assessments/unknown/follow-up-plan")
                self.assertEqual(code, 404)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
