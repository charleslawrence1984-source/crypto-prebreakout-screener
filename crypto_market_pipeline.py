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


STABLE_BASES = {
    "USDT", "USDC", "DAI", "FDUSD", "TUSD", "USDE", "PYUSD", "EURC", "USD1",
    "BUSD", "USDP", "GUSD", "LUSD", "FRAX", "EUR", "GBP",
}
LEVERAGED_SUFFIXES = ("UP", "DOWN", "BULL", "BEAR", "2L", "2S", "3L", "3S", "5L", "5S")
EXCHANGES = {
    "binance": "Binance",
    "okx": "OKX",
    "bybit": "Bybit",
}


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
    config = {"enableRateLimit": True, "options": {"defaultType": "spot"}}
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


def is_leveraged(base: str) -> bool:
    base = str(base or "").upper()
    return any(
        base.endswith(suffix) and len(base) > len(suffix) + 2
        for suffix in LEVERAGED_SUFFIXES
    )


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
    min_quote_volume: float,
) -> Tuple[pd.DataFrame, dict]:
    exchange = make_exchange(exchange_id)
    label = EXCHANGES[exchange_id]
    try:
        markets = await asyncio.wait_for(exchange.load_markets(), timeout=45)
        if not exchange.has.get("fetchTickers"):
            raise RuntimeError(f"{exchange_id} does not expose fetchTickers")
        tickers = await asyncio.wait_for(exchange.fetch_tickers(), timeout=60)

        rows = []
        counts = {
            "active_spot_total": 0,
            "quote_matched": 0,
            "stablecoin_excluded": 0,
            "leveraged_excluded": 0,
            "volume_unavailable": 0,
            "below_liquidity": 0,
            "eligible": 0,
        }

        for symbol, market in markets.items():
            if not market.get("spot") or market.get("active") is False:
                continue
            counts["active_spot_total"] += 1
            if str(market.get("quote") or "").upper() != quote:
                continue
            counts["quote_matched"] += 1

            base = str(market.get("base") or "").upper()
            if base in STABLE_BASES:
                counts["stablecoin_excluded"] += 1
                continue
            if is_leveraged(base):
                counts["leveraged_excluded"] += 1
                continue

            ticker = tickers.get(symbol) or {}
            qv = quote_volume(ticker)
            price = last_price(ticker)
            if not math.isfinite(qv):
                counts["volume_unavailable"] += 1
                continue
            if qv < min_quote_volume:
                counts["below_liquidity"] += 1
                continue

            counts["eligible"] += 1
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
        counts["min_quote_volume"] = min_quote_volume
        return frame, counts
    finally:
        await exchange.close()


async def load_all_universes(quote: str, min_quote_volume: float):
    tasks = [
        load_exchange_snapshot(exchange_id, quote, min_quote_volume)
        for exchange_id in EXCHANGES
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    frames = []
    audits = []
    errors = []
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
        frame, audit = result
        frames.append(frame)
        audit["status"] = "CURRENT"
        audits.append(audit)

    combined = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if combined.empty:
        return combined, pd.DataFrame(), audits, errors

    combined = combined.sort_values(
        ["Base", "24h quote volume"], ascending=[True, False]
    )
    # One underlying coin enters the rotating discovery queue once. Use its most
    # liquid eligible market as the analysis venue, but retain all venue coverage.
    grouped = []
    for base, group in combined.groupby("Base", sort=True):
        best = group.iloc[0].to_dict()
        best["Eligible exchange count"] = int(group["Exchange id"].nunique())
        best["Eligible exchanges"] = ", ".join(sorted(group["Exchange"].unique()))
        best["Cross-exchange quote volume"] = float(group["24h quote volume"].sum())
        grouped.append(best)

    deduped = pd.DataFrame(grouped).sort_values(
        "Cross-exchange quote volume", ascending=False
    ).reset_index(drop=True)
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


async def run(args) -> int:
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    manifest_path = output / "manifest.json"
    manifest = load_manifest(manifest_path)

    all_markets, universe, audits, universe_errors = await load_all_universes(
        args.quote, args.min_quote_volume
    )
    if universe.empty:
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

    total_unique = len(bases)
    scanned_unique = (
        int((merged["status"] == "SCANNED").sum())
        if not merged.empty and "status" in merged.columns else 0
    )
    coverage_pct = scanned_unique / total_unique * 100 if total_unique else 0.0
    runs_per_sweep = math.ceil(total_unique / args.batch_size) if total_unique else 0
    nominal_minutes = runs_per_sweep * 5

    audit = {
        "generated_at": now_iso(),
        "quote": args.quote,
        "minimum_24h_quote_volume": args.min_quote_volume,
        "exchange_counts": audits,
        "eligible_market_rows": int(len(all_markets)),
        "unique_eligible_coins": int(total_unique),
        "deduplicated_cross_exchange_coins": int(total_unique),
        "discovery_batch_size": args.batch_size,
        "discovery_batch_scanned": len(batch),
        "discovery_rows_current": scanned_unique,
        "discovery_coverage_pct": round(coverage_pct, 1),
        "active_candidates": int(len(active)),
        "estimated_full_sweep_minutes_at_5m_cadence": nominal_minutes,
        "errors": universe_errors + discovery_errors[:50],
    }
    save_json(audit, output / "audit.json")

    manifest = {
        "updated_at": now_iso(),
        "status": "CURRENT" if not universe_errors else "PARTIAL",
        "quote": args.quote,
        "min_quote_volume": args.min_quote_volume,
        "eligible_market_rows": int(len(all_markets)),
        "unique_eligible_coins": int(total_unique),
        "discovery_cursor": next_cursor,
        "discovery_batch_size": args.batch_size,
        "discovery_batch_bases": batch,
        "discovery_coverage_pct": round(coverage_pct, 1),
        "active_candidates": int(len(active)),
        "estimated_full_sweep_minutes": nominal_minutes,
        "errors": universe_errors + discovery_errors[:25],
    }
    save_json(manifest, manifest_path)

    print(json.dumps(audit, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="CL Signal all-market Crypto discovery and active monitor."
    )
    parser.add_argument("--quote", default="USDT")
    parser.add_argument("--min-quote-volume", type=float, default=5_000_000)
    parser.add_argument("--batch-size", type=int, default=150)
    parser.add_argument("--max-active", type=int, default=150)
    parser.add_argument("--concurrency", type=int, default=10)
    parser.add_argument("--output-dir", default="prepared_crypto")
    args = parser.parse_args()
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
