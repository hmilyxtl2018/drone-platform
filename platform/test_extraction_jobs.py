"""Durable extraction recovery and optimistic graph commits without networking."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

try:
    from extraction_jobs import ExtractionJobManager, encoded
    from intelligence_agent import DeterministicClaimProvider, DeterministicTrendProvider, IntelligenceAgent
    from intelligence_store import IntelligenceStore, IntelligenceConflict, IntelligenceValidationError
    from object_attribute_extraction import enrich_object_attributes
    from evidence_graph import extract_research_graph
except ModuleNotFoundError:  # pragma: no cover
    from .extraction_jobs import ExtractionJobManager, encoded
    from .intelligence_agent import DeterministicClaimProvider, DeterministicTrendProvider, IntelligenceAgent
    from .intelligence_store import IntelligenceStore, IntelligenceConflict, IntelligenceValidationError
    from .object_attribute_extraction import enrich_object_attributes
    from .evidence_graph import extract_research_graph


class ProcessInterrupted(BaseException):
    """Simulate loss of the worker after its durable checkpoint was committed."""


class LocalJsonProvider:
    model = "isolated-json-fixture-model"

    def __init__(self):
        self.calls = []
        self.identity_calls = []
        self.link_calls = []
        self.fail_ids = set()
        self.fail_links = False
        self.before_response = None

    def _request_json(self, messages, **options):
        data = json.loads(messages[1]["content"].split("\n", 1)[1])
        if "requestedObjectLabels" in data:
            self.identity_calls.append(data)
            nodes = []
            for label in data["requestedObjectLabels"]:
                for material in data["materials"]:
                    for excerpt in material["excerpts"]:
                        quote = next((line for line in excerpt["text"].splitlines() if line.startswith(label + " uses")), None)
                        if quote:
                            nodes.append({"id": "fixture-" + str(len(nodes)), "type": "EquipmentModel", "label": label,
                                          "itemId": material["itemId"], "quote": quote})
            return {"nodes": nodes, "edges": [], "gaps": []}
        if "findings" in data:
            self.link_calls.append(data)
            if self.fail_links:
                raise TimeoutError("fixture-sensitive-link-error")
            links = []
            for finding in data["findings"]:
                prop = next((attribute for attribute in data["attributes"]
                             if set(finding["evidenceIds"]) & {ref["itemId"] for ref in attribute["evidenceRefs"]}), None)
                if prop:
                    links.append({"findingId": finding["id"], "objectId": prop["objectId"],
                                  "propertyPath": prop["propertyPath"], "assertionId": prop["assertionId"],
                                  "basis": "context", "supportReason": "此公开接口描述仅提供具体设计背景，不能认定实际效果或整体趋势已验证。"})
            return {"links": links, "gaps": []}
        identity = data["object"]
        object_id, label = identity["objectId"], identity["label"]
        self.calls.append(object_id)
        if self.before_response:
            self.before_response(object_id)
        if object_id in self.fail_ids:
            raise RuntimeError("fixture-sensitive-provider-error")
        for material in data["materials"]:
            for excerpt in material["excerpts"]:
                quote = next((line for line in excerpt["text"].splitlines() if line.startswith(label + " uses")), None)
                if quote:
                    return {"objectId": object_id, "attributes": [{"name": "接口方式", "attributeKey": "interface-standard",
                            "value": "an open interface", "itemId": material["itemId"], "quote": quote}], "gaps": []}
        return {"objectId": object_id, "attributes": [], "gaps": []}


class ExtractionJobTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = IntelligenceStore(Path(self.temp.name) / "research.sqlite")
        self.json_provider = LocalJsonProvider()
        self.agent = IntelligenceAgent(self.store, provider=DeterministicClaimProvider(),
            trend_provider=SimpleNamespace(configured=True, claim_provider=self.json_provider))
        self._assessment(3)
        self.manager = ExtractionJobManager(self.agent)

    def _assessment(self, count):
        body = "\n\n".join(f"Camera {index} uses an open interface in a public laboratory test." for index in range(count))
        self.source = {"itemId": "source-a", "title": "Public interface test", "body": body,
                       "sourceUri": "https://example.test/interfaces", "sourceVersion": "v1",
                       "contentHash": hashlib.sha256(body.encode()).hexdigest(), "publishedAt": None}
        draft = DeterministicTrendProvider().generate(task_id="isolated-task", title="Public interface research",
            question="What is disclosed about public interfaces?", horizon_years=3, materials=[self.source])
        draft["summary"]["findings"] = [{"id": "public-interface-trend", "title": "公开接口描述",
            "judgment": "材料披露开放接口描述，采用效果仍需补证。", "evidenceIds": ["source-a"], "confidence": "low"}]
        self.assessment = self.store.create_assessment(draft)["assessment"]
        self.aid = self.assessment["assessmentId"]
        nodes = []
        for index in range(count):
            label = f"Camera {index}"
            quote = next(line for line in body.splitlines() if line.startswith(label + " uses"))
            start = body.index(quote)
            ref = {key: self.source[key] for key in ("itemId", "sourceUri", "sourceVersion", "contentHash")}
            ref.update(quote=quote, startChar=start, endChar=start + len(quote))
            nodes.append({"id": f"camera-{index}", "type": "EquipmentModel", "label": label,
                          "evidenceIds": ["source-a"], "payload": {"extractionMethod": "llm",
                          "reviewStatus": "pending", "confidence": "low", "attributes": [], "evidenceRefs": [ref]}})
        self.graph = self.store.save_graph(self.aid, {"nodes": nodes, "edges": []})

    def submit(self, **options):
        return self.manager.submit(self.aid, {"expectedRevision": 1, "extractEntities": False,
                                             "extractAttributes": True, **options}, start=False)["job"]

    def checkpoint_data(self, job_id):
        with self.store._connect() as con:
            return json.loads(con.execute("SELECT checkpoint FROM research_extraction_job WHERE job_id=?", (job_id,)).fetchone()["checkpoint"])

    def graph_count(self):
        with self.store._connect() as con:
            return con.execute("SELECT count(*) FROM intelligence_graph_revision WHERE assessment_id=?", (self.aid,)).fetchone()[0]

    def test_active_identical_submission_reuses_job_and_different_request_conflicts(self):
        request = {"expectedRevision": 1, "extractAttributes": True}
        first = self.manager.submit(self.aid, request, start=False)
        duplicate = self.manager.submit(self.aid, deepcopy(request), start=False)
        self.assertTrue(duplicate["duplicate"])
        self.assertEqual(first["job"]["jobId"], duplicate["job"]["jobId"])
        with self.assertRaises(IntelligenceConflict):
            self.manager.submit(self.aid, {**request, "objectIds": ["camera-1"]}, start=False)
        self.assertEqual(self.json_provider.calls, [])

    def test_job_inputs_and_checkpoint_history_cannot_be_rewritten_or_deleted(self):
        job = self.submit()
        data = {"nodes": self.graph["nodes"], "processedObjectIds": [], "selectedObjectIds": ["camera-0"]}
        self.manager.checkpoint(job["jobId"], "object-identities", data)
        statements = [
            ("UPDATE research_extraction_job SET source_snapshot_hash='changed' WHERE job_id=?", (job["jobId"],)),
            ("UPDATE research_extraction_job SET request_payload='{}' WHERE job_id=?", (job["jobId"],)),
            ("DELETE FROM research_extraction_job WHERE job_id=?", (job["jobId"],)),
            ("UPDATE research_extraction_checkpoint SET payload='{}' WHERE job_id=?", (job["jobId"],)),
            ("DELETE FROM research_extraction_checkpoint WHERE job_id=?", (job["jobId"],)),
        ]
        for sql, args in statements:
            with self.subTest(sql=sql), self.assertRaises(sqlite3.IntegrityError):
                with self.store._connect() as con:
                    con.execute(sql, args)
        self.assertEqual(self.checkpoint_data(job["jobId"]), data)

    def test_live_agent_restart_skips_checkpointed_objects_and_saves_one_graph(self):
        job = self.submit()
        def interrupted_runner(aid, request, *, extraction_checkpoint, restored_extraction):
            def checkpoint(phase, data):
                extraction_checkpoint(phase, data)
                if data.get("processedObjectIds") == ["camera-0"]:
                    raise ProcessInterrupted()
            return self.agent.build_assessment_graph(aid, request, extraction_checkpoint=checkpoint,
                                                     restored_extraction=restored_extraction)
        self.manager.runner = interrupted_runner
        with self.assertRaises(ProcessInterrupted):
            self.manager.run(job["jobId"])
        self.assertEqual(self.json_provider.calls, ["camera-0"])
        self.assertEqual(self.graph_count(), 1)
        self.assertEqual(self.manager.get(job["jobId"])["status"], "running")
        checkpoint = self.checkpoint_data(job["jobId"])
        self.assertEqual(checkpoint["processedObjectIds"], ["camera-0"])
        self.assertEqual(len(checkpoint["nodes"][0]["payload"]["attributes"]), 1)
        restarted = ExtractionJobManager(self.agent)
        self.assertEqual(restarted.recover(start=False), [job["jobId"]])
        restarted.run(job["jobId"])
        self.assertEqual(self.json_provider.calls, ["camera-0", "camera-1", "camera-2"])
        self.assertEqual(self.graph_count(), 2)
        final = restarted.get(job["jobId"])
        self.assertEqual(final["status"], "completed")
        graph = self.store.get_graph(self.aid)
        objects = [node for node in graph["nodes"] if node["payload"].get("extractionMethod") == "llm"]
        self.assertTrue(all(len(node["payload"]["attributes"]) == 1 for node in objects))
        self.assertEqual(final["resultGraphRevisionId"], graph["graphRevisionId"])
        self.assertEqual(len(self.json_provider.link_calls), 1)

    def test_link_checkpoint_survives_repeated_restart_without_repeating_any_model_stage(self):
        job = self.submit()
        def interrupt_after_links(aid, request, *, extraction_checkpoint, restored_extraction):
            def checkpoint(phase, data):
                extraction_checkpoint(phase, data)
                if phase == "finding-attribute-links":
                    raise ProcessInterrupted()
            return self.agent.build_assessment_graph(aid, request, extraction_checkpoint=checkpoint,
                                                     restored_extraction=restored_extraction)
        self.manager.runner = interrupt_after_links
        with self.assertRaises(ProcessInterrupted):
            self.manager.run(job["jobId"])
        self.assertEqual(len(self.json_provider.calls), 3)
        self.assertEqual(len(self.json_provider.link_calls), 1)
        self.assertIn("findingLinks", self.checkpoint_data(job["jobId"]))
        self.manager.recover(start=False)
        self.manager.runner = self.agent.build_assessment_graph
        with patch.object(self.store, "save_graph", side_effect=ProcessInterrupted()):
            with self.assertRaises(ProcessInterrupted):
                self.manager.run(job["jobId"])
        self.assertIn("findingLinks", self.checkpoint_data(job["jobId"]))
        self.manager.recover(start=False)
        self.manager.run(job["jobId"])
        self.assertEqual(len(self.json_provider.calls), 3)
        self.assertEqual(len(self.json_provider.link_calls), 1)
        self.assertEqual(self.graph_count(), 2)
        self.assertEqual(self.manager.get(job["jobId"])["status"], "completed")

    def test_link_failure_is_partial_keeps_valid_attributes_and_does_not_leak_error(self):
        self.json_provider.fail_links = True
        job = self.submit()
        self.manager.run(job["jobId"])
        final = self.manager.get(job["jobId"])
        self.assertEqual(final["status"], "partial")
        graph = self.store.get_graph(self.aid)
        objects = [node for node in graph["nodes"] if node["payload"].get("extractionMethod") == "llm"]
        self.assertTrue(all(node["payload"]["attributes"] for node in objects))
        trends = [node for node in graph["nodes"] if node["type"] == "TrendHypothesis"]
        self.assertTrue(all(node["payload"]["attributeLinkMetadata"]["status"] == "failed" for node in trends))
        self.assertNotIn("fixture-sensitive-link-error", json.dumps(graph))

    def test_partial_failure_retains_old_property_identity_and_completes_other_objects(self):
        old_graph = deepcopy(self.graph)
        old = {"name": "已有属性", "value": "historical", "reviewStatus": "pending"}
        old_graph["nodes"][1]["payload"]["attributes"] = [old]
        self.graph = self.store.save_graph(self.aid, {"nodes": old_graph["nodes"], "edges": []})
        self.json_provider.fail_ids = {"camera-1"}
        job = self.submit()
        self.manager.run(job["jobId"])
        final = self.manager.get(job["jobId"])
        self.assertEqual(final["status"], "partial")
        objects = {node["id"]: node for node in self.store.get_graph(self.aid)["nodes"] if node["payload"].get("extractionMethod") == "llm"}
        self.assertEqual(objects["camera-1"]["payload"]["attributes"], [old])
        self.assertEqual(objects["camera-1"]["payload"]["evidenceRefs"], old_graph["nodes"][1]["payload"]["evidenceRefs"])
        self.assertEqual(objects["camera-1"]["payload"]["attributeExtraction"]["status"], "failed")
        self.assertEqual(len(objects["camera-0"]["payload"]["attributes"]), 1)
        self.assertNotIn("fixture-sensitive-provider-error", json.dumps(final))

    def test_selected_subset_does_not_count_unselected_objects_as_processed(self):
        job = self.submit(objectIds=["camera-1"])
        self.manager.run(job["jobId"])
        self.assertEqual(self.json_provider.calls, ["camera-1"])
        checkpoint = self.checkpoint_data(job["jobId"])
        self.assertEqual(checkpoint["processedObjectIds"], ["camera-1"])
        self.assertEqual(self.manager.get(job["jobId"])["progress"]["processedObjects"], 1)

    def test_output_truncation_recovers_current_object_with_larger_budget_and_checkpoints(self):
        original_request = self.json_provider._request_json
        attempts = []
        def request(messages, **options):
            data = json.loads(messages[1]["content"].split("\n", 1)[1])
            if "object" in data:
                attempts.append((deepcopy(data), deepcopy(options)))
                if len(attempts) == 1:
                    raise ValueError("provider output exhausted its token budget")
            return original_request(messages, **options)
        job = self.submit()
        with patch.object(self.json_provider, "_request_json", side_effect=request):
            self.manager.run(job["jobId"])
        self.assertEqual([data["object"]["objectId"] for data, options in attempts], ["camera-0", "camera-0", "camera-1", "camera-2"])
        self.assertEqual([options["output_token_limit"] for data, options in attempts], [4096, 8192, 4096, 4096])
        self.assertTrue(all(options["thinking"] is False for data, options in attempts))
        self.assertEqual(attempts[0][0], attempts[1][0])
        self.assertEqual(self.manager.get(job["jobId"])["status"], "completed")
        checkpoint = self.checkpoint_data(job["jobId"])
        self.assertEqual(checkpoint["processedObjectIds"], ["camera-0", "camera-1", "camera-2"])
        objects = {node["id"]: node for node in self.store.get_graph(self.aid)["nodes"] if node["payload"].get("extractionMethod") == "llm"}
        self.assertEqual(objects["camera-0"]["payload"]["attributeExtraction"]["calls"], 2)
        self.assertEqual(objects["camera-0"]["payload"]["attributeExtraction"]["outputTruncationRetries"], 1)
        self.assertTrue(all(len(node["payload"]["attributes"]) == 1 for node in objects.values()))

    def test_supplemental_identity_requests_only_new_object_and_retains_seven_existing_objects(self):
        self._assessment(8)
        old_nodes = deepcopy(self.graph["nodes"][:7])
        quote = self.source["body"].split("\n\n")[0]
        ref = deepcopy(old_nodes[0]["payload"]["evidenceRefs"][0])
        old_attribute = {"assertionId": "old-interface", "name": "接口方式", "value": "an open interface", "evidenceRefs": [ref]}
        old_nodes[0]["payload"]["attributes"] = [old_attribute]
        old_relation = {"id": "old-context", "from": "camera-0", "to": "camera-1", "relation": "relatedTo", "evidenceIds": ["source-a"],
                        "payload": {"extractionMethod": "llm", "evidenceRefs": [ref]}}
        self.graph = self.store.save_graph(self.aid, {"nodes": old_nodes, "edges": [old_relation]})
        job = self.manager.submit(self.aid, {"expectedRevision": 1, "extractEntities": True, "objectLabels": ["Camera 7"]}, start=False)["job"]
        self.manager.run(job["jobId"])
        self.assertEqual(len(self.json_provider.identity_calls), 1)
        self.assertEqual(self.json_provider.identity_calls[0]["requestedObjectLabels"], ["Camera 7"])
        self.assertEqual(len(self.json_provider.calls), 1)
        self.assertEqual(len(self.json_provider.link_calls), 1)
        graph = self.store.get_graph(self.aid)
        extracted = {node["id"]: node for node in graph["nodes"] if node["payload"].get("extractionMethod") == "llm"}
        self.assertEqual(len(extracted), 8)
        new = next(node for node in extracted.values() if node["label"] == "Camera 7")
        self.assertEqual(self.json_provider.calls, [new["id"]])
        self.assertEqual(len(new["payload"]["attributes"]), 1)
        for old in old_nodes:
            self.assertEqual(extracted[old["id"]]["payload"]["evidenceRefs"], old["payload"]["evidenceRefs"])
            self.assertEqual(extracted[old["id"]]["payload"]["attributes"], old["payload"]["attributes"])
        self.assertEqual(next(edge for edge in graph["edges"] if edge["id"] == "old-context"), old_relation)
        checkpoint = self.checkpoint_data(job["jobId"])
        self.assertEqual(checkpoint["selectedObjectIds"], [new["id"]])
        self.assertEqual(checkpoint["processedObjectIds"], [new["id"]])
        self.assertEqual(self.manager.get(job["jobId"])["status"], "completed")

    def test_unknown_supplemental_label_rejected_before_any_provider_call(self):
        request = {"expectedRevision": 1, "extractEntities": True, "objectLabels": ["Unstated object"]}
        with self.assertRaises(IntelligenceValidationError):
            self.manager.submit(self.aid, request, start=False)
        with self.assertRaises(IntelligenceValidationError):
            self.agent.build_assessment_graph(self.aid, request)
        self.assertEqual(self.json_provider.calls, [])
        self.assertEqual(self.json_provider.identity_calls, [])
        self.assertEqual(self.json_provider.link_calls, [])
        self.assertEqual(self.graph_count(), 1)

    def test_supplemental_checkpoint_restart_preserves_new_object_scope(self):
        self._assessment(8)
        self.graph = self.store.save_graph(self.aid, {"nodes": self.graph["nodes"][:7], "edges": []})
        before_count = self.graph_count()
        job = self.manager.submit(self.aid, {"expectedRevision": 1, "extractEntities": True, "objectLabels": ["Camera 7"]}, start=False)["job"]
        def interrupted_identity_runner(aid, request, *, extraction_checkpoint, restored_extraction):
            def checkpoint(phase, data):
                extraction_checkpoint(phase, data)
                if phase == "object-identities":
                    raise ProcessInterrupted()
            return self.agent.build_assessment_graph(aid, request, extraction_checkpoint=checkpoint,
                                                     restored_extraction=restored_extraction)
        self.manager.runner = interrupted_identity_runner
        with self.assertRaises(ProcessInterrupted):
            self.manager.run(job["jobId"])
        checkpoint = self.checkpoint_data(job["jobId"])
        new_ids = checkpoint["selectedObjectIds"]
        self.assertEqual(len(new_ids), 1)
        self.assertEqual(self.json_provider.calls, [])
        self.assertEqual(len(self.json_provider.identity_calls), 1)
        self.manager.recover(start=False)
        self.manager.runner = self.agent.build_assessment_graph
        self.manager.run(job["jobId"])
        self.assertEqual(self.json_provider.calls, new_ids)
        self.assertEqual(len(self.json_provider.identity_calls), 1)
        self.assertEqual(len(self.json_provider.link_calls), 1)
        self.assertEqual(self.graph_count(), before_count + 1)
        self.assertEqual(self.manager.get(job["jobId"])["status"], "completed")

    def test_budget_skipped_objects_remain_outside_processed_checkpoint(self):
        self._assessment(10)
        job = self.submit()
        self.manager.run(job["jobId"])
        self.assertEqual(len(self.json_provider.calls), 8)
        checkpoint = self.checkpoint_data(job["jobId"])
        self.assertEqual(len(checkpoint["processedObjectIds"]), 8)
        self.assertNotIn("camera-8", checkpoint["processedObjectIds"])
        self.assertEqual(self.manager.get(job["jobId"])["status"], "partial")

    def test_stale_graph_before_worker_runs_prevents_provider_and_graph_writes(self):
        job = self.submit()
        changed_nodes = deepcopy(self.graph["nodes"])
        changed_nodes[0]["payload"]["revisionMarker"] = "external-edit-before-run"
        changed = self.store.save_graph(self.aid, {"nodes": changed_nodes, "edges": []})
        self.manager.run(job["jobId"])
        self.assertEqual(self.json_provider.calls, [])
        self.assertEqual(self.graph_count(), 2)
        self.assertEqual(self.store.get_graph(self.aid)["graphRevisionId"], changed["graphRevisionId"])
        self.assertEqual(self.manager.get(job["jobId"])["errorCode"], "graph-or-report-version-changed")

    def test_source_snapshot_mismatch_aborts_before_model_call(self):
        job = self.submit()
        changed_report = deepcopy(self.assessment)
        changed_report["sourceSnapshot"][0]["body"] += " Changed source content."
        with patch.object(self.store, "get_assessment", return_value=changed_report):
            self.manager.run(job["jobId"])
        self.assertEqual(self.json_provider.calls, [])
        self.assertEqual(self.graph_count(), 1)
        self.assertEqual(self.manager.get(job["jobId"])["errorCode"], "graph-or-report-version-changed")

    def test_report_revision_change_aborts_before_model_call(self):
        job = self.submit()
        changed_report = deepcopy(self.assessment)
        changed_report["revision"] += 1
        with patch.object(self.store, "get_assessment", return_value=changed_report):
            self.manager.run(job["jobId"])
        self.assertEqual(self.json_provider.calls, [])
        self.assertEqual(self.graph_count(), 1)
        self.assertEqual(self.manager.get(job["jobId"])["errorCode"], "graph-or-report-version-changed")

    def test_graph_changed_during_provider_call_blocks_final_commit(self):
        job = self.submit()
        changed = []
        def update_graph(_):
            if not changed:
                changed_nodes = deepcopy(self.graph["nodes"])
                changed_nodes[0]["payload"]["revisionMarker"] = "external-edit-during-call"
                changed.append(self.store.save_graph(self.aid, {"nodes": changed_nodes, "edges": []}))
        self.json_provider.before_response = update_graph
        self.manager.run(job["jobId"])
        self.assertEqual(self.graph_count(), 2)
        self.assertEqual(self.store.get_graph(self.aid)["graphRevisionId"], changed[0]["graphRevisionId"])
        self.assertEqual(self.manager.get(job["jobId"])["status"], "failed")
        self.assertEqual(self.manager.get(job["jobId"])["errorCode"], "graph-or-report-version-changed")

    def test_checkpoint_persistence_failure_stops_later_calls_and_keeps_original_graph(self):
        job = self.submit()
        with patch.object(self.manager, "checkpoint", side_effect=OSError("fixture-secret-disk-detail")):
            self.manager.run(job["jobId"])
        self.assertEqual(self.json_provider.calls, ["camera-0"])
        self.assertEqual(self.graph_count(), 1)
        final = self.manager.get(job["jobId"])
        self.assertEqual(final["status"], "failed")
        self.assertEqual(final["errorCode"], "connection-or-timeout")
        self.assertNotIn("fixture-secret-disk-detail", json.dumps(final))

    def test_final_graph_commit_failure_keeps_checkpoint_and_old_graph(self):
        job = self.submit()
        with patch.object(self.store, "save_graph", side_effect=OSError("fixture-sensitive-commit-error")):
            self.manager.run(job["jobId"])
        self.assertEqual(len(self.json_provider.calls), 3)
        self.assertEqual(self.graph_count(), 1)
        self.assertEqual(self.manager.get(job["jobId"])["status"], "failed")
        checkpoint = self.checkpoint_data(job["jobId"])
        self.assertEqual(len(checkpoint["processedObjectIds"]), 3)
        self.assertTrue(all(node["payload"]["attributes"] for node in checkpoint["nodes"]))


class RealAttributeBaselineTests(unittest.TestCase):
    def test_valid_mars_identity_exact_original_quote_is_present_in_targeted_model_input(self):
        path = Path(__file__).resolve().parents[1] / "docs" / "fixtures" / "osint-uav-attributes-baseline-20261008.json"
        fixture = json.loads(path.read_text(encoding="utf-8"))
        material = fixture["selectedMaterial"]
        proposal = fixture["knownExtractableAttributes"][0]
        quote = proposal["evidence"]["quote"]
        calls = []
        raw = {"nodes": [{"id": "mars-mission", "type": "Technology", "label": "MARS Mission System",
                          "itemId": material["itemId"], "quote": quote}], "edges": [], "gaps": []}
        def request(messages):
            calls.append(messages)
            payload = json.loads(messages[1]["content"].split("\n", 1)[1])
            self.assertEqual(payload["requestedObjectLabels"], ["MARS Mission System"])
            self.assertTrue(any(quote in excerpt["text"] for row in payload["materials"] for excerpt in row["excerpts"]))
            return raw
        extracted = extract_research_graph([material], request, identity_only=True, object_labels=["MARS Mission System"])
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(extracted["nodes"]), 1)
        self.assertEqual(extracted["nodes"][0]["label"], "MARS Mission System")
        self.assertEqual(extracted["nodes"][0]["type"], "Technology")
        self.assertEqual(extracted["nodes"][0]["payload"]["evidenceRefs"][0]["startChar"], 7117)
        self.assertEqual(extracted["metadata"]["rejectedReasonCounts"], {})
        for change in ({"type": "MissionSystem"}, {"itemId": "source-1"},
                       {"quote": "MARS Mission System is a modular open architecture solution."}):
            altered = deepcopy(raw)
            altered["nodes"][0].update(change)
            rejected = extract_research_graph([material], lambda _: altered, identity_only=True, object_labels=["MARS Mission System"])
            self.assertEqual(rejected["nodes"], [])
            self.assertTrue(rejected["metadata"]["rejectedReasonCounts"])

    def test_saved_airbus_quote_hash_offsets_and_correct_object_scope(self):
        path = Path(__file__).resolve().parents[1] / "docs" / "fixtures" / "osint-uav-attributes-baseline-20261008.json"
        fixture = json.loads(path.read_text(encoding="utf-8"))
        material = fixture["selectedMaterial"]
        proposal = fixture["knownExtractableAttributes"][0]
        ref = proposal["evidence"]
        self.assertEqual(hashlib.sha256(material["body"].encode()).hexdigest(), material["contentHash"])
        self.assertEqual(material["body"][ref["startChar"]:ref["endChar"]], ref["quote"])
        objects = [{"id": "fixture-mission-system", "label": proposal["objectLabel"], "type": "Technology",
                    "evidenceIds": [material["itemId"]], "payload": {"attributes": [], "extractionMethod": "llm", "evidenceRefs": [deepcopy(ref)]}}]
        response = {"attributes": [{"name": proposal["name"], "attributeKey": proposal["attributeKey"],
                                    "value": proposal["rawValue"], "displayValue": proposal["displayValue"],
                                    "itemId": material["itemId"], "quote": ref["quote"]}]}
        retained = enrich_object_attributes([material], objects, lambda _: response)
        self.assertEqual(retained["metadata"]["retainedAttributes"], 1)
        self.assertEqual(retained["nodes"][0]["payload"]["attributes"][0]["claimKind"], "manufacturer-claim")
        self.assertFalse(retained["nodes"][0]["payload"]["attributes"][0]["truthVerified"])
        for wrong_label in proposal["wrongTargets"]:
            wrong = deepcopy(objects)
            wrong[0]["label"] = wrong_label
            result = enrich_object_attributes([material], wrong, lambda _: response)
            self.assertEqual(result["metadata"]["retainedAttributes"], 0)


if __name__ == "__main__":
    unittest.main()
