"""No-order source-candle admission, duplicate, API and schedule fail-closed tests."""
from datetime import datetime, timedelta, timezone
from io import BytesIO
import json
import unittest
from unittest.mock import patch
import zipfile

from funded_forward_capture import create_snapshot, funded_universe
from funded_capture_schedule_admission import (
    already_captured, decide, expected_source_bar, read_recent_original_sources,
)

UTC = timezone.utc
UNIVERSE = {"crypto": ("BTC", "CRV"), "stocks": ("NVDA",)}


def clock(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def source(captured_at="2026-10-26T00:35:00Z",
           bar="2026-10-25T20:00:00Z", *,
           corrupt=False, failed=False, market_pair=True, universe=UNIVERSE):
    records = []
    for coin in universe["crypto"]:
        records.append({
            "asset": coin,
            "status": "DATA UNAVAILABLE" if failed and coin == "CRV" else "WATCH",
            "bar_timestamp": bar,
            "meta": {"pair": coin + "/USD"} if market_pair else {},
        })
    result = create_snapshot(
        "crypto", records, observed_at=clock(captured_at),
        run_id="777-1-crypto-20261026T003500Z", universe=universe,
    )
    if corrupt:
        result["rows"][0]["status"] = "BUY"
    return result


class ScheduledFourHourAdmissionTests(unittest.TestCase):
    def test_bar_identity_is_actual_utc_not_nominal_cron(self):
        opening, close, minutes = expected_source_bar(clock("2026-10-26T00:35:00Z"))
        self.assertEqual(opening.isoformat(), "2026-10-25T20:00:00+00:00")
        self.assertEqual(close.isoformat(), "2026-10-26T00:00:00+00:00")
        self.assertEqual(minutes, 35)

    def test_just_closed_candle_requires_minute_finality(self):
        opening, close, _ = expected_source_bar(clock("2026-10-26T04:00:30Z"))
        self.assertEqual(opening.isoformat(), "2026-10-25T20:00:00+00:00")
        self.assertEqual(close.isoformat(), "2026-10-26T00:00:00+00:00")
        latest, closed, lag = expected_source_bar(clock("2026-10-26T04:01:00Z"))
        self.assertEqual(latest.isoformat(), "2026-10-26T00:00:00+00:00")
        self.assertEqual(closed.isoformat(), "2026-10-26T04:00:00+00:00")
        self.assertEqual(lag, 1)

    def test_fresh_first_original_schedule_is_admitted(self):
        result = decide(clock("2026-10-26T00:35:00Z"), [], universe=UNIVERSE)
        self.assertTrue(result["capture_new_snapshot"])
        self.assertEqual(result["decision"], "ELIGIBLE_FRESH_4H_CANDLE")
        self.assertTrue(result["no_backdated_signal_or_broker_order"])

    def test_sha_verified_already_completed_candle_blocks_redundant_second_run(self):
        prior = source()
        result = decide(clock("2026-10-26T00:55:00Z"), [prior], universe=UNIVERSE)
        self.assertFalse(result["capture_new_snapshot"])
        self.assertEqual(result["decision"], "PRIOR_COMPLETE_SHA_VALIDATED_CAPTURE")

    def test_previous_slot_archive_does_not_block_current(self):
        prior = source(captured_at="2026-10-25T20:35:00Z",
                       bar="2026-10-25T16:00:00Z")
        r = decide(clock("2026-10-26T00:55:00Z"), [prior], universe=UNIVERSE)
        self.assertTrue(r["capture_new_snapshot"])

    def test_provider_failure_preserves_recovery_opportunity_for_redundant_run(self):
        prior = source(failed=True)
        r = decide(clock("2026-10-26T00:55:00Z"), [prior], universe=UNIVERSE)
        self.assertTrue(r["capture_new_snapshot"])

    def test_missing_public_pair_does_not_block_redundancy(self):
        r = decide(clock("2026-10-26T00:55:00Z"),
                   [source(market_pair=False)], universe=UNIVERSE)
        self.assertTrue(r["capture_new_snapshot"])

    def test_simultaneous_admission_without_uploaded_artifact_is_not_atomic(self):
        # Two independent runners can both pass before either uploads a ZIP.
        # This documents a release BLOCKER, not a claimed race fix.
        instant = clock("2026-10-26T00:55:00Z")
        first = decide(instant, [], universe=UNIVERSE)
        second = decide(instant, [], universe=UNIVERSE)
        self.assertTrue(first["capture_new_snapshot"])
        self.assertTrue(second["capture_new_snapshot"])

    def test_uploaded_complete_archive_prevents_second_admission(self):
        first = source()
        second = decide(clock("2026-10-26T00:55:00Z"),
                        [first], universe=UNIVERSE)
        self.assertFalse(second["capture_new_snapshot"])
        self.assertEqual(second["decision"], "PRIOR_COMPLETE_SHA_VALIDATED_CAPTURE")

    def test_artifact_upload_delay_leaves_second_runner_eligible(self):
        # No archive visible during upload: a second runner cannot know it
        # is duplicating a successful but unpublished capture.
        pending_upload = []
        second = decide(clock("2026-10-26T00:55:00Z"),
                        pending_upload, universe=UNIVERSE)
        self.assertEqual(second["decision"], "ELIGIBLE_FRESH_4H_CANDLE")
        self.assertTrue(second["capture_new_snapshot"])

    def test_github_run_late_does_not_fabricate_old_buy(self):
        result = decide(clock("2026-10-26T02:45:00Z"), [], universe=UNIVERSE)
        self.assertFalse(result["capture_new_snapshot"])
        self.assertEqual(result["decision"], "LATE_START_UNKNOWN_NO_BACKFILL")
        self.assertTrue(result["unobserved_bars_are_UNKNOWN"])

    def test_damaged_archived_json_fails_sha_validation(self):
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            decide(clock("2026-10-26T00:55:00Z"),
                   [source(corrupt=True)], universe=UNIVERSE)

    def test_naive_capture_clock_cannot_be_admitted(self):
        with self.assertRaisesRegex(ValueError, "UTC"):
            expected_source_bar(datetime(2026, 10, 26, 0, 55))

    def test_bad_github_token_or_repo_fails_before_network(self):
        with self.assertRaisesRegex(ValueError, "read-only repo"):
            read_recent_original_sources("invalid", "abc", clock("2026-10-26T00:55:00Z"))
        with self.assertRaisesRegex(ValueError, "read-only repo"):
            read_recent_original_sources("owner/repo", "", clock("2026-10-26T00:55:00Z"))

    def test_read_only_github_listing_uses_main_archive_not_pull_request(self):
        actual_universe = funded_universe()
        frozen = source(universe=actual_universe)
        buf = BytesIO()
        with zipfile.ZipFile(buf, "w") as output:
            output.writestr("funded-crypto-20261026T003500Z-777-1.json",
                            json.dumps(frozen))
        base = {
            "name": "funded-crypto-777-1",
            "created_at": "2026-10-26T00:36:00Z",
            "expired": False,
            "archive_download_url": "https://example.invalid/frozen.zip",
        }
        good = dict(base, workflow_run={"head_branch": "main"})
        bad = dict(base, name="funded-crypto-555-1",
                   workflow_run={"head_branch": "research-not-main"})
        seen = []
        def stub(url, *, token):
            seen.append(url)
            if "actions/artifacts" in url:
                return json.dumps({"artifacts": [bad, good]}).encode()
            self.assertEqual(url, good["archive_download_url"])
            return buf.getvalue()
        with patch("funded_capture_schedule_admission._github_request",
                   side_effect=stub):
            originals = read_recent_original_sources(
                "owner/repo", "read_token", clock("2026-10-26T00:55:00Z"))
        self.assertEqual(len(originals), 1)
        self.assertEqual(originals[0]["payload_sha256"], frozen["payload_sha256"])
        self.assertEqual(len([u for u in seen if u.endswith(".zip")]), 1)

    def test_incomplete_github_archive_pagination_fails_closed(self):
        item = {"created_at": "2026-10-26T00:55:00Z", "name": "unrelated"}
        with patch("funded_capture_schedule_admission._github_request",
                   return_value=json.dumps({"artifacts": [item] * 100}).encode()):
            with self.assertRaisesRegex(RuntimeError, "complete recent"):
                read_recent_original_sources(
                    "owner/repo", "read_token", clock("2026-10-26T00:55:00Z"))


if __name__ == "__main__":
    unittest.main()
