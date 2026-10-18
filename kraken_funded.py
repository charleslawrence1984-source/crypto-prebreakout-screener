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
from funded_stock_breakout import evaluate_funded_breakout, validate_breakout_quote
from funded_crypto_checks import completed_funded_crypto_candles, crypto_gate_audit, crypto_distance_sensitivity
from funded_stock_paper import (
    empty_journal, capture_new_breakouts, import_journal_csv,
    update_paper_outcomes, summarize_paper_journal,
    explain_capture_eligibility,
)
from funded_stock_data import get_validated_stock_history
from funded_stock_events import earnings_window, lookup_estimated_earnings, earnings_trade_gate, validate_official_event_verification, fetch_fmp_earnings_calendar
from funded_stock_backtest import signal_candidates, simulate_portfolio, summarize_backtest_funnel, summarize_individual_rule_gates, breakout_plan_research, summarize_breakout_plan_research, simulate_shadow_breakout_stops, summarize_shadow_breakout_stops, summarize_shadow_robustness, audit_history_window_consistency, historical_signal_start
from trade_rules import FundamentalSnapshot, evaluate_price_setup, score_fundamental_snapshot, _wilder_rsi
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
FUNDED_STOCK_MAX_SPREAD_PCT = 0.60
FUNDED_STOCK_COST_BUFFER_PCT = 0.50

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


def funded_stock_spread_gate(buy, sell) -> tuple[bool, float, str]:
    buy, sell = safe_number(buy), safe_number(sell)
    if not all(np.isfinite(v) for v in (buy, sell)) or buy <= 0 or sell <= 0 or sell > buy:
        return False, np.nan, "MISSING OR INVALID BUY/SELL QUOTES"
    spread_pct = (buy - sell) / ((buy + sell) / 2) * 100
    if spread_pct > FUNDED_STOCK_MAX_SPREAD_PCT:
        return False, spread_pct, "SPREAD EXCEEDS 0.60%"
    return True, spread_pct, "PASS"


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


@st.cache_data(ttl=3600, show_spinner=False)
def funded_earnings_estimates() -> dict:
    """Estimated events only. User-entered official checks are mandatory.

    Prefer one keyed provider request when configured, then a throttled Yahoo
    fallback for missing tickers. Failure never becomes a positive safety check.
    """
    today = pd.Timestamp.now(tz="Europe/London").date()
    try:
        fmp_key = st.secrets.get("FMP_API_KEY", "")
    except Exception:
        fmp_key = ""
    collected, provider_note = fetch_fmp_earnings_calendar(
        fmp_key, KRAKEN_FUNDED_STOCKS, today
    )
    remaining = [s for s in KRAKEN_FUNDED_STOCKS if s not in collected]
    def fetch(symbol):
        try:
            result = lookup_estimated_earnings(yf.Ticker(symbol), today)
        except Exception as exc:
            result = {"estimated_date": "", "source": "Yahoo (UNAVAILABLE)",
                      "quality": f"FETCH ERROR: {type(exc).__name__}"}
        if not result.get("estimated_date"):
            result["quality"] = (
                result.get("quality", "NO UPCOMING DATE")
                + "; " + provider_note
            )
        return result
    # Yahoo can rate-limit simultaneous calls: minimize concurrency, cache
    # results for an hour, and never guess a date if both providers fail.
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = {pool.submit(fetch, symbol): symbol for symbol in remaining}
        for future in as_completed(futures):
            symbol = futures[future]
            collected[symbol] = future.result()
    return collected


def _account_guard(risk_usd: float) -> str:
    # User-entered manual information; NOT connected to the funded broker.
    if not st.session_state.get("funded_risk_verified", False):
        return "🟡 FUNDED WATCH · VERIFY ACCOUNT"
    equity = safe_number(st.session_state.get("funded_account_equity"))
    other_risk = safe_number(st.session_state.get("funded_other_open_risk"))
    if (not np.isfinite(equity) or not np.isfinite(other_risk) or other_risk < 0
            or not np.isfinite(risk_usd) or risk_usd <= 0
            or equity <= FUNDED_FAIL_BALANCE + 10
            or other_risk + risk_usd > FUNDED_MAX_TOTAL_OPEN_RISK_USD
            or other_risk + risk_usd > equity - FUNDED_FAIL_BALANCE - 10):
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
    event_estimates = funded_earnings_estimates()
    benchmark_raw = yf.download("^GSPC", period="3y", interval="1d", auto_adjust=False, progress=False)
    benchmark = _extract_yahoo_frame(benchmark_raw, "^GSPC")
    if benchmark is None:
        benchmark = benchmark_raw
    try:
        prices = yf.download(
            KRAKEN_FUNDED_STOCKS, period="3y", interval="1d", group_by="ticker",
            auto_adjust=False, threads=True, progress=False,
        )
    except Exception:
        # Every ticker will be checked through a separate source request below.
        prices = pd.DataFrame()
    scan_now = pd.Timestamp.now(tz="America/New_York")

    def retry_stock(symbol):
        return yf.download(
            symbol, period="3y", interval="1d", auto_adjust=False,
            threads=False, progress=False,
        )
    rows = []
    for symbol in KRAKEN_FUNDED_STOCKS:
        fundamental = fundamentals.get(symbol, {})
        fscore = safe_number(fundamental.get("score"))
        quality = ("— unavailable" if not np.isfinite(fscore)
                   else f"{'✅' if fundamental.get('pass') else '⚠️'} {fscore:.0f}")
        estimate = event_estimates.get(symbol, {})
        estimated_date = estimate.get("estimated_date") or ""
        basic = {
            "Ticker": symbol, "Fundamentals": quality, "Fundamental score": fscore,
            "Fundamental note": fundamental.get("reason", "NOT PREPARED"),
            "Next earnings estimate": estimated_date,
            "Earnings source": estimate.get("source", "ESTIMATE UNAVAILABLE"),
            "Earnings data quality": estimate.get("quality", "NO DATE RETURNED"),
            "Prepared earnings date (historic)": fundamental.get("earnings_date") or "",
            "Earnings check": earnings_window(
                estimated_date, pd.Timestamp.now(tz="Europe/London").date()
            ),
            "Scan source": "LIVE",
            "scanned_at": pd.Timestamp.now(tz="UTC").isoformat(),
        }
        frame, data_source, data_gate = get_validated_stock_history(
            prices, symbol, retry_stock, scan_now,
        )
        if frame is None:
            rows.append({
                **basic, "Technical state": "DATA UNAVAILABLE",
                "Gate": data_gate, "Price source": data_source,
                "Funded status": "⚪ DATA UNAVAILABLE",
                "CL Signal": "⚪ DATA UNAVAILABLE",
            })
            continue
        technical = evaluate_price_setup(frame, benchmark)
        # Same Wilder RSI as the core engine, available even for WATCH/BLOCKED.
        current_rsi = safe_number(_wilder_rsi(pd.to_numeric(frame["Close"], errors="coerce"), 14).iloc[-1])
        if technical.get("technical_state") == "BLOCKED":
            breakout = evaluate_funded_breakout(frame, benchmark)
            if breakout is not None:
                technical.update(breakout)
            else:
                developing = funded_trend_watch(frame, benchmark)
                if developing:
                    technical.update(developing)
        clean = frame.dropna(subset=["Close", "Volume"])
        turnover_usd = float((clean["Close"] * clean["Volume"]).tail(20).median())
        row = {
            **basic, "Technical state": technical.get("technical_state"),
            "Price source": data_source,
            "Last completed bar": str(pd.Timestamp(frame.index[-1]).date()),
            "CL Signal": technical.get("technical_state", "—"),
            "Gate": technical.get("technical_reason"),
            "MACD progress": technical.get("macd_progress"),
            "RSI": round(current_rsi, 1) if np.isfinite(current_rsi) else np.nan,
            "Entry": technical.get("entry"),
            "Stop": technical.get("stop"), "Target": technical.get("target"),
            "R:R": technical.get("reward_risk"),
            "Median traded value GBPm": turnover_usd * 0.75 / 1_000_000,
            "Pre-cross blocker": technical.get("pre_cross_blocker"),
            "Technical score": technical.get("technical_score"),
            "To resistance %": technical.get("distance_to_resistance_pct"),
            "Resistance": technical.get("resistance"),
            "Breakout volume ×": technical.get("volume_ratio"),
            "Breakout tests": technical.get("breakout_tests"),
            "Breakout date": technical.get("breakout_signal_date"),
            "Core signal date": technical.get("signal_date"),
            "Reference entry": technical.get("reference_entry"),
            "Reference stop": technical.get("reference_stop"),
            "Research retest stop": technical.get("research_stop_retest"),
            "Reference target": technical.get("reference_target"),
            "Reference R:R": technical.get("reference_reward_risk"),
            "Target basis": technical.get("reference_target_basis"),
        }
        row["Funded status"] = funded_stock_status(pd.Series(row))
        risk_usd = (
            FUNDED_STANDARD_RISK_USD if row["Funded status"] == "🟢 A+ FUNDED READY"
            else FUNDED_REDUCED_RISK_USD if row["Funded status"] == "🟢 FUNDED READY"
            else np.nan
        )
        plan = (funded_stock_position_plan(row.get("Entry"), row.get("Stop"), risk_usd, row.get("Verified spread %"))
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
        scan_clock = pd.Timestamp.now(tz="UTC")
        btc4 = completed_funded_crypto_candles(
            ohlcv_frame(await exchange.fetch_ohlcv("BTC/USD", timeframe="4h", limit=180)),
            "4h", scan_clock,
        )
        btcd = completed_funded_crypto_candles(
            ohlcv_frame(await exchange.fetch_ohlcv("BTC/USD", timeframe="1d", limit=365)),
            "1d", scan_clock,
        )
        if len(btc4) < 70 or len(btcd) < 35:
            raise ValueError("Insufficient completed BTC benchmark candles")

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
                coin_clock = pd.Timestamp.now(tz="UTC")
                df4 = completed_funded_crypto_candles(
                    ohlcv_frame(raw4), "4h", coin_clock,
                )
                dfd = completed_funded_crypto_candles(
                    ohlcv_frame(rawd), "1d", coin_clock,
                )
                week_raw = ohlcv_frame(raww)
                dfw = (
                    completed_funded_crypto_candles(week_raw, "1w", coin_clock)
                    if not week_raw.empty else week_raw
                )
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
                flat["Last completed 4h bar UTC"] = str(df4["timestamp"].iloc[-1])
                flat["Last completed daily bar UTC"] = str(dfd["timestamp"].iloc[-1])
                flat["4h bar age hours"] = round(
                    (coin_clock - (df4["timestamp"].iloc[-1] + pd.Timedelta(hours=4)))
                    .total_seconds() / 3600, 2,
                )
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


def _apply_verified_stock_quote(frame: pd.DataFrame) -> pd.DataFrame:
    """Only the verified quote can populate an actionable funded stock entry."""
    result = frame.copy()
    record = st.session_state.get("funded_stock_confirmed_quote") or {}
    symbol = str(record.get("ticker") or "")
    verified_at = pd.to_datetime(record.get("verified_at"), errors="coerce", utc=True)
    if (not symbol or symbol not in result["Ticker"].values or pd.isna(verified_at)
            or (pd.Timestamp.now(tz="UTC") - verified_at).total_seconds() > 600
            or (pd.Timestamp.now(tz="UTC") - verified_at).total_seconds() < -10):
        return result
    i = result.index[result["Ticker"] == symbol][0]
    row = result.loc[i]
    if str(row.get("Scan source")) != "LIVE":
        return result
    scanned_at = pd.to_datetime(row.get("scanned_at"), errors="coerce", utc=True)
    if pd.isna(scanned_at) or (pd.Timestamp.now(tz="UTC") - scanned_at).total_seconds() > 24 * 3600:
        return result
    quote = safe_number(record.get("price"))
    spread_ok, spread_pct, reason = funded_stock_spread_gate(quote, record.get("sell_price"))
    if not spread_ok:
        result.at[i, "Gate"] = "LIVE QUOTE REJECTED — " + reason
        return result

    state = str(row.get("Technical state") or "")
    if state == "FUNDED BREAKOUT PENDING OPEN":
        signal_date = pd.to_datetime(row.get("Breakout date"), errors="coerce")
        if pd.isna(signal_date) or not 0 <= (pd.Timestamp.now(tz="America/New_York").date()
                                             - signal_date.date()).days <= 4:
            return result
        plan = validate_breakout_quote(
            {"reference_entry": row.get("Reference entry"),
             "resistance": row.get("Resistance"),
             "reference_stop": row.get("Reference stop"),
             "reference_target": row.get("Reference target")},
            quote,
        )
        if not plan.get("ok"):
            result.at[i, "Gate"] = "LIVE QUOTE REJECTED — " + str(plan.get("reason"))
            return result
        result.at[i, "Technical state"] = "FUNDED BREAKOUT ENTRY CHECK"
        result.at[i, "MACD progress"] = "BREAKOUT CONFIRMED"
    elif state == "ENTRY READY":
        signal_date = pd.to_datetime(row.get("Core signal date"), errors="coerce")
        if pd.isna(signal_date) or not 0 <= (
            pd.Timestamp.now(tz="America/New_York").date() - signal_date.date()
        ).days <= 4:
            return result
        old_entry = safe_number(row.get("Entry"))
        stop, target = safe_number(row.get("Stop")), safe_number(row.get("Target"))
        if not all(np.isfinite(v) for v in (old_entry, stop, target)):
            return result
        if (quote > old_entry * 1.01 or quote < old_entry * 0.98
                or quote <= stop or quote >= target):
            result.at[i, "Gate"] = "MANUAL QUOTE TOO FAR FROM ORIGINAL ENTRY"
            return result
        plan = {"ok": True, "entry": quote, "stop": stop, "target": target}
        result.at[i, "Technical state"] = "FUNDED CORE ENTRY CHECK"
    else:
        return result
    result.at[i, "Entry"] = float(plan["entry"])
    result.at[i, "Stop"] = float(plan["stop"])
    result.at[i, "Target"] = float(plan["target"])
    expected_exit_spread = quote - safe_number(record.get("sell_price"))
    net_reward = float(plan["target"]) - quote - expected_exit_spread
    net_risk = quote - float(plan["stop"]) + expected_exit_spread
    result.at[i, "R:R"] = net_reward / net_risk if net_risk > 0 else np.nan
    result.at[i, "Spread-adjusted R:R"] = result.at[i, "R:R"]
    result.at[i, "Verified quote time"] = verified_at.isoformat()
    result.at[i, "Verified spread %"] = round(spread_pct, 3)
    return result


def funded_stock_position_plan(entry, stop, risk_usd=FUNDED_STANDARD_RISK_USD,
                               observed_spread_pct=np.nan):
    """Funded stocks only: model stop risk PLUS a cost/slippage reserve.

    This cannot enforce maximum loss when Kraken requires manual exits.
    """
    entry, stop, risk_usd = safe_number(entry), safe_number(stop), safe_number(risk_usd)
    observed = safe_number(observed_spread_pct)
    if (not all(np.isfinite(v) for v in (entry, stop, risk_usd))
            or entry <= stop or entry <= 0 or risk_usd <= 0 or _account_guard(risk_usd)):
        return np.nan, np.nan, np.nan
    reserve_pct = max(FUNDED_STOCK_COST_BUFFER_PCT,
                      observed if np.isfinite(observed) else 0.0)
    modeled_loss_fraction = (entry - stop) / entry + reserve_pct / 100
    if modeled_loss_fraction <= 0:
        return np.nan, np.nan, np.nan
    equity = safe_number(st.session_state.get("funded_account_equity"))
    notional = min(risk_usd / modeled_loss_fraction,
                   equity * FUNDED_MAX_POSITION_PCT / 100)
    return notional / entry, notional, notional * modeled_loss_fraction


def funded_stock_status(row: pd.Series) -> str:
    state = str(row.get("Technical state") or "").upper()
    rr = safe_number(row.get("R:R"))
    liquidity = safe_number(row.get("Median traded value GBPm"))
    entry, stop, target = (safe_number(row.get(key)) for key in ("Entry", "Stop", "Target"))
    macd = str(row.get("MACD progress") or "").upper()
    rsi = safe_number(row.get("RSI"))
    if state == "DATA UNAVAILABLE":
        return "⚪ DATA UNAVAILABLE"
    if state not in {
        "WATCH", "FUNDED TREND WATCH", "PRE-CROSS READY",
        "ENTRY READY", "AWAITING NEXT OPEN",
        "FUNDED BREAKOUT PENDING OPEN", "FUNDED BREAKOUT REVIEW",
        "FUNDED BREAKOUT ENTRY CHECK", "FUNDED CORE ENTRY CHECK",
    }:
        return "⚪ NO CURRENT SETUP"
    if not np.isfinite(liquidity) or liquidity < 0.5:
        return "🔴 FUNDED PASS · LIQUIDITY"
    if state == "FUNDED BREAKOUT REVIEW":
        return "🟡 FUNDED WATCH · BREAKOUT REVIEW"
    if state == "FUNDED BREAKOUT PENDING OPEN":
        return "🟡 FUNDED WATCH · VERIFY LIVE ENTRY"
    if state == "FUNDED TREND WATCH":
        return "🟡 FUNDED WATCH · COMPRESSION"
    if state == "WATCH":
        return "🟡 FUNDED WATCH · DEVELOPING"
    if state == "PRE-CROSS READY":
        return "🟡 FUNDED WATCH · PRE-CROSS"
    if state == "AWAITING NEXT OPEN":
        return "🟡 FUNDED WATCH · NEXT OPEN"
    if state == "ENTRY READY":
        # The original engine records the last session's historic opening
        # price. A historic opening is NOT a confirmed executable quote.
        return "🟡 FUNDED WATCH · RECONFIRM LIVE PRICE"
    if state in {"FUNDED BREAKOUT ENTRY CHECK", "FUNDED CORE ENTRY CHECK"}:
        verified_at = pd.to_datetime(row.get("Verified quote time"), errors="coerce", utc=True)
        spread = safe_number(row.get("Verified spread %"))
        if pd.isna(verified_at) or not np.isfinite(spread) or spread > FUNDED_STOCK_MAX_SPREAD_PCT:
            return "🟡 FUNDED WATCH · VERIFY SPREAD"
        if not 0 <= (pd.Timestamp.now(tz="UTC") - verified_at).total_seconds() <= 600:
            return "🟡 FUNDED WATCH · REFRESH QUOTE"
    if (not all(np.isfinite(v) for v in (entry, stop, target, rr))
            or entry <= stop or target <= entry):
        return "🟡 FUNDED WATCH · RISK PLAN"
    actual_rr = (target - entry) / (entry - stop)
    if min(rr, actual_rr) < FUNDED_READY_MIN_RR:
        return "🟡 FUNDED WATCH · R:R"
    if macd not in {"CONFIRMED", "BREAKOUT CONFIRMED"}:
        return "🟡 FUNDED WATCH · CONFIRMATION"
    technical_score = safe_number(row.get("Technical score"))
    if state == "FUNDED BREAKOUT ENTRY CHECK" and (
        not np.isfinite(technical_score) or technical_score < 75
    ):
        return "🟡 FUNDED WATCH · QUALITY SCORE"
    earnings_gate = earnings_trade_gate(
        row,
        st.session_state.get("funded_earnings_verifications", {}),
        pd.Timestamp.now(tz="Europe/London").date(),
        pd.Timestamp.now(tz="UTC"),
    )
    if earnings_gate:
        return earnings_gate
    if str(row.get("Scan source") or "") != "LIVE":
        return "🟡 FUNDED WATCH · RUN LIVE SCAN"
    scanned_at = pd.to_datetime(row.get("scanned_at"), errors="coerce", utc=True)
    if pd.isna(scanned_at) or (pd.Timestamp.now(tz="UTC") - scanned_at).total_seconds() > 24 * 3600:
        return "🟡 FUNDED WATCH · REFRESH SIGNAL"
    if np.isfinite(rsi) and rsi > 80:
        return "🔴 FUNDED PASS · EXTENDED RSI"
    tier = (
        "🟢 A+ FUNDED READY"
        if min(rr, actual_rr) >= FUNDED_A_PLUS_MIN_RR
           and np.isfinite(technical_score) and technical_score >= 75
        else "🟢 FUNDED READY"
    )
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
    output["Prepared earnings date (historic)"] = output["Ticker"].map(
        lambda ticker: fundamentals.get(ticker, {}).get("earnings_date") or ""
    )
    output["Next earnings estimate"] = ""
    output["Earnings source"] = "PREPARED DATA ONLY — RUN LIVE STOCK SCAN"
    output["Earnings data quality"] = "NOT REFRESHED"
    output["Earnings check"] = "DATE UNKNOWN"
    state = output.get("Technical state", pd.Series("", index=output.index)).fillna("").astype(str)
    output["CL Signal"] = np.select(
        [
            state.isin(["ENTRY READY", "AWAITING NEXT OPEN"]),
            state.eq("WATCH"),
        ],
        [
            "🟡 CORE SIGNAL · RECHECK LIVE",
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
            funded_stock_position_plan(
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

st.warning(
    "Kraken Funded in the standard Kraken app does NOT offer advanced order types. "
    "CL Signal's breakout entry, invalidation/stop and target are RESEARCH LEVELS, "
    "not automatically executable protective orders. A manual exit may be late "
    "or impossible during a sharp move; the fixed $970 loss floor can be hit "
    "even briefly. Check actual Funded-tab buy/sell prices, current account "
    "equity and whether you can monitor/exit manually. Do not interpret READY "
    "or a successful paper stop as guaranteed drawdown protection."
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
        - For stocks, higher-low **COMPRESSION**, **PRE-CROSS READY**, **AWAITING NEXT OPEN** and daily breakouts remain WATCH until a current Kraken Funded buy/sell quote is manually verified. Stocks trade 24/7 on Kraken Funded.
        - Funded breakout confirmation requires resistance tests, a constructive base, >=1.35x volume, rising 180/200-day averages, RSI 50–75, positive relative strength, and a credible projected 2R+ plan. The target is a **projection**, not an assured resistance exit.
        - **RECLAIM NEEDED / EARLY** remains WATCH until confirmation actually arrives.
        - Maximum combined planned open risk remains **$10** so most of the $30 failure buffer stays unused.
        - Position size is calculated from **entry → invalidation/stop**, capped at **35% of current entered equity**.
        - Crypto marked **EXTENDED / TOO LATE** or failing execution/liquidity is still an automatic PASS.
        - READY requires confirmation of equity/open risk and dated manual company earnings evidence. Yahoo earnings dates are only unverified estimates; dates within 5 weekday sessions cause an automatic veto.
        - This screener is **not linked to broker positions**. The consumer Kraken Funded app has **no advanced stop-loss orders**; paper invalidation is a manual risk reference, **not** an installed protective exit. Slippage and missed exits can breach the fixed $970 loss floor.
        - There is **no need to force trades** because the challenge has no time limit.
        """
    )

today_str = pd.Timestamp.now(tz="Europe/London").strftime("%Y-%m-%d")
if st.session_state.get("funded_validation_day") != today_str:
    st.session_state["funded_validation_day"] = today_str
    st.session_state["funded_risk_verified"] = False
    st.session_state["funded_earnings_verifications"] = {}

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
        float(st.session_state["funded_account_equity"]) - FUNDED_FAIL_BALANCE - 10
        - float(st.session_state["funded_other_open_risk"]),
    )
    st.caption(
        f"Remaining planned risk allowance: ${max(0.0, remaining):.2f}. "
        "This keeps a $10 buffer above the $970 failure level before slippage."
    )
    st.divider()
    st.markdown("**Manually confirm company earnings announcements**")
    st.caption(
        "Yahoo earnings dates are estimates, not official company confirmations. "
        "Check the actual investor-relations announcement and enter its URL and "
        "next confirmed earnings date. Your verification expires at midnight UK time. "
        "These source details are user-entered; they are not authenticated by the app."
    )
    ev_ticker = st.selectbox(
        "Stock to verify", options=KRAKEN_FUNDED_STOCKS,
        key="funded_earnings_verify_ticker",
    )
    ev_date = st.text_input(
        "Next company-confirmed earnings date (YYYY-MM-DD)",
        value="", placeholder="2026-11-05", key="funded_earnings_verify_date",
    )
    ev_url = st.text_input(
        "Company investor-relations announcement URL",
        value="", placeholder="https://investor.company.com/...",
        key="funded_earnings_verify_source",
    )
    ev_confirm = st.checkbox(
        "I checked this date against the company's own announcement today",
        key="funded_earnings_verify_attestation",
    )
    if st.button("Record earnings verification", key="funded_earnings_verify_button"):
        tentative = {
            "ticker": ev_ticker, "next_date": ev_date.strip(),
            "source_url": ev_url.strip(),
            "user_confirmed": bool(ev_confirm),
            "checked_at": pd.Timestamp.now(tz="UTC").isoformat(),
        }
        checked, problem = validate_official_event_verification(
            tentative, ev_ticker, pd.Timestamp.now(tz="Europe/London").date(),
            pd.Timestamp.now(tz="UTC"),
        )
        if checked is None or checked < pd.Timestamp.now(tz="Europe/London").date():
            st.warning(
                "Not accepted: " + (problem if checked is None else "Next event date is in the past")
            )
        else:
            st.session_state.setdefault("funded_earnings_verifications", {})[ev_ticker] = tentative
            st.success(
                f"User-confirmed {ev_ticker} earnings date recorded as {checked}; "
                "this is NOT an independent verification by the screener."
            )
    verified_now = st.session_state.get("funded_earnings_verifications", {})
    if verified_now:
        st.caption(
            "Manually confirmed today: " + ", ".join(sorted(verified_now))
        )

if "funded_stock_live_scan" not in st.session_state:
    st.session_state["funded_stock_live_scan"] = pd.DataFrame()
if "funded_crypto_live_scan" not in st.session_state:
    st.session_state["funded_crypto_live_scan"] = pd.DataFrame()
if "funded_scan_time" not in st.session_state:
    st.session_state["funded_scan_time"] = ""

scan_col, note_col = st.columns([1, 2.2], vertical_alignment="center")
with scan_col:
    run_stock_scan = st.button(
        "📈 Scan stocks only", type="primary",
        use_container_width=True, key="scan_kraken_funded_stocks_only",
    )
    run_funded_scan = st.button(
        "🔄 Scan all funded assets",
        use_container_width=True,
        key="scan_kraken_funded_universe",
    )
with note_col:
    st.caption(
        "Scan stocks only for a faster, focused review. Scan all retains the "
        "existing combined stocks and crypto workflow."
    )

if run_stock_scan or run_funded_scan:
    with st.spinner("Scanning all Kraken Funded stocks…"):
        try:
            st.session_state["funded_stock_live_scan"] = scan_funded_stocks_live()
        except Exception as exc:
            st.session_state["funded_stock_live_scan"] = pd.DataFrame()
            st.error(f"Stock scan failed: {type(exc).__name__}")
    if run_funded_scan:
        with st.spinner("Scanning all Kraken Funded crypto…"):
            try:
                st.session_state["funded_crypto_live_scan"] = scan_funded_crypto_live()
            except Exception as exc:
                st.session_state["funded_crypto_live_scan"] = pd.DataFrame()
                st.error(f"Crypto scan failed: {type(exc).__name__}")
    stamp = pd.Timestamp.now(tz="Europe/London").strftime("%d %b %Y %H:%M")
    st.session_state["funded_stock_scan_time"] = stamp
    if run_funded_scan:
        st.session_state["funded_scan_time"] = stamp
        if (not st.session_state["funded_stock_live_scan"].empty
                and not st.session_state["funded_crypto_live_scan"].empty):
            st.success("Full funded scan completed; inspect data gaps before trading.")
        else:
            st.warning("At least one scan failed; older prepared results may be displayed.")
    elif not st.session_state["funded_stock_live_scan"].empty:
        st.success("Stock scan complete; crypto was not rescanned.")
    else:
        st.warning("Stock scan did not return data; no new stock signals are verified.")

if st.session_state.get("funded_stock_scan_time"):
    st.caption(f"Last stock scan: **{st.session_state['funded_stock_scan_time']}**")
if st.session_state.get("funded_scan_time"):
    st.caption(f"Last full funded scan: **{st.session_state['funded_scan_time']}**")

with st.expander("📈 Stocks — verify a live entry before BUY", expanded=False):
    live_for_quote = st.session_state.get("funded_stock_live_scan", pd.DataFrame())
    pending = []
    if isinstance(live_for_quote, pd.DataFrame) and not live_for_quote.empty:
        for _, item in live_for_quote.iterrows():
            if str(item.get("Technical state")) in {"ENTRY READY", "FUNDED BREAKOUT PENDING OPEN"}:
                pending.append(str(item.get("Ticker")))
    st.caption(
        "Kraken Funded stocks can trade 24/7. Enter current BUY and SELL quotes "
        "from the Kraken Funded app. Spread must not exceed 0.60%. "
        "Quote confirmation expires after ten minutes; this is not an order."
    )
    chosen = st.selectbox(
        "Candidate", options=["— select candidate —"] + sorted(set(pending)),
        key="funded_stock_quote_ticker",
    )
    quote = st.number_input(
        "Current Kraken Funded BUY price ($)",
        min_value=0.0, step=0.10, format="%.4f", key="funded_stock_quote_input",
    )
    sell_quote = st.number_input(
        "Current Kraken Funded SELL price ($)",
        min_value=0.0, step=0.10, format="%.4f",
        key="funded_stock_sell_quote_input",
    )
    spread_ok, spread_pct, reason = funded_stock_spread_gate(quote, sell_quote)
    st.caption(
        f"Current quoted spread: {spread_pct:.3f}%"
        if np.isfinite(spread_pct) else "Current quoted spread: —"
    )
    confirmed = st.checkbox(
        "I checked this quote just now in the funded trading platform",
        key="funded_stock_quote_confirmed",
    )
    if st.button("Validate stock entry", key="funded_stock_quote_button"):
        if chosen == "— select candidate —" or not confirmed:
            st.warning("Select a candidate and confirm both prices are current.")
        elif not spread_ok:
            st.warning(reason)
        else:
            st.session_state["funded_stock_confirmed_quote"] = {
                "ticker": chosen, "price": float(quote),
                "sell_price": float(sell_quote),
                "verified_at": pd.Timestamp.now(tz="UTC").isoformat(),
            }
            st.success("Quote accepted for entry checks; no order has been placed.")
    record = st.session_state.get("funded_stock_confirmed_quote") or {}
    if record.get("ticker"):
        st.caption(
            f"Last checked: {record.get('ticker')} BUY ${float(record.get('price')):.4f}, "
            f"SELL ${float(record.get('sell_price', 0)):.4f}. "
            "Recheck both prices before trading."
        )

st.warning(
    "Kraken Funded supports 24/7 stock trading and no advanced order types. "
    "Stops are manual invalidation/exit levels, NOT guaranteed stop-loss orders. "
    "Sharp moves and wide spreads can exceed $3/$4 planned risk."
)

stock_tab, crypto_tab = st.tabs(["📈 Stocks", "⚡ Crypto"])

with stock_tab:
    live_stocks = st.session_state.get("funded_stock_live_scan", pd.DataFrame())
    stocks = live_stocks.copy() if isinstance(live_stocks, pd.DataFrame) and not live_stocks.empty else load_funded_stock_rows()
    # Re-evaluate manual risk, verified LIVE quotes and earnings every rerun.
    if isinstance(live_stocks, pd.DataFrame) and not live_stocks.empty:
        stocks = _apply_verified_stock_quote(stocks)
        stocks["Funded status"] = stocks.apply(funded_stock_status, axis=1)
        risk_plans = [
            funded_stock_position_plan(
                row.get("Entry"), row.get("Stop"),
                FUNDED_STANDARD_RISK_USD if row["Funded status"] == "🟢 A+ FUNDED READY"
                else FUNDED_REDUCED_RISK_USD,
                row.get("Verified spread %"),
            ) if row["Funded status"] in {"🟢 A+ FUNDED READY", "🟢 FUNDED READY"}
            else (np.nan, np.nan, np.nan)
            for _, row in stocks.iterrows()
        ]
        stocks["Position $"] = [round(v[1], 2) if np.isfinite(v[1]) else np.nan for v in risk_plans]
        stocks["Risk $"] = [round(v[2], 2) if np.isfinite(v[2]) else np.nan for v in risk_plans]

    # Distinguish provider data failure from 'no upcoming earnings' so that
    # a broken calendar is visible to the user, not silently omitted.
    if "Next earnings estimate" in stocks.columns:
        date_count = int(
            pd.to_datetime(stocks["Next earnings estimate"], errors="coerce").notna().sum()
        )
        if date_count == 0:
            st.warning(
                "Earnings-calendar provider returned ZERO upcoming dates for this stock scan. "
                "Date risk remains unknown. Company event checks are mandatory before "
                "any funded stock READY; optional FMP_API_KEY in Streamlit secrets "
                "enables an independent estimated-calendar provider."
            )
        else:
            st.caption(
                f"Upcoming estimated earnings dates: {date_count}/{len(stocks)}. "
                "Unverified estimates do not replace company announcements."
            )
    aplus_count = int(stocks["Funded status"].eq("🟢 A+ FUNDED READY").sum())
    ready_count = int(stocks["Funded status"].eq("🟢 FUNDED READY").sum())
    watch_count = int(stocks["Funded status"].astype(str).str.startswith("🟡 FUNDED WATCH").sum())
    no_setup_count = int(stocks["Funded status"].eq("⚪ NO CURRENT SETUP").sum())
    unavailable_count = int(stocks["Funded status"].eq("⚪ DATA UNAVAILABLE").sum())

    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("Funded stocks", len(KRAKEN_FUNDED_STOCKS))
    c2.metric("A+ Ready", aplus_count)
    c3.metric("Ready", ready_count)
    c4.metric("Watch", watch_count)
    c5.metric("No current setup", no_setup_count)
    c6.metric("Data unavailable", unavailable_count)

    stock_filter = st.segmented_control(
        "Show",
        ["All", "A+ Ready", "Ready", "Watch", "No current setup", "Data unavailable"],
        default="All",
        key="funded_stock_filter",
    )
    # Build display diagnostics before filtering/copying to avoid dropping
    # MACD and liquidity from the table and exported CSV.
    if "MACD" not in stocks.columns and "MACD progress" in stocks.columns:
        stocks["MACD"] = stocks["MACD progress"].fillna("—").astype(str)
    if "Liquidity" not in stocks.columns and "Median traded value GBPm" in stocks.columns:
        stock_liq = pd.to_numeric(stocks["Median traded value GBPm"], errors="coerce")
        stocks["Liquidity"] = stock_liq.apply(
            lambda value: "—" if not np.isfinite(value) else f"{'✅' if value >= 0.5 else '❌'} £{value:.2f}m"
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
    elif stock_filter == "Data unavailable":
        shown = shown[shown["Funded status"] == "⚪ DATA UNAVAILABLE"]

    # Actionable candidates first, followed by developing compression patterns.
    # Preserve all rows and avoid changing their calculated statuses.
    rank = {
        "🟢 A+ FUNDED READY": 0,
        "🟢 FUNDED READY": 1,
        "🟡 FUNDED WATCH · VERIFY LIVE ENTRY": 2,
        "🟡 FUNDED WATCH · RECONFIRM LIVE PRICE": 2,
        "🟡 FUNDED WATCH · NEXT OPEN": 3,
        "🟡 FUNDED WATCH · PRE-CROSS": 3,
        "🟡 FUNDED WATCH · COMPRESSION": 4,
        "🟡 FUNDED WATCH · DEVELOPING": 5,
        "🟡 FUNDED WATCH · BREAKOUT REVIEW": 6,
        "⚪ NO CURRENT SETUP": 8,
        "⚪ DATA UNAVAILABLE": 9,
    }
    shown["_rank"] = shown["Funded status"].map(rank).fillna(7)
    shown["_distance"] = pd.to_numeric(shown.get("To resistance %"), errors="coerce").abs()
    shown["_score"] = pd.to_numeric(shown.get("Technical score"), errors="coerce")
    shown = shown.sort_values(
        ["_rank", "_distance", "_score"],
        ascending=[True, True, False], na_position="last",
    )

    stock_cols = [
        "Funded status", "CL Signal", "Ticker", "Fundamentals", "Fundamental note",
        "Next earnings estimate", "Earnings source",
        "Earnings data quality", "Earnings check",
        "Prepared earnings date (historic)", "Price source", "Last completed bar",
        "Gate", "Pre-cross blocker", "Technical score",
        "To resistance %", "Resistance", "Breakout volume ×", "Breakout tests",
        "Breakout date", "Reference entry", "Reference stop", "Reference target",
        "Reference R:R", "Target basis", "Verified quote time", "Verified spread %",
        "Spread-adjusted R:R", "MACD",
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
                "No current setup",
                "Data unavailable",
            ],
            "Count": [
                int(stock_status.eq("🟢 A+ FUNDED READY").sum()),
                int(stock_status.eq("🟢 FUNDED READY").sum()),
                int(stock_status.str.startswith("🟡 FUNDED WATCH").sum()),
                int(stock_status.str.contains("R:R", regex=False).sum()),
                int(stock_status.str.contains("NEEDS CONFIRMATION", regex=False).sum()),
                int(stock_status.str.contains("LIQUIDITY", regex=False).sum()),
                int(stock_status.str.contains("RISK PLAN", regex=False).sum()),
                int(stock_status.eq("⚪ NO CURRENT SETUP").sum()),
                int(stock_status.eq("⚪ DATA UNAVAILABLE").sum()),
            ],
        })
        st.dataframe(breakdown, hide_index=True, use_container_width=True)
    st.caption(
        "Funded stock sizing reserves at least 0.50% of notional for spread/slippage, "
        "and uses verified Kraken buy/sell quotes for a conservative R:R. "
        "A+ = $4 modeled loss at 2.5R+; READY = $3 modeled loss at 2R+. "
        "These are not guaranteed stop losses: Kraken Funded exits are manual."
    )

    with st.expander("📝 Stocks only — prospective paper-trading journal", expanded=False):
        st.warning(
            "RESEARCH ONLY — not an order or live BUY signal. Record plans only "
            "from a fresh STOCKS live scan, before the NEXT regular US session "
            "opens. Paper entries use the following US session OPEN as a proxy, "
            "with stop-first daily-bar modelling, not actual Kraken 24/7 fills. "
            "Results exclude historical earnings blackouts and cannot verify "
            "whether a stop is executable."
        )
        st.caption(
            "Only breakout plans with a score of at least 75 and no failed "
            "technical checks beyond projected R:R or original stop width "
            "are eligible for this research journal. Each signal creates "
            "four immutable method/cost scenarios (original vs resistance "
            "retest, both at 0.5% and 1.0% modelled costs). No past backtest "
            "candidates can be added retrospectively."
        )
        if "funded_stock_paper_journal" not in st.session_state:
            st.session_state["funded_stock_paper_journal"] = empty_journal()
        paper_upload = st.file_uploader(
            "Import previously downloaded paper journal CSV",
            type=["csv"], key="funded_stock_paper_upload",
        )
        if st.button("Import paper journal (replace current in-memory copy)",
                     disabled=paper_upload is None,
                     key="funded_stock_paper_import"):
            try:
                st.session_state["funded_stock_paper_journal"] = import_journal_csv(
                    paper_upload.getvalue(), KRAKEN_FUNDED_STOCKS,
                )
                st.success("Journal loaded. Re-export after each new snapshot or update.")
            except ValueError as exc:
                st.error(f"Journal rejected: {exc}")
        paper_live_scan = st.session_state.get(
            "funded_stock_live_scan", pd.DataFrame(),
        )
        if st.button(
            "Record NEW signal-day breakout plans from fresh live stock scan",
            key="funded_stock_paper_capture",
        ):
            saved, added = capture_new_breakouts(
                paper_live_scan, st.session_state["funded_stock_paper_journal"],
                pd.Timestamp.now(tz="UTC"),
            )
            st.session_state["funded_stock_paper_journal"] = saved
            if added:
                st.success(
                    f"Recorded {added} new paper scenarios. Download your CSV "
                    "below before closing or refreshing this session."
                )
            else:
                st.info(
                    "No new qualifying fresh breakout snapshots. Run "
                    "'Scan stocks only' first; WATCH setups with other failed "
                    "gates cannot be backfilled or promoted into paper trades."
                )
        with st.expander("Why were no NEW stock paper signals recorded?"):
            capture_checks = explain_capture_eligibility(
                paper_live_scan,
                st.session_state["funded_stock_paper_journal"],
                pd.Timestamp.now(tz="UTC"),
            )
            summary_counts = (
                capture_checks["Paper journal assessment"]
                .value_counts().rename_axis("Explanation")
                .reset_index(name="Stocks")
            )
            st.dataframe(
                summary_counts, hide_index=True, use_container_width=True,
            )
            st.caption(
                "A zero capture count is normal when there is no recent "
                "qualifying breakout. Data unavailable, expired scans and "
                "other technical vetoes are displayed separately. This "
                "report does NOT override any funded trading restrictions."
            )
            st.dataframe(
                capture_checks, hide_index=True, use_container_width=True,
            )
        paper_book = st.session_state["funded_stock_paper_journal"]
        if not paper_book.empty:
            if st.button(
                "Update paper outcomes using subsequently completed US daily bars",
                key="funded_stock_paper_update",
            ):
                fetched, failures = {}, []
                with st.spinner("Checking completed US stock candles for journaled signals…"):
                    now_et = pd.Timestamp.now(tz="America/New_York")
                    for symbol in sorted(set(paper_book["Ticker"])):
                        try:
                            clean, provider, why = get_validated_stock_history(
                                None, symbol,
                                lambda ticker: yf.download(
                                    ticker, period="2y", interval="1d",
                                    auto_adjust=False, progress=False,
                                    threads=False,
                                ),
                                now_et,
                            )
                            if clean is not None:
                                fetched[symbol] = clean
                            else:
                                failures.append(f"{symbol}: {why}")
                        except Exception as exc:
                            failures.append(f"{symbol}: {type(exc).__name__}")
                    st.session_state["funded_stock_paper_journal"] = update_paper_outcomes(
                        paper_book, fetched, pd.Timestamp.now(tz="UTC"),
                    )
                if failures:
                    st.warning(
                        "No update for stocks without validated, fully completed "
                        "daily candles: " + "; ".join(failures)
                    )
                else:
                    st.success("Paper observations updated. Export the new CSV below.")
            paper_book = st.session_state["funded_stock_paper_journal"]
            st.dataframe(
                summarize_paper_journal(paper_book),
                hide_index=True, use_container_width=True,
            )
            with st.expander("Inspect frozen paper signals and outcomes"):
                st.dataframe(
                    paper_book, hide_index=True, use_container_width=True,
                )
            st.download_button(
                "Download CURRENT forward paper-trading journal CSV",
                data=paper_book.to_csv(index=False),
                file_name="funded_stock_forward_paper_journal.csv",
                mime="text/csv", key="funded_stock_paper_download",
            )
            st.warning(
                "IMPORTANT: This journal is held in browser-session memory only. "
                "Download the CSV after every change, and import your latest "
                "saved copy next visit; otherwise observations may be lost. "
                "CSV records are user-managed and not tamper-proof evidence."
            )
        else:
            st.caption(
                "No paper observations recorded. The journal starts empty "
                "on purpose; historical test winners are NOT imported."
            )

    with st.expander("📊 Stocks only — historical backtest (research)", expanded=False):
        st.warning(
            "RESEARCH ONLY — NOT a Kraken Funded trading-performance record. "
            "Historical Yahoo US-session daily candles are proxies for 24/7 Kraken quotes. "
            "Historical earnings blackouts, real spreads, fill availability and past changes "
            "to Kraken's permitted stock universe are NOT reconstructed. "
            "Results cannot demonstrate fully deployable funded challenge performance."
        )
        back_col1, back_col2 = st.columns(2)
        with back_col1:
            period = st.selectbox(
                "Download history", ["3y", "5y", "10y"], index=1,
                key="funded_stock_backtest_period",
            )
        with back_col2:
            holding = st.selectbox(
                "Maximum holding period (trading sessions)",
                [10, 20, 30], index=1, key="funded_stock_backtest_hold",
            )
        st.caption(
            "Signals are calculated using only completed bars through each signal date. "
            "Entry is estimated at the NEXT US session open, with 0.50% baseline "
            "or 1.00% stress-model roundtrip friction. Stop and target crossing "
            "on the same daily candle is treated as a stop. Open trades at the "
            "historical endpoint are censored. A 10-year download includes "
            "approximately one year of required indicator warm-up, so the usable "
            "test period is shorter than ten years."
        )
        if st.button(
            "Run stock historical backtest", key="funded_stock_backtest_run",
            use_container_width=True,
        ):
            with st.spinner("Downloading price history and evaluating historical stock signals…"):
                try:
                    now_ny = pd.Timestamp.now(tz="America/New_York")
                    market_raw = yf.download(
                        "^GSPC", period=period, interval="1d",
                        auto_adjust=False, progress=False,
                    )
                    benchmark_hist = _extract_yahoo_frame(market_raw, "^GSPC")
                    if benchmark_hist is None or len(benchmark_hist) < 253:
                        raise ValueError("S&P 500 benchmark history unavailable")
                    price_batch = yf.download(
                        KRAKEN_FUNDED_STOCKS, period=period, interval="1d",
                        group_by="ticker", auto_adjust=False,
                        threads=True, progress=False,
                    )
                    histories, data_problems = {}, []
                    for stock in KRAKEN_FUNDED_STOCKS:
                        frame, source, why = get_validated_stock_history(
                            price_batch, stock,
                            lambda ticker: yf.download(
                                ticker, period=period, interval="1d",
                                auto_adjust=False, progress=False, threads=False,
                            ),
                            now_ny,
                        )
                        if frame is None:
                            data_problems.append({
                                "Ticker": stock, "Problem": why,
                            })
                        else:
                            histories[stock] = frame
                    if not histories:
                        raise ValueError("No historical stocks met the OHLCV checks")
                    start_year = historical_signal_start(now_ny, period)
                    stage_trace = [] if period == "10y" else None
                    signals, diagnostics = signal_candidates(
                        histories, benchmark_hist, first_date=start_year,
                        audit_trace=stage_trace,
                    )
                    baseline = simulate_portfolio(
                        histories, signals, roundtrip_cost_pct=0.5,
                        max_hold_sessions=holding,
                    )
                    stressed = simulate_portfolio(
                        histories, signals, roundtrip_cost_pct=1.0,
                        max_hold_sessions=holding,
                    )
                    observations = breakout_plan_research(diagnostics)
                    shadow = simulate_shadow_breakout_stops(
                        histories, observations,
                        holding_sessions=holding, friction_pct=0.5,
                    )
                    shadow_stressed = simulate_shadow_breakout_stops(
                        histories, observations,
                        holding_sessions=holding, friction_pct=1.0,
                    )
                    robustness = summarize_shadow_robustness({
                        "0.50% modelled roundtrip friction": shadow,
                        "1.00% modelled roundtrip friction": shadow_stressed,
                    })
                    history_audit = None
                    if period == "10y":
                        five_year_start = (
                            now_ny.tz_localize(None).normalize()
                            - pd.DateOffset(years=5)
                        )
                        history_audit = audit_history_window_consistency(
                            histories, benchmark_hist, diagnostics,
                            last_five_years_start=five_year_start,
                            full_audit_trace=stage_trace,
                        )
                    st.session_state["funded_stock_backtest_result"] = {
                        "base": baseline, "stress": stressed,
                        "shadow_outcomes": shadow,
                        "shadow_stressed_outcomes": shadow_stressed,
                        "shadow_robustness": robustness,
                        "history_consistency_audit": history_audit,
                        "diagnostics": diagnostics,
                        "unavailable": pd.DataFrame(data_problems),
                        "signals": len(signals), "period": period,
                        "hold": holding,
                        "calculated_at": pd.Timestamp.now(tz="UTC").isoformat(),
                    }
                except Exception as exc:
                    st.session_state.pop("funded_stock_backtest_result", None)
                    st.error(f"Stock backtest failed: {type(exc).__name__}: {exc}")
        history_result = st.session_state.get("funded_stock_backtest_result")
        if history_result:
            st.caption(
                "Last simulation: " + history_result["calculated_at"]
                + " | Historical range: " + history_result["period"]
                + " | Signal count: " + str(history_result["signals"])
            )
            comparison = pd.DataFrame([
                {
                    "Scenario": label,
                    **summarize_backtest_funnel(
                        history_result["diagnostics"], history_result[name]
                    ),
                }
                for label, name in [
                    ("0.50% roundtrip friction", "base"),
                    ("1.00% stress friction", "stress"),
                ]
            ])
            st.dataframe(comparison, hide_index=True, use_container_width=True)
            st.download_button(
                "Download backtest scenario summary CSV",
                data=comparison.to_csv(index=False),
                file_name="funded_stock_backtest_summary.csv",
                mime="text/csv",
                key="funded_stock_backtest_summary_export",
            )
            base_case = history_result["base"]
            if not base_case["equity_curve"].empty:
                points = base_case["equity_curve"].copy()
                points["Date"] = pd.to_datetime(points["Date"])
                st.line_chart(points.set_index("Date")[["Equity $"]])
            for title, key, filename in [
                ("Completed trades (base case)", "trades", "funded_stock_backtest_trades.csv"),
                ("Signals rejected or skipped (base case)", "skipped", "funded_stock_backtest_skipped.csv"),
                ("Open / censored outcomes", "unclosed", "funded_stock_backtest_open.csv"),
            ]:
                data = base_case[key]
                st.markdown("**" + title + "**")
                if data.empty:
                    st.caption("No rows recorded")
                else:
                    st.dataframe(data, hide_index=True, use_container_width=True)
                    st.download_button(
                        "Export " + title.lower(),
                        data=data.to_csv(index=False), file_name=filename,
                        mime="text/csv", key="funded_export_" + key,
                    )
            gate_breakdown = summarize_individual_rule_gates(
                history_result["diagnostics"]
            )
            st.markdown("**Why historical setups fail — individual rules**")
            st.caption(
                "Exact historical gate-hit totals. A breakout can fail multiple "
                "conditions, so rows do NOT sum to the total rejected sessions. "
                "For the core RSI/MACD engine, only the FIRST blocker is shown. "
                "These are research diagnostics, not adjustments to entry rules."
            )
            if gate_breakdown.empty:
                st.caption("No historical full-rule rejection counts recorded.")
            else:
                st.dataframe(
                    gate_breakdown, hide_index=True, use_container_width=True,
                )
                st.download_button(
                    "Download individual rule rejection counts CSV",
                    data=gate_breakdown.to_csv(index=False),
                    file_name="funded_stock_backtest_rule_rejections.csv",
                    mime="text/csv", key="funded_stock_backtest_gate_export",
                )
            plan_details = breakout_plan_research(history_result["diagnostics"])
            st.markdown("**Why breakout plans fail — reward/risk versus structural stops**")
            st.caption(
                "This is a diagnostic study using completed signal-date candles, "
                "not an alternative BUY strategy. It distinguishes reward/risk "
                "as the ONLY veto from reward/risk failures that overlap with "
                "weak volume, untested resistance or other quality rules. "
                "No historical live Kraken quotes or earnings-event gates are modelled."
            )
            if plan_details.empty:
                st.caption("No breakout plans were evaluated.")
            else:
                plan_summary = summarize_breakout_plan_research(plan_details)
                st.dataframe(
                    plan_summary, hide_index=True, use_container_width=True,
                )
                st.download_button(
                    "Download breakout trade-plan summary CSV",
                    data=plan_summary.to_csv(index=False),
                    file_name="funded_stock_backtest_breakout_plan_summary.csv",
                    mime="text/csv",
                    key="funded_stock_backtest_plan_summary_export",
                )
                with st.expander("Inspect historical breakout plan observations"):
                    st.dataframe(
                        plan_details, hide_index=True, use_container_width=True,
                    )
                    st.download_button(
                        "Download all historical breakout plan observations CSV",
                        data=plan_details.to_csv(index=False),
                        file_name="funded_stock_backtest_breakout_plan_details.csv",
                        mime="text/csv",
                        key="funded_stock_backtest_plan_details_export",
                    )
            st.markdown("**Research only — observed outcomes for three fixed stop methods**")
            st.caption(
                "A separate historical experiment on plan-only breakout rejections. "
                "It compares our original conservative stop with two PREDECLARED "
                "signal-day levels: recent support minus 0.2 ATR, and resistance "
                "minus 0.7 ATR. Every option must still pass 2R after 0.50% "
                "modelled costs, the 0.4–8% stop limit and anti-chasing opening "
                "price checks. A rejected option counts as no trade. Stop-before-"
                "target is assumed for ambiguous intraday bars; incomplete "
                "forward price histories are censored. These are independent "
                "hypothetical entries, NOT a funded portfolio performance result."
            )
            historical_shadow = history_result.get("shadow_outcomes", pd.DataFrame())
            if historical_shadow.empty:
                st.caption(
                    "No qualifying research observations; run the historical "
                    "backtest using the latest version."
                )
            else:
                shadow_summary = summarize_shadow_breakout_stops(historical_shadow)
                st.dataframe(
                    shadow_summary, hide_index=True, use_container_width=True,
                )
                st.download_button(
                    "Download shadow stop outcome summary CSV",
                    data=shadow_summary.to_csv(index=False),
                    file_name="funded_stock_backtest_shadow_stops_summary.csv",
                    mime="text/csv", key="funded_shadow_stop_summary_export",
                )
                with st.expander("Inspect every hypothetical stop outcome"):
                    st.dataframe(
                        historical_shadow, hide_index=True, use_container_width=True,
                    )
                    st.download_button(
                        "Download shadow stop outcomes CSV",
                        data=historical_shadow.to_csv(index=False),
                        file_name="funded_stock_backtest_shadow_stop_outcomes.csv",
                        mime="text/csv", key="funded_shadow_stop_outcomes_export",
                    )
                st.warning(
                    "Even a positive shadow result is NOT evidence that these "
                    "stops are structurally sound. Historical earnings dates, "
                    "actual Kraken Funded 24/7 quotes, and real executable "
                    "stops are not reconstructed. Do not use shadow levels to trade."
                )
            st.markdown("**Retrospective robustness — market periods and trading costs**")
            st.caption(
                "Compare the same fixed stop approaches in earlier versus later "
                "historical years, under 0.50% and 1.00% modelled roundtrip friction. "
                "The 2025 onward cohort has already influenced our research "
                "decisions; none of these cohorts is a genuinely untouched holdout. "
                "Every row shows independent hypothetical setups, NOT a funded "
                "account-equity simulation. Stops, targets, earnings and Kraken "
                "24/7 execution remain subject to the stated backtest limitations."
            )
            robust = history_result.get("shadow_robustness", pd.DataFrame())
            if robust.empty:
                st.caption("No eligible robustness observations in this scan.")
            else:
                st.dataframe(
                    robust, hide_index=True, use_container_width=True,
                )
                st.download_button(
                    "Download shadow robustness by period and cost CSV",
                    data=robust.to_csv(index=False),
                    file_name="funded_stock_shadow_robustness.csv",
                    mime="text/csv",
                    key="funded_stock_shadow_robustness_export",
                )
                total_rows = robust.loc[
                    robust["Historical cohort"] == "All observed years"
                ]
                if total_rows.empty or (
                    total_rows["Completed trades"].max() < 30
                ):
                    st.warning(
                        "Fewer than 30 completed trades in these scenarios. "
                        "Historical results cannot establish a reliable edge, "
                        "even if the mean R happens to be positive. Increasing "
                        "history is descriptive research, not independent validation."
                    )
            history_audit = history_result.get("history_consistency_audit")
            if history_audit is not None:
                st.markdown("**10-year vs 5-year overlapping history — integrity audit**")
                st.warning(
                    "This is a DATA / SIGNAL REPRODUCIBILITY CHECK, not a new trading "
                    "strategy. It compares the last five years within the SAME 10y "
                    "download with their full-history calculation, allowing one "
                    "additional calendar year for indicator warm-up. "
                    "It does not check consistency against a separate 5y Yahoo download."
                )
                st.dataframe(
                    history_audit["summary"], hide_index=True,
                    use_container_width=True,
                )
                st.download_button(
                    "Download 10y versus 5y consistency summary CSV",
                    data=history_audit["summary"].to_csv(index=False),
                    file_name="funded_stock_history_consistency_summary.csv",
                    mime="text/csv",
                    key="funded_stock_history_consistency_summary",
                )
                st.markdown("**Historical bar coverage by stock**")
                st.dataframe(
                    history_audit["coverage"], hide_index=True,
                    use_container_width=True,
                )
                st.download_button(
                    "Download 10-year stock historical coverage CSV",
                    data=history_audit["coverage"].to_csv(index=False),
                    file_name="funded_stock_10y_history_coverage.csv",
                    mime="text/csv",
                    key="funded_stock_10y_history_coverage",
                )
                if not history_audit["disagreements"].empty:
                    st.error(
                        "Historical signals CHANGE when using the same daily candles "
                        "with a longer indicator warm-up. Treat the 10-year "
                        "result as UNVERIFIED until this discrepancy is reviewed."
                    )
                    st.dataframe(
                        history_audit["disagreements"], hide_index=True,
                        use_container_width=True,
                    )
                    st.download_button(
                        "Download mismatched historical signal dates CSV",
                        data=history_audit["disagreements"].to_csv(index=False),
                        file_name="funded_stock_history_signal_mismatches.csv",
                        mime="text/csv",
                        key="funded_stock_history_signal_mismatches",
                    )
                stage_mismatches = history_audit.get(
                    "stage_mismatches", pd.DataFrame()
                )
                if not stage_mismatches.empty:
                    st.markdown("**Which signal-detection stage disagrees?**")
                    st.caption(
                        "Identifies whether the same historical session generated "
                        "a different preliminary trigger or a different CORE "
                        "priority classification when supplied longer warm-up. "
                        "This is an audit, NOT a new trading signal."
                    )
                    st.dataframe(
                        stage_mismatches.head(100), hide_index=True,
                        use_container_width=True,
                    )
                    st.download_button(
                        "Download stock historical routing discrepancies CSV",
                        data=stage_mismatches.to_csv(index=False),
                        file_name="funded_stock_historical_routing_discrepancies.csv",
                        mime="text/csv",
                        key="funded_stock_historical_routing_discrepancies_export",
                    )
                if not history_audit["coverage"].empty:
                    observed_earliest = pd.to_datetime(
                        history_audit["coverage"]["Actual first daily bar"],
                        errors="coerce",
                    ).min()
                    if pd.notna(observed_earliest):
                        requested_start = (
                            pd.Timestamp.now(tz="America/New_York")
                            .tz_localize(None).normalize()
                            - pd.DateOffset(years=9)
                        )
                        if observed_earliest > requested_start:
                            st.error(
                                "No downloaded stock has a daily candle older "
                                "than nine years: investigate whether Yahoo "
                                "returned the requested ten-year history."
                            )
            st.markdown("**Historical signal funnel and data coverage**")
            st.caption(
                "Trigger sessions show the raw RSI/MACD or resistance-cross events. "
                "Full-rule rejections show how many did not pass all established "
                "technical conditions; qualified signals still need a valid next-open "
                "entry. The backtest does not relax our BUY criteria."
            )
            st.dataframe(
                history_result["diagnostics"], hide_index=True,
                use_container_width=True,
            )
            st.download_button(
                "Download signal funnel and coverage CSV",
                data=history_result["diagnostics"].to_csv(index=False),
                file_name="funded_stock_backtest_funnel.csv",
                mime="text/csv", key="funded_stock_backtest_funnel_export",
            )
            if not history_result["unavailable"].empty:
                st.warning("Some stocks were excluded because historical OHLCV was inadequate.")
                st.dataframe(
                    history_result["unavailable"], hide_index=True,
                    use_container_width=True,
                )
            if history_result["base"]["metrics"]["Completed trades"] == 0:
                reason_rows = base_case["skipped"]
                explanation = (
                    "; ".join(
                        f"{row['Ticker']}: {row['Reason']}"
                        for _, row in reason_rows.head(3).iterrows()
                    ) if not reason_rows.empty else "No candidates reached a next-open entry."
                )
                st.error(
                    "ZERO completed trades: no strategy profitability conclusion "
                    "is possible. First skipped or rejected entries: " + explanation
                )
            elif history_result["base"]["metrics"]["Completed trades"] < 30:
                st.warning(
                    "Fewer than 30 completed trades: this sample is too small to "
                    "reliably estimate a strategy win rate or expectancy."
                )
            st.info(
                "No future information is used to generate signals. However "
                "US daily OHLC cannot prove 24/7 Kraken fills or reconstruct "
                "historical earnings; treat results as a technical proxy only."
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
    with st.expander("Research audit — crypto signal funnel and freshness", expanded=False):
        st.caption(
            "These audit counts OVERLAP: one coin can fail distance, "
            "quality, timing and spot liquidity simultaneously. They are "
            "not reasons to bypass the original strategy rules. "
            "The 33 configured coins are a fixed research list, not a "
            "verified complete list of every region-specific asset shown "
            "in the Kraken Funded mobile app. Spot-exchange quote volume "
            "is only a liquidity proxy, not an actual Funded quote/spread."
        )
        st.dataframe(
            crypto_gate_audit(crypto), hide_index=True,
            use_container_width=True,
        )
        st.markdown("**Proximity sensitivity — hypothetical WATCH research only**")
        st.dataframe(
            crypto_distance_sensitivity(crypto),
            hide_index=True, use_container_width=True,
        )
        audit_columns = [
            "Coin", "CL Signal", "Funded status", "Score", "Entry timing",
            "Swing eligible", "Entry timing actionable", "Swing reason",
            "To resistance %", "RSI", "RS vs BTC %",
            "Execution liquidity pass", "Execution reason",
            "Last completed 4h bar UTC", "Last completed daily bar UTC",
            "4h bar age hours",
        ]
        st.dataframe(
            crypto[[col for col in audit_columns if col in crypto.columns]],
            hide_index=True, use_container_width=True,
        )
        st.download_button(
            "Download detailed Kraken Funded crypto rule audit CSV",
            data=crypto.to_csv(index=False),
            file_name="funded_crypto_rule_audit.csv",
            mime="text/csv",
            key="funded_crypto_rule_audit_export",
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
