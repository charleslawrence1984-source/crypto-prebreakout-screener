"""Deterministic forward-only funded paper research contract tests.

These fixtures do not prove live broker execution, alpha, or a challenge pass.
"""
from datetime import datetime, timezone
import unittest

from funded_forward_capture import create_snapshot
from funded_forward_outcomes import evaluate_snapshot, write_outcome_once

STOCK_NOW = datetime(2026, 10, 19, 22, 45, tzinfo=timezone.utc)
CRYPTO_NOW = datetime(2026, 10, 19, 1, 0, tzinfo=timezone.utc)
STOCK_UNIVERSE = {"stocks": ("AAPL", "SPCX")}
CRYPTO_UNIVERSE = {"crypto": ("BTC", "ETH")}
FLAGS = {
    "swing_eligible": True,
    "entry_timing_actionable": True,
    "execution_liquidity_pass_public_spot": True,
    "funded_bid_ask_verified": False,
}


def stock(status="FUNDED BREAKOUT PENDING OPEN", **kwargs):
    fields = {
        "asset": "AAPL", "status": status, "bar_timestamp": "2026-10-19",
        "score": 82, "entry": 100, "stop": 95, "target": 115, "rr": 3.0,
    }
    fields.update(kwargs)
    return create_snapshot("stocks", [fields,
        {"asset": "SPCX", "status": "DATA UNAVAILABLE", "reason": "PRICE HISTORY 108/252 COMPLETED BARS"}],
        observed_at=STOCK_NOW, run_id="frozen-stock", universe=STOCK_UNIVERSE)


def crypto(status="BUY", **kwargs):
    fields = {
        "asset": "BTC", "status": status,
        "bar_timestamp": "2026-10-18 20:00:00+00:00",
        "score": 92, "entry": 100, "stop": 95, "target": 115, "rr": 3.0,
        "meta": dict(FLAGS),
    }
    fields.update(kwargs)
    return create_snapshot("crypto", [fields,
        {"asset": "ETH", "status": "PASS",
         "bar_timestamp": "2026-10-18 20:00:00+00:00"}],
        observed_at=CRYPTO_NOW, run_id="frozen-crypto", universe=CRYPTO_UNIVERSE)


def daily(day, open=99, high=110, low=98, close=104):
    return {"timestamp": day, "open": open, "high": high, "low": low, "close": close}


def bar(t, open=99, high=110, low=98, close=104):
    return {"timestamp": t, "open": open, "high": high, "low": low, "close": close}


class ProspectiveShadowOutcomes(unittest.TestCase):
    def score_stock(self, rows, snapshot=None):
        return evaluate_snapshot(snapshot or stock(), {"AAPL": rows},
            evaluated_at=datetime(2026, 11, 25, 23, 0, tzinfo=timezone.utc),
            universe=STOCK_UNIVERSE)["rows"][0]

    def score_crypto(self, rows, snapshot=None):
        return evaluate_snapshot(snapshot or crypto(), {"BTC": rows},
            evaluated_at=datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc),
            universe=CRYPTO_UNIVERSE)["rows"][0]

    def test_rejected_crypto_pass_cannot_ever_be_a_shadow_trade(self):
        result = self.score_crypto([bar("2026-10-19T04:00:00+00:00")],
                                   crypto(status="PASS"))
        self.assertEqual(result["shadow_status"], "NO RESEARCH ENTRY")
        self.assertNotIn("hypothetical_entry", result)
        self.assertFalse(result["broker_trade"])

    def test_valid_crypto_buy_can_enter_future_bar_and_hit_target(self):
        result = self.score_crypto([
            bar("2026-10-19T00:00:00+00:00", open=100, high=125, low=94, close=119),
            bar("2026-10-19T04:00:00+00:00", open=99, high=104, low=98),
            bar("2026-10-19T08:00:00+00:00", open=104, high=117, low=101),
        ])
        self.assertEqual(result["shadow_status"], "SHADOW TARGET")
        self.assertEqual(result["hypothetical_entry"], 100)
        self.assertLess(result["shadow_net_r"], 3.0)
        self.assertFalse(result["broker_trade"])

    def test_intrabar_stop_always_wins_over_target(self):
        result = self.score_stock([daily("2026-10-20", high=120, low=93)])
        self.assertEqual(result["shadow_status"], "SHADOW STOP")
        self.assertEqual(result["hypothetical_exit"], 95)

    def test_gap_below_stop_is_worse_than_planned_stop(self):
        result = self.score_stock([
            daily("2026-10-20", high=101, low=97, close=100),
            daily("2026-10-21", open=90, high=91, low=89, close=90),
        ])
        self.assertEqual(result["shadow_status"], "SHADOW STOP (GAP)")
        self.assertEqual(result["hypothetical_exit"], 90)
        self.assertLess(result["shadow_net_r"], -1)

    def test_gap_above_entry_can_veto_real_reward_risk(self):
        result = self.score_stock([
            daily("2026-10-20", open=112, high=115, low=109, close=113),
        ])
        self.assertEqual(result["shadow_status"], "GAP / REWARD:RISK VETO")
        self.assertNotIn("shadow_net_r", result)

    def test_watches_do_not_count_as_trades(self):
        result = self.score_stock([daily("2026-10-20", high=120)],
                                  stock(status="FUNDED TREND WATCH"))
        self.assertEqual(result["shadow_status"], "NO RESEARCH ENTRY")
        self.assertNotIn("shadow_net_r", result)

    def test_missing_price_data_is_not_win_or_loss(self):
        result = self.score_stock(None)
        self.assertEqual(result["shadow_status"], "DATA UNVERIFIED")
        self.assertNotIn("shadow_net_r", result)

    def test_crypto_missing_4h_candle_fails_closed(self):
        result = self.score_crypto([
            bar("2026-10-19T08:00:00+00:00", open=99, high=115),
        ])
        self.assertEqual(result["shadow_status"], "DATA UNVERIFIED")
        self.assertIn("MISSING", result["reason"])

    def test_still_forming_crypto_bar_is_not_a_trade(self):
        result = evaluate_snapshot(crypto(),
            {"BTC": [bar("2026-10-19T04:00:00+00:00", high=120, low=93)]},
            evaluated_at=datetime(2026, 10, 19, 6, 0, tzinfo=timezone.utc),
            universe=CRYPTO_UNIVERSE)["rows"][0]
        self.assertEqual(result["shadow_status"], "AWAITING FUTURE CANDLES")

    def test_stock_signal_candle_before_capture_is_not_replayed(self):
        result = self.score_stock([
            daily("2026-10-19", open=100, high=125, low=93),
            daily("2026-10-20", open=99, high=101, low=98, close=100),
        ])
        self.assertEqual(result["shadow_status"], "SHADOW OPEN")

    def test_entry_window_expiry_requires_complete_future_bars(self):
        result = self.score_stock([
            daily("2026-10-20", high=99, low=96, close=98),
            daily("2026-10-21", high=99, low=96, close=98),
            daily("2026-10-22", high=99, low=96, close=98),
        ])
        self.assertEqual(result["shadow_status"], "EXPIRED WITHOUT ENTRY")

    def test_no_outcomes_are_fabricated_from_qualified_zero_scenario(self):
        payload = crypto(status="WATCH")
        result = evaluate_snapshot(payload, {}, evaluated_at=CRYPTO_NOW,
                                   universe=CRYPTO_UNIVERSE)
        self.assertEqual(result["hypothetical_closed_scenarios"], 0)
        self.assertEqual(result["hypothetical_net_r_sum"], 0)
        self.assertTrue(result["aggregate_is_not_account_equity_or_strategy_pnl"])
        self.assertFalse(result["funded_challenge_pass_claimed"])

    def test_target_requires_completed_bar(self):
        result = evaluate_snapshot(crypto(), {"BTC": [
            bar("2026-10-19T04:00:00+00:00", high=101, low=97, close=100),
            bar("2026-10-19T08:00:00+00:00", high=120, low=99),
        ]}, evaluated_at=datetime(2026, 10, 19, 11, 0, tzinfo=timezone.utc),
        universe=CRYPTO_UNIVERSE)["rows"][0]
        self.assertEqual(result["shadow_status"], "SHADOW OPEN")

    def test_invalid_frozen_plan_never_enters(self):
        result = self.score_crypto([
            bar("2026-10-19T04:00:00+00:00", high=120),
        ], crypto(entry=100, stop=101))
        self.assertEqual(result["shadow_status"], "FROZEN PLAN VETO")

    def test_original_snapshot_and_outcome_files_are_not_overwritten(self):
        from tempfile import TemporaryDirectory
        from pathlib import Path
        original = crypto(status="WATCH")
        evaluated = evaluate_snapshot(original, {}, evaluated_at=CRYPTO_NOW,
                                      universe=CRYPTO_UNIVERSE)
        with TemporaryDirectory() as tmp:
            dest = Path(tmp)/"new-outcome.json"
            write_outcome_once(dest, evaluated)
            contents = dest.read_bytes()
            with self.assertRaises(FileExistsError):
                write_outcome_once(dest, evaluated)
            self.assertEqual(dest.read_bytes(), contents)

    def test_archive_tampering_blocks_all_shadow_evaluation(self):
        snap = stock()
        snap["rows"][0]["status"] = "ENTRY READY"
        with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
            self.score_stock([], snap)


if __name__ == "__main__":
    unittest.main()
