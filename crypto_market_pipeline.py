from __future__ import annotations

import argparse
import asyncio
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Tuple

import ccxt.async_support as ccxt
import numpy as np
import pandas as pd

from crypto_macro import snapshot_age_minutes, snapshot_payload
from crypto_universe_rules import crypto_universe_exclusion_reason
from crypto_rule_engine import ScreenerConfig, score_setup


EXCHANGES = {
    "binance": "Binance",
    "okx": "OKX",
    "bybit": "Bybit",
    "kraken": "Kraken",
    "cryptocom": "Crypto.com",
}

EXECUTION_EXCHANGES = {
    "kraken": "Kraken",
    "cryptocom": "Crypto.com",
}
EXECUTION_USD_QUOTES = {"USD", "USDT", "USDC"}


def safe(value, default=np.nan) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except Exception:
        return default


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def make_exchange(exchange_id: str):
    cls = getattr(ccxt, exchange_id)
    config = {
        "enableRateLimit": True,
        "options": {
            "defaultType": "spot",
            "fetchMarkets": {"types": ["spot"]},
            "fetchCurrencies": False,
            "fetchMargins": False,
        },
    }
    if exchange_id == "bybit":
        # Bybit documents bytick.com as an alternate mainnet host.
        config["hostname"] = "bytick.com"
    exchange = cls(config)
    if exchange_id == "binance":
        # Binance's official market-data-only host is suitable for the public
        # endpoints this pipeline uses and avoids location restrictions on the
        # general trading API host.
        api_urls = exchange.urls.get("api", {})
        if isinstance(api_urls, dict):
            api_urls["public"] = "https://data-api.binance.vision/api/v3"
            api_urls["v1"] = "https://data-api.binance.vision/api/v1"
    return exchange


def quote_volume(ticker: dict) -> float:
    qv = safe(ticker.get("quoteVolume"))
    if math.isfinite(qv):
        return qv
    last = safe(ticker.get("last"))
    base_volume = safe(ticker.get("baseVolume"))
    if math.isfinite(last) and math.isfinite(base_volume):
        return last * base_volume
    return np.nan


def last_price(ticker: dict) -> float:
    for key in ("last", "close", "bid", "ask"):
        value = safe(ticker.get(key))
        if math.isfinite(value) and value > 0:
            return value
    return np.nan


async def load_exchange_snapshot(
    exchange_id: str,
    quote: str,
    market_data_min_quote_volume: float,
) -> Tuple[pd.DataFrame, dict, dict]:
    """
    Load one venue.

    The market-data floor is intentionally much lower than the BUY liquidity
    requirement. Discovery needs to see a coin before it is already a large,
    obvious market; execution safety is checked later using combined liquidity
    and the user's Kraken/Crypto.com venues.
    """
    exchange = make_exchange(exchange_id)
    label = EXCHANGES[exchange_id]
    try:
        markets = await asyncio.wait_for(exchange.load_markets(), timeout=45)
        if not exchange.has.get("fetchTickers"):
            raise RuntimeError(f"{exchange_id} does not expose fetchTickers")
        tickers = await asyncio.wait_for(exchange.fetch_tickers(), timeout=60)

        rows = []
        execution_listed_bases = set()
        execution_usd_volume_by_base: Dict[str, float] = {}
        counts = {
            "active_spot_total": 0,
            "quote_matched": 0,
            "stablecoin_excluded": 0,
            "leveraged_excluded": 0,
            "tokenized_security_excluded": 0,
            "invalid_symbol_excluded": 0,
            "volume_unavailable": 0,
            "below_market_data_floor": 0,
            "market_data_eligible": 0,
        }

        for symbol, market in markets.items():
            if not market.get("spot") or market.get("active") is False:
                continue
            counts["active_spot_total"] += 1

            base = str(market.get("base") or "").upper()
            market_quote = str(market.get("quote") or "").upper()
            exclusion = crypto_universe_exclusion_reason(base, exchange_id)
            if exclusion == "stable_or_cash":
                counts["stablecoin_excluded"] += 1
                continue
            if exclusion == "leveraged_token":
                counts["leveraged_excluded"] += 1
                continue
            if exclusion == "tokenized_security":
                counts["tokenized_security_excluded"] += 1
                continue
            if exclusion:
                counts["invalid_symbol_excluded"] += 1
                continue

            ticker = tickers.get(symbol) or {}

            if exchange_id in EXECUTION_EXCHANGES:
                execution_listed_bases.add(base)
                if market_quote in EXECUTION_USD_QUOTES:
                    execution_qv = quote_volume(ticker)
                    if math.isfinite(execution_qv):
                        execution_usd_volume_by_base[base] = (
                            execution_usd_volume_by_base.get(base, 0.0)
                            + float(execution_qv)
                        )

            if market_quote != quote:
                continue
            counts["quote_matched"] += 1

            qv = quote_volume(ticker)
            price = last_price(ticker)
            if not math.isfinite(qv):
                counts["volume_unavailable"] += 1
                continue
            if qv < market_data_min_quote_volume:
                counts["below_market_data_floor"] += 1
                continue

            counts["market_data_eligible"] += 1
            rows.append({
                "Base": base,
                "Symbol": symbol,
                "Exchange": label,
                "Exchange id": exchange_id,
                "Quote": quote,
                "Price": price,
                "24h quote volume": qv,
            })

        frame = pd.DataFrame(rows)
        counts["exchange"] = label
        counts["exchange_id"] = exchange_id
        counts["quote"] = quote
        counts["market_data_min_quote_volume"] = market_data_min_quote_volume
        execution_meta = {
            "exchange_id": exchange_id,
            "exchange": label,
            "listed_bases": execution_listed_bases,
            "usd_volume_by_base": execution_usd_volume_by_base,
        }
        return frame, counts, execution_meta
    finally:
        await exchange.close()


async def load_all_universes(
    quote: str,
    market_data_min_quote_volume: float,
    discovery_min_combined_volume: float,
    buy_min_combined_volume: float,
    buy_min_execution_volume: float,
):
    tasks = [
        load_exchange_snapshot(exchange_id, quote, market_data_min_quote_volume)
        for exchange_id in EXCHANGES
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    frames = []
    audits = []
    errors = []
    execution_meta_by_id = {}

    for exchange_id, result in zip(EXCHANGES, results):
        if isinstance(result, Exception):
            errors.append(f"{exchange_id}: {type(result).__name__}: {result}")
            audits.append({
                "exchange": EXCHANGES[exchange_id],
                "exchange_id": exchange_id,
                "status": "DATA ISSUE",
                "error": f"{type(result).__name__}: {str(result)[:300]}",
            })
            continue
        frame, audit, execution_meta = result
        frames.append(frame)
        audit["status"] = "CURRENT"
        audits.append(audit)
        execution_meta_by_id[exchange_id] = execution_meta

    combined = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if combined.empty:
        return combined, pd.DataFrame(), audits, errors

    combined = combined.sort_values(
        ["Base", "24h quote volume"], ascending=[True, False]
    )

    grouped = []
    pre_discovery_unique = int(combined["Base"].nunique())
    for base, group in combined.groupby("Base", sort=True):
        cross_volume = float(
            pd.to_numeric(group["24h quote volume"], errors="coerce").fillna(0).sum()
        )
        if cross_volume < discovery_min_combined_volume:
            continue

        best = group.iloc[0].to_dict()
        best["Eligible exchange count"] = int(group["Exchange id"].nunique())
        best["Eligible exchanges"] = ", ".join(sorted(group["Exchange"].unique()))
        best["Cross-exchange quote volume"] = cross_volume

        execution_venues = []
        execution_volumes = {}
        for execution_id, execution_label in EXECUTION_EXCHANGES.items():
            meta = execution_meta_by_id.get(execution_id, {})
            listed = base in meta.get("listed_bases", set())
            volume = safe(meta.get("usd_volume_by_base", {}).get(base), 0.0)
            best[f"{execution_label} available"] = bool(listed)
            best[f"{execution_label} USD-like 24h volume"] = float(volume)
            if listed:
                execution_venues.append(execution_label)
            execution_volumes[execution_label] = float(volume)

        max_execution_volume = max(execution_volumes.values(), default=0.0)
        execution_available = bool(execution_venues)
        execution_liquidity_pass = (
            execution_available
            and cross_volume >= buy_min_combined_volume
            and max_execution_volume >= buy_min_execution_volume
        )

        best["Execution available"] = execution_available
        best["Execution venues"] = ", ".join(execution_venues)
        best["Execution venue count"] = len(execution_venues)
        best["Execution max USD-like 24h volume"] = max_execution_volume
        best["Execution liquidity pass"] = bool(execution_liquidity_pass)
        if not execution_available:
            best["Execution reason"] = "Not listed on Kraken or Crypto.com"
        elif cross_volume < buy_min_combined_volume:
            best["Execution reason"] = (
                f"Combined liquidity below USD {buy_min_combined_volume/1_000_000:.1f}m BUY floor"
            )
        elif max_execution_volume < buy_min_execution_volume:
            best["Execution reason"] = (
                f"Kraken/Crypto.com liquidity below USD {buy_min_execution_volume/1_000_000:.1f}m BUY floor"
            )
        else:
            best["Execution reason"] = "Execution liquidity passes"

        grouped.append(best)

    deduped = pd.DataFrame(grouped)
    if not deduped.empty:
        deduped = deduped.sort_values(
            "Cross-exchange quote volume", ascending=False
        ).reset_index(drop=True)

    for audit in audits:
        audit["pre_discovery_unique_coins"] = pre_discovery_unique
        audit["discovery_min_combined_volume"] = discovery_min_combined_volume
        audit["buy_min_combined_volume"] = buy_min_combined_volume
        audit["buy_min_execution_volume"] = buy_min_execution_volume

    return combined, deduped, audits, errors


def ohlcv_frame(rows: list) -> pd.DataFrame:
    frame = pd.DataFrame(
        rows, columns=["timestamp", "open", "high", "low", "close", "volume"]
    )
    if frame.empty:
        return frame
    for column in ["open", "high", "low", "close", "volume"]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], unit="ms", utc=True)
    return frame.dropna().reset_index(drop=True)


def rsi14(close: pd.Series) -> float:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    values = 100 - (100 / (1 + rs))
    return safe(values.iloc[-1], 50.0)


def discovery_metrics(frame: pd.DataFrame, btc: pd.DataFrame) -> dict:
    # Ignore the last exchange candle because it may still be forming.
    data = frame.iloc[:-1].copy() if len(frame) > 35 else frame.copy()
    benchmark = btc.iloc[:-1].copy() if len(btc) > 35 else btc.copy()
    if len(data) < 40 or len(benchmark) < 20:
        return {"status": "INSUFFICIENT HISTORY"}

    latest = data.iloc[-1]
    price = safe(latest["close"])
    prior30 = data.iloc[-31:-1]
    resistance = safe(prior30["high"].max())
    distance_pct = (
        (resistance - price) / price * 100
        if price > 0 and math.isfinite(resistance) else np.nan
    )

    recent_low = safe(data["low"].iloc[-12:].min())
    previous_low = safe(data["low"].iloc[-24:-12].min())
    higher_low = (
        math.isfinite(recent_low)
        and math.isfinite(previous_low)
        and recent_low > previous_low
    )

    prev_close = data["close"].shift(1)
    tr = pd.concat(
        [
            data["high"] - data["low"],
            (data["high"] - prev_close).abs(),
            (data["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr = tr.ewm(alpha=1 / 14, adjust=False).mean()
    current_atr_pct = safe(atr.iloc[-1] / price * 100) if price > 0 else np.nan
    prior_atr_pct = safe((atr.iloc[-30:-6] / data["close"].iloc[-30:-6] * 100).median())
    compression_ratio = (
        current_atr_pct / prior_atr_pct
        if math.isfinite(current_atr_pct)
        and math.isfinite(prior_atr_pct)
        and prior_atr_pct > 0
        else np.nan
    )
    compression = math.isfinite(compression_ratio) and compression_ratio <= 0.90

    recent_volume = safe(data["volume"].iloc[-6:].mean())
    prior_volume = safe(data["volume"].iloc[-30:-6].median())
    volume_ratio = (
        recent_volume / prior_volume
        if math.isfinite(recent_volume)
        and math.isfinite(prior_volume)
        and prior_volume > 0
        else np.nan
    )
    volume_contracting = math.isfinite(volume_ratio) and volume_ratio <= 0.90

    lookback = min(12, len(data) - 1, len(benchmark) - 1)
    coin_return = (
        data["close"].iloc[-1] / data["close"].iloc[-1 - lookback] - 1
        if lookback > 0 else np.nan
    )
    btc_return = (
        benchmark["close"].iloc[-1] / benchmark["close"].iloc[-1 - lookback] - 1
        if lookback > 0 else np.nan
    )
    rs_btc_pct = (
        (coin_return - btc_return) * 100
        if math.isfinite(coin_return) and math.isfinite(btc_return)
        else np.nan
    )

    # This is deliberately a broad discovery rank, not the CL Signal BUY score.
    # It only decides which coins deserve expensive analysis first.
    points = 0.0
    if math.isfinite(distance_pct):
        if 0 <= distance_pct <= 5:
            points += 25
        elif 5 < distance_pct <= 10:
            points += 12
    if higher_low:
        points += 20
    if compression:
        points += 20
    if volume_contracting:
        points += 15
    if math.isfinite(rs_btc_pct) and rs_btc_pct > 0:
        points += 20

    return {
        "status": "SCANNED",
        "price_at_scan": price,
        "resistance": resistance,
        "distance_to_resistance_pct": distance_pct,
        "higher_low": bool(higher_low),
        "compression": bool(compression),
        "compression_ratio": compression_ratio,
        "volume_contracting": bool(volume_contracting),
        "volume_ratio": volume_ratio,
        "rs_vs_btc_48h_pct": rs_btc_pct,
        "rsi14": rsi14(data["close"]),
        "discovery_rank": round(points, 1),
    }


async def discovery_batch(universe: pd.DataFrame, symbols: List[str], concurrency: int = 10):
    if not symbols:
        return [], []

    subset = universe[universe["Base"].isin(symbols)].copy()
    grouped = {
        exchange_id: group.to_dict(orient="records")
        for exchange_id, group in subset.groupby("Exchange id")
    }
    rows = []
    errors = []

    async def scan_exchange(exchange_id: str, items: list):
        exchange = make_exchange(exchange_id)
        sem = asyncio.Semaphore(concurrency)
        local = []
        try:
            await asyncio.wait_for(exchange.load_markets(), timeout=45)
            btc_symbol = "BTC/USDT"
            btc_rows = await asyncio.wait_for(
                exchange.fetch_ohlcv(btc_symbol, timeframe="4h", limit=100), timeout=25
            )
            btc = ohlcv_frame(btc_rows)

            async def one(item):
                async with sem:
                    symbol = item["Symbol"]
                    try:
                        raw = await asyncio.wait_for(
                            exchange.fetch_ohlcv(symbol, timeframe="4h", limit=100),
                            timeout=25,
                        )
                        metrics = discovery_metrics(ohlcv_frame(raw), btc)
                        return {
                            **item,
                            **metrics,
                            "discovery_scanned_at": now_iso(),
                        }
                    except Exception as exc:
                        errors.append(
                            f"{exchange_id}/{symbol}: {type(exc).__name__}: {str(exc)[:180]}"
                        )
                        return {
                            **item,
                            "status": "DATA ISSUE",
                            "discovery_scanned_at": now_iso(),
                        }

            tasks = [asyncio.create_task(one(item)) for item in items]
            local.extend(await asyncio.gather(*tasks))
        finally:
            await exchange.close()
        return local

    results = await asyncio.gather(
        *(scan_exchange(exchange_id, items) for exchange_id, items in grouped.items()),
        return_exceptions=True,
    )
    for exchange_id, result in zip(grouped, results):
        if isinstance(result, Exception):
            errors.append(f"{exchange_id}: {type(result).__name__}: {result}")
        else:
            rows.extend(result)
    return rows, errors


def load_csv(path: Path) -> pd.DataFrame:
    try:
        return pd.read_csv(path, compression="gzip")
    except Exception:
        return pd.DataFrame()


def save_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False, compression="gzip")


def load_manifest(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_json(payload: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def refresh_macro_snapshot(output: Path, max_age_minutes: float = 50.0) -> dict:
    """Refresh macro data off the user request path and retain the last good snapshot."""
    path = output / "macro.json"
    previous = load_manifest(path)
    if previous and snapshot_age_minutes(previous) <= max_age_minutes:
        return previous
    try:
        refreshed = snapshot_payload(timeout=8.0)
    except Exception as exc:
        refreshed = {
            "available": False,
            "errors": [f"{type(exc).__name__}: {str(exc)[:250]}"],
            "generated_at": now_iso(),
        }

    if not refreshed.get("available") and previous.get("available"):
        previous["last_refresh_attempt_at"] = now_iso()
        previous["last_refresh_errors"] = refreshed.get("errors", [])
        save_json(previous, path)
        return previous

    save_json(refreshed, path)
    return refreshed


def merge_discovery(existing: pd.DataFrame, fresh: pd.DataFrame, eligible_bases: set) -> pd.DataFrame:
    if not existing.empty:
        existing = existing[existing["Base"].astype(str).isin(eligible_bases)].copy()
    if fresh.empty:
        return existing
    if existing.empty:
        return fresh.reset_index(drop=True)
    refreshed = set(fresh["Base"].astype(str))
    existing = existing[~existing["Base"].astype(str).isin(refreshed)]
    return pd.concat([existing, fresh], ignore_index=True)


def active_monitor(
    discovery: pd.DataFrame,
    all_markets: pd.DataFrame,
    max_active: int,
) -> pd.DataFrame:
    if discovery.empty:
        return pd.DataFrame()

    d = discovery.copy()
    d["discovery_rank"] = pd.to_numeric(d.get("discovery_rank"), errors="coerce")
    d["distance_to_resistance_pct"] = pd.to_numeric(
        d.get("distance_to_resistance_pct"), errors="coerce"
    )

    broad = d[
        (d["status"] == "SCANNED")
        & (
            (d["discovery_rank"] >= 40)
            | d["distance_to_resistance_pct"].between(-2, 10, inclusive="both")
        )
    ].copy()
    broad = broad.sort_values(
        ["discovery_rank", "24h quote volume"], ascending=[False, False]
    ).head(max_active)

    if broad.empty:
        return broad

    latest = (
        all_markets.sort_values("24h quote volume", ascending=False)
        .drop_duplicates(["Base", "Exchange id"])
    )
    quote_lookup = {
        (str(row["Base"]), str(row["Exchange id"])): row
        for _, row in latest.iterrows()
    }

    out = []
    for _, row in broad.iterrows():
        item = row.to_dict()
        quote = quote_lookup.get((str(row["Base"]), str(row["Exchange id"])), {})
        current = safe(quote.get("Price"))
        resistance = safe(row.get("resistance"))
        distance = (
            (resistance - current) / current * 100
            if current > 0 and math.isfinite(resistance)
            else np.nan
        )

        if not math.isfinite(current):
            state = "DATA STALE"
        elif math.isfinite(resistance) and current >= resistance * 1.02:
            state = "EXTENDED"
        elif math.isfinite(resistance) and current >= resistance:
            state = "BREAKOUT TEST"
        elif math.isfinite(distance) and 0 <= distance <= 3:
            state = "NEAR RESISTANCE"
        elif math.isfinite(distance) and 3 < distance <= 10:
            state = "DEVELOPING"
        else:
            state = "MONITOR"

        item.update({
            "Current price": current,
            "Live distance to resistance %": distance,
            "Monitor state": state,
            "monitor_updated_at": now_iso(),
        })
        out.append(item)
    return pd.DataFrame(out)


def flatten_deep_score(item: dict, result: dict) -> dict:
    base = str(item.get("Base") or "")
    rs_pass = base == "BTC" or safe(result.get("rs_vs_btc_pct"), -999) > 0
    score = safe(result.get("score"), 0)
    execution_pass = bool(item.get("Execution liquidity pass"))
    swing_ready = (
        bool(result.get("eligible"))
        and score >= 80
        and rs_pass
        and not bool(result.get("candle_caution"))
        and execution_pass
    )
    if swing_ready:
        swing_status = "BUY"
    elif bool(result.get("eligible")) or score >= 65:
        swing_status = "WATCH"
    else:
        swing_status = "PASS"

    accumulation_verdict = str(result.get("accumulation_verdict") or "")
    if accumulation_verdict == "ACCUMULATION READY" and execution_pass:
        accumulation_status = "ACCUMULATE"
    elif accumulation_verdict == "ACCUMULATION READY" or accumulation_verdict.startswith("WATCH") or safe(result.get("bottom_score"), 0) >= 50:
        accumulation_status = "WATCH"
    else:
        accumulation_status = "PASS"

    return {
        "Base": base,
        "Symbol": item.get("Symbol"),
        "Exchange": item.get("Exchange"),
        "Exchange id": item.get("Exchange id"),
        "24h quote volume": item.get("24h quote volume"),
        "Eligible exchange count": item.get("Eligible exchange count"),
        "Eligible exchanges": item.get("Eligible exchanges"),
        "Cross-exchange quote volume": item.get("Cross-exchange quote volume"),
        "Kraken available": item.get("Kraken available", False),
        "Crypto.com available": item.get("Crypto.com available", False),
        "Kraken USD-like 24h volume": item.get("Kraken USD-like 24h volume", 0.0),
        "Crypto.com USD-like 24h volume": item.get("Crypto.com USD-like 24h volume", 0.0),
        "Execution available": item.get("Execution available", False),
        "Execution venues": item.get("Execution venues", ""),
        "Execution max USD-like 24h volume": item.get("Execution max USD-like 24h volume", 0.0),
        "Execution liquidity pass": item.get("Execution liquidity pass", False),
        "Execution reason": item.get("Execution reason", ""),
        "deep_scored_at": now_iso(),
        "Swing status": swing_status,
        "Swing score": result.get("score"),
        "Swing eligible": bool(result.get("eligible")),
        "Swing reason": (
            result.get("reason")
            if execution_pass
            else f"{result.get('reason', '')}; execution: {item.get('Execution reason', 'not ready')}"
        ),
        "Price": result.get("price"),
        "Entry low": result.get("entry_low"),
        "Entry high": result.get("entry_high"),
        "Planned entry": result.get("planned_entry"),
        "Invalidation": result.get("invalidation"),
        "Target": result.get("projected_target"),
        "Target upside %": result.get("target_upside_pct"),
        "R:R": result.get("risk_reward"),
        "Resistance": result.get("resistance"),
        "Distance %": result.get("distance_pct"),
        "Resistance tests": result.get("resistance_tests"),
        "RSI": result.get("rsi"),
        "ATR ratio": result.get("atr_ratio"),
        "Vol ratio": result.get("volume_ratio"),
        "RS vs BTC %": result.get("rs_vs_btc_pct"),
        "RS vs BTC 96h %": result.get("rs_vs_btc_96h_pct"),
        "Coin trend": result.get("coin_trend"),
        "Market trend": result.get("market_trend"),
        "SMA regime": result.get("sma_regime"),
        "Candle caution": "CAUTION" if result.get("candle_caution") else "CLEAR",
        "Last 4h candle": result.get("candle_pattern"),
        "Pattern": result.get("triangle_label"),
        "BB 4h regime": result.get("bb_4h_regime"),
        "BB 4h width percentile": result.get("bb_4h_width_percentile"),
        "4h Channel": result.get("channel_4h_direction"),
        "Project freshness": result.get("project_freshness"),
        "History days": result.get("history_days"),
        "Accumulation status": accumulation_status,
        "Accumulation score": result.get("bottom_score"),
        "Accumulation verdict": result.get("accumulation_verdict"),
        "Accumulation low": result.get("accumulation_low"),
        "Accumulation high": result.get("accumulation_high"),
        "In accumulation zone": bool(result.get("in_accumulation_zone")),
        "4Y cycle position %": result.get("cycle_position_pct"),
    }


async def deep_score_batch(
    universe: pd.DataFrame,
    bases: List[str],
    quote: str,
    concurrency: int = 8,
):
    if not bases:
        return [], []

    subset = universe[universe["Base"].astype(str).isin(set(bases))].copy()
    grouped = {
        exchange_id: group.to_dict(orient="records")
        for exchange_id, group in subset.groupby("Exchange id")
    }
    rows = []
    errors = []

    async def score_exchange(exchange_id: str, items: list):
        exchange = make_exchange(exchange_id)
        sem = asyncio.Semaphore(concurrency)
        local = []
        try:
            await asyncio.wait_for(exchange.load_markets(), timeout=45)
            btc_symbol = f"BTC/{quote}"
            btc4_raw, btcd_raw = await asyncio.gather(
                asyncio.wait_for(exchange.fetch_ohlcv(btc_symbol, timeframe="4h", limit=180), timeout=30),
                asyncio.wait_for(exchange.fetch_ohlcv(btc_symbol, timeframe="1d", limit=365), timeout=30),
            )
            btc4 = ohlcv_frame(btc4_raw)
            btcd = ohlcv_frame(btcd_raw)

            async def one(item):
                async with sem:
                    symbol = str(item["Symbol"])
                    try:
                        raw4, rawd, raww = await asyncio.gather(
                            asyncio.wait_for(exchange.fetch_ohlcv(symbol, timeframe="4h", limit=180), timeout=30),
                            asyncio.wait_for(exchange.fetch_ohlcv(symbol, timeframe="1d", limit=365), timeout=30),
                            asyncio.wait_for(exchange.fetch_ohlcv(symbol, timeframe="1w", limit=220), timeout=30),
                        )
                        df4 = ohlcv_frame(raw4)
                        dfd = ohlcv_frame(rawd)
                        dfw = ohlcv_frame(raww)
                        cfg = ScreenerConfig(
                            exchange_id=exchange_id,
                            quote=quote,
                            universe_size=50,
                            min_quote_volume=5_000_000,
                            score_threshold=80,
                            min_gross_profit_pct=30.0,
                        )
                        result = score_setup(df4, dfd, btc4, cfg, dfw=dfw, btcd=btcd)
                        return flatten_deep_score(item, result)
                    except Exception as exc:
                        errors.append(
                            f"deep/{exchange_id}/{symbol}: {type(exc).__name__}: {str(exc)[:180]}"
                        )
                        return None

            scored = await asyncio.gather(*(one(item) for item in items))
            local.extend([row for row in scored if row])
        finally:
            await exchange.close()
        return local

    results = await asyncio.gather(
        *(score_exchange(exchange_id, items) for exchange_id, items in grouped.items()),
        return_exceptions=True,
    )
    for exchange_id, result in zip(grouped, results):
        if isinstance(result, Exception):
            errors.append(f"deep/{exchange_id}: {type(result).__name__}: {result}")
        else:
            rows.extend(result)
    return rows, errors


def merge_deep_scores(existing: pd.DataFrame, fresh: pd.DataFrame, eligible_bases: set) -> pd.DataFrame:
    if not existing.empty:
        existing = existing[existing["Base"].astype(str).isin(eligible_bases)].copy()
    if fresh.empty:
        return existing
    if existing.empty:
        return fresh.reset_index(drop=True)
    refreshed = set(fresh["Base"].astype(str))
    existing = existing[~existing["Base"].astype(str).isin(refreshed)]
    return pd.concat([fresh, existing], ignore_index=True)


def refresh_execution_metadata(scores: pd.DataFrame, universe: pd.DataFrame) -> pd.DataFrame:
    """
    Refresh execution/listing metadata for every cached deep-score row on every run.

    This prevents older technical scores from carrying blank Kraken/Crypto.com fields
    after a universe/liquidity-rule migration. Technical timestamps can remain older
    until their rotation slot is rescored, but execution safety always reflects the
    latest universe snapshot.
    """
    if scores.empty or universe.empty:
        return scores

    execution_cols = [
        "Base",
        "Cross-exchange quote volume",
        "Kraken available",
        "Crypto.com available",
        "Kraken USD-like 24h volume",
        "Crypto.com USD-like 24h volume",
        "Execution available",
        "Execution venues",
        "Execution venue count",
        "Execution max USD-like 24h volume",
        "Execution liquidity pass",
        "Execution reason",
    ]
    available = [col for col in execution_cols if col in universe.columns]
    meta = universe[available].copy()
    meta["Base"] = meta["Base"].astype(str)

    out = scores.copy()
    refresh_cols = [col for col in available if col != "Base"]
    out = out.drop(columns=refresh_cols, errors="ignore")
    out["Base"] = out["Base"].astype(str)
    out = out.merge(meta, on="Base", how="left")

    exec_pass = out.get("Execution liquidity pass", False)
    if isinstance(exec_pass, pd.Series):
        exec_pass = exec_pass.fillna(False).astype(bool)
    else:
        exec_pass = pd.Series(False, index=out.index)

    score = pd.to_numeric(out.get("Swing score"), errors="coerce").fillna(0)
    rs = pd.to_numeric(out.get("RS vs BTC %"), errors="coerce").fillna(-999)
    eligible = out.get("Swing eligible", False)
    if isinstance(eligible, pd.Series):
        eligible = eligible.fillna(False).astype(bool)
    else:
        eligible = pd.Series(False, index=out.index)
    candle_clear = out.get("Candle caution", "CLEAR").astype(str).ne("CAUTION")
    btc_or_rs = out["Base"].eq("BTC") | rs.gt(0)

    buy_mask = eligible & score.ge(80) & btc_or_rs & candle_clear & exec_pass
    watch_mask = eligible | score.ge(65)
    out["Swing status"] = np.select(
        [buy_mask, watch_mask],
        ["BUY", "WATCH"],
        default="PASS",
    )

    acc_verdict = out.get("Accumulation verdict", "").fillna("").astype(str)
    acc_score = pd.to_numeric(out.get("Accumulation score"), errors="coerce").fillna(0)
    acc_ready = acc_verdict.eq("ACCUMULATION READY")
    acc_watch = acc_ready | acc_verdict.str.startswith("WATCH") | acc_score.ge(50)
    out["Accumulation status"] = np.select(
        [acc_ready & exec_pass, acc_watch],
        ["ACCUMULATE", "WATCH"],
        default="PASS",
    )
    return out


def prepared_opportunity_feeds(scores: pd.DataFrame) -> Tuple[pd.DataFrame, pd.DataFrame]:
    if scores.empty:
        return pd.DataFrame(), pd.DataFrame()

    swing = scores[scores["Swing status"].isin(["BUY", "WATCH"])].copy()
    if not swing.empty:
        swing["_status_rank"] = swing["Swing status"].map({"BUY": 0, "WATCH": 1}).fillna(2)
        swing["_shape_rank"] = np.where(
            swing.get("Swing eligible", False).fillna(False).astype(bool), 0, 1
        )
        swing["_execution_rank"] = np.where(
            swing.get("Execution liquidity pass", False).fillna(False).astype(bool), 0, 1
        )
        swing["Opportunity stage"] = np.select(
            [
                swing["Swing status"].eq("BUY"),
                swing.get("Swing eligible", False).fillna(False).astype(bool)
                & swing.get("Execution liquidity pass", False).fillna(False).astype(bool),
                swing.get("Swing eligible", False).fillna(False).astype(bool),
            ],
            [
                "READY",
                "TECHNICAL WATCH",
                "LIQUIDITY / PLATFORM WATCH",
            ],
            default="EARLY WATCH",
        )
        swing = swing.sort_values(
            ["_status_rank", "_shape_rank", "_execution_rank", "Swing score", "24h quote volume"],
            ascending=[True, True, True, False, False],
            na_position="last",
        ).drop(columns=["_status_rank", "_shape_rank", "_execution_rank"])

    accumulation = scores[
        scores["Accumulation status"].isin(["ACCUMULATE", "WATCH"])
    ].copy()
    if not accumulation.empty:
        accumulation["_status_rank"] = accumulation["Accumulation status"].map(
            {"ACCUMULATE": 0, "WATCH": 1}
        ).fillna(2)
        accumulation["_ready_rank"] = np.where(
            accumulation.get("Accumulation verdict", "").fillna("").astype(str).eq("ACCUMULATION READY"),
            0,
            1,
        )
        accumulation["_execution_rank"] = np.where(
            accumulation.get("Execution liquidity pass", False).fillna(False).astype(bool), 0, 1
        )
        accumulation["Opportunity stage"] = np.select(
            [
                accumulation["Accumulation status"].eq("ACCUMULATE"),
                accumulation.get("Accumulation verdict", "").fillna("").astype(str).eq("ACCUMULATION READY"),
            ],
            ["READY", "LIQUIDITY / PLATFORM WATCH"],
            default="BASE DEVELOPING",
        )
        accumulation = accumulation.sort_values(
            ["_status_rank", "_ready_rank", "_execution_rank", "Accumulation score", "24h quote volume"],
            ascending=[True, True, True, False, False],
            na_position="last",
        ).drop(columns=["_status_rank", "_ready_rank", "_execution_rank"])

    return swing.reset_index(drop=True), accumulation.reset_index(drop=True)


async def run(args) -> int:
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    manifest_path = output / "manifest.json"
    manifest = load_manifest(manifest_path)

    # Macro sources are refreshed by the scheduled worker, never while a user is
    # waiting for the Streamlit page to render.
    macro_task = asyncio.create_task(asyncio.to_thread(refresh_macro_snapshot, output))

    all_markets, universe, audits, universe_errors = await load_all_universes(
        args.quote,
        args.market_data_min_quote_volume,
        args.discovery_min_combined_volume,
        args.buy_min_combined_volume,
        args.buy_min_execution_volume,
    )
    if universe.empty:
        await macro_task
        save_json({
            "generated_at": now_iso(),
            "status": "DATA ISSUE",
            "exchanges": audits,
            "errors": universe_errors,
        }, output / "audit.json")
        return 1

    save_csv(all_markets, output / "eligible_markets.csv.gz")
    save_csv(universe, output / "universe.csv.gz")

    # Rotate deterministically through every eligible underlying coin.
    bases = sorted(universe["Base"].astype(str).unique())
    cursor = int(manifest.get("discovery_cursor", 0) or 0)
    if cursor >= len(bases):
        cursor = 0
    batch = []
    for step in range(min(args.batch_size, len(bases))):
        batch.append(bases[(cursor + step) % len(bases)])
    next_cursor = (cursor + len(batch)) % max(len(bases), 1)

    fresh_rows, discovery_errors = await discovery_batch(
        universe, batch, concurrency=args.concurrency
    )
    fresh = pd.DataFrame(fresh_rows)
    existing = load_csv(output / "discovery.csv.gz")
    merged = merge_discovery(existing, fresh, set(bases))
    if not merged.empty:
        merged = merged.sort_values(
            ["discovery_rank", "24h quote volume"],
            ascending=[False, False],
            na_position="last",
        ).reset_index(drop=True)
    save_csv(merged, output / "discovery.csv.gz")

    active = active_monitor(merged, all_markets, args.max_active)
    save_csv(active, output / "active_monitor.csv.gz")

    # Deep scoring uses the exact same shared rule engine as Quick Analysis.
    # Prioritise the strongest active Swing candidates while also rotating through
    # the current discovery batch so Accumulation coverage is not breakout-biased.
    active_priority = (
        active.sort_values(["discovery_rank", "24h quote volume"], ascending=[False, False])["Base"]
        .astype(str).head(max(1, args.deep_score_size // 2)).tolist()
        if not active.empty else []
    )
    rotating_priority = [str(base) for base in batch[: max(1, args.deep_score_size // 2)]]
    deep_bases = []
    for base in active_priority + rotating_priority:
        if base not in deep_bases:
            deep_bases.append(base)
        if len(deep_bases) >= args.deep_score_size:
            break

    deep_rows, deep_errors = await deep_score_batch(
        universe,
        deep_bases,
        args.quote,
        concurrency=max(2, min(args.concurrency, 8)),
    )
    deep_fresh = pd.DataFrame(deep_rows)
    deep_existing = load_csv(output / "deep_scores.csv.gz")
    deep_scores = merge_deep_scores(deep_existing, deep_fresh, set(bases))
    deep_scores = refresh_execution_metadata(deep_scores, universe)
    if not deep_scores.empty:
        deep_scores = deep_scores.sort_values(
            ["deep_scored_at", "Swing score"],
            ascending=[False, False],
            na_position="last",
        ).reset_index(drop=True)
    save_csv(deep_scores, output / "deep_scores.csv.gz")

    swing_feed, accumulation_feed = prepared_opportunity_feeds(deep_scores)
    save_csv(swing_feed, output / "swing_opportunities.csv.gz")
    save_csv(accumulation_feed, output / "accumulation_opportunities.csv.gz")

    total_unique = len(bases)
    scanned_unique = (
        int((merged["status"] == "SCANNED").sum())
        if not merged.empty and "status" in merged.columns else 0
    )
    coverage_pct = scanned_unique / total_unique * 100 if total_unique else 0.0
    runs_per_sweep = math.ceil(total_unique / args.batch_size) if total_unique else 0
    nominal_minutes = runs_per_sweep * 5

    macro_snapshot = await macro_task

    audit = {
        "generated_at": now_iso(),
        "quote": args.quote,
        "market_data_min_24h_quote_volume": args.market_data_min_quote_volume,
        "discovery_min_combined_24h_volume": args.discovery_min_combined_volume,
        "buy_min_combined_24h_volume": args.buy_min_combined_volume,
        "buy_min_execution_24h_volume": args.buy_min_execution_volume,
        "exchange_counts": audits,
        "eligible_market_rows": int(len(all_markets)),
        "unique_eligible_coins": int(total_unique),
        "deduplicated_cross_exchange_coins": int(total_unique),
        "discovery_batch_size": args.batch_size,
        "discovery_batch_scanned": len(batch),
        "discovery_rows_current": scanned_unique,
        "discovery_coverage_pct": round(coverage_pct, 1),
        "active_candidates": int(len(active)),
        "deep_score_batch": int(len(deep_bases)),
        "deep_scores_current": int(len(deep_scores)),
        "swing_opportunities": int(len(swing_feed)),
        "accumulation_opportunities": int(len(accumulation_feed)),
        "estimated_full_sweep_minutes_at_5m_cadence": nominal_minutes,
        "macro_updated_at": macro_snapshot.get("generated_at"),
        "macro_available": bool(macro_snapshot.get("available")),
        "errors": universe_errors + discovery_errors[:30] + deep_errors[:30],
    }
    save_json(audit, output / "audit.json")

    manifest = {
        "updated_at": now_iso(),
        "status": "CURRENT" if not universe_errors else "PARTIAL",
        "quote": args.quote,
        "market_data_min_quote_volume": args.market_data_min_quote_volume,
        "discovery_min_combined_volume": args.discovery_min_combined_volume,
        "buy_min_combined_volume": args.buy_min_combined_volume,
        "buy_min_execution_volume": args.buy_min_execution_volume,
        "eligible_market_rows": int(len(all_markets)),
        "unique_eligible_coins": int(total_unique),
        "discovery_cursor": next_cursor,
        "discovery_batch_size": args.batch_size,
        "discovery_batch_bases": batch,
        "discovery_coverage_pct": round(coverage_pct, 1),
        "active_candidates": int(len(active)),
        "deep_score_batch": int(len(deep_bases)),
        "deep_scores_current": int(len(deep_scores)),
        "swing_opportunities": int(len(swing_feed)),
        "accumulation_opportunities": int(len(accumulation_feed)),
        "estimated_full_sweep_minutes": nominal_minutes,
        "macro_updated_at": macro_snapshot.get("generated_at"),
        "macro_available": bool(macro_snapshot.get("available")),
        "errors": universe_errors + discovery_errors[:15] + deep_errors[:15],
    }
    save_json(manifest, manifest_path)

    print(json.dumps(audit, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="CL Signal all-market Crypto discovery and active monitor."
    )
    parser.add_argument("--quote", default="USDT")
    parser.add_argument("--market-data-min-quote-volume", type=float, default=250_000)
    parser.add_argument("--discovery-min-combined-volume", type=float, default=1_000_000)
    parser.add_argument("--buy-min-combined-volume", type=float, default=5_000_000)
    parser.add_argument("--buy-min-execution-volume", type=float, default=1_000_000)
    parser.add_argument("--batch-size", type=int, default=150)
    parser.add_argument("--max-active", type=int, default=150)
    parser.add_argument("--deep-score-size", type=int, default=60)
    parser.add_argument("--concurrency", type=int, default=10)
    parser.add_argument("--output-dir", default="prepared_crypto")
    args = parser.parse_args()
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
