from __future__ import annotations

import asyncio
import io
import json
import math
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import pandas as pd
import requests
import streamlit as st
import yfinance as yf

from cl_signal_ui import render_module_header
from trade_rules import FundamentalSnapshot, evaluate_price_setup, score_fundamental_snapshot
from crypto_market_pipeline import make_exchange, ohlcv_frame, flatten_deep_score
from crypto_rule_engine import ScreenerConfig, score_setup


st.set_page_config(page_title="CL Signal · Kraken Funded", page_icon="💼", layout="wide")

ROOT = Path(__file__).resolve().parent
TRADE_TECHNICAL_DIR = ROOT / "prepared_trade_technicals"
PREPARED_CRYPTO_DIR = ROOT / "prepared_crypto"
TRADE_FUNDAMENTAL_DIR = ROOT / "prepared_trade_fundamentals"

# Kraken Funded challenge overlay. The underlying CL Signal rulebooks are unchanged.
FUNDED_START_BALANCE = 1_000.0
FUNDED_TARGET_BALANCE = 1_120.0
FUNDED_FAIL_BALANCE = 970.0
FUNDED_STANDARD_RISK_USD = 4.0
FUNDED_MAX_RISK_USD = 5.0
FUNDED_MAX_TOTAL_OPEN_RISK_USD = 10.0
FUNDED_MIN_RR = 2.5
FUNDED_MAX_POSITION_PCT = 35.0

KRAKEN_FUNDED_STOCKS = [
    "NVDA", "NFLX", "SPCX", "GOOGL", "CRCL", "AAPL", "TSLA", "MSTR", "HOOD",
    "SNDK", "META", "AMZN", "BABA", "AMD", "ARM", "AVGO", "BB", "CBRS",
    "COIN", "CRWV", "HIMS", "INTC", "LITE", "MRVL", "MSFT", "MU", "ORCL",
    "PLTR", "RKLB", "TSM",
]

KRAKEN_FUNDED_CRYPTO = [
    "BTC", "ETH", "LINK", "BCH", "DOGE", "SHIB", "ARB", "BONK", "AAVE", "AVAX",
    "BNB", "CRV", "DOT", "FIL", "GRASS", "ADA", "HBAR", "INJ", "KAITO", "LDO",
    "LTC", "MOODENG", "NEAR", "PNUT", "SOL", "S", "TRUMP", "UNI", "WLD", "XRP",
    "ALGO", "APT", "ATOM",
]


def safe_number(value):
    try:
        number = float(value)
        return number if np.isfinite(number) else np.nan
    except Exception:
        return np.nan


def first_existing(frame: pd.DataFrame, names: list[str]) -> str | None:
    for name in names:
        if name in frame.columns:
            return name
    return None



def _json_list(value):
    if isinstance(value, list):
        return value
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return []
    try:
        parsed = json.loads(str(value))
        return parsed if isinstance(parsed, list) else []
    except Exception:
        return []


def _text(value, default=""):
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return default
    text = str(value).strip()
    return text or default


def snapshot_from_record(row: dict) -> FundamentalSnapshot:
    earnings_date = None
    try:
        parsed = pd.to_datetime(row.get("Earnings date"), errors="coerce")
        if not pd.isna(parsed):
            earnings_date = pd.Timestamp(parsed)
    except Exception:
        pass
    symbol = _text(row.get("Ticker"))
    return FundamentalSnapshot(
        symbol=symbol,
        company=_text(row.get("Company"), symbol),
        sector=_text(row.get("Sector"), "UNAVAILABLE"),
        industry=_text(row.get("Industry"), "UNAVAILABLE"),
        currency=_text(row.get("Currency"), "USD").upper(),
        market_cap=safe_number(row.get("Market cap")),
        roic=safe_number(row.get("ROIC")),
        roe=safe_number(row.get("ROE")),
        operating_margin=safe_number(row.get("Operating margin")),
        fcf_margin=safe_number(row.get("FCF margin")),
        annual_fcf=[safe_number(v) for v in _json_list(row.get("Annual FCF")) if np.isfinite(safe_number(v))],
        annual_net_income=[safe_number(v) for v in _json_list(row.get("Annual net income")) if np.isfinite(safe_number(v))],
        net_debt_to_fcf=safe_number(row.get("Net debt / FCF")),
        interest_coverage=safe_number(row.get("Interest coverage")),
        no_interest_expense=str(row.get("No interest expense")).strip().lower() in {"true", "1", "yes"},
        current_ratio=safe_number(row.get("Current ratio")),
        revenue_growth=safe_number(row.get("Revenue growth")),
        earnings_growth=safe_number(row.get("Earnings growth")),
        operating_growth=safe_number(row.get("Operating growth")),
        growth_source=_text(row.get("Growth source"), "PREPARED"),
        share_change=safe_number(row.get("Share change")),
        distribution_ratio=safe_number(row.get("Distribution ratio")),
        earnings_date=earnings_date,
        earnings_source=_text(row.get("Earnings source"), "UNVERIFIED"),
        trailing_pe=safe_number(row.get("Trailing PE")),
        price_sales=safe_number(row.get("Price sales")),
        missing_hard_inputs=[str(v) for v in _json_list(row.get("Missing hard inputs"))],
        trailing_fcf=safe_number(row.get("Trailing FCF")),
    )


@st.cache_data(ttl=300, show_spinner=False)
def funded_fundamental_results() -> dict:
    frames = []
    for kind in ("nasdaq", "nyse", "otc"):
        path = TRADE_FUNDAMENTAL_DIR / f"{kind}.csv.gz"
        if not path.exists():
            continue
        try:
            frame = pd.read_csv(path, compression="gzip")
            if not frame.empty:
                frames.append(frame)
        except Exception:
            continue
    if not frames:
        return {}

    prepared = pd.concat(frames, ignore_index=True).drop_duplicates("Ticker", keep="last")
    snapshots = []
    for record in prepared.to_dict(orient="records"):
        try:
            snapshots.append(snapshot_from_record(record))
        except Exception:
            continue

    usd_to_gbp = np.nan
    try:
        fx = yf.download("GBPUSD=X", period="5d", interval="1d", auto_adjust=False, progress=False)
        close = pd.to_numeric(fx["Close"], errors="coerce").dropna()
        if len(close):
            usd_to_gbp = 1.0 / float(close.iloc[-1])
    except Exception:
        pass
    if not np.isfinite(usd_to_gbp):
        usd_to_gbp = 0.75

    output = {}
    for snapshot in snapshots:
        if snapshot.symbol not in KRAKEN_FUNDED_STOCKS:
            continue
        rate = usd_to_gbp if snapshot.currency == "USD" else 1.0
        try:
            result = score_fundamental_snapshot(
                snapshot, snapshots, rate, earnings_sessions=None,
                official_event_verified=False, apply_event_gate=False,
            )
            failures = list(result.get("fundamental_failures", []))
            score = float(result.get("fundamental_score", 0) or 0)
            output[snapshot.symbol] = {
                "score": score,
                "pass": not failures and score >= 65,
                "reason": "; ".join(failures) if failures else "PASS",
            }
        except Exception as exc:
            output[snapshot.symbol] = {"score": np.nan, "pass": False, "reason": f"FUNDAMENTAL ERROR: {type(exc).__name__}"}
    return output


def _extract_yahoo_frame(batch: pd.DataFrame, symbol: str):
    if batch is None or batch.empty:
        return None
    if isinstance(batch.columns, pd.MultiIndex):
        level0 = set(str(v) for v in batch.columns.get_level_values(0))
        level1 = set(str(v) for v in batch.columns.get_level_values(1))
        if symbol in level0:
            frame = batch[symbol].copy()
        elif symbol in level1:
            frame = batch.xs(symbol, level=1, axis=1).copy()
        else:
            return None
    else:
        frame = batch.copy()
    frame = frame.rename(columns={str(col): str(col).title() for col in frame.columns})
    needed = {"Open", "High", "Low", "Close", "Volume"}
    return frame if needed.issubset(frame.columns) else None


def scan_funded_stocks_live() -> pd.DataFrame:
    fundamentals = funded_fundamental_results()
    benchmark_raw = yf.download("^GSPC", period="3y", interval="1d", auto_adjust=False, progress=False)
    benchmark = _extract_yahoo_frame(benchmark_raw, "^GSPC")
    if benchmark is None:
        benchmark = benchmark_raw
    prices = yf.download(
        KRAKEN_FUNDED_STOCKS, period="3y", interval="1d", group_by="ticker",
        auto_adjust=False, threads=True, progress=False,
    )

    rows = []
    for symbol in KRAKEN_FUNDED_STOCKS:
        fundamental = fundamentals.get(symbol, {})
        fscore = safe_number(fundamental.get("score"))
        if not fundamental:
            rows.append({
                "Ticker": symbol, "Fundamentals": "— unavailable", "Technical state": "DATA UNAVAILABLE",
                "Gate": "FUNDAMENTALS NOT PREPARED", "Fundamental score": np.nan,
            })
            continue
        if not fundamental.get("pass"):
            rows.append({
                "Ticker": symbol, "Fundamentals": f"❌ {fscore:.0f}" if np.isfinite(fscore) else "❌",
                "Technical state": "BLOCKED", "Gate": fundamental.get("reason", "FUNDAMENTAL FAIL"),
                "Fundamental score": fscore,
            })
            continue
        frame = _extract_yahoo_frame(prices, symbol)
        if frame is None or len(frame.dropna(subset=["Close", "Volume"])) < 252:
            rows.append({
                "Ticker": symbol, "Fundamentals": f"✅ {fscore:.0f}", "Technical state": "DATA UNAVAILABLE",
                "Gate": "INSUFFICIENT PRICE HISTORY", "Fundamental score": fscore,
            })
            continue
        technical = evaluate_price_setup(frame, benchmark)
        clean = frame.dropna(subset=["Close", "Volume"])
        turnover_usd = float((clean["Close"] * clean["Volume"]).tail(20).median())
        liquidity_gbpm = turnover_usd * 0.75 / 1_000_000
        row = {
            "Ticker": symbol,
            "Fundamentals": f"✅ {fscore:.0f}",
            "Fundamental score": fscore,
            "Technical state": technical.get("technical_state"),
            "Gate": technical.get("technical_reason"),
            "MACD progress": technical.get("macd_progress"),
            "RSI": technical.get("rsi"),
            "Entry": technical.get("entry"),
            "Stop": technical.get("stop"),
            "Target": technical.get("target"),
            "R:R": technical.get("reward_risk"),
            "Median traded value GBPm": liquidity_gbpm,
        }
        row["Funded status"] = funded_stock_status(pd.Series(row))
        plan = funded_position_plan(row.get("Entry"), row.get("Stop"))
        row["Position $"] = round(plan[1], 2) if np.isfinite(plan[1]) else np.nan
        row["Risk $"] = round(plan[2], 2) if np.isfinite(plan[2]) else np.nan
        rows.append(row)

    output = pd.DataFrame(rows)
    if "Funded status" not in output.columns:
        output["Funded status"] = output.apply(funded_stock_status, axis=1)
    return output


async def scan_funded_crypto_live_async() -> pd.DataFrame:
    exchange = make_exchange("kraken")
    rows = []
    try:
        await exchange.load_markets()
        tickers = await exchange.fetch_tickers()
        btc4 = ohlcv_frame(await exchange.fetch_ohlcv("BTC/USD", timeframe="4h", limit=180))
        btcd = ohlcv_frame(await exchange.fetch_ohlcv("BTC/USD", timeframe="1d", limit=365))

        for base in KRAKEN_FUNDED_CRYPTO:
            symbol = f"{base}/USD"
            market = exchange.markets.get(symbol)
            if market is None:
                # Kraken/CCXT sometimes exposes USD pairs through an alias; try USDT.
                symbol = f"{base}/USDT"
                market = exchange.markets.get(symbol)
            if market is None:
                rows.append({"Coin": base, "CL Signal": "⚪ DATA UNAVAILABLE", "Funded status": "⚪ PAIR NOT FOUND", "Gate": "PAIR NOT FOUND ON KRAKEN"})
                continue

            try:
                raw4, rawd, raww = await asyncio.gather(
                    exchange.fetch_ohlcv(symbol, timeframe="4h", limit=180),
                    exchange.fetch_ohlcv(symbol, timeframe="1d", limit=365),
                    exchange.fetch_ohlcv(symbol, timeframe="1w", limit=220),
                )
                df4, dfd, dfw = ohlcv_frame(raw4), ohlcv_frame(rawd), ohlcv_frame(raww)
                cfg = ScreenerConfig(
                    exchange_id="kraken", quote=symbol.split("/")[-1],
                    universe_size=len(KRAKEN_FUNDED_CRYPTO), min_quote_volume=0,
                    score_threshold=80,
                )
                result = score_setup(df4, dfd, btc4, cfg, dfw=dfw, btcd=btcd)
                ticker = tickers.get(symbol, {}) or {}
                quote_volume = safe_number(ticker.get("quoteVolume"))
                if not np.isfinite(quote_volume):
                    quote_volume = 0.0
                item = {
                    "Base": base, "Symbol": symbol, "Exchange": "Kraken", "Exchange id": "kraken",
                    "24h quote volume": quote_volume,
                    "Eligible exchange count": 1, "Eligible exchanges": "Kraken",
                    "Major venue listing count": 1, "Major venue listings": "Kraken", "Major venues checked": 1,
                    "Cross-exchange quote volume": quote_volume,
                    "Kraken available": True, "Crypto.com available": False,
                    "Kraken USD-like 24h volume": quote_volume, "Crypto.com USD-like 24h volume": 0.0,
                    "Execution available": True, "Execution venues": "Kraken",
                    "Execution max USD-like 24h volume": quote_volume,
                    "Execution liquidity pass": quote_volume >= 1_000_000,
                    "Execution reason": "PASS" if quote_volume >= 1_000_000 else "Kraken 24h volume below $1m",
                }
                flat = flatten_deep_score(item, result)
                flat["Coin"] = base
                rows.append(flat)
            except Exception as exc:
                rows.append({
                    "Coin": base, "CL Signal": "⚪ DATA UNAVAILABLE", "Funded status": "⚪ SCAN ERROR",
                    "Gate": f"{type(exc).__name__}: {str(exc)[:120]}",
                })
    finally:
        await exchange.close()

    return pd.DataFrame(rows)


def scan_funded_crypto_live() -> pd.DataFrame:
    raw = asyncio.run(scan_funded_crypto_live_async())
    if raw.empty:
        return raw

    # Reuse the same funded overlay fields used by the prepared crypto view.
    raw["CL Signal"] = np.select(
        [
            raw.get("Swing status", pd.Series("", index=raw.index)).astype(str).str.upper().eq("BUY"),
            raw.get("Swing status", pd.Series("", index=raw.index)).astype(str).str.upper().eq("WATCH"),
            raw.get("Swing status", pd.Series("", index=raw.index)).astype(str).str.upper().eq("PASS"),
        ],
        ["🟢 READY / BUY", "🟡 WATCH", "🔴 PASS"],
        default=raw.get("CL Signal", pd.Series("⚪ DATA UNAVAILABLE", index=raw.index)),
    )
    raw["Score"] = pd.to_numeric(raw.get("Swing score"), errors="coerce").round(1)
    raw["Entry timing"] = raw.get("Entry timing", "—")
    raw["RSI"] = pd.to_numeric(raw.get("RSI"), errors="coerce").round(1)
    raw["To resistance %"] = pd.to_numeric(raw.get("Distance %"), errors="coerce").round(1)
    raw["Funded entry"] = pd.to_numeric(raw.get("Active entry"), errors="coerce")
    raw["Funded stop"] = pd.to_numeric(raw.get("Invalidation"), errors="coerce")
    raw["Funded target"] = pd.to_numeric(raw.get("First technical target"), errors="coerce")
    raw["Funded R:R"] = pd.to_numeric(raw.get("Current R:R", raw.get("R:R")), errors="coerce").round(2)
    execution_series = raw.get(
        "Execution liquidity pass",
        pd.Series(False, index=raw.index),
    )
    if not isinstance(execution_series, pd.Series):
        execution_series = pd.Series(bool(execution_series), index=raw.index)
    raw["Execution"] = execution_series.apply(lambda v: "✅" if bool(v) else "❌")

    funded_status, positions, risks = [], [], []
    for _, row in raw.iterrows():
        core = str(row.get("CL Signal") or "")
        timing = str(row.get("Entry timing") or "").upper()
        execution = str(row.get("Execution") or "")
        rr = safe_number(row.get("Funded R:R"))
        entry = safe_number(row.get("Funded entry"))
        stop = safe_number(row.get("Funded stop"))
        if core == "🔴 PASS":
            status = "🔴 FUNDED PASS"
        elif core == "🟡 WATCH":
            status = "🟡 FUNDED WATCH"
        elif core.startswith("⚪"):
            status = str(row.get("Funded status") or "⚪ DATA UNAVAILABLE")
        elif "EXTENDED" in timing or "TOO LATE" in timing:
            status = "🔴 FUNDED PASS · LATE"
        elif execution == "❌":
            status = "🔴 FUNDED PASS · EXECUTION"
        elif not np.isfinite(rr) or rr < FUNDED_MIN_RR:
            status = "🟡 FUNDED WATCH · R:R"
        elif not np.isfinite(entry) or not np.isfinite(stop) or entry <= stop:
            status = "🟡 FUNDED WATCH · RISK PLAN"
        else:
            status = "🟢 FUNDED READY"
        plan = funded_position_plan(entry, stop)
        funded_status.append(status)
        positions.append(round(plan[1], 2) if np.isfinite(plan[1]) else np.nan)
        risks.append(round(plan[2], 2) if np.isfinite(plan[2]) else np.nan)
    raw["Funded status"] = funded_status
    raw["Position $"] = positions
    raw["Risk $"] = risks
    return raw


def funded_position_plan(entry, stop, risk_usd=FUNDED_STANDARD_RISK_USD):
    entry = safe_number(entry)
    stop = safe_number(stop)
    if not np.isfinite(entry) or not np.isfinite(stop) or entry <= stop or entry <= 0:
        return np.nan, np.nan, np.nan

    per_unit_risk = entry - stop
    risk_units = risk_usd / per_unit_risk
    max_notional = FUNDED_START_BALANCE * FUNDED_MAX_POSITION_PCT / 100
    notional = min(risk_units * entry, max_notional)
    units = notional / entry
    actual_risk = units * per_unit_risk
    return units, notional, actual_risk


def funded_stock_status(row: pd.Series) -> str:
    state = str(row.get("Technical state") or "")
    if state == "WATCH":
        return "🟡 FUNDED WATCH"
    if state not in {"ENTRY READY", "AWAITING NEXT OPEN"}:
        return "⚪ NO CURRENT SETUP"

    rr = safe_number(row.get("R:R"))
    liquidity = safe_number(row.get("Median traded value GBPm"))
    entry = safe_number(row.get("Entry"))
    stop = safe_number(row.get("Stop"))

    if not np.isfinite(rr) or rr < FUNDED_MIN_RR:
        return "🟡 FUNDED WATCH · R:R"
    if not np.isfinite(liquidity) or liquidity < 0.5:
        return "🔴 FUNDED PASS · LIQUIDITY"
    if not np.isfinite(entry) or not np.isfinite(stop) or entry <= stop:
        return "🟡 FUNDED WATCH · RISK PLAN"
    return "🟢 FUNDED READY"


@st.cache_data(ttl=300, show_spinner=False)
def load_funded_stock_rows() -> pd.DataFrame:
    frames = []
    if TRADE_TECHNICAL_DIR.exists():
        for path in TRADE_TECHNICAL_DIR.glob("*.csv.gz"):
            try:
                frame = pd.read_csv(path, compression="gzip")
            except Exception:
                continue
            if frame.empty or "Ticker" not in frame.columns:
                continue
            frame = frame[frame["Ticker"].astype(str).str.upper().isin(KRAKEN_FUNDED_STOCKS)].copy()
            if not frame.empty:
                frame["Source market"] = path.stem.replace(".csv", "")
                frames.append(frame)

    if frames:
        technical = pd.concat(frames, ignore_index=True)
        if "Median traded value GBPm" in technical.columns:
            technical["_liq"] = pd.to_numeric(technical["Median traded value GBPm"], errors="coerce")
            technical = technical.sort_values("_liq", ascending=False, na_position="last")
        technical = technical.drop_duplicates("Ticker", keep="first")
    else:
        technical = pd.DataFrame(columns=["Ticker"])

    universe = pd.DataFrame({"Ticker": KRAKEN_FUNDED_STOCKS})
    output = universe.merge(technical, on="Ticker", how="left")

    state = output.get("Technical state", pd.Series("", index=output.index)).fillna("").astype(str)
    output["CL Signal"] = np.select(
        [
            state.isin(["ENTRY READY", "AWAITING NEXT OPEN"]),
            state.eq("WATCH"),
        ],
        [
            "🟢 READY",
            "🟡 WATCH",
        ],
        default="⚪ NO CURRENT SETUP",
    )

    macd = output.get("MACD progress", pd.Series("", index=output.index)).fillna("").astype(str).str.upper()
    output["MACD"] = np.select(
        [
            macd.eq("CONFIRMED"),
            macd.eq("NEAR CROSSOVER"),
            macd.eq("IMPROVING"),
            macd.eq("WEAK"),
        ],
        [
            "✅ Confirmed",
            "🟡 Near crossover",
            "🟠 Improving",
            "❌ Weak",
        ],
        default="—",
    )

    liquidity = pd.to_numeric(
        output.get("Median traded value GBPm", pd.Series(np.nan, index=output.index)),
        errors="coerce",
    )
    output["Liquidity"] = liquidity.apply(
        lambda value: (
            "—" if not np.isfinite(value)
            else "❌ <£0.01m" if value < 0.01
            else f"{'✅' if value >= 0.5 else '❌'} £{value:.2f}m"
        )
    )

    output["RSI"] = pd.to_numeric(output.get("RSI"), errors="coerce").round(1)
    output["Entry"] = pd.to_numeric(output.get("Entry"), errors="coerce")
    output["Stop"] = pd.to_numeric(output.get("Stop"), errors="coerce")
    output["Target"] = pd.to_numeric(output.get("Target"), errors="coerce")
    output["R:R"] = pd.to_numeric(output.get("R:R"), errors="coerce").round(2)

    output["Funded status"] = output.apply(funded_stock_status, axis=1)
    stock_plans = output.apply(
        lambda row: funded_position_plan(row.get("Entry"), row.get("Stop")),
        axis=1,
    )
    output["Risk $"] = [round(plan[2], 2) if np.isfinite(plan[2]) else np.nan for plan in stock_plans]
    output["Position $"] = [round(plan[1], 2) if np.isfinite(plan[1]) else np.nan for plan in stock_plans]

    return output


@st.cache_data(ttl=60, show_spinner=False)
def load_crypto_pipeline() -> dict[str, pd.DataFrame]:
    raw_base = (
        "https://raw.githubusercontent.com/"
        "charleslawrence1984-source/crypto-prebreakout-screener/"
        "crypto-data/prepared_crypto/"
    )
    names = {
        "scores": "deep_scores.csv.gz",
        "swing": "swing_opportunities.csv.gz",
        "active": "active_monitor.csv.gz",
    }

    def fetch(filename: str):
        try:
            response = requests.get(raw_base + filename, timeout=4)
            response.raise_for_status()
            return response.content
        except Exception:
            return None

    remote = {}
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {executor.submit(fetch, filename): key for key, filename in names.items()}
        for future in as_completed(futures):
            remote[futures[future]] = future.result()

    result = {}
    for key, filename in names.items():
        try:
            payload = remote.get(key)
            if payload:
                result[key] = pd.read_csv(io.BytesIO(payload), compression="gzip")
                continue
        except Exception:
            pass
        try:
            result[key] = pd.read_csv(PREPARED_CRYPTO_DIR / filename, compression="gzip")
        except Exception:
            result[key] = pd.DataFrame()
    return result


def normalise_crypto_base(frame: pd.DataFrame) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame()
    output = frame.copy()
    base_col = first_existing(output, ["Coin", "Base", "Symbol", "Ticker"])
    if not base_col:
        return pd.DataFrame()
    output["_Base"] = (
        output[base_col].astype(str).str.upper()
        .str.replace("/USDT", "", regex=False)
        .str.replace("/USD", "", regex=False)
        .str.replace("-USD", "", regex=False)
    )
    return output


@st.cache_data(ttl=60, show_spinner=False)
def load_funded_crypto_rows() -> pd.DataFrame:
    pipeline = load_crypto_pipeline()
    frames = []

    for priority, key in enumerate(["swing", "scores", "active"]):
        frame = normalise_crypto_base(pipeline.get(key, pd.DataFrame()))
        if frame.empty:
            continue
        frame = frame[frame["_Base"].isin(KRAKEN_FUNDED_CRYPTO)].copy()
        if frame.empty:
            continue
        frame["_priority"] = priority
        frames.append(frame)

    if frames:
        combined = pd.concat(frames, ignore_index=True, sort=False)
        combined = combined.sort_values("_priority").drop_duplicates("_Base", keep="first")
    else:
        combined = pd.DataFrame(columns=["_Base"])

    universe = pd.DataFrame({"Coin": KRAKEN_FUNDED_CRYPTO})
    output = universe.merge(combined, left_on="Coin", right_on="_Base", how="left")

    status_col = first_existing(
        output,
        ["Swing status", "Decision", "Trade decision", "Monitor state", "Entry mode"],
    )
    raw_status = (
        output[status_col].fillna("").astype(str).str.upper()
        if status_col else pd.Series("", index=output.index)
    )

    output["CL Signal"] = np.select(
        [
            raw_status.str.contains("BUY|READY|ENTRY", regex=True),
            raw_status.str.contains("WATCH|EARLY|MONITOR|DEVELOP", regex=True),
            raw_status.str.contains("PASS|AVOID|BLOCK|EXTENDED", regex=True),
        ],
        [
            "🟢 READY / BUY",
            "🟡 WATCH",
            "🔴 PASS",
        ],
        default="⚪ NOT DEEP-SCORED",
    )

    score_col = first_existing(output, ["Swing score", "Score", "Technical score"])
    output["Score"] = (
        pd.to_numeric(output[score_col], errors="coerce").round(1)
        if score_col else np.nan
    )

    timing_col = first_existing(
        output,
        ["Entry mode", "Entry timing", "Entry timing verdict", "discovery_entry_timing"],
    )
    output["Entry timing"] = output[timing_col].fillna("—").astype(str) if timing_col else "—"

    rsi_col = first_existing(output, ["RSI", "RSI 4h", "rsi"])
    output["RSI"] = (
        pd.to_numeric(output[rsi_col], errors="coerce").round(1)
        if rsi_col else np.nan
    )

    distance_col = first_existing(
        output,
        ["Distance to resistance %", "Live distance to resistance %", "distance_to_resistance_pct"],
    )
    output["To resistance %"] = (
        pd.to_numeric(output[distance_col], errors="coerce").round(1)
        if distance_col else np.nan
    )

    rr_col = first_existing(output, ["R:R", "Reward/Risk", "Reward risk"])
    output["R:R"] = (
        pd.to_numeric(output[rr_col], errors="coerce").round(2)
        if rr_col else np.nan
    )

    execution_col = first_existing(
        output,
        ["Execution available", "Execution liquidity pass", "Execution gate"],
    )
    if execution_col:
        raw_execution = output[execution_col]
        output["Execution"] = raw_execution.apply(
            lambda value: "✅" if str(value).strip().lower() in {"true", "1", "yes", "pass"} else "❌"
        )
    else:
        output["Execution"] = "—"

    entry_col = first_existing(output, ["Active entry", "Planned entry", "Entry"])
    stop_col = first_existing(output, ["Invalidation", "Stop"])
    target_col = first_existing(output, ["First technical target", "Target"])
    rr_funded_col = first_existing(output, ["Current R:R", "R:R", "Reward/Risk", "Reward risk"])

    output["Funded entry"] = pd.to_numeric(output[entry_col], errors="coerce") if entry_col else np.nan
    output["Funded stop"] = pd.to_numeric(output[stop_col], errors="coerce") if stop_col else np.nan
    output["Funded target"] = pd.to_numeric(output[target_col], errors="coerce") if target_col else np.nan
    output["Funded R:R"] = (
        pd.to_numeric(output[rr_funded_col], errors="coerce").round(2)
        if rr_funded_col else output["R:R"]
    )

    funded_status = []
    positions = []
    risks = []
    for _, row in output.iterrows():
        core = str(row.get("CL Signal") or "")
        timing = str(row.get("Entry timing") or "").upper()
        execution = str(row.get("Execution") or "")
        rr = safe_number(row.get("Funded R:R"))
        entry = safe_number(row.get("Funded entry"))
        stop = safe_number(row.get("Funded stop"))

        if core == "🔴 PASS":
            status = "🔴 FUNDED PASS"
        elif core == "⚪ NOT DEEP-SCORED":
            status = "⚪ NOT DEEP-SCORED"
        elif core == "🟡 WATCH":
            status = "🟡 FUNDED WATCH"
        elif "EXTENDED" in timing or "TOO LATE" in timing:
            status = "🔴 FUNDED PASS · LATE"
        elif execution == "❌":
            status = "🔴 FUNDED PASS · EXECUTION"
        elif not np.isfinite(rr) or rr < FUNDED_MIN_RR:
            status = "🟡 FUNDED WATCH · R:R"
        elif not np.isfinite(entry) or not np.isfinite(stop) or entry <= stop:
            status = "🟡 FUNDED WATCH · RISK PLAN"
        else:
            status = "🟢 FUNDED READY"

        plan = funded_position_plan(entry, stop)
        funded_status.append(status)
        positions.append(round(plan[1], 2) if np.isfinite(plan[1]) else np.nan)
        risks.append(round(plan[2], 2) if np.isfinite(plan[2]) else np.nan)

    output["Funded status"] = funded_status
    output["Position $"] = positions
    output["Risk $"] = risks

    return output


render_module_header(
    "Kraken Funded",
    "💼",
    "Apply the existing CL Signal Trade rules only to assets available in your Kraken Funded universe.",
)

st.info(
    "This is a restricted universe, not a new strategy. The normal Stock Trade and Crypto Swing rulebooks still decide "
    "whether a setup is valid. The Funded overlay then asks whether that valid setup is suitable for a challenge with "
    "very little drawdown room."
)

m1, m2, m3, m4 = st.columns(4)
m1.metric("Starting balance", "$1,000")
m2.metric("Pass target", "$1,120", "+12%")
m3.metric("Failure level", "$970", "-3%")
m4.metric("Standard risk / trade", "$4", "0.4%")

with st.expander("Kraken Funded overlay rules", expanded=False):
    st.markdown(
        """
        **Core CL Signal rules stay unchanged.** The Funded layer is deliberately more selective:

        - Normal CL Signal **READY / BUY** is required before a Funded READY is possible.
        - Minimum Funded reward/risk: **2.5:1**. A normal 2.0–2.49R setup remains WATCH for the challenge.
        - Standard planned loss: **$4 per trade**; never intentionally exceed **$5**.
        - Maximum combined planned open risk: **$10** so most of the $30 failure buffer remains unused.
        - Position size is calculated from **entry → invalidation/stop**, capped at **35% of the $1,000 account**.
        - Crypto marked **EXTENDED / TOO LATE** is not eligible for Funded READY.
        - There is **no need to force trades** because the challenge has no time limit.
        """
    )

if "funded_stock_live_scan" not in st.session_state:
    st.session_state["funded_stock_live_scan"] = pd.DataFrame()
if "funded_crypto_live_scan" not in st.session_state:
    st.session_state["funded_crypto_live_scan"] = pd.DataFrame()
if "funded_scan_time" not in st.session_state:
    st.session_state["funded_scan_time"] = ""

scan_col, note_col = st.columns([1, 2.2], vertical_alignment="center")
with scan_col:
    run_funded_scan = st.button(
        "🔄 Scan Kraken Funded universe",
        type="primary",
        use_container_width=True,
        key="scan_kraken_funded_universe",
    )
with note_col:
    st.caption(
        "Runs all funded stocks and coins through the current CL Signal rule engines, "
        "then keeps the results loaded while you move between tabs."
    )

if run_funded_scan:
    with st.spinner("Scanning all Kraken Funded stocks…"):
        try:
            st.session_state["funded_stock_live_scan"] = scan_funded_stocks_live()
        except Exception as exc:
            st.session_state["funded_stock_live_scan"] = pd.DataFrame()
            st.error(f"Stock scan failed: {type(exc).__name__}")
    with st.spinner("Scanning all Kraken Funded crypto…"):
        try:
            st.session_state["funded_crypto_live_scan"] = scan_funded_crypto_live()
        except Exception as exc:
            st.session_state["funded_crypto_live_scan"] = pd.DataFrame()
            st.error(f"Crypto scan failed: {type(exc).__name__}")
    st.session_state["funded_scan_time"] = pd.Timestamp.now(tz="Europe/London").strftime("%d %b %Y %H:%M")
    st.success("Kraken Funded universe scan complete.")

if st.session_state.get("funded_scan_time"):
    st.caption(f"Last full funded scan: **{st.session_state['funded_scan_time']}**")

stock_tab, crypto_tab = st.tabs(["📈 Stocks", "⚡ Crypto"])

with stock_tab:
    live_stocks = st.session_state.get("funded_stock_live_scan", pd.DataFrame())
    stocks = live_stocks.copy() if isinstance(live_stocks, pd.DataFrame) and not live_stocks.empty else load_funded_stock_rows()

    ready_count = int(stocks["Funded status"].eq("🟢 FUNDED READY").sum())
    watch_count = int(stocks["Funded status"].astype(str).str.startswith("🟡 FUNDED WATCH").sum())
    no_setup_count = int(stocks["Funded status"].eq("⚪ NO CURRENT SETUP").sum())

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Funded stocks", len(KRAKEN_FUNDED_STOCKS))
    c2.metric("Ready", ready_count)
    c3.metric("Watch", watch_count)
    c4.metric("No current setup", no_setup_count)

    stock_filter = st.segmented_control(
        "Show",
        ["All", "Ready", "Watch", "No current setup"],
        default="All",
        key="funded_stock_filter",
    )
    shown = stocks.copy()
    if stock_filter == "Ready":
        shown = shown[shown["Funded status"] == "🟢 FUNDED READY"]
    elif stock_filter == "Watch":
        shown = shown[shown["Funded status"].astype(str).str.startswith("🟡 FUNDED WATCH")]
    elif stock_filter == "No current setup":
        shown = shown[shown["Funded status"] == "⚪ NO CURRENT SETUP"]

    if "MACD" not in stocks.columns and "MACD progress" in stocks.columns:
        stocks["MACD"] = stocks["MACD progress"].fillna("—").astype(str)
    if "Liquidity" not in stocks.columns and "Median traded value GBPm" in stocks.columns:
        stock_liq = pd.to_numeric(stocks["Median traded value GBPm"], errors="coerce")
        stocks["Liquidity"] = stock_liq.apply(
            lambda value: "—" if not np.isfinite(value) else f"{'✅' if value >= 0.5 else '❌'} £{value:.2f}m"
        )
    stock_cols = [
        "Funded status", "CL Signal", "Ticker", "Fundamentals", "Gate", "MACD", "RSI", "Liquidity",
        "Entry", "Stop", "Target", "R:R", "Position $", "Risk $",
    ]
    st.dataframe(
        shown[[column for column in stock_cols if column in shown.columns]],
        hide_index=True,
        use_container_width=True,
    )
    st.caption(
        "CL Signal shows the original Stock Trade verdict. Funded status applies the extra challenge filter. "
        "Position $ is sized from the stop so the planned loss is about $4, subject to the 35% position cap."
    )

with crypto_tab:
    live_crypto = st.session_state.get("funded_crypto_live_scan", pd.DataFrame())
    crypto = live_crypto.copy() if isinstance(live_crypto, pd.DataFrame) and not live_crypto.empty else load_funded_crypto_rows()

    ready_count = int(crypto["Funded status"].eq("🟢 FUNDED READY").sum())
    watch_count = int(crypto["Funded status"].astype(str).str.startswith("🟡 FUNDED WATCH").sum())
    pass_count = int(crypto["Funded status"].astype(str).str.startswith("🔴 FUNDED PASS").sum())

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Funded coins", len(KRAKEN_FUNDED_CRYPTO))
    c2.metric("Ready / Buy", ready_count)
    c3.metric("Watch", watch_count)
    c4.metric("Pass", pass_count)

    crypto_filter = st.segmented_control(
        "Show",
        ["All", "Ready / Buy", "Watch", "Pass"],
        default="All",
        key="funded_crypto_filter",
    )
    shown = crypto.copy()
    if crypto_filter == "Ready / Buy":
        shown = shown[shown["Funded status"] == "🟢 FUNDED READY"]
    elif crypto_filter == "Watch":
        shown = shown[shown["Funded status"].astype(str).str.startswith("🟡 FUNDED WATCH")]
    elif crypto_filter == "Pass":
        shown = shown[shown["Funded status"].astype(str).str.startswith("🔴 FUNDED PASS")]

    crypto_cols = [
        "Funded status", "CL Signal", "Coin", "Score", "Entry timing", "Swing reason", "Gate", "RSI",
        "To resistance %", "Funded entry", "Funded stop", "Funded target",
        "Funded R:R", "Execution", "Position $", "Risk $",
    ]
    st.dataframe(
        shown[[column for column in crypto_cols if column in shown.columns]],
        hide_index=True,
        use_container_width=True,
    )
    st.caption(
        "CL Signal remains the original Swing verdict. Funded status then applies the stricter challenge overlay: "
        "at least 2.5R, valid execution, no late/extended entry and position sizing from the invalidation."
    )
