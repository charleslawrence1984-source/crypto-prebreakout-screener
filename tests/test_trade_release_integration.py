"""Release-intent composition regression: original gates always fail closed."""
from __future__ import annotations

import unittest
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from trade_action_contract import REQUIRED_RECEIPTS
from trade_release_integration import inspect_unreleased_trade

NOW = datetime(2026, 10, 29, 15, 0, tzinfo=timezone.utc)


def price_bars(end="2026-10-28", n=280):
    dates = pd.bdate_range(end=end, periods=n)
    return pd.DataFrame({
        "Open": np.full(n, 100.0), "High": np.full(n, 102.0),
        "Low": np.full(n, 98.0), "Close": np.full(n, 100.5),
        "Volume": np.full(n, 1_000_000.0),
    }, index=dates)


def technical_looks_eligible(*_):
    return {"technical_state": "AWAITING NEXT OPEN", "technical_reason": "ALL_TECH_PASS",
            "technical_score": 95.0}


def all_claims():
    return {"approved_trade_status": "PAPER CANDIDATE",
            **{key: True for key in REQUIRED_RECEIPTS}}


class IntegratedReleaseTests(unittest.TestCase):
    def gate(self, frame=None, benchmark=True, **kwargs):
        return inspect_unreleased_trade(
            "AAPL", "nasdaq", frame if frame is not None else price_bars(),
            price_bars() if benchmark else None,
            NOW, technical_evaluator=technical_looks_eligible, **kwargs,
        )

    def assert_closed(self, result):
        self.assertEqual(result["live_readiness"], "BLOCKED")
        self.assertFalse(result["approved_order"])
        self.assertFalse(result["approved_paper_entry"])
        self.assertEqual(result["rendered_action_contract"], {
            "quick_analyse": "WAIT", "direct_ticker": "WAIT",
            "ranked_trade": "WAIT",
        })

    def test_even_perfect_technical_claim_is_not_a_live_buy(self):
        row = self.gate()
        self.assertEqual(row["technical_state"], "AWAITING NEXT OPEN")
        self.assertEqual(row["source_check"]["status"], "PASS")
        self.assertEqual(row["price_session"], "2026-10-28")
        self.assert_closed(row)

    def test_all_untrusted_claimed_receipts_never_escalate_to_buy(self):
        self.assert_closed(self.gate(claimed_publication_receipts=all_claims()))

    def test_preceding_session_stale_bar_cannot_pass_price_gate(self):
        row = self.gate(frame=price_bars(end="2026-10-27"))
        self.assertEqual(row["source_check"]["status"], "BLOCKED")
        self.assertIn("UNVERIFIED_COMPLETED_EXCHANGE_BAR", row["blocked_gates"])
        self.assert_closed(row)

    def test_partial_current_day_excluded_not_promoted(self):
        data = price_bars()
        data.loc[pd.Timestamp("2026-10-29")] = [100, 101, 99, 100, 100000]
        row = self.gate(frame=data)
        self.assertTrue(row["source_check"]["excluded_incomplete_latest_bar"])
        self.assertEqual(row["price_session"], "2026-10-28")
        self.assert_closed(row)

    def test_missing_benchmark_blocks_scoring(self):
        row = self.gate(benchmark=False)
        self.assertEqual(row["technical_state"], "NOT_EVALUATED")
        self.assertIn("BENCHMARK_PRICE_HISTORY_UNAVAILABLE", row["technical_reason"])
        self.assert_closed(row)

    def test_stale_benchmark_blocks_scoring(self):
        row = inspect_unreleased_trade("AAPL","nasdaq",price_bars(),
                    price_bars(end="2026-10-27"),NOW,
                    technical_evaluator=technical_looks_eligible)
        self.assertIn("BENCHMARK_STALE_OR_MISSING_COMPLETED_BAR", row["technical_reason"])
        self.assert_closed(row)

    def test_anomalous_london_2024_history_is_not_automatically_repaired(self):
        dates = pd.bdate_range(end="2026-10-28", periods=670)
        n=len(dates)
        data=pd.DataFrame({
            "Open":np.full(n,100.0), "High":np.full(n,102.0),
            "Low":np.full(n,98.0), "Close":np.full(n,100.5),
            "Volume":np.full(n,1_000_000.0),
        },index=dates)
        # Inject a bad old candle; cannot silently discard for EMA calculations.
        data.iloc[23, data.columns.get_loc("High")] = 90.0
        row=inspect_unreleased_trade("AZN.L","lse",data,data,NOW,
                    technical_evaluator=technical_looks_eligible)
        self.assertEqual(row["source_check"]["status"],"BLOCKED")
        self.assertIn("INVALID_OHLCV",row["technical_reason"])
        self.assert_closed(row)

    def test_missing_historical_pit_metadata_stays_blocked(self):
        row=self.gate(pit_receipt={"ticker":"AAPL"})
        self.assertEqual(row["pit_evidence_status"],"BLOCKED_MISSING_OR_LOOKAHEAD_EVIDENCE")
        self.assert_closed(row)

    def test_claimed_metadata_must_not_be_authenticated(self):
        # A self-declared hash cannot turn this research pipeline into BUY.
        row=self.gate(pit_receipt={
            "ticker":"AAPL","venue_mic":"XNAS","isin":"US0378331005",
            "decision_close_utc":"2026-10-28T20:00:00Z",
            "financial_published_utc":"2026-10-27T12:00:00Z",
            "source_evidence_sha256":"f"*64,
        })
        self.assertFalse(row["publication_receipts_authenticated"])
        self.assert_closed(row)

    def test_wrong_venue_gettex_and_otc_are_blocked(self):
        for venue in ("gettex","otc"):
            with self.subTest(venue=venue):
                row=inspect_unreleased_trade("AAPL",venue,price_bars(),price_bars(),
                                              NOW,technical_evaluator=technical_looks_eligible)
                self.assertIn("UNSUPPORTED_EXCHANGE_CALENDAR",row["source_check"]["reason"])
                self.assert_closed(row)

    def test_no_naive_capture_timestamps(self):
        with self.assertRaises(ValueError):
            inspect_unreleased_trade("AAPL","nasdaq",price_bars(),price_bars(),
                                      datetime(2026,10,29,15), technical_evaluator=technical_looks_eligible)

    def test_approved_original_technical_block_does_not_promote_review(self):
        row=inspect_unreleased_trade("AAPL","nasdaq",price_bars(),price_bars(),NOW,
            technical_evaluator=lambda *_:{"technical_state":"BLOCKED",
                                            "technical_reason":"NO_MACD_CROSS"})
        self.assertEqual(row["technical_state"],"BLOCKED")
        self.assert_closed(row)

if __name__=="__main__":
    unittest.main()
