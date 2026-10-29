"""Baseline regression: document unsafe legacy labels, not approve them."""
import ast
import hashlib
import importlib.util
import math
import unittest
from pathlib import Path
from typing import Dict

from trade_surface_contract_audit import audit

ROOT = Path(__file__).resolve().parents[1]


def quick_trade_function():
    """Extract only the real function from stock_app; no Streamlit execution."""
    source = (ROOT / "stock_app.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    fn = next(node for node in tree.body
              if isinstance(node, ast.FunctionDef) and node.name == "quick_trade_decision")
    # Compile the original function body, not a hand-written approximation.
    fn.decorator_list = []
    mod = ast.Module(body=[ast.ImportFrom(module="__future__",
                                          names=[ast.alias(name="annotations")], level=0), fn],
                     type_ignores=[])
    ast.fix_missing_locations(mod)
    namespace = {"Dict": Dict, "math": math,
                 "safe": lambda v: float(v) if v is not None else float("nan")}
    exec(compile(mod, str(ROOT / "stock_app.py"), "exec"), namespace)
    return namespace["quick_trade_decision"]


class TradeSurfaceContractAudit(unittest.TestCase):
    def test_legacy_direct_ticker_buy_without_approved_signal_evidence(self):
        spec = importlib.util.spec_from_file_location("two_strategy", ROOT / "two_strategy.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        result = module.trade_decision({
            "price": 100, "swing_target": 125, "invalidation": 90,
            "trade_score": 90, "one_year_hold_score": 80,
            "valuation_score": 20, "rr": 2.5, "upside_pct": 25,
            "in_preferred_zone": True,
            # Deliberately NO completed-bar, event or approved technical_state.
        })
        self.assertEqual(result["action"], "BUY")
        self.assertTrue(result["qualifies"])

    def test_quick_analyse_buy_without_approved_signal_evidence(self):
        result = quick_trade_function()({
            "classification": "Swing only", "in_preferred_zone": True,
            "trade_score": 90, "hold_score": 80, "rr": 2.5,
            "upside_pct": 25,
            # No confirmed Trade state, source freshness or event evidence.
        })
        self.assertEqual(result["action"], "BUY")

    def test_current_mixed_ui_route_and_legacy_ranking_are_recorded(self):
        report = audit(ROOT)
        found = report["source_observations"]
        self.assertTrue(found["stocks_route_still_uses_mixed_overlay"])
        self.assertFalse(found["separate_pension_route_present"])
        self.assertTrue(found["investment_section_still_in_stocks_overlay"])
        self.assertTrue(found["overlay_ranks_trade_buy_using_legacy_thresholds"])
        self.assertTrue(found["approved_trade_feed_labels_ready_to_verify_not_buy"])

    def test_source_hashes_match_real_main_files(self):
        report = audit(ROOT)
        for name, digest in report["source_sha256"].items():
            self.assertEqual(digest, hashlib.sha256((ROOT / name).read_bytes()).hexdigest())

    def test_real_manifest_provenance_and_unresolved_venues(self):
        report = audit(ROOT)
        tech = report["manifest_audit"]["prepared_trade_technicals"]["venues"]
        fund = report["manifest_audit"]["prepared_trade_fundamentals"]["venues"]
        self.assertIn("OLD_RULEBOOK_BUILD", tech["gettex"]["flags"])
        self.assertIn("PARTIAL_OR_INCOMPLETE", tech["gettex"]["flags"])
        self.assertIn("OLD_RULEBOOK_BUILD", tech["otc"]["flags"])
        self.assertIn("PARTIAL_OR_INCOMPLETE", fund["tsx"]["flags"])
        self.assertIn("PARTIAL_OR_INCOMPLETE", fund["lse"]["flags"])

    def test_no_green_release_from_static_source_or_manifest(self):
        report = audit(ROOT)
        self.assertEqual(report["stocks_trade_production_readiness"], "NOT_VERIFIED")
        self.assertIn("individual latest completed exchange bar timestamps and vendor lag",
                      report["not_verified"])
        self.assertIn("adequate independent prospective Trade paper outcomes",
                      report["not_verified"])


if __name__ == "__main__":
    unittest.main()
