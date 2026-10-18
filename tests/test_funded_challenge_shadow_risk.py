"""Research-only funded 12%/3% reference risk tests. No broker fills."""
from datetime import datetime, timedelta, timezone
import unittest

from funded_forward_capture import create_snapshot, funded_universe
from funded_challenge_shadow_risk import simulate_risk

BASE = datetime(2026, 10, 18, 4, 0, tzinfo=timezone.utc)
ASOF = datetime(2026, 10, 20, 0, 0, tzinfo=timezone.utc)
COINS = funded_universe()["crypto"]


def frozen(run, captured, ticker="BTC", stop=95):
    rows = []
    for coin in COINS:
        rows.append({"asset": coin, "status": "BUY" if coin == ticker else "PASS",
                     "bar_timestamp": (captured-timedelta(hours=4)).isoformat(),
                     "entry": 100 if coin == ticker else None,
                     "stop": stop if coin == ticker else None,
                     "target": 115 if coin == ticker else None,
                     "score": 90 if coin == ticker else None,
                     "rr": 3 if coin == ticker else None})
    return create_snapshot("crypto", rows, observed_at=captured,
                           run_id=run, universe={"crypto": COINS})


def results(snapshot, *, entry_time=None, exit_time=None, exit_price=115,
            ticker="BTC", status="SHADOW TARGET", entry=100):
    opened = entry_time or BASE + timedelta(hours=4)
    ended = exit_time or BASE + timedelta(hours=8)
    rows = []
    for item in snapshot["rows"]:
        if item["asset"] != ticker:
            rows.append({"asset": item["asset"], "original_engine_status": item["status"],
                         "shadow_status": "NO RESEARCH ENTRY", "reason": "not qualified",
                         "research_only": True, "broker_trade": False})
            continue
        r = {"asset": ticker, "original_engine_status": item["status"],
             "shadow_status": status, "research_only": True, "broker_trade": False,
             "entry_at": opened.isoformat(), "hypothetical_entry": entry,
             "scenario_roundtrip_cost_pct": .5}
        if status != "SHADOW OPEN":
            r.update(exit_at=ended.isoformat(), hypothetical_exit=exit_price)
        rows.append(r)
    return {"research_only": True, "broker_trades": 0,
            "funded_account_connected": False, "asset_type": "crypto",
            "source_run_id": snapshot["run_id"],
            "source_sha256": snapshot["payload_sha256"],
            "evaluated_at_utc": ASOF.isoformat(), "rows": rows}


class RiskResearchTests(unittest.TestCase):
    def model(self, snaps, reports, **kwargs):
        return simulate_risk(snaps, reports, evaluated_at=ASOF, **kwargs)

    def test_rejected_pass_and_watch_only_is_no_profit_evidence(self):
        s = frozen("no-trades", BASE, ticker="UNLISTED")
        r = results(s)
        out = self.model([s], [r])
        self.assertEqual(out["admitted_unique_paper_cases"], 0)
        self.assertEqual(out["modeled_realized_equity_proxy_usd"], 1000)
        self.assertFalse(out["kraken_funded_challenge_pass_confirmed"])

    def test_entry_risk_and_notional_caps_hold_even_if_stop_near_entry(self):
        s = frozen("close-stop", BASE, stop=99.99)
        r = results(s)
        out = self.model([s], [r])
        self.assertEqual(out["admitted_unique_paper_cases"], 1)
        self.assertLessEqual(out["admissions"][0]["hypothetical_position_notional_usd"], 350)
        self.assertLessEqual(out["admissions"][0]["planned_risk_including_cost_usd"], 3)
        self.assertLessEqual(out["peak_planned_open_risk_usd"], 10)
        self.assertEqual(out["open_unique_paper_cases"], 0)
        self.assertGreater(out["modeled_realized_equity_proxy_usd"], 1000)

    def test_duplicate_observation_does_not_double_count_open_asset(self):
        a = frozen("run-one", BASE)
        b = frozen("run-two", BASE+timedelta(minutes=5))
        r1 = results(a, status="SHADOW OPEN",
                     entry_time=BASE+timedelta(hours=4))
        r2 = results(b, status="SHADOW OPEN",
                     entry_time=BASE+timedelta(hours=4,minutes=10))
        out = self.model([a,b], [r1,r2])
        self.assertEqual(out["admitted_unique_paper_cases"], 1)
        self.assertEqual(out["duplicate_or_risk_vetoed_cases"], 1)
        self.assertEqual(out["open_unique_paper_cases"], 1)
        self.assertIn("REPEATED SIGNAL", out["vetoes"][0]["reason"])

    def test_gap_stop_below_970_halts_new_entries(self):
        stops = [frozen(f"run-{i}", BASE+timedelta(hours=i),ticker=coin)
                 for i, coin in enumerate(COINS[:11])]
        reports = []
        for i, (snap, coin) in enumerate(zip(stops, COINS[:11])):
            # Each sequential closed loss can exceed planned stop due to gap.
            entry_at = BASE+timedelta(hours=4+i)
            exit_at = entry_at+timedelta(minutes=5)
            reports.append(results(snap, ticker=coin,
                entry_time=entry_at, exit_time=exit_at,
                status="SHADOW STOP (GAP)", exit_price=50))
        out = self.model(stops,reports)
        self.assertTrue(out["modeled_realized_threshold_breach"])
        self.assertLessEqual(out["modeled_realized_equity_proxy_usd"], 970)
        self.assertGreater(out["duplicate_or_risk_vetoed_cases"],0)
        self.assertFalse(out["kraken_funded_challenge_pass_confirmed"])

    def test_cannot_model_future_report_or_enter_before_frozen_capture(self):
        s = frozen("test", BASE)
        r = results(s,entry_time=BASE-timedelta(minutes=1))
        with self.assertRaisesRegex(ValueError,"before original"):
            self.model([s],[r])
        r = results(s)
        r["evaluated_at_utc"] = (ASOF+timedelta(minutes=1)).isoformat()
        with self.assertRaisesRegex(ValueError, "future paper"):
            self.model([s],[r])

    def test_invalid_source_hash_fails_even_if_outcomes_look_profitable(self):
        s = frozen("bad", BASE)
        r = results(s)
        s["rows"][0]["status"] = "PASS"
        with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
            self.model([s],[r])

    def test_reject_over_budget_and_unverified_claims(self):
        s = frozen("budget", BASE)
        r = results(s)
        with self.assertRaisesRegex(ValueError,"per-position cap"):
            self.model([s],[r],risk_usd=6)
        r["broker_trades"] = 1
        with self.assertRaisesRegex(ValueError,"broker-disconnected"):
            self.model([s],[r])

    def test_total_open_planned_risk_and_notional_never_exceed_caps(self):
        snaps=[]
        reports=[]
        for i,coin in enumerate(COINS[:7]):
            snap=frozen(f"overlap-{i}", BASE+timedelta(minutes=i), ticker=coin)
            snaps.append(snap)
            reports.append(results(snap, ticker=coin, status="SHADOW OPEN",
                    entry_time=BASE+timedelta(hours=4,minutes=i)))
        out=self.model(snaps,reports)
        self.assertLessEqual(out["peak_planned_open_risk_usd"], 10)
        self.assertEqual(out["closed_unique_paper_cases"],0)
        self.assertLessEqual(sum(x["hypothetical_position_notional_usd"] for x in out["admissions"]), 1000.001)
        self.assertFalse(out["kraken_funded_challenge_pass_confirmed"])


if __name__ == "__main__":
    unittest.main()
