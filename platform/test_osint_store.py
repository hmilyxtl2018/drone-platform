"""Deterministic tests for the OSINT task coordinator."""

from __future__ import annotations

import tempfile
import time
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    from osint_store import OSINTTaskStore
    from intelligence_agent import IntelligenceAgent
    from intelligence_store import IntelligenceStore
except ModuleNotFoundError:  # pragma: no cover
    from .osint_store import OSINTTaskStore
    from .intelligence_agent import IntelligenceAgent
    from .intelligence_store import IntelligenceStore


class FakeCollector:
    def __init__(self, *, errors=None):
        self.errors = list(errors or [])

    def collect(self, source_ids, keywords, *, since_days=30, max_items=50):
        # Return two equal hashes to prove the coordinator deduplicates source
        # output before storing immutable material records.
        first = {
            "title": "公开采购公告：远程雷达交付",
            "sourceId": source_ids[0],
            "sourceName": "Fixture public feed",
            "sourceUri": "https://fixture.example/feed.xml",
            "itemUri": "https://fixture.example/radar-1",
            "sourceVersion": "fixture-1",
            "publishedAt": "2026-09-28T08:00:00Z",
            "body": "远程雷达 | 交付 | 2026年第四季度\n来源公开摘要，待核验。",
            "contentHash": "a" * 64,
            "matchedKeywords": ["采购"],
        }
        duplicate = dict(first, itemUri="https://fixture.example/radar-duplicate")
        second = dict(first, title="公开产业合作：传感器供应链", itemUri="https://fixture.example/sensor-2", sourceVersion="fixture-2", body="传感器 | 合作 | 供应链", contentHash="b" * 64, matchedKeywords=["产业"])
        return {
            "items": [first, duplicate, second],
            "errors": self.errors,
            "sourceResults": [{"sourceId": source_id, "status": "ok"} for source_id in source_ids],
        }


class OSINTTaskStoreTests(unittest.TestCase):
    def wait_for(self, store, task_id, statuses={"completed", "partial", "failed"}):
        deadline = time.time() + 5
        while time.time() < deadline:
            task = store.get_task(task_id)
            if task and task["status"] in statuses:
                for worker in threading.enumerate():
                    if worker.name == 'osint-' + task_id:
                        worker.join(timeout=2)
                        self.assertFalse(worker.is_alive(), 'collection trace must close before deleting the temporary store')
                return task
            time.sleep(0.02)
        self.fail(f"task did not finish: {task_id}")

    def make_store(self, root: Path, collector):
        intelligence = IntelligenceAgent(store=IntelligenceStore(root / "intelligence.sqlite"))
        return OSINTTaskStore(path=root / "osint.sqlite", collector=collector, intelligence=intelligence), intelligence

    def payload(self):
        return {
            "title": "装备采购与产业动态",
            "question": "哪些公开材料提到雷达采购和产业合作？",
            "keywords": ["采购", "产业"],
            "sourceIds": ["fixture"],
            "sinceDays": 30,
        }

    def test_background_run_deduplicates_and_handoffs_candidates(self):
        with tempfile.TemporaryDirectory() as directory:
            store, intelligence = self.make_store(Path(directory), FakeCollector())
            # The production store validates against its curated allowlist;
            # replace the fixture map only inside this deterministic test.
            import osint_store as module
            module.SOURCE_BY_ID["fixture"] = {"id": "fixture", "name": "Fixture public feed", "url": "https://fixture.example/feed.xml"}
            task = store.create_task(self.payload())
            finished = self.wait_for(store, task["id"])
            self.assertEqual(finished["status"], "completed")
            self.assertEqual(len(finished["items"]), 2)
            self.assertEqual(finished["steps"][-1]["status"], "done")
            result = store.handoff(task["id"], [item["id"] for item in finished["items"]])
            self.assertEqual(result["candidateCount"], 3)
            self.assertEqual(len(intelligence.store.list_documents()), 2)

    def test_automation_options_are_persisted_and_returned(self):
        with tempfile.TemporaryDirectory() as directory:
            store, _ = self.make_store(Path(directory), FakeCollector())
            import osint_store as module
            module.SOURCE_BY_ID["fixture"] = {"id": "fixture", "name": "Fixture public feed", "url": "https://fixture.example/feed.xml"}
            task = store.create_task({**self.payload(), "workflowMode": "auto", "automation": {"autoStartAssessment": True, "autoRunEvidenceCheck": True}})
            self.assertEqual(task["workflowMode"], "auto")
            self.assertTrue(task["automation"]["autoStartAssessment"])
            self.assertTrue(task["automation"]["autoRunEvidenceCheck"])
            self.assertFalse(task["automation"]["autoCreateAdmissionCandidates"])

    def test_partial_source_failure_is_visible_and_still_reviewable(self):
        with tempfile.TemporaryDirectory() as directory:
            store, _ = self.make_store(Path(directory), FakeCollector(errors=[{"sourceId": "fixture", "error": "timeout"}]))
            import osint_store as module
            module.SOURCE_BY_ID["fixture"] = {"id": "fixture", "name": "Fixture public feed", "url": "https://fixture.example/feed.xml"}
            finished = self.wait_for(store, store.create_task(self.payload())["id"])
            self.assertEqual(finished["status"], "partial")
            self.assertEqual(finished["errors"][0]["error"], "timeout")
            self.assertEqual(len(finished["items"]), 2)

    def test_task_input_is_validated_before_thread_start(self):
        with tempfile.TemporaryDirectory() as directory:
            store, _ = self.make_store(Path(directory), FakeCollector())
            with self.assertRaises(Exception):
                store.create_task({"title": "", "question": "", "sourceIds": []})

    def test_assessment_uses_server_owned_items_and_task_question(self):
        with tempfile.TemporaryDirectory() as directory:
            store, intelligence = self.make_store(Path(directory), FakeCollector())
            import osint_store as module
            module.SOURCE_BY_ID["fixture"] = {"id": "fixture", "name": "Fixture public feed", "url": "https://fixture.example/feed.xml"}
            task = self.wait_for(store, store.create_task(self.payload())["id"])
            result = store.create_assessment(task["id"], {"itemIds": [task["items"][0]["id"]], "horizonYears": 5})
            assessment = result["assessment"]
            self.assertEqual(assessment["taskId"], task["id"])
            self.assertEqual(assessment["question"], task["question"])
            self.assertEqual(assessment["sourceSnapshot"][0]["contentHash"], task["items"][0]["contentHash"])
            self.assertEqual(assessment["status"], "under-review")

    def test_source_scope_options_are_separate_from_engine_and_default_official_first(self):
        from osint_store import public_sources
        with patch.dict("os.environ", {"CRK_SEARCH_PROVIDER":"firecrawl"}):
            sources=public_sources()
        self.assertEqual([s["id"] for s in sources if s["defaultSelected"]], ["defense-official","test-evaluation","procurement-budget"])
        self.assertEqual(len(sources),6)
        self.assertNotIn("firecrawl-search",[s["id"] for s in sources])
        self.assertTrue(all(s["domains"] and s["description"] for s in sources))
        with patch.dict("os.environ", {"CRK_SEARCH_PROVIDER":"rss"}):
            self.assertTrue(all(s["engine"]=="rss" for s in public_sources()))

    def test_image_discovery_keeps_empty_body_and_public_query_plan(self):
        class ImageCollector:
            def collect(self, source_ids, keywords, **kwargs):
                return {"items":[{"title":"Archive caption", "sourceId":"public-imagery", "sourceName":"esa.int", "sourceUri":"https://esa.int/archive", "itemUri":"https://esa.int/archive", "body":"", "contentHash":"c"*64, "provenance":{"retrievalKind":"search-discovery-only", "contentKind":"image-search-metadata", "mediaReferences":[{"imageUrl":"https://esa.int/image.jpg", "sourceUrl":"https://esa.int/archive", "caption":"Archive caption", "mediaKind":"unclassified-image", "captureDate":None, "verificationStatus":"unverified"}]}}], "errors":[], "sourceResults":[], "queryPlan":[{"sourceId":"public-imagery", "searchType":"images", "query":'"Primoco" "satellite image" site:esa.int', "targetAnchors":["Primoco"], "requestedLimit":1, "authorization":"fixture-secret-header"}]}
        with tempfile.TemporaryDirectory() as directory:
            store,_=self.make_store(Path(directory),ImageCollector())
            payload=dict(self.payload(), sourceIds=["public-imagery"])
            task=self.wait_for(store,store.create_task(payload)["id"])
            self.assertEqual(task["items"][0]["body"], "")
            self.assertEqual(task["items"][0]["provenance"]["contentKind"], "image-search-metadata")
            self.assertEqual(task["brief"]["queryPlan"][0]["query"], '"Primoco" "satellite image" site:esa.int')
            self.assertNotIn("authorization", task["brief"]["queryPlan"][0])
            self.assertNotIn("fixture-secret-header", str(task["brief"]))

    def test_provenance_survives_snapshot_and_handoff_keeps_source_nature(self):
        class ClassifiedCollector(FakeCollector):
            def collect(self, source_ids, keywords, **kwargs):
                result=super().collect(source_ids, keywords, **kwargs)
                for i,item in enumerate(result["items"]):
                    item["provenance"]={"collector":"firecrawl-search","sourceRole":"manufacturer-disclosure" if i==2 else "secondary-report","contentNature":"manufacturer-claim" if i==2 else "secondary-report","sourceReliability":"primary" if i==2 else "secondary","publisherDomain":"fixture.example","verificationStatus":"unverified","selectedScopes":["manufacturer-disclosure","defense-media"],"classificationBasis":"publisher-scope","documentTypeVerified":False}
                return result
        with tempfile.TemporaryDirectory() as directory:
            store,intelligence=self.make_store(Path(directory),ClassifiedCollector())
            import osint_store as module
            module.SOURCE_BY_ID["fixture"]={"id":"fixture","name":"Fixture","url":"https://fixture.example/feed.xml"}
            task=self.wait_for(store,store.create_task(self.payload())["id"])
            snapshot=store.create_assessment(task["id"],{})["assessment"]["sourceSnapshot"]
            self.assertEqual({item["sourceRole"] for item in snapshot},{"manufacturer-disclosure","secondary-report"})
            self.assertTrue(all(item["provenance"]["verificationStatus"]=="unverified" for item in snapshot))
            result=store.handoff(task["id"],[item["id"] for item in task["items"]])
            self.assertEqual({doc["sourceReliability"] for doc in result["documents"]},{"primary","secondary"})

    def test_legacy_metadata_is_not_fabricated_during_read(self):
        with tempfile.TemporaryDirectory() as directory:
            store,_=self.make_store(Path(directory),FakeCollector())
            import osint_store as module
            module.SOURCE_BY_ID["fixture"]={"id":"fixture","name":"Fixture","url":"https://fixture.example/feed.xml"}
            task=self.wait_for(store,store.create_task(self.payload())["id"])
            self.assertTrue(all("sourceRole" not in item for item in task["items"]))


if __name__ == "__main__":
    unittest.main()
