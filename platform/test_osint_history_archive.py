"""History reset preserves retained evidence and only hides finished tasks."""

import sqlite3
import tempfile
import threading
import time
import unittest
from contextlib import closing
from pathlib import Path

try:
    from osint_store import OSINTTaskStore, OSINTValidationError, SCHEMA
except ModuleNotFoundError:
    from .osint_store import OSINTTaskStore, OSINTValidationError, SCHEMA


class FixtureCollector:
    def collect(self, source_ids, keywords, **kwargs):
        return {"items": [{
            "title": "Public camera interface announcement", "sourceId": source_ids[0],
            "sourceName": "Local fixture", "sourceUri": "https://fixture.example/camera",
            "body": "Camera Alpha uses a documented public interface.", "contentHash": "a" * 64,
            "matchedKeywords": [],
        }], "errors": [], "sourceResults": []}


class OSINTHistoryArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "osint.sqlite"
        self.store = OSINTTaskStore(self.path, collector=FixtureCollector(), intelligence=object())

    def finished_task(self):
        task = self.store.create_task({
            "title": "Public camera interfaces", "question": "Which interfaces are publicly documented?",
            "sourceIds": ["defense-official"], "keywords": [],
        })
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            for worker in threading.enumerate():
                if worker.name == "osint-" + task["id"]:
                    worker.join(timeout=1)
            task = self.store.get_task(task["id"])
            if task["status"] in {"completed", "failed", "partial"} and not any(
                worker.name == "osint-" + task["id"] for worker in threading.enumerate()
            ):
                self.assertEqual(task["status"], "completed")
                return task
        self.fail("fixture collection did not finish")

    def test_archive_hides_history_and_retains_items_trace_and_triggers(self):
        task = self.finished_task()
        trace = self.store.list_execution_trace(task_id=task["id"])
        self.assertTrue(trace)
        with self.store.connect() as con:
            items = [tuple(row) for row in con.execute("SELECT * FROM osint_item")]
            triggers = [tuple(row) for row in con.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger' ORDER BY name")]
        result = self.store.archive_history("Operator requested a fresh run")
        self.assertEqual(result["archivedCount"], 1)
        self.assertEqual(self.store.list_tasks(), [])
        retained = self.store.get_task(task["id"])
        self.assertEqual(retained["archivedAt"], result["archivedAt"])
        self.assertEqual(retained["archivedReason"], "Operator requested a fresh run")
        self.assertEqual(retained["status"], task["status"])
        self.assertEqual(retained["items"], task["items"])
        self.assertEqual(self.store.list_tasks(include_archived=True), [retained])
        self.assertEqual(self.store.list_execution_trace(task_id=task["id"]), trace)
        with self.store.connect() as con:
            self.assertEqual([tuple(row) for row in con.execute("SELECT * FROM osint_item")], items)
            self.assertEqual([tuple(row) for row in con.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger' ORDER BY name")], triggers)

    def test_new_task_and_materials_are_unaffected_by_archived_history(self):
        old = self.finished_task()
        first = self.store.archive_history()
        self.assertEqual(self.store.archive_history()["archivedCount"], 0)
        fresh = self.finished_task()
        self.assertIsNone(fresh["archivedAt"])
        self.assertEqual(len(fresh["items"]), 1)
        self.assertEqual([task["id"] for task in self.store.list_tasks(limit=1)], [fresh["id"]])
        self.assertEqual(self.store.get_task(old["id"])["archivedAt"], first["archivedAt"])

    def test_active_task_rejects_archive_atomically(self):
        finished = self.finished_task()
        second = self.finished_task()
        for status in ("queued", "running", "processing", "generating"):
            with self.subTest(status=status):
                with self.store.connect() as con:
                    con.execute("UPDATE osint_task SET status=? WHERE task_id=?", (status, second["id"]))
                with self.assertRaises(OSINTValidationError):
                    self.store.archive_history()
                self.assertIsNone(self.store.get_task(finished["id"])["archivedAt"])
                self.assertIsNone(self.store.get_task(second["id"])["archivedAt"])
                self.assertEqual(len(self.store.list_tasks()), 2)

    def test_existing_database_migrates_without_archiving_records(self):
        task = self.finished_task()
        legacy_path = Path(self.temp.name) / "legacy.sqlite"
        legacy_schema = SCHEMA.replace(
            "started_at TEXT,completed_at TEXT,archived_at TEXT,archived_reason TEXT);",
            "started_at TEXT,completed_at TEXT);",
        )
        with closing(sqlite3.connect(legacy_path)) as con:
            con.executescript(legacy_schema)
            con.execute("ATTACH DATABASE ? AS source", (str(self.path),))
            columns = [row[1] for row in con.execute("PRAGMA table_info(osint_task)")]
            names = ",".join(columns)
            con.execute(f"INSERT INTO osint_task({names}) SELECT {names} FROM source.osint_task")
            con.commit()
        migrated = OSINTTaskStore(legacy_path, collector=FixtureCollector(), intelligence=object())
        self.assertEqual([row["id"] for row in migrated.list_tasks()], [task["id"]])
        self.assertIsNone(migrated.get_task(task["id"])["archivedAt"])
        self.assertEqual(migrated.archive_history()["archivedCount"], 1)


if __name__ == "__main__":
    unittest.main()
