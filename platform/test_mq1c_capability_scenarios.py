"""MQ-1C end-to-end capability scenarios on a disposable copy of the archive."""
import hashlib
import tempfile
import unittest
from pathlib import Path

from .capability_profile import CapabilityProfiles
from .capability_workflow import CapabilityWorkflow
from .intelligence_agent import IntelligenceAgent
from .intelligence_store import IntelligenceConflict, IntelligenceStore
from .ontology_agent import OntologyAgent
from .equipment_verification import EquipmentVerificationQueue


ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_DB = ROOT / "ontology" / "coast-raid-k" / "coast-raid-k-intelligence.sqlite"
MQ1C_NAME = "MQ-1C Gray Eagle"


class ScenarioSearch:
    def __init__(self, bodies):
        self.bodies = list(bodies)

    def search(self, *args, **kwargs):
        items = []
        for index, body in enumerate(self.bodies):
            uri = f"https://scenario.example/mq1c/{index}"
            items.append({
                "title": f"MQ-1C public capability report {index + 1}",
                "sourceUri": uri,
                "itemUri": uri,
                "body": body,
                "contentHash": hashlib.sha256(body.encode()).hexdigest(),
                "sourceVersion": f"scenario-{index + 1}",
            })
        return {"fetchedAt": "2026-10-14T00:00:00Z", "items": items, "errors": [], "budget": {"stopReason": "target-reached"}}


class ScenarioClaims:
    provider_id = "scenario-claim-provider"
    model = "deepseek-v4-pro"

    def __init__(self, values):
        self.values = list(values)

    def extract(self, document):
        target = document["targetEntity"]["preferredName"]
        claims = []
        for value, quote, source_index in self.values:
            claims.append({
                "attributeKey": "flight-range",
                "sourceSubject": target,
                "subject": target,
                "value": value,
                "rawValue": value,
                "unit": "km",
                "conditions": "operational test configuration",
                "variant": "MQ-1C",
                "configuration": "operational test",
                "quote": quote,
                "lineStart": 1,
                "lineEnd": 1,
                "sourceItemIndex": source_index,
                "extractionConfidence": 0.98,
                "qualifier": "exact",
                "recommendation": {"analysisType": "source-fact"},
            })
        return claims


class ScenarioTrend:
    configured = True

    def __init__(self, values):
        self.claim_provider = ScenarioClaims(values)


class MQ1CCapabilityScenarioTests(unittest.TestCase):
    def setUp(self):
        self.archive_hash = hashlib.sha256(PRODUCTION_DB.read_bytes()).hexdigest()

    def tearDown(self):
        self.assertEqual(hashlib.sha256(PRODUCTION_DB.read_bytes()).hexdigest(), self.archive_hash)

    def disposable_agent(self, bodies, values):
        temp = tempfile.TemporaryDirectory()
        path = Path(temp.name) / "mq1c-scenario.sqlite"
        with IntelligenceStore(PRODUCTION_DB)._connect() as source, IntelligenceStore(path)._connect() as target:
            source.backup(target)
        store = IntelligenceStore(path)
        agent = OntologyAgent(store, trend_provider=ScenarioTrend(values))
        agent.queue = EquipmentVerificationQueue(store, ScenarioSearch(bodies))
        entity = next(item for item in agent._entities() if item["preferredName"] == MQ1C_NAME)
        return temp, store, agent, entity["entityId"]

    def execute_scenario(self, bodies, values, *, capability_codes=None, force=True):
        temp, store, agent, entity_id = self.disposable_agent(bodies, values)
        workflow = CapabilityWorkflow(agent)
        result = workflow.submit({
            "capabilityCodes": capability_codes or ["platform"],
            "maxJobs": 1,
            "maxModelCalls": 1,
            "maxElapsedSeconds": 60,
            "force": force,
        }, entity_id, background=False)
        return temp, store, agent, entity_id, result

    def test_evidence_sufficient_runs_to_explicit_publication_and_detailed_readback(self):
        quote = "MQ-1C Gray Eagle flight range 600 km under operational test configuration."
        temp, store, agent, entity_id, run = self.execute_scenario([quote], [("600", quote, 0)])
        try:
            self.assertEqual(run["status"], "awaiting-human-review")
            profiles = CapabilityProfiles(agent)
            profile = profiles.get(run["result"]["profileId"])
            record = next(m for c in profile["capabilities"] for m in c["metrics"] if m["attributeKey"] == "flight-range" and m["origin"] == "candidate")
            profiles.review(profile["profileId"], {"recordId": record["id"], "action": "accept", "reviewer": "scenario reviewer", "reason": "Matched the MQ-1C quote and configuration", "verifiedConfidence": "high"})
            publication = profiles.publish(profile["profileId"], {"recordIds": [record["id"]], "publishedBy": "scenario reviewer", "reason": "Explicit isolated scenario publication"})
            self.assertTrue(publication["ok"])
            detailed = store.published_ontology(detailed=True)
            self.assertIn("CapabilityMetric", {item["type"] for item in detailed["objects"]})
            self.assertIn("SourceDocument", {item["type"] for item in detailed["objects"]})
            self.assertIn("supportedBy", {edge["relation"] for edge in detailed["relations"]})
            self.assertEqual(profiles.read(entity_id)["quality"]["verifiedCount"], 1)
        finally:
            temp.cleanup()

    def test_material_gap_finishes_with_gap_and_keeps_unknown(self):
        temp, store, agent, entity_id, run = self.execute_scenario([], [], capability_codes=["communications"])
        try:
            self.assertEqual(run["status"], "completed-with-gaps")
            profile = CapabilityProfiles(agent).get(run["result"]["profileId"])
            metric = next(m for c in profile["capabilities"] for m in c["metrics"] if m["attributeKey"] == "communication-range")
            self.assertEqual(metric["claimState"], "unknown")
            self.assertEqual(metric["missingReason"], "本次检索未找到资料")
            self.assertEqual(profile["quality"]["verifiedCount"], 0)
        finally:
            temp.cleanup()

    def test_conflicting_sources_require_resolution_before_publication(self):
        first = "MQ-1C Gray Eagle flight range 600 km under operational test configuration."
        second = "MQ-1C Gray Eagle flight range 700 km under operational test configuration."
        temp, store, agent, entity_id, run = self.execute_scenario([first, second], [("600", first, 0), ("700", second, 1)])
        try:
            self.assertEqual(run["status"], "awaiting-human-review")
            profiles = CapabilityProfiles(agent)
            profile = profiles.get(run["result"]["profileId"])
            records = [m for c in profile["capabilities"] for m in c["metrics"] if m["attributeKey"] == "flight-range" and m["origin"] == "candidate"]
            self.assertEqual(len(records), 2)
            self.assertTrue(all(record["claimState"] == "conflict" for record in records))
            with self.assertRaises(IntelligenceConflict):
                profiles.review(profile["profileId"], {"recordId": records[0]["id"], "action": "accept", "reviewer": "scenario reviewer", "reason": "Conflict must be resolved", "verifiedConfidence": "high"})
            self.assertEqual(store.published_ontology(detailed=True)["admissionCount"], 0)
        finally:
            temp.cleanup()


if __name__ == "__main__":
    unittest.main()
