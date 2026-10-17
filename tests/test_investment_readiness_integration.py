"""Guard against the Investment Streamlit screens bypassing publication safety.

The Streamlit app is intentionally not imported: it executes UI at module
scope. This structural test ensures Home and Opportunities use the same
fail-closed loader while the standalone dataframe tests validate behavior.
"""
import ast
import unittest
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "stock_app.py"


class InvestmentUiReadinessIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = SOURCE.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)

    def test_central_opportunities_loader_guards_all_buy_labels(self):
        funcs = {node.name: node for node in self.tree.body if isinstance(node, ast.FunctionDef)}
        self.assertIn("load_all_investment_opportunities", funcs)
        f = funcs["load_all_investment_opportunities"]
        calls = [node for node in ast.walk(f) if isinstance(node, ast.Call) and
                 isinstance(node.func, ast.Name)]
        names = {node.func.id for node in calls}
        self.assertIn("withhold_unverified_investment_buys", names)
        self.assertIn("load_investment_publication_status", names)

    def test_home_and_opportunities_display_publication_warning(self):
        self.assertIn('if not load_investment_publication_status().get("ready"):', self.source)
        self.assertIn('if not investment_health.get("ready"):', self.source)
        self.assertIn("INVESTMENT DATA NOT LIVE-READY", self.source)
        self.assertIn("Saved BUY labels are withheld", self.source)

    def test_trade_loader_is_not_patched_with_investment_gate(self):
        funcs = {node.name: node for node in self.tree.body if isinstance(node, ast.FunctionDef)}
        self.assertIn("load_all_trade_opportunities", funcs)
        trade = ast.get_source_segment(self.source, funcs["load_all_trade_opportunities"])
        self.assertNotIn("withhold_unverified_investment_buys", trade)


if __name__ == "__main__":
    unittest.main()
