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
FUNDED_REDUCED_RISK_USD = 3.0
FUNDED_MAX_RISK_USD = 5.0
FUNDED_MAX_TOTAL_OPEN_RISK_USD = 10.0
FUNDED_A_PLUS_MIN_RR = 2.5
FUNDED_READY_MIN_RR = 2.0
FUNDED_CRYPTO_READY_SCORE = 80.0
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


def _as_bool(value) -> bool:
    return bool(value) if isinstance(value, (bool, np.bool_)) else str(value).strip().lower() in {"true", "1", "yes", "pass"}


def _earnings_window(raw) -> str:
    date = pd.to_datetime(raw, errors="coerce")
    if pd.isna(date):
        return "DATE UNKNOWN"
    date = pd.Timestamp(date).date()
    today = pd.Timestamp.now(tz="Europe/London").date()
    if date < today:
        return "DATE EXPIRED"
    sessions = len(pd.bdate_range(today, date)) - 1
    return "WITHIN 5 SESSIONS" if sessions <= 5 else "OUTSIDE 5 SESSIONS (UNVERIFIED)"


def _account_guard(risk_usd: float) -> str:
    # User-entered manual information; NOT connected to the funded broker.
    if not st.session_state.get("funded_risk_verified", False):
        return "🟡 FUNDED WATCH · VERIFY ACCOUNT"
    equity = safe_number(st.session_state.get("funded_account_equity"))
    other_risk = safe_number(st.session_state.get("funded_other_open_risk"))
    if (not np.isfinite(equity) or not np.isfinite(other_risk) or other_risk < 0
            or equity <= FUNDED_FAIL_BALANCE + 10
            or equity - FUNDED_FAIL_BALANCE - 10 < risk_usd
            or other_risk + risk_usd > FUNDED_MAX_TOTAL_OPEN_RISK_USD):
        return "🔴 FUNDED PASS · ACCOUNT RISK LIMIT"
    return ""


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
                "reason": "; ".join(failures) if failures else ("QUALITY SCORE BELOW 65" if score < 65 else "PASS"),
                "earnings_date": str(snapshot.earnings_date) if snapshot.earnings_date is not None else "",
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


def funded_trend_watch(frame: pd.DataFrame, benchmark: pd.DataFrame | None = None) -> dict | None:
    """Early discovery only: rising higher-low bases near resistance; NEVER a buy."""
    data = frame.dropna(subset=["Open", "High", "Low", "Close", "Volume"]).sort_index().copy()
    if len(data) < 252:
        return None
    close = pd.to_numeric(data["Close"], errors="coerce")
    high = pd.to_numeric(data["High"], errors="coerce")
    low = pd.to_numeric(data["Low"], errors="coerce")
    volume = pd.to_numeric(data["Volume"], errors="coerce")
    ma180, ma200 = close.rolling(180).mean(), close.rolling(200).mean()
    price = float(close.iloc[-1])
    above_mas = price > max(float(ma180.iloc[-1]), float(ma200.iloc[-1]))
    rising_mas = (
        ma180.iloc[-1] > ma180.iloc[-21] * 1.005
        and ma200.iloc[-1] > ma200.iloc[-21] * 1.005
    )
    # Compare finished ten-session windows; avoid single candle pivot noise.
    higher_lows = float(low.iloc[-10:].min()) > float(low.iloc[-30:-10].min()) * 1.005
    prev_close = close.shift(1)
    true_range = pd.concat(
        [(high - low), (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    range_recent = float(true_range.iloc[-10:].mean())
    range_prior = float(true_range.iloc[-30:-10].mean())
    contraction = range_prior > 0 and range_recent <= range_prior * 0.9
    declining_volume = float(volume.iloc[-10:].median()) <= float(volume.iloc[-30:-10].median()) * 1.05
    resistance = float(high.iloc[-61:-1].max())
    to_resistance_pct = (resistance - price) / resistance * 100 if resistance > 0 else np.nan
    near_resistance = np.isfinite(to_resistance_pct) and 0 <= to_resistance_pct <= 8
    macd = close.ewm(span=12, adjust=False).mean() - close.ewm(span=26, adjust=False).mean()
    signal = macd.ewm(span=9, adjust=False).mean()
    histogram = macd - signal
    momentum_improving = bool(histogram.iloc[-1] > histogram.iloc[-2] > histogram.iloc[-3])
    relative_positive = False
    try:
        if benchmark is not None and "Close" in benchmark:
            reference = pd.to_numeric(benchmark["Close"], errors="coerce")
            pair = pd.concat([close.rename("stock"), reference.rename("benchmark")], axis=1).dropna()
            if len(pair) >= 21:
                relative_positive = bool(
                    pair["stock"].iloc[-1] / pair["stock"].iloc[-21]
                    > pair["benchmark"].iloc[-1] / pair["benchmark"].iloc[-21]
                )
    except (ValueError, TypeError, KeyError):
        pass
    score = (
        20 * above_mas + 15 * rising_mas + 20 * higher_lows
        + 10 * contraction + 5 * declining_volume + 10 * near_resistance
        + 15 * momentum_improving + 5 * relative_positive
    )
    if not (above_mas and rising_mas and near_resistance
            and (higher_lows or contraction) and score >= 65):
        return None
    return {
        "technical_state": "FUNDED TREND WATCH",
        "technical_reason": "HIGHER-LOW / COMPRESSION BASE; WAIT FOR BREAKOUT CONFIRMATION",
        "macd_progress": "IMPROVING" if momentum_improving else "DEVELOPING",
        "technical_score": float(score),
        "distance_to_resistance_pct": round(to_resistance_pct, 2),
        "pre_cross_blocker": "NO CONFIRMED ENTRY; EARLY DISCOVERY ONLY",
    }


def scan_funded_stocks_live() -> pd.DataFrame:
    """Funded trading: check technical setups before advisory company quality."""
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
        quality = ("— unavailable" if not np.isfinite(fscore)
                   else f"{'✅' if fundamental.get('pass') else '⚠️'} {fscore:.0f}")
        basic = {
            "Ticker": symbol, "Fundamentals": quality, "Fundamental score": fscore,
            "Fundamental note": fundamental.get("reason", "NOT PREPARED"),
            "Earnings check": _earnings_window(fundamental.get("earnings_date")),
            "Scan source": "LIVE",
            "scanned_at": pd.Timestamp.now(tz="UTC").isoformat(),
        }
        frame = _extract_yahoo_frame(prices, symbol)
        if frame is None or len(frame.dropna(subset=["Close", "Volume"])) < 252:
            rows.append({**basic, "Technical state": "DATA UNAVAILABLE",
                         "Gate": "INSUFFICIENT PRICE HISTORY",
                         "Funded status": "⚪ DATA UNAVAILABLE"})
            continue
        # Yahoo daily bars may include today's unfinished US session. Never
        # promote an incomplete daily crossover to an executable trade.
        ny_now = pd.Timestamp.now(tz="America/New_York")
        last_bar_date = pd.Timestamp(frame.index[-1]).date()
        if last_bar_date == ny_now.date() and (ny_now.hour < 16 or (ny_now.hour == 16 and ny_now.minute < 15)):
            frame = frame.iloc[:-1]
        technical = evaluate_price_setup(frame, benchmark)
        if technical.get("technical_state") == "BLOCKED":
            developing = funded_trend_watch(frame, benchmark)
            if developing:
                technical.update(developing)
        clean = frame.dropna(subset=["Close", "Volume"])
        turnover_usd = float((clean["Close"] * clean["Volume"]).tail(20).median())
        row = {
            **basic, "Technical state": technical.get("technical_state"),
            "CL Signal": technical.get("technical_state", "—"),
            "Gate": technical.get("technical_reason"),
            "MACD progress": technical.get("macd_progress"),
            "RSI": technical.get("rsi"), "Entry": technical.get("entry"),
            "Stop": technical.get("stop"), "Target": technical.get("target"),
            "R:R": technical.get("reward_risk"),
            "Median traded value GBPm": turnover_usd * 0.75 / 1_000_000,
            "Pre-cross blocker": technical.get("pre_cross_blocker"),
            "Technical score": technical.get("technical_score"),
            "To resistance %": technical.get("distance_to_resistance_pct"),
        }
        row["Funded status"] = funded_stock_status(pd.Series(row))
        risk_usd = (
            FUNDED_STANDARD_RISK_USD if row["Funded status"] == "🟢 A+ FUNDED READY"
            else FUNDED_REDUCED_RISK_USD if row["Funded status"] == "🟢 FUNDED READY"
            else np.nan
        )
        plan = (funded_position_plan(row.get("Entry"), row.get("Stop"), risk_usd)
                if np.isfinite(risk_usd) else (np.nan, np.nan, np.nan))
        row["Position $"] = round(plan[1], 2) if np.isfinite(plan[1]) else np.nan
        row["Risk $"] = round(plan[2], 2) if np.isfinite(plan[2]) else np.nan
        rows.append(row)
    return pd.DataFrame(rows)


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
                flat["Candle caution"] = bool(result.get("candle_caution", False))
                rows.append(flat)
            except Exception as exc:
                rows.append({
                    "Coin": base, "CL Signal": "⚪ DATA UNAVAILABLE", "Funded status": "⚪ SCAN ERROR",
                    "Gate": f"{type(exc).__name__}: {str(exc)[:120]}",
                })
    finally:
        await exchange.close()

    return pd.DataFrame(rows)


def funded_crypto_status(row: pd.Series) -> str:
    """No promotion without the originating engine's eligibility and execution gates."""
    core = str(row.get("CL Signal") or "")
    timing = str(row.get("Entry timing") or "").strip().upper()
    entry = safe_number(row.get("Funded entry"))
    stop = safe_number(row.get("Funded stop"))
    target = safe_number(row.get("Funded target"))
    source_rr = safe_number(row.get("Funded R:R"))
    score = safe_number(row.get("Score"))
    coin = str(row.get("Coin") or row.get("Base") or "").upper()
    if core == "🔴 PASS":
        return "🔴 FUNDED PASS"
    if core.startswith("⚪"):
        return "⚪ NOT DEEP-SCORED"
    if timing in {"TOO LATE", "EXTENDED"}:
        return "🔴 FUNDED PASS · LATE"
    if (not _as_bool(row.get("Execution available"))
            or not _as_bool(row.get("Execution liquidity pass"))):
        return "🔴 FUNDED PASS · EXECUTION"
    if timing == "RECLAIM NEEDED":
        return "🟡 FUNDED WATCH · RECLAIM"
    if timing == "EARLY":
        return "🟡 FUNDED WATCH · EARLY"
    if not all(np.isfinite(v) for v in (entry, stop, target)) or not stop < entry < target:
        return "🟡 FUNDED WATCH · RISK PLAN"
    real_rr = (target - entry) / (entry - stop)
    checked_rr = min(source_rr, real_rr) if np.isfinite(source_rr) else real_rr
    if checked_rr < FUNDED_READY_MIN_RR:
        return "🟡 FUNDED WATCH · R:R"
    if timing not in {"READY", "RETEST", "FRESH BREAKOUT"} or not _as_bool(row.get("Entry timing actionable")):
        return "🟡 FUNDED WATCH · TIMING"
    if not _as_bool(row.get("Swing eligible")):
        return "🟡 FUNDED WATCH · SETUP NOT ELIGIBLE"
    rs = safe_number(row.get("RS vs BTC %"))
    if coin != "BTC" and (not np.isfinite(rs) or rs <= 0):
        return "🟡 FUNDED WATCH · RELATIVE STRENGTH"
    if not np.isfinite(score) or score < FUNDED_CRYPTO_READY_SCORE:
        return "🟡 FUNDED WATCH · QUALITY SCORE"
    if core != "🟢 READY / BUY":
        # The original BUY engine also checks candle safety. A retest marked
        # WATCH must NOT bypass that veto just because its numeric score is high.
        return "🟡 FUNDED WATCH · CORE CONFIRMATION"
    at = pd.to_datetime(row.get("deep_scored_at"), errors="coerce", utc=True)
    age_hours = ((pd.Timestamp.now(tz="UTC") - at).total_seconds() / 3600
                 if not pd.isna(at) else np.inf)
    if age_hours < -0.1 or age_hours > 8:
        return "🟡 FUNDED WATCH · REFRESH SIGNAL"
    tier = "🟢 A+ FUNDED READY" if checked_rr >= FUNDED_A_PLUS_MIN_RR else "🟢 FUNDED READY"
    account_gate = _account_guard(
        FUNDED_STANDARD_RISK_USD if tier == "🟢 A+ FUNDED READY" else FUNDED_REDUCED_RISK_USD
    )
    return account_gate or tier


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
    raw["Execution"] = execution_series.apply(lambda v: "✅" if _as_bool(v) else "❌")

    funded_status, positions, risks = [], [], []
    for _, row in raw.iterrows():
        status = funded_crypto_status(row)
        entry = safe_number(row.get("Funded entry"))
        stop = safe_number(row.get("Funded stop"))
        risk_usd = (
            FUNDED_STANDARD_RISK_USD if status == "🟢 A+ FUNDED READY"
            else FUNDED_REDUCED_RISK_USD if status == "🟢 FUNDED READY"
            else np.nan
        )
        plan = (funded_position_plan(entry, stop, risk_usd)
                if np.isfinite(risk_usd) else (np.nan, np.nan, np.nan))
        if status.startswith("🔴 FUNDED PASS"):
            for field in ("Funded entry", "Funded stop", "Funded target", "Funded R:R"):
                raw.at[row.name, field] = np.nan
        funded_status.append(status)
        positions.append(round(plan[1], 2) if np.isfinite(plan[1]) else np.nan)
        risks.append(round(plan[2], 2) if np.isfinite(plan[2]) else np.nan)
    raw["Funded status"] = funded_status
    raw["Position $"] = positions
    raw["Risk $"] = risks
    return raw


def funded_position_plan(entry, stop, risk_usd=FUNDED_STANDARD_RISK_USD):
    entry, stop, risk_usd = safe_number(entry), safe_number(stop), safe_number(risk_usd)
    if (not all(np.isfinite(v) for v in (entry, stop, risk_usd))
            or entry <= stop or entry <= 0 or risk_usd <= 0 or _account_guard(risk_usd)):
        return np.nan, np.nan, np.nan
    equity = float(st.session_state["funded_account_equity"])
    risk_units = risk_usd / (entry - stop)
    max_notional = equity * FUNDED_MAX_POSITION_PCT / 100
    notional = min(risk_units * entry, max_notional)
    units = notional / entry
    return units, notional, units * (entry - stop)


def funded_stock_status(row: pd.Series) -> str:
    state = str(row.get("Technical state") or "").upper()
    rr = safe_number(row.get("R:R"))
    liquidity = safe_number(row.get("Median traded value GBPm"))
    entry, stop, target = (safe_number(row.get(key)) for key in ("Entry", "Stop", "Target"))
    macd = str(row.get("MACD progress") or "").upper()
    rsi = safe_number(row.get("RSI"))
    if state not in {"WATCH", "FUNDED TREND WATCH", "PRE-CROSS READY", "ENTRY READY", "AWAITING NEXT OPEN"}:
        return "⚪ NO CURRENT SETUP"
    if not np.isfinite(liquidity) or liquidity < 0.5:
        return "🔴 FUNDED PASS · LIQUIDITY"
    if state == "FUNDED TREND WATCH":
        return "🟡 FUNDED WATCH · COMPRESSION"
    if state == "WATCH":
        return "🟡 FUNDED WATCH · DEVELOPING"
    if state == "PRE-CROSS READY":
        return "🟡 FUNDED WATCH · PRE-CROSS"
    if state == "AWAITING NEXT OPEN":
        return "🟡 FUNDED WATCH · NEXT OPEN"
    if (not all(np.isfinite(v) for v in (entry, stop, target, rr))
            or entry <= stop or target <= entry):
        return "🟡 FUNDED WATCH · RISK PLAN"
    actual_rr = (target - entry) / (entry - stop)
    if min(rr, actual_rr) < FUNDED_READY_MIN_RR:
        return "🟡 FUNDED WATCH · R:R"
    if macd != "CONFIRMED":
        return "🟡 FUNDED WATCH · CONFIRMATION"
    earnings = str(row.get("Earnings check") or "")
    if earnings == "WITHIN 5 SESSIONS":
        return "🔴 FUNDED PASS · EARNINGS WINDOW"
    if str(row.get("Scan source") or "") != "LIVE":
        return "🟡 FUNDED WATCH · RUN LIVE SCAN"
    scanned_at = pd.to_datetime(row.get("scanned_at"), errors="coerce", utc=True)
    if pd.isna(scanned_at) or (pd.Timestamp.now(tz="UTC") - scanned_at).total_seconds() > 24 * 3600:
        return "🟡 FUNDED WATCH · REFRESH SIGNAL"
    if str(row.get("Ticker") or "") not in st.session_state.get("funded_verified_earnings", []):
        return "🟡 FUNDED WATCH · VERIFY EARNINGS"
    if np.isfinite(rsi) and rsi > 80:
        return "🔴 FUNDED PASS · EXTENDED RSI"
    tier = "🟢 A+ FUNDED READY" if min(rr, actual_rr) >= FUNDED_A_PLUS_MIN_RR else "🟢 FUNDED READY"
    account_gate = _account_guard(
        FUNDED_STANDARD_RISK_USD if tier == "🟢 A+ FUNDED READY" else FUNDED_REDUCED_RISK_USD
    )
    return account_gate or tier


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

    output["Scan source"] = "PREPARED"
    fundamentals = funded_fundamental_results()
    output["Earnings check"] = output["Ticker"].map(
        lambda ticker: _earnings_window(fundamentals.get(ticker, {}).get("earnings_date"))
    )
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
        lambda row: (
            funded_position_plan(
                row.get("Entry"),
                row.get("Stop"),
                risk_usd=(
                    FUNDED_STANDARD_RISK_USD
                    if row.get("Funded status") == "🟢 A+ FUNDED READY"
                    else FUNDED_REDUCED_RISK_USD
                ),
            )
            if row.get("Funded status") in {"🟢 A+ FUNDED READY", "🟢 FUNDED READY"}
            else (np.nan, np.nan, np.nan)
        ),
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
        ["Entry timing", "Entry timing verdict", "discovery_entry_timing", "Entry mode"],
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

    # Both venue execution AND minimum Kraken liquidity must pass.
    if "Execution available" in output and "Execution liquidity pass" in output:
        available = output["Execution available"].apply(_as_bool)
        liquid = output["Execution liquidity pass"].apply(_as_bool)
        output["Execution"] = np.where(available & liquid, "✅", "❌")
    else:
        output["Execution"] = "❌"

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

    funded_status, positions, risks = [], [], []
    for _, row in output.iterrows():
        status = funded_crypto_status(row)
        entry, stop = safe_number(row.get("Funded entry")), safe_number(row.get("Funded stop"))
        risk_usd = (
            FUNDED_STANDARD_RISK_USD if status == "🟢 A+ FUNDED READY"
            else FUNDED_REDUCED_RISK_USD if status == "🟢 FUNDED READY"
            else np.nan
        )
        plan = (funded_position_plan(entry, stop, risk_usd)
                if np.isfinite(risk_usd) else (np.nan, np.nan, np.nan))
        if status.startswith("🔴 FUNDED PASS"):
            for field in ("Funded entry", "Funded stop", "Funded target", "Funded R:R"):
                output.at[row.name, field] = np.nan
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

        - **A+ FUNDED READY:** at least **2.5R**, actionable timing and all funded gates passed. Planned loss: **$4**.
        - **FUNDED READY:** at least **2.0R** with stronger confirmation. Planned loss: **$3**.
        - For crypto, the original engine must confirm **BUY**, eligibility, timing, relative strength and execution; the score must be **80+**. WATCH never bypasses those gates.
        - For stocks, higher-low **COMPRESSION**, **PRE-CROSS READY** and **AWAITING NEXT OPEN** remain WATCH until there is a completed, executable entry. A live technical setup requires at least **2.0R**, liquidity and a manual earnings check.
        - **RECLAIM NEEDED / EARLY** remains WATCH until confirmation actually arrives.
        - Maximum combined planned open risk remains **$10** so most of the $30 failure buffer stays unused.
        - Position size is calculated from **entry → invalidation/stop**, capped at **35% of current entered equity**.
        - Crypto marked **EXTENDED / TOO LATE** or failing execution/liquidity is still an automatic PASS.
        - READY requires confirmation of manual equity/open risk; this screener is **not linked to broker positions**. Stops are not guaranteed in gaps or slippage.
        - There is **no need to force trades** because the challenge has no time limit.
        """
    )

today_str = pd.Timestamp.now(tz="Europe/London").strftime("%Y-%m-%d")
if st.session_state.get("funded_validation_day") != today_str:
    st.session_state["funded_validation_day"] = today_str
    st.session_state["funded_risk_verified"] = False
    st.session_state["funded_verified_earnings"] = []

def _clear_account_confirmation():
    st.session_state["funded_risk_verified"] = False

with st.expander("✅ Confirm funded account and stock earnings before READY", expanded=True):
    st.caption(
        "Manual safety check, NOT synced to Kraken Funded. Enter current account equity "
        "and total planned stop-loss risk on all other open trades. Reconfirm after any change."
    )
    x1, x2 = st.columns(2)
    with x1:
        st.number_input(
            "Current funded equity ($)", min_value=0.0, value=FUNDED_START_BALANCE,
            step=10.0, key="funded_account_equity", on_change=_clear_account_confirmation,
        )
    with x2:
        st.number_input(
            "Other open positions' combined stop-risk ($)",
            min_value=0.0, value=0.0, step=1.0,
            key="funded_other_open_risk", on_change=_clear_account_confirmation,
        )
    st.checkbox(
        "I have checked today's account equity and all open-position risk",
        key="funded_risk_verified",
    )
    remaining = min(
        FUNDED_MAX_TOTAL_OPEN_RISK_USD - float(st.session_state["funded_other_open_risk"]),
        float(st.session_state["funded_account_equity"]) - FUNDED_FAIL_BALANCE - 10,
    )
    st.caption(
        f"Remaining planned risk allowance: ${max(0.0, remaining):.2f}. "
        "This keeps a $10 buffer above the $970 failure level before slippage."
    )
    st.multiselect(
        "Stock tickers whose next earnings date I verified from an official company source",
        options=KRAKEN_FUNDED_STOCKS,
        key="funded_verified_earnings",
        help="Only select a ticker after verifying there is no earnings event within the next 5 trading sessions.",
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
    if (not st.session_state["funded_stock_live_scan"].empty
            and not st.session_state["funded_crypto_live_scan"].empty):
        st.success("Funded stock and crypto scans completed. Check unavailable rows before trading.")
    else:
        st.warning("At least one scan failed or returned no data; the page may show prepared, older results.")

if st.session_state.get("funded_scan_time"):
    st.caption(f"Last full funded scan: **{st.session_state['funded_scan_time']}**")

stock_tab, crypto_tab = st.tabs(["📈 Stocks", "⚡ Crypto"])

with stock_tab:
    live_stocks = st.session_state.get("funded_stock_live_scan", pd.DataFrame())
    stocks = live_stocks.copy() if isinstance(live_stocks, pd.DataFrame) and not live_stocks.empty else load_funded_stock_rows()
    # Re-evaluate manual risk and earnings inputs on EVERY rerun, without new network calls.
    if isinstance(live_stocks, pd.DataFrame) and not live_stocks.empty:
        stocks["Funded status"] = stocks.apply(funded_stock_status, axis=1)
        risk_plans = [
            funded_position_plan(
                row.get("Entry"), row.get("Stop"),
                FUNDED_STANDARD_RISK_USD if row["Funded status"] == "🟢 A+ FUNDED READY"
                else FUNDED_REDUCED_RISK_USD,
            ) if row["Funded status"] in {"🟢 A+ FUNDED READY", "🟢 FUNDED READY"}
            else (np.nan, np.nan, np.nan)
            for _, row in stocks.iterrows()
        ]
        stocks["Position $"] = [round(v[1], 2) if np.isfinite(v[1]) else np.nan for v in risk_plans]
        stocks["Risk $"] = [round(v[2], 2) if np.isfinite(v[2]) else np.nan for v in risk_plans]

    aplus_count = int(stocks["Funded status"].eq("🟢 A+ FUNDED READY").sum())
    ready_count = int(stocks["Funded status"].eq("🟢 FUNDED READY").sum())
    watch_count = int(stocks["Funded status"].astype(str).str.startswith("🟡 FUNDED WATCH").sum())
    no_setup_count = int(stocks["Funded status"].eq("⚪ NO CURRENT SETUP").sum())

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Funded stocks", len(KRAKEN_FUNDED_STOCKS))
    c2.metric("A+ Ready", aplus_count)
    c3.metric("Ready", ready_count)
    c4.metric("Watch", watch_count)
    c5.metric("No current setup", no_setup_count)

    stock_filter = st.segmented_control(
        "Show",
        ["All", "A+ Ready", "Ready", "Watch", "No current setup"],
        default="All",
        key="funded_stock_filter",
    )
    shown = stocks.copy()
    if stock_filter == "A+ Ready":
        shown = shown[shown["Funded status"] == "🟢 A+ FUNDED READY"]
    elif stock_filter == "Ready":
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
        "Funded status", "CL Signal", "Ticker", "Fundamentals", "Fundamental note",
        "Earnings check", "Gate", "Pre-cross blocker", "Technical score", "To resistance %", "MACD",
        "RSI", "Liquidity", "Entry", "Stop", "Target", "R:R", "Position $", "Risk $",
    ]
    st.dataframe(
        shown[[column for column in stock_cols if column in shown.columns]],
        hide_index=True,
        use_container_width=True,
    )
    with st.expander("Why stocks are being filtered out", expanded=False):
        stock_status = stocks["Funded status"].fillna("UNKNOWN").astype(str)
        breakdown = pd.DataFrame({
            "Reason": [
                "A+ ready",
                "Ready",
                "Watch / developing",
                "R:R below funded minimum",
                "Needs stronger confirmation",
                "Liquidity veto",
                "Risk-plan issue",
                "No current setup / unavailable",
            ],
            "Count": [
                int(stock_status.eq("🟢 A+ FUNDED READY").sum()),
                int(stock_status.eq("🟢 FUNDED READY").sum()),
                int(stock_status.eq("🟡 FUNDED WATCH").sum()),
                int(stock_status.str.contains("R:R", regex=False).sum()),
                int(stock_status.str.contains("NEEDS CONFIRMATION", regex=False).sum()),
                int(stock_status.str.contains("LIQUIDITY", regex=False).sum()),
                int(stock_status.str.contains("RISK PLAN", regex=False).sum()),
                int(stock_status.str.startswith("⚪").sum()),
            ],
        })
        st.dataframe(breakdown, hide_index=True, use_container_width=True)
    st.caption(
        "A+ READY uses about $4 planned risk at 2.5R+. READY uses about $3 risk at 2.0R+ with stronger confirmation. "
        "For stocks, a WATCH may upgrade only with confirmed MACD, constructive RSI, valid liquidity and risk plan. WATCH/PASS states never receive live sizing."
    )

with crypto_tab:
    live_crypto = st.session_state.get("funded_crypto_live_scan", pd.DataFrame())
    crypto = live_crypto.copy() if isinstance(live_crypto, pd.DataFrame) and not live_crypto.empty else load_funded_crypto_rows()
    if isinstance(live_crypto, pd.DataFrame) and not live_crypto.empty:
        crypto["Funded status"] = crypto.apply(funded_crypto_status, axis=1)
        risk_plans = [
            funded_position_plan(
                row.get("Funded entry"), row.get("Funded stop"),
                FUNDED_STANDARD_RISK_USD if row["Funded status"] == "🟢 A+ FUNDED READY"
                else FUNDED_REDUCED_RISK_USD,
            ) if row["Funded status"] in {"🟢 A+ FUNDED READY", "🟢 FUNDED READY"}
            else (np.nan, np.nan, np.nan)
            for _, row in crypto.iterrows()
        ]
        crypto["Position $"] = [round(v[1], 2) if np.isfinite(v[1]) else np.nan for v in risk_plans]
        crypto["Risk $"] = [round(v[2], 2) if np.isfinite(v[2]) else np.nan for v in risk_plans]

    aplus_count = int(crypto["Funded status"].eq("🟢 A+ FUNDED READY").sum())
    ready_count = int(crypto["Funded status"].eq("🟢 FUNDED READY").sum())
    watch_count = int(crypto["Funded status"].astype(str).str.startswith("🟡 FUNDED WATCH").sum())
    pass_count = int(crypto["Funded status"].astype(str).str.startswith("🔴 FUNDED PASS").sum())

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Funded coins", len(KRAKEN_FUNDED_CRYPTO))
    c2.metric("A+ Ready", aplus_count)
    c3.metric("Ready", ready_count)
    c4.metric("Watch", watch_count)
    c5.metric("Pass", pass_count)

    crypto_filter = st.segmented_control(
        "Show",
        ["All", "A+ Ready", "Ready", "Watch", "Pass"],
        default="All",
        key="funded_crypto_filter",
    )
    shown = crypto.copy()
    if crypto_filter == "A+ Ready":
        shown = shown[shown["Funded status"] == "🟢 A+ FUNDED READY"]
    elif crypto_filter == "Ready":
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
    with st.expander("Why crypto is being filtered out", expanded=False):
        crypto_status = crypto["Funded status"].fillna("UNKNOWN").astype(str)
        breakdown = pd.DataFrame({
            "Reason": [
                "A+ ready",
                "Ready",
                "Reclaim needed",
                "Too early",
                "R:R below 2.0",
                "Needs stronger confirmation",
                "Timing not actionable",
                "Execution/liquidity veto",
                "Late / extended",
                "Core pass / unavailable",
            ],
            "Count": [
                int(crypto_status.eq("🟢 A+ FUNDED READY").sum()),
                int(crypto_status.eq("🟢 FUNDED READY").sum()),
                int(crypto_status.str.contains("RECLAIM", regex=False).sum()),
                int(crypto_status.str.contains("EARLY", regex=False).sum()),
                int(crypto_status.str.contains("R:R", regex=False).sum()),
                int(crypto_status.str.contains("NEEDS CONFIRMATION", regex=False).sum()),
                int(crypto_status.str.contains("TIMING", regex=False).sum()),
                int(crypto_status.str.contains("EXECUTION", regex=False).sum()),
                int(crypto_status.str.contains("LATE", regex=False).sum()),
                int(crypto_status.eq("🔴 FUNDED PASS").sum() + crypto_status.str.startswith("⚪").sum()),
            ],
        })
        st.dataframe(breakdown, hide_index=True, use_container_width=True)
    st.caption(
        "A+ READY requires 2.5R+ and uses about $4 planned risk. READY requires at least 2.0R plus stronger confirmation and uses about $3 risk. "
        "RECLAIM, EARLY, late/extended and execution failures remain non-actionable."
    )
