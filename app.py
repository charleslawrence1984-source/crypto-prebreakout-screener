from __future__ import annotations

import asyncio
import io
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import ccxt.async_support as ccxt
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st


st.set_page_config(page_title="Pre-Breakout Crypto Screener", page_icon="⚡", layout="wide")

STABLE_BASES = {
    "USDT", "USDC", "DAI", "FDUSD", "TUSD", "USDE", "PYUSD", "EURC", "USD1",
    "BUSD", "USDP", "GUSD", "LUSD", "FRAX", "EUR", "GBP",
}
LEVERAGED_SUFFIXES = ("UP", "DOWN", "BULL", "BEAR", "2L", "2S", "3L", "3S", "5L", "5S")

EXCHANGES = {
    "Binance": "binance",
    "Bybit": "bybit",
    "OKX": "okx",
}


@dataclass
class ScreenerConfig:
    exchange_id: str = "okx"
    quote: str = "USDT"
    universe_size: int = 50
    min_quote_volume: float = 5_000_000
    resistance_lookback: int = 30
    near_resistance_min_pct: float = 0.15
    near_resistance_max_pct: float = 5.0
    score_threshold: int = 80
    too_late_pct: float = 2.0
    max_rsi: float = 69.0
    min_gross_profit_pct: float = 30.0
    concurrency: int = 5


def _safe_float(x, default=np.nan):
    try:
        v = float(x)
        return v if math.isfinite(v) else default
    except Exception:
        return default


@st.cache_data(ttl=3600, show_spinner=False)
def _fred_series(series_id: str) -> pd.Series:
    r = requests.get(
        "https://fred.stlouisfed.org/graph/fredgraph.csv",
        params={"id": series_id},
        headers={"User-Agent": "pre-breakout-screener/1.0"},
        timeout=15,
    )
    r.raise_for_status()
    df = pd.read_csv(io.StringIO(r.text))
    if df.empty or len(df.columns) < 2:
        return pd.Series(dtype=float)
    date_col = df.columns[0]
    value_col = df.columns[-1]
    idx = pd.to_datetime(df[date_col], errors="coerce", utc=True)
    values = pd.to_numeric(df[value_col], errors="coerce")
    out = pd.Series(values.to_numpy(), index=idx).dropna()
    return out[~out.index.isna()].sort_index()


@st.cache_data(ttl=3600, show_spinner=False)
def _stablecoin_supply_series() -> pd.Series:
    r = requests.get(
        "https://stablecoins.llama.fi/stablecoincharts/all",
        headers={"User-Agent": "pre-breakout-screener/1.0"},
        timeout=20,
    )
    r.raise_for_status()
    data = r.json() or []
    points = []
    for item in data:
        try:
            ts = pd.to_datetime(float(item.get("date")), unit="s", utc=True)
            circulating = item.get("totalCirculatingUSD") or item.get("totalCirculating") or {}
            value = _safe_float(circulating.get("peggedUSD"), np.nan)
            if math.isfinite(value):
                points.append((ts, value))
        except Exception:
            continue
    if not points:
        return pd.Series(dtype=float)
    idx, vals = zip(*points)
    return pd.Series(vals, index=pd.DatetimeIndex(idx)).sort_index()


def _series_change_days(series: pd.Series, days: int) -> float:
    if series is None or series.empty:
        return np.nan
    s = series.dropna().sort_index()
    if len(s) < 2:
        return np.nan
    latest_time = s.index[-1]
    target = latest_time - pd.Timedelta(days=days)
    prior = s.loc[:target]
    if prior.empty:
        return np.nan
    old = float(prior.iloc[-1])
    new = float(s.iloc[-1])
    if old == 0:
        return np.nan
    return (new / old - 1) * 100


def _series_delta_days(series: pd.Series, days: int) -> float:
    if series is None or series.empty:
        return np.nan
    s = series.dropna().sort_index()
    if len(s) < 2:
        return np.nan
    latest_time = s.index[-1]
    target = latest_time - pd.Timedelta(days=days)
    prior = s.loc[:target]
    if prior.empty:
        return np.nan
    return float(s.iloc[-1]) - float(prior.iloc[-1])


def _score_growth(change: float, bands: List[Tuple[float, float]], fallback: float = 0.0) -> float:
    if not math.isfinite(change):
        return np.nan
    for threshold, points in bands:
        if change >= threshold:
            return points
    return fallback


@st.cache_data(ttl=3600, show_spinner=False)
def macro_liquidity_regime() -> Dict:
    factors = []
    errors = []

    def add_factor(name, value, change_text, points, max_points, signal, source):
        if math.isfinite(points):
            factors.append({
                "Factor": name,
                "Latest": value,
                "Trend change": change_text,
                "Points": round(points, 1),
                "Max": max_points,
                "Signal": signal,
                "Source": source,
            })

    # Broad money: medium-term expansion/contraction.
    try:
        s = _fred_series("M2SL")
        ch = _series_change_days(s, 100)
        pts = _score_growth(ch, [(1.5, 20), (0.5, 16), (0.0, 12), (-0.5, 8)], 3)
        add_factor(
            "US M2", float(s.iloc[-1]), f"{ch:+.2f}% / ~3m", pts, 20,
            "Expanding" if ch > 0 else "Contracting",
            "FRED M2SL",
        )
    except Exception as exc:
        errors.append("US M2: " + str(exc))

    # Fed net-liquidity proxy = Fed assets - Treasury General Account - ON RRP.
    # WALCL and WTREGEN are millions of USD; RRPONTSYD is billions, so convert RRP.
    try:
        fed_assets = _fred_series("WALCL")
        tga = _fred_series("WTREGEN")
        rrp = _fred_series("RRPONTSYD") * 1000.0
        net_frame = pd.concat(
            [
                fed_assets.rename("fed"),
                tga.rename("tga"),
                rrp.rename("rrp"),
            ],
            axis=1,
        ).sort_index().ffill().dropna()
        net_liquidity = net_frame["fed"] - net_frame["tga"] - net_frame["rrp"]
        ch = _series_change_days(net_liquidity, 91)
        pts = _score_growth(ch, [(2.0, 15), (0.5, 12), (0.0, 9), (-2.0, 5)], 2)
        add_factor(
            "Fed net liquidity",
            float(net_liquidity.iloc[-1]),
            f"{ch:+.2f}% / 13w",
            pts,
            15,
            "Expanding" if ch > 0 else "Contracting",
            "FRED WALCL - WTREGEN - RRPONTSYD",
        )
    except Exception as exc:
        errors.append("Fed net liquidity: " + str(exc))

    # Financial conditions: negative NFCI is loose; falling NFCI is easing.
    try:
        s = _fred_series("NFCI")
        latest = float(s.iloc[-1])
        delta = _series_delta_days(s, 28)
        if latest <= -0.5 and (not math.isfinite(delta) or delta <= 0):
            pts = 20
        elif latest <= -0.25:
            pts = 17 if not math.isfinite(delta) or delta <= 0 else 14
        elif latest <= 0:
            pts = 13 if not math.isfinite(delta) or delta <= 0 else 10
        elif math.isfinite(delta) and delta < 0:
            pts = 7
        else:
            pts = 2
        add_factor(
            "Financial conditions (NFCI)", latest,
            f"{delta:+.3f} index pts / 4w" if math.isfinite(delta) else "Unavailable",
            pts, 20,
            "Loose / easing" if latest < 0 and (not math.isfinite(delta) or delta <= 0)
            else "Tightening / restrictive",
            "FRED NFCI",
        )
    except Exception as exc:
        errors.append("NFCI: " + str(exc))

    # Falling real yields generally improve liquidity/risk-asset conditions.
    try:
        s = _fred_series("DFII10")
        latest = float(s.iloc[-1])
        delta = _series_delta_days(s, 30)
        if math.isfinite(delta) and delta <= -0.25:
            pts = 15
        elif math.isfinite(delta) and delta < -0.05:
            pts = 12
        elif math.isfinite(delta) and delta <= 0.05:
            pts = 8
        elif math.isfinite(delta) and delta <= 0.25:
            pts = 4
        else:
            pts = 1
        add_factor(
            "10Y real yield", latest,
            f"{delta * 100:+.0f} bps / 30d" if math.isfinite(delta) else "Unavailable",
            pts, 15,
            "Falling / supportive" if math.isfinite(delta) and delta < 0 else "Rising / headwind",
            "FRED DFII10",
        )
    except Exception as exc:
        errors.append("10Y real yield: " + str(exc))

    # A weaker broad dollar is normally supportive for global risk liquidity.
    try:
        s = _fred_series("DTWEXBGS")
        latest = float(s.iloc[-1])
        ch = _series_change_days(s, 30)
        if ch <= -2:
            pts = 15
        elif ch <= -0.5:
            pts = 12
        elif ch <= 0.5:
            pts = 8
        elif ch <= 2:
            pts = 4
        else:
            pts = 1
        add_factor(
            "Broad US dollar", latest, f"{ch:+.2f}% / 30d", pts, 15,
            "Weakening / supportive" if ch < 0 else "Strengthening / headwind",
            "FRED DTWEXBGS",
        )
    except Exception as exc:
        errors.append("Broad dollar: " + str(exc))

    # Crypto-native liquidity: stablecoin supply growth.
    try:
        s = _stablecoin_supply_series()
        latest = float(s.iloc[-1])
        ch = _series_change_days(s, 30)
        pts = _score_growth(ch, [(5.0, 15), (2.0, 13), (0.5, 10), (0.0, 8), (-2.0, 4)], 1)
        add_factor(
            "Stablecoin supply", latest, f"{ch:+.2f}% / 30d", pts, 15,
            "Expanding" if ch > 0 else "Contracting",
            "DefiLlama stablecoins",
        )
    except Exception as exc:
        errors.append("Stablecoin supply: " + str(exc))

    available_max = sum(float(f["Max"]) for f in factors)
    raw_points = sum(float(f["Points"]) for f in factors)
    if available_max < 45:
        return {
            "available": False,
            "score": np.nan,
            "regime": "DATA LIMITED",
            "stance": "Do not macro-gate trades",
            "allows_new_swing_risk": True,
            "factors": factors,
            "errors": errors,
        }

    score = round(raw_points / available_max * 100, 1)
    if score >= 70:
        regime, stance = "EXPANSION", "Risk-on supportive"
    elif score >= 58:
        regime, stance = "IMPROVING", "Supportive / selective risk-on"
    elif score >= 42:
        regime, stance = "MIXED / NEUTRAL", "Technical setups can proceed selectively"
    elif score >= 30:
        regime, stance = "DETERIORATING", "New swing risk should wait"
    else:
        regime, stance = "CONTRACTION", "Defensive — avoid new swing risk"

    return {
        "available": True,
        "score": score,
        "regime": regime,
        "stance": stance,
        "allows_new_swing_risk": score >= 42,
        "factors": factors,
        "errors": errors,
    }


@st.cache_data(ttl=900, show_spinner=False)
def coingecko_tokenomics_snapshot() -> Dict[str, Dict]:
    """
    Bulk CoinGecko tokenomics snapshot keyed by ticker symbol.
    Two pages cover up to 500 large/mid-cap assets without one request per coin.
    When symbols collide, retain the higher market-cap-ranked asset.
    """
    rows = []
    for page in (1, 2):
        try:
            r = requests.get(
                "https://api.coingecko.com/api/v3/coins/markets",
                params={
                    "vs_currency": "usd",
                    "order": "market_cap_desc",
                    "per_page": 250,
                    "page": page,
                    "sparkline": "false",
                },
                headers={"User-Agent": "pre-breakout-screener/1.0"},
                timeout=20,
            )
            r.raise_for_status()
            page_rows = r.json() or []
            if isinstance(page_rows, list):
                rows.extend(page_rows)
        except Exception:
            continue

    by_symbol: Dict[str, Dict] = {}
    for item in rows:
        symbol = str(item.get("symbol") or "").upper().strip()
        if not symbol:
            continue
        existing = by_symbol.get(symbol)
        rank = item.get("market_cap_rank") or 10**9
        existing_rank = existing.get("market_cap_rank") or 10**9 if existing else 10**9
        if existing is None or rank < existing_rank:
            by_symbol[symbol] = item
    return by_symbol


def tokenomics_from_market(symbol: str, snapshot: Dict[str, Dict]) -> Dict:
    base = str(symbol).split("/")[0].upper()
    item = snapshot.get(base) or {}

    circulating = _safe_float(item.get("circulating_supply"), np.nan)
    total = _safe_float(item.get("total_supply"), np.nan)
    max_supply = _safe_float(item.get("max_supply"), np.nan)
    market_cap = _safe_float(item.get("market_cap"), np.nan)
    fdv = _safe_float(item.get("fully_diluted_valuation"), np.nan)

    denominator = total if math.isfinite(total) and total > 0 else max_supply
    supply_basis = "Total supply" if math.isfinite(total) and total > 0 else (
        "Max supply" if math.isfinite(max_supply) and max_supply > 0 else "Unavailable"
    )

    circulating_pct = (
        circulating / denominator * 100
        if math.isfinite(circulating)
        and math.isfinite(denominator)
        and denominator > 0
        else np.nan
    )
    fdv_mcap = (
        fdv / market_cap
        if math.isfinite(fdv) and fdv > 0
        and math.isfinite(market_cap) and market_cap > 0
        else np.nan
    )

    if math.isfinite(circulating_pct):
        gate = "PASS" if circulating_pct >= 25.0 else "FAIL"
    else:
        gate = "UNKNOWN"

    risks = []
    if math.isfinite(circulating_pct) and circulating_pct < 25.0:
        risks.append("Low float <25%")
    if math.isfinite(fdv_mcap) and fdv_mcap >= 4.0:
        risks.append("High FDV / low-float risk")
    if not item:
        risks.append("Tokenomics data unverified")

    return {
        "tokenomics_gate": gate,
        "circulating_supply": circulating,
        "total_supply": total,
        "max_supply": max_supply,
        "supply_basis": supply_basis,
        "circulating_pct": round(float(circulating_pct), 1) if math.isfinite(circulating_pct) else np.nan,
        "market_cap": market_cap,
        "fdv": fdv,
        "fdv_mcap": round(float(fdv_mcap), 2) if math.isfinite(fdv_mcap) else np.nan,
        "tokenomics_risks": "; ".join(risks),
        "coingecko_id": item.get("id") or "",
        "vc_unlock_review": "UNVERIFIED — specialist unlock/allocation data required",
    }


def ohlcv_to_df(rows: list) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
    if df.empty:
        return df
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.dropna().reset_index(drop=True)


def ema(s: pd.Series, span: int) -> pd.Series:
    return s.ewm(span=span, adjust=False).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    out = 100 - (100 / (1 + rs))
    return out.fillna(50)


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def obv(df: pd.DataFrame) -> pd.Series:
    direction = np.sign(df["close"].diff()).fillna(0)
    return (direction * df["volume"]).cumsum()


def lin_slope(values: pd.Series) -> float:
    y = pd.to_numeric(values, errors="coerce").dropna().to_numpy(dtype=float)
    if len(y) < 3:
        return 0.0
    x = np.arange(len(y), dtype=float)
    slope = np.polyfit(x, y, 1)[0]
    denom = np.nanmean(np.abs(y))
    return float(slope / denom) if denom else 0.0


def clamp_score(v: float, lo: float = 0, hi: float = 1) -> float:
    return float(max(lo, min(hi, v)))


def classify_trend(df4h: pd.DataFrame, dfd: pd.DataFrame) -> Dict:
    """
    Classify trend as UPTREND / SIDEWAYS / DOWNTREND.

    Daily structure sets the primary direction. The 4h structure is used as
    confirmation so a short-term bounce or pullback does not redefine the
    broader trend on its own.
    """
    if dfd is None or len(dfd) < 60 or df4h is None or len(df4h) < 50:
        return {
            "trend": "UNAVAILABLE",
            "daily": "UNAVAILABLE",
            "four_hour": "UNAVAILABLE",
            "detail": "Not enough history",
        }

    d = dfd.copy()
    h = df4h.copy()

    d["ema20"] = ema(d["close"], 20)
    d["ema50"] = ema(d["close"], 50)
    d["ema200"] = ema(d["close"], 200)
    h["ema20"] = ema(h["close"], 20)
    h["ema50"] = ema(h["close"], 50)

    d_price = float(d["close"].iloc[-1])
    d20 = float(d["ema20"].iloc[-1])
    d50 = float(d["ema50"].iloc[-1])
    d200 = float(d["ema200"].iloc[-1]) if len(d) >= 200 else np.nan
    d50_slope = lin_slope(d["ema50"].iloc[-12:])
    daily_spread_pct = abs(d20 - d50) / d_price * 100 if d_price else np.nan

    h_price = float(h["close"].iloc[-1])
    h20 = float(h["ema20"].iloc[-1])
    h50 = float(h["ema50"].iloc[-1])
    h50_slope = lin_slope(h["ema50"].iloc[-12:])
    fourh_spread_pct = abs(h20 - h50) / h_price * 100 if h_price else np.nan

    daily_up = d_price > d20 > d50 and d50_slope > 0
    daily_down = d_price < d20 < d50 and d50_slope < 0
    daily_flat = (
        (math.isfinite(daily_spread_pct) and daily_spread_pct <= 2.0)
        or abs(d50_slope) < 0.0006
    )

    fourh_up = h_price > h20 > h50 and h50_slope > 0
    fourh_down = h_price < h20 < h50 and h50_slope < 0
    fourh_flat = (
        (math.isfinite(fourh_spread_pct) and fourh_spread_pct <= 1.5)
        or abs(h50_slope) < 0.0008
    )

    daily_label = (
        "UPTREND" if daily_up
        else "DOWNTREND" if daily_down
        else "SIDEWAYS"
    )
    fourh_label = (
        "UPTREND" if fourh_up
        else "DOWNTREND" if fourh_down
        else "SIDEWAYS"
    )

    if daily_up and not fourh_down:
        trend = "UPTREND"
    elif daily_down and not fourh_up:
        trend = "DOWNTREND"
    elif daily_up and fourh_down:
        trend = "SIDEWAYS"
    elif daily_down and fourh_up:
        trend = "SIDEWAYS"
    elif daily_flat or fourh_flat:
        trend = "SIDEWAYS"
    else:
        trend = "SIDEWAYS"

    ema200_context = ""
    if math.isfinite(d200):
        ema200_context = (
            "above 200D EMA" if d_price >= d200 else "below 200D EMA"
        )

    detail = (
        f"Daily {daily_label}; 4h {fourh_label}; "
        f"daily EMA50 slope {d50_slope:+.4f}"
    )
    if ema200_context:
        detail += f"; {ema200_context}"

    return {
        "trend": trend,
        "daily": daily_label,
        "four_hour": fourh_label,
        "detail": detail,
    }


def scan_cell_style(value, column: str) -> str:
    """Traffic-light styling for the main scan's decision columns."""
    green = "background-color: #d8f3dc; color: #16351c; font-weight: 600"
    amber = "background-color: #fff3bf; color: #5f4500; font-weight: 600"
    red = "background-color: #ffd6d6; color: #5c1717; font-weight: 600"

    if column == "Accumulation signal":
        label = str(value)
        if label == "Strong potential base":
            return green
        if label == "Base developing":
            return amber
        return red

    if column in ("Coin trend", "Market trend"):
        label = str(value).upper()
        if label == "UPTREND":
            return green
        if label == "SIDEWAYS":
            return amber
        if label == "DOWNTREND":
            return red
        return ""

    try:
        number = float(value)
    except (TypeError, ValueError):
        return ""

    if not math.isfinite(number):
        return ""

    if column == "Tests":
        return green if number >= 2 else amber if number >= 1 else red
    if column == "RSI":
        return green if 52 <= number <= 64 else amber if 48 <= number <= 69 else red
    if column == "ATR ratio":
        return green if number <= 0.95 else amber if number <= 1.10 else red
    if column == "Vol ratio":
        return green if number <= 0.90 else amber if number <= 1.15 else red
    if column in ("RS vs BTC %", "RS vs BTC 96h %"):
        return green if number > 0 else amber if number >= -2 else red
    if column == "R:R":
        return green if number >= 2 else amber if number >= 1 else red
    return ""


def score_setup(
    df4h: pd.DataFrame,
    dfd: pd.DataFrame,
    btc4h: pd.DataFrame,
    cfg: ScreenerConfig,
    dfw: Optional[pd.DataFrame] = None,
    btcd: Optional[pd.DataFrame] = None,
) -> Dict:
    if len(df4h) < max(cfg.resistance_lookback + 25, 70) or len(dfd) < 35 or len(btc4h) < 30:
        return {"eligible": False, "reason": "Not enough history"}

    coin_trend_info = classify_trend(df4h, dfd)
    market_trend_info = (
        classify_trend(btc4h, btcd)
        if btcd is not None and not btcd.empty
        else {
            "trend": "UNAVAILABLE",
            "daily": "UNAVAILABLE",
            "four_hour": "UNAVAILABLE",
            "detail": "BTC daily history unavailable",
        }
    )

    x = df4h.copy()
    x["rsi"] = rsi(x["close"])
    x["ema20"] = ema(x["close"], 20)
    x["ema50"] = ema(x["close"], 50)
    x["atr"] = atr(x)
    x["obv"] = obv(x)
    macd = ema(x["close"], 12) - ema(x["close"], 26)
    signal = ema(macd, 9)
    x["macd_hist"] = macd - signal

    # Exclude the current candle from resistance discovery so a breakout candle cannot define its own resistance.
    hist = x.iloc[-cfg.resistance_lookback - 1 : -1]
    recent = x.iloc[-12:]
    previous = x.iloc[-24:-12]
    price = float(x["close"].iloc[-1])
    resistance = float(hist["high"].max())
    if not price or not resistance:
        return {"eligible": False, "reason": "Bad price data"}

    distance_pct = (resistance - price) / price * 100
    breakout_pct = (price - resistance) / resistance * 100

    # Keep scoring even when the strict pre-breakout shape fails. The broad scan
    # still excludes these coins, while Quick analyse can explain the full setup.
    shape_rejection = None
    if breakout_pct > cfg.too_late_pct:
        shape_rejection = "Too late / already broken out"
    elif distance_pct < -0.05:
        shape_rejection = "Already above resistance"
    elif distance_pct > cfg.near_resistance_max_pct:
        shape_rejection = "Too far below resistance"

    # 1) Price structure: higher lows + repeated resistance tests + EMA structure (20 pts)
    lows_slope = lin_slope(recent["low"])
    higher_lows_component = clamp_score((lows_slope + 0.001) / 0.004)
    test_band = resistance * 0.015
    resistance_tests = int(((hist["high"] >= resistance - test_band) & (hist["high"] <= resistance * 1.001)).sum())
    test_component = clamp_score((resistance_tests - 1) / 3)
    ema_component = 1.0 if price > x["ema20"].iloc[-1] > x["ema50"].iloc[-1] else (0.55 if price > x["ema20"].iloc[-1] else 0.15)
    structure_score = 8 * higher_lows_component + 6 * test_component + 6 * ema_component

    # 2) Compression: ATR and range tightening (15 pts)
    atr_now = float(x["atr"].iloc[-5:].mean())
    atr_base = float(x["atr"].iloc[-40:-10].median())
    atr_ratio = atr_now / atr_base if atr_base else 1.0
    atr_component = clamp_score((1.15 - atr_ratio) / 0.45)
    recent_range = (recent["high"].max() - recent["low"].min()) / price
    prev_price = float(previous["close"].iloc[-1]) if len(previous) else price
    prev_range = (previous["high"].max() - previous["low"].min()) / prev_price if len(previous) and prev_price else recent_range
    range_ratio = recent_range / prev_range if prev_range else 1.0
    range_component = clamp_score((1.15 - range_ratio) / 0.55)
    compression_score = 8 * atr_component + 7 * range_component

    # 3) Volume contraction during the coil (15 pts)
    vol5 = float(x["volume"].iloc[-5:].mean())
    vol20 = float(x["volume"].iloc[-20:].mean())
    vol_ratio = vol5 / vol20 if vol20 else 1.0
    vol_contract_component = clamp_score((1.15 - vol_ratio) / 0.5)
    red_mask = x["close"].diff().iloc[-12:] < 0
    green_mask = ~red_mask
    red_vol = float(x["volume"].iloc[-12:][red_mask].mean()) if red_mask.any() else vol20
    green_vol = float(x["volume"].iloc[-12:][green_mask].mean()) if green_mask.any() else vol20
    buy_pressure_component = clamp_score((green_vol / red_vol - 0.8) / 0.8) if red_vol else 0.5
    volume_score = 10 * vol_contract_component + 5 * buy_pressure_component

    # 4) Relative strength vs BTC, measured over 12 and 24 4h bars (15 pts)
    n = min(len(x), len(btc4h))
    coin = x["close"].iloc[-n:].reset_index(drop=True)
    btc = btc4h["close"].iloc[-n:].reset_index(drop=True)
    c12 = coin.iloc[-1] / coin.iloc[-13] - 1 if len(coin) >= 13 else 0
    b12 = btc.iloc[-1] / btc.iloc[-13] - 1 if len(btc) >= 13 else 0
    c24 = coin.iloc[-1] / coin.iloc[-25] - 1 if len(coin) >= 25 else 0
    b24 = btc.iloc[-1] / btc.iloc[-25] - 1 if len(btc) >= 25 else 0
    rs12 = c12 - b12
    rs24 = c24 - b24
    rs_component = clamp_score(((0.65 * rs12 + 0.35 * rs24) + 0.02) / 0.10)
    rs_score = 15 * rs_component

    # 5) Momentum: RSI in the 50-65 zone + improving MACD histogram (10 pts)
    rsi_now = float(x["rsi"].iloc[-1])
    if rsi_now > cfg.max_rsi:
        rsi_component = max(0, 1 - (rsi_now - cfg.max_rsi) / 10)
    elif 52 <= rsi_now <= 64:
        rsi_component = 1.0
    elif 48 <= rsi_now < 52:
        rsi_component = 0.65
    elif 64 < rsi_now <= cfg.max_rsi:
        rsi_component = 0.75
    else:
        rsi_component = 0.25
    mh = x["macd_hist"]
    macd_rising = float(mh.iloc[-1] - mh.iloc[-4])
    macd_scale = max(abs(float(mh.iloc[-10:].std())), price * 1e-5)
    macd_component = clamp_score(0.5 + macd_rising / (3 * macd_scale))
    momentum_score = 6 * rsi_component + 4 * macd_component

    # 6) Accumulation proxy: rising OBV (5 pts)
    obv_slope = lin_slope(x["obv"].iloc[-20:])
    obv_component = clamp_score((obv_slope + 0.005) / 0.02)
    obv_score = 5 * obv_component

    # 7) Daily context: positive but not extended (5 pts)
    d = dfd.copy()
    d["ema20"] = ema(d["close"], 20)
    d["rsi"] = rsi(d["close"])
    d["atr"] = atr(d)
    d["obv"] = obv(d)
    daily_price = float(d["close"].iloc[-1])
    daily_ema = float(d["ema20"].iloc[-1])
    daily_rsi = float(d["rsi"].iloc[-1])
    if daily_price >= daily_ema and daily_rsi <= 70:
        daily_component = 1.0
    elif daily_price >= daily_ema:
        daily_component = 0.6
    else:
        daily_component = 0.25
    daily_score = 5 * daily_component

    # Separate potential-base signal for disciplined accumulation entries.
    # This does not reward averaging into an unconfirmed downtrend.
    base_window = d.iloc[-60:]
    base_low = float(base_window["low"].min())
    base_distance_pct = (daily_price / base_low - 1) * 100 if base_low else 100.0
    base_location_component = clamp_score((25 - base_distance_pct) / 20)

    recent_daily_low = float(d["low"].iloc[-10:].min())
    prior_daily_low = float(d["low"].iloc[-30:-10].min())
    higher_base_component = clamp_score(
        0.5 + ((recent_daily_low / prior_daily_low - 1) / 0.08)
    ) if prior_daily_low else 0.0

    daily_ema_slope = lin_slope(d["ema20"].iloc[-10:])
    flattening_component = clamp_score((daily_ema_slope + 0.004) / 0.010)

    daily_rsi_change = daily_rsi - float(d["rsi"].iloc[-6])
    rsi_recovery_component = (
        clamp_score(0.55 + daily_rsi_change / 16)
        if 32 <= daily_rsi <= 62
        else 0.2
    )

    daily_obv_slope = lin_slope(d["obv"].iloc[-20:])
    daily_obv_component = clamp_score((daily_obv_slope + 0.006) / 0.024)

    bottom_components_raw = {
        "Base proximity": 30 * base_location_component,
        "Higher lows": 25 * higher_base_component,
        "Trend flattening": 20 * flattening_component,
        "RSI recovery": 15 * rsi_recovery_component,
        "Daily OBV": 10 * daily_obv_component,
    }
    bottom_score = round(float(sum(bottom_components_raw.values())), 1)
    bottom_components = {
        label: round(float(points), 1)
        for label, points in bottom_components_raw.items()
    }

    recent_support = float(d["low"].iloc[-20:].min())
    daily_atr = float(d["atr"].iloc[-5:].mean())
    accumulation_low = recent_support
    accumulation_high = min(
        recent_support + 1.5 * daily_atr,
        recent_support * 1.12,
    )
    in_accumulation_zone = accumulation_low <= daily_price <= accumulation_high

    if bottom_score >= 70:
        bottom_status = "Strong potential base"
    elif bottom_score >= 50:
        bottom_status = "Base developing"
    else:
        bottom_status = "Bottom not confirmed"

    # 8) Entry quality: near resistance but not touching it, with nearby invalidation (10 pts)
    ideal_mid = (cfg.near_resistance_min_pct + min(cfg.near_resistance_max_pct, 3.5)) / 2
    distance_component = clamp_score(1 - abs(distance_pct - ideal_mid) / max(ideal_mid, 1.0))
    # Use the nearest meaningful support across the 4h structure and daily trend
    # for entry timing, rather than anchoring solely to the current price.
    swing_low = float(x["low"].iloc[-24:].min())
    support_candidates = {
        "4h EMA20": float(x["ema20"].iloc[-1]),
        "4h EMA50": float(x["ema50"].iloc[-1]),
        "Daily EMA20": daily_ema,
        "20-day support cluster": float(d["low"].iloc[-20:].quantile(0.35)),
    }
    valid_supports = {
        label: level for label, level in support_candidates.items()
        if 0 < level <= price
    }
    if valid_supports:
        entry_basis, entry_anchor = max(valid_supports.items(), key=lambda item: item[1])
    else:
        entry_basis, entry_anchor = "Current price fallback", price
    entry_atr = float(x["atr"].iloc[-5:].mean())
    invalidation = min(swing_low, entry_anchor - 1.25 * entry_atr) * 0.995
    risk_pct = max((price - invalidation) / price * 100, 0.01)

    # Short-term measured move remains useful, while long-range weekly resistance
    # uses up to ~4 years of available history as a technical reference window.
    # This is not treated as evidence of a fixed four-year crypto cycle.
    pattern_base_low = float(hist["low"].min())
    pattern_height_pct = max((resistance - pattern_base_low) / resistance * 100, 0)
    measured_target = resistance * (1 + min(pattern_height_pct, 35) / 100)

    weekly_primary = np.nan
    weekly_stretch = np.nan
    weekly_target_levels: List[float] = []
    cycle_high = np.nan
    cycle_position_pct = np.nan
    cycle_accumulation_low = np.nan
    cycle_accumulation_high = np.nan
    in_cycle_accumulation_zone = False
    cycle_accumulation_basis = "Weekly history unavailable"
    target_basis = "4h measured move"

    if dfw is not None and len(dfw) >= 26:
        w = dfw.copy().tail(209)
        completed_w = w.iloc[:-1] if len(w) > 1 else w
        weekly_highs = completed_w["high"]
        weekly_lows = completed_w["low"]
        cycle_high = float(weekly_highs.max())
        cycle_low = float(weekly_lows.min())
        if cycle_high > cycle_low:
            cycle_position_pct = (price - cycle_low) / (cycle_high - cycle_low) * 100

        local_lows = weekly_lows[
            weekly_lows == weekly_lows.rolling(5, center=True, min_periods=3).min()
        ]
        weekly_supports = sorted(
            float(level)
            for level in local_lows.dropna()
            if 0 < float(level) <= price
        )
        if weekly_supports:
            weekly_support = weekly_supports[-1]
            weekly_atr = float(atr(completed_w).iloc[-5:].mean())
            cycle_accumulation_low = max(
                weekly_support - 0.25 * weekly_atr,
                weekly_support * 0.92,
            )
            cycle_accumulation_high = min(
                weekly_support + 0.25 * weekly_atr,
                weekly_support * 1.08,
            )
            in_cycle_accumulation_zone = (
                cycle_accumulation_low <= price <= cycle_accumulation_high
            )
            cycle_accumulation_basis = "Nearest confirmed weekly swing-low support"

        local_peaks = weekly_highs[
            weekly_highs == weekly_highs.rolling(5, center=True, min_periods=3).max()
        ]
        overhead = sorted(
            {
                float(level)
                for level in local_peaks.dropna()
                if float(level) >= max(price * 1.08, resistance * 1.03)
            }
        )
        if overhead:
            weekly_target_levels = [float(level) * 0.985 for level in overhead]
            weekly_primary = weekly_target_levels[0]
            for candidate in weekly_target_levels[1:]:
                if candidate >= weekly_primary * 1.08:
                    weekly_stretch = candidate
                    break
            target_basis = "Nearest major weekly resistance"

    first_take_profit = (
        float(weekly_primary)
        if math.isfinite(weekly_primary)
        else max(measured_target, resistance * 1.03)
    )
    first_take_profit_basis = (
        "Nearest major weekly resistance"
        if math.isfinite(weekly_primary)
        else "4h measured move"
    )

    entry_half_width = max(entry_atr * 0.40, price * 0.004)
    raw_entry_low = max(invalidation * 1.01, entry_anchor - entry_half_width)
    raw_entry_high = min(resistance * 0.998, entry_anchor + entry_half_width)
    entry_low = min(raw_entry_low, raw_entry_high * 0.999)
    entry_high = max(raw_entry_high, entry_low * 1.001)
    planned_entry = (entry_low + entry_high) / 2

    credible_targets = list(weekly_target_levels)
    if measured_target > price:
        credible_targets.append(float(measured_target))
    credible_targets = sorted(set(credible_targets))

    minimum_trade_target = planned_entry * (1 + cfg.min_gross_profit_pct / 100)
    qualifying_targets = [
        level for level in credible_targets
        if level >= minimum_trade_target
    ]
    projected_target = qualifying_targets[0] if qualifying_targets else np.nan
    stretch_target = qualifying_targets[1] if len(qualifying_targets) > 1 else np.nan

    if math.isfinite(projected_target):
        target_basis = (
            "Major weekly resistance meeting the 30% rule"
            if any(abs(projected_target - level) < max(level * 1e-8, 1e-12) for level in weekly_target_levels)
            else "4h measured move meeting the 30% rule"
        )
        target_upside_pct = (projected_target - planned_entry) / planned_entry * 100
    else:
        target_basis = "No credible target meets the 30% gross-profit rule"
        target_upside_pct = np.nan

    downside_to_invalidation_pct = max(
        (planned_entry - invalidation) / planned_entry * 100,
        0.01,
    )
    reward_pct = max(float(target_upside_pct), 0) if math.isfinite(target_upside_pct) else 0.0
    rr = reward_pct / downside_to_invalidation_pct
    rr_component = clamp_score((rr - 1.0) / 2.5)
    entry_score = 5 * distance_component + 5 * rr_component

    # The trade model's published component weights total 95 points. Normalize
    # the raw result so the displayed score genuinely uses a 0–100 scale.
    raw_total = (
        structure_score + compression_score + volume_score + rs_score
        + momentum_score + obv_score + daily_score + entry_score
    )
    total = round(float(max(0, min(100, raw_total / 95 * 100))), 1)

    shape_eligible = (
        cfg.near_resistance_min_pct <= distance_pct <= cfg.near_resistance_max_pct
        and resistance_tests >= 2
        and rsi_now <= cfg.max_rsi + 3
        and lows_slope > -0.0015
    )
    trade_target_eligible = math.isfinite(projected_target)
    eligible = shape_eligible and trade_target_eligible

    if eligible:
        result_reason = "Pre-breakout candidate with at least 30% gross target upside"
    elif not shape_eligible:
        result_reason = shape_rejection or "Shape filter not met"
    else:
        result_reason = "Pre-breakout shape found, but no credible 30% gross-profit target"

    # Accumulation is now driven by the actual daily base structure. Long-range
    # weekly support and the four-year range are reference context only and do
    # not trigger an accumulation decision.
    if bottom_score >= 70 and in_accumulation_zone:
        accumulation_verdict = "ACCUMULATION READY"
    elif bottom_score >= 50 or in_accumulation_zone:
        accumulation_verdict = "WATCH FOR BASE CONFIRMATION"
    else:
        accumulation_verdict = "NOT READY TO ACCUMULATE"

    previous_cycle_high_reference = (
        cycle_high * 0.985
        if math.isfinite(cycle_high) and cycle_high > planned_entry
        else np.nan
    )

    return {
        "eligible": bool(eligible),
        "shape_eligible": bool(shape_eligible),
        "trade_target_eligible": bool(trade_target_eligible),
        "reason": result_reason,
        "score": total,
        "price": price,
        "resistance": resistance,
        "distance_pct": round(distance_pct, 2),
        "resistance_tests": resistance_tests,
        "rsi": round(rsi_now, 1),
        "atr_ratio": round(atr_ratio, 2),
        "volume_ratio": round(vol_ratio, 2),
        "rs_vs_btc_pct": round(rs12 * 100, 2),
        "rs_vs_btc_96h_pct": round(rs24 * 100, 2),
        "risk_reward": round(rr, 2),
        "planned_entry": planned_entry,
        "downside_to_invalidation_pct": round(downside_to_invalidation_pct, 2),
        "entry_low": entry_low,
        "entry_high": entry_high,
        "invalidation": invalidation,
        "target_1": resistance * 1.05,
        "target_2": resistance * 1.10,
        "first_take_profit": first_take_profit,
        "first_take_profit_basis": first_take_profit_basis,
        "projected_target": projected_target,
        "stretch_target": stretch_target,
        "target_upside_pct": round(float(target_upside_pct), 2) if math.isfinite(target_upside_pct) else np.nan,
        "target_basis": target_basis,
        "minimum_gross_profit_pct": cfg.min_gross_profit_pct,
        "entry_basis": entry_basis,
        "coin_trend": coin_trend_info["trend"],
        "coin_trend_daily": coin_trend_info["daily"],
        "coin_trend_4h": coin_trend_info["four_hour"],
        "coin_trend_detail": coin_trend_info["detail"],
        "market_trend": market_trend_info["trend"],
        "market_trend_daily": market_trend_info["daily"],
        "market_trend_4h": market_trend_info["four_hour"],
        "market_trend_detail": market_trend_info["detail"],
        "cycle_position_pct": round(float(cycle_position_pct), 1) if math.isfinite(cycle_position_pct) else np.nan,
        "cycle_accumulation_low": cycle_accumulation_low,
        "cycle_accumulation_high": cycle_accumulation_high,
        "in_cycle_accumulation_zone": bool(in_cycle_accumulation_zone),
        "cycle_accumulation_basis": cycle_accumulation_basis,
        "accumulation_verdict": accumulation_verdict,
        "previous_cycle_high_reference": previous_cycle_high_reference,
        "bottom_score": bottom_score,
        "bottom_components": bottom_components,
        "bottom_status": bottom_status,
        "accumulation_low": accumulation_low,
        "accumulation_high": accumulation_high,
        "in_accumulation_zone": bool(in_accumulation_zone),
        "components": {
            "Structure": round(structure_score, 1),
            "Compression": round(compression_score, 1),
            "Volume": round(volume_score, 1),
            "RS vs BTC": round(rs_score, 1),
            "Momentum": round(momentum_score, 1),
            "OBV": round(obv_score, 1),
            "Daily context": round(daily_score, 1),
            "Entry / R:R": round(entry_score, 1),
        },
    }


async def fetch_market_universe(cfg: ScreenerConfig) -> Tuple[List[Tuple[str, float]], Dict[str, dict], dict]:
    cls = getattr(ccxt, cfg.exchange_id)
    exchange = cls({"enableRateLimit": True, "options": {"defaultType": "spot"}})
    try:
        markets = await exchange.load_markets()
        if not exchange.has.get("fetchTickers"):
            raise RuntimeError(f"{cfg.exchange_id} does not expose batch tickers through CCXT")
        tickers = await exchange.fetch_tickers()

        candidates = []
        for symbol, market in markets.items():
            if not market.get("spot") or market.get("active") is False:
                continue
            if market.get("quote") != cfg.quote:
                continue
            base = str(market.get("base", "")).upper()
            if base in STABLE_BASES or any(base.endswith(sfx) and len(base) > len(sfx) + 2 for sfx in LEVERAGED_SUFFIXES):
                continue
            t = tickers.get(symbol) or {}
            qv = _safe_float(t.get("quoteVolume"), np.nan)
            last = _safe_float(t.get("last"), np.nan)
            bv = _safe_float(t.get("baseVolume"), np.nan)
            if np.isnan(qv) and not np.isnan(last) and not np.isnan(bv):
                qv = last * bv
            if np.isnan(qv) or qv < cfg.min_quote_volume:
                continue
            candidates.append((symbol, qv))
        candidates.sort(key=lambda z: z[1], reverse=True)
        return candidates[: cfg.universe_size], markets, tickers
    finally:
        await exchange.close()


def coingecko_symbol_candidates(query: str) -> List[str]:
    """Resolve a typed coin name to likely ticker symbols."""
    try:
        response = requests.get(
            "https://api.coingecko.com/api/v3/search",
            params={"query": query},
            headers={"User-Agent": "pre-breakout-screener/1.0"},
            timeout=10,
        )
        response.raise_for_status()
        coins = response.json().get("coins", [])
    except Exception:
        return []

    query_lower = query.strip().lower()
    ranked = sorted(
        coins[:20],
        key=lambda coin: (
            0 if str(coin.get("name", "")).lower() == query_lower else
            1 if str(coin.get("symbol", "")).lower() == query_lower else
            2,
            coin.get("market_cap_rank") or 10**9,
        ),
    )
    symbols: List[str] = []
    for coin in ranked:
        symbol = str(coin.get("symbol", "")).upper().strip()
        if symbol and symbol not in symbols:
            symbols.append(symbol)
    return symbols


async def analyse_individual_coin(cfg: ScreenerConfig, query: str) -> Tuple[str, Dict, Dict[str, pd.DataFrame]]:
    cls = getattr(ccxt, cfg.exchange_id)
    exchange = cls({"enableRateLimit": True, "options": {"defaultType": "spot"}})
    try:
        markets = await exchange.load_markets()
        typed = query.strip().upper().replace("-", "/")
        if typed.endswith(f"/{cfg.quote}"):
            requested_base = typed.rsplit("/", 1)[0]
        else:
            requested_base = typed.split("/", 1)[0]

        eligible_markets = {
            symbol: market for symbol, market in markets.items()
            if market.get("spot")
            and market.get("active") is not False
            and market.get("quote") == cfg.quote
        }

        direct = [
            symbol for symbol, market in eligible_markets.items()
            if str(market.get("base", "")).upper() == requested_base
            or symbol.upper() == typed
            or str(market.get("id", "")).upper() == typed.replace("/", "")
        ]

        if direct:
            symbol = direct[0]
        else:
            bases = await asyncio.to_thread(coingecko_symbol_candidates, query)
            symbol = next(
                (
                    market_symbol
                    for base in bases
                    for market_symbol, market in eligible_markets.items()
                    if str(market.get("base", "")).upper() == base
                ),
                "",
            )

        if not symbol:
            raise ValueError(
                f"Could not find {query!r} as an active {cfg.quote} spot market on "
                f"{exchange.name}. Try its ticker, for example SOL."
            )

        coin4, coind, coinw, btc4, btcd = await asyncio.gather(
            exchange.fetch_ohlcv(symbol, timeframe="4h", limit=180),
            exchange.fetch_ohlcv(symbol, timeframe="1d", limit=365),
            exchange.fetch_ohlcv(symbol, timeframe="1w", limit=220),
            exchange.fetch_ohlcv(f"BTC/{cfg.quote}", timeframe="4h", limit=180),
            exchange.fetch_ohlcv(f"BTC/{cfg.quote}", timeframe="1d", limit=365),
        )
        df4 = ohlcv_to_df(coin4)
        dfd = ohlcv_to_df(coind)
        dfw = ohlcv_to_df(coinw)
        btcdf = ohlcv_to_df(btc4)
        btcd_df = ohlcv_to_df(btcd)
        result = score_setup(df4, dfd, btcdf, cfg, dfw=dfw, btcd=btcd_df)
        result["symbol"] = symbol
        tokenomics = tokenomics_from_market(symbol, coingecko_tokenomics_snapshot())
        result.update(tokenomics)
        return symbol, result, {"4h": df4, "1d": dfd, "1w": dfw}
    finally:
        await exchange.close()


async def scan_exchange(cfg: ScreenerConfig, progress=None) -> Tuple[pd.DataFrame, Dict[str, Dict[str, pd.DataFrame]], List[str]]:
    deadline = asyncio.get_running_loop().time() + 300
    universe, _, _ = await asyncio.wait_for(fetch_market_universe(cfg), timeout=45)
    tokenomics_snapshot = await asyncio.to_thread(coingecko_tokenomics_snapshot)
    cls = getattr(ccxt, cfg.exchange_id)
    exchange = cls({"enableRateLimit": True, "options": {"defaultType": "spot"}})
    errors: List[str] = []
    raw: Dict[str, Dict[str, pd.DataFrame]] = {}
    sem = asyncio.Semaphore(cfg.concurrency)

    try:
        await asyncio.wait_for(exchange.load_markets(), timeout=45)
        btc_symbol = f"BTC/{cfg.quote}"
        if btc_symbol not in exchange.markets:
            raise RuntimeError(f"{btc_symbol} is not available on {cfg.exchange_id}")
        btc4_rows, btcd_rows = await asyncio.gather(
            asyncio.wait_for(
                exchange.fetch_ohlcv(btc_symbol, timeframe="4h", limit=180),
                timeout=20,
            ),
            asyncio.wait_for(
                exchange.fetch_ohlcv(btc_symbol, timeframe="1d", limit=365),
                timeout=20,
            ),
        )
        btc4h = ohlcv_to_df(btc4_rows)
        btcd = ohlcv_to_df(btcd_rows)

        async def one(symbol: str, qv: float):
            async with sem:
                try:
                    # One candle request per worker; keep exchange throttling enabled.
                    rows4 = await asyncio.wait_for(
                        exchange.fetch_ohlcv(symbol, timeframe="4h", limit=180), timeout=20
                    )
                    rowsd = await asyncio.wait_for(
                        exchange.fetch_ohlcv(symbol, timeframe="1d", limit=365), timeout=20
                    )
                    df4 = ohlcv_to_df(rows4)
                    dfd = ohlcv_to_df(rowsd)
                    dfw = pd.DataFrame()
                    result = score_setup(df4, dfd, btc4h, cfg, btcd=btcd)
                    needs_weekly_context = (
                        result.get("shape_eligible")
                        or result.get("bottom_score", 0) >= 50
                        or result.get("in_accumulation_zone", False)
                    )
                    if needs_weekly_context:
                        try:
                            rowsw = await asyncio.wait_for(
                                exchange.fetch_ohlcv(symbol, timeframe="1w", limit=220), timeout=20
                            )
                            dfw = ohlcv_to_df(rowsw)
                            result = score_setup(df4, dfd, btc4h, cfg, dfw=dfw, btcd=btcd)
                        except Exception as weekly_error:
                            errors.append(f"{symbol} weekly context: {type(weekly_error).__name__}: {weekly_error}")
                    raw[symbol] = {"4h": df4, "1d": dfd, "1w": dfw}
                    if "score" not in result:
                        errors.append(f"{symbol}: {result.get('reason', 'Could not score market data')}")
                    result["symbol"] = symbol
                    result["quote_volume_24h"] = qv
                    result.update(tokenomics_from_market(symbol, tokenomics_snapshot))
                    return result
                except Exception as e:
                    errors.append(f"{symbol}: {type(e).__name__}: {e}")
                    return None

        results = []
        attempted = 0
        # Schedule only a small batch at a time. Keep completed results on timeout.
        for offset in range(0, len(universe), 10):
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                errors.append(f"Scan time limit reached: completed {attempted} of {len(universe)} markets.")
                break
            batch = universe[offset:offset + 10]
            tasks = [asyncio.create_task(one(symbol, qv)) for symbol, qv in batch]
            try:
                done, pending = await asyncio.wait(tasks, timeout=remaining)
                for task in tasks:
                    if task in done:
                        results.append(task.result())
                attempted += len(done)
            finally:
                # Drain cancelled requests before closing the exchange connection.
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
            if progress is not None:
                progress(attempted, len(universe))
            if pending:
                errors.append(f"Scan time limit reached: completed {attempted} of {len(universe)} markets. Showing partial results.")
                break
        # Keep all successfully scored coins visible for candidate review.
        rows = [r for r in results if r and "score" in r]
        if not rows:
            empty = pd.DataFrame()
            empty.attrs["markets_selected"] = len(universe)
            empty.attrs["markets_completed"] = attempted
            return empty, raw, errors

        def opportunity_rank(result: Dict) -> Tuple[int, float]:
            accumulation_ready = result.get("accumulation_verdict") == "ACCUMULATION READY"
            if result.get("eligible") and accumulation_ready:
                category = 4
            elif result.get("eligible"):
                category = 3
            elif accumulation_ready:
                category = 2
            else:
                category = 1
            relevant_score = max(
                result.get("score", 0) if result.get("eligible") else 0,
                result.get("bottom_score", 0),
            )
            return category, relevant_score

        rows.sort(key=opportunity_rank, reverse=True)
        display = pd.DataFrame([
            {
                "Coin": r["symbol"].split("/")[0],
                "Symbol": r["symbol"],
                "Opportunity": (
                    "BUY" if r["eligible"] and r["score"] >= cfg.score_threshold
                    else "ACCUMULATE" if r["accumulation_verdict"] == "ACCUMULATION READY"
                    else "WAIT"
                ),
                "Score": r["score"],
                "Trade reason": r["reason"],
                "Coin trend": r.get("coin_trend", "UNAVAILABLE"),
                "Market trend": r.get("market_trend", "UNAVAILABLE"),
                "Coin trend detail": r.get("coin_trend_detail", ""),
                "Market trend detail": r.get("market_trend_detail", ""),
                "Tokenomics gate": r.get("tokenomics_gate", "UNKNOWN"),
                "Circulating %": r.get("circulating_pct", np.nan),
                "Supply basis": r.get("supply_basis", "Unavailable"),
                "FDV": r.get("fdv", np.nan),
                "Market cap": r.get("market_cap", np.nan),
                "FDV / MCap": r.get("fdv_mcap", np.nan),
                "Tokenomics risks": r.get("tokenomics_risks", ""),
                "VC / unlock review": r.get("vc_unlock_review", "UNVERIFIED"),
                "Price": r["price"],
                "To resistance %": r["distance_pct"],
                "Tests": r["resistance_tests"],
                "RSI": r["rsi"],
                "ATR ratio": r["atr_ratio"],
                "Vol ratio": r["volume_ratio"],
                "RS vs BTC %": r["rs_vs_btc_pct"],
                "RS vs BTC 96h %": r.get("rs_vs_btc_96h_pct", np.nan),
                "R:R": r["risk_reward"],
                "Entry low": r["entry_low"],
                "Entry high": r["entry_high"],
                "Entry basis": r["entry_basis"],
                "Breakout": r["resistance"],
                "Invalidation": r["invalidation"],
                "Target +5%": r["target_1"],
                "Target +10%": r["target_2"],
                "First resistance target": r["first_take_profit"],
                "First resistance basis": r["first_take_profit_basis"],
                "Sell target": r["projected_target"],
                "Stretch target": r["stretch_target"],
                "Trade verdict": (
                    "QUALIFIES — 30%+ GROSS TARGET"
                    if r["eligible"]
                    else "PASS — " + r["reason"]
                ),
                "Target upside %": r["target_upside_pct"],
                "Target basis": r["target_basis"],
                "4Y cycle position %": r["cycle_position_pct"],
                "Accumulation signal": r["bottom_status"],
                "Accumulation score": r["bottom_score"],
                "Accumulation low": r["accumulation_low"],
                "Accumulation high": r["accumulation_high"],
                "In accumulation zone": r["in_accumulation_zone"],
                "Cycle accumulation low": r["cycle_accumulation_low"],
                "Cycle accumulation high": r["cycle_accumulation_high"],
                "In cycle accumulation zone": r["in_cycle_accumulation_zone"],
                "Cycle accumulation basis": r["cycle_accumulation_basis"],
                "Accumulation verdict": r["accumulation_verdict"],
                "Previous cycle-high reference": r["previous_cycle_high_reference"],
                "24h quote vol": r["quote_volume_24h"],
                "_components": r["components"],
            }
            for r in rows
        ])
        display.attrs["markets_selected"] = len(universe)
        display.attrs["markets_completed"] = attempted
        return display, raw, errors
    finally:
        await exchange.close()


def fmt_price(v: float) -> str:
    v = float(v)
    if v >= 1000:
        return f"{v:,.2f}"
    if v >= 1:
        return f"{v:,.4f}"
    if v >= 0.01:
        return f"{v:.5f}"
    return f"{v:.8f}"


def fmt_optional_price(v: float, fallback: str = "No qualifying target") -> str:
    value = _safe_float(v, np.nan)
    return fmt_price(value) if math.isfinite(value) else fallback


def make_chart(
    df: pd.DataFrame,
    row: pd.Series,
    timeframe_label: str = "4h",
    max_bars: int = 180,
) -> go.Figure:
    d = df.tail(max_bars)
    chart_low = float(d["low"].min())
    chart_high = float(d["high"].max())

    def level_is_visible(low: float, high: float) -> bool:
        padding = max(chart_high - chart_low, chart_high * 0.05)
        return high >= chart_low - padding * 0.35 and low <= chart_high + padding * 0.35

    fig = go.Figure()
    fig.add_trace(go.Candlestick(
        x=d["timestamp"], open=d["open"], high=d["high"], low=d["low"], close=d["close"], name=timeframe_label
    ))
    fig.add_hline(y=float(row["Breakout"]), line_dash="dash", annotation_text="Breakout / resistance")
    fig.add_hline(y=float(row["Invalidation"]), line_dash="dot", annotation_text="Invalidation")
    fig.add_hrect(
        y0=float(row["Entry low"]), y1=float(row["Entry high"]),
        opacity=0.12, line_width=0, fillcolor="#2ecc71", annotation_text="Pre-breakout entry zone",
    )
    if "Accumulation low" in row and "Accumulation high" in row:
        daily_zone_low = float(row["Accumulation low"])
        daily_zone_high = float(row["Accumulation high"])
        if level_is_visible(daily_zone_low, daily_zone_high):
            fig.add_hrect(
                y0=daily_zone_low, y1=daily_zone_high,
                opacity=0.10, line_width=0, fillcolor="#3498db",
                annotation_text="Daily base accumulation zone",
            )
    if (
        timeframe_label == "1w"
        and "Cycle accumulation low" in row
        and "Cycle accumulation high" in row
        and pd.notna(row["Cycle accumulation low"])
        and pd.notna(row["Cycle accumulation high"])
    ):
        cycle_zone_low = float(row["Cycle accumulation low"])
        cycle_zone_high = float(row["Cycle accumulation high"])
        if level_is_visible(cycle_zone_low, cycle_zone_high):
            fig.add_hrect(
                y0=cycle_zone_low, y1=cycle_zone_high,
                opacity=0.12, line_width=0, fillcolor="#8e44ad",
                annotation_text="Long-range weekly support zone",
            )
    if "Sell target" in row and pd.notna(row["Sell target"]):
        take_profit = float(row["Sell target"])
        if timeframe_label != "4h" or level_is_visible(take_profit, take_profit):
            fig.add_hline(
                y=take_profit, line_dash="dashdot",
                line_color="#f39c12", annotation_text="First take-profit target",
            )
    fig.update_layout(height=480, margin=dict(l=10, r=10, t=35, b=10), xaxis_rangeslider_visible=False)
    return fig


async def fetch_backtest_data(exchange_id: str, symbol: str, quote: str) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    cls = getattr(ccxt, exchange_id)
    exchange = cls({"enableRateLimit": True, "options": {"defaultType": "spot"}})
    try:
        await exchange.load_markets()
        coin4 = ohlcv_to_df(await exchange.fetch_ohlcv(symbol, timeframe="4h", limit=500))
        coind = ohlcv_to_df(await exchange.fetch_ohlcv(symbol, timeframe="1d", limit=365))
        btc4 = ohlcv_to_df(await exchange.fetch_ohlcv(f"BTC/{quote}", timeframe="4h", limit=500))
        return coin4, coind, btc4
    finally:
        await exchange.close()


def historical_backtest(coin4: pd.DataFrame, coind: pd.DataFrame, btc4: pd.DataFrame, cfg: ScreenerConfig, threshold: int, horizon: int) -> pd.DataFrame:
    records = []
    start = max(cfg.resistance_lookback + 30, 80)
    # Use only completed historical bars. We approximate daily context by data available up to each timestamp.
    for i in range(start, len(coin4) - horizon):
        cut4 = coin4.iloc[: i + 1].copy()
        ts = cut4["timestamp"].iloc[-1]
        cutd = coind[coind["timestamp"] <= ts].copy()
        cutbtc = btc4[btc4["timestamp"] <= ts].copy()
        if len(cutd) < 35 or len(cutbtc) < 30:
            continue
        result = score_setup(cut4, cutd, cutbtc, cfg)
        if not result.get("eligible") or result.get("score", 0) < threshold:
            continue
        entry = result["price"]
        invalidation = result["invalidation"]
        future = coin4.iloc[i + 1 : i + 1 + horizon]
        max_ret = (future["high"].max() / entry - 1) * 100
        min_ret = (future["low"].min() / entry - 1) * 100
        stop_hit = bool((future["low"] <= invalidation).any())
        records.append({
            "Time": ts,
            "Score": result["score"],
            "Entry": entry,
            "Max return %": max_ret,
            "Max adverse %": min_ret,
            "Hit +5%": max_ret >= 5,
            "Hit +10%": max_ret >= 10,
            "Hit +20%": max_ret >= 20,
            "Invalidation touched": stop_hit,
        })
    return pd.DataFrame(records)


# ---------------- UI ----------------
st.title("⚡ Pre-Breakout Crypto Screener")
st.caption("Built to find compression before expansion — and reject coins that have already run.")

st.markdown("""
<style>
/* mobile tuning */
@media (max-width: 700px) {
  .block-container { padding-top: 1rem; padding-left: 0.75rem; padding-right: 0.75rem; }
  h1 { font-size: 1.8rem !important; }
  div[data-testid="stMetricValue"] { font-size: 1.35rem; }
  div[data-testid="stDataFrame"] { font-size: 0.85rem; }
}
</style>
""", unsafe_allow_html=True)

with st.sidebar:
    st.header("Scan settings")
    exchange_name = st.selectbox("Exchange", list(EXCHANGES.keys()), index=2)
    universe_size = st.select_slider("Top liquid coins to scan", options=[25, 50, 75, 100, 150, 200, 250], value=200)
    st.caption("Scan up to 250 liquid coins. Fewer may qualify for your volume filter.")
    min_vol_m = st.number_input("Minimum 24h quote volume ($m)", min_value=1.0, max_value=500.0, value=5.0, step=1.0)
    threshold = st.slider("Minimum BUY score", 80, 95, 80, 1)
    max_distance = st.slider("Maximum distance below resistance (%)", 1.0, 8.0, 5.0, 0.25)
    max_rsi = st.slider("Maximum RSI", 60, 75, 69, 1)
    refresh_minutes = st.selectbox("Auto-refresh", [2, 5, 10, 15, 30], index=1, format_func=lambda x: f"Every {x} min")
    if universe_size > 150 and refresh_minutes < 10:
        refresh_minutes = 10
        st.caption("Scans above 150 coins refresh at most every 10 minutes.")
    sound_alerts = st.checkbox("Sound alert for new flags", value=False, help="Browser autoplay usually works after you have interacted with the page once.")
    st.divider()
    st.caption("No exchange API key is required. This dashboard only reads public market data.")

cfg = ScreenerConfig(
    exchange_id=EXCHANGES[exchange_name],
    universe_size=universe_size,
    min_quote_volume=min_vol_m * 1_000_000,
    score_threshold=threshold,
    near_resistance_max_pct=max_distance,
    max_rsi=float(max_rsi),
)

if "scan_df" not in st.session_state:
    st.session_state.scan_df = pd.DataFrame()
if "raw_data" not in st.session_state:
    st.session_state.raw_data = {}
if "previous_flags" not in st.session_state:
    st.session_state.previous_flags = set()
if "last_scan" not in st.session_state:
    st.session_state.last_scan = None

macro = macro_liquidity_regime()
st.session_state.macro_liquidity = macro

st.subheader("Macro liquidity regime")
if macro.get("available"):
    ml1, ml2, ml3, ml4 = st.columns(4)
    ml1.metric("Liquidity score", f"{macro['score']:.1f}/100")
    ml2.metric("Regime", macro["regime"])
    ml3.metric("Risk stance", macro["stance"])
    ml4.metric(
        "New swing risk",
        "ALLOWED" if macro["allows_new_swing_risk"] else "WAIT",
    )
    if not macro["allows_new_swing_risk"]:
        st.warning(
            "Technical setups can still be identified, but new swing BUY signals are "
            "downgraded to WAIT while the macro-liquidity regime is deteriorating or contracting."
        )
else:
    st.info(
        "Macro-liquidity data is currently incomplete, so the scanner will not block "
        "technical BUY signals on macro grounds."
    )

with st.expander("Macro liquidity factors"):
    macro_factors = pd.DataFrame(macro.get("factors", []))
    if not macro_factors.empty:
        st.dataframe(macro_factors, hide_index=True, use_container_width=True)
    if macro.get("errors"):
        st.caption("Unavailable inputs: " + " | ".join(macro["errors"]))
    st.caption(
        "Primary cycle framework: macro liquidity, not a fixed four-year crypto cycle. "
        "The regime combines broad money, Fed net liquidity, financial conditions, "
        "real yields, the broad US dollar and stablecoin supply. Four-year price-range "
        "statistics remain reference-only."
    )

manual_col, info_col = st.columns([1, 4])
with manual_col:
    manual_scan = st.button("Run scan now", type="primary", use_container_width=True)
with info_col:
    st.info("A flag means the setup matches the pre-breakout rules. It is not a prediction or a guarantee of a pump.")

run_every = f"{refresh_minutes}m"

@st.fragment(run_every=run_every)
def live_scan():
    # A dropdown interaction reruns the full app. Only fetch fresh market data when
    # the refresh interval has elapsed, so inspecting another coin cannot reset it.
    now = datetime.now(timezone.utc)
    last_scan = st.session_state.last_scan
    scan_due = (
        last_scan is None
        or (now - last_scan).total_seconds() >= refresh_minutes * 60
    )
    needs_candidate_refresh = (
        not st.session_state.scan_df.empty
        and "Trade reason" not in st.session_state.scan_df.columns
    )
    settings_changed = st.session_state.get("last_scan_config") != vars(cfg)
    should_scan = manual_scan or needs_candidate_refresh or settings_changed or scan_due
    if should_scan:
        status = st.status(f"Scanning top {cfg.universe_size} liquid {cfg.quote} spot markets on {exchange_name}…", expanded=False)
        try:
            def report_progress(completed, total):
                status.update(label=f"Analysing markets: {completed}/{total} completed…")
            df, raw, errors = asyncio.run(scan_exchange(cfg, progress=report_progress))
            if df.empty and errors:
                raise RuntimeError("No markets could be scored; previous results have been retained. " + errors[0])
            st.session_state.scan_df = df
            st.session_state.raw_data = raw
            st.session_state.last_scan_config = dict(vars(cfg))
            st.session_state.last_scan = datetime.now(timezone.utc)
            selected_count = df.attrs.get("markets_selected", len(df))
            status.update(
                label=f"Scan finished — {len(df)} coins scored from {selected_count} selected markets",
                state="complete",
            )
            if len(df) < selected_count:
                st.warning(
                    f"Partial coverage: {len(df)} of {selected_count} selected markets were scored. "
                    "See market-data warnings for unavailable or timed-out data."
                )
            if errors:
                with st.expander(f"{len(errors)} market-data warnings"):
                    st.code("\n".join(errors[:25]))
        except Exception as e:
            status.update(label="Scan failed", state="error")
            st.error(f"{type(e).__name__}: {e}")
            return

    df = st.session_state.scan_df.copy()
    if st.session_state.last_scan:
        st.caption("Last scan: " + st.session_state.last_scan.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z"))

    if df.empty:
        st.warning("No coins could be scored from the available market data. Check market-data warnings and try another scan.")
        return

    technical_swing_setups = df[
        (df["Trade verdict"] == "QUALIFIES — 30%+ GROSS TARGET")
        & (df["Score"] >= cfg.score_threshold)
    ].copy().sort_values("Score", ascending=False)
    rs_qualified_setups = technical_swing_setups[
        (technical_swing_setups["Coin"] == "BTC")
        | (technical_swing_setups["RS vs BTC %"] > 0)
    ].copy()
    tokenomics_qualified_setups = rs_qualified_setups[
        rs_qualified_setups["Tokenomics gate"] == "PASS"
    ].copy()
    macro_now = st.session_state.get("macro_liquidity") or {}
    macro_allows_new_risk = bool(macro_now.get("allows_new_swing_risk", True))
    swing_setups = (
        tokenomics_qualified_setups
        if macro_allows_new_risk
        else tokenomics_qualified_setups.iloc[0:0].copy()
    )
    accumulation_setups = df[
        df["Accumulation verdict"] == "ACCUMULATION READY"
    ].copy().sort_values("Accumulation score", ascending=False)

    # Tables retain potential candidates even when no actionable setups exist.
    swing_candidates = df.copy()
    swing_candidates["Status"] = np.where(
        swing_candidates["Symbol"].isin(swing_setups["Symbol"]), "BUY", "WAIT"
    )
    swing_candidates["Macro regime"] = macro_now.get("regime", "DATA LIMITED")
    swing_candidates["Macro score"] = macro_now.get("score", np.nan)
    swing_candidates["Reason"] = swing_candidates.apply(
        lambda row: (
            "Meets technical rules and macro liquidity allows new swing risk"
            if row["Status"] == "BUY"
            else (
                (
                    "Technical setup qualifies, but the altcoin is not beating BTC over the "
                    "48-hour relative-strength window. "
                    if (
                        row["Symbol"] in set(technical_swing_setups["Symbol"])
                        and row["Coin"] != "BTC"
                        and row["RS vs BTC %"] <= 0
                    )
                    else ""
                )
                + (
                    (
                        "Technical setup qualifies, but tokenomics need review: "
                        + (
                            "circulating float is below the 25% rule. "
                            if row.get("Tokenomics gate") == "FAIL"
                            else "circulating/total supply could not be verified. "
                        )
                    )
                    if (
                        row["Symbol"] in set(rs_qualified_setups["Symbol"])
                        and row.get("Tokenomics gate") != "PASS"
                    )
                    else ""
                )
                )
                + (
                    f"Technical setup qualifies, but macro liquidity is "
                    f"{macro_now.get('regime', 'DATA LIMITED')} "
                    f"({macro_now.get('score', np.nan):.1f}/100). "
                    if (
                        row["Symbol"] in set(tokenomics_qualified_setups["Symbol"])
                        and not macro_allows_new_risk
                        and pd.notna(macro_now.get("score", np.nan))
                    )
                    else ""
                )
                + (
                    f"Score {row['Score']:.1f} below {cfg.score_threshold}. "
                    if row["Score"] < cfg.score_threshold else ""
                )
                + (
                    row["Trade reason"]
                    if row["Trade verdict"] != "QUALIFIES — 30%+ GROSS TARGET" else ""
                )
            )
        ), axis=1,
    )
    swing_candidates = swing_candidates.sort_values(
        ["Status", "Score"], ascending=[True, False]
    )
    accumulation_candidates = df.copy()
    accumulation_candidates["Status"] = np.where(
        accumulation_candidates["Symbol"].isin(accumulation_setups["Symbol"]),
        "ACCUMULATE", "WAIT",
    )
    accumulation_candidates["Reason"] = accumulation_candidates.apply(
        lambda row: (
            "Meets accumulation rules" if row["Status"] == "ACCUMULATE"
            else (
                (f"Base score {row['Accumulation score']:.1f} below 70. "
                 if row["Accumulation score"] < 70 else "")
                + ("Price outside the confirmed daily base accumulation zone."
                   if not row["In accumulation zone"]
                   else "")
            )
        ), axis=1,
    )
    accumulation_candidates = accumulation_candidates.sort_values(
        ["Status", "Accumulation score"], ascending=[True, False]
    )

    current_flags = set(swing_setups["Symbol"].tolist()) | set(
        accumulation_setups["Symbol"].tolist()
    )
    new_flags = current_flags - st.session_state.previous_flags
    if new_flags:
        st.toast(
            "New actionable setup: "
            + ", ".join(sorted(symbol.split("/")[0] for symbol in new_flags))
        )
        if sound_alerts:
            sr = 16000
            t = np.linspace(0, 0.28, int(sr * 0.28), endpoint=False)
            tone = (0.20 * np.sin(2 * np.pi * 880 * t)).astype(np.float32)
            st.audio(tone, sample_rate=sr, autoplay=True)
    st.session_state.previous_flags = current_flags

    best_swing_score = (
        f"{swing_setups['Score'].max():.1f}/100"
        if not swing_setups.empty
        else "None"
    )
    current_market_trend = (
        str(df["Market trend"].dropna().iloc[0])
        if "Market trend" in df.columns and not df["Market trend"].dropna().empty
        else "UNAVAILABLE"
    )
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Candidates analysed", len(df))
    c2.metric(f"Swing BUYs ≥ {cfg.score_threshold}", len(swing_setups))
    c3.metric("Accumulation setups", len(accumulation_setups))
    c4.metric("Best swing score", best_swing_score)
    c5.metric("Market trend (BTC)", current_market_trend)

    swing_tab, accumulation_tab = st.tabs(["Swing trades", "Accumulation"])

    with swing_tab:
        st.subheader("Swing-trade candidates")
        st.caption(
            f"All {len(df)} analysed coins are shown. BUY requires a trade score of "
            f"{cfg.score_threshold}+ and the existing shape and 30% gross-target rules. "
            "A technical qualifier is only promoted to BUY when an altcoin is beating BTC over "
            "the 48h relative-strength window, circulating supply is at least 25% of total/max "
            "supply, and the macro-liquidity regime is not deteriorating/contracting. "
            "Unknown tokenomics remain WAIT rather than passing by assumption. "
            "WAIT candidates remain visible with their reasons. "
            "Green = preferred, amber = borderline, red = weak or extended."
        )
        if swing_setups.empty:
            rs_blocked = len(technical_swing_setups) - len(rs_qualified_setups)
            tokenomics_blocked = len(rs_qualified_setups) - len(tokenomics_qualified_setups)
            if rs_blocked > 0:
                st.info(
                    f"{rs_blocked} technical setup(s) currently qualify technically but remain "
                    "WAIT because the altcoin is not beating BTC over the 48h RS window."
                )
            elif tokenomics_blocked > 0:
                st.info(
                    f"{tokenomics_blocked} technical setup(s) currently qualify technically "
                    "but remain WAIT because the 25% circulating-supply tokenomics gate "
                    "fails or cannot be verified."
                )
            elif not tokenomics_qualified_setups.empty and not macro_allows_new_risk:
                st.info(
                    f"{len(tokenomics_qualified_setups)} technical setup(s) currently meet the "
                    f"{cfg.score_threshold}+, 30% target and tokenomics rules, but macro liquidity is "
                    f"{macro_now.get('regime', 'DATA LIMITED')}; they remain WAIT."
                )
            else:
                st.info(
                    f"No swing-trade setup currently meets the {cfg.score_threshold}+ "
                    "BUY rules and 30% gross-target requirement."
                )
        swing_cols = [
            "Coin", "Status", "Coin trend", "Market trend", "Tokenomics gate", "Circulating %", "FDV / MCap", "Tokenomics risks", "Macro regime", "Macro score", "Score", "Reason", "Price", "To resistance %", "Tests", "RSI",
            "ATR ratio", "Vol ratio", "RS vs BTC %", "RS vs BTC 96h %", "R:R",
            "Entry low", "Entry high", "Entry basis", "Breakout",
            "Invalidation", "First resistance target", "Sell target",
            "Stretch target", "Target upside %", "Target basis",
        ]
        styled_swing = swing_candidates[swing_cols].style
        for trend_column in ["Coin trend", "Market trend"]:
            styled_swing = styled_swing.map(
                lambda value, column=trend_column: scan_cell_style(value, column),
                subset=[trend_column],
            )
        styled_swing = styled_swing.map(
            lambda value: (
                "background-color: #d8f3dc; color: #16351c; font-weight: 600"
                if str(value) == "PASS"
                else "background-color: #ffd6d6; color: #5c1717; font-weight: 600"
                if str(value) == "FAIL"
                else "background-color: #fff3bf; color: #5f4500; font-weight: 600"
            ),
            subset=["Tokenomics gate"],
        )
        for column in [
            "Tests", "RSI", "ATR ratio", "Vol ratio", "RS vs BTC %", "RS vs BTC 96h %", "R:R",
        ]:
            styled_swing = styled_swing.map(
                lambda value, column=column: scan_cell_style(value, column),
                subset=[column],
            )
        st.dataframe(
            styled_swing,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Circulating %": st.column_config.NumberColumn(format="%.1f%%"),
                "FDV / MCap": st.column_config.NumberColumn(format="%.2fx"),
                "Macro score": st.column_config.ProgressColumn(
                    "Macro liquidity", min_value=0, max_value=100, format="%.1f"
                ),
                "Score": st.column_config.ProgressColumn(
                    "Trade score", min_value=0, max_value=100, format="%.1f"
                ),
                "To resistance %": st.column_config.NumberColumn(format="%.2f%%"),
                "RS vs BTC %": st.column_config.NumberColumn("RS vs BTC 48h %", format="%.2f%%"),
                "RS vs BTC 96h %": st.column_config.NumberColumn(format="%.2f%%"),
                "R:R": st.column_config.NumberColumn(format="%.2f"),
                "Price": st.column_config.NumberColumn(format="%.8g"),
                "Entry low": st.column_config.NumberColumn(format="%.8g"),
                "Entry high": st.column_config.NumberColumn(format="%.8g"),
                "Breakout": st.column_config.NumberColumn(format="%.8g"),
                "Invalidation": st.column_config.NumberColumn(format="%.8g"),
                "First resistance target": st.column_config.NumberColumn(
                    "First resistance / partial profit", format="%.8g"
                ),
                "Sell target": st.column_config.NumberColumn(
                    "30% trade target", format="%.8g"
                ),
                "Stretch target": st.column_config.NumberColumn(format="%.8g"),
                "Target upside %": st.column_config.NumberColumn(format="%.2f%%"),
            },
        )

    with accumulation_tab:
        st.subheader("Accumulation candidates")
        st.caption(
            "All analysed coins are ranked by their separate accumulation score. "
            "ACCUMULATE requires at least 70 and price inside the confirmed daily "
            "base accumulation zone. Long-range weekly support and the 4Y range are "
            "reference-only and do not trigger the decision."
        )
        if accumulation_setups.empty:
            st.info("No coin currently meets the confirmed accumulation rules.")
        accumulation_cols = [
            "Coin", "Status", "Coin trend", "Market trend", "Tokenomics gate", "Circulating %", "FDV / MCap", "Tokenomics risks", "Accumulation score", "Reason", "Price", "Accumulation signal",
            "Accumulation low", "Accumulation high", "In accumulation zone",
            "Cycle accumulation low", "Cycle accumulation high",
            "In cycle accumulation zone", "4Y cycle position %",
            "Previous cycle-high reference", "Accumulation verdict",
        ]
        accumulation_display = accumulation_candidates[accumulation_cols].rename(columns={
            "Cycle accumulation low": "Weekly support low",
            "Cycle accumulation high": "Weekly support high",
            "In cycle accumulation zone": "In weekly support zone",
            "4Y cycle position %": "4Y range position % (reference)",
            "Previous cycle-high reference": "4Y range-high reference",
        })
        styled_accumulation = accumulation_display.style.map(
            lambda value: scan_cell_style(value, "Accumulation signal"),
            subset=["Accumulation signal"],
        )
        for trend_column in ["Coin trend", "Market trend"]:
            styled_accumulation = styled_accumulation.map(
                lambda value, column=trend_column: scan_cell_style(value, column),
                subset=[trend_column],
            )
        styled_accumulation = styled_accumulation.map(
            lambda value: (
                "background-color: #d8f3dc; color: #16351c; font-weight: 600"
                if str(value) == "PASS"
                else "background-color: #ffd6d6; color: #5c1717; font-weight: 600"
                if str(value) == "FAIL"
                else "background-color: #fff3bf; color: #5f4500; font-weight: 600"
            ),
            subset=["Tokenomics gate"],
        )
        st.dataframe(
            styled_accumulation,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Circulating %": st.column_config.NumberColumn(format="%.1f%%"),
                "FDV / MCap": st.column_config.NumberColumn(format="%.2fx"),
                "Accumulation score": st.column_config.ProgressColumn(
                    "Accumulation score",
                    min_value=0,
                    max_value=100,
                    format="%.1f",
                ),
                "Price": st.column_config.NumberColumn(format="%.8g"),
                "Accumulation low": st.column_config.NumberColumn(format="%.8g"),
                "Accumulation high": st.column_config.NumberColumn(format="%.8g"),
                "Weekly support low": st.column_config.NumberColumn(format="%.8g"),
                "Weekly support high": st.column_config.NumberColumn(format="%.8g"),
                "4Y range position % (reference)": st.column_config.NumberColumn(format="%.1f%%"),
                "4Y range-high reference": st.column_config.NumberColumn(format="%.8g"),
            },
        )

live_scan()

st.divider()
st.subheader("Quick analyse")
st.caption("Search any active coin on the selected exchange, even if it did not appear in the main scan.")

if "quick_analysis" not in st.session_state:
    st.session_state.quick_analysis = None

qa_input_col, qa_button_col = st.columns([4, 1])
with qa_input_col:
    quick_query = st.text_input(
        "Ticker or coin name",
        placeholder="For example: SOL, SOL/USDT or Solana",
        key="quick_query",
    )
with qa_button_col:
    st.write("")
    st.write("")
    run_quick_analysis = st.button("Analyse", type="primary", use_container_width=True)

if run_quick_analysis:
    if not quick_query.strip():
        st.warning("Enter a ticker or coin name first.")
    else:
        with st.spinner(f"Analysing {quick_query.strip()}…"):
            try:
                qa_symbol, qa_result, qa_raw = asyncio.run(analyse_individual_coin(cfg, quick_query))
                st.session_state.quick_analysis = {
                    "symbol": qa_symbol,
                    "result": qa_result,
                    "raw": qa_raw,
                    "exchange": exchange_name,
                }
            except Exception as e:
                st.session_state.quick_analysis = None
                st.error(f"{type(e).__name__}: {e}")

qa = st.session_state.quick_analysis
if qa:
    qa_result = qa["result"]
    qa_symbol = qa["symbol"]
    st.markdown(f"### {qa_symbol.split('/')[0]} on {qa['exchange']}")

    if "score" not in qa_result:
        st.warning(qa_result.get("reason", "Not enough market data to score this coin."))
    else:
        macro_now = st.session_state.get("macro_liquidity") or {}
        tokenomics_gate = qa_result.get("tokenomics_gate", "UNKNOWN")
        qa_is_btc = qa_symbol.split("/")[0].upper() == "BTC"
        qa_rs_pass = qa_is_btc or qa_result.get("rs_vs_btc_pct", -999) > 0
        if (
            qa_result.get("eligible")
            and qa_rs_pass
            and tokenomics_gate == "PASS"
            and macro_now.get("allows_new_swing_risk", True)
        ):
            st.success(
                "TRADE QUALIFIES: technical pre-breakout rules pass, circulating supply "
                "meets the 25% tokenomics rule, and macro liquidity allows new swing risk."
            )
        elif qa_result.get("eligible") and not qa_rs_pass:
            st.warning(
                "TECHNICAL QUALIFIER — RELATIVE-STRENGTH WAIT: the altcoin is not "
                "currently beating BTC over the 48-hour window."
            )
        elif qa_result.get("eligible") and tokenomics_gate != "PASS":
            st.warning(
                "TECHNICAL QUALIFIER — TOKENOMICS WAIT: "
                + (
                    "circulating supply is below 25% of total/max supply."
                    if tokenomics_gate == "FAIL"
                    else "circulating versus total/max supply could not be verified."
                )
            )
        elif qa_result.get("eligible"):
            st.warning(
                "TECHNICAL QUALIFIER — MACRO WAIT: the setup passes the pre-breakout "
                f"rules and tokenomics gate, but macro liquidity is "
                f"{macro_now.get('regime', 'DATA LIMITED')} "
                f"({macro_now.get('score', np.nan):.1f}/100)."
            )
        elif qa_result.get("shape_eligible"):
            st.warning("TRADE PASS: the pre-breakout shape is present, but no credible 30% gross-profit target was found.")
        else:
            st.warning("TRADE PASS: " + qa_result.get("reason", "Shape filter not met"))
        accumulation_verdict = qa_result.get("accumulation_verdict", "NOT READY TO ACCUMULATE")
        if accumulation_verdict == "ACCUMULATION READY":
            st.success("LONG-TERM: " + accumulation_verdict)
        elif accumulation_verdict.startswith("WATCH"):
            st.info("LONG-TERM: " + accumulation_verdict)
        else:
            st.caption("LONG-TERM: " + accumulation_verdict)

        q1, q2, q3, q4 = st.columns(4)
        q1.metric("Trade setup score", f"{qa_result['score']:.1f}/100")
        q2.metric("Price", fmt_price(qa_result["price"]))
        q3.metric("To resistance", f"{qa_result['distance_pct']:.2f}%")
        q4.metric("RSI", f"{qa_result['rsi']:.1f}")

        rs1, rs2 = st.columns(2)
        rs1.metric("RS vs BTC — 48h", f"{qa_result.get('rs_vs_btc_pct', np.nan):+.2f}%")
        rs2.metric("RS vs BTC — 96h", f"{qa_result.get('rs_vs_btc_96h_pct', np.nan):+.2f}%")

        tr1, tr2, tr3, tr4 = st.columns(4)
        tr1.metric("Coin trend", qa_result.get("coin_trend", "UNAVAILABLE"))
        tr2.metric("Market trend (BTC)", qa_result.get("market_trend", "UNAVAILABLE"))
        tr3.metric("Coin daily / 4h", f"{qa_result.get('coin_trend_daily', '—')} / {qa_result.get('coin_trend_4h', '—')}")
        tr4.metric("BTC daily / 4h", f"{qa_result.get('market_trend_daily', '—')} / {qa_result.get('market_trend_4h', '—')}")
        st.caption(
            f"Coin trend: {qa_result.get('coin_trend_detail', '')} · "
            f"Market trend: {qa_result.get('market_trend_detail', '')}"
        )

        tok1, tok2, tok3, tok4 = st.columns(4)
        tok1.metric("Tokenomics gate", qa_result.get("tokenomics_gate", "UNKNOWN"))
        circ_pct = qa_result.get("circulating_pct", np.nan)
        tok2.metric(
            "Circulating / supply",
            f"{circ_pct:.1f}%" if pd.notna(circ_pct) else "Unavailable",
            qa_result.get("supply_basis", ""),
        )
        fdv_mcap = qa_result.get("fdv_mcap", np.nan)
        tok3.metric("FDV / Market cap", f"{fdv_mcap:.2f}x" if pd.notna(fdv_mcap) else "Unavailable")
        tok4.metric("VC / unlock review", "Needs verification")
        if qa_result.get("tokenomics_risks"):
            st.caption("Tokenomics risks: " + qa_result["tokenomics_risks"])

        qa_row = pd.Series({
            "Breakout": qa_result["resistance"],
            "Invalidation": qa_result["invalidation"],
            "Entry low": qa_result["entry_low"],
            "Entry high": qa_result["entry_high"],
            "Accumulation low": qa_result["accumulation_low"],
            "Accumulation high": qa_result["accumulation_high"],
            "Sell target": qa_result["projected_target"],
            "Cycle accumulation low": qa_result["cycle_accumulation_low"],
            "Cycle accumulation high": qa_result["cycle_accumulation_high"],
        })
        qa_timeframes = {
            "4-hour — entry timing (30 days)": ("4h", "4h", 180),
            "Daily — structure (up to 1 year)": ("1d", "1d", 365),
            "Weekly — long-range structure (up to ~4 years)": ("1w", "1w", 209),
        }
        qa_timeframe_choice = st.selectbox(
            "Chart timeframe",
            list(qa_timeframes.keys()),
            key="quick_chart_timeframe",
        )
        qa_data_key, qa_label, qa_bars = qa_timeframes[qa_timeframe_choice]
        qa_chart_data = qa["raw"].get(qa_data_key, pd.DataFrame())
        if not qa_chart_data.empty:
            qa_chart_key = (
                "quick_chart_" + qa_symbol.replace("/", "_").replace(":", "_")
                + "_" + qa_data_key
            )
            st.plotly_chart(
                make_chart(qa_chart_data, qa_row, qa_label, qa_bars),
                use_container_width=True,
                key=qa_chart_key,
            )

        l1, l2, l3, l4 = st.columns(4)
        l1.metric("Pre-breakout entry zone", f"{fmt_price(qa_result['entry_low'])} – {fmt_price(qa_result['entry_high'])}", qa_result["entry_basis"])
        l2.metric("Breakout level", fmt_price(qa_result["resistance"]))
        l3.metric("Invalidation", fmt_price(qa_result["invalidation"]))
        l4.metric("Risk / reward", f"{qa_result['risk_reward']:.2f}:1")

        t1, t2, t3, t4 = st.columns(4)
        t1.metric(
            "First resistance / partial profit",
            fmt_price(qa_result["first_take_profit"]),
            qa_result["first_take_profit_basis"],
        )
        t2.metric("30% trade target", fmt_optional_price(qa_result["projected_target"]))
        gross_upside = qa_result.get("target_upside_pct", np.nan)
        t3.metric(
            "Gross upside from planned entry",
            f"{gross_upside:.1f}%" if pd.notna(gross_upside) else "Below requirement",
        )
        t4.metric(
            "Reward / risk",
            f"{qa_result['risk_reward']:.2f}:1" if qa_result.get("trade_target_eligible") else "Not qualified",
        )

        a1, a2, a3, a4 = st.columns(4)
        a1.metric("Bottoming signal", qa_result["bottom_status"], f"{qa_result['bottom_score']:.1f}/100")
        a2.metric(
            "Daily base accumulation zone",
            f"{fmt_price(qa_result['accumulation_low'])} – {fmt_price(qa_result['accumulation_high'])}",
        )
        a3.metric("Inside daily base zone", "Yes" if qa_result["in_accumulation_zone"] else "No")
        a4.metric(
            "Long-term accumulation verdict",
            qa_result["accumulation_verdict"],
        )
        cycle_position = qa_result.get("cycle_position_pct", np.nan)
        cycle_text = f"{cycle_position:.1f}%" if pd.notna(cycle_position) else "Unavailable"
        cycle_low = qa_result.get("cycle_accumulation_low", np.nan)
        cycle_high = qa_result.get("cycle_accumulation_high", np.nan)
        cycle_zone_text = (
            f"{fmt_price(cycle_low)} – {fmt_price(cycle_high)}"
            if pd.notna(cycle_low) and pd.notna(cycle_high)
            else "Unavailable"
        )
        st.caption(
            f"Long-range weekly support zone: {cycle_zone_text} · "
            f"Inside zone: {'Yes' if qa_result.get('in_cycle_accumulation_zone') else 'No'} · "
            f"{qa_result.get('cycle_accumulation_basis', '')}"
        )
        st.caption(
            f"30% target basis: {qa_result['target_basis']} · "
            f"Next qualifying target: {fmt_optional_price(qa_result['stretch_target'], 'Unavailable')} · "
            f"Position within available 4Y range (reference only): {cycle_text}"
        )

        qa_components = qa_result["components"]
        qa_comp_df = pd.DataFrame({
            "Factor": list(qa_components.keys()),
            "Points": list(qa_components.values()),
        })
        st.bar_chart(qa_comp_df.set_index("Factor"), horizontal=True)

st.divider()
st.subheader("Inspect a setup")
scan_df = st.session_state.scan_df
if not scan_df.empty:
    symbols = scan_df["Symbol"].tolist()
    # Keep the user's inspected coin selected across normal Streamlit reruns and
    # scheduled rescans. Fall back to the new top result only if it leaves the scan.
    if st.session_state.get("inspect_symbol") not in symbols:
        st.session_state.inspect_symbol = symbols[0]
    selected = st.selectbox(
        "Candidate",
        symbols,
        key="inspect_symbol",
        format_func=lambda s: f"{s.split('/')[0]} — {float(scan_df.loc[scan_df['Symbol']==s, 'Score'].iloc[0]):.1f}/100",
    )
    row = scan_df.loc[scan_df["Symbol"] == selected].iloc[0]
    raw = st.session_state.raw_data.get(selected, {})
    inspect_timeframes = {
        "4-hour — entry timing (30 days)": ("4h", "4h", 180),
        "Daily — structure (up to 1 year)": ("1d", "1d", 365),
        "Weekly — long-range structure (up to ~4 years)": ("1w", "1w", 209),
    }
    available_timeframes = {
        label: values
        for label, values in inspect_timeframes.items()
        if values[0] in raw and not raw[values[0]].empty
    }
    if available_timeframes:
        inspect_timeframe_choice = st.selectbox(
            "Chart timeframe",
            list(available_timeframes.keys()),
            key="inspect_chart_timeframe",
        )
        inspect_data_key, inspect_label, inspect_bars = available_timeframes[inspect_timeframe_choice]
        chart_key = (
            "inspect_chart_" + selected.replace("/", "_").replace(":", "_")
            + "_" + inspect_data_key
        )
        st.plotly_chart(
            make_chart(raw[inspect_data_key], row, inspect_label, inspect_bars),
            use_container_width=True,
            key=chart_key,
        )

    trend1, trend2 = st.columns(2)
    trend1.metric("Coin trend", row.get("Coin trend", "UNAVAILABLE"))
    trend2.metric("Market trend (BTC)", row.get("Market trend", "UNAVAILABLE"))
    if row.get("Coin trend detail") or row.get("Market trend detail"):
        st.caption(
            f"Coin: {row.get('Coin trend detail', '')} · "
            f"Market: {row.get('Market trend detail', '')}"
        )

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Pre-breakout entry zone", f"{fmt_price(row['Entry low'])} – {fmt_price(row['Entry high'])}", row["Entry basis"])
    m2.metric("Breakout level", fmt_price(row["Breakout"]))
    m3.metric("Invalidation", fmt_price(row["Invalidation"]))
    m4.metric("Risk / reward", f"{row['R:R']:.2f}:1")

    t1, t2, t3, t4 = st.columns(4)
    t1.metric("First resistance / partial profit", fmt_price(row["First resistance target"]), row["First resistance basis"])
    t2.metric("30% trade target", fmt_optional_price(row["Sell target"]))
    t3.metric(
        "Gross upside from planned entry",
        f"{row['Target upside %']:.1f}%" if pd.notna(row["Target upside %"]) else "Below requirement",
    )
    t4.metric(
        "Reward / risk",
        f"{row['R:R']:.2f}:1" if row["Trade verdict"].startswith("QUALIFIES") else "Not qualified",
    )

    a1, a2, a3, a4 = st.columns(4)
    a1.metric("Bottoming signal", row["Accumulation signal"], f"{row['Accumulation score']:.1f}/100")
    a2.metric(
        "Daily base accumulation zone",
        f"{fmt_price(row['Accumulation low'])} – {fmt_price(row['Accumulation high'])}",
    )
    a3.metric("Inside daily base zone", "Yes" if row["In accumulation zone"] else "No")
    a4.metric("Long-term accumulation verdict", row["Accumulation verdict"])
    cycle_text = f"{row['4Y cycle position %']:.1f}%" if pd.notna(row["4Y cycle position %"]) else "Unavailable"
    cycle_zone_text = (
        f"{fmt_price(row['Cycle accumulation low'])} – {fmt_price(row['Cycle accumulation high'])}"
        if pd.notna(row["Cycle accumulation low"]) and pd.notna(row["Cycle accumulation high"])
        else "Unavailable"
    )
    st.caption(
        f"Long-range weekly support zone: {cycle_zone_text} · "
        f"Inside zone: {'Yes' if row['In cycle accumulation zone'] else 'No'} · "
        f"{row['Cycle accumulation basis']}"
    )
    st.caption(
        f"30% target basis: {row['Target basis']} · "
        f"Next qualifying target: {fmt_optional_price(row['Stretch target'], 'Unavailable')} · "
        f"Position within available 4Y range (reference only): {cycle_text}"
    )

    comps = row["_components"]
    comp_df = pd.DataFrame({"Factor": list(comps.keys()), "Points": list(comps.values())})
    st.bar_chart(comp_df.set_index("Factor"), horizontal=True)
else:
    st.caption("Run a scan to inspect individual setups.")

st.divider()
st.subheader("Historical sanity check")
st.caption(
    "This is a simple event study, not a full execution simulator. It replays the "
    "technical setup only; the new macro-liquidity overlay is not yet historically "
    "replayed in this backtest."
)

if not scan_df.empty:
    bc1, bc2, bc3 = st.columns(3)
    with bc1:
        bt_symbol = st.selectbox("Coin to backtest", scan_df["Symbol"].tolist(), key="bt_symbol")
    with bc2:
        bt_threshold = st.slider("Historical score threshold", 65, 95, cfg.score_threshold, 1)
    with bc3:
        horizon = st.selectbox("Forward window", [6, 12, 18, 24, 36], index=3, format_func=lambda x: f"{x} × 4h bars ({x*4}h)")

    if st.button("Run backtest"):
        with st.spinner("Fetching historical candles and replaying the setup rules…"):
            try:
                coin4, coind, btc4 = asyncio.run(fetch_backtest_data(cfg.exchange_id, bt_symbol, cfg.quote))
                bt = historical_backtest(coin4, coind, btc4, cfg, bt_threshold, horizon)
                if bt.empty:
                    st.warning("No historical signals met those settings in the available candle history.")
                else:
                    b1, b2, b3, b4, b5 = st.columns(5)
                    b1.metric("Signals", len(bt))
                    b2.metric("Hit +5%", f"{bt['Hit +5%'].mean()*100:.1f}%")
                    b3.metric("Hit +10%", f"{bt['Hit +10%'].mean()*100:.1f}%")
                    b4.metric("Hit +20%", f"{bt['Hit +20%'].mean()*100:.1f}%")
                    b5.metric("Invalidation touched", f"{bt['Invalidation touched'].mean()*100:.1f}%")
                    st.dataframe(bt.sort_values("Time", ascending=False), use_container_width=True, hide_index=True)
            except Exception as e:
                st.error(f"Backtest failed: {type(e).__name__}: {e}")

with st.expander("How the opportunity scores work"):
    st.markdown(
        """
The score measures **technical setup quality, not probability of success or expected return**. The Swing trades tab shows trade quality; the Accumulation tab shows bottoming quality. WAIT rows remain visible for review and are not actionable signals.

#### Trend regime — directional context

Each coin and the wider crypto market (using BTC) are classified as **UPTREND, SIDEWAYS or DOWNTREND**. The **daily chart sets the primary direction** using price versus the 20/50 EMAs and the slope of the 50 EMA; the **4h chart confirms or weakens** that direction. The 200-day EMA is shown as longer-term context when enough history is available. Trend is currently displayed as decision context rather than a new hard BUY gate.

#### Relative strength gate — altcoin must beat Bitcoin

For an **altcoin** to become a BUY, its 48-hour return must be stronger than BTC's over the same period (**RS vs BTC > 0%**). BTC itself is exempt. The 96-hour reading remains confirmation: positive on both windows is stronger; positive 48h with weaker 96h can indicate early rotation. A technically good altcoin that is not beating BTC remains WAIT.

#### Tokenomics gate — supply quality

For altcoin BUY decisions, the scanner now requires **at least 25% of total supply (or max supply when total supply is unavailable) to be circulating**. Below 25% is treated as low float and remains WAIT; missing supply data is UNKNOWN and also remains WAIT rather than being assumed safe. The scanner also flags **FDV / market-cap ratios of 4x or more** as high-FDV/low-float risk. Detailed VC allocations and future insider unlock schedules require a specialist verified dataset and are shown as needing separate verification rather than guessed.

#### BUY score — pre-breakout swing-trade quality

- **20 raw pts — Structure:** higher lows, repeated resistance tests, 4h EMA structure.
- **15 raw pts — Compression:** ATR contraction and a tightening trading range.
- **15 raw pts — Volume:** volume dries up during the coil, with preference for stronger volume on up-bars.
- **15 raw pts — Relative strength:** coin return versus BTC over recent 4h windows.
- **10 raw pts — Momentum:** RSI in a constructive zone plus improving MACD histogram.
- **5 raw pts — OBV:** accumulation proxy via rising on-balance volume.
- **5 raw pts — Daily context:** daily trend constructive without being extremely stretched.
- **10 raw pts — Entry / R:R:** distance to resistance and projected reward versus invalidation risk.

Those weights total 95 raw points, which the app now normalises to a genuine **0–100 score**. BUY also has separate hard rules: the setup must still be below and near resistance, show at least two tests, avoid material overextension, and have a technically credible target offering at least **30% gross upside from the planned entry**.

#### ACCUMULATE score — bottoming quality

- **30 pts — Base proximity:** price is near its 60-day low.
- **25 pts — Higher lows:** the recent daily low is improving versus the prior base.
- **20 pts — Trend flattening:** the daily 20 EMA is stabilising or turning up.
- **15 pts — RSI recovery:** daily momentum is recovering from a constructive level.
- **10 pts — Daily OBV:** volume flow is improving.

ACCUMULATE also requires the score to reach 70 and price to be inside the confirmed daily base zone. Long-range weekly support and the four-year price range remain visible as technical reference only; they do not trigger ACCUMULATE.

#### Macro-liquidity regime — primary cycle framework

The scanner no longer assumes crypto must follow a fixed four-year cycle. New swing BUY signals are overlaid with a macro-liquidity regime built from **US M2 (20 pts), Fed net liquidity (15), Chicago Fed financial conditions (20), 10Y real-yield direction (15), the broad US dollar (15), and stablecoin supply growth (15)**. Scores below 42 are treated as a macro headwind and technically qualified swings remain WAIT until liquidity improves.
        """
    )

st.caption("Trading tool only — not financial advice. Crypto can gap through technical levels; always size risk independently of the score.")