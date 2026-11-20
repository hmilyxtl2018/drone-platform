import tempfile
import unittest
from pathlib import Path

try:
    from intelligence_store import IntelligenceStore
    from ontology_workbook_import import import_workbook
    from ontology_agent import OntologyAgent, ontology_taxonomy
    from equipment_verification import EquipmentVerificationQueue
except ModuleNotFoundError:  # pragma: no cover
    from .intelligence_store import IntelligenceStore
    from .ontology_workbook_import import import_workbook
    from .ontology_agent import OntologyAgent, ontology_taxonomy
    from .equipment_verification import EquipmentVerificationQueue

from openpyxl import Workbook


class FakeSearchProvider:
    def search(self, query, keywords, *, since_days, max_items, source_ids):
        body = "Test UAV endurance is 30 h and payload is 200 kg according to a public test report."
        return {
            "fetchedAt": "2026-10-11T00:00:00Z",
            "items": [{
                "title": "Test UAV public test report",
                "sourceUri": "https://example.test/test-uav",
                "itemUri": "https://example.test/test-uav",
                "sourceVersion": "v1",
                "publishedAt": "2026-01-01T00:00:00Z",
                "body": body,
                "contentHash": "hash-test-uav",
                "sourceReliability": "secondary",
            }],
            "errors": [],
        }


class FakeClaimProvider:
    provider_id = "fake-deepseek"
    model = "deepseek-v4-pro"

    def extract(self, document):
        return [{
            "statement": "Test UAV endurance is 30 h.",
            "predicate": "endurance",
            "value": "30 h",
            "quote": "Test UAV endurance is 30 h",
            "lineStart": 1,
            "lineEnd": 1,
        }]


class FakeTrendProvider:
    configured = True
    claim_provider = FakeClaimProvider()

    def metadata(self):
        return {"id": "deepseek-trend-provider", "model": "deepseek-v4-pro", "configured": True}


def fixture_workbook(path: Path) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "主表_全部机型"
    sheet.append(["测试装备本体"])
    sheet.append(["只读导入测试"])
    sheet.append(["序号", "类别", "子类别", "型号", "英文名", "原产国", "制造商", "首飞年", "服役年", "状态", "最大速度 km/h", "最大航程 km", "续航 h", "有效载荷 kg", "蜂群兼容", "主要使用国", "实战记录"])
    sheet.append([1, "察打一体", "MALE UCAV", "Test UAV", "Test UAV", "测试国", "Test Works", 2024, 2025, "试验", 300, 1200, 24, 150, "是", "测试国,伙伴国", "是(测试记录)"])
    sheet.append([2, "巡飞弹", "Loitering Munition", "Test Loiter", "Test Loiter", "伙伴国", "Loiter Works", 2022, 2024, "服役", 180, 300, 2, 20, "否", "伙伴国", "否"])
    workbook.save(path)


class OntologyAgentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.database = root / "intelligence.sqlite"
        self.workbook = root / "equipment.xlsx"
        fixture_workbook(self.workbook)
        import_workbook(self.workbook, self.database)
        self.store = IntelligenceStore(self.database)
        self.agent = OntologyAgent(self.store)

    def tearDown(self):
        self.temp.cleanup()

    def test_state_reports_real_database_and_tools(self):
        state = self.agent.state()
        self.assertTrue(state["ok"])
        self.assertEqual(state["summary"]["entityCount"], 2)
        self.assertEqual(state["tools"]["database"]["status"], "connected")
        self.assertFalse(state["tools"]["deepseek"]["writesOntology"])

    def test_equipment_taxonomy_exposes_mission_subclass(self):
        taxonomy = ontology_taxonomy(
            "EquipmentModel", "察打一体", "MALE UCAV", "Test UAV", "Test UAV", "equipment-test-uav"
        )
        self.assertEqual(taxonomy["ontologyClass"], "UnmannedAircraft")
        self.assertEqual(taxonomy["equipmentSubclass"], "strike")
        self.assertEqual(taxonomy["equipmentSubclassLabel"], "察打一体 / 攻击型")

    def test_entity_brief_includes_taxonomy_fields(self):
        entity = self.agent._entities()[0]
        brief = self.agent.object_brief(entity["entityId"])
        self.assertIn("ontologyClass", brief["entity"])
        self.assertIn("equipmentSubclass", brief["entity"])

    def test_query_returns_structured_answer_for_capability_view(self):
        result = self.agent.query({"question": "比较公开续航、速度、载荷能力指标"})
        self.assertTrue(result["ok"])
        self.assertEqual(result["view"], "capability")
        self.assertIn("能力指标", result["answer"])
        self.assertTrue(result["tables"])
        self.assertTrue(result["trace"])
        self.assertTrue(any(item["kind"] == "database" for item in result["evidence"]))

    def test_object_brief_and_plan_action_use_existing_queue(self):
        entities = self.agent._entities()
        entity_id = entities[0]["entityId"]
        brief = self.agent.object_brief(entity_id)
        self.assertTrue(brief["ok"])
        self.assertEqual(brief["summary"]["jobCount"], 0)
        planned = self.agent.object_action(entity_id, {"action": "plan"})
        self.assertTrue(planned["ok"])
        self.assertEqual(len(planned["jobs"]), 7)
        updated = self.agent.object_brief(entity_id)
        self.assertEqual(updated["summary"]["jobCount"], 7)

    def test_unknown_entity_returns_not_found(self):
        with self.assertRaises(Exception):
            self.agent.object_brief("no-such-entity")

    def test_collect_extract_and_review_attribute_candidate(self):
        queue = EquipmentVerificationQueue(self.store, provider=FakeSearchProvider())
        agent = OntologyAgent(self.store, trend_provider=FakeTrendProvider(), verification_queue=queue)
        entity_id = agent._entities()[0]["entityId"]
        plan = agent.attribute_plan(entity_id, {"scope": "performance"})
        self.assertTrue(plan["targets"])
        collected = agent.collect_attribute_evidence(entity_id, {"scope": "performance"})
        self.assertEqual(collected["retainedItems"], 1)
        extracted = agent.extract_attribute_candidates(entity_id, {"scope": "performance"})
        self.assertEqual(extracted["status"], "under-review")
        self.assertEqual(len(extracted["candidates"]), 1)
        candidate = extracted["candidates"][0]
        self.assertEqual(candidate["attributeKey"], "endurance")
        reviewed = agent.review_attribute_candidate(candidate["candidateId"], {"action": "accept", "reviewer": "tester", "reason": "fixture acceptance"})
        self.assertEqual(reviewed["candidate"]["status"], "accepted")

    def test_extract_requires_model_configuration(self):
        queue = EquipmentVerificationQueue(self.store, provider=FakeSearchProvider())
        agent = OntologyAgent(self.store, verification_queue=queue)
        entity_id = agent._entities()[0]["entityId"]
        agent.collect_attribute_evidence(entity_id, {"scope": "performance"})
        extracted = agent.extract_attribute_candidates(entity_id, {"scope": "performance"})
        self.assertEqual(extracted["status"], "model-unavailable")
        self.assertFalse(extracted["candidates"])


if __name__ == "__main__":
    unittest.main()
