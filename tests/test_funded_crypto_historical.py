"""No-hindsight crypto multi-timeframe historical Kraken replay invariants."""
import unittest

import numpy as np
import pandas as pd

from funded_crypto_historical import (
    normalize_bars, score_asof, replay_coin, independent_mover_episodes,
    research_exit_proxy,
)


def candles(starts, *, price=100.0):
    n=len(starts)
    return pd.DataFrame({
        "timestamp":starts,"open":np.full(n,price),
        "high":np.full(n,price+2),"low":np.full(n,price-2),
        "close":np.full(n,price),"volume":np.full(n,2000.0),
    })


def fixture():
    dates4=pd.date_range("2026-01-01",periods=720,freq="4h",tz="UTC")
    datesd=pd.date_range("2026-01-01",periods=150,freq="1D",tz="UTC")
    datesw=pd.date_range("2025-12-29",periods=36,freq="7D",tz="UTC")
    obj={"4h":candles(dates4),"1d":candles(datesd),"1w":candles(datesw)}
    return obj,{k:v.copy(deep=True) for k,v in obj.items()},dates4


def mock_flat(item,result):
    return {"Swing status":("BUY" if result.get("ready") else "WATCH"),
            "Swing score":result["score"],"Swing reason":"synthetic",
            "Entry timing":"RETEST","Swing eligible":True,
            "Entry timing actionable":True,"Current R:R":3.0,
            "Active entry":100.0,"Invalidation":98.0,
            "First technical target":110.0,"RS vs BTC %":3.0}


class CryptoAsOfTests(unittest.TestCase):
    def test_today_and_future_daily_weekly_candles_never_visible(self):
        coin,btc,dates=fixture()
        observations=[]
        def mock(c4,d,bc,cfg,dfw,btcd):
            observations.append((c4.copy(),d.copy(),bc.copy(),dfw.copy(),btcd.copy()))
            return {"score":82,"ready":True}
        t=dates[610]
        result=score_asof(coin,btc,coin_name="ETH",pair="ETH/USD",
                          bar_open=t,scorer=mock,flattener=mock_flat)
        c4,d,b4,w,bd=observations[0]
        clock=t+pd.Timedelta(hours=4,minutes=1)
        for data,delta in ((c4,"4h"),(d,"1d"),(b4,"4h"),(w,"1w"),(bd,"1d")):
            self.assertTrue((data["timestamp"]+{"4h":pd.Timedelta(hours=4),
                "1d":pd.Timedelta(days=1),"1w":pd.Timedelta(days=7)}[delta]
                +pd.Timedelta(minutes=1)<=clock).all())
        self.assertEqual(c4["timestamp"].iloc[-1],t)
        self.assertEqual(b4["timestamp"].iloc[-1],t)
        self.assertEqual(result["Research class"],"PROXY_BUY_UNVERIFIED")
        self.assertFalse(result["Historical Kraken Funded broker eligible"])
        self.assertEqual(len(c4),180)

    def test_future_prices_do_not_rewrite_past_original_decision(self):
        coin,btc,dates=fixture()
        def mock(c4,d,b4,cfg,dfw,btcd):
            return {"score":90 if c4["close"].iloc[-1]>95 else 50,
                    "ready":c4["close"].iloc[-1]>95}
        t=dates[610]
        first=score_asof(coin,btc,coin_name="SOL",pair="SOL/USD",
                        bar_open=t,scorer=mock,flattener=mock_flat)
        coin["4h"].loc[615,"close"]=77.0
        coin["4h"].loc[615,"low"]=75.0
        coin["1d"].loc[105,"close"]=76.0
        coin["1d"].loc[105,"low"]=74.0
        coin["1w"].loc[20,"close"]=78.0
        coin["1w"].loc[20,"low"]=76.0
        again=score_asof(coin,btc,coin_name="SOL",pair="SOL/USD",
                         bar_open=t,scorer=mock,flattener=mock_flat)
        self.assertEqual(first,again)

    def test_missing_4h_history_and_gaps_fail_closed(self):
        coin,btc,dates=fixture()
        coin["4h"]=coin["4h"].drop(index=606).reset_index(drop=True)
        with self.assertRaisesRegex(ValueError,"gapped recent 4h"):
            score_asof(coin,btc,coin_name="ETH",pair="ETH/USD",
                       bar_open=dates[610],scorer=lambda *a,**k:{},
                       flattener=mock_flat)

    def test_missing_weekly_and_btc_clock_gaps_cannot_approve(self):
        coin,btc,dates=fixture()
        coin["1w"]=coin["1w"].iloc[:6]
        with self.assertRaises(ValueError):
            score_asof(coin,btc,coin_name="ETH",pair="ETH/USD",
                       bar_open=dates[610],scorer=lambda *a,**k:{},
                       flattener=mock_flat)
        coin,btc,dates=fixture()
        btc["4h"]=btc["4h"].drop(index=609).reset_index(drop=True)
        with self.assertRaisesRegex(ValueError,"gapped recent 4h"):
            score_asof(coin,btc,coin_name="ETH",pair="ETH/USD",
                       bar_open=dates[610],scorer=lambda *a,**k:{},
                       flattener=mock_flat)

    def test_rolling_historical_spot_volume_not_present_ticker(self):
        coin,btc,dates=fixture()
        t=dates[610]
        def scorer(c4,d,b4,cfg,dfw,btcd):
            return {"score":90,"ready":True}
        old=score_asof(coin,btc,coin_name="ETH",pair="ETH/USD",
                       bar_open=t,scorer=scorer,flattener=mock_flat)
        coin["4h"].loc[611:615,"volume"]=999999999.0
        again=score_asof(coin,btc,coin_name="ETH",pair="ETH/USD",
                         bar_open=t,scorer=scorer,flattener=mock_flat)
        self.assertEqual(old["24h OHLCV quote volume USD proxy"],
                         again["24h OHLCV quote volume USD proxy"])
        self.assertFalse(old["Real historical ticker volume verified"])

    def test_replay_only_future_closes_label_movers(self):
        coin,btc,dates=fixture()
        t=dates[610]
        def scorer(c4,d,b4,cfg,dfw,btcd):
            return {"score":90,"ready":True}
        a,b,errors=replay_coin(coin,btc,coin_name="ETH",pair="ETH/USD",
                              max_anchors=115,horizons=(5,),
                              thresholds=(10,),scorer=scorer,flattener=mock_flat)
        self.assertFalse(a.empty)
        self.assertFalse(b.empty)
        before=b.set_index("Decision bar open")["Later mover"]
        coin["4h"].loc[612,"close"]=140.0
        coin["4h"].loc[612,"high"]=145.0
        a2,b2,errors2=replay_coin(coin,btc,coin_name="ETH",pair="ETH/USD",
                              max_anchors=115,horizons=(5,),
                              thresholds=(10,),scorer=scorer,flattener=mock_flat)
        key=str(t)
        self.assertFalse(bool(before.loc[key]))
        self.assertTrue(bool(b2.set_index("Decision bar open").loc[key,"Later mover"]))
        self.assertEqual(a.set_index("Signal open UTC").loc[t.isoformat(),"Research class"],
                         a2.set_index("Signal open UTC").loc[t.isoformat(),"Research class"])

    def test_exit_same_bar_stop_and_target_always_stop_first(self):
        coin,btc,dates=fixture()
        history=coin["4h"].iloc[:7].copy()
        history.loc[history.index[1],"high"]=115.0
        history.loc[history.index[1],"low"]=96.0
        audit=pd.DataFrame([{
            "Coin":"ETH","Signal open UTC":dates[0].isoformat(),
            "Research class":"PROXY_BUY_UNVERIFIED",
            "Stop proxy":98.0,"Target proxy":110.0,
        }])
        result=research_exit_proxy(history,audit,max_hold_bars=4)
        self.assertEqual(result.iloc[0]["Outcome reason"],"STOP FIRST (TARGET MAY ALSO HIT)")
        self.assertLess(result.iloc[0]["Net R proxy"],0)
        self.assertFalse(result.iloc[0]["Kraken Funded fill verified"])

    def test_normalize_does_not_silently_fix_bad_prices_or_duplicates(self):
        coin,btc,dates=fixture()
        f=coin["4h"].copy()
        f.loc[1,"high"]=90.0
        with self.assertRaisesRegex(ValueError,"invalid"):
            normalize_bars(f,"4h")
        f=coin["4h"].copy()
        f.loc[1,"timestamp"]=f.loc[0,"timestamp"]
        with self.assertRaisesRegex(ValueError,"duplicate"):
            normalize_bars(f,"4h")

    def test_original_watch_pass_and_proxy_buy_remain_distinct(self):
        coin,btc,dates=fixture()
        clock=dates[610]
        for source,expected in (
            ("WATCH","WATCH"),("PASS","REJECTED_PASS"),
            ("BUY","PROXY_BUY_UNVERIFIED"),("BOGUS","UNKNOWN")
        ):
            def flat(item, result):
                return {"Swing status":source,"Swing score":65.0,
                        "Swing reason":"synthetic",
                        "Swing eligible":False}
            row=score_asof(
                coin,btc,coin_name="ETH",pair="ETH/USD",bar_open=clock,
                scorer=lambda *a,**kw:{"score":65},
                flattener=flat,
            )
            self.assertEqual(row["Research class"],expected)
            self.assertFalse(row["Historical Kraken Funded broker eligible"])

    def test_mover_episode_cooldown_limits_repeated_winner_anchors(self):
        coin,btc,dates=fixture()
        t=dates[610]
        def scorer(*a,**k):
            return {"score":80,"ready":False}
        coin["4h"].loc[612,"high"]=142.0
        coin["4h"].loc[612,"close"]=140.0
        a,b,errors=replay_coin(coin,btc,coin_name="ETH",pair="ETH/USD",
                              max_anchors=115,horizons=(5,),
                              thresholds=(10,),scorer=scorer,flattener=mock_flat)
        ep=independent_mover_episodes(b)
        self.assertLessEqual(len(ep),len(b[b["Later mover"]]))
        self.assertTrue((ep["Not an independently executed trade"]).all())


if __name__=="__main__":
    unittest.main()
