"""Four Kraken Funded research workstream integrity checks; no live orders."""
import unittest
import pandas as pd

from funded_app_challenge_scenarios import simulate_closed_equity, OFFICIAL_APP_FUNDED
from funded_gate_reason_evidence import gate_report, reason_category
from funded_forward_evidence_qa import reconcile
from funded_stock_vendor_probe import freshness_result


def row(r=-1.,t="2025-01-01T00:00:00Z",cohort="ORIGINAL_BUY"):
    return {"Coin":"ETH","Signal bar":t,"Research fold":"TRAIN",
            "Counterfactual cohort":cohort,"Decision":"MODELED_EXIT",
            "Cost roundtrip %":.8,"Bars to exit":2,"Net R proxy":r}


class FundedFourPrioritiesTests(unittest.TestCase):
    def test_funded_is_not_kraken_prop_daily_reset_or_margin(self):
        self.assertEqual(OFFICIAL_APP_FUNDED["target_pct"],12)
        self.assertEqual(OFFICIAL_APP_FUNDED["static_max_loss_pct"],3)
        self.assertFalse(OFFICIAL_APP_FUNDED["daily_loss_reset"])
        self.assertFalse(OFFICIAL_APP_FUNDED["advanced_stop_orders"])
        self.assertFalse(OFFICIAL_APP_FUNDED["leverage_permitted"])
        self.assertEqual(OFFICIAL_APP_FUNDED["minimum_spread_one_side_pct"],.04)

    def test_challenge_fixed_floor_and_close_only_not_pass_verified(self):
        d=pd.DataFrame([row(r=-6.)])
        report,summary=simulate_closed_equity(d,start_balance=1000,risk_pct=.5)
        self.assertEqual(summary["status"],"CLOSED_EQUITY_BREACH_PROXY")
        self.assertEqual(summary["final_closed_equity_proxy"],970)
        self.assertEqual(summary["floor"],970)
        self.assertFalse(summary["pass_verified"])
        self.assertFalse(summary["intrabar_breach_checked"])
        self.assertFalse(summary["funded_account_execution_verified"])

    def test_closed_equity_profit_target_not_claimed_as_real_pass(self):
        _,summary=simulate_closed_equity(
            pd.DataFrame([row(r=25.)]),risk_pct=1.)
        self.assertEqual(summary["status"],"CLOSED_EQUITY_TARGET_PROXY")
        self.assertFalse(summary["pass_verified"])

    def test_serial_positions_ignore_overlapping_original_decisions(self):
        d=pd.DataFrame([row(r=1.,t="2025-01-01T00:00:00Z"),
                        row(r=-1.,t="2025-01-01T04:00:00Z")])
        _,summary=simulate_closed_equity(d)
        self.assertEqual(summary["chosen_serial_positions"],1)
        self.assertEqual(summary["overlapping_skipped"],1)

    def test_no_closed_positions_cannot_show_false_zero_loss(self):
        _,summary=simulate_closed_equity(pd.DataFrame([{
            **row(),"Decision":"AWAITING FUTURE CANDLES"}]))
        self.assertEqual(summary["status"],"NO_HISTORICAL_CLOSED_TRADES")
        self.assertFalse(summary["pass_verified"])

    def test_reason_label_does_not_claim_isolated_macd_effect(self):
        self.assertEqual(reason_category("Too far below resistance; execution: research"),
                         "RESISTANCE_DISTANCE")
        decisions=pd.DataFrame([
            {"Coin":"ETH","Signal open UTC":"2025-01-01T00:00:00Z",
             "Research class":"WATCH","Original reason":"Bullish retest is holding",
             "Research fold":"TRAIN","Original technical score":81,
             "Original R:R":2.0,"24h OHLCV quote volume USD proxy":2e6},
            {"Coin":"ETH","Signal open UTC":"2025-01-08T00:00:00Z",
             "Research class":"REJECTED_PASS","Original reason":"Too far below resistance",
             "Research fold":"TRAIN","Original technical score":74,
             "Original R:R":None,"24h OHLCV quote volume USD proxy":2e6},
        ])
        outcomes=pd.DataFrame([
            {"Coin":"ETH","Decision bar open":"2025-01-01T00:00:00Z",
             "Horizon 4h bars":20,"Mover threshold %":10,"Later mover":True},
            {"Coin":"ETH","Decision bar open":"2025-01-08T00:00:00Z",
             "Horizon 4h bars":20,"Mover threshold %":10,"Later mover":False},
        ])
        risk=pd.DataFrame([{"Coin":"ETH","Signal bar":"2025-01-01T00:00:00Z",
             "Cost roundtrip %":.8,"Decision":"MODELED_EXIT",
             "Counterfactual cohort":"WATCH_READY_BYPASS",
             "Net R proxy":-.8,"Exit reason":"STOP"}])
        joined,groups=gate_report(decisions,outcomes,risk)
        self.assertEqual(len(joined),2)
        self.assertEqual(groups["Future +10% close movers"].sum(),1)
        self.assertEqual(groups["Future +10% close nonmovers"].sum(),1)
        self.assertFalse(groups["Independent single-gate causal effect identified"].any())
        self.assertFalse(groups["MACD gate time-series provided"].any())

    def test_gate_join_missing_outcome_refuses_missing_as_negative(self):
        decisions=pd.DataFrame([{
            "Coin":"ETH","Signal open UTC":"2025-01-01T00:00:00Z",
            "Research class":"WATCH","Original reason":"Bullish retest",
            "Research fold":"TRAIN","Original technical score":81,
            "Original R:R":2.0,"24h OHLCV quote volume USD proxy":2e6,
        }])
        outcomes=pd.DataFrame(columns=[
            "Coin","Decision bar open","Horizon 4h bars","Mover threshold %","Later mover"])
        risk=pd.DataFrame(columns=["Coin","Signal bar","Cost roundtrip %",
                                    "Decision","Counterfactual cohort","Net R proxy","Exit reason"])
        with self.assertRaisesRegex(ValueError,"missing outcomes"):
            gate_report(decisions,outcomes,risk)

    def test_stock_comparison_requires_exchange_session_not_calendar_age(self):
        f=pd.DataFrame([{"Date":"2026-10-20","Open":100,"Close":101,"Volume":50}])
        d=pd.Timestamp("2026-10-21").date()
        self.assertEqual(freshness_result("Yahoo","AAPL",f,expected=d)["State"],
                         "STALE")
        f.loc[0,"Date"]="2026-10-21"
        self.assertEqual(freshness_result("Stooq comparator","AAPL",f,expected=d)["State"],
                         "EXPECTED_SESSION_PRESENT")
        f.loc[0,"Date"]="2026-10-22"
        self.assertEqual(freshness_result("Yahoo","AAPL",f,expected=d)["State"],
                         "FUTURE_OR_IN_PROGRESS_BAR")

    def test_stock_vendor_bad_bars_never_certified_fresh(self):
        bad=pd.DataFrame([{"Date":"2026-10-21","Close":-1,"Volume":100}])
        with self.assertRaisesRegex(ValueError,"impossible"):
            freshness_result("Yahoo","AAPL",bad,
                 expected=pd.Timestamp("2026-10-21").date())

    def test_prospective_overlap_and_delayed_journal_not_profits(self):
        funnel={"research_only":True,"broker_trades_verified":0,
                "audited_at_utc":"2026-10-22T08:32:15Z",
                "research_entry_trigger_observations":[
                    {"asset":"CRV","asset_type":"crypto",
                     "captured_at_utc":"2026-10-21T20:22:23Z"}],
                "observed_crypto_gaps_over_5_5h":[{"gap_hours":7.0}]}
        paper={"research_only":True,"broker_trades_executed":0,
               "evaluated_at_utc":"2026-10-22T01:46:04Z",
               "original_technical_trigger_observations":1,
               "performance_evidence":{"distinct_closed_paper_positions":0}}
        res=reconcile(funnel,paper)
        self.assertIn("MISSED_RESEARCH_COVERAGE_WINDOWS",res["risks"])
        self.assertIn("OUTCOME_JOURNAL_OLDER_THAN_FUNNEL",res["risks"])
        self.assertFalse(res["profitability_established"])
        self.assertFalse(res["broker_funded_readiness_confirmed"])

    def test_forward_duplicate_trigger_is_not_second_new_trade(self):
        funnel={"research_only":True,"broker_trades_verified":0,
                "audited_at_utc":"2026-10-22T08:32:15Z",
                "research_entry_trigger_observations":[
                    {"asset":"CRV","asset_type":"crypto",
                     "captured_at_utc":"2026-10-21T20:22:23Z"}]*2,
                "observed_crypto_gaps_over_5_5h":[]}
        paper={"research_only":True,"broker_trades_executed":0,
               "evaluated_at_utc":"2026-10-22T01:46:04Z",
               "original_technical_trigger_observations":2,
               "performance_evidence":{"distinct_closed_paper_positions":0}}
        with self.assertRaisesRegex(ValueError,"duplicate"):
            reconcile(funnel,paper)


if __name__=="__main__":
    unittest.main()
