"""Prospective no-trade source snapshot tests; never retrospectively backfill."""
import copy
import unittest

import numpy as np
import pandas as pd

from trade_forward_paper_observation import (
    WATCHLIST, capture, freeze_one, later_price_observation,
)

NOW = pd.Timestamp("2026-10-29 14:45:00",tz="UTC")


def bars():
    dates=pd.bdate_range(end="2026-10-28",periods=270)
    return pd.DataFrame({
        "Open":np.full(270,100.0), "High":np.full(270,103.0),
        "Low":np.full(270,98.0), "Close":np.full(270,101.0),
        "Volume":np.full(270,1_000_000.0),
    },index=dates)


class PaperObservationTests(unittest.TestCase):
    def test_frozen_observation_never_qualifies_as_broker_paper_entry(self):
        row=freeze_one("AAPL","nasdaq",bars(),bars(),NOW)
        self.assertEqual(row["source_validation"],"PASS")
        self.assertEqual(row["source_session"],"2026-10-28")
        self.assertFalse(row["approved_paper_entry"])
        self.assertEqual(row["paper_mode"],"RESEARCH_OBSERVATION_NO_EXECUTION")
        self.assertEqual(len(row["observation_id"]),64)

    def test_missing_provider_data_blocks_and_still_receipts(self):
        row=freeze_one("AAPL","nasdaq",None,bars(),NOW)
        self.assertEqual(row["technical_state"],"UNAVAILABLE")
        self.assertEqual(row["source_validation"],"BLOCKED")
        self.assertFalse(row["approved_paper_entry"])

    def test_no_clean_benchmark_cannot_create_technical_observation(self):
        row=freeze_one("NVDA","nasdaq",bars(),None,NOW)
        self.assertEqual(row["technical_reason"],"BENCHMARK_EVIDENCE_UNAVAILABLE")
        self.assertEqual(row["original_rulebook_technical_score"],None)

    def test_existing_same_capture_only_not_forward_followup(self):
        prior=freeze_one("AAPL","nasdaq",bars(),bars(),NOW)
        with self.assertRaisesRegex(ValueError,"NOT_A_PROSPECTIVE"):
            later_price_observation(prior,prior)

    def test_future_close_cannot_backdate_capture(self):
        a=freeze_one("MSFT","nasdaq",bars(),bars(),NOW)
        b=copy.deepcopy(a)
        b["first_captured_utc"]="2026-10-28T14:45:00+00:00"
        with self.assertRaisesRegex(ValueError,"NOT_A_PROSPECTIVE"):
            later_price_observation(a,b)

    def test_next_run_without_later_session_has_no_price_change(self):
        a=freeze_one("MSFT","nasdaq",bars(),bars(),NOW)
        b=copy.deepcopy(a)
        b["first_captured_utc"]="2026-10-29T15:45:00+00:00"
        result=later_price_observation(a,b)
        self.assertEqual(result["status"],"NO_LATER_VERIFIED_COMPLETED_PRICE_BAR")
        self.assertFalse(result["approved_paper_trade"])

    def test_prospective_next_completed_close_is_not_fake_pnl(self):
        a=freeze_one("MSFT","nasdaq",bars(),bars(),NOW)
        b=copy.deepcopy(a)
        b["first_captured_utc"]="2026-10-30T21:50:00+00:00"
        b["source_session"]="2026-10-30"
        b["signal_close_price"]=103.0
        result=later_price_observation(a,b)
        self.assertEqual(result["status"],"DESCRIPTIVE_FORWARD_PRICE_CHANGE_NOT_TRADE_RETURN")
        self.assertFalse(result["approved_paper_trade"])
        self.assertIsNone(result["realized_strategy_return"])

    def test_wrong_symbol_source_does_not_join_paper_returns(self):
        a=freeze_one("AAPL","nasdaq",bars(),bars(),NOW)
        b=freeze_one("NVDA","nasdaq",bars(),bars(),NOW+pd.Timedelta(days=1))
        with self.assertRaisesRegex(ValueError,"SYMBOL_MISMATCH"):
            later_price_observation(a,b)

    def test_fixed_watchlist_real_fixture_cannot_claim_approved_trade(self):
        report=capture(NOW, fetch=lambda _: bars())
        self.assertEqual(len(report["observations"]),len(WATCHLIST))
        self.assertEqual(report["approved_trade_entries"],0)
        self.assertEqual(report["simulated_trade_entries"],0)
        self.assertEqual(report["live_trade_readiness"],"NOT_VERIFIED")
        self.assertTrue(all(x["approved_paper_entry"] is False
                            for x in report["observations"]))
        self.assertTrue(all(x["first_captured_utc"]==NOW.isoformat()
                            for x in report["observations"]))

    def test_provider_errors_are_explicit_not_hindsight_results(self):
        def fail(_):
            raise RuntimeError("provider unavailable")
        report=capture(NOW,fetch=fail)
        self.assertEqual(report["approved_trade_entries"],0)
        self.assertTrue(all(r["source_validation"]=="BLOCKED"
                            for r in report["observations"]))
        self.assertTrue(all(r["provider_issue"]=="RuntimeError"
                            for r in report["observations"]))


if __name__=="__main__":
    unittest.main()
