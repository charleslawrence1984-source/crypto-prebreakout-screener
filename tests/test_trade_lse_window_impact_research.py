"""No-lookahead diagnostic tests for old vs current-window price defects."""
import unittest

import numpy as np
import pandas as pd

from trade_lse_window_impact_research import inspect_frame, run


def frame():
    dates = pd.bdate_range(end="2026-10-28", periods=700)
    return pd.DataFrame({
        "Open": np.full(len(dates), 100.0),
        "High": np.full(len(dates), 105.0),
        "Low": np.full(len(dates), 95.0),
        "Close": np.full(len(dates), 101.0),
        "Volume": np.full(len(dates), 10000.0),
    }, index=dates)


class WindowImpactTests(unittest.TestCase):
    def test_old_invalid_row_outside_recent_window_stays_unresolved(self):
        data = frame()
        date = data.index[100]
        data.loc[date, "High"] = 90.0
        result = inspect_frame(data, "AZN.L")
        self.assertEqual(len(result["all_bad_sessions"]), 1)
        self.assertEqual(len(result["recent_bad_sessions"]), 0)
        self.assertTrue(result["recent_window_clean_not_live_verified"])
        self.assertEqual(result["status"], "HISTORICAL_DEFECT_OUTSIDE_LAST_252_NOT_CLEARED")
        self.assertFalse(result["historical_asof_backtest_allowed"])

    def test_bad_current_window_is_not_safely_scored(self):
        data = frame()
        date = data.index[-3]
        data.loc[date, "Low"] = 110
        result = inspect_frame(data, "SHEL.L")
        self.assertEqual(len(result["recent_bad_sessions"]), 1)
        self.assertEqual(result["status"], "DEFECT_IN_RECENT_WINDOW")
        self.assertFalse(result["recent_window_clean_not_live_verified"])

    def test_short_source_never_qualifies_as_complete(self):
        result = inspect_frame(frame().tail(20), "BP.L")
        self.assertEqual(result["status"], "INSUFFICIENT_RECENT_SOURCE_BARS")
        self.assertFalse(result["historical_asof_backtest_allowed"])

    def test_valid_geometry_does_not_qualify_buy_or_live_readiness(self):
        result = inspect_frame(frame(), "VOD.L")
        self.assertEqual(result["status"], "NO_GEOMETRY_DEFECT_OBSERVED_NOT_CERTIFIED")
        self.assertEqual(result["live_trade_readiness"], "NOT_VERIFIED")
        self.assertFalse(result["historical_asof_backtest_allowed"])

    def test_intraday_2026_session_is_excluded_from_last_252(self):
        from trade_lse_window_impact_research import expected_completed_lse_session
        now = pd.Timestamp("2026-10-29 11:30", tz="Europe/London")
        self.assertEqual(expected_completed_lse_session(now), "2026-10-28")
        data = frame()
        data.loc[pd.Timestamp("2026-10-29")] = [100, 106, 95, 101, 10000]
        result = inspect_frame(data, "AZN.L", now=now)
        self.assertEqual(result["recent_window_last"], "2026-10-28")
        self.assertEqual(result["excluded_uncompleted_source_rows"], 1)
        self.assertEqual(result["live_trade_readiness"], "NOT_VERIFIED")

    def test_after_lse_close_plus_grace_includes_today(self):
        from trade_lse_window_impact_research import expected_completed_lse_session
        later = pd.Timestamp("2026-10-29 17:45", tz="Europe/London")
        self.assertEqual(expected_completed_lse_session(later), "2026-10-29")
        data = frame()
        data.loc[pd.Timestamp("2026-10-29")] = [100, 106, 95, 101, 10000]
        result = inspect_frame(data, "AZN.L", now=later)
        self.assertEqual(result["recent_window_last"], "2026-10-29")
        self.assertEqual(result["excluded_uncompleted_source_rows"], 0)

    def test_provider_errors_stay_unknown(self):
        result = run(fetch=lambda _: (_ for _ in ()).throw(RuntimeError("outage")))
        self.assertTrue(all(x["status"] == "PROVIDER_UNKNOWN" for x in result["reports"]))
        self.assertEqual(result["readiness"], "NOT_VERIFIED")


if __name__ == "__main__":
    unittest.main()
