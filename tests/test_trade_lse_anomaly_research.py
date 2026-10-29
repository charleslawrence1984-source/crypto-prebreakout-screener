"""Deterministic fail-closed LSE date/provenance research tests; no network."""
import unittest
from datetime import datetime, timezone

import pandas as pd

from trade_lse_anomaly_research import (
    ANOMALY_DAY, SAMPLES, invalid_ohlcv_reason, rows_in_window, invalid_dates_in_history,
    run_audit, stooq_result, summarize, yahoo_result,
)


def window_frame():
    dates = pd.to_datetime(["2024-04-30", ANOMALY_DAY, "2024-05-02"])
    frame = pd.DataFrame({
        "Open": [100, 101, 103], "High": [105, 102, 106],
        "Low": [99, 99, 101], "Close": [103, 103, 104],
        "Volume": [4000, 5000, 6000],
    }, index=dates)
    return frame


CSV = """Date,Open,High,Low,Close,Volume
2024-04-30,100,105,99,103,4000
2024-05-01,101,104,99,103,5000
2024-05-02,103,106,101,104,6000
"""


class LondonAnomalyTests(unittest.TestCase):
    def test_invalid_source_bar_is_reported_not_repaired(self):
        row = rows_in_window(window_frame())[1]
        self.assertEqual(row["date"], ANOMALY_DAY)
        self.assertEqual(row["geometry_issue"], "HIGH_BELOW_OPEN_LOW_OR_CLOSE")
        self.assertEqual(row["High"], 102)
        self.assertEqual(row["Close"], 103)

    def test_missing_price_and_volume_are_rejected(self):
        row = {"Open": 100, "High": 102, "Low": 99,
               "Close": None, "Volume": 5000}
        self.assertEqual(invalid_ohlcv_reason(row), "NONFINITE" if False else "MISSING_OR_NONNUMERIC")
        row["Close"] = 101
        row["Volume"] = -1
        self.assertEqual(invalid_ohlcv_reason(row), "NONPOSITIVE_OR_NEGATIVE_VOLUME")

    def test_yahoo_evidence_has_hash_and_original_row(self):
        got = yahoo_result("AZN.L", lambda _: window_frame())
        self.assertEqual(got["status"], "SOURCE_ROWS_OBSERVED")
        self.assertEqual(got["anomaly_day_issue"], "HIGH_BELOW_OPEN_LOW_OR_CLOSE")
        self.assertEqual(len(got["source_response_sha256"]), 64)
        self.assertEqual(got["anomaly_day_ohlcv"]["Close"], 103)

    def test_source_outage_is_unknown_not_clean(self):
        got = yahoo_result("AZN.L", lambda _: pd.DataFrame())
        self.assertEqual(got["status"], "UNKNOWN")
        self.assertIsNone(got["anomaly_day_issue"])

    def test_true_secondary_csv_can_be_observed_but_not_certified(self):
        data = stooq_result("azn.uk", lambda _: CSV)
        self.assertEqual(data["status"], "INDEPENDENT_PROVIDER_DAILY_CANDLE_OBSERVED")
        self.assertIsNone(data["anomaly_day_issue"])
        self.assertEqual(len(data["source_response_sha256"]), 64)
        self.assertIn("UNVERIFIED", data["price_comparability_to_yahoo"])

    def test_provider_key_limit_and_html_are_unknown(self):
        for payload in ("Exceeded daily hits limit", "<html>API Key</html>", "No data"):
            with self.subTest(payload=payload):
                data = stooq_result("azn.uk", lambda _: payload)
                self.assertEqual(data["status"], "UNKNOWN")
                self.assertIsNone(data["anomaly_day_ohlcv"])

    def test_absent_independent_date_is_not_a_verified_comparison(self):
        csv = "Date,Open,High,Low,Close,Volume\n2024-04-30,100,105,99,103,4000\n"
        data = stooq_result("azn.uk", lambda _: csv)
        self.assertEqual(data["status"], "INDEPENDENT_PROVIDER_DATE_ABSENT")

    def test_comparison_never_approves_replacement_or_trade(self):
        yahoo = yahoo_result("AZN.L", lambda _: window_frame())
        independent = stooq_result("azn.uk", lambda _: CSV)
        result = summarize(yahoo, independent)
        self.assertEqual(result["status"], "TWO_PROVIDER_CANDLES_OBSERVED_NOT_NORMALIZED")
        self.assertFalse(result["automatic_replacement_or_repair_authorized"])
        self.assertFalse(result["independent_exchange_verified"])

    def test_full_history_detects_anomalous_date_without_price_repair(self):
        frame = window_frame()
        original = frame.copy(deep=True)
        bad = invalid_dates_in_history(frame)
        self.assertEqual(bad, [{"date": ANOMALY_DAY,
                                "geometry_issue": "HIGH_BELOW_OPEN_LOW_OR_CLOSE"}])
        pd.testing.assert_frame_equal(frame, original)

    def test_cross_section_lists_repeated_day_for_all_control_symbols(self):
        report = run_audit(
            now=datetime(2026, 10, 29, 10, tzinfo=timezone.utc),
            yahoo_fetch=lambda _: window_frame(),
            stooq_fetch=lambda _: "No data",
        )
        self.assertCountEqual(
            report["yahoo_cross_section_invalid_dates"][ANOMALY_DAY], SAMPLES,
        )
    def test_entire_report_preserves_all_six_and_never_readiness(self):
        report = run_audit(
            now=datetime(2026, 10, 29, 10, tzinfo=timezone.utc),
            yahoo_fetch=lambda _: window_frame(),
            stooq_fetch=lambda _: "<html>API key required</html>",
        )
        self.assertEqual(report["trade_production_readiness"], "NOT_VERIFIED")
        self.assertEqual(len(report["source"]), len(SAMPLES))
        self.assertTrue(all(x["status"] == "UNKNOWN" for x in report["secondary"].values()))
        self.assertEqual(report["research_decision"], "NO_TRADE_SIGNAL_OR_HISTORY_REPAIR")


if __name__ == "__main__":
    unittest.main()
