"""Free delayed consolidated daily prices require actual credentialed 15m+ data.

Synthetic fixtures only: never count these as authenticated price observations.
"""
from __future__ import annotations

import copy
import json
import unittest

import pandas as pd

from trade_free_alpaca_reference import read_one_symbol,normalize_daily_bars,cross_check_yahoo,run

NOW=pd.Timestamp("2026-10-29T15:55:00Z")


def history(periods=900):
    dates=pd.bdate_range(end="2026-10-28",periods=periods)
    result=[]
    for date in dates:
        day=date.strftime("%Y-%m-%d")
        result.append({"t":day+"T04:00:00Z","o":100.0,"h":101.0,
                       "l":99.0,"c":100.5,"v":1000000})
    return result


class FakeResponse:
    def __init__(self,body,status=200):
        self._body=body
        self.status_code=status
    def json(self):
        return copy.deepcopy(self._body)


class FakeAlpaca:
    def __init__(self,responses):
        self.responses=list(responses)
        self.calls=[]
    def get(self,url,params,headers,timeout,allow_redirects):
        self.calls.append({
            "url":url, "params":copy.deepcopy(params),
            "headers":copy.deepcopy(headers),
            "timeout":timeout, "allow_redirects":allow_redirects,
        })
        return self.responses.pop(0)


class FreeSIPSourceTests(unittest.TestCase):
    def test_missing_credentials_no_network_or_cost(self):
        network=FakeAlpaca([])
        row=read_one_symbol("AAPL",key_id="",secret="",at=NOW,session=network)
        self.assertEqual(row["status"],"BLOCKED_NO_FREE_ALPACA_API_CREDENTIALS")
        self.assertEqual(network.calls,[])
        self.assertFalse(row["live_trade_buy_authorized"])
        self.assertFalse(row["attempted_trading_order"])

    def test_no_unapproved_symbols_and_no_wrong_country(self):
        net=FakeAlpaca([])
        x=read_one_symbol("AZN.L",key_id="fixture",secret="fixture",at=NOW,session=net)
        self.assertEqual(x["status"],"UNAPPROVED_SAMPLE_SYMBOL")
        self.assertEqual(net.calls,[])

    def test_realistic_credentials_are_only_used_as_request_headers(self):
        net=FakeAlpaca([FakeResponse({"bars":history()[:600],"next_page_token":"next"}),
                        FakeResponse({"bars":history()[600:],"next_page_token":None})])
        row=read_one_symbol("AAPL",key_id="FAKE_TEST_KEY",secret="FAKE_TEST_SECRET",
                            at=NOW,session=net)
        self.assertEqual(row["status"],
                         "PROVIDER_SIP_SESSION_STRUCTURALLY_VALID_PENDING_COMPARISON")
        self.assertEqual(row["completed_session"],"2026-10-28")
        self.assertGreaterEqual(row["validated_completed_bars"],252)
        self.assertEqual(row["pages"],2)
        self.assertEqual(len(row["source_ohlcv_sha256"]),64)
        self.assertFalse(row["licence_for_public_display_or_redistribution"])
        self.assertTrue(row["authorized_price_research_authenticated"])
        self.assertFalse(row["paper_order_authorized"])
        self.assertEqual(len(net.calls),2)
        a=net.calls[0]
        self.assertEqual(a["params"]["feed"],"sip")
        self.assertEqual(a["params"]["adjustment"],"raw")
        self.assertEqual(a["params"]["timeframe"],"1Day")
        self.assertLess(pd.Timestamp(a["params"]["end"]),NOW-pd.Timedelta(minutes=15))
        self.assertNotIn("FAKE_TEST_SECRET",json.dumps(row))
        self.assertTrue(all(q["url"]=="https://data.alpaca.markets/v2/stocks/AAPL/bars"
                            for q in net.calls))
        self.assertFalse(a["allow_redirects"])

    def test_free_plan_entitlement_denial_is_final(self):
        net=FakeAlpaca([FakeResponse({"message":"not permitted"},422)])
        row=read_one_symbol("AAPL",key_id="key",secret="secret",at=NOW,session=net)
        self.assertEqual(row["status"],"BLOCKED_PROVIDER_NOT_ENTITLED")
        self.assertEqual(len(net.calls),1)
        self.assertFalse(row["authorized_price_research_authenticated"])

    def test_source_pagination_exhaustion_is_blocked_not_truncated(self):
        net=FakeAlpaca([FakeResponse({"bars":history()[:300],"next_page_token":"x"})]*4)
        row=read_one_symbol("AAPL",key_id="key",secret="secret",at=NOW,session=net)
        self.assertEqual(row["status"],"BLOCKED_PAGINATION_INTEGRITY")

    def test_missing_bar_is_not_a_verified_completed_session(self):
        net=FakeAlpaca([FakeResponse({"bars":history()[:-1],"next_page_token":None})])
        row=read_one_symbol("AAPL",key_id="key",secret="secret",at=NOW,session=net)
        self.assertEqual(row["status"],"BLOCKED_STALE_OR_MISSING_COMPLETED_BAR")

    def test_malformed_geometry_can_never_be_exchanged_for_raw_yahoo(self):
        bars=history()
        bars[100]["h"]=95.0
        net=FakeAlpaca([FakeResponse({"bars":bars,"next_page_token":None})])
        row=read_one_symbol("AAPL",key_id="key",secret="secret",at=NOW,session=net)
        self.assertIn("INVALID_OHLCV_PRICE_OR_VOLUME",row["status"])
        self.assertFalse(row["live_trade_buy_authorized"])

    def test_incomplete_latest_session_is_excluded(self):
        bars=history()
        bars.append({"t":"2026-10-29T04:00:00Z",
                     "o":100,"h":102,"l":99,"c":101,"v":500})
        row=read_one_symbol("AAPL",key_id="key",secret="secret",at=NOW,
                            session=FakeAlpaca([FakeResponse({"bars":bars,"next_page_token":None})]))
        self.assertEqual(row["status"],"PROVIDER_SIP_SESSION_STRUCTURALLY_VALID_PENDING_COMPARISON")
        self.assertTrue(row["calendar_check"]["excluded_incomplete_latest_bar"])
        self.assertEqual(row["completed_session"],"2026-10-28")

    def test_252_same_closes_not_exchange_certification(self):
        a=normalize_daily_bars(history())
        b=a.copy(deep=True)
        answer=cross_check_yahoo(a,b)
        self.assertEqual(answer["status"],"PAIRWISE_CLOSES_WITHIN_TOLERANCE_NOT_EXCHANGE_CERTIFICATION")
        self.assertFalse(answer["same_venue_session_prices_proven"])
        self.assertFalse(answer["licensed_for_publication"])
        self.assertEqual(answer["cross_source_comparison_sessions"],252)

    def test_five_year_fixture_provider_history_is_actually_cross_checked_with_yahoo(self):
        source=history()
        comparison=normalize_daily_bars(source)
        result=read_one_symbol("MSFT",key_id="TEST_KEY",secret="TEST_SECRET",at=NOW,
            session=FakeAlpaca([FakeResponse({"bars":source,"next_page_token":None})]),
            reference_history=comparison)
        self.assertEqual(
            result["cross_vendor_check"]["status"],
            "PAIRWISE_CLOSES_WITHIN_TOLERANCE_NOT_EXCHANGE_CERTIFICATION")
        self.assertEqual(result["cross_vendor_check"]["cross_source_comparison_sessions"],252)
        self.assertFalse(result["cross_vendor_check"]["licensed_for_publication"])
        self.assertFalse(result["live_trade_buy_authorized"])

    def test_cross_vendor_large_difference_remains_review_only(self):
        source=history()
        reference=normalize_daily_bars(source)
        reference.iloc[-1,reference.columns.get_loc("Close")]=90.0
        result=read_one_symbol("MSFT",key_id="TEST_KEY",secret="TEST_SECRET",at=NOW,
            session=FakeAlpaca([FakeResponse({"bars":source,"next_page_token":None})]),
            reference_history=reference)
        self.assertEqual(
            result["cross_vendor_check"]["status"],
            "SOURCE_MISMATCH_REQUIRES_HUMAN_REVIEW")
        self.assertEqual(result["paper_order_authorized"],False)

    def test_large_mismatch_quarantines_without_patching(self):
        a=normalize_daily_bars(history())
        b=a.copy(deep=True)
        b.iloc[-1,b.columns.get_loc("Close")]=80.0
        x=cross_check_yahoo(a,b,max_close_diff_bps=50)
        self.assertEqual(x["status"],"SOURCE_MISMATCH_REQUIRES_HUMAN_REVIEW")
        self.assertIn("2026-10-28",x["mismatched_sessions"])

    def test_run_without_env_auth_never_claims_free_prices(self):
        import os
        from unittest.mock import patch
        with patch.dict(os.environ,{
            "ALPACA_KEY_ID":"",
            "ALPACA_SECRET_KEY":"",
            "ALPACA_PERSONAL_RESEARCH_RIGHTS_CONFIRMED":"0",
        }):
            x=run()
        self.assertEqual(x["independent_price_session_authenticated"],0)
        self.assertEqual(x["paper_entry_approved"],0)
        self.assertFalse(x["license_public_website"])
        self.assertTrue(all(v["status"]=="BLOCKED_NO_FREE_ALPACA_API_CREDENTIALS"
                            for v in x["source_rows"]))


if __name__=="__main__":
    unittest.main()
