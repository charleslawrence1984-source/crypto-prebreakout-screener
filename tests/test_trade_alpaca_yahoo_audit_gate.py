"""Synthetic QA: operational failures must stay red; research anomalies are visible warnings."""
import copy
import unittest
from trade_alpaca_yahoo_audit_gate import evaluate

NAMES=("AAPL","MSFT","NVDA","GOOGL","AMZN")

def specimen():
    return {
        "credentials_present":True,"rights_acknowledged":True,"paper_account_verified":True,
        "public_data_licence_verified":False,"production_signals_modified":False,
        "paper_or_live_orders":False,"subscriptions_started":False,
        "matched_session_pairs":1260,
        "rows":[{
            "symbol":name,"status":"RESEARCH_PAIRED_WITHIN_TOLERANCE",
            "matched_sessions":252,"missing_yahoo_dates":[],"extra_yahoo_dates":[],
            "field_mismatch_counts":{k:0 for k in ("o","h","l","c","v")},
            "technical_condition_disagreements":{"prior_20d_high_break":0,"above_50d_mean":0},
            "approved_trade_signal":False,"paper_or_live_order":False,
        } for name in NAMES]}

class GateTests(unittest.TestCase):
    def test_all_clean_is_completed_not_exchange_certified(self):
        yes,status,rows=evaluate(specimen())
        self.assertTrue(yes)
        self.assertIn("NOT_PRICE_CERTIFIED",status)
        self.assertEqual(len(rows),5)
    def test_confirmed_volume_discrepancy_is_successful_RESEARCH_but_needs_review(self):
        d=specimen()
        d["rows"][2]["field_mismatch_counts"]["v"]=6
        d["rows"][2]["status"]="REVIEW_PRICE_VOLUME_OR_TRIGGER_SENSITIVITY"
        yes,status,rows=evaluate(d)
        self.assertTrue(yes)
        self.assertIn("REVIEW_REQUIRED",status)
        self.assertFalse(rows[2]["approved_trade_signal"])
    def test_trigger_sensitivity_is_warning_not_trading(self):
        d=specimen()
        d["rows"][1]["technical_condition_disagreements"]["prior_20d_high_break"]=2
        d["rows"][1]["status"]="REVIEW_PRICE_VOLUME_OR_TRIGGER_SENSITIVITY"
        yes,status,_=evaluate(d)
        self.assertTrue(yes)
        self.assertIn("REVIEW_REQUIRED",status)
    def test_api_failure_still_red(self):
        d=specimen()
        d["rows"][1]["status"]="BLOCKED_SOURCE_NETWORK_FAILURE"
        self.assertFalse(evaluate(d)[0])
    def test_missing_day_still_red(self):
        d=specimen()
        d["rows"][1]["matched_sessions"]=251
        self.assertFalse(evaluate(d)[0])
    def test_unpaired_day_still_red(self):
        d=specimen()
        d["rows"][1]["missing_yahoo_dates"]=["2026-10-28"]
        self.assertFalse(evaluate(d)[0])
    def test_incomplete_cohort_still_red(self):
        d=specimen()
        d["rows"].pop()
        self.assertFalse(evaluate(d)[0])
    def test_paper_account_unverified_still_red(self):
        d=specimen()
        d["paper_account_verified"]=False
        self.assertFalse(evaluate(d)[0])
    def test_unsafe_order_claim_still_red(self):
        d=specimen()
        d["rows"][0]["paper_or_live_order"]=True
        self.assertFalse(evaluate(d)[0])
    def test_keys_missing_still_red(self):
        d=specimen()
        d["credentials_present"]=False
        self.assertFalse(evaluate(d)[0])
    def test_public_licence_claim_still_red(self):
        d=specimen()
        d["public_data_licence_verified"]=True
        self.assertFalse(evaluate(d)[0])
    def test_anomaly_hiding_without_review_state_not_green(self):
        # A declared clean row with anomaly counts is not a permission to trade,
        # but the report must not pretend the research was clean either.
        d=specimen()
        d["rows"][0]["field_mismatch_counts"]["v"]=1
        yes,status,_=evaluate(d)
        self.assertTrue(yes)
        self.assertIn("REVIEW_REQUIRED",status)
    def test_prior_2_attempts_same_eight_flags_allowed_for_research_only(self):
        d=specimen()
        for idx,n in ((1,1),(2,6),(3,1)):
            d["rows"][idx]["field_mismatch_counts"]["v"]=n
            d["rows"][idx]["status"]="REVIEW_PRICE_VOLUME_OR_TRIGGER_SENSITIVITY"
        self.assertEqual(evaluate(d)[1],"RESEARCH_COMPLETED_REVIEW_REQUIRED_NOT_PRICE_CERTIFIED")
        self.assertFalse(any(x["approved_trade_signal"] for x in d["rows"]))

if __name__=="__main__":
    unittest.main()
