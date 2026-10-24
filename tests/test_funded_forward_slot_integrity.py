"""Deterministic original-frozen prospective 4h coverage and trigger safety tests."""
from __future__ import annotations
from datetime import datetime, timezone
import unittest

from funded_forward_capture import create_snapshot
from funded_forward_health import validate_snapshot
from funded_forward_slot_integrity import audit_snapshots, source_candle_clock, slot_floor

UNIVERSE={"crypto":("BTC","CRV"),"stocks":("MSFT",)}


def snap(stamp,run,crv_state="WATCH",bar=None,asset_type="crypto"):
    observed=datetime.fromisoformat(stamp.replace("Z","+00:00"))
    last=bar or (slot_floor(observed.isoformat()).replace(
        hour=(slot_floor(observed.isoformat()).hour-4)%24).isoformat())
    if asset_type=="crypto":
        items=[
            {"asset":"BTC","status":"PASS","bar_timestamp":last},
            {"asset":"CRV","status":crv_state,"bar_timestamp":last},
        ]
    else:
        items=[{"asset":"MSFT","status":"DATA UNAVAILABLE",
                "reason":"Yahoo previous-session stale after NYSE completed close"}]
    return create_snapshot(
        asset_type,items,observed_at=observed,run_id=run,universe=UNIVERSE)


def check(s):
    return validate_snapshot(s,universe=UNIVERSE)


class SourceClockTests(unittest.TestCase):
    def test_completed_previous_candle_and_unavailable_preserved(self):
        row={"status":"WATCH","bar_timestamp":"2026-10-20T20:00:00+00:00"}
        self.assertEqual(source_candle_clock(row,datetime.fromisoformat(
            "2026-10-21T00:35:00+00:00")),"LATEST_COMPLETED_4H_CANDLE")
        self.assertEqual(source_candle_clock(
            {"status":"DATA UNAVAILABLE","bar_timestamp":"2026-10-20T20:00:00+00:00"},
            "2026-10-21T00:35:00Z"),"SOURCE_UNAVAILABLE")
        row["bar_timestamp"]="2026-10-21T00:00:00+00:00"
        self.assertEqual(source_candle_clock(
            row,"2026-10-21T00:35:00Z"),"FUTURE_OR_UNCOMPLETED_4H_CANDLE")
        row["bar_timestamp"]="2026-10-20T16:00:00+00:00"
        self.assertEqual(source_candle_clock(
            row,"2026-10-21T00:35:00Z"),"STALE_COMPLETED_4H_CANDLE")
        row["bar_timestamp"]="2026-10-20T21:00:00+00:00"
        self.assertEqual(source_candle_clock(
            row,"2026-10-21T00:35:00Z"),"NON_4H_CANDLE_ALIGNMENT")

    def test_timezone_naive_rejected(self):
        with self.assertRaisesRegex(ValueError,"not UTC-aware"):
            slot_floor("2026-10-21T00:30:00")
        with self.assertRaisesRegex(ValueError,"not UTC-aware"):
            slot_floor("2026-10-21T01:35:00+01:00")


class SnapshotSlotTests(unittest.TestCase):
    def test_exact_missing_4h_bucket_is_unknown_not_no_trade(self):
        a=snap("2026-10-21T00:35:00Z","crypto1",bar="2026-10-20T20:00:00Z")
        b=snap("2026-10-21T04:35:00Z","crypto2","BUY",bar="2026-10-21T00:00:00Z")
        c=snap("2026-10-21T12:40:00Z","crypto3","BUY",bar="2026-10-21T00:00:00Z")
        result=audit_snapshots([a,b,c],as_of="2026-10-21T15:00:00Z",validate=check)
        self.assertEqual(result["capture_4h_bins_in_observed_range"],4)
        self.assertEqual(result["observed_crypto_4h_bins"],3)
        self.assertEqual(result["unobserved_crypto_4h_bins"],1)
        self.assertEqual(result["4h_capture_coverage_pct"],75.)
        self.assertEqual(result["original_BUY_capture_observations"],2)
        self.assertEqual(result["original_BUY_unique_asset_plus_completed_bar"],1)
        self.assertEqual(len(result["watch_to_original_BUY_transitions_after_observed_continuity"]),1)
        self.assertEqual(result["latest_completed_coin_candle_quality"].get(
            "STALE_COMPLETED_4H_CANDLE"),2)
        self.assertEqual(result["per_4h_bin"][2]["State"],
                         "UNOBSERVED_CAPTURE_BUCKET_NOT_NO_SIGNAL")
        self.assertTrue(result["missing_bin_means_NO_KNOWN_OUTCOME"])
        self.assertFalse(result["funded_market_quotes_or_execution_verified"])

    def test_multiple_captures_in_one_bucket_do_not_expand_denominator(self):
        x=snap("2026-10-21T04:35:00Z","crypto11","BUY",
               bar="2026-10-21T00:00:00Z")
        y=snap("2026-10-21T04:56:00Z","crypto12","BUY",
               bar="2026-10-21T00:00:00Z")
        report=audit_snapshots([x,y],as_of="2026-10-21T06:00:00Z",validate=check)
        self.assertEqual(report["observed_crypto_4h_bins"],1)
        self.assertEqual(report["multiple_crypto_captures_in_same_4h_bin"],1)
        self.assertEqual(report["original_BUY_capture_observations"],2)
        self.assertEqual(report["original_BUY_unique_asset_plus_completed_bar"],1)

    def test_duplicate_identical_source_run_is_single_count(self):
        a=snap("2026-10-21T04:35:00Z","crypto20","BUY",
               bar="2026-10-21T00:00:00Z")
        report=audit_snapshots([a,a],as_of="2026-10-21T06:00:00Z",validate=check)
        self.assertEqual(report["source_crypto_scans"],1)

    def test_tampered_payload_refused_before_any_coverage_claim(self):
        a=snap("2026-10-21T04:35:00Z","crypto21","BUY",
               bar="2026-10-21T00:00:00Z")
        a["rows"][1]["status"]="PASS"
        with self.assertRaisesRegex(ValueError,"SHA-256 mismatch"):
            audit_snapshots([a],as_of="2026-10-21T06:00:00Z",validate=check)

    def test_missing_source_captures_never_equal_healthy_no_trade(self):
        with self.assertRaisesRegex(RuntimeError,"NO FROZEN CRYPTO SCANS"):
            audit_snapshots([],as_of="2026-10-21T06:00:00Z",validate=check)

    def test_last_capture_after_as_of_not_allowed(self):
        a=snap("2026-10-21T04:35:00Z","crypto31")
        with self.assertRaisesRegex(ValueError,"later than"):
            audit_snapshots([a],as_of="2026-10-21T04:34:00Z",validate=check)

    def test_stock_outage_stays_outside_crypto_capture_denominator(self):
        a=snap("2026-10-21T04:35:00Z","crypto40")
        b=snap("2026-10-21T05:35:00Z","stock40",asset_type="stocks")
        report=audit_snapshots([a,b],as_of="2026-10-21T06:00:00Z",validate=check)
        self.assertEqual(report["source_stock_scans"],1)
        self.assertEqual(report["source_crypto_scans"],1)
        self.assertEqual(report["observed_crypto_4h_bins"],1)


if __name__=="__main__":
    unittest.main()
