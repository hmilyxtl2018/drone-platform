import json
import tempfile
import unittest
from pathlib import Path

try:
    from intelligence_store import IntelligenceStore
    from ontology_workbook_import import import_workbook
    from equipment_verification import EquipmentVerificationQueue
except ModuleNotFoundError:  # pragma: no cover
    from .intelligence_store import IntelligenceStore
    from .ontology_workbook_import import import_workbook
    from .equipment_verification import EquipmentVerificationQueue

from openpyxl import Workbook


class OntologyWorkbookImportTests(unittest.TestCase):
    def test_import_is_pending_traceable_and_idempotent(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workbook_path = root / "equipment.xlsx"
            database = root / "intelligence.sqlite"
            workbook = Workbook()
            sheet = workbook.active
            sheet.title = "主表_全部机型"
            sheet.append(["测试装备本体"])
            sheet.append(["只读导入测试"])
            sheet.append(["序号", "类别", "子类别", "型号", "英文名", "原产国", "制造商", "首飞年", "服役年", "状态", "最大航程 km", "续航 h", "蜂群兼容", "主要使用国", "实战记录"])
            sheet.append([1, "察打一体", "MALE UCAV", "Test UAV", "Test UAV", "测试国", "Test Works", 2024, 2025, "试验", 1200, 24, "是", "测试国,伙伴国", "是(乌战)"])
            workbook.save(workbook_path)
            workbook.close()

            first = import_workbook(workbook_path, database)
            second = import_workbook(workbook_path, database)
            self.assertEqual(first["status"], "completed")
            self.assertEqual(first["rowCount"], 1)
            self.assertEqual(second["status"], "duplicate")

            store = IntelligenceStore(database)
            with store._connect() as con:
                entity = con.execute("SELECT * FROM ontology_entity").fetchone()
                self.assertEqual(entity["review_status"], "pending")
                self.assertEqual(entity["origin_country"], "测试国")
                self.assertEqual(json.loads(entity["user_countries_json"]), ["测试国", "伙伴国"])
                self.assertEqual(con.execute("SELECT count(*) FROM ontology_import_snapshot").fetchone()[0], 1)
                assertion = con.execute("SELECT * FROM ontology_assertion WHERE attribute_key='combat-status'").fetchone()
                self.assertEqual(assertion['raw_value'],'是(乌战)')
                self.assertIsNone(assertion["conditions"])
                self.assertEqual(assertion["review_status"], "pending")
                evidence = con.execute("SELECT * FROM ontology_evidence_link WHERE evidence_id=json_extract(?, '$[0]')", (assertion["evidence_ids"],)).fetchone()
                locator = json.loads(evidence["locator_json"])
                self.assertEqual(locator, {"sheetName": "主表_全部机型", "rowNumber": 4, "cellRange": "O4"})
                document = json.loads(con.execute("SELECT attributes_json FROM ontology_entity_revision").fetchone()[0])
                self.assertEqual(document["attributes"]["flight-range"]["values"][0]["normalizedValue"], 1200)
                self.assertEqual(document["attributes"]["combat-status"]["values"][0]["truthVerified"], False)

            class FakeProvider:
                def search(self, question, keywords, **options):
                    self.call = {"question": question, "keywords": keywords, **options}
                    return {"items": [{"title": "Test UAV specification", "body": "Test UAV range 1200 km.", "itemUri": "https://example.org/test-uav", "sourceVersion": "2026-01-01", "publishedAt": "2026-01-01T00:00:00Z", "contentHash": "a" * 64, "sourceReliability": "primary"}], "errors": [], "fetchedAt": "2026-10-09T00:00:00Z", "sourceResults": []}

            provider = FakeProvider()
            queue = EquipmentVerificationQueue(store, provider=provider)
            jobs = queue.plan(entity["entity_id"])
            self.assertEqual(len(jobs), 7)
            performance = next(job for job in jobs if job["targetType"] == "performance")
            self.assertTrue(performance["query"].startswith('"Test UAV"'))
            completed = queue.execute(performance["verificationId"])
            self.assertEqual(completed["status"], "needs-review")
            self.assertIn('"Test UAV"', provider.call["keywords"])
            with store._connect() as con:
                evidence = con.execute("SELECT verification_status FROM ontology_evidence_link WHERE source_ref='https://example.org/test-uav'").fetchone()
                self.assertEqual(evidence[0], "unverified")


if __name__ == "__main__":
    unittest.main()
