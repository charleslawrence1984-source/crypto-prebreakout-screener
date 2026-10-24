"""GitHub schedule forensic evidence tests; no hidden intended-cron guesses."""
import unittest

from funded_gha_schedule_forensics import (
    last_possible_crypto_cron_before, infer_crypto_creation_lag,
    schedule_forensics,
)


def record(run_id, when, *, state="success"):
    return {
        "id":run_id,"created_at":when,"event":"schedule",
        "crypto_job_name":"Frozen crypto research observations (33 coins)",
        "crypto_job_conclusion":state,
    }


def snap(run_id,when):
    return {"asset_type":"crypto",
            "run_id":f"{run_id}-1-crypto-20261021T202223Z",
            "captured_at_utc":when}


class ScheduleForensicsTests(unittest.TestCase):
    def test_nominal_cron_lag_is_labeled_heuristic(self):
        a=infer_crypto_creation_lag(record(
            2,"2026-10-21T23:33:38Z"))
        self.assertEqual(a["latest_prior_scheduled_crypto_cron_utc"],
                         "2026-10-21T20:35:00+00:00")
        self.assertAlmostEqual(a["minimum_nominal_crypto_creation_delay_minutes"],
                               178.63,places=2)
        self.assertFalse(a["actual_original_cron_occurrence_proven"])

    def test_overnight_cron_reference_previous_day(self):
        a=last_possible_crypto_cron_before("2026-10-22T00:15:00Z")
        self.assertEqual(a.isoformat(),"2026-10-21T20:35:00+00:00")

    def test_workflow_run_without_archived_snapshot_cannot_claim_no_trade(self):
        runs=[
            record(12,"2026-10-21T20:40:00Z"),
            record(13,"2026-10-22T00:42:00Z",state="failure"),
        ]
        originals=[snap(12,"2026-10-21T20:43:00Z")]
        r=schedule_forensics(runs,originals,as_of="2026-10-22T08:00:00Z")
        self.assertEqual(r["job_with_matching_archive_count"],1)
        self.assertEqual(r["job_without_matching_archive_count"],1)
        self.assertTrue(r["runs"][1]["missing_original_capture_not_zero_signals"])
        self.assertTrue(r["no_new_schedule_or_live_change"])

    def test_dispatch_sources_not_labelled_confirmed_scheduled_job(self):
        r=schedule_forensics([], [snap(20,"2026-10-21T20:44:00Z")],
                             as_of="2026-10-22T00:00:00Z")
        self.assertEqual(r["original_crypto_captures_without_examined_schedule_run"],["20"])

    def test_crypto_job_skipped_or_wrong_name_fails_identity(self):
        bad=record(34,"2026-10-22T01:35:00Z")
        bad["crypto_job_name"]="Stock research"
        with self.assertRaisesRegex(ValueError,"Cannot identify"):
            schedule_forensics([bad],[],as_of="2026-10-22T02:00:00Z")

    def test_bad_naive_timestamp_refused(self):
        bad=record(35,"2026-10-22T04:44:00")
        with self.assertRaisesRegex(ValueError,"not UTC-aware"):
            infer_crypto_creation_lag(bad)


if __name__=="__main__":
    unittest.main()
