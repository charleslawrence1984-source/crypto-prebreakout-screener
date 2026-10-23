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
    summarize_shadow_robustness, audit_history_window_consistency,
    _qualifying_shadow_plan_keys, _compare_candidate_route_traces,
    historical_signal_start,
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
    def test_stock_history_selector_keeps_entire_ten_year_horizon(self):
        now = pd.Timestamp("2026-10-14 12:30:00", tz="America/New_York")
        self.assertEqual(historical_signal_start(now, "10y"), pd.Timestamp("2016-10-14"))
        self.assertEqual(historical_signal_start(now, "5y"), pd.Timestamp("2021-10-14"))
        self.assertEqual(historical_signal_start(now, "3y"), pd.Timestamp("2023-10-14"))

    def test_history_selector_normalizes_naive_clock(self):
        now = pd.Timestamp("2026-10-14 23:50:00")
        self.assertEqual(historical_signal_start(now, "10y"), pd.Timestamp("2016-10-14"))
        self.assertEqual(historical_signal_start(now, "5y"), pd.Timestamp("2021-10-14"))

    def test_stock_history_selector_rejects_unsupported_labels(self):
        for period in ("1y", "0y", "10", "11y", "", None):
            with self.subTest(period=period):
                with self.assertRaises(ValueError):
                    historical_signal_start(pd.Timestamp("2026-10-14"), period)

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
        audit_trace = []
        _, diagnostics = signal_candidates(
            {"AAPL": extended}, new_benchmark,
            first_date=prices.index[-1], audit_trace=audit_trace,
        )
        row = diagnostics.iloc[0]
        gates = json.loads(row["Individual gate hits JSON"])
        self.assertGreater(int(row["Full-rule rejections"]), 0)
        self.assertIn("BREAKOUT: BREAKOUT VOLUME BELOW 1.35X", gates)
        self.assertTrue(audit_trace)
        self.assertEqual(audit_trace[-1]["Core state"], "BLOCKED")
        self.assertIn("BREAKOUT VOLUME BELOW 1.35X", audit_trace[-1]["Breakout reason"])
        self.assertIn(audit_trace[-1]["Breakout state"],
                      {"FUNDED BREAKOUT REVIEW", "FUNDED BREAKOUT PENDING OPEN"})

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
        self.assertTrue(
            alternatives["Exit reason"].eq("STOP (TARGET ALSO CROSSED)").all()
        )

    def test_shadow_plain_stop_does_not_claim_ambiguous_high(self):
        hist, details, dates = self._shadow_case()
        hist["AAPL"].loc[dates[2], ["High", "Low", "Close"]] = [106, 93, 100]
        result = simulate_shadow_breakout_stops(hist, details, holding_sessions=4)
        alternatives = result[result["Stop method"].str.contains("research")]
        self.assertTrue(alternatives["Exit reason"].eq("STOP").all())
        self.assertTrue(alternatives["Realized R"].eq(-1.0).all())

    def test_shadow_lost_resistance_distinguished_from_gap_chasing(self):
        hist, details, dates = self._shadow_case()
        hist["AAPL"].loc[dates[1], ["Open", "High", "Low", "Close"]] = [
            99.0, 100.0, 98.5, 99.0,
        ]
        result = simulate_shadow_breakout_stops(hist, details, holding_sessions=4)
        self.assertTrue(result["Status"].eq("ENTRY RESISTANCE LOST").all())

    def test_shadow_over_two_percent_above_resistance_distinguished(self):
        hist, details, dates = self._shadow_case()
        details.loc[0, "Resistance"] = 98.0
        result = simulate_shadow_breakout_stops(hist, details, holding_sessions=4)
        self.assertTrue(result["Status"].eq("ENTRY >2% ABOVE RESISTANCE").all())

    def test_portfolio_plain_stop_and_same_candle_ambiguity_are_distinguished(self):
        common = {
            "stop": 98.0, "target": 110.0, "half_cost": 0.0025,
            "max_hold": 20,
        }
        plain = _exit_on_bar(
            common, {"Open": 100, "High": 101, "Low": 97, "Close": 99},
            False, 1,
        )
        ambiguous = _exit_on_bar(
            common, {"Open": 100, "High": 112, "Low": 97, "Close": 104},
            False, 1,
        )
        self.assertEqual(plain[1], "STOP")
        self.assertEqual(ambiguous[1], "STOP (TARGET ALSO CROSSED)")
        self.assertEqual(plain[0], ambiguous[0])

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
        self.assertTrue(output["Status"].eq("ENTRY GAP >1% ABOVE SIGNAL CLOSE").all())
        self.assertEqual(
            summarize_shadow_breakout_stops(output)["Completed outcomes"].sum(), 0,
        )

    def test_shadow_matches_timezone_aware_daily_prices(self):
        hist, details, dates = self._shadow_case()
        hist["AAPL"].index = hist["AAPL"].index.tz_localize("America/New_York")
        hist["AAPL"].loc[
            hist["AAPL"].index[2], ["High", "Low", "Close"]
        ] = [121, 99, 120]
        output = simulate_shadow_breakout_stops(
            hist, details, holding_sessions=4,
        )
        self.assertEqual(len(output), 3)
        self.assertEqual(
            int(output["Status"].eq("COMPLETED").sum()), 2,
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

    def test_robustness_splits_historical_periods_and_keeps_costs_separate(self):
        data = pd.DataFrame([
            {
                "Entry date": "2018-06-04", "Stop method": "RESISTANCE RETEST (research)",
                "Status": "COMPLETED", "Exit reason": "TARGET", "Realized R": 2.5,
            },
            {
                "Entry date": "2021-06-04", "Stop method": "RESISTANCE RETEST (research)",
                "Status": "COMPLETED", "Exit reason": "STOP", "Realized R": -1.0,
            },
            {
                "Entry date": "2024-06-04", "Stop method": "RESISTANCE RETEST (research)",
                "Status": "OPEN / INCOMPLETE OUTCOME", "Exit reason": "", "Realized R": np.nan,
            },
            {
                "Entry date": "2025-06-04", "Stop method": "RESISTANCE RETEST (research)",
                "Status": "ENTRY GAP >1% ABOVE SIGNAL CLOSE",
                "Exit reason": "", "Realized R": np.nan,
            },
        ])
        stress = data.copy()
        stress.loc[0, "Status"] = "NET REWARD/RISK BELOW 2R"
        stress.loc[0, "Exit reason"] = ""
        stress.loc[0, "Realized R"] = np.nan
        results = summarize_shadow_robustness({
            "0.50%": data, "1.00%": stress,
        })
        baseline = results[(results["Cost scenario"] == "0.50%") &
                           (results["Historical cohort"] == "All observed years")].iloc[0]
        stressed = results[(results["Cost scenario"] == "1.00%") &
                           (results["Historical cohort"] == "All observed years")].iloc[0]
        self.assertEqual(int(baseline["Observed plans"]), 4)
        self.assertEqual(int(baseline["Passed next-open entry"]), 3)
        self.assertEqual(int(baseline["Completed trades"]), 2)
        self.assertEqual(int(baseline["Censored trades"]), 1)
        self.assertEqual(float(baseline["Win rate % (completed)"]), 50.0)
        self.assertAlmostEqual(float(baseline["Mean R (completed)"]), .75)
        self.assertEqual(int(stressed["Completed trades"]), 1)
        self.assertEqual(int(stressed["Entry rejections"]), 2)
        self.assertEqual(
            set(results.loc[results["Cost scenario"] == "0.50%", "Historical cohort"]),
            {"All observed years", "Before 2020", "2020–2022", "2023–2024", "2025 onward"},
        )

    def test_robustness_never_fabricates_win_rate_without_trades(self):
        only_skipped = pd.DataFrame([{
            "Entry date": "2026-10-12", "Stop method": "APPROVED ORIGINAL (control)",
            "Status": "ENTRY RESISTANCE LOST", "Exit reason": "",
            "Realized R": np.nan,
        }])
        results = summarize_shadow_robustness({"Stress": only_skipped})
        self.assertEqual(len(results), 2)
        self.assertTrue(results["Win rate % (completed)"].isna().all())
        self.assertTrue(
            results["Statistical confidence"].eq("NO EXECUTED PROXY TRADES").all()
        )
        self.assertTrue(summarize_shadow_robustness({}).empty)
        self.assertTrue(summarize_shadow_robustness({
            "Bad": pd.DataFrame({"junk": [1]})
        }).empty)

    def test_robustness_stops_remain_hypothetical_and_costs_rechecked(self):
        histories, details, dates = self._shadow_case()
        histories["AAPL"].loc[dates[2], ["High", "Low", "Close"]] = [
            121, 99, 120,
        ]
        baseline = simulate_shadow_breakout_stops(
            histories, details, holding_sessions=4, friction_pct=0.5,
        )
        stress = simulate_shadow_breakout_stops(
            histories, details, holding_sessions=4, friction_pct=1.0,
        )
        combined = summarize_shadow_robustness({
            "Base": baseline, "Stress": stress,
        })
        totals = combined[combined["Historical cohort"] == "All observed years"]
        self.assertEqual(len(totals), 6)
        for scenario in ("Base", "Stress"):
            retest = totals[
                (totals["Cost scenario"] == scenario) &
                (totals["Stop method"] == "RESISTANCE RETEST (research)")
            ]
            self.assertEqual(int(retest.iloc[0]["Observed plans"]), 1)
        self.assertTrue(
            totals["Statistical confidence"].str.contains(
                "INCONCLUSIVE|NO EXECUTED PROXY TRADES"
            ).all()
        )

    def test_qualifying_shadow_keys_respect_cohort_and_full_rules(self):
        raw = pd.DataFrame([
            {"Ticker": "MU", "Signal date": "2023-05-04",
             "Other gates failed": 0, "Technical score": 90,
             "R:R failed": True, "Stop-distance failed": False},
            {"Ticker": "MSFT", "Signal date": "2025-08-12",
             "Other gates failed": 2, "Technical score": 90,
             "R:R failed": True, "Stop-distance failed": True},
            {"Ticker": "TSM", "Signal date": "2025-09-18",
             "Other gates failed": 0, "Technical score": 95,
             "R:R failed": False, "Stop-distance failed": True},
            {"Ticker": "AAPL", "Signal date": "2025-12-08",
             "Other gates failed": 0, "Technical score": 70,
             "R:R failed": True, "Stop-distance failed": True},
        ])
        self.assertEqual(
            _qualifying_shadow_plan_keys(raw, pd.Timestamp("2024-01-01")),
            {("TSM", "2025-09-18")},
        )

    def test_ten_year_overlap_audit_reports_exact_coverage_and_candidate_dates(self):
        bars, benchmark = sample_bars()
        future_date = bars.index[-1] + pd.offsets.BDay(1)
        bar_next = bars.iloc[-1:].copy()
        bar_next.index = [future_date]
        bar_next["Open"] = bar_next["Close"]
        extended = pd.concat([bars, bar_next])

        old_days = pd.bdate_range(
            end=bars.index[0] - pd.offsets.BDay(1), periods=800,
        )
        close_values = np.linspace(50, 99.7, len(old_days))
        old_stock = pd.DataFrame({
            "Open": close_values, "High": close_values+0.5,
            "Low": close_values-0.5, "Close": close_values,
            "Volume": np.full(len(old_days), 900000.0),
        }, index=old_days)
        old_benchmark = pd.DataFrame(
            {"Close": np.linspace(4400, 4899, len(old_days))},
            index=old_days,
        )
        full_stock = pd.concat([old_stock, extended])
        full_market = pd.concat([
            old_benchmark, benchmark,
            pd.DataFrame(
                {"Close": [benchmark["Close"].iloc[-1]]},
                index=[future_date],
            ),
        ])
        signals, full_diagnostics = signal_candidates(
            {"AAPL": full_stock}, full_market,
            first_date=pd.Timestamp("2023-01-01"),
        )
        audit = audit_history_window_consistency(
            {"AAPL": full_stock}, full_market, full_diagnostics,
            last_five_years_start=pd.Timestamp("2025-01-01"),
        )
        self.assertEqual(len(audit["coverage"]), 1)
        self.assertEqual(
            audit["coverage"].iloc[0]["Full download bars"],
            len(full_stock),
        )
        self.assertEqual(
            audit["coverage"].iloc[0]["Actual first daily bar"],
            str(old_days[0].date()),
        )
        self.assertGreater(
            audit["coverage"].iloc[0]["Bars in last 5y crop"], 252
        )
        self.assertTrue(audit["disagreements"].empty)
        self.assertEqual(
            audit["summary"].loc[
                audit["summary"]["Check"] == "Eligible setup date disagreements",
                "Value",
            ].iloc[0], 0,
        )

    def test_history_overlap_incomplete_prices_do_not_claim_valid_5yr(self):
        dates = pd.bdate_range("2026-01-01", periods=260)
        vals = np.linspace(100, 102, len(dates))
        bars = pd.DataFrame({
            "Open": vals, "High": vals + 0.2, "Low": vals - 0.2,
            "Close": vals, "Volume": np.full(len(vals), 3000000),
        }, index=dates)
        benchmark = bars[["Close"]].copy()
        diag = pd.DataFrame([{
            "Ticker": "AAPL", "Status": "SCANNED",
            "Candidate sessions": 0,
            "Breakout plan observations JSON": "[]",
        }])
        audit = audit_history_window_consistency(
            {"AAPL": bars}, benchmark, diag,
            last_five_years_start=pd.Timestamp("2021-10-01"),
        )
        self.assertEqual(
            audit["coverage"].iloc[0]["Actual first daily bar"],
            "2026-01-01",
        )
        self.assertEqual(
            audit["summary"].loc[
                audit["summary"]["Check"] ==
                "Earliest historical daily candle available", "Value",
            ].iloc[0],
            "2026-01-01",
        )
        self.assertTrue(audit["disagreements"].empty)

    def test_route_trace_discriminates_prefilter_and_core_priority(self):
        full = [
            {"Ticker": "AAPL", "Signal date": "2025-01-06",
             "Breakout trigger": True, "Core trigger": False,
             "Core state": "BLOCKED"},
            {"Ticker": "MU", "Signal date": "2025-01-07",
             "Breakout trigger": True, "Core trigger": False,
             "Core state": "WATCH"},
            {"Ticker": "TSM", "Signal date": "2025-01-08",
             "Breakout trigger": True, "Core trigger": False,
             "Core state": "BLOCKED"},
        ]
        shorter = [
            {**full[0], "Core state": "WATCH"},
            {**full[1], "Core state": "BLOCKED"},
            {"Ticker": "NVDA", "Signal date": "2025-01-09",
             "Breakout trigger": True, "Core trigger": False,
             "Core state": "BLOCKED"},
        ]
        comparison = _compare_candidate_route_traces(
            full, shorter, "2023-01-01",
        )
        counts = dict(zip(
            comparison["counts"]["Check"], comparison["counts"]["Value"],
        ))
        self.assertEqual(counts["Preliminary candidate sessions — full history"], 3)
        self.assertEqual(counts["Preliminary candidate sessions — cropped history"], 3)
        self.assertEqual(counts["Breakout triggers — full history"], 3)
        self.assertEqual(counts["Breakouts reaching breakout engine — full history"], 2)
        self.assertEqual(counts["Breakouts reaching breakout engine — cropped history"], 2)
        self.assertEqual(counts["Prefilter trigger presence disagreements"], 2)
        self.assertEqual(counts["Core route state disagreements"], 2)
        self.assertEqual(len(comparison["differences"]), 4)

    def test_route_trace_identical_sessions_have_no_mismatches(self):
        event = {"Ticker": "AAPL", "Signal date": "2025-05-15",
                 "Breakout trigger": True, "Core trigger": False,
                 "Core state": "BLOCKED"}
        result = _compare_candidate_route_traces(
            [event], [dict(event)], "2024-01-01",
        )
        self.assertTrue(result["differences"].empty)
        counts = dict(zip(result["counts"]["Check"], result["counts"]["Value"]))
        self.assertEqual(counts["Breakout triggers — full history"], 1)
        self.assertEqual(counts["Core route state disagreements"], 0)

    def test_candidate_trace_instrumentation_never_changes_signals(self):
        bars, benchmark = sample_bars()
        future_date = bars.index[-1] + pd.offsets.BDay(1)
        next_bar = bars.iloc[-1:].copy()
        next_bar.index = [future_date]
        histories = {"AAPL": pd.concat([bars, next_bar])}
        full_signals, full_diagnostics = signal_candidates(
            histories, benchmark,
        )
        trace = []
        traced_signals, traced_diagnostics = signal_candidates(
            histories, benchmark, audit_trace=trace,
        )
        self.assertEqual(full_signals, traced_signals)
        pd.testing.assert_frame_equal(full_diagnostics, traced_diagnostics)
        self.assertTrue(trace)
        self.assertTrue(all("Core state" in row for row in trace))

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
