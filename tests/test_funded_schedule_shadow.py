import datetime as dt
import unittest

from scripts.funded_schedule_shadow import audit, intended_slots, next_slot, UTC


def run(rid, timestamp, event="schedule"):
    return {"id": rid, "created_at": timestamp, "event": event,
            "path": ".github/workflows/funded-forward-research.yml"}


def job(asset, status="completed", conclusion="success"):
    return {"name": ("Frozen crypto research observations (33 coins)"
                     if asset == "crypto" else "Frozen stock research observations (30 tickers)"),
            "status": status, "conclusion": conclusion}


def artifact(asset, rid, size=100, expired=False):
    return {"name": f"funded-{asset}-{rid}-1", "size_in_bytes": size,
            "expired": expired}


class ShadowResilienceTests(unittest.TestCase):
    def test_immature_grace_does_not_report_missing(self):
        now = dt.datetime(2026, 10, 20, 1, 0, tzinfo=UTC)
        start = dt.datetime(2026, 10, 20, 0, 0, tzinfo=UTC)
        self.assertEqual(audit(now, start, [], {}, {}), [])

    def test_missing_is_observed_window_not_confirmed_dropped_cron(self):
        now = dt.datetime(2026, 10, 20, 7, 0, tzinfo=UTC)
        start = dt.datetime(2026, 10, 20, 0, 0, tzinfo=UTC)
        rows = audit(now, start, [], {}, {})
        self.assertEqual([r["status"] for r in rows], ["no_matching_asset_job_observed"] * 2)
        self.assertTrue(all(r["original_cron_occurrence"] == "not_exposed_by_github_api" for r in rows))

    def test_crypto_success_requires_nonempty_unexpired_artifact(self):
        now = dt.datetime(2026, 10, 20, 3, 0, tzinfo=UTC)
        start = dt.datetime(2026, 10, 20, 0, 0, tzinfo=UTC)
        runs = [run(42, "2026-10-20T00:52:00Z")]
        rows = audit(now, start, runs, {42: [job("crypto")]},
                     {42: [artifact("crypto", 42)]})
        self.assertEqual(rows[0]["status"], "artifact_observed_content_unverified")
        rows = audit(now, start, runs, {42: [job("crypto")]},
                     {42: [artifact("crypto", 42, size=0)]})
        self.assertEqual(rows[0]["status"], "successful_asset_job_artifact_unverified_or_missing")

    def test_skipped_asset_job_cannot_qualify(self):
        now = dt.datetime(2026, 10, 20, 3, 0, tzinfo=UTC)
        start = dt.datetime(2026, 10, 20, 0, 0, tzinfo=UTC)
        rows = audit(now, start, [run(42, "2026-10-20T00:52:00Z")],
                     {42: [job("stocks"), job("crypto", conclusion="skipped")]},
                     {42: [artifact("crypto", 42)]})
        self.assertEqual(rows[0]["status"], "no_matching_asset_job_observed")

    def test_stock_cross_midnight_is_attributed_to_previous_evening(self):
        now = dt.datetime(2026, 10, 20, 4, 0, tzinfo=UTC)
        start = dt.datetime(2026, 10, 19, 20, 0, tzinfo=UTC)
        rows = audit(now, start, [run(7, "2026-10-20T01:17:52Z")],
                     {7: [job("stocks")]}, {7: [artifact("stocks", 7)]})
        stock = [r for r in rows if r["asset"] == "stocks"][0]
        crypto = [r for r in rows if r["asset"] == "crypto" and
                  r["nominal_slot_utc"].startswith("2026-10-20T00:35")][0]
        self.assertEqual(stock["status"], "artifact_observed_content_unverified")
        self.assertEqual(stock["attributed_run_ids"], [7])
        self.assertEqual(crypto["status"], "no_matching_asset_job_observed")

    def test_earlier_run_cannot_satisfy_future_slot(self):
        now = dt.datetime(2026, 10, 20, 23, 0, tzinfo=UTC)
        start = dt.datetime(2026, 10, 20, 16, 0, tzinfo=UTC)
        rows = audit(now, start, [run(7, "2026-10-20T20:05:59Z")],
                     {7: [job("crypto")]}, {7: [artifact("crypto", 7)]})
        self.assertEqual([x["status"] for x in rows if x["asset"] == "crypto"],
                         ["artifact_observed_content_unverified", "no_matching_asset_job_observed"])

    def test_rate_limit_or_fetch_failure_is_unknown_not_missing(self):
        now = dt.datetime(2026, 10, 20, 3, 0, tzinfo=UTC)
        start = dt.datetime(2026, 10, 20, 0, 0, tzinfo=UTC)
        rows = audit(now, start, [run(7, "2026-10-20T00:45:00Z")], {}, {})
        self.assertEqual(rows[0]["status"], "job_metadata_unavailable")

    def test_incomplete_history_not_mislabeled_as_dropped(self):
        now = dt.datetime(2026, 10, 20, 3, 0, tzinfo=UTC)
        start = dt.datetime(2026, 10, 20, 0, 0, tzinfo=UTC)
        rows = audit(now, start, [], {}, {}, complete=False)
        self.assertEqual(rows[0]["status"], "history_incomplete")

    def test_weekday_stock_and_weekend_count(self):
        fri = dt.datetime(2026, 10, 23, 22, 45, tzinfo=UTC)
        self.assertEqual(next_slot("stocks", fri),
                         dt.datetime(2026, 10, 26, 22, 45, tzinfo=UTC))
        start = dt.datetime(2026, 10, 24, 0, 0, tzinfo=UTC)
        end = dt.datetime(2026, 10, 25, 23, 0, tzinfo=UTC)
        self.assertTrue(all(asset == "crypto" for asset, _ in intended_slots(start, end)))

    def test_manual_dispatch_not_a_scheduled_capture(self):
        now = dt.datetime(2026, 10, 20, 3, 0, tzinfo=UTC)
        start = dt.datetime(2026, 10, 20, 0, 0, tzinfo=UTC)
        rows = audit(now, start, [run(7, "2026-10-20T00:45:00Z", event="workflow_dispatch")],
                     {7: [job("crypto")]}, {7: [artifact("crypto", 7)]})
        self.assertEqual(rows[0]["status"], "no_matching_asset_job_observed")

    def test_expired_archive_is_not_a_good_observation(self):
        now = dt.datetime(2026, 10, 20, 3, 0, tzinfo=UTC)
        start = dt.datetime(2026, 10, 20, 0, 0, tzinfo=UTC)
        rows = audit(now, start, [run(7, "2026-10-20T00:45:00Z")],
                     {7: [job("crypto")]}, {7: [artifact("crypto", 7, expired=True)]})
        self.assertEqual(rows[0]["status"], "successful_asset_job_artifact_unverified_or_missing")


if __name__ == "__main__":
    unittest.main()
