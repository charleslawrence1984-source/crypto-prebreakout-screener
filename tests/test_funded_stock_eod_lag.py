import unittest
from scripts.audit_funded_stock_eod_lag import audit


class StockEODLagTests(unittest.TestCase):
    def row(self, **kw):
        data = dict(captured_at_utc="2026-10-23T01:00:00Z", symbol="AAPL",
                    expected_session="2026-10-22", last_bar_session="2026-10-21",
                    source="yahoo", archive_sha256_ok="true", provider_error="")
        data.update(kw)
        return data

    def test_previous_session_is_stale_after_close(self):
        self.assertEqual(audit([self.row()])[0]["counts"]["STALE"], 1)

    def test_correct_previous_calendar_day_is_fresh(self):
        self.assertTrue(audit([self.row(last_bar_session="2026-10-22")])[0]["all_rows_fresh"])

    def test_provider_error_not_no_setup(self):
        self.assertEqual(audit([self.row(provider_error="timeout")])[0]["counts"]["PROVIDER_ERROR"], 1)

    def test_unverified_archive_not_fresh(self):
        r = audit([self.row(archive_sha256_ok="false", last_bar_session="2026-10-22")])[0]
        self.assertFalse(r["all_rows_fresh"])

    def test_missing_bar(self):
        self.assertEqual(audit([self.row(last_bar_session="")])[0]["counts"]["MISSING_BAR"], 1)

    def test_future_bar_rejected(self):
        with self.assertRaises(ValueError):
            audit([self.row(last_bar_session="2026-10-23")])

    def test_timezone_required(self):
        with self.assertRaises(ValueError):
            audit([self.row(captured_at_utc="2026-10-23T01:00:00")])

    def test_group_multiple_symbols(self):
        r = audit([self.row(), self.row(symbol="MSFT", last_bar_session="2026-10-22")])[0]
        self.assertEqual(r["observed_rows"], 2)
        self.assertFalse(r["all_rows_fresh"])


if __name__ == "__main__":
    unittest.main()
