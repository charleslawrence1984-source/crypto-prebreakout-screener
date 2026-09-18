import unittest

import numpy as np
import pandas as pd

from trade_rules import (
    FundamentalSnapshot,
    build_fundamental_snapshot,
    business_sessions_until,
    evaluate_price_setup,
    market_regime_score,
    score_fundamental_snapshot,
)


class TradeRulesTests(unittest.TestCase):
    def test_snapshot_survives_individual_provider_endpoint_failures(self):
        class PartialTicker:
            fast_info = {"currency": "GBP", "market_cap": 2_000_000_000}

            def __getattr__(self, _name):
                raise RuntimeError("provider endpoint unavailable")

            @property
            def info(self):
                raise RuntimeError("quote metadata unavailable")

        snapshot = build_fundamental_snapshot("TEST.L", PartialTicker())
        self.assertEqual(snapshot.currency, "GBP")
        self.assertEqual(snapshot.market_cap, 2_000_000_000)
        self.assertEqual(snapshot.sector, "UNAVAILABLE")
        self.assertIn("three annual FCF periods", snapshot.missing_hard_inputs)

    def test_price_history_requires_252_sessions(self):
        index = pd.bdate_range("2025-01-01", periods=251)
        frame = pd.DataFrame(
            {
                "Open": 100.0,
                "High": 101.0,
                "Low": 99.0,
                "Close": 100.0,
                "Volume": 1_000_000.0,
            },
            index=index,
        )
        result = evaluate_price_setup(frame)
        self.assertEqual(result["technical_state"], "BLOCKED")
        self.assertIn("251/252", result["technical_reason"])

    def test_market_regime_uses_three_states(self):
        index = pd.bdate_range("2024-01-01", periods=260)
        close = np.linspace(100.0, 140.0, len(index))
        frame = pd.DataFrame({"Close": close}, index=index)
        score, state = market_regime_score(frame)
        self.assertEqual(score, 15.0)
        self.assertEqual(state, "BULL")

    def test_business_session_count_excludes_start_date(self):
        self.assertEqual(
            business_sessions_until(pd.Timestamp("2026-09-25"), pd.Timestamp("2026-09-18")),
            5,
        )

    def test_unverified_event_check_is_a_hard_block(self):
        snapshot = FundamentalSnapshot(
            symbol="TEST",
            company="Test Company",
            sector="Technology",
            industry="Software",
            currency="GBP",
            market_cap=2_000_000_000,
            roic=0.20,
            roe=0.25,
            operating_margin=0.20,
            fcf_margin=0.20,
            annual_fcf=[200, 180, 160, 140, 120],
            annual_net_income=[180, 160, 140],
            net_debt_to_fcf=0.5,
            interest_coverage=20,
            no_interest_expense=False,
            current_ratio=2.0,
            revenue_growth=0.15,
            earnings_growth=0.25,
            operating_growth=0.20,
            growth_source="TTM",
            share_change=-0.02,
            distribution_ratio=0.5,
            earnings_date=pd.Timestamp("2026-10-30"),
            earnings_source="PROVIDER CALENDAR",
            trailing_pe=20,
            price_sales=5,
            missing_hard_inputs=[],
        )
        result = score_fundamental_snapshot(
            snapshot, [snapshot], gbp_rate=1.0, earnings_sessions=30,
            official_event_verified=False,
        )
        self.assertGreaterEqual(result["fundamental_score"], 65)
        self.assertIn(
            "FAIL-SAFE EVENT BLOCK — OFFICIAL CHECK UNVERIFIED",
            result["fundamental_failures"],
        )

    def test_event_gate_can_be_deferred_until_candidate_stage(self):
        snapshot = FundamentalSnapshot(
            symbol="TEST",
            company="Test Company",
            sector="Technology",
            industry="Software",
            currency="GBP",
            market_cap=2_000_000_000,
            roic=0.20,
            roe=0.25,
            operating_margin=0.20,
            fcf_margin=0.20,
            annual_fcf=[200, 180, 160],
            annual_net_income=[180, 160, 140],
            net_debt_to_fcf=0.5,
            interest_coverage=20,
            no_interest_expense=False,
            current_ratio=2.0,
            revenue_growth=0.15,
            earnings_growth=0.25,
            operating_growth=0.20,
            growth_source="TTM",
            share_change=0.0,
            distribution_ratio=0.5,
            earnings_date=None,
            earnings_source="UNVERIFIED",
            trailing_pe=20,
            price_sales=5,
            missing_hard_inputs=[],
        )
        result = score_fundamental_snapshot(
            snapshot, [snapshot], gbp_rate=1.0, earnings_sessions=None,
            official_event_verified=False, apply_event_gate=False,
        )
        self.assertNotIn("EARNINGS DATE UNVERIFIED", result["fundamental_failures"])
        self.assertNotIn(
            "FAIL-SAFE EVENT BLOCK — OFFICIAL CHECK UNVERIFIED",
            result["fundamental_failures"],
        )

    def test_financial_sector_is_excluded(self):
        snapshot = FundamentalSnapshot(
            symbol="BANK",
            company="Bank",
            sector="Financial Services",
            industry="Banks",
            currency="GBP",
            market_cap=2_000_000_000,
            roic=0.2,
            roe=0.25,
            operating_margin=0.2,
            fcf_margin=0.2,
            annual_fcf=[10, 10, 10],
            annual_net_income=[10, 10, 10],
            net_debt_to_fcf=0.5,
            interest_coverage=20,
            no_interest_expense=False,
            current_ratio=2,
            revenue_growth=0.15,
            earnings_growth=0.25,
            operating_growth=0.2,
            growth_source="TTM",
            share_change=0,
            distribution_ratio=0.5,
            earnings_date=pd.Timestamp("2026-10-30"),
            earnings_source="PROVIDER CALENDAR",
            trailing_pe=10,
            price_sales=2,
            missing_hard_inputs=[],
        )
        result = score_fundamental_snapshot(snapshot, [snapshot], 1.0, 30, True)
        self.assertIn("EXCLUDED SECTOR", result["fundamental_failures"])

    def test_tradingview_finance_sector_alias_is_excluded(self):
        snapshot = FundamentalSnapshot(
            symbol="BANK2",
            company="Bank 2",
            sector="Finance",
            industry="Banks",
            currency="USD",
            market_cap=2_000_000_000,
            roic=0.2,
            roe=0.25,
            operating_margin=0.2,
            fcf_margin=0.2,
            annual_fcf=[10, 10, 10],
            annual_net_income=[10, 10, 10],
            net_debt_to_fcf=0.5,
            interest_coverage=20,
            no_interest_expense=False,
            current_ratio=2,
            revenue_growth=0.15,
            earnings_growth=0.25,
            operating_growth=0.2,
            growth_source="TRADINGVIEW",
            share_change=0,
            distribution_ratio=0.5,
            earnings_date=pd.Timestamp("2026-10-30"),
            earnings_source="TRADINGVIEW CALENDAR",
            trailing_pe=10,
            price_sales=2,
            missing_hard_inputs=[],
        )
        result = score_fundamental_snapshot(
            snapshot, [snapshot], 1.0, 30, True, apply_event_gate=False
        )
        self.assertIn("EXCLUDED SECTOR", result["fundamental_failures"])


if __name__ == "__main__":
    unittest.main()
