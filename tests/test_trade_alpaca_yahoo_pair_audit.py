"""Synthetic-only audit regressions; never claim verified Alpaca or Yahoo prices."""
import unittest
from datetime import datetime, timedelta, timezone
from trade_alpaca_yahoo_pair_audit import (
    Blocked, audit, compare, alpaca_normalize, yahoo_normalize,
    verify_paper, fetch_alpaca, fetch_yahoo)

NOW=datetime(2026,10,29,18,25,tzinfo=timezone.utc)
ENV={"ALPACA_KEY_ID":"FAKE","ALPACA_SECRET_KEY":"FAKE_SECRET",
     "ALPACA_PERSONAL_RESEARCH_RIGHTS_CONFIRMED":"1"}

def sample(n=252):
    start=datetime(2025,1,1,tzinfo=timezone.utc)
    return {(start+timedelta(days=i)).date().isoformat():
            {"o":100.0,"h":101.0,"l":99.0,"c":100.5,"v":100000.0} for i in range(n)}
def dates():
    return list(sample())

class FakeResponse:
    def __init__(self,doc):
        import json
        self.content=json.dumps(doc).encode()
        self.status=200
    def __enter__(self):return self
    def __exit__(self,*args):return False
    def read(self,n):return self.content[:n]

class FakeNetwork:
    def __init__(self):self.calls=[]
    def __call__(self,request,timeout):
        self.calls.append(request)
        if "paper-api" in request.full_url:
            return FakeResponse({"id":"FAKE_PAPER_ACCOUNT"})
        if "data.alpaca.markets" in request.full_url:
            bars=[dict({"t":day+"T04:00:00Z"},**bar) for day,bar in sample().items()]
            return FakeResponse({"bars":bars,"next_page_token":None})
        raise AssertionError("No Yahoo requests expected in this fake")

class Tests(unittest.TestCase):
    def test_no_secret_no_network(self):
        f=FakeNetwork()
        r=audit(now=NOW,environ={},opener=f)
        self.assertEqual(f.calls,[])
        self.assertFalse(r["paper_or_live_orders"])
        self.assertFalse(r["paper_account_verified"])
    def test_consent_missing_no_network(self):
        f=FakeNetwork()
        r=audit(now=NOW,environ={**ENV,"ALPACA_PERSONAL_RESEARCH_RIGHTS_CONFIRMED":"0"},opener=f)
        self.assertEqual(f.calls,[])
        self.assertIn("BLOCKED",r["overall_status"])
    def test_paper_account_first_get_only(self):
        f=FakeNetwork()
        verify_paper("FAKE","FAKE_SECRET",f)
        self.assertEqual(len(f.calls),1)
        self.assertEqual(f.calls[0].get_method(),"GET")
        self.assertNotIn("FAKE_SECRET",f.calls[0].full_url)
    def test_alpaca_feed_and_delayed_raw_only(self):
        f=FakeNetwork()
        result=fetch_alpaca("AAPL",NOW,"FAKE","FAKE_SECRET",f)
        self.assertEqual(len(result),252)
        url=f.calls[0].full_url
        self.assertIn("feed=sip",url)
        self.assertIn("adjustment=raw",url)
        self.assertIn("timeframe=1Day",url)
        self.assertNotIn("FAKE_SECRET",url)
    def test_exact_252_clean_does_not_authorize_trades(self):
        x=compare(sample(),sample())
        self.assertEqual(x["status"],"RESEARCH_PAIRED_WITHIN_TOLERANCE")
        self.assertEqual(x["matched_sessions"],252)
        self.assertFalse(x["approved_trade_signal"])
        self.assertFalse(x["paper_or_live_order"])
    def test_missing_yahoo_middle_date_cannot_be_skipped(self):
        a,b=sample(),sample()
        del b[dates()[100]]
        x=compare(a,b)
        self.assertEqual(x["status"],"BLOCKED_UNPAIRED_PROVIDER_SESSIONS")
        self.assertEqual(x["matched_sessions"],251)
        self.assertIn(dates()[100],x["missing_yahoo_dates"])
    def test_yahoo_extra_day_cannot_be_skipped(self):
        a,b=sample(),sample()
        del a[dates()[50]]
        a["2024-12-31"]=next(iter(a.values()))
        x=compare(a,b)
        self.assertEqual(x["status"],"BLOCKED_UNPAIRED_PROVIDER_SESSIONS")
        self.assertIn(dates()[50],x["extra_yahoo_dates"])
    def test_close_gap_requires_review(self):
        a,b=sample(),sample()
        b[dates()[-1]]=dict(b[dates()[-1]],c=90)
        x=compare(a,b)
        self.assertEqual(x["status"],"REVIEW_PRICE_VOLUME_OR_TRIGGER_SENSITIVITY")
        self.assertEqual(x["field_mismatch_counts"]["c"],1)
        self.assertEqual(a[dates()[-1]]["c"],100.5)
    def test_volume_gap_requires_review(self):
        a,b=sample(),sample()
        b[dates()[-1]]=dict(b[dates()[-1]],v=80000)
        x=compare(a,b)
        self.assertEqual(x["field_mismatch_counts"]["v"],1)
        self.assertEqual(x["field_mismatch_counts"]["c"],0)
    def test_corporate_action_is_context_not_automatic_waiver(self):
        a,b=sample(),sample()
        b[dates()[-1]]=dict(b[dates()[-1]],c=90)
        x=compare(a,b,[dates()[-1]])
        self.assertIn(dates()[-1],x["corporate_action_dates"])
        self.assertNotEqual(x["status"],"RESEARCH_PAIRED_WITHIN_TOLERANCE")
    def test_breakout_difference_is_recorded_not_trade(self):
        a,b=sample(),sample()
        a[dates()[-1]]=dict(a[dates()[-1]],c=102,h=103)
        x=compare(a,b)
        self.assertEqual(x["technical_condition_disagreements"]["prior_20d_high_break"],1)
        self.assertFalse(x["approved_trade_signal"])
    def test_50day_average_difference_is_recorded(self):
        a,b=sample(),sample()
        for day in dates()[-50:-1]: a[day]=dict(a[day],c=100)
        x=compare(a,b)
        self.assertGreater(x["technical_condition_disagreements"]["above_50d_mean"],0)
    def test_insufficient_history_blocked(self):
        x=compare(dict(list(sample().items())[:251]),sample())
        self.assertEqual(x["status"],"BLOCKED_INSUFFICIENT_252_ALPACA_SESSIONS")
    def test_duplicate_alpaca_bar_is_blocked(self):
        row={"t":"2026-10-28T04:00:00Z","o":100,"h":101,"l":99,"c":100.5,"v":10}
        with self.assertRaises(Blocked):alpaca_normalize([row,row],"2026-10-29")
    def test_today_incomplete_bar_excluded(self):
        row={"t":"2026-10-29T04:00:00Z","o":100,"h":101,"l":99,"c":100.5,"v":10}
        self.assertEqual(alpaca_normalize([row],"2026-10-29"),{})
    def test_unadjusted_yahoo_quote_close_not_adjusted_close(self):
        ts=int(datetime(2026,10,28,15,tzinfo=timezone.utc).timestamp())
        d={"chart":{"result":[{"timestamp":[ts],
           "indicators":{"quote":[{"open":[100],"high":[101],"low":[99],"close":[100.5],"volume":[100]}],
           "adjclose":[{"adjclose":[90]}]},
           "events":{"splits":{"split1":{"date":ts}}}}]}}
        x, actions=yahoo_normalize(d,"2026-10-29")
        self.assertEqual(x["2026-10-28"]["c"],100.5)
        self.assertIn("2026-10-28",actions)

if __name__=="__main__":
    unittest.main()
