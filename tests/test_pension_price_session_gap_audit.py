"""Deterministic, no-provider research fixtures for Investment candle identity."""
import unittest
from datetime import datetime, timezone

from scripts.pension_price_session_gap_audit import audit_session


class SessionIdentityResearchTests(unittest.TestCase):
    def test_saturday_stale_tuesday_can_pass_existing_four_day_guard(self):
        result = audit_session(now_utc=datetime(2026, 10, 31, 9, 40, tzinfo=timezone.utc),
                               candle_date="2026-10-27", expected_session="2026-10-30")
        self.assertTrue(result["gap_accepted_by_existing_guard"])
        self.assertFalse(result["buy_readiness_assessed"])

    def test_monday_stale_thursday_can_pass_existing_four_day_guard(self):
        result = audit_session(now_utc=datetime(2026, 10, 26, 12, tzinfo=timezone.utc),
                               candle_date="2026-10-22", expected_session="2026-10-23")
        self.assertTrue(result["gap_accepted_by_existing_guard"])

    def test_saturday_friday_last_session_matches(self):
        result = audit_session(now_utc=datetime(2026, 10, 31, 9, tzinfo=timezone.utc),
                               candle_date="2026-10-30", expected_session="2026-10-30")
        self.assertFalse(result["gap_accepted_by_existing_guard"])
        self.assertTrue(result["matches_independently_supplied_session"])

    def test_old_and_future_candles_rejected_by_existing_guard(self):
        for observed in ("2026-10-20", "2026-11-01", "bad-date"):
            with self.subTest(observed=observed):
                result = audit_session(now_utc=datetime(2026, 10, 31, 9, tzinfo=timezone.utc),
                                       candle_date=observed, expected_session="2026-10-30")
                self.assertFalse(result["existing_calendar_guard_accepts"])
                self.assertFalse(result["gap_accepted_by_existing_guard"])

    def test_missing_clock_timezone_and_future_expected_session_fail(self):
        with self.assertRaises(ValueError):
            audit_session(now_utc=datetime(2026, 10, 31, 9), candle_date="2026-10-30",
                          expected_session="2026-10-30")
        with self.assertRaises(ValueError):
            audit_session(now_utc=datetime(2026, 10, 31, 9, tzinfo=timezone.utc),
                          candle_date="2026-10-30", expected_session="2026-11-02")


if __name__ == "__main__":
    unittest.main()
