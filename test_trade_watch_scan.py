"""Exercise the actual Trade scan orchestration without Streamlit or live feeds."""
import ast
import math
from pathlib import Path
from types import SimpleNamespace
from typing import Dict, List
from unittest import TestCase, main
from unittest.mock import Mock

import numpy as np
import pandas as pd

from trade_rules import FundamentalSnapshot, score_fundamental_snapshot, evaluate_price_setup


class WatchScanTests(TestCase):
    def scan(self, sector="Technology", missing=None):
        snapshot = FundamentalSnapshot(
            symbol="TEST", company="Test", sector=sector, industry="Software",
            currency="GBP", market_cap=2e9, roic=.2, roe=.25,
            operating_margin=.2, fcf_margin=.2, annual_fcf=[100]*3,
            annual_net_income=[100]*3, net_debt_to_fcf=.5,
            interest_coverage=20, no_interest_expense=False, current_ratio=2,
            revenue_growth=.15, earnings_growth=.25, operating_growth=.2,
            growth_source="TTM", share_change=-.02, distribution_ratio=.5,
            earnings_date=pd.Timestamp("2027-01-01"), earnings_source="TEST",
            trailing_pe=20, price_sales=5, missing_hard_inputs=missing or [],
        )
        frame = pd.DataFrame({"Close": [100.]*260, "Volume": [1e6]*260})
        namespace = dict(
            pd=pd, np=np, math=math, List=List, Dict=Dict,
            TRADE_MARKET_CONTEXT={"test": dict(currency="GBP", price_scale=1, benchmark="INDEX")},
            trade_fx_to_gbp=lambda: {"GBP": 1}, trade_benchmark_frame=lambda _: frame,
            yf=SimpleNamespace(download=lambda **_: frame, Ticker=lambda _: object()),
            extract_ticker_frame=lambda *_: frame,
            evaluate_price_setup=lambda *_: dict(technical_state="WATCH", technical_reason="PENDING MACD", rsi=29),
            build_fundamental_snapshot=Mock(return_value=snapshot),
            business_sessions_until=lambda _: 30,
            score_fundamental_snapshot=score_fundamental_snapshot,
        )
        source = Path(__file__).with_name("stock_app.py").read_text()
        function = next(node for node in ast.parse(source).body
                        if isinstance(node, ast.FunctionDef) and node.name == "approved_trade_market_scan")
        function.decorator_list = []
        exec(compile(ast.Module(body=[function], type_ignores=[]), "stock_app.py", "exec"), namespace)
        result = namespace["approved_trade_market_scan"](("TEST",), 0, "test", "TEST-BUILD")
        namespace["build_fundamental_snapshot"].assert_called_once()
        return result.iloc[0]

    def test_financial_and_real_estate_watch_setups_are_blocked(self):
        for sector in ("Financial Services", "Real Estate"):
            with self.subTest(sector=sector):
                row = self.scan(sector)
                self.assertEqual(row["Status"], "BLOCKED")
                self.assertIn("EXCLUDED SECTOR", row["Reason"])

    def test_missing_company_data_does_not_bypass_gate(self):
        row = self.scan(missing=["three annual FCF periods"])
        self.assertEqual(row["Status"], "BLOCKED")
        self.assertIn("FUNDAMENTAL DATA INCOMPLETE", row["Reason"])

    def test_watch_remains_subject_to_event_gate_and_labels_pending_score(self):
        row = self.scan()
        self.assertIn("FAIL-SAFE EVENT BLOCK", row["Reason"])
        self.assertEqual(row["Score status"], "PENDING CROSSOVER")
        self.assertEqual(row["Signal date"], "PENDING")
        self.assertGreaterEqual(row["Fundamental score"], 65)

    def test_falling_trend_cannot_be_watch(self):
        close = np.linspace(200, 100, 280)
        frame = pd.DataFrame(dict(Open=close, High=close+1, Low=close-1,
                                  Close=close, Volume=np.full(280, 1e6)))
        result = evaluate_price_setup(frame)
        self.assertEqual(result["technical_state"], "BLOCKED")


if __name__ == "__main__":
    main()
