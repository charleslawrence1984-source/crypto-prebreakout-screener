"""READ-ONLY Kraken Funded stock EOD vendor publication comparison.

NEVER alters main Yahoo feed, benchmark, NYSE last-completed-session rule,
original strategy, stock BUY states or scheduled workflows. Stooq is an
experimental research comparator, not an authorised production fallback.
"""
from __future__ import annotations

from datetime import datetime, timezone
import io
import json
from pathlib import Path
from urllib.request import urlopen, Request

import pandas as pd

from funded_stock_data import expected_completed_nyse_session

SYMBOLS = ("AAPL","MSFT","NVDA","SPY")
BASE = "https://stooq.com/q/d/l/"


def freshness_result(source, symbol, rows, *, expected):
    if rows is None or rows.empty:
        return {"Source":source,"Symbol":symbol,"State":"DATA UNAVAILABLE",
                "Reason":"No provider OHLCV","Expected session":str(expected)}
    value=rows.copy()
    if "Date" in value:
        times=pd.to_datetime(value["Date"],errors="raise")
    else:
        times=pd.to_datetime(value.index,errors="raise")
    if isinstance(times,pd.Series):
        latest=times.max()
    else:
        latest=times.max()
    if pd.isna(latest):
        raise ValueError("invalid provider session date")
    last=latest.date()
    if "Close" not in value or "Volume" not in value:
        raise ValueError("no close/volume")
    price=pd.to_numeric(value["Close"],errors="coerce")
    volume=pd.to_numeric(value["Volume"],errors="coerce")
    if price.isna().any() or (price<=0).any() or volume.isna().any() or (volume<0).any():
        raise ValueError("impossible provider market data")
    if last>expected:
        state="FUTURE_OR_IN_PROGRESS_BAR"
    elif last<expected:
        state="STALE"
    else:
        state="EXPECTED_SESSION_PRESENT"
    return {"Source":source,"Symbol":symbol,"State":state,
            "Expected session":str(expected),"Last provider session":str(last),
            "Last provider close":float(price.iloc[-1]),
            "Dataset count":len(value),
            "Research comparison only":True}


def stooq_history(symbol, *, start, end, opener=urlopen):
    if symbol not in SYMBOLS:
        raise ValueError("Stooq pilot symbol not on whitelist")
    query=f"{BASE}?s={symbol.lower()}.us&i=d&d1={start:%Y%m%d}&d2={end:%Y%m%d}"
    request=Request(query,headers={"User-Agent":"Mozilla/5.0 CLSignal EOD research",
                                   "Accept":"text/csv"})
    with opener(request,timeout=20) as r:
        raw=r.read(2_000_000)
    if not raw or len(raw)>=2_000_000:
        raise ValueError("Stooq CSV absent/too large")
    return pd.read_csv(io.BytesIO(raw))


def yahoo_history(symbol):
    import yfinance as yf
    result=yf.download(symbol,period="14d",interval="1d",auto_adjust=False,
                       progress=False,threads=False)
    if isinstance(result.columns,pd.MultiIndex):
        result.columns=result.columns.get_level_values(0)
    return result


def make_report(*, now=None, primary=yahoo_history, comparator=stooq_history):
    now=pd.Timestamp.now(tz="UTC") if now is None else pd.Timestamp(now)
    expected=expected_completed_nyse_session(now)
    results=[]
    for symbol in SYMBOLS:
        for source,fn in (("Yahoo",primary),("Stooq comparator",comparator)):
            try:
                if source.startswith("Stooq"):
                    bars=fn(symbol,start=now.date()-pd.Timedelta(days=15),
                            end=now.date())
                else:
                    bars=fn(symbol)
                item=freshness_result(source,symbol,bars,expected=expected)
            except Exception as exc:
                item={"Source":source,"Symbol":symbol,"State":"DATA UNAVAILABLE",
                      "Expected session":str(expected),
                      "Reason":f"{type(exc).__name__}: {str(exc)[:160]}"}
            results.append(item)
    return pd.DataFrame(results),{
        "as_of_utc":now.isoformat(),"NYSE expected completed session":str(expected),
        "scope":"PUBLIC PROVIDER CAPABILITY RESEARCH ONLY",
        "auto_switch_provider":False,"live_strategy_changed":False,
        "vendor_reliability_proven":False,
        "official_kraken_funded_quote_verified":False,
        "caveat":"One snapshot cannot prove publication timeliness across market closes",
    }


def main():
    import argparse
    p=argparse.ArgumentParser()
    p.add_argument("--out-dir",default="funded-stock-provider-study")
    args=p.parse_args()
    frame,meta=make_report()
    out=Path(args.out_dir);out.mkdir(parents=True,exist_ok=True)
    frame.to_csv(out/"independent_vendor_session_comparison.csv",index=False)
    (out/"metadata.json").write_text(json.dumps(meta,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"states":frame.groupby(["Source","State"]).size().to_dict()
                      if False else frame[["Source","Symbol","State"]].to_dict("records"),
                      "expected":meta["NYSE expected completed session"],
                      "changed_live_feed":False},sort_keys=True))
    # Do NOT treat a provider outage or an after-close stale bar as success.
    # CI preserves diagnostic artifact for operator review.
    if (frame["State"]=="EXPECTED_SESSION_PRESENT").sum()==0:
        raise RuntimeError("No sample vendor had latest completed NYSE bar")
    return 0


if __name__=="__main__":
    raise SystemExit(main())
