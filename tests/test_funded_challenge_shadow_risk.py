"""Research-only funded 12%/3% reference risk tests. No broker fills."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
import ast
import math
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
                     "rr": 3 if coin == ticker else None,
                     "meta": ({"swing_eligible": True, "entry_timing_actionable": True,
                               "execution_liquidity_pass_public_spot": True,
                               "funded_bid_ask_verified": False}
                              if coin == ticker else {})})
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
        r = results(s, ticker="UNLISTED")
        out = self.model([s], [r])
        self.assertEqual(out["admitted_unique_paper_cases"], 0)
        self.assertEqual(out["modeled_realized_equity_proxy_usd"], 1000)
        self.assertFalse(out["kraken_funded_challenge_pass_confirmed"])


    def test_forged_paper_target_from_rejected_pass_is_blocked(self):
        s = frozen("no-trades-reject", BASE, ticker="UNLISTED")
        r = results(s)  # Fabricated TARGET on original engine PASS.
        with self.assertRaisesRegex(ValueError, "Rejected original"):
            self.model([s], [r])

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

    def test_stop_not_executable_ten_percent_shock_can_breach_970_floor(self):
        # A mathematically sensible $3 planned stop can still fail a
        # $30 fixed drawdown limit if no native Kraken Funded stop exists.
        source = frozen("stress-without-native-stops", BASE, stop=99.5)
        report = results(source, status="SHADOW OPEN")
        result = self.model([source], [report])
        stress = {
            item["simultaneous_adverse_move_pct"]: item
            for item in result["unprotected_simultaneous_adverse_move_stress"]
        }
        self.assertEqual(result["admitted_unique_paper_cases"], 1)
        self.assertLessEqual(result["peak_planned_open_risk_usd"], 3)
        self.assertEqual(result["modeled_realized_equity_proxy_usd"], 1000)
        self.assertFalse(result["modeled_realized_threshold_breach"])
        self.assertFalse(stress[5.0]["would_touch_fixed_970_floor_in_this_hypothesis"])
        self.assertTrue(stress[10.0]["would_touch_fixed_970_floor_in_this_hypothesis"])
        self.assertLess(stress[10.0]["lowest_model_equity_after_shock_usd"], 970)
        self.assertFalse(result["native_kraken_funded_protective_stops_available"])
        self.assertFalse(result["kraken_funded_challenge_pass_confirmed"])

    def test_no_paper_exposure_yields_unknown_stress_not_a_safety_pass(self):
        source = frozen("no-fake-safety", BASE, ticker="UNLISTED")
        report = results(source, ticker="UNLISTED")
        result = self.model([source], [report])
        self.assertEqual(result["admitted_unique_paper_cases"], 0)
        self.assertEqual(result["peak_simultaneous_paper_notional_usd"], 0)
        for item in result["unprotected_simultaneous_adverse_move_stress"]:
            self.assertIsNone(item["lowest_model_equity_after_shock_usd"])
            self.assertIsNone(item["would_touch_fixed_970_floor_in_this_hypothesis"])
        self.assertTrue(result["stress_is_hypothetical_not_observed_price_path"])

    def test_original_ui_risk_rules_match_research_guard_constants(self):
        from funded_challenge_shadow_risk import (
            START_EQUITY, TARGET_EQUITY, FAIL_EQUITY,
            MAX_SINGLE_RISK_USD, MAX_OPEN_PLANNED_RISK_USD,
            DEFAULT_REDUCED_RISK_USD, STANDARD_A_PLUS_RISK_USD,
            MAX_POSITION_EQUITY_FRACTION, MIN_EQUITY_BUFFER_ABOVE_FLOOR_USD,
        )
        tree = ast.parse(Path(__file__).resolve().parents[1]
                         .joinpath("kraken_funded.py").read_text(encoding="utf-8"))
        original = {}
        for node in tree.body:
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id.startswith("FUNDED_"):
                        if isinstance(node.value, ast.Constant):
                            original[target.id] = node.value.value
        self.assertEqual(original["FUNDED_START_BALANCE"], START_EQUITY)
        self.assertEqual(original["FUNDED_TARGET_BALANCE"], TARGET_EQUITY)
        self.assertEqual(original["FUNDED_FAIL_BALANCE"], FAIL_EQUITY)
        self.assertEqual(original["FUNDED_MAX_RISK_USD"], MAX_SINGLE_RISK_USD)
        self.assertEqual(original["FUNDED_MAX_TOTAL_OPEN_RISK_USD"], MAX_OPEN_PLANNED_RISK_USD)
        self.assertEqual(original["FUNDED_REDUCED_RISK_USD"], DEFAULT_REDUCED_RISK_USD)
        self.assertEqual(original["FUNDED_STANDARD_RISK_USD"], STANDARD_A_PLUS_RISK_USD)
        self.assertEqual(original["FUNDED_MAX_POSITION_PCT"],
                         MAX_POSITION_EQUITY_FRACTION * 100)
        self.assertEqual(MIN_EQUITY_BUFFER_ABOVE_FLOOR_USD, 10)

    def test_funded_ui_account_guard_blocks_combined_risk_into_10_dollar_buffer(self):
        # Execute only the original pure UI guard using isolated AST,
        # without importing Streamlit, account credentials or a broker.
        tree = ast.parse(Path(__file__).resolve().parents[1]
                         .joinpath("kraken_funded.py").read_text(encoding="utf-8"))
        fn = next(n for n in tree.body
                  if isinstance(n, ast.FunctionDef) and n.name == "_account_guard")
        state = {
            "funded_risk_verified": True,
            "funded_account_equity": 985.0,
            "funded_other_open_risk": 3.0,
        }
        scope = {
            "st": SimpleNamespace(session_state=state),
            "np": SimpleNamespace(isfinite=math.isfinite),
            "safe_number": lambda x: float("nan") if x is None else float(x),
            "FUNDED_FAIL_BALANCE": 970.0,
            "FUNDED_MAX_TOTAL_OPEN_RISK_USD": 10.0,
        }
        exec(compile(ast.Module(body=[fn], type_ignores=[]),
                     filename="kraken_funded.py", mode="exec"), scope)
        guard = scope["_account_guard"]
        # Old guard allowed this $3 new position because 985-980>=3
        # and 3 existing + 3 new <= 10. Combined $6 breaches the buffer.
        self.assertIn("ACCOUNT RISK LIMIT", guard(3.0))
        state["funded_other_open_risk"] = 2.0
        self.assertEqual(guard(3.0), "")  # exactly $5 remaining capacity
        state["funded_account_equity"] = 1000.0
        state["funded_other_open_risk"] = 8.0
        self.assertIn("ACCOUNT RISK LIMIT", guard(3.0))
        state["funded_other_open_risk"] = 0.0
        state["funded_risk_verified"] = False
        self.assertIn("VERIFY ACCOUNT", guard(3.0))
        state["funded_risk_verified"] = True
        self.assertIn("ACCOUNT RISK LIMIT", guard(float("nan")))

    def test_low_remaining_equity_blocks_new_paper_trade_before_970_failure(self):
        first = frozen("first-adverse-close", BASE, ticker="BTC")
        second = frozen("second-blocked", BASE+timedelta(minutes=2), ticker="ETH")
        first_outcome = results(
            first, ticker="BTC", status="SHADOW STOP (GAP)",
            entry_time=BASE+timedelta(hours=4),
            exit_time=BASE+timedelta(hours=4, minutes=1),
            exit_price=67,
        )
        second_outcome = results(
            second, ticker="ETH", status="SHADOW OPEN",
            entry_time=BASE+timedelta(hours=4, minutes=2),
        )
        paper = self.model([first, second], [first_outcome, second_outcome])
        self.assertGreater(paper["modeled_realized_equity_proxy_usd"], 970)
        self.assertLess(paper["modeled_realized_equity_proxy_usd"], 983)
        self.assertFalse(paper["modeled_realized_threshold_breach"])
        self.assertEqual(paper["admitted_unique_paper_cases"], 1)
        self.assertEqual(paper["duplicate_or_risk_vetoed_cases"], 1)
        self.assertIn("EQUITY BUFFER", paper["vetoes"][0]["reason"])
        self.assertEqual(paper["ui_equity_buffer_above_fixed_failure_floor_usd"], 10)

    def test_four_dollar_a_plus_budget_and_combined_cap(self):
        source = frozen("a-plus-model", BASE, ticker="BTC")
        report = results(source, status="SHADOW OPEN")
        model = self.model([source], [report], risk_usd=4)
        self.assertEqual(model["risk_budget_per_paper_position_usd"], 4)
        self.assertEqual(model["standard_a_plus_risk_usd"], 4)
        self.assertLessEqual(model["admissions"][0]["planned_risk_including_cost_usd"], 4)

    def test_booked_loss_recalculates_unprotected_stress_while_another_asset_is_open(self):
        first = frozen("first-overlap-loss", BASE, ticker="BTC")
        second = frozen("second-stays-open", BASE+timedelta(minutes=1), ticker="ETH")
        r1 = results(
            first, ticker="BTC", status="SHADOW STOP (GAP)",
            entry_time=BASE+timedelta(hours=4),
            exit_time=BASE+timedelta(hours=4, minutes=4),
            exit_price=50,
        )
        r2 = results(
            second, ticker="ETH", status="SHADOW OPEN",
            entry_time=BASE+timedelta(hours=4, minutes=2),
        )
        out = self.model([first, second], [r1, r2])
        stress = {x["simultaneous_adverse_move_pct"]: x
                  for x in out["unprotected_simultaneous_adverse_move_stress"]}
        self.assertEqual(out["admitted_unique_paper_cases"], 2)
        self.assertGreater(out["modeled_realized_equity_proxy_usd"], 970)
        self.assertLess(stress[10]["lowest_model_equity_after_shock_usd"], 970)
        self.assertTrue(stress[10]["would_touch_fixed_970_floor_in_this_hypothesis"])
        self.assertGreaterEqual(stress[10]["tested_paper_exposure_count"], 3)
        self.assertFalse(out["kraken_funded_challenge_pass_confirmed"])

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

    def test_loss_can_violate_buffer_on_other_open_trade_without_new_entry(self):
        # The first paper stop gaps enough to consume equity headroom, but
        # has NOT yet touched the $970 floor. The second trade remains open.
        # No third entry occurs to retrigger the normal entry-time UI guard.
        first = frozen("buffer-loss-first", BASE, ticker="BTC")
        second = frozen("buffer-left-open", BASE + timedelta(minutes=1), ticker="ETH")
        closed = results(
            first, ticker="BTC", status="SHADOW STOP (GAP)",
            entry_time=BASE + timedelta(hours=4),
            exit_time=BASE + timedelta(hours=4, minutes=4),
            exit_price=67,
        )
        still_open = results(
            second, ticker="ETH", status="SHADOW OPEN",
            entry_time=BASE + timedelta(hours=4, minutes=2),
        )
        out = self.model([first, second], [closed, still_open])
        self.assertEqual(out["admitted_unique_paper_cases"], 2)
        self.assertEqual(out["open_unique_paper_cases"], 1)
        self.assertFalse(out["modeled_realized_threshold_breach"])
        self.assertGreater(out["modeled_realized_equity_proxy_usd"], 970)
        self.assertTrue(out["remaining_position_buffer_breach_after_booked_loss"])
        self.assertEqual(len(out["remaining_position_buffer_breach_events"]), 1)
        event = out["remaining_position_buffer_breach_events"][0]
        self.assertGreater(
            event["remaining_open_planned_risk_usd"],
            event["allowed_open_risk_after_buffer_usd"],
        )
        self.assertEqual(event["remaining_open_assets"], ["crypto:ETH"])
        self.assertTrue(event["not_an_observed_funded_account_event"])
        self.assertFalse(out["kraken_funded_challenge_pass_confirmed"])

    def test_no_buffer_breach_on_zero_paper_positions(self):
        s = frozen("empty-buffer-case", BASE, ticker="UNLISTED")
        out = self.model([s], [results(s, ticker="UNLISTED")])
        self.assertFalse(out["remaining_position_buffer_breach_after_booked_loss"])
        self.assertEqual(out["remaining_position_buffer_breach_events"], [])

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
                status="SHADOW STOP (GAP)", exit_price=40))
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
