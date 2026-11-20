"""Contracts for published graph records and the read-only KB attachment."""
from copy import deepcopy
import json
from pathlib import Path
import unittest

from jsonschema import Draft202012Validator, ValidationError

try:
    from intelligence_ontology import attach_intelligence_ontology
    from intelligence_store import GRAPH_RELATION_TYPES
    import test_graph_admissions as graph_fixture
    import test_governance_store as capability_fixture
except ModuleNotFoundError:
    from .intelligence_ontology import attach_intelligence_ontology
    from .intelligence_store import GRAPH_RELATION_TYPES
    from . import test_graph_admissions as graph_fixture
    from . import test_governance_store as capability_fixture

ROOT = Path(__file__).resolve().parents[1] / "ontology" / "coast-raid-k"


class IntelligenceProjectionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = graph_fixture.GraphAdmissionTests()
        self.fixture.setUp()
        self.schema = json.loads((ROOT / "schema.intelligence.json").read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(self.schema)

    def tearDown(self):
        self.fixture.tearDown()

    def validate(self, definition, value):
        validator = Draft202012Validator({"$ref": "#/$defs/" + definition, "$defs": self.schema["$defs"]})
        validator.validate(value)

    def published(self):
        proposals = self.fixture.propose(["org", "camera", "test"], ["manufactures"])
        for proposal in proposals:
            self.validate("OntologyAdmission", proposal)
            accepted = self.fixture.accept(proposal)
            self.validate("OntologyAdmission", accepted)
            published = self.fixture.publish(accepted)
            self.validate("OntologyAdmission", published)
        layer = self.fixture.store.published_ontology()
        for record in layer["objects"]:
            self.validate("PublishedIntelligenceObject", record)
        return layer

    def test_published_entities_events_relations_attach_with_declared_classes(self):
        layer = self.published()
        boot = {"files": {"tbox.json": {"classes": []}}, "inventory": {"entries": []}}
        original = deepcopy(boot)
        attached = attach_intelligence_ontology(boot, layer, ROOT)
        self.assertEqual(boot, original)
        runtime = [e for e in attached["inventory"]["entries"] if e["file"] == "intelligence-ontology.json"]
        self.assertEqual({e["type"] for e in runtime}, {"Organization", "EquipmentModel", "TestEvent"})
        self.assertEqual(attached["intelligenceOntology"]["relationCount"], 1)
        self.assertTrue(all(e["module"] == "Knowledge" for e in runtime))
        self.assertIn("schema.intelligence.json", attached["files"])
        self.assertEqual(attached["files"]["intelligence-ontology.json"]["provenance"], layer["provenance"])
        again = attach_intelligence_ontology(attached, layer, ROOT)
        self.assertEqual(len(again["inventory"]["entries"]), len(attached["inventory"]["entries"]))

    def test_projection_rejects_undeclared_vocab_and_dangling_relation(self):
        layer = self.published()
        boot = {"files": {"tbox.json": {"classes": []}}, "inventory": {"entries": []}}
        for field, value in [("type", "EventRecord"), ("ontologyClass", "https://example.test/Class")]:
            bad = deepcopy(layer)
            bad["objects"][0][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                attach_intelligence_ontology(boot, bad, ROOT)
        for field, value in [("relation", "relatedTo"), ("predicate", "https://example.test/relationship"), ("to", "missing-object")]:
            bad = deepcopy(layer)
            bad["relations"][0][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                attach_intelligence_ontology(boot, bad, ROOT)

    def test_contract_cannot_mix_graph_kinds_or_invent_attributes_and_confidence(self):
        proposal = self.fixture.propose(["org"])[0]
        for mutate in [
            lambda p: p.update(kind="capability-metric"),
            lambda p: p["assertion"]["attributes"].update(arbitraryFact=999),
            lambda p: p.update(proposedConfidence="high"),
            lambda p: p.update(status="accepted"),
        ]:
            bad = deepcopy(proposal)
            mutate(bad)
            with self.assertRaises(ValidationError):
                self.validate("OntologyAdmission", bad)

    def test_legacy_capability_admissions_keep_their_original_contract(self):
        fixture = capability_fixture.GovernanceStoreTests()
        fixture.setUp()
        try:
            proposal = fixture._propose()
            self.validate("OntologyAdmission", proposal)
            accepted = fixture.store.review_admission(proposal["admissionId"], {"action": "accept", "reviewer": "Analyst", "reason": "Checked equipment metric and conditions", "expectedRevision": proposal["revision"]})
            self.validate("OntologyAdmission", accepted)
            fixture.store.publish_admission(proposal["admissionId"], {"reviewer": "Publisher", "expectedRevision": accepted["revision"]})
            for record in fixture.store.published_ontology()["objects"]:
                self.validate("PublishedIntelligenceObject", record)
        finally:
            fixture.tearDown()

    def test_every_admission_relation_maps_compatible_declared_vocabulary(self):
        tbox = json.loads((ROOT / "tbox.json").read_text(encoding="utf-8"))
        classes = {c["id"]: c for c in tbox["classes"]}
        properties = {p["id"]: p for p in tbox["objectProperties"]}
        def ancestors(kind):
            seen, pending = set(), [kind]
            while pending:
                current = pending.pop()
                if current in seen:
                    continue
                seen.add(current)
                pending.extend(classes[current].get("subClassOf", []))
            return seen
        for name, (domains, ranges, mapped, inverse) in GRAPH_RELATION_TYPES.items():
            self.assertIn(mapped, properties)
            if inverse:
                domains, ranges = ranges, domains
            for domain in domains:
                self.assertIn(properties[mapped]["domain"], ancestors(domain), name)
            for range_ in ranges:
                self.assertIn(properties[mapped]["range"], ancestors(range_), name)


if __name__ == "__main__":
    unittest.main()
