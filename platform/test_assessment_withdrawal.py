"""Administrative publication correction must preserve the original audit."""
import sqlite3
import tempfile
import unittest
from pathlib import Path

from intelligence_agent import DeterministicTrendProvider
from intelligence_store import IntelligenceStore, IntelligenceConflict


class AssessmentWithdrawalTests(unittest.TestCase):
    def test_withdrawal_retains_history_and_blocks_republication(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = IntelligenceStore(Path(tmp) / "test.sqlite")
            payload = DeterministicTrendProvider().generate(
                task_id="isolated-test", title="Test", question="Public trends?", horizon_years=5,
                materials=[{"itemId": "a", "title": "Public software research", "sourceUri": "https://example.test/a", "contentHash": "a" * 64, "body": "Public software research."}],
            )
            draft = store.create_assessment(payload)["assessment"]
            aid = draft["assessmentId"]
            with self.assertRaises(IntelligenceConflict):
                store.withdraw_assessment_publication(aid, {"actor": "test-recovery", "reason": "Not published"})
            accepted = store.review_assessment(aid, {"action": "accept", "reviewer": "synthetic-test-reviewer", "reason": "Isolated fixture only", "expectedRevision": 1})
            published = store.publish_assessment(aid, {"reviewer": "synthetic-test-reviewer", "expectedRevision": accepted["revision"]})
            correction = {"actor": "test-recovery", "reason": "Synthetic review was not a user approval"}
            withdrawn = store.withdraw_assessment_publication(aid, correction)
            self.assertEqual(withdrawn["status"], "withdrawn")
            self.assertEqual(withdrawn["contentHash"], published["contentHash"])
            self.assertEqual(withdrawn["publicationWithdrawal"]["actor"], "test-recovery")
            self.assertEqual(store.get_assessment(aid)["status"], "withdrawn")
            self.assertEqual(store.list_assessments("isolated-test")[0]["status"], "withdrawn")
            self.assertEqual(store.create_assessment(payload)["assessment"]["status"], "withdrawn")
            store.withdraw_assessment_publication(aid, correction)
            events = [e for e in store.list_events() if e["eventType"] == "TrendAssessmentPublicationWithdrawn"]
            self.assertEqual(len(events), 1)
            self.assertEqual(len(store.list_assessment_reviews(aid)), 1)
            with self.assertRaises(IntelligenceConflict):
                store.publish_assessment(aid, {"reviewer": "test", "expectedRevision": accepted["revision"]})
            with store._connect() as con:
                self.assertEqual(con.execute("SELECT status FROM intelligence_assessment WHERE assessment_id=?", (aid,)).fetchone()[0], "published")
                with self.assertRaises(sqlite3.IntegrityError):
                    con.execute("UPDATE intelligence_assessment SET status='under-review' WHERE assessment_id=?", (aid,))


if __name__ == "__main__":
    unittest.main()
