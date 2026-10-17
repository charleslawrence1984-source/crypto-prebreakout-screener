"""Regression for the 'float object is not callable' Investment backfill bug.

The Stocks Streamlit page is executed at import time. Assigning a numeric
display value to module-global `rsi` overwrites the RSI calculation function,
so later calls from scheduled_investment_technicals.py fail for every ticker.
"""
import ast
import math
import types
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parent.parent
PROTECTED={"rsi","atr","safe","stock_trend_channel","latest_completed_daily_candle_signal"}


def module_ast(filename):
    return ast.parse((ROOT/filename).read_text(encoding="utf-8"),filename=filename)


class PublicBindings(ast.NodeVisitor):
    """Assignments under module-level with/if/loops share globals with defs."""
    def __init__(self):
        self.found=[]
    def visit_Name(self,node):
        if isinstance(node.ctx,ast.Store) and node.id in PROTECTED:
            self.found.append((node.id,node.lineno))
    def visit_FunctionDef(self,node):
        pass
    def visit_AsyncFunctionDef(self,node):
        pass
    def visit_ClassDef(self,node):
        pass
    def visit_Lambda(self,node):
        pass


def pure_functions(filename,names,namespace):
    tree=module_ast(filename)
    kept=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in names]
    if len(kept)!=len(names):
        raise RuntimeError(f"Expected functions missing from {filename}")
    module=ast.Module(
        body=[ast.ImportFrom(module="__future__",names=[ast.alias(name="annotations")],level=0)] + kept,
        type_ignores=[],
    )
    exec(compile(ast.fix_missing_locations(module),filename,"exec"),namespace)
    return namespace


def daily_fixture():
    dates=pd.bdate_range(end=pd.Timestamp.now(tz="UTC")-pd.Timedelta(days=1),periods=280)
    close=np.linspace(100,130,len(dates))+np.sin(np.arange(len(dates))/4)
    return pd.DataFrame({
        "Open":close-0.3,"High":close+1.0,"Low":close-1.3,
        "Close":close,"Volume":np.full(len(dates),1_000_000),
    },index=dates)


class GlobalFunctionSafety(unittest.TestCase):
    def test_technical_functions_never_overwritten_by_page_display_values(self):
        visitor=PublicBindings()
        visitor.visit(module_ast("stock_app.py"))
        self.assertEqual(visitor.found,[],
                         "Module-level UI assignment overwrites calculation function")

    def test_technical_analysis_runs_on_finished_daily_candles(self):
        names={"safe","rsi","atr","stock_trend_channel",
               "latest_completed_daily_candle_signal","technical_from_df"}
        namespace=pure_functions("stock_app.py",names,{"pd":pd,"np":np,"math":math})
        self.assertTrue(callable(namespace["rsi"]))
        result=namespace["technical_from_df"](daily_fixture())
        self.assertIsInstance(result,dict)
        self.assertTrue(math.isfinite(result["rsi"]))
        self.assertIn("trade_score",result)

    def test_backfill_uses_original_ticker_and_recovers_rsi_function(self):
        names={"safe","rsi","atr","stock_trend_channel",
               "latest_completed_daily_candle_signal","technical_from_df"}
        ns=pure_functions("stock_app.py",names,{"pd":pd,"np":np,"math":math})
        class FakeTicker:
            def __init__(self,symbol):
                assert symbol=="ASML.AS"
            def history(self,**kwargs):
                return daily_fixture()
        stock=types.SimpleNamespace(
            yf=types.SimpleNamespace(Ticker=FakeTicker),
            technical_from_df=ns["technical_from_df"],
        )
        worker=pure_functions(
            "scheduled_investment_technicals.py",{"recovery_analysis"},
            {"pd":pd,"stock_app":stock},
        )
        value=worker["recovery_analysis"]("ASML.AS")
        self.assertEqual(value["History source ticker"],"ASML.AS")
        self.assertTrue(math.isfinite(value["rsi"]))


if __name__=="__main__":
    unittest.main()
