"""HEADLESS prospective Kraken-funded STOCK+CRYPTO research snapshots.

This runs the existing stock/crypto technical engines on PUBLIC MARKET DATA.
It cannot reproduce the Streamlit UI's broker-confirmed Funded READY because
there is no Kraken Funded account connection, funded buy/sell quotes or
official company earnings confirmation in unattended GitHub Actions.

A snapshot is an observation of a market-data setup, NOT an entry approval.
No orders, broker credentials, trade permissions, or automatic backfills.
"""
from __future__ import annotations

import argparse
import ast
import asyncio
import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from funded_forward_capture import SOURCE, create_snapshot, funded_universe, write_snapshot_once
from funded_crypto_checks import completed_funded_crypto_candles
from funded_stock_data import get_validated_stock_history, clean_stock_daily_frame
from funded_stock_breakout import evaluate_funded_breakout
from trade_rules import evaluate_price_setup, _wilder_rsi


def _safe(value):
    try:
        v = float(value)
        return v if math.isfinite(v) else None
    except (ValueError, TypeError):
        return None


def _trend_watch_from_original_source():
    """Re-use exact existing pure function without importing Streamlit UI."""
    parsed = ast.parse(SOURCE.read_text(encoding="utf-8"), filename=str(SOURCE))
    found = [
        obj for obj in parsed.body
        if isinstance(obj, ast.FunctionDef) and obj.name == "funded_trend_watch"
    ]
    if len(found) != 1:
        raise RuntimeError("Funded original stock trend-watch engine missing")
    namespace = {"pd": pd, "np": np}
    stub = ast.Module(
        body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0),found[0]],
        type_ignores=[],
    )
    exec(compile(ast.fix_missing_locations(stub),str(SOURCE),"exec"),namespace)
    return namespace["funded_trend_watch"]


def collect_stocks(tickers):
    import yfinance as yf

    benchmark_raw = yf.download(
        "^GSPC",period="3y",interval="1d",auto_adjust=False,
        threads=False,progress=False,
    )
    if benchmark_raw is None or benchmark_raw.empty:
        raise RuntimeError("No benchmark history — refusing to score stocks")
    if isinstance(benchmark_raw.columns,pd.MultiIndex):
        benchmark_raw.columns = benchmark_raw.columns.get_level_values(0)
    ny_now = pd.Timestamp.now(tz="America/New_York")
    benchmark, benchmark_reason = clean_stock_daily_frame(benchmark_raw, ny_now)
    if benchmark is None:
        raise RuntimeError("Stale or invalid public US-stock benchmark — " + benchmark_reason)

    try:
        batch = yf.download(
            list(tickers), period="3y",interval="1d",group_by="ticker",
            auto_adjust=False,threads=True,progress=False,
        )
    except Exception:
        batch = pd.DataFrame()
    trend_watch = _trend_watch_from_original_source()
    observations = []
    for symbol in tickers:
        try:
            def single(t):
                return yf.download(
                    t,period="3y",interval="1d",auto_adjust=False,
                    threads=False,progress=False,
                )
            frame, source, reason = get_validated_stock_history(batch,symbol,single,ny_now)
            if frame is None:
                observations.append({
                    "asset":symbol,"status":"DATA UNAVAILABLE","reason":reason,
                    "source":source,
                })
                continue
            core = evaluate_price_setup(frame,benchmark)
            origin = "trade_rules.evaluate_price_setup"
            state = str(core.get("technical_state") or "NO CURRENT SETUP")
            if state == "BLOCKED":
                breakout = evaluate_funded_breakout(frame,benchmark)
                if breakout is not None:
                    core.update(breakout)
                    origin = "funded_stock_breakout.evaluate_funded_breakout"
                else:
                    developing = trend_watch(frame,benchmark)
                    if developing:
                        core.update(developing)
                        origin = "kraken_funded.funded_trend_watch"
            close = float(frame["Close"].iloc[-1])
            volume = float((frame["Close"]*frame["Volume"]).tail(20).median())
            observations.append({
                "asset":symbol,
                "status":str(core.get("technical_state") or state),
                "reason":str(core.get("technical_reason") or ""),
                "source":"Yahoo Finance PUBLIC daily OHLCV / " + source,
                "bar_timestamp":str(pd.Timestamp(frame.index[-1]).date()),
                "score":_safe(core.get("technical_score")),
                "entry":_safe(core.get("entry") or core.get("reference_entry")),
                "stop":_safe(core.get("stop") or core.get("reference_stop")),
                "target":_safe(core.get("target") or core.get("reference_target")),
                "rr":_safe(core.get("reward_risk") or core.get("reference_reward_risk")),
                "meta":{
                    "origin_engine":origin,
                    "last_close_public_proxy":close,
                    "median_daily_volume_usd_proxy":volume,
                    "macd_progress":str(core.get("macd_progress") or ""),
                    "liquidity_gbpm_approx":round(volume*0.75/1e6,3),
                    "earnings_event_officially_verified":False,
                    "funded_bid_ask_verified":False,
                    "trade_status":"RESEARCH ONLY",
                },
            })
        except Exception as err:
            observations.append({
                "asset":symbol,"status":"SCAN ERROR",
                "source":"Yahoo Finance public daily OHLCV",
                "reason":f"{type(err).__name__}: {str(err)[:170]}",
            })
    return observations


async def collect_crypto(coins):
    from crypto_market_pipeline import (
        make_exchange, ohlcv_frame, flatten_deep_score,
    )
    from crypto_rule_engine import ScreenerConfig, score_setup

    exchange = make_exchange("kraken")
    snapshots = []
    try:
        markets = await asyncio.wait_for(exchange.load_markets(),timeout=60)
        tickers = await asyncio.wait_for(exchange.fetch_tickers(),timeout=75)
        observed = pd.Timestamp.now(tz="UTC")
        btc4 = completed_funded_crypto_candles(
            ohlcv_frame(await exchange.fetch_ohlcv("BTC/USD",timeframe="4h",limit=180)),
            "4h",observed,
        )
        btcd = completed_funded_crypto_candles(
            ohlcv_frame(await exchange.fetch_ohlcv("BTC/USD",timeframe="1d",limit=365)),
            "1d",observed,
        )
        if len(btc4)<70 or len(btcd)<35:
            raise RuntimeError("Kraken BTC crypto benchmark has insufficient completed bars")
        for coin in coins:
            symbol = f"{coin}/USD" if f"{coin}/USD" in markets else f"{coin}/USDT"
            if symbol not in markets:
                snapshots.append({
                    "asset":coin,"status":"DATA UNAVAILABLE",
                    "reason":"Kraken public USD/USDT spot market not found",
                    "source":"Kraken public spot markets",
                })
                continue
            try:
                bars4,barsd,barsw = await asyncio.wait_for(
                    asyncio.gather(
                        exchange.fetch_ohlcv(symbol,timeframe="4h",limit=180),
                        exchange.fetch_ohlcv(symbol,timeframe="1d",limit=365),
                        exchange.fetch_ohlcv(symbol,timeframe="1w",limit=220),
                    ),timeout=90,
                )
                point=pd.Timestamp.now(tz="UTC")
                frame4=completed_funded_crypto_candles(ohlcv_frame(bars4),"4h",point)
                framed=completed_funded_crypto_candles(ohlcv_frame(barsd),"1d",point)
                weekraw=ohlcv_frame(barsw)
                framew=completed_funded_crypto_candles(weekraw,"1w",point) if not weekraw.empty else weekraw
                cfg=ScreenerConfig(
                    exchange_id="kraken",quote=symbol.split("/")[-1],
                    universe_size=len(coins),min_quote_volume=0,score_threshold=80,
                )
                score=score_setup(frame4,framed,btc4,cfg,dfw=framew,btcd=btcd)
                details=tickers.get(symbol,{}) or {}
                qv=_safe(details.get("quoteVolume"))
                qv=qv if qv is not None else 0.0
                item={
                    "Base":coin,"Symbol":symbol,"Exchange":"Kraken","Exchange id":"kraken",
                    "24h quote volume":qv,"Kraken available":True,
                    "Execution available":True,"Execution venues":"Kraken",
                    "Execution liquidity pass":qv>=1_000_000,
                    "Execution reason":"PASS" if qv>=1_000_000 else "Kraken public 24h spot volume under $1m",
                }
                flat=flatten_deep_score(item,score)
                snapshots.append({
                    "asset":coin,"status":str(flat.get("Swing status") or "UNKNOWN"),
                    "reason":str(flat.get("Swing reason") or ""),
                    "source":"Kraken PUBLIC spot OHLCV / crypto_rule_engine.score_setup",
                    "bar_timestamp":str(frame4["timestamp"].iloc[-1]),
                    "score":_safe(flat.get("Swing score")),
                    "entry":_safe(flat.get("Active entry")),
                    "stop":_safe(flat.get("Invalidation")),
                    "target":_safe(flat.get("First technical target")),
                    "rr":_safe(flat.get("Current R:R")),
                    "meta":{
                        "origin_engine":"crypto_rule_engine.score_setup",
                        "pair":symbol,"original_swing_status":str(flat.get("Swing status") or ""),
                        "entry_timing":str(flat.get("Entry timing") or ""),
                        "entry_timing_actionable":bool(flat.get("Entry timing actionable")),
                        "swing_eligible":bool(flat.get("Swing eligible")),
                        "relative_strength_vs_btc_pct":_safe(flat.get("RS vs BTC %")),
                        "volume_usd_public_spot_proxy":qv,
                        "execution_liquidity_pass_public_spot":qv>=1_000_000,
                        "funded_bid_ask_verified":False,
                        "broker_market_access_verified":False,
                        "trade_status":"RESEARCH ONLY",
                    },
                })
            except Exception as err:
                snapshots.append({
                    "asset":coin,"status":"SCAN ERROR",
                    "reason":f"{type(err).__name__}: {str(err)[:170]}",
                    "source":"Kraken public spot OHLCV",
                })
    finally:
        try:
            await asyncio.wait_for(exchange.close(),timeout=10)
        except Exception:
            pass
    return snapshots


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--asset",required=True,choices=("stocks","crypto"))
    parser.add_argument("--out-dir",default="funded-forward-artifacts")
    args=parser.parse_args()

    universe=funded_universe()
    run=os.environ.get("GITHUB_RUN_ID","local")
    attempt=os.environ.get("GITHUB_RUN_ATTEMPT","1")
    capture=datetime.now(timezone.utc)
    records=(collect_stocks(universe["stocks"]) if args.asset=="stocks"
             else asyncio.run(collect_crypto(universe["crypto"])))
    observed=datetime.now(timezone.utc)
    payload=create_snapshot(args.asset,records,observed_at=observed,
                            run_id=f"{run}-{attempt}-{args.asset}-{observed:%Y%m%dT%H%M%SZ}",
                            universe=universe)
    filename=f"funded-{args.asset}-{observed:%Y%m%dT%H%M%SZ}-{run}-{attempt}.json"
    path=write_snapshot_once(Path(args.out_dir)/filename,payload)

    failures=sum(1 for x in payload["rows"] if x["status"] in {"DATA UNAVAILABLE","SCAN ERROR"})
    print(json.dumps({
        "archive_file":str(path),"asset_type":args.asset,
        "captured_at_utc":payload["captured_at_utc"],
        "expected":payload["expected_universe"],
        "unavailable_or_errors":failures,
        "research_only":True,"no_trading_authority":True,
    },sort_keys=True))
    # A full provider outage is a failed research refresh, not a valid
    # 'no qualifying trade' observation. Artifact remains available.
    if failures >= payload["expected_universe"]:
        raise RuntimeError("All assets are unavailable — failing this scheduled research refresh")


if __name__=="__main__":
    main()
