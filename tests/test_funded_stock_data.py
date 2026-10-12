"""Data-integrity regression tests for Kraken Funded's live STOCK scan only."""
import unittest
import numpy as np
import pandas as pd
from funded_stock_data import (
    clean_stock_daily_frame, get_validated_stock_history,
)


NOW = pd.Timestamp("2026-10-12 11:00", tz="America/New_York")


def bars(end="2026-10-09", periods=270):
    dates = pd.bdate_range(end=end, periods=periods)
    return pd.DataFrame({
        "Open": np.full(periods, 100.0),
        "High": np.full(periods, 102.0),
        "Low": np.full(periods, 99.0),
        "Close": np.full(periods, 101.0),
        "Volume": np.full(periods, 1000000),
    }, index=dates)


class FundedStockDataTests(unittest.TestCase):
    def test_missing_batch_ticker_recovers_with_single_download(self):
        other_batch = pd.concat({"AAPL": bars()}, axis=1)
        calls = []
        def fetch(symbol):
            calls.append(symbol)
            return bars()
        frame, source, reason = get_validated_stock_history(other_batch, "MRVL", fetch, NOW)
        self.assertEqual(source, "SINGLE-TICKER RETRY")
        self.assertEqual(reason, "PASS")
        self.assertEqual(len(frame), 270)
        self.assertEqual(calls, ["MRVL"])

    def test_healthy_batch_does_not_trigger_download(self):
        def unexpected(_):
            raise AssertionError("unneeded retry")
        batch = pd.concat({"MSFT": bars()}, axis=1)
        frame, source, reason = get_validated_stock_history(batch, "MSFT", unexpected, NOW)
        self.assertEqual(source, "BATCH")
        self.assertEqual(reason, "PASS")
        self.assertEqual(len(frame), 270)

    def test_stale_batch_and_retry_are_blocked(self):
        old = bars(end="2026-09-10")
        frame, source, reason = get_validated_stock_history(
            old, "MRVL", lambda _: old, NOW,
        )
        self.assertIsNone(frame)
        self.assertEqual(source, "NO VALID PRICE DATA")
        self.assertIn("STALE PRICE DATA", reason)

    def test_incomplete_current_bar_is_removed(self):
        data = bars()
        data = pd.concat([data, bars(end="2026-10-12", periods=1)])
        good, status = clean_stock_daily_frame(data, NOW)
        self.assertEqual(status, "PASS")
        self.assertEqual(good.index[-1].date(), pd.Timestamp("2026-10-09").date())

    def test_incomplete_bar_cannot_bypass_history_requirement(self):
        data = bars(periods=251)
        data = pd.concat([data, bars(end="2026-10-12", periods=1)])
        good, reason = clean_stock_daily_frame(data, NOW)
        self.assertIsNone(good)
        self.assertIn("251/252", reason)

    def test_bad_ohlc_is_rejected_not_silently_used(self):
        data = bars(periods=252)
        data.loc[data.index[-1], "High"] = 90.0
        good, reason = clean_stock_daily_frame(data, NOW)
        self.assertIsNone(good)
        self.assertIn("251/252", reason)

    def test_freshness_guard_rejects_future_dated_prices(self):
        future = bars(end="2026-10-15")
        good, reason = clean_stock_daily_frame(future, NOW)
        self.assertIsNone(good)
        self.assertIn("FUTURE-DATED", reason)

    def test_failed_retry_preserves_explanatory_gate(self):
        def unavailable(_):
            raise RuntimeError("rate limited")
        frame, source, reason = get_validated_stock_history(
            pd.DataFrame(), "MRVL", unavailable, NOW,
        )
        self.assertIsNone(frame)
        self.assertEqual(source, "NO VALID PRICE DATA")
        self.assertIn("RETRY ERROR: RuntimeError", reason)


if __name__ == "__main__":
    unittest.main()
