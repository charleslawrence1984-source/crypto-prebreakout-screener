"""PR-only public Kraken spot crypto research; NO orders or live rule changes.

Bounded source history from Kraken via CCXT, not full historical membership or
true historic executable Kraken Funded quotes. ALL raw input snapshots, symbols,
timeframes and source fingerprints are preserved for reproducibility.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path

import pandas as pd

from funded_forward_capture import funded_universe
from funded_crypto_historical import (
    normalize_bars, replay_coin, independent_mover_episodes,
    research_exit_proxy,
)


async def fetch(exchange, pair, timeframe):
    data = await asyncio.wait_for(
        exchange.fetch_ohlcv(pair, timeframe=timeframe,limit={
            "4h":720,"1d":365,"1w":220,
        }[timeframe]), timeout=40,
    )
    from crypto_market_pipeline import ohlcv_frame
    return normalize_bars(ohlcv_frame(data), timeframe)


async def run(args):
    from crypto_market_pipeline import make_exchange
    allow = funded_universe()["crypto"]
    coins = ([x.strip().upper() for x in args.coins.split(",") if x.strip()]
             if args.coins.lower() != "all" else list(allow))
    if not coins or len(coins) != len(set(coins)) or set(coins)-set(allow):
        raise ValueError("Coins must be distinct members of frozen funded crypto universe")
    if not 1 <= args.max_anchors <= 300:
        raise ValueError("Limit PR-only history replay to 1..300 decision anchors per coin")
    if args.min_valid < 1 or args.min_valid > len(coins):
        raise ValueError("invalid minimum coverage")
    out=Path(args.out_dir)
    out.mkdir(parents=True,exist_ok=True)
    exchange=make_exchange("kraken")
    audits=[]; outcomes=[];errors=[];coverage=[]; proxy_outcomes=[]
    sources={}
    start=datetime.now(timezone.utc)
    try:
        markets=await asyncio.wait_for(exchange.load_markets(),timeout=65)
        pair_map={}
        for coin in coins:
            pair=next((s for s in (f"{coin}/USD",f"{coin}/USDT")
                       if s in markets and markets[s].get("spot", True)
                       and markets[s].get("active",True) is not False), None)
            pair_map[coin]=pair
        if "BTC/USD" not in markets:
            raise RuntimeError("BTC/USD Kraken spot benchmark absent")
        pairs={"BTC":"BTC/USD",**{k:v for k,v in pair_map.items() if v}}
        all_candles={}
        for coin,pair in pairs.items():
            if pair is None:
                continue
            try:
                # Sequential, rate-limited CCXT requests; no API keys.
                frames={}
                for timeframe in ("4h","1d","1w"):
                    frames[timeframe]=await fetch(exchange,pair,timeframe)
                    raw=frames[timeframe].to_csv(index=False)
                    identifier=coin.lower()+"_"+timeframe+".csv"
                    (out/identifier).write_text(raw,encoding="utf-8")
                    sources[coin+":"+timeframe]={
                        "pair":pair,"rows":len(frames[timeframe]),
                        "first_open_utc":str(frames[timeframe]["timestamp"].iloc[0]),
                        "last_open_utc":str(frames[timeframe]["timestamp"].iloc[-1]),
                        "sha256":sha256(raw.encode("utf-8")).hexdigest(),
                        "artifact_file":identifier,
                    }
                all_candles[coin]=frames
            except Exception as ex:
                coverage.append({"Coin":coin,"Pair":pair,"State":"PROVIDER UNAVAILABLE",
                                 "Reason":f"{type(ex).__name__}: {str(ex)[:150]}"})
        if "BTC" not in all_candles:
            raise RuntimeError("BTC 4h/daily/weekly historical benchmark unavailable")
        for coin in coins:
            pair=pair_map[coin]
            if not pair:
                coverage.append({"Coin":coin,"Pair":"","State":"NOT CURRENTLY LISTED",
                                 "Reason":"No approved public Kraken USD/USDT spot pair"})
                continue
            if coin not in all_candles:
                continue
            frames=all_candles[coin]
            a,o,e=replay_coin(
                frames,all_candles["BTC"],coin_name=coin,pair=pair,
                max_anchors=args.max_anchors,
            )
            state="ASOF SCORED" if len(a)>=args.min_anchors else "INSUFFICIENT ASOF AUDITS"
            coverage.append({"Coin":coin,"Pair":pair,"State":state,
                             "Reason":f"{len(a)} historical decisions; {len(e)} unscored"})
            if not a.empty:
                audits.append(a)
                proxy=research_exit_proxy(frames["4h"],a,cost_pct=0.8,max_hold_bars=20)
                if not proxy.empty:
                    proxy_outcomes.append(proxy)
            if not o.empty:
                outcomes.append(o)
            if not e.empty:
                errors.append(e)
    finally:
        await exchange.close()

    decisions=pd.concat(audits,ignore_index=True) if audits else pd.DataFrame()
    mover=pd.concat(outcomes,ignore_index=True) if outcomes else pd.DataFrame()
    unavailable=pd.concat(errors,ignore_index=True) if errors else pd.DataFrame()
    simulated=pd.concat(proxy_outcomes,ignore_index=True) if proxy_outcomes else pd.DataFrame()
    episodes=independent_mover_episodes(mover)
    pd.DataFrame(coverage).to_csv(out/"crypto_coverage.csv",index=False)
    decisions.to_csv(out/"asof_original_crypto_decisions.csv",index=False)
    mover.to_csv(out/"all_mover_and_nonmover_anchors.csv",index=False)
    unavailable.to_csv(out/"unscored_asof_clocks.csv",index=False)
    episodes.to_csv(out/"cooldown_separated_mover_episodes.csv",index=False)
    simulated.to_csv(out/"unverified_next_open_proxy_outcomes.csv",index=False)
    metadata={
        "captured_at_utc":start.isoformat(),
        "mode":"READ_ONLY_KRAKEN_SPOT_ORIGINAL_RULES_CRYPTO_RESEARCH",
        "rulebook_baseline_main_commit":"a1f1a37c3945743261cc4f7dd0ade0648f04cb62",
        "actual_executed_analysis_branch":"PR-only; not merged",
        "original_score_engine":"crypto_rule_engine.score_setup",
        "original_status_engine":"crypto_market_pipeline.flatten_deep_score",
        "original_funded_crypto_universe_size":len(allow),
        "selected_current_symbols":coins,
        "market_selection":"Kraken spot USD, fallback USDT if listed now; not historical listing proof",
        "asof": "4h close+1min; completed 4h/daily/weekly/BTC bars only",
        "score_lookbacks":"original engine 180x4h/365x1d/220x1w caps",
        "historical_volume_proxy":"rolling 6 completed 4h candles, base volume*close; not verified historic Kraken ticker",
        "historic_funded_bid_ask_verified":False,
        "historic_market_listing_asof_verified":False,
        "provider_historical_vintage_verified":False,
        "broker_order_fills_verified":False,
        "crypto_outcome_definition":"maximum later completed 4h CLOSE t+1..t+h vs CLOSE(t), not executable trade",
        "mover_thresholds_pct":[10,20,30],
        "horizons_4h_bars":[5,10,20],
        "proxy_cost_roundtrip_pct":0.8,
        "risks":[
            "Only Kraken recent bounded OHLCV available, not multi-year original crypto market and delisted universe",
            "Historic provider corrections can affect retrieved older candle values",
            "Historic OHLCV rolling dollar volume does NOT verify original point-in-time 24-hour Kraken ticker volume",
            "Current Kraken symbol and spot-market listing does not prove historical eligibility",
            "Funded broker spreads, liquidity and challenge account rules cannot be reconstructed",
            "Overlapping mover labels and cooldown episodes are correlated, not independent profitable trades",
            "No rule tuning or investment recommendation from a small recent sample",
        ],
        "source_price_files":sources,
    }
    (out/"source_manifest.json").write_text(
        json.dumps(metadata,indent=2,sort_keys=True,allow_nan=False)+"\n",encoding="utf-8")
    valid=sorted(decisions["Coin"].unique()) if not decisions.empty else []
    summary={
        "complete_audit_coins":valid,"requested_coins":coins,
        "historical_decision_clocks":len(decisions),
        "outcome_labeled_anchor_rows":len(mover),
        "data_unavailable_clocks":len(unavailable),
        "descriptive_separated_mover_episodes":len(episodes),
        "original_proxy_buys_unverified":int(
            (decisions["Research class"]=="PROXY_BUY_UNVERIFIED").sum()
        ) if not decisions.empty else 0,
        "broker_funded_ready_verified":0,
        "run_mode":"RESEARCH_ONLY",
    }
    (out/"summary.json").write_text(json.dumps(summary,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    print(json.dumps(summary,sort_keys=True))
    if len(valid)<args.min_valid:
        raise RuntimeError(f"only {len(valid)} / {args.min_valid} required coins passed as-of replay")
    return summary


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--coins",default="BTC,ETH,SOL")
    p.add_argument("--max-anchors",type=int,default=90)
    p.add_argument("--min-valid",type=int,default=2)
    p.add_argument("--min-anchors",type=int,default=20)
    p.add_argument("--out-dir",default="funded-crypto-historical-pilot")
    args=p.parse_args()
    asyncio.run(run(args))


if __name__=="__main__":
    main()
