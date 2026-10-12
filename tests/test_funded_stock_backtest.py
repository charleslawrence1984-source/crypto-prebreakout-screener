"""Funded stock-only backtest: no-lookahead and adverse-execution contracts."""
import unittest

import numpy as np
import pandas as pd

from funded_stock_backtest import (
    _entry_plan, _exit_on_bar, signal_candidates, simulate_portfolio,
    summarize_backtest_funnel,
)
from test_funded_stock_breakout import sample_bars


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
