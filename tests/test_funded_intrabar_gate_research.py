"""Unit-test stricter Kraken APP account equity and no-hindsight gated cohorts."""
from __future__ import annotations

import unittest
import pandas as pd

from funded_intrabar_risk_research import simulate_intrabar_account
from funded_single_gate_observational import observational_gate_conditioning


def bars(*, low=95, start="2025-01-01T04:00:00Z", n=25):
    dates=pd.date_range(start,periods=n,freq="4h",tz="UTC")
    f=pd.DataFrame({"timestamp":dates,"open":[100.] * n,
                    "high":[105.] * n,"low":[low] * n,
                    "close":[100.] * n,"volume":[1000.] * n})
    return f.set_index("timestamp",drop=False)


def trade(**overrides):
    return {
        "Coin":"ETH","Signal bar":"2025-01-01T00:00:00Z",
        "Counterfactual cohort":"ORIGINAL_BUY","Decision":"MODELED_EXIT",
        "Cost roundtrip %":.8,"Next 4h open proxy":100.,
        "Original stop":99.,"Net R proxy":1.,
        "Bars to exit":2, **overrides,
    }


def gate(**overrides):
    return {
        "Coin":"ETH","Historical 4h open UTC":"2025-01-01T00:00:00Z",
        "Original historical research class":"WATCH",
        "Original research fold":"TRAIN",
        "4h MACD histogram improving":True,
        "4h MACD histogram above zero":False,
        "Number of resistance tests >=2":True,
        "Shape original true":True,
        "RSI >80":False, **overrides,
    }


def risk(**overrides):
    return {
        "Coin":"ETH","Signal bar":"2025-01-01T00:00:00Z",
        "Counterfactual cohort":"WATCH_READY_BYPASS",
        "Cost roundtrip %":.8,"Decision":"MODELED_EXIT",
        "Net R proxy":-.8,"Exit reason":"STOP", **overrides,
    }


class IntrabarAndGateTests(unittest.TestCase):
    def test_public_low_can_touch_fixed_floor_despite_positive_close(self):
        source=bars(low=70)
        results,summary=simulate_intrabar_account(
            pd.DataFrame([trade()]),{"ETH":source},risk_pct=1.0)
        self.assertEqual(len(results),1)
        self.assertEqual(summary["Account outcome"],"NO_CLOSED_EQUITY_THRESHOLD")
        self.assertEqual(summary["Possible intrabar public low crossings"],1)
        self.assertTrue(results.iloc[0]["Possible 3% loss-line contact (public proxy)"])
        self.assertFalse(summary["Real Funded account outcome verified"])

    def test_no_leverage_caps_planned_risk_on_tight_stop(self):
        source=bars(low=99)
        rows,summary=simulate_intrabar_account(
            pd.DataFrame([trade(**{"Original stop":99.98})]),
            {"ETH":source},risk_pct=1.)
        self.assertLessEqual(rows.iloc[0]["Notional % pre-trade equity"],100.0001)
        self.assertLess(rows.iloc[0]["Planned original stop-risk USD"],10)
        self.assertTrue(summary["Strict unlevered capital cap"])

    def test_gapped_future_never_inferred_as_benign_market(self):
        source=bars().drop(pd.Timestamp("2025-01-01T08:00:00Z"))
        with self.assertRaisesRegex(ValueError,"future 4h series missing or gapped"):
            simulate_intrabar_account(pd.DataFrame([trade()]),{"ETH":source})

    def test_paper_entry_differs_from_frozen_source_open_fails_closed(self):
        source=bars()
        with self.assertRaisesRegex(ValueError,"next-open changed"):
            simulate_intrabar_account(
                pd.DataFrame([trade(**{"Next 4h open proxy":105.})]),
                {"ETH":source})

    def test_cost_and_floor_static_for_partial_capital(self):
        source=bars(low=90)
        rows,summary=simulate_intrabar_account(
            pd.DataFrame([trade(**{"Net R proxy":-5., "Original stop":98.})]),
            {"ETH":source},risk_pct=1.)
        self.assertEqual(summary["Fixed starting-equity floor"],970.)
        self.assertEqual(summary["Fixed starting-equity profit target"],1120.)
        self.assertFalse(rows.iloc[0]["Actual intrabar stop execution or quote verified"])

    def test_reject_broker_unvalidated_no_data_return(self):
        _,summary=simulate_intrabar_account(
            pd.DataFrame([trade()]),{"ETH":bars()},cohort="WATCH_READY_BYPASS")
        self.assertEqual(summary["Account outcome"],"NO_PAPER_ENTRIES")
        self.assertFalse(summary["Real Funded account outcome verified"])

    def test_one_feature_observed_not_a_causal_relaxation(self):
        features=pd.DataFrame([gate()])
        exits=pd.DataFrame([risk()])
        join,summary=observational_gate_conditioning(features,exits)
        self.assertEqual(len(join),1)
        selected=summary.loc[
            (summary["Frozen diagnostic feature"]=="MACD_HISTOGRAM_IMPROVING")
            & (summary["Feature passed at original historical clock"]==True)]
        self.assertEqual(int(selected["Observed stop-first paper examples"].iloc[0]),1)
        self.assertLess(float(selected["Mean hypothetical R"].iloc[0]),0)
        self.assertFalse(summary["Independent single-rule causal change demonstrated"].any())
        self.assertFalse(summary["Originally unqualified events promoted to BUY"].any())

    def test_original_pass_cannot_be_mapped_to_hypothetical_watch_entry(self):
        with self.assertRaisesRegex(ValueError,"counterfactual cohort mutated"):
            observational_gate_conditioning(
                pd.DataFrame([gate(**{"Original historical research class":"REJECTED_PASS"})]),
                pd.DataFrame([risk()]))

    def test_feature_boolean_false_string_remains_false(self):
        df=pd.DataFrame([gate(**{"4h MACD histogram improving":"False"})])
        _,report=observational_gate_conditioning(df,pd.DataFrame([risk()]))
        selected=report.loc[
            (report["Frozen diagnostic feature"]=="MACD_HISTOGRAM_IMPROVING")
            & (report["Feature passed at original historical clock"]==False)]
        self.assertEqual(int(selected["Observed stop-first paper examples"].iloc[0]),1)

    def test_malformed_string_feature_cannot_be_interpreted_as_true(self):
        with self.assertRaisesRegex(ValueError,"boolean must be true"):
            observational_gate_conditioning(
                pd.DataFrame([gate(**{"4h MACD histogram improving":"Unknown"})]),
                pd.DataFrame([risk()]))


if __name__=="__main__":
    unittest.main()
