import datetime as dt
import unittest
from scripts.audit_funded_schedule import classify, UTC

class ScheduleAuditTests(unittest.TestCase):
    def test_missing_slot(self):
        now = dt.datetime(2026, 10, 20, 10, 30, tzinfo=UTC)
        start = dt.datetime(2026, 10, 20, 0, 0, tzinfo=UTC)
        rows = classify([], now, start)
        self.assertEqual([r["status"] for r in rows if r["asset"] == "crypto"], ["missing", "missing"])

    def test_success_not_equated_with_artifact_integrity(self):
        now = dt.datetime(2026, 10, 20, 3, 0, tzinfo=UTC)
        start = dt.datetime(2026, 10, 20, 0, 0, tzinfo=UTC)
        run = {"event": "schedule", "path": ".github/workflows/funded-forward-research.yml",
               "created_at": "2026-10-20T00:50:00Z", "id": 42,
               "status": "completed", "conclusion": "success"}
        rows = classify([run], now, start)
        self.assertEqual(rows[0]["status"], "run_success_artifact_unverified")

    def test_grace_window(self):
        now = dt.datetime(2026, 10, 20, 1, 0, tzinfo=UTC)
        start = dt.datetime(2026, 10, 20, 0, 0, tzinfo=UTC)
        self.assertEqual(classify([], now, start), [])

if __name__ == "__main__":
    unittest.main()
