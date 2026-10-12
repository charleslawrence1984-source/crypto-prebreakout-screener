"""Synthetic earnings-calendar and manual-review checks for funded stocks."""
from datetime import datetime, date, timedelta, timezone
import unittest

import pandas as pd
from funded_stock_events import (
    lookup_estimated_earnings,
    earnings_window,
    validate_official_event_verification,
    earnings_trade_gate,
)


TODAY = date(2026, 10, 12)
NOW = pd.Timestamp("2026-10-12T11:30:00+01:00")


class MockTicker:
    def __init__(self, calendar=None, schedule=None, fail_calendar=False):
        self._calendar = calendar
        self._schedule = schedule
        self.fail_calendar = fail_calendar

    @property
    def calendar(self):
        if self.fail_calendar:
            raise ValueError("temporarily unavailable")
        return self._calendar

    def get_earnings_dates(self, limit=12):
        return self._schedule


def official(event="2026-11-20", timestamp="2026-10-12T11:29:00+01:00",
             source="https://investor.example.com/results"):
    return {"ticker": "NVDA", "next_date": event,
            "source_url": source, "user_confirmed": True,
            "checked_at": timestamp}


class FundedEarningsTests(unittest.TestCase):
    def test_calendar_prefers_nearest_future_estimate_over_expired(self):
        ticker = MockTicker(calendar={"Earnings Date": [
            "2026-07-01", "2026-11-14", "2026-12-20",
        ]})
        result = lookup_estimated_earnings(ticker, TODAY)
        self.assertEqual(result["estimated_date"], "2026-11-14")
        self.assertIn("UNVERIFIED", result["source"])

    def test_schedule_fallback_when_calendar_unavailable(self):
        df = pd.DataFrame({"EPS Estimate": [1, 2, 3]},
                          index=pd.to_datetime(["2026-02-01", "2026-10-28", "2027-01-28"]))
        result = lookup_estimated_earnings(
            MockTicker(schedule=df, fail_calendar=True), TODAY
        )
        self.assertEqual(result["estimated_date"], "2026-10-28")
        self.assertIn("schedule", result["source"])

    def test_provider_only_has_old_events(self):
        ticker = MockTicker(calendar={"Earnings Date": ["2026-08-22"]})
        result = lookup_estimated_earnings(ticker, TODAY)
        self.assertEqual(result["estimated_date"], "")
        self.assertEqual(result["quality"], "NO UPCOMING DATE FOUND")

    def test_estimated_date_inside_five_weekdays_blocks(self):
        record = {"Ticker": "NVDA", "Next earnings estimate": "2026-10-15"}
        self.assertEqual(earnings_trade_gate(record, {"NVDA": official()}, TODAY, NOW),
                         "🔴 FUNDED PASS · EARNINGS WINDOW")

    def test_verified_date_inside_five_weekdays_blocks(self):
        row = {"Ticker": "NVDA", "Next earnings estimate": "2026-12-01"}
        self.assertEqual(
            earnings_trade_gate(row, {"NVDA": official(event="2026-10-15")}, TODAY, NOW),
            "🔴 FUNDED PASS · EARNINGS WINDOW",
        )

    def test_unknown_estimate_with_verified_company_date_can_pass(self):
        row = {"Ticker": "NVDA", "Next earnings estimate": ""}
        self.assertEqual(earnings_trade_gate(row, {"NVDA": official()}, TODAY, NOW), "")

    def test_unverified_company_event_blocks(self):
        row = {"Ticker": "NVDA", "Next earnings estimate": "2026-11-20"}
        self.assertIn(
            "VERIFY EARNINGS",
            earnings_trade_gate(row, {}, TODAY, NOW),
        )

    def test_past_date_is_not_an_upcoming_verification(self):
        row = {"Ticker": "NVDA", "Next earnings estimate": ""}
        result = earnings_trade_gate(
            row, {"NVDA": official(event="2026-01-12")}, TODAY, NOW
        )
        self.assertIn("VERIFY UPCOMING EARNINGS", result)

    def test_conflicting_near_term_estimate_and_corporate_date_blocks(self):
        row = {"Ticker": "NVDA", "Next earnings estimate": "2026-10-29"}
        result = earnings_trade_gate(
            row, {"NVDA": official(event="2026-11-25")}, TODAY, NOW
        )
        self.assertIn("EARNINGS DATE CONFLICT", result)

    def test_same_day_official_source_record_is_required(self):
        checked, why = validate_official_event_verification(
            official(timestamp="2026-10-11T23:15:00+01:00"),
            "NVDA", TODAY, NOW,
        )
        self.assertIsNone(checked)
        self.assertEqual(why, "STALE EARNINGS VERIFICATION")

    def test_untrusted_noncompany_source_rejected(self):
        checked, why = validate_official_event_verification(
            official(source="https://finance.yahoo.com/quote/NVDA"),
            "NVDA", TODAY, NOW,
        )
        self.assertIsNone(checked)
        self.assertEqual(why, "COMPANY INVESTOR-RELATIONS SOURCE REQUIRED")

    def test_risk_labels_cover_unknown_expired_and_near(self):
        self.assertEqual(earnings_window(None, TODAY), "DATE UNKNOWN")
        self.assertEqual(earnings_window("2026-01-01", TODAY), "DATE EXPIRED")
        self.assertEqual(earnings_window("2026-10-13", TODAY), "WITHIN 5 SESSIONS")
        self.assertEqual(earnings_window("2026-11-13", TODAY),
                         "OUTSIDE 5 SESSIONS (UNVERIFIED)")

    def test_failed_provider_cannot_fabricate_date(self):
        class Broken:
            @property
            def calendar(self):
                raise RuntimeError("no data")
            def get_earnings_dates(self, limit=12):
                raise RuntimeError("rate limited")
        result = lookup_estimated_earnings(Broken(), TODAY)
        self.assertEqual(result["estimated_date"], "")
        self.assertIn("SCHEDULE RuntimeError", result["quality"])


if __name__ == "__main__":
    unittest.main()
