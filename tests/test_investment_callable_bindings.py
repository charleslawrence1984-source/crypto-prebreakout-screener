"""Regression guard against top-level UI values shadowing stock indicators.

Streamlit executes stock_app.py at module scope. A UI display assignment like
`rsi = 55.0` can silently replace the earlier `rsi(close)` technical
function and break every Investment technical refresh.
"""
import ast
from pathlib import Path
import unittest

SOURCE = Path(__file__).resolve().parents[1] / "stock_app.py"
PROTECTED = {"rsi", "atr", "safe", "technical_from_df",
             "stock_trend_channel", "latest_completed_daily_candle_signal"}


class ModuleScopeWrites(ast.NodeVisitor):
    def __init__(self):
        self.writes = []

    def visit_FunctionDef(self, node):
        return  # nested/local assignments cannot rebind the module

    def visit_AsyncFunctionDef(self, node):
        return

    def visit_ClassDef(self, node):
        return

    def visit_Name(self, node):
        if isinstance(node.ctx, (ast.Store, ast.Del)) and node.id in PROTECTED:
            self.writes.append((node.id, node.lineno))


class StockTechnicalBindings(unittest.TestCase):
    def test_global_indicator_functions_are_never_overwritten_by_ui_values(self):
        tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
        funcs = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
        self.assertTrue(PROTECTED <= funcs, "Protected calculation functions must remain defined")
        scan = ModuleScopeWrites()
        scan.visit(tree)
        self.assertEqual(scan.writes, [], f"Global indicator functions shadowed: {scan.writes}")


if __name__ == "__main__":
    unittest.main()
