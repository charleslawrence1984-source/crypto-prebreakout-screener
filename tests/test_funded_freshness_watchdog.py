import datetime as dt
import unittest
from scripts.funded_freshness_watchdog import assess, expected_stock_slot, UTC


def run(asset, when, id=1):
    return {"asset": asset, "event": "schedule", "conclusion": "success",
            "created_at": when, "id": id}


def artifact(name):
    return {"name": name, "expired": False, "size_in_bytes": 100}


class WatchdogTests(unittest.TestCase):
    def test_stale_crypto_with_artifact(self):
        now = dt.datetime(2026, 10, 20, 20, 0, tzinfo=UTC)
        rows = [run("crypto", "2026-10-20T13:48:00Z")]
        result = assess(now, rows, {1: [artifact("funded-crypto-1-1")]})
        self.assertEqual(result["crypto"]["status"], "stale")

    def test_fresh_crypto_with_artifact(self):
        now = dt.datetime(2026, 10, 20, 15, 0, tzinfo=UTC)
        rows = [run("crypto", "2026-10-20T13:48:00Z")]
        result = assess(now, rows, {1: [artifact("funded-crypto-1-1")]})
        self.assertEqual(result["crypto"]["status"], "observed_artifact_present_unverified")

    def test_success_without_artifact_is_not_healthy(self):
        now = dt.datetime(2026, 10, 20, 15, 0, tzinfo=UTC)
        result = assess(now, [run("crypto", "2026-10-20T13:48:00Z")], {1: []})
        self.assertEqual(result["crypto"]["status"], "artifact_missing")

    def test_stock_after_midnight_is_not_marked_missing(self):
        now = dt.datetime(2026, 10, 20, 3, 0, tzinfo=UTC)
        rows = [run("stocks", "2026-10-20T01:17:00Z", 2)]
        result = assess(now, rows, {2: [artifact("funded-stocks-2-1")]})
        self.assertEqual(result["stocks"]["status"], "observed_artifact_present_unverified")

    def test_weekend_stock_slot(self):
        saturday = dt.datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
        self.assertEqual(expected_stock_slot(saturday).weekday(), 4)

    def test_missing_history(self):
        now = dt.datetime(2026, 10, 20, 15, 0, tzinfo=UTC)
        self.assertEqual(assess(now, [], {})["crypto"]["status"], "missing")


if __name__ == "__main__":
    unittest.main()
