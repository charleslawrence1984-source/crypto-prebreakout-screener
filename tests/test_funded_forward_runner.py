"""Headless funded forward-runner smoke tests with mocked PUBLIC provider data.

This checks routing and non-trading records. It does not simulate Kraken fills,
execute trades, or establish crypto/stock strategy profitability.
"""
import asyncio
import sys
import types
import unittest
from unittest.mock import patch, MagicMock

import pandas as pd
import numpy as np

import funded_forward_runner as runner
from funded_forward_capture import create_snapshot

NOW=pd.Timestamp.now(tz="UTC")


class MockKraken:
    def __init__(self):
        self.markets={"BTC/USD":{},"ETH/USD":{}}
        self.closes=0

    async def load_markets(self):
        return self.markets

    async def fetch_tickers(self):
        return {
            "BTC/USD":{"quoteVolume":2000000},
            "ETH/USD":{"quoteVolume":2000000},
        }

    async def fetch_ohlcv(self,symbol,timeframe,limit):
        return [symbol,timeframe,limit]

    async def close(self):
        self.closes+=1


class RunnerReadOnlyTests(unittest.TestCase):
    def test_stock_collector_reuses_original_trade_core_without_broker(self):
        dates=pd.bdate_range(end=pd.Timestamp.now(tz="America/New_York").tz_localize(None),periods=280)
        candle=pd.DataFrame({
            "Open":np.ones(280)*100,"High":np.ones(280)*102,
            "Low":np.ones(280)*98,"Close":np.ones(280)*100,
            "Volume":np.ones(280)*1000000,
        },index=dates)
        yahoo=types.SimpleNamespace(download=lambda *args,**kwargs:candle.copy())
        technical={"technical_state":"PRE-CROSS READY",
                   "technical_reason":"WAITING FOR CONFIRMATION",
                   "technical_score":78,
                   "entry":100.0,"stop":95.0,"target":115.0,"reward_risk":3.0}
        with patch.dict(sys.modules,{"yfinance":yahoo}):
            with patch.object(runner,"get_validated_stock_history",
                              return_value=(candle,"BATCH","PASS")):
                with patch.object(runner,"evaluate_price_setup",return_value=technical):
                    result=runner.collect_stocks(("NVDA",))
        self.assertEqual(len(result),1)
        self.assertEqual(result[0]["status"],"PRE-CROSS READY")
        self.assertIn("evaluate_price_setup",result[0]["meta"]["origin_engine"])
        self.assertFalse(result[0]["meta"]["funded_bid_ask_verified"])

    def test_stocks_provider_failures_are_rows_not_hidden(self):
        calls=[]
        def download(*args,**kwargs):
            calls.append(args[0])
            return pd.DataFrame()
        yahoo=types.SimpleNamespace(download=download)
        with patch.dict(sys.modules,{"yfinance":yahoo}):
            records=runner.collect_stocks(("NVDA","AAPL"))
        self.assertEqual(calls, ["^GSPC", "^GSPC"])
        self.assertEqual([r["asset"] for r in records], ["NVDA","AAPL"])
        self.assertTrue(all(r["status"]=="DATA UNAVAILABLE" for r in records))
        self.assertTrue(all("BENCHMARK" in r["reason"] for r in records))

    def test_stale_benchmark_retries_and_never_falsely_scores(self):
        from funded_stock_data import clean_stock_daily_frame
        monday=pd.Timestamp("2026-10-19 18:00",tz="America/New_York")
        stale=pd.DataFrame({
            "Open":[100.]*280,"High":[102.]*280,"Low":[99.]*280,
            "Close":[101.]*280,"Volume":[1_000_000]*280,
        },index=pd.bdate_range(end="2026-10-16",periods=280))
        calls=[]
        def download(*args,**kwargs):
            calls.append(args[0])
            return stale.copy()
        yahoo=types.SimpleNamespace(download=download)
        def validate(frame,now):
            return clean_stock_daily_frame(frame,monday)
        with patch.dict(sys.modules,{"yfinance":yahoo}):
            with patch.object(runner,"clean_stock_daily_frame",side_effect=validate):
                with patch.object(runner,"evaluate_price_setup",
                                  side_effect=AssertionError("stale benchmark must not score")):
                    rows=runner.collect_stocks(("NVDA","AAPL"))
        self.assertEqual(calls, ["^GSPC","^GSPC"])
        self.assertEqual([r["asset"] for r in rows], ["NVDA","AAPL"])
        self.assertTrue(all(r["status"]=="DATA UNAVAILABLE" for r in rows))
        self.assertTrue(all("STALE PRICE DATA" in r["reason"] for r in rows))

    def test_benchmark_retry_can_restore_fresh_research(self):
        from funded_stock_data import clean_stock_daily_frame
        monday=pd.Timestamp("2026-10-19 18:00",tz="America/New_York")
        dates=pd.bdate_range(end="2026-10-19",periods=280)
        candles=pd.DataFrame({
            "Open":[100.]*280,"High":[102.]*280,"Low":[99.]*280,
            "Close":[101.]*280,"Volume":[1_000_000]*280,
        },index=dates)
        attempts=[]
        def download(*args,**kwargs):
            attempts.append(args[0])
            if args[0]=="^GSPC" and attempts.count("^GSPC")==1:
                return pd.DataFrame()
            return candles.copy()
        yahoo=types.SimpleNamespace(download=download)
        technical={"technical_state":"BLOCKED",
                   "technical_reason":"mocked technical evaluation"}
        with patch.dict(sys.modules,{"yfinance":yahoo}):
            with patch.object(runner,"clean_stock_daily_frame",
                              side_effect=lambda frame,now:clean_stock_daily_frame(frame,monday)):
                with patch.object(runner,"get_validated_stock_history",
                                  return_value=(candles,"BATCH","PASS")):
                    with patch.object(runner,"evaluate_price_setup",return_value=technical):
                        with patch.object(runner,"evaluate_funded_breakout",return_value=None):
                            with patch.object(runner,"_trend_watch_from_original_source",
                                              return_value=lambda *args:None):
                                rows=runner.collect_stocks(("NVDA",))
        self.assertEqual(attempts[:2], ["^GSPC","^GSPC"])
        self.assertEqual(rows[0]["status"], "BLOCKED")
        self.assertNotIn("DATA UNAVAILABLE", [r["status"] for r in rows])

    def test_crypto_collector_uses_original_scoring_but_never_claims_broker_quote(self):
        market=MockKraken()
        fake_market_module=types.SimpleNamespace(
            make_exchange=lambda provider:market,
            ohlcv_frame=lambda data:pd.DataFrame({
                "timestamp":[NOW-pd.Timedelta(hours=8)] * 75,
                "open":[1.0]*75,"high":[2.0]*75,"low":[.5]*75,
                "close":[1.5]*75,"volume":[1e6]*75,
            }),
            flatten_deep_score=lambda item,result:{
                "Swing status":"BUY","Swing reason":"synthetic completed candle",
                "Swing score":93,"Entry timing":"RETEST",
                "Entry timing actionable":True,"Swing eligible":True,
                "Active entry":2,"Invalidation":1.8,
                "First technical target":2.8,"Current R:R":4.0,
            },
        )
        fake_rules=types.SimpleNamespace(
            ScreenerConfig=lambda **kw:kw,
            score_setup=lambda *args,**kwargs:{"score":93},
        )
        def candles(frame,timeframe,now):
            return frame
        with patch.dict(sys.modules,{
            "crypto_market_pipeline":fake_market_module,
            "crypto_rule_engine":fake_rules,
        }):
            with patch.object(runner,"completed_funded_crypto_candles",side_effect=candles):
                captured=asyncio.run(runner.collect_crypto(("BTC","ETH","UNLISTED")))
        self.assertEqual(market.closes,1)
        self.assertEqual(len(captured),3)
        self.assertEqual(captured[0]["status"],"BUY")
        self.assertFalse(captured[0]["meta"]["funded_bid_ask_verified"])
        self.assertEqual(captured[2]["status"],"DATA UNAVAILABLE")
        payload=create_snapshot("crypto",captured,observed_at=NOW.to_pydatetime(),
                                run_id="fake-only",universe={"crypto":("BTC","ETH","UNLISTED")})
        self.assertTrue(all(r["trade_authorization"].startswith("RESEARCH ONLY")
                            for r in payload["rows"]))


if __name__=="__main__":
    unittest.main()
