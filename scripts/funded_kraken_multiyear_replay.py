"""Offline PR-only multi-year Kraken funded crypto as-of rule replay.

Consumes exact source CSV and SHA-256 manifests extracted from Kraken's
official full-through-Q2-2026 + Q3-2026 archives; obtains public Kraken
native 1w candles ONLY to validate Thursday weekly OHLCV parity.
Every scored decision uses *original* unchanged swing scoring rules with
completed 4h/daily/weekly/BTC as of its own historical clock.

No historical broker fill, funded READY signal, trading authority, or
survivorship-free universe proof. This is descriptive research only.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path

import pandas as pd

from funded_kraken_official_archive import (
    merge_historic_segments, weekly_from_kraken_daily,
    weekly_price_and_volume_parity,
)
from funded_crypto_historical import (
    normalize_bars, replay_coin, independent_mover_episodes, research_exit_proxy,
)
from funded_forward_capture import funded_universe

COLUMNS=("timestamp","open","high","low","close","volume","trades")
CUTOFF=pd.Timestamp("2026-10-01T00:00:00Z")


def validated_source(directory, source, coin, interval):
    manifest_path=Path(directory)/f"{source}_selection_manifest.json"
    metadata=json.loads(manifest_path.read_text(encoding="utf-8"))
    if metadata.get("quarter")!=source or not metadata.get("zip_crc_integrity_verified"):
        raise ValueError("archive origin/CRC provenance not established")
    if metadata.get("source_zip_full_sha256_verified") is not False:
        raise ValueError("archive verification provenance must be explicit")
    item=metadata["selected_sources"].get(f"{coin}:{interval}")
    if not item:
        raise ValueError(f"{coin} missing {source} interval {interval}")
    name=item["file"]
    if Path(name).name!=name or name!=f"{source}_{coin}_{interval}.csv":
        raise ValueError("unsafe or unexpected archive source member filename")
    payload=(Path(directory)/name).read_bytes()
    if sha256(payload).hexdigest()!=item["sha256"]:
        raise ValueError("Kraken source archive member SHA-256 mismatch")
    raw=pd.read_csv(pd.io.common.BytesIO(payload),header=None,names=COLUMNS)
    if raw.empty or list(raw.columns)!=list(COLUMNS):
        raise ValueError("empty or malformed official Kraken seven-column CSV")
    stamp=pd.to_numeric(raw["timestamp"],errors="raise")
    if not stamp.between(1e9,2e10).all():
        raise ValueError("official Kraken timestamps are Unix seconds")
    raw["timestamp"]=pd.to_datetime(stamp,unit="s",utc=True)
    timeframe={"240":"4h","1440":"1d"}[interval]
    frame=normalize_bars(raw[list(COLUMNS[:6])],timeframe)
    if timeframe=="4h":
        if not (frame["timestamp"].dt.hour%4==0).all():
            raise ValueError("malaligned official 4h open timestamp")
    elif not (frame["timestamp"].dt.hour==0).all():
        raise ValueError("malaligned official daily open timestamp")
    return frame,{"coin":coin,"source":source,"interval":interval,
                  "file":name,"sha256":item["sha256"],
                  "first":str(frame["timestamp"].iloc[0]),
                  "last":str(frame["timestamp"].iloc[-1]),
                  "rows":len(frame)}


def joined_source(full_directory,q3_directory,coin):
    full4,m1=validated_source(full_directory,"FULL",coin,"240")
    full1,m2=validated_source(full_directory,"FULL",coin,"1440")
    q4,m3=validated_source(q3_directory,"2026Q3",coin,"240")
    q1,m4=validated_source(q3_directory,"2026Q3",coin,"1440")
    history4=merge_historic_segments(full4,q4,timeframe="4h")
    history1=merge_historic_segments(full1,q1,timeframe="1d")
    history4=history4.loc[history4["timestamp"]<CUTOFF].reset_index(drop=True)
    history1=history1.loc[history1["timestamp"]<CUTOFF].reset_index(drop=True)
    week=weekly_from_kraken_daily(history1)
    return {"4h":history4,"1d":history1,"1w":week},[m1,m2,m3,m4]


def _folds(audits, outcome, *, horizon=20):
    if audits.empty:
        return audits,outcome
    audits=audits.copy()
    outcome=outcome.copy()
    def decision_key(coin,stamp):
        instant=pd.Timestamp(stamp)
        if instant.tzinfo is None:
            raise ValueError("historical decision timestamp lacks timezone")
        return (str(coin),instant.tz_convert("UTC").isoformat())
    mapping={}
    for coin,idx in audits.groupby("Coin",sort=True).groups.items():
        positions=list(idx)
        count=len(positions)
        b1=int(count*.6);b2=int(count*.8)
        for ordinal,row_idx in enumerate(positions):
            fold=("TRAIN" if ordinal<b1 else
                  "VALIDATION" if ordinal<b2 else "UNTOUCHED HOLDOUT")
            mapping[decision_key(coin,audits.loc[row_idx,"Signal open UTC"])]=fold
            audits.loc[row_idx,"Research fold"]=fold
    if not outcome.empty:
        outcome["Research fold"]=[
            mapping.get(decision_key(c,t),"UNKNOWN")
            for t,c in zip(outcome["Decision bar open"],outcome["Coin"])
        ]
        # Decisions are sampled >=42 4h bars apart and horizons are <=20;
        # avoid claiming independence across different coins or thresholds.
    return audits,outcome


async def run(args):
    coins=[x.strip().upper() for x in args.coins.split(",") if x.strip()]
    allowed=set(funded_universe()["crypto"])
    if not coins or set(coins)-allowed or len(coins)!=len(set(coins)):
        raise ValueError("requested coins must be unique approved funded assets")
    if not 1<=args.min_coins<=len(coins):
        raise ValueError("invalid min_coins")
    out=Path(args.out_dir)
    out.mkdir(exist_ok=True,parents=True)
    all_history={}; sources=[]; coverage=[]
    for coin in coins:
        try:
            history,items=joined_source(args.full_dir,args.q3_dir,coin)
            all_history[coin]=history
            sources.extend(items)
            coverage.append({"coin":coin,"status":"SOURCE JOINED",
                             "4h completed rows":len(history["4h"]),
                             "daily rows":len(history["1d"]),
                             "derived full weeks":len(history["1w"]),
                             "first 4h":str(history["4h"]["timestamp"].iloc[0]),
                             "last 4h":str(history["4h"]["timestamp"].iloc[-1])})
        except Exception as exc:
            coverage.append({"coin":coin,"status":"DATA UNAVAILABLE",
                             "reason":f"{type(exc).__name__}: {str(exc)[:190]}"})
    if "BTC" not in all_history:
        raise RuntimeError("required BTC benchmark missing verified official history")

    # Verify reconstructed Thursday-open weeks against authentic native Kraken
    # public weekly candles while there is a real overlapping interval.
    from crypto_market_pipeline import make_exchange,ohlcv_frame
    from funded_crypto_checks import completed_funded_crypto_candles
    exchange=make_exchange("kraken")
    parity={}
    audits=[];outcomes=[];errors=[];paper=[]
    try:
        markets=await asyncio.wait_for(exchange.load_markets(),timeout=70)
        for coin in coins:
            if coin not in all_history:
                continue
            pair=next((name for name in (f"{coin}/USD",f"{coin}/USDT")
                       if name in markets and markets[name].get("spot",True)),None)
            if not pair:
                coverage.append({"coin":coin,"status":"NO CURRENT SPOT PAIR FOR PARITY"})
                continue
            try:
                raw=await asyncio.wait_for(exchange.fetch_ohlcv(
                    pair,timeframe="1w",limit=120),timeout=45)
                reference=completed_funded_crypto_candles(
                    ohlcv_frame(raw),"1w",pd.Timestamp.now(tz="UTC"))
                audit=weekly_price_and_volume_parity(
                    all_history[coin]["1w"],reference,
                    price_rtol=1e-7,max_volume_relative_difference=0.005)
                parity[coin]={**audit,"reference_pair":pair}
                a,o,e=replay_coin(
                    all_history[coin],all_history["BTC"],coin_name=coin,pair=pair,
                    max_anchors=args.max_anchors,
                    anchor_stride=args.stride_4h,
                    min_daily_bars=200,
                )
                if not a.empty:audits.append(a)
                if not o.empty:outcomes.append(o)
                if not e.empty:errors.append(e)
                if not a.empty:
                    px=research_exit_proxy(
                        all_history[coin]["4h"],a,max_hold_bars=20,cost_pct=.8)
                    if not px.empty:paper.append(px)
            except Exception as exc:
                parity[coin]={"status":"FAILED CLOSED",
                              "reason":f"{type(exc).__name__}: {str(exc)[:200]}"}
    finally:
        await exchange.close()
    a=pd.concat(audits,ignore_index=True) if audits else pd.DataFrame()
    o=pd.concat(outcomes,ignore_index=True) if outcomes else pd.DataFrame()
    e=pd.concat(errors,ignore_index=True) if errors else pd.DataFrame()
    proxy=pd.concat(paper,ignore_index=True) if paper else pd.DataFrame()
    a,o=_folds(a,o)
    episode=independent_mover_episodes(o)
    pd.DataFrame(coverage).to_csv(out/"source_coverage.csv",index=False)
    a.to_csv(out/"multi_year_original_asof_decisions.csv",index=False)
    o.to_csv(out/"multi_year_mover_vs_nonmover_controls.csv",index=False)
    e.to_csv(out/"failed_asof_clock_reasons.csv",index=False)
    episode.to_csv(out/"separated_price_episodes.csv",index=False)
    proxy.to_csv(out/"unverified_next_open_proxy_results.csv",index=False)
    summary={
        "mode":"KRAKEN_OFFICIAL_SELECTED_HISTORY_RESEARCH_ONLY",
        "source_archive_versions":["FULL to June 2026","2026Q3 to September 2026"],
        "source_full_zip_sha256_verified":False,
        "individual_csv_sha256s_verified":True,
        "joined_coins":sorted(all_history),
        "fully_audited_coins":sorted(a["Coin"].unique()) if not a.empty else [],
        "native_kraken_weekly_parity":parity,
        "original_historical_asof_clocks":len(a),
        "technical_states":dict(Counter(a["Original technical state"])) if not a.empty else {},
        "unverified_research_buys":int(
            (a["Research class"]=="PROXY_BUY_UNVERIFIED").sum()) if not a.empty else 0,
        "real_funded_trades_confirmed":0,
        "historical_source_ohlcv":sources,
        "sparse_stride_4h":args.stride_4h,
        "max_historical_decisions_per_coin":args.max_anchors,
        "daily_indicator_warmup":200,
        "outcome_rows_descriptive":len(o),
        "censored_errors":len(e),
        "overlapping_threshold_episode_rows":len(episode),
        "risk_caveats":[
            "Historical Kraken OHLCV records do not verify archived Kraken Funded market availability",
            "Current spot pair check only; historical ticker volume approximated by rolling completed 4h candles",
            "Per-quarter ZIP full-file SHA256 not checked; only saved selected member SHA and ZIP CRC",
            "No historical bid-ask, slippage, earnings or funded account-equity/official challenge reconstruction",
            "Unchanged original signal rules; out-of-sample fold labels NOT license to tune on holdout",
            "No automated trading and no strategy expected profit claim",
        ],
    }
    (out/"research_manifest.json").write_text(
        json.dumps(summary,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    print(json.dumps({k:v for k,v in summary.items()
                      if k not in {"historical_source_ohlcv","risk_caveats"}},
                     sort_keys=True))
    if len(summary["fully_audited_coins"])<args.min_coins:
        raise RuntimeError("Insufficient verified as-of Kraken coins after source/weekly parity")
    return summary


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--full-dir",required=True)
    p.add_argument("--q3-dir",required=True)
    p.add_argument("--out-dir",default="funded-kraken-multiyear-research")
    p.add_argument("--coins",default="BTC,ETH,SOL,NEAR,HBAR")
    p.add_argument("--max-anchors",type=int,default=250)
    p.add_argument("--stride-4h",type=int,default=42)
    p.add_argument("--min-coins",type=int,default=3)
    args=p.parse_args()
    asyncio.run(run(args))


if __name__=="__main__":
    main()
