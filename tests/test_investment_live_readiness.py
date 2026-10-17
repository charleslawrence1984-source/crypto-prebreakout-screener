"""Deterministic, offline regression tests for Investment publication BUY safety."""
import ast
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from investment_live_readiness import (
    InvestmentPublication,
    guard_quick_investment_decision,
    withhold_unverified_buys,
)


NOW = datetime(2026, 10, 17, 16, 0, tzinfo=timezone.utc)


def stamp(delta_hours=0):
    return (NOW + timedelta(hours=delta_hours)).isoformat()


class InvestmentReadinessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for dirname in ("prepared_scans", "prepared_investment_technicals", "prepared_priority_investments"):
            (self.root / dirname).mkdir()
        self.fund = {
            "exchanges": {"nyse": {
                "status": "complete", "remaining_symbols": 0,
                "eligible_symbols": 3, "coverage_symbols": 2,
                "coverage_target_symbols": 2, "provider_deferred_symbols": 1,
                "completed_at": stamp(-4),
            }}
        }
        self.tech = {
            "updated_at": stamp(-2), "technical_build": "model-1",
            "exchanges": {"nyse": {
                "status": "partial", "eligible_symbols": 2, "coverage_symbols": 1,
                "remaining_symbols": 1, "source_provider_deferred_symbols": 1,
                "source_total_eligible_symbols": 3,
            }}
        }
        self.price = {
            "price_refreshed_at": stamp(-1),
            "exchanges": {"nyse": {
                "price_refreshed_at": stamp(-1),
                "price_refresh_status": "complete", "price_refresh_missing": 0,
                "price_refresh_rate_limit_errors": 0, "price_refresh_updated": 1,
            }}
        }
        self.summary = {"refreshed_at": stamp(-1), "rows": [{
            "Ticker": "AAA", "Prepared exchange": "nyse", "Action": "BUY CANDIDATE",
            "Valuation FX status": "PASS", "Valuation gate": "PASS",
            "Valuation model version": "model-v2",
            "Price": 15.5, "Base margin of safety %": 42,
            "Required margin of safety %": 30,
            "Bear margin of safety %": 15, "Quality score": 81,
        }]}
        self.rows = [{
            "Ticker": "AAA", "Technical calculated at": stamp(-2),
            "Fundamental source updated at": stamp(-4),
            "Fundamental fingerprint": "sha256-test", "Technical build": "model-1",
            "rsi": 50.5, "trade_score": 65,
        }]
        self.persist()

    def persist(self):
        for dirname, content in (("prepared_scans", self.fund),
                                 ("prepared_investment_technicals", self.tech),
                                 ("prepared_priority_investments", self.price)):
            (self.root / dirname / "manifest.json").write_text(json.dumps(content))
        (self.root / "prepared_priority_investments" / "summary.json").write_text(json.dumps(self.summary))
        pd.DataFrame(self.rows).to_csv(
            self.root / "prepared_investment_technicals" / "nyse.csv.gz",
            index=False, compression="gzip",
        )

    def evidence(self):
        return InvestmentPublication(self.root, now=NOW, expected_valuation_model="model-v2")

    def test_verified_listing_allowed_despite_other_source_provider_deferred_symbol(self):
        self.assertEqual(self.evidence().issue("AAA", kind="nyse"), "")
        self.assertEqual(self.evidence().issue("AAA"), "")

    def test_missing_and_ambiguous_listings_fail_closed(self):
        self.assertIn("listing", self.evidence().issue("BBB"))
        self.assertIn("technical", self.evidence().issue("BBB", kind="nyse"))
        self.assertTrue(self.evidence().issue("AAA", kind="nasdaq"))

    def test_stale_or_future_fundamental_technical_and_priority(self):
        changes = [
            (self.fund["exchanges"]["nyse"], "completed_at", stamp(-24 * 9), "fundamental"),
            (self.tech, "updated_at", stamp(-150), "technical"),
            (self.rows[0], "Technical calculated at", stamp(-200), "technical"),
            (self.price["exchanges"]["nyse"], "price_refreshed_at", stamp(-150), "quote"),
            (self.price["exchanges"]["nyse"], "price_refreshed_at", stamp(3), "quote"),
        ]
        for target, key, bad, expected in changes:
            with self.subTest(key=key, value=bad):
                before = target[key]
                target[key] = bad
                self.persist()
                self.assertIn(expected, self.evidence().issue("AAA", kind="nyse"))
                target[key] = before

    def test_missing_source_mismatch_and_validation_fail_closed(self):
        cases = [
            (self.fund["exchanges"]["nyse"], "status", "partial", "fundamental"),
            (self.fund["exchanges"]["nyse"], "remaining_symbols", 1, "fundamental"),
            (self.tech["exchanges"]["nyse"], "source_total_eligible_symbols", 99, "versions"),
            (self.rows[0], "Fundamental source updated at", stamp(-5), "reconciled"),
            (self.rows[0], "Technical build", "wrong", "build"),
            (self.rows[0], "Fundamental fingerprint", "", "fingerprint"),
            (self.rows[0], "rsi", None, "RSI"),
            (self.summary["rows"][0], "Action", "WAIT", "priority"),
            (self.summary["rows"][0], "Valuation model version", "model-v1", "model version"),
            (self.price["exchanges"]["nyse"], "price_refresh_updated", 0, "updated prices"),
            (self.summary["rows"][0], "Base margin of safety %", 12, "valuation"),
            (self.price["exchanges"]["nyse"], "price_refresh_status", "partial", "quote"),
        ]
        for target, key, bad, expected in cases:
            with self.subTest(key=key, value=bad):
                prior = target[key]
                target[key] = bad
                self.persist()
                self.assertIn(expected.lower(), self.evidence().issue("AAA", kind="nyse").lower())
                target[key] = prior

    def test_home_opportunities_and_quick_share_same_buy_gate(self):
        initial = pd.DataFrame([
            {"Ticker": "AAA", "Exchange": "NYSE", "Action": "BUY CANDIDATE", "Valuation gate": "PASS", "Decision reason": "model buy"},
            {"Ticker": "BBB", "Exchange": "NYSE", "Action": "BUY CANDIDATE", "Valuation gate": "PASS", "Decision reason": "model buy"},
            {"Ticker": "CCC", "Exchange": "NYSE", "Action": "WAIT", "Valuation gate": "WAIT", "Decision reason": "model wait"},
        ])
        before = initial.copy(deep=True)
        evidence = self.evidence()
        result = withhold_unverified_buys(initial, evidence, {"NYSE": "nyse"})
        self.assertEqual(list(result["Action"]), ["BUY CANDIDATE", "REVALUE", "WAIT"])
        self.assertIn("RESEARCH ONLY", result.iloc[1]["Decision reason"])
        self.assertEqual(result.iloc[0]["Investment readiness"], "VERIFIED PUBLICATION — MANUAL REVIEW REQUIRED")
        self.assertEqual(guard_quick_investment_decision({"action": "BUY CANDIDATE"}, "AAA", evidence)["action"], "BUY CANDIDATE")
        self.assertEqual(guard_quick_investment_decision({"action": "BUY CANDIDATE"}, "BBB", evidence)["action"], "REVALUE")
        pd.testing.assert_frame_equal(initial, before)

    def test_if_the_price_refresh_stales_no_screen_can_retain_buy(self):
        self.price["exchanges"]["nyse"]["price_refreshed_at"] = stamp(-150)
        self.persist()
        evidence = self.evidence()
        frame = pd.DataFrame([{"Ticker": "AAA", "Exchange": "NYSE", "Action": "BUY CANDIDATE"}])
        self.assertEqual(withhold_unverified_buys(frame, evidence, {"NYSE": "nyse"}).iloc[0]["Action"], "REVALUE")
        self.assertEqual(guard_quick_investment_decision({"action": "BUY CANDIDATE"}, "AAA", evidence)["action"], "REVALUE")

    def test_missing_all_publication_files_fails_closed(self):
        with tempfile.TemporaryDirectory() as empty:
            evidence = InvestmentPublication(Path(empty), now=NOW)
            self.assertTrue(evidence.issue("AAA"))


class InvestmentUIIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (Path(__file__).resolve().parents[1] / "stock_app.py").read_text(encoding="utf-8")
        cls.funcs = {node.name: node for node in ast.parse(cls.source).body
                     if isinstance(node, ast.FunctionDef)}

    def test_one_loader_governs_home_and_opportunities_buy_labels(self):
        self.assertIn("withhold_unverified_buys", ast.unparse(self.funcs["load_all_investment_opportunities"]))
        self.assertIn("home_investment = load_all_investment_opportunities()", self.source)
        self.assertIn("investment_opportunities = load_all_investment_opportunities()", self.source)

    def test_quick_and_watchlist_decisions_share_guard(self):
        self.assertIn("guard_quick_investment_decision", ast.unparse(self.funcs["quick_investment_decision"]))
        self.assertIn("investment_summary = quick_investment_decision(investment_row)", self.source)
        self.assertIn("investment_decision_summary = quick_investment_decision(investment_row)", self.source)

    def test_advanced_investment_guarded_but_trade_loader_not_touched(self):
        self.assertIn("filtered_fundamentals = withhold_unverified_buys(", self.source)
        self.assertNotIn("withhold_unverified_buys", ast.unparse(self.funcs["load_all_trade_opportunities"]))
        self.assertIn("Investment data readiness:", self.source)
        self.assertIn("Investment data not fully verified:", self.source)


if __name__ == "__main__":
    unittest.main()
