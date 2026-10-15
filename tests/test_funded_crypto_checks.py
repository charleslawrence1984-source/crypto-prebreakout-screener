"""Guard Kraken Funded crypto scans against still-open OHLCV bars."""
import unittest

import numpy as np
import pandas as pd

from funded_crypto_checks import (
    completed_funded_crypto_candles, crypto_gate_audit,
    crypto_distance_sensitivity,
)


class FundedCryptoCandleTests(unittest.TestCase):
    def bars(self, stamps, closes=None):
        closes = closes or list(range(1, len(stamps)+1))
        return pd.DataFrame({
            "timestamp": pd.to_datetime(stamps, utc=True),
            "open": closes,
            "high": [float(v)+1 for v in closes],
            "low": [max(float(v)-1, 0.01) for v in closes],
            "close": closes,
            "volume": [1000.0] * len(stamps),
        })

    def test_4h_current_forming_bar_never_changes_signal_input(self):
        now = pd.Timestamp("2026-10-15T11:30:00Z")
        bars = self.bars([
            "2026-10-15T00:00:00Z",
            "2026-10-15T04:00:00Z",
            "2026-10-15T08:00:00Z",
        ], [100., 101., 500.])
        closed = completed_funded_crypto_candles(bars, "4h", now)
        self.assertEqual(len(closed), 2)
        self.assertEqual(closed["close"].tolist(), [100, 101])
        # Drastically revising an unfinished candle is irrelevant to the input.
        bars.loc[2, "close"] = 10.0
        amended = completed_funded_crypto_candles(bars, "4h", now)
        pd.testing.assert_frame_equal(closed, amended)

    def test_4h_new_candle_only_appears_after_close_and_grace(self):
        bars = self.bars(["2026-10-15T04:00:00Z", "2026-10-15T08:00:00Z"])
        before = completed_funded_crypto_candles(
            bars, "4h", pd.Timestamp("2026-10-15T12:00:00Z"),
        )
        self.assertEqual(len(before), 1)
        after = completed_funded_crypto_candles(
            bars, "4h", pd.Timestamp("2026-10-15T12:02:00Z"),
        )
        self.assertEqual(len(after), 2)

    def test_daily_and_weekly_incomplete_bar_excluded(self):
        daily = self.bars(["2026-10-14T00:00:00Z", "2026-10-15T00:00:00Z"])
        closed = completed_funded_crypto_candles(
            daily, "1d", pd.Timestamp("2026-10-15T12:30:00Z"),
        )
        self.assertEqual(len(closed), 1)
        week = self.bars(["2026-10-05T00:00:00Z", "2026-10-12T00:00:00Z"])
        closed_week = completed_funded_crypto_candles(
            week, "1w", pd.Timestamp("2026-10-15T12:30:00Z"),
        )
        self.assertEqual(len(closed_week), 1)

    def test_stale_benchmark_data_fails_closed(self):
        old = self.bars(["2026-10-13T00:00:00Z"])
        with self.assertRaisesRegex(ValueError, "Stale"):
            completed_funded_crypto_candles(
                old, "4h", pd.Timestamp("2026-10-15T12:30:00Z"),
            )

    def test_duplicate_future_dated_and_missing_timeframes(self):
        now = pd.Timestamp("2026-10-15T12:30:00Z")
        bars = self.bars([
            "2026-10-15T04:00:00Z",
            "2026-10-15T04:00:00Z",
            "2026-10-15T20:00:00Z",
        ])
        closed = completed_funded_crypto_candles(bars, "4h", now)
        self.assertEqual(len(closed), 1)
        self.assertEqual(float(closed["close"].iloc[0]), 2.0)
        with self.assertRaises(ValueError):
            completed_funded_crypto_candles(bars, "2h", now)
        with self.assertRaises(ValueError):
            completed_funded_crypto_candles(bars, "4h", pd.Timestamp("2026-10-15"))

    def test_crypto_gate_counts_overlap_not_sum_to_universe(self):
        rows = pd.DataFrame([
            {
                "Coin": "BTC", "Score": 26.2, "Entry timing actionable": True,
                "Swing eligible": False, "To resistance %": 4.2,
                "Execution liquidity pass": True,
                "CL Signal": "🔴 PASS", "Funded status": "🔴 FUNDED PASS",
            },
            {
                "Coin": "ETH", "Score": 16.9, "Entry timing actionable": False,
                "Swing eligible": False, "To resistance %": 9.2,
                "Execution liquidity pass": True,
                "CL Signal": "🔴 PASS", "Funded status": "🔴 FUNDED PASS",
            },
            {
                "Coin": "SHIB", "Score": 27.1, "Entry timing actionable": False,
                "Swing eligible": False, "To resistance %": 11.9,
                "Execution liquidity pass": False,
                "CL Signal": "🔴 PASS", "Funded status": "🔴 FUNDED PASS",
            },
        ])
        counts = dict(zip(
            crypto_gate_audit(rows)["Diagnostic"],
            crypto_gate_audit(rows)["Coins"],
        ))
        self.assertEqual(counts["Coins in report"], 3)
        self.assertEqual(counts["Outside 5% below 4h resistance"], 2)
        self.assertEqual(counts["Below required swing score of 80"], 3)
        self.assertEqual(counts["Timing not actionable"], 2)
        self.assertEqual(counts["Underlying swing shape not eligible"], 3)
        self.assertEqual(counts["Kraken spot-volume proxy below $1m"], 1)
        self.assertEqual(counts["Funded READY/A+ (all live checks)"], 0)

    def test_proximity_widening_does_not_create_a_buy(self):
        scan = pd.DataFrame({
            "Coin": ["BTC", "BNB", "ETH", "SHIB"],
            "To resistance %": [4.2, 5.3, 9.2, 11.9],
            "Score": [26.2, 23.2, 16.9, 27.1],
        })
        sensitivity = crypto_distance_sensitivity(scan)
        counts = dict(zip(
            sensitivity["Hypothetical max distance to resistance"],
            sensitivity["Coins nearby"],
        ))
        self.assertEqual(counts["3%"], 0)
        self.assertEqual(counts["5%"], 1)
        self.assertEqual(counts["10%"], 3)
        self.assertEqual(int(sensitivity["Nearby coins with score >=80"].sum()), 0)
        self.assertTrue(sensitivity["Trade approvals"].str.startswith("NONE").all())

    def test_empty_crypto_audit_does_not_fabricate_qualifying_assets(self):
        result = crypto_gate_audit(pd.DataFrame())
        self.assertEqual(int(result["Coins"].sum()), 0)


if __name__ == "__main__":
    unittest.main()
