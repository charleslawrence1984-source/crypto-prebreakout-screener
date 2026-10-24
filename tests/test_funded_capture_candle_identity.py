"""Immutable source candle identity and duplicate/conflicting technical BUY tests."""
import unittest
from datetime import datetime

from funded_forward_capture import create_snapshot
from funded_forward_health import validate_snapshot
from funded_capture_candle_identity import (
    audit,bucket,last_complete_four_hour_open,classify_4h_row,
)

UNIVERSE={"crypto":("BTC","CRV"),"stocks":("NVDA",)}


def snap(time,run,crv="WATCH",bar=None,btc="PASS",pair="CRV/USD"):
    stamp=datetime.fromisoformat(time.replace("Z","+00:00"))
    rows=[
        {"asset":"BTC","status":btc,"bar_timestamp":bar or "2026-10-23T20:00:00Z",
         "meta":{"pair":"BTC/USD"}},
        {"asset":"CRV","status":crv,"bar_timestamp":bar or "2026-10-23T20:00:00Z",
         "meta":{"pair":pair}}
    ]
    return create_snapshot("crypto",rows,observed_at=stamp,run_id=run,universe=UNIVERSE)


def check(source):
    return validate_snapshot(source,universe=UNIVERSE)


class CandleRules(unittest.TestCase):
    def test_utc_4h_boundary_requires_candle_close_plus_one_minute(self):
        self.assertEqual(last_complete_four_hour_open("2026-10-24T04:00:30Z").isoformat(),
                         "2026-10-23T20:00:00+00:00")
        self.assertEqual(last_complete_four_hour_open("2026-10-24T04:01:00Z").isoformat(),
                         "2026-10-24T00:00:00+00:00")
        self.assertEqual(bucket("2026-10-24T07:59:00Z").hour,4)
        with self.assertRaisesRegex(ValueError,"not UTC-aware"):
            bucket("2026-10-24T08:59:00")

    def test_source_current_stale_future_bad_and_unavailable(self):
        t="2026-10-24T04:56:00Z"
        expected={"2026-10-24T00:00:00Z":"COMPLETED_SOURCE_CANDLE_AT_CAPTURE",
                  "2026-10-23T20:00:00Z":"STILL_OLDER_THAN_COMPLETED_4H_CANDLE",
                  "2026-10-24T04:00:00Z":"FUTURE_OR_UNFINISHED_SOURCE_CANDLE",
                  "2026-10-24T00:30:00Z":"MISALIGNED_SOURCE_CANDLE",
                  "nonsense":"UNVERIFIED_SOURCE_CANDLE_TIME"}
        for bar,decision in expected.items():
            self.assertEqual(classify_4h_row({"status":"BUY","bar_timestamp":bar},t),
                             decision)
        self.assertEqual(classify_4h_row({"status":"DATA UNAVAILABLE"},t),
                         "PROVIDER_UNAVAILABLE_OR_ERROR")
        self.assertEqual(classify_4h_row({"status":"WATCH"},t),"MISSING_SOURCE_CANDLE")

    def test_missing_capture_slots_stay_unknown_and_same_bar_buy_dedupes(self):
        a=snap("2026-10-24T00:35:00Z","id1","WATCH",bar="2026-10-23T20:00:00Z")
        b=snap("2026-10-24T04:35:00Z","id2","BUY",bar="2026-10-24T00:00:00Z")
        c=snap("2026-10-24T05:45:00Z","id3","BUY",bar="2026-10-24T00:00:00Z")
        d=snap("2026-10-24T12:40:00Z","id4","PASS",bar="2026-10-24T08:00:00Z")
        result=audit([a,b,c,d],as_of="2026-10-24T13:00:00Z",validator=check)
        self.assertEqual(result["4h_bins_between_first_and_last_actual_capture"],4)
        self.assertEqual(result["4h_bins_observed"],3)
        self.assertEqual(result["4h_bins_unobserved_UNKNOWN"],1)
        self.assertEqual(result["multiple_crypto_captures_in_same_bin"],1)
        self.assertEqual(result["original_technical_BUY_observations"],2)
        self.assertEqual(result["consistent_unique_original_technical_BUY_episodes"],1)
        self.assertEqual(result["quarantined_BUY_episodes"],0)
        self.assertFalse(result["broker_funded_quote_or_account_verified"])

    def test_conflicting_same_candle_buy_watch_is_quarantined(self):
        a=snap("2026-10-24T04:35:00Z","id1","BUY",bar="2026-10-24T00:00:00Z")
        b=snap("2026-10-24T04:56:00Z","id2","WATCH",bar="2026-10-24T00:00:00Z")
        x=audit([a,b],as_of="2026-10-24T05:00:00Z",validator=check)
        self.assertEqual(x["quarantined_BUY_episodes"],1)
        self.assertEqual(x["consistent_unique_original_technical_BUY_episodes"],0)

    def test_pair_changed_under_same_asset_candle_cannot_be_approved(self):
        a=snap("2026-10-24T04:35:00Z","id1","BUY",bar="2026-10-24T00:00:00Z")
        b=snap("2026-10-24T04:56:00Z","id2","BUY",bar="2026-10-24T00:00:00Z",pair="CRV/USDT")
        result=audit([a,b],as_of="2026-10-24T05:00:00Z",validator=check)
        self.assertEqual(result["quarantined_BUY_episodes"],1)

    def test_stale_buy_is_quarantined_never_retroactive_ready(self):
        a=snap("2026-10-24T08:35:00Z","late","BUY",bar="2026-10-23T20:00:00Z")
        r=audit([a],as_of="2026-10-24T09:00:00Z",validator=check)
        self.assertEqual(r["original_technical_BUY_observations"],1)
        self.assertEqual(r["consistent_unique_original_technical_BUY_episodes"],0)
        self.assertEqual(r["quarantined_BUY_episodes"],1)

    def test_identical_replayed_archive_dedupes_idempotently(self):
        a=snap("2026-10-24T04:35:00Z","id1","BUY",bar="2026-10-24T00:00:00Z")
        x=audit([a,a],as_of="2026-10-24T06:00:00Z",validator=check)
        self.assertEqual(x["actual_crypto_snapshots"],1)
        self.assertEqual(x["original_technical_BUY_observations"],1)

    def test_source_hash_tampering_fails_before_signal_classification(self):
        a=snap("2026-10-24T04:35:00Z","id1","BUY",bar="2026-10-24T00:00:00Z")
        a["rows"][1]["status"]="WATCH"
        with self.assertRaisesRegex(ValueError,"SHA-256"):
            audit([a],as_of="2026-10-24T06:00:00Z",validator=check)

    def test_empty_evidence_is_not_a_clean_no_buy_scan(self):
        with self.assertRaisesRegex(RuntimeError,"no real original crypto scans"):
            audit([],as_of="2026-10-24T06:00:00Z",validator=check)

    def test_future_snapshot_not_reported_as_market_quote(self):
        a=snap("2026-10-24T04:35:00Z","id1","BUY",bar="2026-10-24T00:00:00Z")
        with self.assertRaisesRegex(ValueError,"future source"):
            audit([a],as_of="2026-10-24T04:34:00Z",validator=check)


if __name__=="__main__":
    unittest.main()
