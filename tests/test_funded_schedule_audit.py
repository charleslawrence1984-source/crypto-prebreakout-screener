import datetime as dt
import unittest

from scripts.audit_funded_schedule import classify, following_slot, UTC


def run(when, run_id=42):
    return {
        "event": "schedule",
        "path": ".github/workflows/funded-forward-research.yml",
        "created_at": when, "id": run_id, "status": "completed",
        "conclusion": "success",
    }


def job(asset, conclusion="success"):
    name = (
        "Frozen crypto research observations (33 coins)"
        if asset == "crypto" else "Frozen stock research observations (30 tickers)"
    )
    return {"name": name, "status": "completed", "conclusion": conclusion}


def statuses(rows, asset):
    return [(r["slot_utc"], r["status"]) for r in rows if r["asset"] == asset]


class ScheduleAuditTests(unittest.TestCase):
    def test_missing_slots_are_inferences_not_proven_misses(self):
        now = dt.datetime(2026, 10, 20, 10, 30, tzinfo=UTC)
        start = dt.datetime(2026, 10, 20, 0, 0, tzinfo=UTC)
        rows = classify([], now, start)
        self.assertEqual(len(statuses(rows, "crypto")), 3)
        self.assertTrue(all(x[1] == "no_matching_run_observed"
                            for x in statuses(rows, "crypto")))

    def test_success_is_not_claimed_to_validate_artifact(self):
        now = dt.datetime(2026, 10, 20, 3, 0, tzinfo=UTC)
        start = dt.datetime(2026, 10, 20, 0, 0, tzinfo=UTC)
        rows = classify([run("2026-10-20T00:50:00Z")], now, start,
                        {42: [job("crypto"), job("stocks", "skipped")]})
        self.assertEqual(statuses(rows, "crypto")[0][1],
                         "observed_success_artifact_unverified_slot_inferred")

    def test_grace_window_prevents_premature_missing(self):
        now = dt.datetime(2026, 10, 20, 1, 0, tzinfo=UTC)
        start = dt.datetime(2026, 10, 20, 0, 0, tzinfo=UTC)
        self.assertEqual(classify([], now, start), [])

    def test_run_created_before_later_slot_not_attributed_to_it(self):
        now = dt.datetime(2026, 10, 20, 22, 30, tzinfo=UTC)
        start = dt.datetime(2026, 10, 20, 16, 0, tzinfo=UTC)
        rows = classify([run("2026-10-20T20:05:59Z")], now, start,
                        {42: [job("crypto")]})
        outcomes = statuses(rows, "crypto")
        self.assertEqual(outcomes[0][1],
                         "observed_success_artifact_unverified_slot_inferred")
        self.assertEqual(outcomes[1][1], "no_matching_run_observed")

    def test_stock_cross_midnight_not_misread_as_crypto(self):
        now = dt.datetime(2026, 10, 20, 3, 0, tzinfo=UTC)
        start = dt.datetime(2026, 10, 19, 20, 0, tzinfo=UTC)
        rows = classify([run("2026-10-20T01:17:52Z")], now, start,
                        {42: [job("stocks"), job("crypto", "skipped")]})
        self.assertEqual(statuses(rows, "stocks")[0][1],
                         "observed_success_artifact_unverified_slot_inferred")
        self.assertEqual(statuses(rows, "crypto")[-1][1], "no_matching_run_observed")

    def test_missing_job_metadata_is_unknown(self):
        now = dt.datetime(2026, 10, 20, 3, 0, tzinfo=UTC)
        start = dt.datetime(2026, 10, 20, 0, 0, tzinfo=UTC)
        rows = classify([run("2026-10-20T00:50:00Z")], now, start)
        self.assertEqual(statuses(rows, "crypto")[0][1],
                         "job_metadata_unavailable")

    def test_skipped_asset_does_not_count_as_success(self):
        now = dt.datetime(2026, 10, 20, 3, 0, tzinfo=UTC)
        start = dt.datetime(2026, 10, 20, 0, 0, tzinfo=UTC)
        rows = classify([run("2026-10-20T00:50:00Z")], now, start,
                        {42: [job("crypto", "skipped"), job("stocks")]})
        self.assertEqual(statuses(rows, "crypto")[0][1], "no_matching_run_observed")

    def test_failed_asset_job_not_healthy(self):
        now = dt.datetime(2026, 10, 20, 3, 0, tzinfo=UTC)
        start = dt.datetime(2026, 10, 20, 0, 0, tzinfo=UTC)
        rows = classify([run("2026-10-20T00:50:00Z")], now, start,
                        {42: [job("crypto", "failure")]})
        self.assertEqual(statuses(rows, "crypto")[0][1], "asset_job_failed")

    def test_incomplete_history_never_reports_definitive_gap(self):
        now = dt.datetime(2026, 10, 20, 3, 0, tzinfo=UTC)
        start = dt.datetime(2026, 10, 20, 0, 0, tzinfo=UTC)
        rows = classify([], now, start, {}, history_complete=False)
        self.assertEqual(statuses(rows, "crypto")[0][1], "history_incomplete")

    def test_stock_weekend_next_slot_is_monday(self):
        friday = dt.datetime(2026, 10, 23, 22, 45, tzinfo=UTC)
        following = following_slot("stocks", friday)
        self.assertEqual(following, dt.datetime(2026, 10, 26, 22, 45, tzinfo=UTC))

    def test_nonscheduled_manual_run_never_replaces_schedule(self):
        now = dt.datetime(2026, 10, 20, 3, 0, tzinfo=UTC)
        start = dt.datetime(2026, 10, 20, 0, 0, tzinfo=UTC)
        r = run("2026-10-20T00:50:00Z")
        r["event"] = "workflow_dispatch"
        rows = classify([r], now, start, {42: [job("crypto")]})
        self.assertEqual(statuses(rows, "crypto")[0][1], "no_matching_run_observed")


if __name__ == "__main__":
    unittest.main()
