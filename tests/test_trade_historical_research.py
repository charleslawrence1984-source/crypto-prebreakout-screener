"""Deterministic tests for read-only Trade historical research; no network."""
import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from trade_historical_research import (
    RESEARCH_ONLY,
    entry_gate,
    exit_trade,
    manifest_audit,
    research_symbol,
    validate_bars,
)


def sample_bars():
    dates = pd.bdate_range("2024-01-01", periods=270)
    return pd.DataFrame({
        "Open": [100.0] * 270,
        "High": [101.0] * 270,
        "Low": [99.0] * 270,
        "Close": [100.0] * 270,
        "Volume": [1000000.0] * 270,
    }, index=dates)


def known_signal(asof, benchmark):
    if len(asof) == 260:
        return {
            "technical_state": "AWAITING NEXT OPEN",
            "price": 100, "atr20": 4, "zone_high": 102,
            "stop": 95, "target": 110,
            "technical_score": 82, "technical_tier": "A",
        }
    return {"technical_state": "BLOCKED"}


class ResearchTests(unittest.TestCase):
    def test_input_missing_or_impossible_candles_fail_closed(self):
        df = sample_bars()
        df.iloc[2, df.columns.get_loc("High")] = 50
        with self.assertRaises(ValueError):
            validate_bars(df)
        df = sample_bars().iloc[::-1]
        with self.assertRaises(ValueError):
            validate_bars(df)
        df = sample_bars().drop(columns=["Volume"])
        with self.assertRaises(ValueError):
            validate_bars(df)

    def test_opening_gap_and_rr_must_still_pass(self):
        plan = known_signal(sample_bars().iloc[:260], None)
        self.assertEqual(entry_gate(plan, 100), "PASS")
        self.assertEqual(entry_gate(plan, 104), "OPENING_GAP_OVER_0_5_ATR")
        self.assertEqual(entry_gate(plan, 94), "STOP_AT_OPEN")
        self.assertNotEqual(entry_gate({**plan, "target": 108}, 100), "PASS")
        self.assertEqual(entry_gate({}, 100), "MISSING_PLAN")

    def test_entry_day_ambiguous_bar_is_stop_first(self):
        df = sample_bars().iloc[260:262].copy()
        df.iloc[0, df.columns.get_loc("High")] = 115
        df.iloc[0, df.columns.get_loc("Low")] = 94
        result = exit_trade(df, 100, 95, 110)
        self.assertEqual(result["exit_reason"], "STOP_FIRST_IN_BAR")
        self.assertEqual(result["exit_price"], 95.0)
        self.assertEqual(result["net_r"], -1.1)

    def test_adverse_gap_uses_open_not_stop(self):
        df = sample_bars().iloc[260:262].copy()
        df.iloc[0, df.columns.get_loc("Open")] = 90
        df.iloc[0, df.columns.get_loc("Low")] = 89
        result = exit_trade(df, 100, 95, 110)
        self.assertEqual(result["exit_reason"], "GAP_THROUGH_STOP")
        self.assertEqual(result["net_r"], -2.1)

    def test_net_costs_subtracted_from_winning_trade(self):
        df = sample_bars().iloc[260:262].copy()
        df.iloc[1, df.columns.get_loc("High")] = 112
        result = exit_trade(df, 100, 95, 110, 50)
        self.assertEqual(result["exit_reason"], "TARGET")
        self.assertEqual(result["net_r"], 1.9)

    def test_complete_future_exit_and_only_asof_decisions(self):
        df = sample_bars()
        df.iloc[261, df.columns.get_loc("High")] = 111
        calls = []
        def tracked(asof, benchmark):
            calls.append(len(asof))
            return known_signal(asof, benchmark)
        result = research_symbol("FIXTURE", df, horizon=3, evaluator=tracked)
        self.assertEqual(result["counters"]["completed_research_trades"], 1)
        self.assertEqual(len(result["events"]), 1)
        self.assertEqual(result["events"][0]["research_scope"], RESEARCH_ONLY)
        self.assertEqual(result["events"][0]["exit_reason"], "TARGET")
        self.assertEqual(min(calls), 252)
        self.assertTrue(all(x <= len(df) - 1 for x in calls))
        # Changing later candles cannot change signal or the 3-day outcome.
        later = df.copy()
        later.iloc[265:, later.columns.get_loc("High")] = 150
        self.assertEqual(
            research_symbol("FIXTURE", later, horizon=3, evaluator=known_signal)["events"],
            result["events"],
        )

    def test_signal_without_full_outcome_is_censored(self):
        df = sample_bars().iloc[:263]
        result = research_symbol("FIXTURE", df, horizon=10, evaluator=known_signal)
        self.assertEqual(result["counters"]["censored_no_full_horizon"], 1)
        self.assertEqual(result["events"], [])

    def test_reject_overextended_next_open(self):
        df = sample_bars()
        df.iloc[260, df.columns.get_loc("Open")] = 104
        df.iloc[260, df.columns.get_loc("High")] = 105
        result = research_symbol("FIXTURE", df, horizon=3, evaluator=known_signal)
        self.assertEqual(result["counters"]["next_open_OPENING_GAP_OVER_0_5_ATR"], 1)
        self.assertEqual(result["events"], [])

    def test_manifest_audit_detects_old_build_and_incomplete_venue(self):
        old = {
            "rulebook_build": "2026.10.02.03",
            "exchanges": {"gettex": {"rulebook_build": "2026.09.29.13",
                         "coverage_symbols": 2, "eligible_symbols": 3,
                         "remaining_symbols": 1, "updated_at": "2026-09-30T00:00:00Z"}},
        }
        fundamentals = {
            "exchanges": {"gettex": {"rulebook_build": "2026.09.29.13",
                         "coverage_symbols": 3, "universe_symbols": 3,
                         "remaining_symbols": 0, "completed_at": "2026-09-30T00:00:00Z"}},
        }
        with tempfile.TemporaryDirectory() as tmp:
            t, f = Path(tmp) / "t.json", Path(tmp) / "f.json"
            t.write_text(json.dumps(old))
            f.write_text(json.dumps(fundamentals))
            result = manifest_audit(t, f, pd.Timestamp("2026-10-23T09:45:00Z"))
            flags = result["venues"]["gettex"]["flags"]
            self.assertIn("TECH_INCOMPLETE", flags)
            self.assertIn("TECH_RULEBOOK_MISMATCH", flags)
            self.assertIn("TECH_OLD_MANIFEST", flags)
            self.assertIn("FUND_OLD_MANIFEST", flags)
            self.assertIn("FUND_RULEBOOK_MISMATCH", flags)


if __name__ == "__main__":
    unittest.main()
