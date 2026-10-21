"""Kraken Funded stock OHLCV/NYSE-completed-session guard regression tests."""
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from funded_stock_data import (
    clean_stock_daily_frame, expected_completed_nyse_session,
    get_validated_stock_history,
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

    def test_monday_after_close_rejects_friday_stale_bar(self):
        monday = pd.Timestamp("2026-10-19 17:30", tz="America/New_York")
        old, reason = clean_stock_daily_frame(bars(end="2026-10-16"), monday)
        self.assertIsNone(old)
        self.assertIn("STALE PRICE DATA", reason)
        self.assertIn("2026-10-19", reason)

    def test_tuesday_after_close_rejects_monday_stale_bar(self):
        tuesday = pd.Timestamp("2026-10-20 19:00", tz="America/New_York")
        old, reason = clean_stock_daily_frame(bars(end="2026-10-19"), tuesday)
        self.assertIsNone(old)
        self.assertIn("EXPECTED COMPLETED NYSE SESSION 2026-10-20", reason)

    def test_last_completed_session_after_normal_close_with_grace(self):
        monday = pd.Timestamp("2026-10-19 17:00", tz="America/New_York")
        self.assertEqual(str(expected_completed_nyse_session(monday)), "2026-10-19")
        good, reason = clean_stock_daily_frame(bars(end="2026-10-19"), monday)
        self.assertEqual(reason, "PASS")
        self.assertEqual(str(good.index[-1].date()), "2026-10-19")

    def test_preclose_current_day_bar_never_scored(self):
        monday = pd.Timestamp("2026-10-19 13:00", tz="America/New_York")
        data = bars(end="2026-10-19")
        frame, reason = clean_stock_daily_frame(data, monday)
        self.assertEqual(reason, "PASS")
        self.assertEqual(str(frame.index[-1].date()), "2026-10-16")

    def test_after_close_inside_vendor_grace_still_uses_friday(self):
        monday = pd.Timestamp("2026-10-19 16:20", tz="America/New_York")
        self.assertEqual(str(expected_completed_nyse_session(monday)), "2026-10-16")
        frame, reason = clean_stock_daily_frame(bars(end="2026-10-19"), monday)
        self.assertEqual(reason, "PASS")
        self.assertEqual(str(frame.index[-1].date()), "2026-10-16")

    def test_stale_batch_retries_to_fresh_monday(self):
        now = pd.Timestamp("2026-10-19 17:30", tz="America/New_York")
        calls = []
        def retry(symbol):
            calls.append(symbol)
            return bars(end="2026-10-19")
        data, origin, reason = get_validated_stock_history(
            pd.concat({"MSFT": bars(end="2026-10-16")}, axis=1),
            "MSFT", retry, now,
        )
        self.assertEqual((origin, reason), ("SINGLE-TICKER RETRY", "PASS"))
        self.assertEqual(calls, ["MSFT"])
        self.assertEqual(str(data.index[-1].date()), "2026-10-19")

    def test_stale_batch_and_stale_single_retry_fail_closed(self):
        now = pd.Timestamp("2026-10-19 17:30", tz="America/New_York")
        data, origin, reason = get_validated_stock_history(
            bars(end="2026-10-16"), "MSFT",
            lambda _: bars(end="2026-10-16"), now,
        )
        self.assertIsNone(data)
        self.assertEqual(origin, "NO VALID PRICE DATA")
        self.assertIn("BATCH: STALE PRICE DATA", reason)
        self.assertIn("RETRY: STALE PRICE DATA", reason)

    def test_weekend_uses_friday_session(self):
        sunday = pd.Timestamp("2026-10-18 20:00", tz="America/New_York")
        self.assertEqual(str(expected_completed_nyse_session(sunday)), "2026-10-16")
        data, reason = clean_stock_daily_frame(bars(end="2026-10-16"), sunday)
        self.assertEqual(reason, "PASS")
        self.assertIsNotNone(data)

    def test_labor_day_holiday_skips_monday(self):
        labor_day = pd.Timestamp("2026-09-07 20:00", tz="America/New_York")
        self.assertEqual(str(expected_completed_nyse_session(labor_day)), "2026-09-04")
        data, reason = clean_stock_daily_frame(bars(end="2026-09-04"), labor_day)
        self.assertEqual(reason, "PASS")
        self.assertIsNotNone(data)

    def test_good_friday_is_closed(self):
        good_friday = pd.Timestamp("2026-04-03 20:00", tz="America/New_York")
        self.assertEqual(str(expected_completed_nyse_session(good_friday)), "2026-04-02")

    def test_columbus_day_is_open_despite_federal_holiday(self):
        columbus_day = pd.Timestamp("2026-10-12 17:00", tz="America/New_York")
        self.assertEqual(str(expected_completed_nyse_session(columbus_day)), "2026-10-12")
        data, reason = clean_stock_daily_frame(bars(end="2026-10-09"), columbus_day)
        self.assertIsNone(data)
        self.assertIn("STALE PRICE DATA", reason)

    def test_thanksgiving_early_close_has_actual_market_close(self):
        early = pd.Timestamp("2026-11-27 13:50", tz="America/New_York")
        self.assertEqual(str(expected_completed_nyse_session(early)), "2026-11-27")
        before = pd.Timestamp("2026-11-27 13:20", tz="America/New_York")
        self.assertEqual(str(expected_completed_nyse_session(before)), "2026-11-25")

    def test_nontrading_holiday_bar_is_not_silently_used(self):
        holiday = pd.Timestamp("2026-09-07 20:00", tz="America/New_York")
        data, reason = clean_stock_daily_frame(bars(end="2026-09-07"), holiday)
        self.assertIsNone(data)
        self.assertIn("NON-COMPLETED OR NON-SESSION", reason)

    def test_new_york_time_crosses_utc_midnight(self):
        instant = pd.Timestamp("2026-10-21 01:00:00", tz="UTC")
        self.assertEqual(str(expected_completed_nyse_session(instant)), "2026-10-20")
        good, reason = clean_stock_daily_frame(bars(end="2026-10-19"), instant)
        self.assertIsNone(good)
        self.assertIn("STALE PRICE DATA", reason)

    def test_exchange_calendar_failure_does_not_fall_back_to_five_days(self):
        with patch("funded_stock_data._nyse", side_effect=ValueError("unavailable")):
            good, reason = clean_stock_daily_frame(bars(), NOW)
        self.assertIsNone(good)
        self.assertIn("NYSE SESSION CALENDAR UNAVAILABLE", reason)


if __name__ == "__main__":
    unittest.main()
