"""Actual Trade scanner/feed source publication safeguards, isolated fixtures."""
from __future__ import annotations

import ast
from datetime import datetime,timezone
import unittest
from pathlib import Path

import pandas as pd

from trade_source_publication import (
    cached_source_assessment, protect_scan_result_rows,
    research_only_cached_trade_frame,
)

NOW=datetime(2026,10,29,15,tzinfo=timezone.utc)
ROOT=Path(__file__).resolve().parents[1]


class EndToEndSourcePublicationTests(unittest.TestCase):
    def test_existing_cached_without_bar_receipt_explicit_wait(self):
        data=pd.DataFrame([{
            "Ticker":"AAPL","Technical state":"ENTRY READY",
            "Technical score":97,"Liquidity gate":"PASS",
        }])
        saved=data.copy(deep=True)
        output=research_only_cached_trade_frame(data,"nasdaq",NOW)
        self.assertEqual(output.iloc[0]["Status"],"WAIT")
        self.assertEqual(output.iloc[0]["Original technical status (research)"],"ENTRY READY")
        self.assertEqual(output.iloc[0]["Source check reason"],"MISSING_COMPLETED_EXCHANGE_SESSION")
        self.assertEqual(output.iloc[0]["Approved Trade action"],"WAIT")
        pd.testing.assert_frame_equal(data,saved)

    def test_fake_self_declared_session_pass_does_not_authenticate(self):
        info={
            "Ticker":"MSFT","Technical state":"AWAITING NEXT OPEN",
            "Last completed exchange session":"2026-10-28",
            "Price session status":"PASS","Completed bar OHLCV SHA256":"a"*64,
            "Price independently exchange verified":True,
        }
        check=cached_source_assessment(info,"nasdaq",NOW)
        self.assertEqual(check["status"],"SOURCE_CLAIM_PRESENT_NOT_AUTHENTICATED")
        self.assertFalse(check["approved_trade_buy"])
        self.assertFalse(check["price_independently_exchange_verified"])
        saved=research_only_cached_trade_frame(pd.DataFrame([info]),"nasdaq",NOW)
        self.assertEqual(saved.iloc[0]["Status"],"WAIT")

    def test_false_session_and_unsupported_venue_cannot_pass(self):
        info={"Last completed exchange session":"2026-10-27","Price session status":"PASS"}
        self.assertEqual(cached_source_assessment(info,"nasdaq",NOW)["reason"],
                         "STALE_FUTURE_OR_MISMATCHED_EXCHANGE_SESSION")
        self.assertEqual(cached_source_assessment(info,"gettex",NOW)["reason"],
                         "UNSUPPORTED_OR_UNAVAILABLE_EXCHANGE_CALENDAR")

    def test_watch_paper_and_ready_market_scan_all_wait(self):
        rows=pd.DataFrame([
            {"Ticker":"AAPL","Status":"PAPER CANDIDATE","Reason":"all gates passed"},
            {"Ticker":"MSFT","Status":"WATCH","Reason":"needs crossover"},
            {"Ticker":"NVDA","Status":"READY TO VERIFY","Reason":"model only"},
            {"Ticker":"GOOGL","Status":"BLOCKED","Reason":"misses RSI"},
        ])
        checks={ticker:{"status":"PASS","last_completed_session":"2026-10-28",
                       "expected_completed_session":"2026-10-28",
                       "last_completed_ohlcv_sha256":"c"*64}
                for ticker in rows["Ticker"]}
        output=protect_scan_result_rows(rows,checks,NOW)
        self.assertEqual(list(output["Status"]),["WAIT","WAIT","WAIT","BLOCKED"])
        self.assertEqual(list(output["Approved Trade action"]),["WAIT"]*4)
        self.assertEqual(output.iloc[0]["Research-only original reason"],"all gates passed")
        self.assertTrue((output["Independent exchange authentication"]==False).all())
        self.assertFalse((output["Status"]=="PAPER CANDIDATE").any())

    def test_original_status_preserved_without_promoting_source(self):
        original=pd.DataFrame([{"Ticker":"AAPL","Status":"WATCH",
                                "Technical state":"WATCH"}])
        out=research_only_cached_trade_frame(original,"nasdaq",NOW)
        self.assertEqual(out.iloc[0]["Original technical status (research)"],"WATCH")
        self.assertEqual(out.iloc[0]["Status"],"WAIT")

    def test_actual_scanner_evaluates_validated_ohlcv_not_raw_yahoo(self):
        src=(ROOT/"stock_app.py").read_text(encoding="utf-8")
        fn=src[src.index("def approved_trade_market_scan("):
               src.index("\n\ndef ",src.index("def approved_trade_market_scan(")+5)]
        self.assertIn("validated, receipt = validate_completed_trade_frame(",fn)
        self.assertIn("technical = evaluate_price_setup(validated, benchmark)",fn)
        self.assertIn("benchmark, benchmark_evidence = validate_completed_trade_frame(",fn)
        self.assertIn("output = protect_scan_result_rows(output, source_checks, captured_at)",fn)
        self.assertNotIn("technical = evaluate_price_setup(frame, benchmark)",fn)

    def test_actual_cached_feed_calls_source_contract_after_state_mapping(self):
        src=(ROOT/"stock_app.py").read_text(encoding="utf-8")
        fn=src[src.index("def load_all_trade_opportunities("):
               src.index("\n\ndef ",src.index("def load_all_trade_opportunities(")+5)]
        self.assertIn("technical = research_only_cached_trade_frame(technical, kind)",fn)
        mapping=fn.index('technical["Status"] = technical["Technical state"].map(')
        guard=fn.index("technical = research_only_cached_trade_frame(technical, kind)")
        self.assertGreater(guard,mapping)

if __name__=="__main__":
    unittest.main()
