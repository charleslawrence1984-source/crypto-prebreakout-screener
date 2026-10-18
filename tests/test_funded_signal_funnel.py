"""Frozen funded evidence funnel: no relaxed strategy rules and no broker orders."""
from datetime import datetime, timedelta, timezone
import unittest

from funded_forward_capture import create_snapshot, funded_universe
from funded_signal_funnel import summarize_history, summarize_snapshot

UNIVERSE = funded_universe()
START = datetime(2026, 10, 17, 18, 45, tzinfo=timezone.utc)


def observation(kind, run, dt, *, changes=None):
    changed = changes or {}
    rows = []
    for coin in UNIVERSE[kind]:
        default_state = "PASS" if kind == "crypto" else "BLOCKED"
        row = {
            "asset": coin, "status": default_state,
            "reason": ("Too far below resistance" if kind == "crypto"
                       else "NO ACTIVE RSI EXHAUSTION OR CURRENT CROSSOVER"),
            "bar_timestamp": ("2026-10-17T12:00:00+00:00" if kind == "crypto"
                              else "2026-10-16"),
            "score": 42 if kind == "crypto" else None,
            "entry": None, "stop": None, "target": None, "rr": 1.5,
            "meta": ({
                "volume_usd_public_spot_proxy": 450000,
                "entry_timing_actionable": False,
                "swing_eligible": False,
                "relative_strength_vs_btc_pct": -1,
            } if kind == "crypto" else {}),
        }
        row.update(changed.get(coin, {}))
        rows.append(row)
    return create_snapshot(kind, rows, observed_at=dt, run_id=run,
                           universe=UNIVERSE)


class KrakenFundedOriginalSignalFunnel(unittest.TestCase):
    def test_rejected_pass_is_never_a_signal_even_with_good_score(self):
        first = observation("crypto", "pass-only", START, changes={
            "BTC": {"score": 96, "rr": 4, "reason": "Too far below resistance",
                    "meta": {"volume_usd_public_spot_proxy": 5_000_000,
                             "entry_timing_actionable": True,
                             "swing_eligible": True}},
        })
        out = summarize_snapshot(first)
        self.assertEqual(out["original_research_entry_triggers"], [])
        self.assertEqual(out["original_states"]["PASS"], 33)
        self.assertEqual(out["overlapping_evidence_gaps"]["distance_from_resistance"], 33)
        self.assertEqual(out["funded_broker_buy_confirmed"], 0)
        self.assertTrue(out["diagnostic_counts_are_nonexclusive"])

    def test_watches_are_observed_not_promoted(self):
        snap = observation("crypto", "watch-only", START, changes={
            "BTC": {"status": "WATCH", "reason": "Pre-breakout candidate",
                    "score": 65.6, "rr": 10.25},
            "HBAR": {"status": "WATCH", "reason": "Pre-breakout candidate",
                     "score": 52.6, "rr": 1.8},
        })
        out = summarize_snapshot(snap)
        self.assertEqual(len(out["watch_assets"]), 2)
        self.assertEqual(out["original_research_entry_triggers"], [])
        self.assertGreaterEqual(out["overlapping_evidence_gaps"]["score_below_80"], 2)
        self.assertGreaterEqual(out["overlapping_evidence_gaps"]["projected_rr_below_2"], 1)

    def test_watch_then_actual_original_buy_counts_one_transition(self):
        a = observation("crypto", "one", START, changes={"BTC": {"status": "WATCH"}})
        b = observation("crypto", "two", START + timedelta(hours=4), changes={
            "BTC": {"status": "BUY", "reason": "Original BUY engine confirmed",
                    "score": 90, "rr": 3},
        })
        c = observation("crypto", "three", START + timedelta(hours=8), changes={
            "BTC": {"status": "BUY", "score": 95, "rr": 3.5},
        })
        out = summarize_history([a, b, c], as_of=(START+timedelta(hours=8, minutes=1)).isoformat())
        self.assertEqual(len(out["research_entry_trigger_observations"]), 2)
        self.assertEqual(len(out["prior_watch_to_original_trigger_transitions"]), 1)
        self.assertFalse(out["funded_challenge_pass_confirmed"])
        self.assertEqual(out["broker_trades_verified"], 0)

    def test_repeated_watch_and_gaps_in_actual_archived_scans(self):
        a = observation("crypto", "watch-old", START, changes={"BTC": {"status": "WATCH"}})
        b = observation("crypto", "watch-next", START+timedelta(hours=6, minutes=20),
                        changes={"BTC": {"status": "WATCH"}})
        out = summarize_history([b, a], as_of=(START+timedelta(hours=7)).isoformat())
        self.assertEqual(len(out["assets_watched_more_than_once"]), 1)
        self.assertEqual(out["assets_watched_more_than_once"][0]["asset"], "BTC")
        self.assertEqual(len(out["observed_crypto_gaps_over_5_5h"]), 1)
        self.assertEqual(out["research_entry_trigger_observations"], [])

    def test_weekend_stock_only_is_no_crypto_scan_evidence(self):
        a = observation("stocks", "stocks-only", START, changes={
            "AAPL": {"status": "FUNDED TREND WATCH",
                     "reason": "HIGHER-LOW / COMPRESSION BASE"},
        })
        out = summarize_history([a], as_of=START.isoformat())
        self.assertEqual(out["stocks_snapshots"], 1)
        self.assertEqual(out["crypto_snapshots"], 0)
        self.assertTrue(out["crypto_capture_age_warn_over_8h"])
        self.assertEqual(len(out["latest_snapshot_summaries"]["stocks"]["watch_assets"]), 1)

    def test_missing_source_or_tampering_never_reports_zero_trades(self):
        with self.assertRaisesRegex(RuntimeError, "No original"):
            summarize_history([], as_of=START.isoformat())
        first = observation("crypto", "one", START)
        first["rows"][0]["status"] = "BUY"
        with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
            summarize_history([first], as_of=START.isoformat())

    def test_same_run_different_archived_fingerprint_is_rejected(self):
        first = observation("crypto", "same", START)
        second = observation("crypto", "same", START+timedelta(hours=4))
        with self.assertRaisesRegex(ValueError, "Conflicting frozen"):
            summarize_history([first, second], as_of=(START+timedelta(hours=4)).isoformat())

    def test_explicit_data_unavailable_is_not_rejected_market_setup(self):
        first = observation("stocks", "missing-stock", START, changes={
            "SPCX": {"status": "DATA UNAVAILABLE",
                     "reason": "PRICE HISTORY 88/252 COMPLETED BARS",
                     "bar_timestamp": ""},
        })
        summary = summarize_snapshot(first)
        self.assertEqual(summary["unavailable_assets"], ["SPCX"])
        self.assertEqual(summary["coverage"], "PARTIAL")
        self.assertNotIn("SPCX", summary["original_research_entry_triggers"])


if __name__ == "__main__":
    unittest.main()
