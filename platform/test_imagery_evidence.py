"""Isolated provenance/target checks for public imagery references.

No external network, live credentials, or application database is used.
"""
from __future__ import annotations

import hashlib
import importlib
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
    from imagery_evidence import ImageryEvidenceRegistry, public_https_reference
    from intelligence_agent import IntelligenceAgent, DeterministicClaimProvider, DeterministicTrendProvider
    from intelligence_store import IntelligenceStore, IntelligenceConflict
    from osint_store import OSINTTaskStore, OSINTValidationError
except ModuleNotFoundError:
    from .imagery_evidence import ImageryEvidenceRegistry, public_https_reference
    from .intelligence_agent import IntelligenceAgent, DeterministicClaimProvider, DeterministicTrendProvider
    from .intelligence_store import IntelligenceStore, IntelligenceConflict
    from .osint_store import OSINTTaskStore, OSINTValidationError


class ImageryEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.intelligence = IntelligenceStore(root / "intelligence.sqlite")
        self.agent = IntelligenceAgent(self.intelligence, provider=DeterministicClaimProvider(), trend_provider=DeterministicTrendProvider())
        self.osint = OSINTTaskStore(root / "osint.sqlite", intelligence=self.agent)
        # Insert immutable fixture material directly; never start a collector.
        self.task_id = "imagery-fixture-task"
        self.item_id = "imagery-fixture-item"
        body = "Public inspection camera laboratory test; optical sensor is documented."
        self.source = {"itemId": self.item_id, "sourceUri": "https://www.nasa.gov/example-publication", "sourceVersion": "fixture-v1", "contentHash": hashlib.sha256(body.encode()).hexdigest(), "title": "Public optical sensor test", "body": body}
        with self.osint.connect() as con:
            con.execute("INSERT INTO osint_task(task_id,title,question,keywords,source_ids,since_days,status,steps,errors,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)", (self.task_id, "Public sensor research", "Which public test context is documented?", "[]", "[]", 30, "completed", "[]", "[]", "2026-09-01T00:00:00Z", "2026-09-01T00:00:00Z"))
            con.execute("INSERT INTO osint_item(item_id,task_id,title,source_id,source_name,source_uri,body,content_hash,matched_keywords,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)", (self.item_id, self.task_id, self.source["title"], "fixture", "Fixture public publisher", self.source["sourceUri"], body, self.source["contentHash"], "[]", "2026-09-01T00:00:00Z"))
        draft = DeterministicTrendProvider().generate(task_id=self.task_id, title="Public sensor research", question="What is documented?", horizon_years=3, materials=[self.source])
        self.assessment = self.intelligence.create_assessment(draft)["assessment"]
        self.aid = self.assessment["assessmentId"]
        self.graph = self.intelligence.save_graph(self.aid, {"nodes": [{"id": "camera-1", "type": "EquipmentModel", "label": "Inspection camera", "evidenceIds": [self.item_id], "payload": {"attributes": [{"name": "sensor", "value": "optical"}]}}], "edges": []})
        self.registry = ImageryEvidenceRegistry(self.osint, self.intelligence)

    def tearDown(self):
        self.temp.cleanup()

    def payload(self, linked=True):
        record = {"title": "Public satellite archive reference", "referenceType": "archive", "mediaKind": "satellite-image", "sourceUrl": "https://www.nasa.gov/example-publication", "imageUrl": "https://www.nasa.gov/example-publication/image.jpg", "provider": "Public archive publisher", "captureDate": None, "publishedDate": "2026-09-01", "supportScope": "public-test-context", "observedContent": "Reference supplied for later pixel review; no object identification asserted.", "interpretation": "Potential background corroboration only, not a capability observation.", "supportStatement": "Compare with the public laboratory evaluation context.", "limitations": "Date and image type require manual verification.", "sourceItemId": self.item_id}
        if linked:
            record.update(assessmentId=self.aid, graphRevisionId=self.graph["graphRevisionId"], graphElementId="camera-1", propertyPath="payload.attributes[0].value")
        return record

    def test_immutable_duplicate_reference_is_pending_and_does_not_promote_confidence(self):
        with patch("socket.getaddrinfo", side_effect=AssertionError("No DNS request permitted")):
            first = self.registry.attach(self.task_id, self.payload())
            repeat = self.registry.attach(self.task_id, self.payload())
        self.assertFalse(first["duplicate"])
        self.assertTrue(repeat["duplicate"])
        reference = first["imageryEvidence"]
        self.assertEqual(reference["id"], repeat["imageryEvidence"]["id"])
        self.assertEqual(reference["captureDateStatus"], "unknown")
        self.assertEqual(reference["targetPropertySnapshot"], "optical")
        self.assertEqual(reference["sourceContentHash"], self.source["contentHash"])
        self.assertEqual(reference["sourceRelationship"], "assessment-source-snapshot")
        self.assertEqual(reference["sourceReferenceMatch"], "source-item-url")
        self.assertFalse(reference["confidencePromotion"])
        self.assertFalse(reference["truthVerified"])
        self.assertFalse(reference["fetched"])
        self.assertEqual(reference["reviewStatus"], "pending-review")
        self.assertEqual(self.intelligence.get_assessment(self.aid), self.assessment)
        self.assertEqual(self.intelligence.list_admissions(), [])
        with self.osint.connect() as con:
            for mutation in ("UPDATE osint_imagery_evidence SET payload='{}'", "DELETE FROM osint_imagery_evidence"):
                with self.assertRaises(sqlite3.IntegrityError):
                    con.execute(mutation)

    def test_unlinked_reference_does_not_masquerade_as_verified_object_evidence(self):
        result = self.registry.attach(self.task_id, self.payload(linked=False))["imageryEvidence"]
        self.assertEqual(result["linkageStatus"], "pending-link")
        self.assertIsNone(result["graphElementId"])
        self.assertFalse(self.registry.list(self.task_id)[0]["staleGraph"])

    def test_unknown_dates_and_future_or_inconsistent_dates_are_rejected(self):
        for change in ({"captureDate": "unknown"}, {"captureDate": "2026-02-30"}, {"captureDate": "2099-01-01"}, {"captureDate": "2026-09-02", "publishedDate": "2026-09-01"}):
            with self.subTest(change=change), self.assertRaises(OSINTValidationError):
                self.registry.attach(self.task_id, {**self.payload(), **change})

    def test_local_credentials_and_invented_publishers_rejected(self):
        for url in ("http://www.nasa.gov/image", "https://localhost/image", "https://127.0.0.1/image", "https://10.0.0.2/image", "https://[::1]/image", "https://user:secret@www.nasa.gov/image", "https://www.nasa.gov:8776/image", "https://www.nasa.gov/image?api_key=secret", "https://www.nasa.gov\\@localhost/image", "https://2130706433/image", "https://127.1/image", "https://host.local/image"):
            with self.subTest(url=url), self.assertRaises(OSINTValidationError):
                public_https_reference(url)
        for url in ("https://www.nasa.gov.attacker.com/image", "https://invented-provider.net/image"):
            with self.subTest(url=url), self.assertRaises(OSINTValidationError):
                self.registry.attach(self.task_id, {**self.payload(False), "sourceItemId": None, "sourceUrl": url})

    def test_valid_archive_reference_still_requires_human_provenance_review(self):
        reference = self.registry.attach(self.task_id, {**self.payload(False), "sourceItemId": None, "sourceUrl": "https://dataspace.copernicus.eu/public-archive"})["imageryEvidence"]
        self.assertIsNone(reference["sourceContentHash"])
        self.assertEqual(reference["sourceVerification"], "pending-human-review")
        self.assertFalse(reference["confidencePromotion"])

    def test_exact_source_target_and_property_must_exist(self):
        for change in ({"sourceItemId": "unknown-source"}, {"sourceUrl": "https://www.nasa.gov/other-page"}, {"graphElementId": "unknown-node"}, {"propertyPath": "payload.attributes[9].value"}, {"propertyPath": "payload.cooperationScore"}, {"assessmentId": None}, {"confidencePromotion": True}, {"supportScope": "combat-coordination"}):
            with self.subTest(change=change), self.assertRaises(OSINTValidationError):
                self.registry.attach(self.task_id, {**self.payload(), **change})

    def test_graph_revision_staleness_cannot_transfer_evidence_to_new_objects(self):
        self.registry.attach(self.task_id, self.payload())
        self.intelligence.save_graph(self.aid, {"nodes": [{"id": "camera-2", "type": "EquipmentModel", "label": "Another camera", "evidenceIds": [self.item_id], "payload": {}}], "edges": []})
        self.assertTrue(self.registry.list(self.task_id)[0]["staleGraph"])
        with self.assertRaises(IntelligenceConflict):
            self.registry.attach(self.task_id, {**self.payload(), "title": "New reference"})

    def test_cross_task_graph_target_is_rejected(self):
        draft = DeterministicTrendProvider().generate(task_id="other-task", title="Other task", question="What is documented?", horizon_years=3, materials=[self.source])
        other = self.intelligence.create_assessment(draft)["assessment"]
        with self.assertRaises(OSINTValidationError):
            self.registry.attach(self.task_id, {**self.payload(), "assessmentId": other["assessmentId"]})

    def test_discovered_image_links_as_supplement_without_rewriting_text_report(self):
        image_item_id = "image-discovery-material"
        metadata = json.dumps({"title": "Public imagery discovery", "sourceUrl": "https://dataspace.copernicus.eu/public-archive", "imageUrl": "https://dataspace.copernicus.eu/discovery-image.jpg"}, sort_keys=True)
        image_hash = hashlib.sha256(metadata.encode()).hexdigest()
        # The image-only material has no scraped text and never enters the
        # model report source snapshot, which contains only the text fixture.
        with self.osint.connect() as con:
            con.execute("INSERT INTO osint_item(item_id,task_id,title,source_id,source_name,source_uri,body,content_hash,matched_keywords,provenance,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)", (image_item_id, self.task_id, "Public imagery discovery", "public-imagery", "Public imagery archive", "https://dataspace.copernicus.eu/public-archive", "", image_hash, "[]", json.dumps({"retrievalKind": "search-discovery-only", "contentKind": "image-search-metadata"}), "2026-09-01T00:00:00Z"))
        report_before = self.intelligence.get_assessment(self.aid)
        graph_before = self.intelligence.get_graph(self.aid)
        payload = {**self.payload(), "sourceItemId": image_item_id, "sourceUrl": "https://dataspace.copernicus.eu/public-archive", "imageUrl": "https://dataspace.copernicus.eu/discovery-image.jpg"}
        reference = self.registry.attach(self.task_id, payload)["imageryEvidence"]
        self.assertEqual(reference["sourceRelationship"], "supplemental-task-material")
        self.assertEqual(reference["sourceContentHash"], image_hash)
        self.assertEqual(reference["sourceSnapshotHash"], self.intelligence._snapshot_hash(report_before["sourceSnapshot"]))
        self.assertEqual(reference["graphRevisionId"], graph_before["graphRevisionId"])
        self.assertEqual(reference["graphContentHash"], graph_before["contentHash"])
        self.assertFalse(reference["confidencePromotion"])
        self.assertFalse(reference["truthVerified"])
        self.assertEqual(self.intelligence.get_assessment(self.aid), report_before)
        self.assertEqual(self.intelligence.get_graph(self.aid), graph_before)
        self.assertEqual(self.intelligence.list_admissions(), [])

    def test_deduplicated_material_only_accepts_exact_saved_media_reference_pair(self):
        merged_id = "deduplicated-media-material"
        original_url = "https://dataspace.copernicus.eu/archive-entry"
        saved_url = "https://dataspace.copernicus.eu/archive-entry#discovery-image"
        image_url = "https://dataspace.copernicus.eu/saved-image.jpg"
        provenance = {"retrievalKind": "search-discovery-only", "contentKind": "image-search-metadata", "mediaReferences": [{"sourceUrl": saved_url, "imageUrl": image_url, "mediaKind": "unclassified-image"}]}
        with self.osint.connect() as con:
            con.execute("INSERT INTO osint_item(item_id,task_id,title,source_id,source_name,source_uri,body,content_hash,matched_keywords,provenance,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)", (merged_id, self.task_id, "Deduplicated public imagery", "public-imagery", "Public archive", original_url, "", "c" * 64, "[]", json.dumps(provenance), "2026-09-01T00:00:00Z"))
        payload = {**self.payload(), "sourceItemId": merged_id, "sourceUrl": saved_url, "imageUrl": image_url}
        reference = self.registry.attach(self.task_id, payload)["imageryEvidence"]
        self.assertEqual(reference["sourceReferenceMatch"], "saved-media-reference")
        self.assertEqual(reference["sourceRelationship"], "supplemental-task-material")
        self.assertEqual(reference["sourceContentHash"], "c" * 64)
        for change in ({"imageUrl": "https://dataspace.copernicus.eu/different-image.jpg"}, {"imageUrl": None}, {"sourceUrl": "https://www.nasa.gov/unrelated-publication"}):
            with self.subTest(change=change), self.assertRaises(OSINTValidationError):
                self.registry.attach(self.task_id, {**payload, **change})

    def test_http_routes_match_reference_only_contract(self):
        prefix = "demo." if IntelligenceAgent.__module__.startswith("demo.") else ""
        secrets_module = importlib.import_module(prefix + "provider_secrets")
        agent_module = importlib.import_module(prefix + "intelligence_agent")
        runtime_module = importlib.import_module(prefix + "runtime_store")
        osint_module = importlib.import_module(prefix + "osint_store")
        with patch.object(secrets_module, "load_provider_environment"), patch.object(agent_module, "IntelligenceAgent", return_value=self.agent), patch.object(runtime_module, "RuntimeStore", return_value=object()), patch.object(osint_module, "OSINTTaskStore", return_value=object()):
            serve = importlib.import_module(prefix + "serve")
        with patch.object(serve, "INTELLIGENCE_AGENT", self.agent), patch.object(serve, "OSINT_STORE", self.osint), patch.object(serve.Handler, "log_message", lambda *_args: None):
            server = serve.ThreadingHTTPServer(("127.0.0.1", 0), serve.Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_address[1]}"
            endpoint = f"/api/osint/tasks/{self.task_id}/imagery-evidence"
            try:
                request = Request(base + endpoint, data=json.dumps(self.payload()).encode(), headers={"Content-Type": "application/json"})
                with urlopen(request, timeout=5) as response:
                    attached = json.load(response)
                    self.assertEqual(response.status, 201)
                    self.assertFalse(attached["confidencePromotion"])
                with urlopen(base + endpoint, timeout=5) as response:
                    listed = json.load(response)
                    self.assertEqual(listed["imageryEvidence"][0]["id"], attached["imageryEvidence"]["id"])
                with self.assertRaises(HTTPError) as context:
                    urlopen(base + "/api/osint/tasks/unknown/imagery-evidence", timeout=5)
                self.assertEqual(context.exception.code, 404)
                context.exception.close()
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
