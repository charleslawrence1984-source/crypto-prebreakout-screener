"""Deterministic offline negative controls; no exchange or account dependencies."""
import sys
from pathlib import Path
import unittest
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from crypto_closed_candle_research import audit_candles, parse_utc


def row(open_hour, close=100):
    return [
        int(datetime(2026, 10, 31, open_hour, tzinfo=timezone.utc).timestamp() * 1000),
        close, close + 1, close - 1, close, 1,
    ]


class CandleAuditTests(unittest.TestCase):
    NOW = "2026-10-31T10:50:33Z"

    def test_forming_4h_bar_is_not_completed(self):
        result = audit_candles([row(0), row(4), row(8, 140)], "4h", self.NOW)
        self.assertTrue(result["scorer_last_bar_differs_from_completed"])
        self.assertIn("SUPPLIED_LAST_BAR_UNCLOSED", result["reasons"])
        self.assertEqual(result["latest_completed_open_ms"], row(4)[0])

    def test_only_completed_4h_bars_structurally_complete(self):
        result = audit_candles([row(0), row(4)], "4h", self.NOW)
        self.assertEqual(result["status"], "STRUCTURALLY_COMPLETE_ONLY")

    def test_duplicate_identity_blocked(self):
        result = audit_candles([row(0), row(4), row(4)], "4h", self.NOW)
        self.assertEqual(result["duplicate_open_times"], 1)
        self.assertIn("DUPLICATE_OPEN_TIMES", result["reasons"])

    def test_missing_4h_bin_is_unknown_not_no_signal(self):
        result = audit_candles([row(0), row(8)], "4h", self.NOW)
        self.assertEqual(result["missing_bins"], 1)
        self.assertIn("MISSING_BINS", result["reasons"])

    def test_out_of_order_blocked(self):
        result = audit_candles([row(4), row(0)], "4h", self.NOW)
        self.assertIn("OUT_OF_ORDER", result["reasons"])

    def test_future_open_blocked(self):
        result = audit_candles([row(0), row(12)], "4h", self.NOW)
        self.assertIn("FUTURE_OPEN", result["reasons"])

    def test_bad_ohlcv_blocked(self):
        bad = row(4)
        bad[2] = bad[3] - 1
        result = audit_candles([row(0), bad], "4h", self.NOW)
        self.assertIn("INVALID_ROWS", result["reasons"])

    def test_timeframe_1d_1w_and_utc_required(self):
        self.assertEqual(audit_candles([row(0)], "1d", self.NOW)["status"], "RESEARCH_BLOCKED")
        self.assertEqual(audit_candles([row(0)], "1w", self.NOW)["status"], "RESEARCH_BLOCKED")
        with self.assertRaises(ValueError):
            parse_utc("2026-10-31T10:50:33")

    def test_no_rows_is_blocked(self):
        self.assertIn("NO_VALID_BARS", audit_candles([], "4h", self.NOW)["reasons"])


if __name__ == "__main__":
    unittest.main()
