"""Return-accounting tests use synthetic NAV curves, never published results."""
import unittest
from dataclasses import replace
from datetime import date, timedelta

from pension_return_accounting import (
    PensionNAV, BenchmarkTotalReturn, compare_verified_gbp_returns,
)


BASE = date(2025, 1, 1)


def navs(values, flows=None):
    flows = flows or [0] * len(values)
    return [
        PensionNAV(BASE+timedelta(days=i), closing_nav_gbp=v, external_flow_at_open_gbp=f)
        for i,(v,f) in enumerate(zip(values, flows))
    ]


def benchmark(values):
    return [
        BenchmarkTotalReturn(BASE+timedelta(days=i), gbp_total_return_index=v)
        for i,v in enumerate(values)
    ]


class ReturnAccountingTests(unittest.TestCase):
    def test_no_cashflows_matches_simple_compounding(self):
        result=compare_verified_gbp_returns(navs([100,110,121]),benchmark([100,105,110.25]))
        self.assertAlmostEqual(result.strategy_total_return_pct,21.0)
        self.assertAlmostEqual(result.benchmark_total_return_pct,10.25)
        self.assertAlmostEqual(result.strategy_max_drawdown_pct,0.0)
        self.assertEqual(result.trading_periods,2)

    def test_external_contribution_is_not_counted_as_gain(self):
        result=compare_verified_gbp_returns(
            navs([100,200,200],[0,100,0]),benchmark([100,100,100]))
        self.assertEqual(result.strategy_total_return_pct,0.0)
        self.assertEqual(result.strategy_cagr_pct,0.0)

    def test_loss_drawdown_and_recovery(self):
        result=compare_verified_gbp_returns(navs([100,70,100]),benchmark([100,95,98]))
        self.assertAlmostEqual(result.strategy_max_drawdown_pct,-30.0)
        self.assertAlmostEqual(result.strategy_total_return_pct,0.0)
        self.assertAlmostEqual(result.benchmark_max_drawdown_pct,-5.0)

    def test_benchmark_sessions_must_match(self):
        book=benchmark([100,101,102])
        book[1]=replace(book[1],day=BASE+timedelta(days=4))
        with self.assertRaisesRegex(ValueError,"increasing|matching"):
            compare_verified_gbp_returns(navs([100,100,100]),book)

    def test_portfolio_cannot_omit_costs_or_dividends(self):
        p=navs([100,110])
        p[1]=replace(p[1],includes_all_costs=False)
        with self.assertRaisesRegex(ValueError,"expenses"):
            compare_verified_gbp_returns(p,benchmark([100,100]))
        p[1]=replace(p[1],includes_all_costs=True,includes_all_dividends=False)
        with self.assertRaisesRegex(ValueError,"distributions"):
            compare_verified_gbp_returns(p,benchmark([100,100]))

    def test_no_unverified_benchmark_total_return(self):
        b=benchmark([100,105])
        b[1]=replace(b[1],verified_total_return=False)
        with self.assertRaisesRegex(ValueError,"verified"):
            compare_verified_gbp_returns(navs([100,110]),b)

    def test_bad_or_duplicate_dates_rejected(self):
        p=navs([100,110,120])
        p[1]=replace(p[1],day=p[0].day)
        with self.assertRaisesRegex(ValueError,"strictly increasing"):
            compare_verified_gbp_returns(p,benchmark([100,105,110]))

    def test_invalid_negative_cashflow_rejected(self):
        with self.assertRaisesRegex(ValueError,"negative opening capital"):
            compare_verified_gbp_returns(
                navs([100,100],[0,-101]),benchmark([100,100])
            )

    def test_initial_funding_is_not_a_midperiod_contribution(self):
        with self.assertRaisesRegex(ValueError,"first NAV"):
            compare_verified_gbp_returns(
                navs([100,110],[100,0]),benchmark([100,110])
            )

    def test_missing_values_rejected_not_reported_as_zero_return(self):
        with self.assertRaisesRegex(ValueError,"invalid"):
            compare_verified_gbp_returns(
                navs([100,float("nan")]),benchmark([100,110])
            )


if __name__=="__main__":
    unittest.main()
