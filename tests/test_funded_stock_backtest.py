"""Funded stock-only backtest: no-lookahead and adverse-execution contracts."""
import unittest
import json

import numpy as np
import pandas as pd

from funded_stock_backtest import (
    _entry_plan, _exit_on_bar, signal_candidates, simulate_portfolio,
    summarize_backtest_funnel, summarize_individual_rule_gates,
    breakout_plan_research, summarize_breakout_plan_research,
    simulate_shadow_breakout_stops, summarize_shadow_breakout_stops,
)
from test_funded_stock_breakout import sample_bars
from funded_stock_breakout import evaluate_funded_breakout


def signal(dates, ticker="AAPL"):
    return {
        "Ticker": ticker, "Route": "FUNDED BREAKOUT",
        "Signal date": dates[0], "Entry date": dates[1],
        "Score": 90,
        "Plan": {
            "reference_entry": 100.0, "reference_stop": 98.0,
            "reference_target": 110.0, "resistance": 99.0,
        },
    }


def make_prices(second=None, third=None):
    dates = pd.bdate_range("2026-09-01", periods=4)
    items = [
        [100, 101, 99, 100],
        [100, 101, 99, 100],  # enters here at the open
        second or [100, 102, 99, 101],
        third or [100, 103, 99, 102],
    ]
    frame = pd.DataFrame(items, columns=["Open", "High", "Low", "Close"], index=dates)
    frame["Volume"] = 2000000
    return frame


class FundedStockBacktestTests(unittest.TestCase):
    def test_next_open_not_signal_close_is_entry(self):
        bars, benchmark = sample_bars()
        future_date = bars.index[-1] + pd.offsets.BDay(1)
        future = pd.DataFrame({
            "Open": [float(bars["Close"].iloc[-1]) + 0.2],
            "High": [float(bars["Close"].iloc[-1]) + 1],
            "Low": [float(bars["Close"].iloc[-1]) - 0.5],
            "Close": [float(bars["Close"].iloc[-1]) + 0.3],
            "Volume": [950000],
        }, index=[future_date])
        prices = pd.concat([bars, future])
        market = pd.concat([benchmark, pd.DataFrame({"Close": [benchmark["Close"].iloc[-1]]}, index=[future_date])])
        found, diagnostics = signal_candidates(
            {"AAPL": prices}, market, first_date=bars.index[-1]
        )
        self.assertTrue(found, "Synthetic qualifying breakout must be backtestable")
        self.assertEqual(found[0]["Signal date"], bars.index[-1])
        self.assertEqual(found[0]["Entry date"], future_date)
        self.assertEqual(found[0]["Route"], "FUNDED BREAKOUT")

    def test_future_bar_change_does_not_change_past_signal(self):
        bars, benchmark = sample_bars()
        future_date = bars.index[-1] + pd.offsets.BDay(1)
        after = pd.DataFrame({
            "Open": [164.0], "High": [165.0], "Low": [163.0],
            "Close": [164.0], "Volume": [1500000],
        }, index=[future_date])
        prices = pd.concat([bars, after])
        market = pd.concat([benchmark, pd.DataFrame({"Close": [benchmark["Close"].iloc[-1]]}, index=[future_date])])
        one, _ = signal_candidates({"AAPL": prices}, market, first_date=bars.index[-1])
        changed = prices.copy()
        changed.loc[future_date, ["Open", "High", "Low", "Close"]] = [200, 220, 160, 210]
        two, _ = signal_candidates({"AAPL": changed}, market, first_date=bars.index[-1])
        self.assertEqual(len(one), len(two))
        self.assertEqual(one[0]["Plan"], two[0]["Plan"])

    def test_watch_only_does_not_generate_entry(self):
        prices = make_prices()
        market = prices[["Close"]]
        signals, diag = signal_candidates({"AAPL": prices}, market)
        self.assertEqual(len(signals), 0)

    def test_valid_breakout_open_plan_reserves_cost(self):
        dates = make_prices().index
        plan, verdict = _entry_plan(signal(dates), 100.0, 0.5)
        self.assertEqual(verdict, "PASS")
        self.assertGreater(plan["entry_ask"], 100.0)
        self.assertLess(plan["stop_bid"], 98.0)
        self.assertLess(plan["target_bid"], 110.0)

    def test_gap_chase_rejected_before_order(self):
        dates = make_prices().index
        plan, verdict = _entry_plan(signal(dates), 105.0, 0.5)
        self.assertIsNone(plan)
        self.assertIn("TOO LATE", verdict)

    def test_same_bar_stop_and_target_is_loss(self):
        prices = make_prices()
        dates = prices.index
        trade = simulate_portfolio(
            {"AAPL": make_prices(second=[100, 112, 96, 106])}, [signal(dates)],
            roundtrip_cost_pct=0.5, max_hold_sessions=20
        )
        self.assertEqual(len(trade["trades"]), 1)
        row = trade["trades"].iloc[0]
        self.assertTrue(row["Exit reason"].startswith("STOP"))
        self.assertLess(row["P&L $"], 0)

    def test_downward_gap_can_breach_stop_and_fail_challenge(self):
        prices = make_prices(second=[60, 70, 50, 63])
        dates = prices.index
        out = simulate_portfolio(
            {"AAPL": prices}, [signal(dates)],
            roundtrip_cost_pct=0.5, max_hold_sessions=20
        )
        self.assertEqual(out["trades"].iloc[0]["Exit reason"], "STOP GAP")
        self.assertTrue(out["metrics"]["Touched $970 fail line"])
        self.assertLess(out["metrics"]["Ending marked equity $"], 970.0)

    def test_no_automatic_profit_from_open_censored_trade(self):
        prices = make_prices(second=[100, 101, 99, 100])
        dates = prices.index
        result = simulate_portfolio(
            {"AAPL": prices}, [signal(dates)],
            roundtrip_cost_pct=0.5, max_hold_sessions=20
        )
        self.assertEqual(result["metrics"]["Completed trades"], 0)
        self.assertEqual(result["metrics"]["Open / censored"], 1)

    def test_holding_limit_closes_at_proxy_bid(self):
        prices = make_prices()
        result = simulate_portfolio(
            {"AAPL": prices}, [signal(prices.index)],
            roundtrip_cost_pct=1.0, max_hold_sessions=2
        )
        self.assertEqual(result["trades"].iloc[0]["Exit reason"], "TIME EXIT")
        self.assertLess(result["trades"].iloc[0]["Exit bid proxy"], 101.0)

    def test_portfolio_risk_is_bounded_before_gap(self):
        dates = make_prices().index
        histories = {s: make_prices() for s in ("AAPL", "MSFT", "NVDA", "GOOGL")}
        events = [signal(dates, s) for s in histories]
        result = simulate_portfolio(histories, events)
        self.assertEqual(result["metrics"]["Candidates"], 4)
        self.assertLessEqual(result["equity_curve"]["Open positions"].max(), 4)
        # $10 open-risk budget and <=35% notional per symbol.
        open_rows = result["unclosed"]
        self.assertLessEqual(open_rows["Modeled initial risk $"].sum(), 10.001)
        self.assertTrue((open_rows["Notional $"] <= 350.001).all())

    def test_stress_friction_must_not_improve_net_return_when_exits_equal(self):
        prices = make_prices(second=[100, 112, 99, 110])
        dates = prices.index
        low = simulate_portfolio({"AAPL": prices}, [signal(dates)], roundtrip_cost_pct=0.5)
        high = simulate_portfolio({"AAPL": prices}, [signal(dates)], roundtrip_cost_pct=1.0)
        self.assertLessEqual(high["metrics"]["Realized P&L $"], low["metrics"]["Realized P&L $"])

    def test_zero_trade_summary_names_exact_skipped_candidate(self):
        dates = make_prices().index
        prices = make_prices()
        # A realistic +5% gap is within the broad target, but beyond
        # our explicit anti-chasing 1% opening-price rule.
        prices.loc[dates[1], ["Open", "High", "Low", "Close"]] = [105, 106, 104, 105]
        result = simulate_portfolio(
            {"AAPL": prices}, [signal(dates)], roundtrip_cost_pct=0.5,
        )
        diagnostics = pd.DataFrame([{
            "Ticker": "AAPL", "Status": "SCANNED",
            "Candidate sessions": 12, "Core trigger sessions": 8,
            "Breakout trigger sessions": 4,
            "Full-rule rejections": 11,
            "Liquidity rejections": 0,
            "Qualified core signals": 0,
            "Qualified breakout signals": 1,
            "Top rule blocker": "BREAKOUT: LOW VOLUME",
        }])
        summary = summarize_backtest_funnel(diagnostics, result)
        self.assertEqual(summary["Completed trades"], 0)
        self.assertEqual(summary["Core trigger sessions"], 8)
        self.assertEqual(summary["Breakout trigger sessions"], 4)
        self.assertEqual(summary["Full-rule rejections"], 11)
        self.assertEqual(summary["Qualified breakout signals"], 1)
        self.assertIn("AAPL", summary["Skipped candidate detail"])
        self.assertIn("TOO LATE", summary["Most common skipped entry reason"])
        self.assertFalse(summary["Statistically evaluable"])

    def test_individual_gate_counts_are_not_confused_with_stock_modal_labels(self):
        diagnostic = pd.DataFrame([
            {
                "Ticker": "AAPL", "Candidate sessions": 12,
                "Individual gate hits JSON": json.dumps({
                    "BREAKOUT: RESISTANCE NOT TESTED TWICE": 8,
                    "BREAKOUT: PROJECTED R:R BELOW 2": 7,
                    "CORE: MOVING AVERAGES NOT RISING": 4,
                }),
            },
            {
                "Ticker": "MSFT", "Candidate sessions": 8,
                "Individual gate hits JSON": json.dumps({
                    "BREAKOUT: PROJECTED R:R BELOW 2": 6,
                    "CORE: MOVING AVERAGES NOT RISING": 3,
                }),
            },
        ])
        summary = summarize_individual_rule_gates(diagnostic)
        self.assertEqual(
            summary.iloc[0]["Failed condition"],
            "PROJECTED R:R BELOW 2",
        )
        self.assertEqual(int(summary.iloc[0]["Signal sessions affected"]), 13)
        self.assertEqual(summary.iloc[0]["% of all candidate sessions"], 65)
        self.assertEqual(int(summary.iloc[0]["Stocks affected"]), 2)
        # Multiple vetoes on the SAME breakout may coexist.
        self.assertGreater(summary["Signal sessions affected"].sum(), 20)

    def test_invalid_and_missing_gate_json_does_not_fabricate_counts(self):
        diagnostics = pd.DataFrame([
            {"Ticker": "AAPL", "Candidate sessions": 7,
             "Individual gate hits JSON": "not JSON"},
            {"Ticker": "NVDA", "Candidate sessions": 9,
             "Individual gate hits JSON": "{}"},
        ])
        self.assertTrue(summarize_individual_rule_gates(diagnostics).empty)
        self.assertTrue(summarize_individual_rule_gates(pd.DataFrame()).empty)

    def test_breakout_rejection_records_each_individual_gate(self):
        prices, benchmark = sample_bars()
        prices = prices.copy()
        prices.loc[prices.index[-1], "Volume"] = 1.0
        future = prices.iloc[-1:].copy()
        future.index = [prices.index[-1] + pd.offsets.BDay(1)]
        future["Close"] = future["Open"]
        extended = pd.concat([prices, future])
        new_benchmark = pd.concat([
            benchmark, pd.DataFrame(
                {"Close": [benchmark["Close"].iloc[-1]]},
                index=future.index,
            ),
        ])
        _, diagnostics = signal_candidates(
            {"AAPL": extended}, new_benchmark,
            first_date=prices.index[-1],
        )
        row = diagnostics.iloc[0]
        gates = json.loads(row["Individual gate hits JSON"])
        self.assertGreater(int(row["Full-rule rejections"]), 0)
        self.assertIn("BREAKOUT: BREAKOUT VOLUME BELOW 1.35X", gates)

    def test_breakout_plan_diagnostics_separate_only_vetoes_from_overlap(self):
        details = pd.DataFrame([
            {"R:R failed": True, "Stop-distance failed": False,
             "Other gates failed": 0, "Projected stop %": 4.0,
             "Projected R:R": 1.8, "Projected upside %": 6.0},
            {"R:R failed": False, "Stop-distance failed": True,
             "Other gates failed": 0, "Projected stop %": 12.0,
             "Projected R:R": 2.6, "Projected upside %": 20.0},
            {"R:R failed": True, "Stop-distance failed": True,
             "Other gates failed": 0, "Projected stop %": 14.0,
             "Projected R:R": 0.9, "Projected upside %": 8.0},
            {"R:R failed": True, "Stop-distance failed": True,
             "Other gates failed": 3, "Projected stop %": 16.0,
             "Projected R:R": 0.6, "Projected upside %": 9.0},
            {"R:R failed": False, "Stop-distance failed": True,
             "Other gates failed": 2, "Projected stop %": 0.2,
             "Projected R:R": 2.5, "Projected upside %": 4.0},
        ])
        result = summarize_breakout_plan_research(details)
        lookup = dict(zip(result["Measure"], result["Value"]))
        self.assertEqual(lookup["Breakout plans evaluated"], 5)
        self.assertEqual(lookup["R:R failed with no other gates failing"], 1)
        self.assertEqual(lookup["Stop-distance failed with no other gates failing"], 1)
        self.assertEqual(lookup["Only R:R and/or stop-distance failed"], 3)
        self.assertEqual(lookup["R:R failed alongside additional non-plan gates"], 1)
        self.assertEqual(lookup["Stop wider than 8%"], 3)
        self.assertEqual(lookup["Stop tighter than 0.4%"], 1)

    def test_json_breakout_plan_export_parses_without_future_data(self):
        prices, benchmark = sample_bars()
        extended = prices.copy()
        next_session = prices.index[-1] + pd.offsets.BDay(1)
        future = prices.iloc[-1:].copy()
        future.index = [next_session]
        future["Close"] = future["Open"]
        extended = pd.concat([extended, future])
        extended_benchmark = pd.concat([
            benchmark,
            pd.DataFrame({"Close": [benchmark["Close"].iloc[-1]]}, index=[next_session]),
        ])
        _, diagnostics = signal_candidates(
            {"AAPL": extended}, extended_benchmark,
            first_date=prices.index[-1],
        )
        detail = breakout_plan_research(diagnostics)
        self.assertFalse(detail.empty)
        self.assertEqual(detail.iloc[0]["Signal date"], str(prices.index[-1].date()))
        self.assertIn(detail.iloc[0]["Technical state"], {
            "FUNDED BREAKOUT PENDING OPEN", "FUNDED BREAKOUT REVIEW",
        })
        self.assertTrue(
            pd.notna(detail.iloc[0]["Projected stop %"])
        )
        # Forward bar changes cannot alter a completed signal's plan.
        modified = extended.copy()
        modified.loc[next_session, ["Open", "High", "Low", "Close"]] = [
            999.0, 1000.0, 998.0, 999.0,
        ]
        _, changed = signal_candidates(
            {"AAPL": modified}, extended_benchmark,
            first_date=prices.index[-1],
        )
        self.assertEqual(
            breakout_plan_research(changed).to_dict("records"),
            detail.to_dict("records"),
        )

    def test_missing_breakout_plan_json_is_fail_closed(self):
        data = pd.DataFrame([
            {"Ticker": "NVDA", "Breakout plan observations JSON": "not valid JSON"},
        ])
        self.assertTrue(breakout_plan_research(data).empty)
        self.assertTrue(summarize_breakout_plan_research(pd.DataFrame()).empty)

    @staticmethod
    def _shadow_case():
        dates = pd.bdate_range("2025-09-01", periods=5)
        market = pd.DataFrame({
            "Open": [100.0] * 5, "High": [101.0] * 5,
            "Low": [99.0] * 5, "Close": [100.0] * 5,
            "Volume": [2000000] * 5,
        }, index=dates)
        details = pd.DataFrame([{
            "Ticker": "AAPL", "Signal date": str(dates[0].date()),
            "Projected R:R": 0.8, "Technical score": 100.0,
            "Other gates failed": 0, "R:R failed": True,
            "Stop-distance failed": True,
            "Reference entry": 100.0, "Reference stop": 88.0,
            "Reference target": 120.0, "Resistance": 99.6,
            "Support stop": 94.0, "Retest stop": 98.0,
        }])
        return {"AAPL": market}, details, dates

    def test_shadow_research_keeps_original_rejected_and_alternates_eligible(self):
        hist, details, dates = self._shadow_case()
        hist["AAPL"].loc[dates[2], ["High", "Low", "Close"]] = [121, 99, 120]
        output = simulate_shadow_breakout_stops(
            hist, details, holding_sessions=4, friction_pct=0.5,
        )
        self.assertEqual(len(output), 3)
        original = output[output["Stop method"].str.startswith("APPROVED")].iloc[0]
        self.assertEqual(original["Status"], "STOP OUTSIDE APPROVED 0.4–8%")
        alternates = output[output["Stop method"].str.contains("research")]
        self.assertEqual(len(alternates), 2)
        self.assertTrue(alternates["Status"].eq("COMPLETED").all())
        self.assertTrue(alternates["Exit reason"].eq("TARGET").all())
        self.assertTrue((alternates["Realized R"] > 2).all())
        summary = summarize_shadow_breakout_stops(output)
        self.assertEqual(summary["Completed outcomes"].sum(), 2)
        self.assertEqual(summary["Targets hit"].sum(), 2)

    def test_shadow_assumes_stop_before_target_when_both_hit(self):
        hist, details, dates = self._shadow_case()
        hist["AAPL"].loc[dates[2], ["High", "Low", "Close"]] = [121, 93, 106]
        output = simulate_shadow_breakout_stops(hist, details, holding_sessions=4)
        alternatives = output[output["Stop method"].str.contains("research")]
        self.assertTrue(alternatives["Status"].eq("COMPLETED").all())
        self.assertTrue(
            alternatives["Exit reason"].str.startswith("STOP").all()
        )
        self.assertTrue((alternatives["Realized R"] < 0).all())

    def test_shadow_censors_when_full_forward_horizon_not_observed(self):
        hist, details, dates = self._shadow_case()
        output = simulate_shadow_breakout_stops(hist, details, holding_sessions=20)
        alternatives = output[output["Stop method"].str.contains("research")]
        self.assertTrue(
            alternatives["Status"].eq("OPEN / INCOMPLETE OUTCOME").all()
        )
        self.assertTrue(alternatives["Realized R"].isna().all())
        grouped = summarize_shadow_breakout_stops(output)
        self.assertEqual(grouped["Completed outcomes"].sum(), 0)
        self.assertEqual(grouped["Censored open"].sum(), 2)

    def test_shadow_rejects_next_open_gap_even_for_better_stops(self):
        hist, details, dates = self._shadow_case()
        hist["AAPL"].loc[dates[1], ["Open", "High", "Low", "Close"]] = [
            105, 106, 104, 105,
        ]
        output = simulate_shadow_breakout_stops(hist, details, holding_sessions=4)
        self.assertTrue(output["Status"].eq("ENTRY GAP OR RESISTANCE LOST").all())
        self.assertEqual(
            summarize_shadow_breakout_stops(output)["Completed outcomes"].sum(), 0,
        )

    def test_shadow_rejects_nonplan_gate_failures_and_bad_assumptions(self):
        hist, details, dates = self._shadow_case()
        details.loc[0, "Other gates failed"] = 1
        self.assertTrue(simulate_shadow_breakout_stops(hist, details).empty)
        details.loc[0, "Other gates failed"] = 0
        details.loc[0, "Technical score"] = 50
        self.assertTrue(simulate_shadow_breakout_stops(hist, details).empty)
        details.loc[0, "Technical score"] = 100
        with self.assertRaises(ValueError):
            simulate_shadow_breakout_stops(hist, details, friction_pct=0)
        with self.assertRaises(ValueError):
            simulate_shadow_breakout_stops(hist, details, holding_sessions=1)

    def test_shadow_stops_only_use_completed_signal_bar_levels(self):
        bars, benchmark = sample_bars()
        result = evaluate_funded_breakout(bars, benchmark)
        self.assertIn("research_stop_support", result)
        self.assertIn("research_stop_retest", result)
        self.assertAlmostEqual(
            result["reference_stop"],
            round(min(
                result["research_stop_support"], result["research_stop_retest"],
            ), 4), places=4,
        )

    def test_zero_candidates_summary_stays_honest(self):
        result = simulate_portfolio({"AAPL": make_prices()}, [])
        funnel = summarize_backtest_funnel(pd.DataFrame(), result)
        self.assertEqual(funnel["Completed trades"], 0)
        self.assertEqual(funnel["Skipped candidate detail"], "")
        self.assertFalse(funnel["Statistically evaluable"])

    def test_no_invented_result_when_no_signals(self):
        prices = make_prices()
        result = simulate_portfolio({"AAPL": prices}, [])
        self.assertEqual(result["metrics"]["Completed trades"], 0)
        self.assertTrue(np.isnan(result["metrics"]["Win rate %"]))
        self.assertEqual(result["metrics"]["Candidates"], 0)
        self.assertEqual(result["metrics"]["Realized P&L $"], 0.0)


if __name__ == "__main__":
    unittest.main()
