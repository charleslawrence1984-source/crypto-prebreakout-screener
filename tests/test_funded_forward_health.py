"""Research-only Kraken Funded publication proof, no broker calls."""
from datetime import datetime, timezone
import unittest

from funded_forward_capture import create_snapshot
from funded_forward_health import validate_snapshot

WHEN = datetime(2026, 10, 17, 16, 35, tzinfo=timezone.utc)
UNIVERSE = {"stocks": ("SPCX", "AAPL"), "crypto": ("BTC", "ETH")}


def frozen(kind, records):
    return create_snapshot(kind, records, observed_at=WHEN,
                           run_id="test-123", universe=UNIVERSE)


class FundedForwardHealthTests(unittest.TestCase):
    def test_stock_short_history_is_partial_not_no_setup(self):
        d = frozen("stocks", [
            {"asset": "SPCX", "status": "DATA UNAVAILABLE",
             "reason": "BATCH: PRICE HISTORY 88/252 COMPLETED BARS"},
            {"asset": "AAPL", "status": "FUNDED TREND WATCH",
             "bar_timestamp": "2026-10-16"},
        ])
        report = validate_snapshot(d, universe=UNIVERSE)
        self.assertEqual(report["usable_price_histories"], 1)
        self.assertEqual(report["insufficient_252_bar_history"], ["SPCX"])
        self.assertEqual(report["coverage"], "PARTIAL")
        self.assertEqual(report["research_trigger_candidates_NOT_funded_ready"], [])
        self.assertEqual(report["funded_trade_ready_confirmed"], 0)

    def test_crypto_pass_is_rejection_and_watch_not_ready(self):
        d = frozen("crypto", [
            {"asset": "BTC", "status": "PASS",
             "bar_timestamp": "2026-10-17 12:00:00+00:00"},
            {"asset": "ETH", "status": "WATCH",
             "bar_timestamp": "2026-10-17 12:00:00+00:00"},
        ])
        report = validate_snapshot(d, universe=UNIVERSE)
        self.assertEqual(report["rejected_pass_rows"], 1)
        self.assertEqual(report["coverage"], "FULL")
        self.assertEqual(report["research_trigger_candidates_NOT_funded_ready"], [])
        self.assertEqual(report["funded_trade_ready_confirmed"], 0)

    def test_crypto_buy_is_only_unverified_research_trigger(self):
        d = frozen("crypto", [
            {"asset": "BTC", "status": "BUY",
             "bar_timestamp": "2026-10-17 12:00:00+00:00",
             "meta": {"funded_bid_ask_verified": False}},
            {"asset": "ETH", "status": "PASS",
             "bar_timestamp": "2026-10-17 12:00:00+00:00"},
        ])
        report = validate_snapshot(d, universe=UNIVERSE)
        self.assertEqual(report["research_trigger_candidates_NOT_funded_ready"], ["BTC"])
        self.assertEqual(report["funded_trade_ready_confirmed"], 0)

    def test_archive_digest_tampering_is_rejected(self):
        d = frozen("crypto", [
            {"asset": "BTC", "status": "PASS", "bar_timestamp": "2026-10-17 12:00:00+00:00"},
            {"asset": "ETH", "status": "WATCH", "bar_timestamp": "2026-10-17 12:00:00+00:00"},
        ])
        d["rows"][0]["status"] = "BUY"
        with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
            validate_snapshot(d, universe=UNIVERSE)

    def test_future_completed_bar_does_not_claim_full_coverage(self):
        d = frozen("crypto", [
            {"asset": "BTC", "status": "PASS",
             "bar_timestamp": "2026-10-18 16:00:00+00:00"},
            {"asset": "ETH", "status": "WATCH",
             "bar_timestamp": "2026-10-17 12:00:00+00:00"},
        ])
        report = validate_snapshot(d, universe=UNIVERSE)
        self.assertEqual(report["coverage"], "PARTIAL")
        self.assertEqual(len(report["bar_evidence_warnings"]), 1)

    def test_disconnected_broker_and_authorization_is_enforced(self):
        d = frozen("stocks", [
            {"asset": "SPCX", "status": "DATA UNAVAILABLE"},
            {"asset": "AAPL", "status": "WATCH", "bar_timestamp": "2026-10-16"},
        ])
        d["broker_account_connected"] = True
        with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
            validate_snapshot(d, universe=UNIVERSE)


if __name__ == "__main__":
    unittest.main()
