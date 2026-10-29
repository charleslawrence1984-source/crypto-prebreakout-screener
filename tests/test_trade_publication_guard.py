"""Fail-closed regression for legacy Stocks Trade BUY publication (no network)."""
from __future__ import annotations

import ast
import math
import unittest
from pathlib import Path
from typing import Dict

from trade_action_contract import REQUIRED_RECEIPTS, guarded_trade_publication
from two_strategy import trade_decision

ROOT = Path(__file__).resolve().parents[1]


def quick_trade_function():
    # The actual stock_app function is extracted to avoid Streamlit side effects.
    source = (ROOT / "stock_app.py").read_text(encoding="utf-8")
    fn = next(node for node in ast.parse(source).body
              if isinstance(node, ast.FunctionDef) and node.name == "quick_trade_decision")
    fn.decorator_list = []
    mod = ast.Module(body=[ast.ImportFrom(module="__future__",
                        names=[ast.alias(name="annotations")], level=0), fn], type_ignores=[])
    ast.fix_missing_locations(mod)
    namespace = {"Dict": Dict, "math": math,
                 "safe": lambda v: float(v) if v is not None else float("nan")}
    exec(compile(mod, str(ROOT / "stock_app.py"), "exec"), namespace)
    return namespace["quick_trade_decision"]


def legacy_buy_fixture():
    return {
        "price": 100, "swing_target": 125, "invalidation": 90,
        "trade_score": 90, "one_year_hold_score": 80,
        "valuation_score": 20, "rr": 2.5, "upside_pct": 25,
        "in_preferred_zone": True, "classification": "Swing only",
        "hold_score": 80,
    }


def full_receipts():
    return {"approved_trade_status": "PAPER CANDIDATE",
            **{key: True for key in REQUIRED_RECEIPTS}}


class PublicationContractTests(unittest.TestCase):
    def test_unverified_legacy_scores_cannot_publish_buy(self):
        result = trade_decision(legacy_buy_fixture())
        self.assertEqual(result["action"], "WAIT")
        self.assertFalse(result["qualifies"])
        self.assertTrue(result["legacy_score_qualifies"])
        self.assertTrue(any("Legacy" in x or "source" in x for x in result["reasons"]))

    def test_quick_analysis_cannot_publish_buy_without_original_evidence(self):
        result = quick_trade_function()(legacy_buy_fixture())
        self.assertEqual(result["action"], "WAIT")

    def test_missing_original_state_is_not_bought(self):
        self.assertEqual(guarded_trade_publication(legacy_buy_fixture())["action"], "WAIT")

    def test_blocked_or_watch_state_cannot_be_promoted_by_positive_receipts(self):
        for state in ("BLOCKED", "WATCH", "RETRY", "AWAITING NEXT OPEN", "DATA STALE"):
            with self.subTest(state=state):
                evidence = {**full_receipts(), "approved_trade_status": state}
                self.assertEqual(guarded_trade_publication(evidence)["action"], "WAIT")

    def test_each_missing_receipt_stops_release(self):
        for field in REQUIRED_RECEIPTS:
            with self.subTest(field=field):
                evidence = full_receipts()
                evidence.pop(field)
                result = guarded_trade_publication(evidence)
                self.assertEqual(result["action"], "WAIT")
                self.assertIn(field, result["reason"])

    def test_non_boolean_truthy_receipts_fail(self):
        for false_claim in ("true", 1, {"verified": True}):
            with self.subTest(value=false_claim):
                evidence = {**full_receipts(), "official_event_and_news_verified": false_claim}
                self.assertEqual(guarded_trade_publication(evidence)["action"], "WAIT")

    def test_all_claimed_receipts_still_only_manual_review_not_buy(self):
        result = guarded_trade_publication(full_receipts())
        self.assertEqual(result["action"], "READY TO VERIFY")
        self.assertIn("manual", result["reason"].lower())
        self.assertEqual(trade_decision({**legacy_buy_fixture(), **full_receipts()})["action"],
                         "READY TO VERIFY")
        self.assertFalse(trade_decision({**legacy_buy_fixture(), **full_receipts()})["qualifies"])
        self.assertEqual(quick_trade_function()({**legacy_buy_fixture(), **full_receipts()})["action"],
                         "READY TO VERIFY")

    def test_owned_exit_and_hold_not_modified_by_new_entry_guard(self):
        fixture = legacy_buy_fixture()
        self.assertEqual(trade_decision({**fixture, "price": 89}, owned=True)["action"],
                         "EXIT / REASSESS")
        self.assertEqual(trade_decision({**fixture, "price": 127}, owned=True)["action"],
                         "EXIT / TARGET REACHED")
        self.assertEqual(trade_decision(fixture, owned=True)["action"], "HOLD")

    def test_ranked_surface_uses_same_guard_not_independent_score_buy(self):
        source = (ROOT / "stocks_page.py").read_text(encoding="utf-8")
        section = source.split('trade_ranked["Trade Action"] = trade_ranked.apply(', 1)[1].split(
            '                    def _stock_channel_rank', 1)[0]
        self.assertIn('guarded_trade_publication(r.to_dict())["action"]', section)
        self.assertNotIn('lambda r: "BUY"', section)

    def test_no_external_execution_or_pension_changes_in_guard(self):
        source = (ROOT / "trade_action_contract.py").read_text(encoding="utf-8")
        self.assertNotIn("requests.", source)
        self.assertNotIn("place_order(", source)
        self.assertNotIn("transfer_pension(", source)

    def test_null_input_fails_closed(self):
        self.assertEqual(guarded_trade_publication(None)["action"], "WAIT")


if __name__ == "__main__":
    unittest.main()
