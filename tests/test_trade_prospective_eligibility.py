"""Paper evidence cannot be manufactured by a high score or venue listing."""
import copy
import unittest

from trade_prospective_eligibility import eligibility


def future_source():
    return {"capture_created_utc":"2026-10-29T16:00:00+00:00",
            "observations":[{
                "symbol":"AAPL","venue":"nasdaq",
                "first_captured_utc":"2026-10-29T16:00:00+00:00",
                "source_session":"2026-10-28",
                "source_validation":"PASS",
                "technical_state":"ENTRY READY",
            },{
                "symbol":"AZN.L","venue":"lse",
                "first_captured_utc":"2026-10-29T16:00:00+00:00",
                "source_session":None,
                "source_validation":"BLOCKED",
                "technical_state":"UNAVAILABLE",
            }]}


def first_party_listing():
    return {"listing":{"status":"OFFICIAL_FORMAT_CURRENT_LISTING_EVIDENCE_ONLY",
                       "listing_symbols_observed":[{
                           "symbol":"AAPL","exchange_listing_observed":True,
                       }]},"price_feed_independently_authenticated":False}


class RealPaperEligibilityTests(unittest.TestCase):
    def test_high_technical_state_and_official_listing_not_a_trade(self):
        out=eligibility(future_source(),first_party_listing())
        self.assertEqual(out["source_cohort_size"],2)
        self.assertEqual(out["approved_paper_entries"],0)
        self.assertEqual(out["recorded_fills"],0)
        a=out["rows"][0]
        self.assertEqual(a["technical_state_original"],"ENTRY READY")
        self.assertTrue(a["exchange_listing_source_matched"])
        self.assertFalse(a["independently_authorized_price_verified"])
        self.assertFalse(a["paper_fill_observed"])
        self.assertIn("AUTHORIZED_PRIMARY_OR_LICENSED_CONSOLIDATED_DAILY_OHLCV",
                      a["blocking_evidence"])

    def test_uk_symbol_does_not_leak_nasdaq_listing(self):
        out=eligibility(future_source(),first_party_listing())
        a=out["rows"][1]
        self.assertFalse(a["exchange_listing_source_matched"])
        self.assertIn("NO_FIRST_PARTY_CURRENT_NASDAQ_LISTING_MATCH",a["blocking_evidence"])

    def test_fake_public_price_claim_has_no_effect_on_entry(self):
        listing=first_party_listing()
        listing["price_feed_independently_authenticated"]=True
        source=future_source()
        source["observations"][0]["approved_paper_entry"]=True
        result=eligibility(source,listing)
        self.assertEqual(result["approved_paper_entries"],0)
        self.assertFalse(result["rows"][0]["paper_order_submitted"])

    def test_no_genuine_frozen_cohort_fail_closed(self):
        with self.assertRaises(ValueError):
            eligibility({"observations":[]},first_party_listing())


if __name__=="__main__":
    unittest.main()
