"""Synthetic breakout tests; all bars are completed and strictly ordered."""
import math
import unittest
import numpy as np
import pandas as pd
from funded_stock_breakout import evaluate_funded_breakout, validate_breakout_quote


def sample_bars(volume_multiplier=1.7, latest_close_override=None):
    periods = 320
    dates = pd.bdate_range("2025-01-02", periods=periods)
    c = np.zeros(periods, dtype=float)
    for i in range(periods):
        if i < 245:
            c[i] = 100.0 + (60.0 / 245.0) * i
        else:
            j = i - 245
            c[i] = 161.0 + 0.008 * j + 0.48 * math.sin(0.75 * j)
    h = c + 0.6
    l = c - 0.65
    o = c - 0.18
    volume = np.full(periods, 950_000.0)
    # A previously washed-out section of the base allows a measured-move
    # target without inventing a future resistance level.
    l[-24] = 151.75
    resistance = float(h[-61:-1].max())
    c[-1] = resistance + 0.42 if latest_close_override is None else latest_close_override
    h[-1] = c[-1] + 0.15
    l[-1] = c[-1] - 0.65
    o[-1] = c[-1] - 0.40
    volume[-1] = volume[-2] * volume_multiplier
    frame = pd.DataFrame({"Open": o, "High": h, "Low": l,
                          "Close": c, "Volume": volume}, index=dates)
    # Benchmark has a much weaker last-20-day increase, by design.
    index = pd.DataFrame({"Close": np.linspace(4900, 4920, periods)}, index=dates)
    return frame, index


class FundedStockBreakoutTests(unittest.TestCase):
    def test_breakout_needs_an_actual_cross(self):
        bars, bench = sample_bars()
        bars.loc[bars.index[-1], "Close"] = bars["Close"].iloc[-2]
        self.assertIsNone(evaluate_funded_breakout(bars, bench))

    def test_breakout_is_never_retroactively_ready(self):
        bars, bench = sample_bars()
        result = evaluate_funded_breakout(bars, bench)
        self.assertIsNotNone(result)
        self.assertTrue(result["technical_state"].startswith("FUNDED BREAKOUT"))
        self.assertNotIn("entry", result)
        self.assertEqual(result["reference_target_basis"],
                         "0.8x 30-day base height; capped at 5x ATR (projection)")

    def test_high_volume_is_required_for_pending_open(self):
        bars, bench = sample_bars(volume_multiplier=0.8)
        result = evaluate_funded_breakout(bars, bench)
        self.assertIsNotNone(result)
        self.assertEqual(result["technical_state"], "FUNDED BREAKOUT REVIEW")
        self.assertIn("VOLUME", result["technical_reason"])

    def test_entry_only_after_confirmed_live_quote(self):
        plan = {"reference_entry": 100.0, "resistance": 99.0,
                "reference_stop": 98.0, "reference_target": 110.0}
        valid = validate_breakout_quote(plan, 100.2)
        self.assertTrue(valid["ok"])
        self.assertGreaterEqual(valid["reward_risk"], 2)

    def test_gap_chase_is_rejected(self):
        plan = {"reference_entry": 100.0, "resistance": 99.0,
                "reference_stop": 98.0, "reference_target": 110.0}
        self.assertFalse(validate_breakout_quote(plan, 102.0)["ok"])

    def test_broken_resistance_is_rejected(self):
        plan = {"reference_entry": 100.0, "resistance": 99.0,
                "reference_stop": 98.0, "reference_target": 110.0}
        self.assertFalse(validate_breakout_quote(plan, 98.3)["ok"])

    def test_no_price_or_missing_inputs(self):
        self.assertFalse(validate_breakout_quote({}, math.nan)["ok"])
        self.assertIsNone(evaluate_funded_breakout(pd.DataFrame(), None))

    def test_previous_bar_cannot_see_future_breakout(self):
        bars, bench = sample_bars()
        self.assertIsNone(evaluate_funded_breakout(bars.iloc[:-1], bench.iloc[:-1]))


if __name__ == "__main__":
    unittest.main()
