"""Isolation tests for graph facts' human admission and publication boundary."""

from copy import deepcopy
from contextlib import closing
import hashlib
from pathlib import Path
import sqlite3
import tempfile
import unittest

try:
    from intelligence_agent import IntelligenceAgent, DeterministicTrendProvider
    from intelligence_store import IntelligenceStore, IntelligenceConflict, IntelligenceValidationError
    from evidence_graph import source_metadata
except ModuleNotFoundError:
    from .intelligence_agent import IntelligenceAgent, DeterministicTrendProvider
    from .intelligence_store import IntelligenceStore, IntelligenceConflict, IntelligenceValidationError
    from .evidence_graph import source_metadata


class GraphAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = IntelligenceStore(Path(self.temp.name) / "test.sqlite")
        self.agent = IntelligenceAgent(self.store, trend_provider=DeterministicTrendProvider())
        body = "Acme manufactures Camera X. In Project P, Acme announced a public camera test."
        source = {"itemId": "s1", "title": "Public camera announcement", "sourceUri": "https://example.test/test", "sourceVersion": "v1", "contentHash": hashlib.sha256(body.encode()).hexdigest(), "body": body}
        draft = DeterministicTrendProvider().generate(task_id="test-task", title="Public camera records", question="What was reported?", horizon_years=3, materials=[source])
        self.assessment = self.store.create_assessment(draft)["assessment"]
        self.aid = self.assessment["assessmentId"]
        self.ref = {key: source[key] for key in ("itemId", "sourceUri", "sourceVersion", "contentHash")}
        self.ref.update({"quote": body, "startChar": 0, "endChar": len(body), "sourceRole": "official-publication", "verificationStatus": "unverified"})
        self.canonical_ref = {**self.ref, **source_metadata(source)}
        def node(nid, kind, label, **payload):
            return {"id": nid, "type": kind, "label": label, "evidenceIds": ["s1"], "payload": {"evidenceRefs": [deepcopy(self.ref)], "confidence": "high", **payload}}
        self.graph_data = {"nodes": [node("org", "Organization", "Acme", arbitraryFact=999), node("camera", "EquipmentModel", "Camera X"), node("test", "EventRecord", "公开相机试验", eventKind="TestEvent", testTemperature=100)], "edges": [{"id": "manufactures", "from": "org", "to": "camera", "relation": "manufactures", "evidenceIds": ["s1"], "payload": {"evidenceRefs": [deepcopy(self.ref)], "confidence": "high"}}]}
        self.graph = self.store.save_graph(self.aid, self.graph_data)

    def tearDown(self):
        self.temp.cleanup()

    def propose(self, nodes=None, edges=None, **extra):
        selection = {"graphRevisionId": self.graph["graphRevisionId"], "graphNodeIds": nodes or [], "graphEdgeIds": edges or [], **extra}
        return self.agent.propose_ontology_admissions(self.aid, graph_selection=selection)

    def accept(self, admission, **extra):
        return self.store.review_admission(admission["admissionId"], {"action": "accept", "reviewer": "Research reviewer", "reason": "Checked quoted assertion, its meaning and attribution against original record", "expectedRevision": admission["revision"], "verifiedConfidence": "high", **extra})

    def publish(self, admission):
        return self.store.publish_admission(admission["admissionId"], {"reviewer": "Publisher", "expectedRevision": admission["revision"]})

    def test_candidates_are_server_derived_and_unsupported_attributes_omitted(self):
        org, event = self.propose(["org", "test"])
        self.assertEqual(org["proposedConfidence"], "medium")
        self.assertEqual(org["assertion"]["attributes"], {})
        self.assertEqual(event["assertion"]["attributes"], {"eventKind": "TestEvent"})
        self.assertTrue(event["ontologyClass"].endswith("#TestEvent"))
        self.assertEqual(org["assertion"]["evidenceRefs"], [self.canonical_ref])
        self.assertEqual(org["assertion"]["evidenceRefs"][0]["sourceRole"], "unclassified")
        self.assertEqual(self.propose(["org"])[0]["admissionId"], org["admissionId"])
        for key in ("assertion", "mapping", "proposedConfidence"):
            with self.subTest(key=key), self.assertRaises(IntelligenceValidationError):
                self.propose(["org"], **{key: {"madeUp": "fact"}})

    def test_selection_cannot_cross_assessment_or_accept_unknown_ids(self):
        for nodes, edges in [(["foreign-node"], []), ([], ["foreign-edge"]), ([], [])]:
            with self.subTest(nodes=nodes), self.assertRaises(IntelligenceValidationError):
                self.propose(nodes, edges)
        with self.assertRaises(IntelligenceConflict):
            self.propose(["org"], graphRevisionId="foreign-graph-revision")

    def test_requires_exact_quote_even_when_evidence_id_exists(self):
        changed = deepcopy(self.graph_data)
        changed["nodes"][0]["payload"]["evidenceRefs"] = []
        self.graph = self.store.save_graph(self.aid, changed)
        with self.assertRaises(IntelligenceValidationError):
            self.propose(["org"])
        bad_ref = deepcopy(self.graph_data)
        bad_ref["nodes"][0]["payload"]["evidenceRefs"][0]["quote"] = "fabricated text"
        with self.assertRaises(IntelligenceValidationError):
            self.store.save_graph(self.aid, bad_ref)

    def test_source_and_trend_nodes_are_not_factual_entities(self):
        for kind in ("EvidenceSource", "TrendHypothesis", "CapabilityObservation", "DeploymentEvent"):
            changed = deepcopy(self.graph_data)
            changed["nodes"][0]["type"] = kind
            self.graph = self.store.save_graph(self.aid, changed)
            with self.subTest(kind=kind), self.assertRaises(IntelligenceValidationError):
                self.propose(["org"])

    def test_high_confidence_requires_explicit_human_acceptance_and_publish_gate(self):
        admission = self.propose(["org"])[0]
        with self.assertRaises(IntelligenceConflict):
            self.publish(admission)
        for confidence in (None, "low", "medium", "model-high"):
            with self.subTest(confidence=confidence), self.assertRaises(IntelligenceValidationError):
                self.accept(admission, verifiedConfidence=confidence)
        accepted = self.accept(admission)
        self.assertEqual(accepted["review"]["verifiedConfidence"], "high")
        self.assertEqual(self.store.published_ontology()["admissionCount"], 0)
        self.publish(accepted)
        trusted = self.store.published_ontology()
        self.assertEqual(trusted["objects"][0]["verifiedConfidence"], "high")
        self.assertEqual(trusted["provenance"][0]["evidenceRefs"], [self.canonical_ref])
        self.assertEqual(trusted["provenance"][0]["graphRevisionId"], self.graph["graphRevisionId"])
        with self.assertRaises(sqlite3.DatabaseError), self.store._connect() as con:
            con.execute("UPDATE intelligence_ontology_admission_review SET verified_confidence='low'")

    def test_stale_graph_blocks_creation_review_and_publication(self):
        org, camera = self.propose(["org", "camera"])
        accepted = self.accept(org)
        old_graph = deepcopy(self.graph)
        changed = deepcopy(self.graph_data)
        changed["nodes"][0]["payload"]["confidence"] = "low"
        self.graph = self.store.save_graph(self.aid, changed)
        with self.assertRaises(IntelligenceConflict):
            self.propose(["org"], graphRevisionId=old_graph["graphRevisionId"])
        with self.assertRaises(IntelligenceConflict):
            self.accept(camera)
        with self.assertRaises(IntelligenceConflict):
            self.publish(accepted)
        self.assertTrue(all(item["staleGraph"] for item in self.store.list_admissions(self.aid)))
        new = self.propose(["org"])[0]
        self.assertNotEqual(new["admissionId"], org["admissionId"])
        self.assertNotEqual(new["candidateIri"], org["candidateIri"])
        self.assertFalse(new["staleGraph"])

    def test_latest_extraction_never_rewrites_published_endpoint_class(self):
        old = self.propose(["org"])[0]
        self.publish(self.accept(old))
        changed = deepcopy(self.graph_data)
        changed["nodes"][0]["type"] = "Technology"
        self.graph = self.store.save_graph(self.aid, changed)
        current = self.propose(["org"])[0]
        self.publish(self.accept(current))
        objects = {obj["iri"]: obj for obj in self.store.published_ontology()["objects"]}
        self.assertEqual(objects[old["candidateIri"]]["type"], "Organization")
        self.assertEqual(objects[current["candidateIri"]]["type"], "Technology")

    def test_opening_existing_database_adds_review_confidence_without_rewriting_data(self):
        legacy_path = Path(self.temp.name) / "legacy.sqlite"
        with closing(sqlite3.connect(legacy_path)) as con:
            con.execute("CREATE TABLE intelligence_ontology_admission_review (review_id TEXT PRIMARY KEY, admission_id TEXT NOT NULL, action TEXT NOT NULL, reviewer TEXT NOT NULL, reason TEXT NOT NULL, expected_revision INTEGER NOT NULL, created_at TEXT NOT NULL)")
            con.execute("INSERT INTO intelligence_ontology_admission_review VALUES ('old-review','old-admission','accept','A','Historical review',1,'2026-09-01T00:00:00Z')")
            con.commit()
        legacy = IntelligenceStore(legacy_path)
        with legacy._connect() as con:
            review = con.execute("SELECT * FROM intelligence_ontology_admission_review WHERE review_id='old-review'").fetchone()
            self.assertIsNone(review["verified_confidence"])
            self.assertEqual(review["reason"], "Historical review")

    def test_relation_inverse_mapping_requires_published_endpoints(self):
        org, camera, relation = self.propose(["org", "camera"], ["manufactures"])
        self.assertEqual(relation["assertion"]["relation"], "manufacturedBy")
        self.assertEqual(relation["assertion"]["from"], camera["candidateIri"])
        self.assertEqual(relation["assertion"]["to"], org["candidateIri"])
        accepted_relation = self.accept(relation)
        with self.assertRaises(IntelligenceConflict):
            self.publish(accepted_relation)
        self.publish(self.accept(org))
        with self.assertRaises(IntelligenceConflict):
            self.publish(accepted_relation)
        self.publish(self.accept(camera))
        self.publish(accepted_relation)
        trusted = self.store.published_ontology()
        self.assertEqual(len(trusted["objects"]), 2)
        self.assertEqual(len(trusted["relations"]), 1)
        self.assertEqual(trusted["relations"][0]["from"], camera["candidateIri"])

    def test_unmapped_or_invalid_relation_endpoints_stay_research_context(self):
        for relation_name in ("relatedTo", "testsEquipment", "inventedPredicate"):
            changed = deepcopy(self.graph_data)
            changed["edges"][0]["relation"] = relation_name
            self.graph = self.store.save_graph(self.aid, changed)
            with self.subTest(relation=relation_name), self.assertRaises(IntelligenceValidationError):
                self.propose(edges=["manufactures"])

    def test_parent_withdrawal_retracts_projection_but_preserves_audit(self):
        admission = self.propose(["test"])[0]
        self.publish(self.accept(admission))
        accepted = self.store.review_assessment(self.aid, {"action": "accept", "reviewer": "A", "reason": "Reviewed evidence", "expectedRevision": 1})
        self.store.publish_assessment(self.aid, {"reviewer": "A", "expectedRevision": accepted["revision"]})
        self.store.withdraw_assessment_publication(self.aid, {"actor": "A", "reason": "Publisher corrected source"})
        self.assertEqual(self.store.published_ontology()["objects"], [])
        self.assertEqual(self.store.list_admissions(self.aid)[0]["status"], "withdrawn")
        with self.store._connect() as con:
            self.assertEqual(con.execute("SELECT status FROM intelligence_ontology_admission").fetchone()[0], "published")
        with self.assertRaises(IntelligenceConflict):
            self.propose(["test"])


if __name__ == "__main__":
    unittest.main()
