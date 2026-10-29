"""Original-score-preserving Trade source-bar guard regression (no market calls)."""
from __future__ import annotations

import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

from trade_completed_bar_guard import (
    VENUE_CALENDARS, expected_completed_session, validate_completed_trade_frame,
)


def bars(end="2026-10-19", count=270):
    dates = pd.bdate_range(end=end, periods=count)
    return pd.DataFrame({
        "Open": np.full(count, 100.0),
        "High": np.full(count, 102.0),
        "Low": np.full(count, 98.0),
        "Close": np.full(count, 101.0),
        "Volume": np.full(count, 1_000_000.0),
    }, index=dates)


def ny(when):
    return pd.Timestamp(when, tz="America/New_York")


class TradeBarGuardTests(unittest.TestCase):
    def test_nyse_regular_close_then_grace_allows_today(self):
        now = ny("2026-10-19 17:00")
        self.assertEqual(expected_completed_session("nyse", now)[0], "2026-10-19")
        data, receipt = validate_completed_trade_frame(bars(), "nyse", now)
        self.assertEqual(receipt["status"], "PASS")
        self.assertEqual(receipt["last_completed_session"], "2026-10-19")
        self.assertEqual(len(data), 270)
        self.assertFalse(receipt["independently_exchange_verified"])

    def test_incomplete_today_candle_excluded_before_scoring(self):
        data, receipt = validate_completed_trade_frame(
            bars(), "nasdaq", ny("2026-10-19 14:00"))
        self.assertEqual(receipt["status"], "PASS")
        self.assertTrue(receipt["excluded_incomplete_latest_bar"])
        self.assertEqual(str(data.index[-1].date()), "2026-10-16")
        self.assertEqual(len(data), 269)

    def test_early_vendor_grace_protects_unpublished_close(self):
        expected, _ = expected_completed_session("nasdaq", ny("2026-10-19 16:20"))
        self.assertEqual(expected, "2026-10-16")

    def test_stale_postclose_is_blocked_not_promoted_by_score(self):
        data, result = validate_completed_trade_frame(
            bars(end="2026-10-16"), "nasdaq", ny("2026-10-19 17:30"))
        self.assertIsNone(data)
        self.assertEqual(result["reason"], "STALE_OR_MISSING_COMPLETED_BAR")

    def test_short_history_after_excluding_today_is_blocked(self):
        data, result = validate_completed_trade_frame(
            bars(count=252), "nyse", ny("2026-10-19 14:00"))
        self.assertIsNone(data)
        self.assertEqual(result["reason"], "INSUFFICIENT_COMPLETED_HISTORY")

    def test_labor_day_uses_friday_and_holiday_bar_is_rejected(self):
        holiday = ny("2026-09-07 20:00")
        self.assertEqual(expected_completed_session("nyse", holiday)[0], "2026-09-04")
        data, result = validate_completed_trade_frame(
            bars(end="2026-09-07"), "nyse", holiday)
        self.assertIsNone(data)
        self.assertEqual(result["reason"], "NON_EXCHANGE_SESSION_BAR")

    def test_uk_exchange_separate_close_and_dst(self):
        before = pd.Timestamp("2026-10-29 16:50", tz="Europe/London")
        after = pd.Timestamp("2026-10-29 17:40", tz="Europe/London")
        self.assertEqual(expected_completed_session("lse", before)[0], "2026-10-28")
        self.assertEqual(expected_completed_session("lse", after)[0], "2026-10-29")
        data, receipt = validate_completed_trade_frame(bars(end="2026-10-29"), "lse", after)
        self.assertEqual(receipt["status"], "PASS")
        self.assertEqual(str(data.index[-1].date()), "2026-10-29")

    def test_early_us_close_uses_actual_session_close(self):
        # US day after Thanksgiving market closes at 13:00 ET.
        early = ny("2026-11-27 13:50")
        before = ny("2026-11-27 13:20")
        self.assertEqual(expected_completed_session("nyse", early)[0], "2026-11-27")
        self.assertEqual(expected_completed_session("nyse", before)[0], "2026-11-25")

    def test_gettex_otc_without_approved_session_calendar_fail_closed(self):
        for venue in ("gettex", "otc", "unknown"):
            self.assertNotIn(venue, VENUE_CALENDARS)
            with self.subTest(venue=venue):
                data, result = validate_completed_trade_frame(
                    bars(), venue, ny("2026-10-19 17:00"))
                self.assertIsNone(data)
                self.assertEqual(result["reason"], "UNSUPPORTED_EXCHANGE_CALENDAR")

    def test_future_price_bar_rejected(self):
        data, receipt = validate_completed_trade_frame(
            bars(end="2026-10-20"), "nyse", ny("2026-10-19 17:00"))
        self.assertIsNone(data)
        self.assertEqual(receipt["reason"], "FUTURE_SOURCE_BAR")

    def test_malformed_ohlcv_rejected(self):
        sample = bars()
        sample.loc[sample.index[-1], "High"] = 1.0
        data, receipt = validate_completed_trade_frame(
            sample, "nyse", ny("2026-10-19 17:00"))
        self.assertIsNone(data)
        self.assertEqual(receipt["reason"], "INVALID_OHLCV_PRICE_OR_VOLUME")

    def test_source_checksum_is_deterministic_and_date_bound(self):
        now = ny("2026-10-19 17:00")
        a, one = validate_completed_trade_frame(bars(), "nyse", now)
        b, two = validate_completed_trade_frame(bars(), "nyse", now)
        self.assertEqual(one["last_completed_ohlcv_sha256"], two["last_completed_ohlcv_sha256"])
        new = bars()
        new.loc[new.index[-1], "Close"] = 101.5
        _, three = validate_completed_trade_frame(new, "nyse", now)
        self.assertNotEqual(one["last_completed_ohlcv_sha256"], three["last_completed_ohlcv_sha256"])
        self.assertEqual(one["status"], "PASS")

    def test_calendar_failure_is_not_backfilled_by_calendar_days(self):
        with patch("trade_completed_bar_guard.xcals.get_calendar", side_effect=RuntimeError("missing")):
            frame, receipt = validate_completed_trade_frame(
                bars(), "nyse", ny("2026-10-19 17:00"))
        self.assertIsNone(frame)
        self.assertTrue(receipt["reason"].startswith("EXCHANGE_CALENDAR_UNAVAILABLE"))

    def test_daily_bar_index_timezone_normalizes_to_venue(self):
        sample = bars()
        sample.index = sample.index.tz_localize("America/New_York")
        data, receipt = validate_completed_trade_frame(
            sample, "nyse", ny("2026-10-19 17:00"))
        self.assertEqual(receipt["status"], "PASS")
        self.assertEqual(str(data.index[-1].date()), "2026-10-19")

    def test_writer_preserves_actual_bar_receipt_without_claiming_exchange_verification(self):
        from scheduled_trade_technicals import technical_row
        technical = {"technical_state": "WATCH", "technical_score": 63.0}
        receipt = {
            "status": "PASS", "last_completed_session": "2026-10-19",
            "expected_completed_session": "2026-10-19",
            "last_completed_ohlcv_sha256": "sha-test",
            "price_source": "YAHOO_FINANCE_YFINANCE_UNADJUSTED_DAILY",
            "exchange_calendar": "XNYS",
            "source_observed_at_utc": "2026-10-19T21:00:00+00:00",
        }
        row = technical_row("MSFT", technical, 1_000_000, "2026-10-19", receipt)
        self.assertEqual(row["Last completed exchange session"], "2026-10-19")
        self.assertEqual(row["Price session status"], "PASS")
        self.assertEqual(row["Price independently exchange verified"], False)
        self.assertEqual(row["Completed bar OHLCV SHA256"], "sha-test")

    def test_legacy_writer_missing_receipt_defaults_to_blocked(self):
        from scheduled_trade_technicals import technical_row
        row = technical_row("MSFT", {}, 1_000_000, "UNAVAILABLE")
        self.assertEqual(row["Price session status"], "BLOCKED")
        self.assertIsNone(row["Last completed exchange session"])

    def test_full_refresh_with_all_failed_prices_erases_prior_disk_rows(self):
        from scheduled_trade_technicals import persist_prepared_trade_rows
        with tempfile.TemporaryDirectory() as folder:
            cache = Path(folder) / "nyse.csv.gz"
            prior = pd.DataFrame([{"Ticker": "TEST", "Price session status": "UNKNOWN",
                                   "Technical state": "ENTRY READY"}])
            prior.to_csv(cache, index=False, compression="gzip")
            cleared = prior.head(0).copy()
            written = persist_prepared_trade_rows(
                cleared, cache, "2026-10-29T00:00:00Z", {"TEST"}, True,
            )
            self.assertTrue(written.empty)
            saved = pd.read_csv(cache, compression="gzip")
            self.assertTrue(saved.empty)
            self.assertIn("Ticker", saved.columns)

    def test_missing_only_scan_does_not_delete_unattempted_cache(self):
        from scheduled_trade_technicals import persist_prepared_trade_rows
        with tempfile.TemporaryDirectory() as folder:
            cache = Path(folder) / "nyse.csv.gz"
            original = pd.DataFrame([{"Ticker": "TEST", "Technical state": "WATCH"}])
            original.to_csv(cache, index=False, compression="gzip")
            persist_prepared_trade_rows(
                original.head(0), cache, "unknown", {"TEST"}, False,
            )
            self.assertEqual(pd.read_csv(cache, compression="gzip")["Ticker"].tolist(),
                             ["TEST"])

    def test_completed_data_carries_provenance_to_saved_cache(self):
        from scheduled_trade_technicals import persist_prepared_trade_rows
        with tempfile.TemporaryDirectory() as folder:
            cache = Path(folder) / "nyse.csv.gz"
            original = pd.DataFrame([{"Ticker": "TEST", "Price session status": "PASS",
                                      "Last completed exchange session": "2026-10-29"}])
            persist_prepared_trade_rows(
                original, cache, "2026-10-29", {"TEST"}, True,
            )
            saved = pd.read_csv(cache, compression="gzip")
            self.assertEqual(saved.loc[0, "Last completed exchange session"], "2026-10-29")
            self.assertEqual(saved.loc[0, "Price session status"], "PASS")


if __name__ == "__main__":
    unittest.main()
