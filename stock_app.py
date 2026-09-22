from __future__ import annotations

import base64
import datetime
import io
import json
import math
import re
import time
import zlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from html.parser import HTMLParser
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urlencode

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st
from streamlit.runtime.scriptrunner import get_script_run_ctx
from streamlit_cookies_controller import CookieController
from streamlit_local_storage import LocalStorage
from cl_signal_ui import render_module_header, render_decision_guidance
import yfinance as yf
from valuation import fundamental_analysis as valuation_fundamental_analysis
from strategy_scores_v3 import long_term_analysis, VALUATION_MODEL_VERSION, MIN_INVESTMENT_QUALITY_SCORE
from two_strategy import investment_decision, trade_decision
from trade_rules import (
    FundamentalSnapshot,
    build_fundamental_snapshot,
    business_sessions_until,
    evaluate_price_setup,
    score_fundamental_snapshot,
)

st.set_page_config(page_title="CL Signal · Stocks", page_icon="📈", layout="wide")



PRIORITY_DEFAULT = ""
PREPARED_SCAN_DIR = Path(__file__).resolve().parent / "prepared_scans"
PRIORITY_INVESTMENT_DIR = Path(__file__).resolve().parent / "prepared_priority_investments"
TRADE_PREPARED_DIR = Path(__file__).resolve().parent / "prepared_trade_fundamentals"
TRADE_TECHNICAL_DIR = Path(__file__).resolve().parent / "prepared_trade_technicals"
TRADE_RULEBOOK_BUILD = "2026.09.18.12"

EXCHANGE_UNIVERSES = {
    "NASDAQ": "nasdaq",
    "NYSE": "nyse",
    "OTC Markets": "otc",
    "London Stock Exchange": "lse",
    "Deutsche Börse Xetra": "xetra",
    "Gettex": "gettex",
    "LSE AIM": "lse_aim",
    "Toronto Stock Exchange": "tsx",
    "Euronext Paris": "euronext_paris",
    "SIX Swiss Exchange": "six",
    "Bolsa de Madrid": "madrid",
    "Euronext Brussels": "euronext_brussels",
    "Wiener Börse": "vienna",
    "Euronext Amsterdam": "euronext_amsterdam",
    "Euronext Lisbon": "euronext_lisbon",
}

# Both searches intentionally expose the same exact exchange list and order.
PUBLIC_UNIVERSES = EXCHANGE_UNIVERSES.copy()
INVESTMENT_UNIVERSES = EXCHANGE_UNIVERSES.copy()

HEADERS = {"User-Agent": "Mozilla/5.0 StockOpportunityScreener/1.0"}

PORTFOLIO_COOKIE_NAME = "cl_signal_stock_portfolio_v1"
PORTFOLIO_LOCAL_STORAGE_KEY = "cl_signal_stock_portfolio_v2"
PORTFOLIO_COOKIE_DAYS = 3650
INVESTMENT_STOCK_TARGET_PCT = 5.0
INVESTMENT_SECTOR_TARGET_PCT = 20.0
PORTFOLIO_SYMBOL_ALIASES = {
    # User-friendly broker tickers -> Yahoo Finance symbols.
    "MGNS": "MGNS.L",
    "BCHN": "BCHN.SW",
}


@dataclass
class Scores:
    trade: float
    hold: float
    opportunity: float
    classification: str


def safe(v, default=np.nan):
    try:
        x = float(v)
        return x if math.isfinite(x) else default
    except Exception:
        return default


def normalise_portfolio_holdings(frame: pd.DataFrame | None) -> pd.DataFrame:
    """Return user-owned position fields in a stable, backward-compatible shape."""
    columns = ["Ticker", "Position type", "Shares", "Average cost"]
    if frame is None:
        return pd.DataFrame(columns=columns)

    out = frame.copy()
    if "Ticker" not in out.columns:
        out["Ticker"] = ""
    if "Position type" not in out.columns:
        out["Position type"] = "INVESTMENT"
    if "Shares" not in out.columns:
        out["Shares"] = 0.0
    if "Average cost" not in out.columns:
        out["Average cost"] = 0.0

    out = out[columns].copy()
    out["Ticker"] = out["Ticker"].fillna("").astype(str).str.strip().str.upper()
    out["Position type"] = (
        out["Position type"]
        .fillna("INVESTMENT")
        .astype(str)
        .str.strip()
        .str.upper()
        .map(lambda value: "TRADE" if value == "TRADE" else "INVESTMENT")
    )
    out["Shares"] = pd.to_numeric(out["Shares"], errors="coerce").fillna(0.0)
    out["Average cost"] = pd.to_numeric(out["Average cost"], errors="coerce").fillna(0.0)

    # Position type defaults to INVESTMENT, so do not let that alone keep a blank row.
    active = (
        out["Ticker"].ne("")
        | out["Shares"].ne(0)
        | out["Average cost"].ne(0)
    )
    return out.loc[active].reset_index(drop=True)

def portfolio_holdings_payload(frame: pd.DataFrame | None) -> str:
    clean = normalise_portfolio_holdings(frame)
    return json.dumps(
        clean.to_dict(orient="records"),
        separators=(",", ":"),
        ensure_ascii=True,
    )


def portfolio_holdings_from_payload(payload) -> pd.DataFrame | None:
    if payload is None:
        return None
    try:
        records = json.loads(payload) if isinstance(payload, str) else payload
        if not isinstance(records, list):
            return None
        return normalise_portfolio_holdings(pd.DataFrame(records))
    except Exception:
        return None


def resolve_portfolio_symbol(symbol: str) -> str:
    """Resolve a broker-style ticker to the Yahoo symbol used for market data."""
    raw = str(symbol or "").strip().upper()
    if not raw:
        return raw
    if raw in PORTFOLIO_SYMBOL_ALIASES:
        return PORTFOLIO_SYMBOL_ALIASES[raw]
    try:
        resolved = resolve_company_query(raw)
        candidate = str(resolved.get("symbol") or "").strip().upper()
        if candidate:
            return candidate
    except Exception:
        pass
    return raw


def action_cell_style(value) -> str:
    """Return the RAG colour for a screener action without changing its value."""
    action = str(value).strip().upper()
    if action in {"BUY", "BUY CANDIDATE", "PAPER CANDIDATE", "ENTRY ZONE"}:
        return "background-color: #d8f3dc; color: #16351c; font-weight: 700"
    if action in {"WAIT", "WATCH", "READY TO VERIFY", "HOLD", "EARNINGS WAIT", "RETRY"}:
        return "background-color: #fff3bf; color: #5f4500; font-weight: 700"
    if action in {"PASS", "AVOID", "SELL", "BLOCKED", "EXTENDED", "INVALIDATED"}:
        return "background-color: #ffd6d6; color: #5c1717; font-weight: 700"
    if action in {"DATA STALE", "LATEST SESSION"}:
        return "background-color: #eceff3; color: #4b5563; font-weight: 700"
    return ""


def portfolio_action_cell_style(value) -> str:
    """Colour long-term investment actions without mixing in allocation warnings."""
    action = str(value).strip().upper()
    if action in {"BUY MORE", "ADD CANDIDATE", "HOLD"}:
        return "background-color: #d8f3dc; color: #16351c; font-weight: 700"
    if action == "REASSESS":
        return "background-color: #fff3bf; color: #5f4500; font-weight: 700"
    if action == "REVIEW FOR SALE":
        return "background-color: #ffd6d6; color: #5c1717; font-weight: 700"
    return ""


def trade_portfolio_action_cell_style(value) -> str:
    """Colour owned trade actions by execution urgency."""
    action = str(value).strip().upper()
    if action in {"HOLD", "EXIT / TARGET REACHED"}:
        return "background-color: #d8f3dc; color: #16351c; font-weight: 700"
    if action == "REASSESS":
        return "background-color: #fff3bf; color: #5f4500; font-weight: 700"
    if action in {"EXIT / REASSESS", "EXIT"}:
        return "background-color: #ffd6d6; color: #5c1717; font-weight: 700"
    return ""


def allocation_cell_style(value) -> str:
    """Colour an investment holding against the user's 5% single-stock ceiling."""
    weight = safe(value)
    if np.isnan(weight):
        return ""
    if weight > INVESTMENT_STOCK_TARGET_PCT:
        return "background-color: #ffd6d6; color: #5c1717; font-weight: 700"
    if weight >= INVESTMENT_STOCK_TARGET_PCT * 0.8:
        return "background-color: #fff3bf; color: #5f4500; font-weight: 700"
    return "background-color: #d8f3dc; color: #16351c; font-weight: 600"


def allocation_status_colour(weight_pct: float, target_pct: float) -> str:
    """Green below 80% of the ceiling, amber near it, red above it."""
    weight = safe(weight_pct)
    if np.isnan(weight):
        return "#9ca3af"
    if weight > target_pct:
        return "#ef4444"
    if weight >= target_pct * 0.8:
        return "#f59e0b"
    return "#22c55e"


def portfolio_donut(frame: pd.DataFrame, label_column: str, title: str, target_pct: float) -> go.Figure:
    """Build a compact donut chart whose colours communicate concentration only."""
    chart_data = frame[[label_column, "Market value £"]].copy()
    chart_data["Market value £"] = pd.to_numeric(chart_data["Market value £"], errors="coerce")
    chart_data = chart_data.dropna(subset=["Market value £"])
    chart_data = chart_data[chart_data["Market value £"] > 0]
    total = chart_data["Market value £"].sum()
    if total <= 0:
        return go.Figure()

    chart_data["Weight %"] = chart_data["Market value £"] / total * 100
    colours = [
        allocation_status_colour(weight, target_pct)
        for weight in chart_data["Weight %"]
    ]

    fig = go.Figure(go.Pie(
        labels=chart_data[label_column],
        values=chart_data["Market value £"],
        hole=0.58,
        sort=False,
        textinfo="label+percent",
        marker=dict(colors=colours, line=dict(color="white", width=2)),
        hovertemplate="%{label}<br>Value: £%{value:,.2f}<br>Weight: %{percent}<extra></extra>",
    ))
    fig.add_annotation(
        text=f"Target<br>≤{target_pct:.0f}%",
        x=0.5,
        y=0.5,
        showarrow=False,
        font=dict(size=16),
    )
    fig.update_layout(
        title=dict(text=title, x=0.5, xanchor="center"),
        height=360,
        margin=dict(l=10, r=10, t=55, b=10),
        showlegend=False,
    )
    return fig

def pct(v):
    x = safe(v)
    return None if np.isnan(x) else x * 100


def fmt_price(v):
    x = safe(v)
    if np.isnan(x):
        return "—"
    if abs(x) >= 1000:
        return f"{x:,.2f}"
    if abs(x) >= 1:
        return f"{x:,.2f}"
    return f"{x:.4f}"


def fmt_price_with_currency(v, currency: str | None = None):
    value = fmt_price(v)
    if value == "—":
        return value
    raw_currency = str(currency or "").strip()
    if raw_currency in {"GBp", "GBX"}:
        return f"{value}p"
    code = raw_currency.upper()
    if code == "USD":
        return f"${value}"
    if code == "GBP":
        return f"£{value}"
    if code == "EUR":
        return f"€{value}"
    if code == "CAD":
        return f"C${value}"
    if code == "CHF":
        return f"CHF {value}"
    return f"{value} {raw_currency}".strip()


@st.cache_data(ttl=3600, show_spinner=False)
def resolve_company_query(query: str) -> Dict:
    """Resolve either a ticker or a plain-English company name to a Yahoo symbol."""
    q = str(query or "").strip()
    if not q:
        return {"symbol": "", "name": "", "exchange": ""}

    try:
        search = yf.Search(q, max_results=10)
        quotes = getattr(search, "quotes", None) or []
    except Exception:
        quotes = []

    equity_quotes = []
    for quote in quotes:
        quote_type = str(quote.get("quoteType") or "").upper()
        symbol = str(quote.get("symbol") or "").strip()
        if not symbol:
            continue
        if quote_type in {"EQUITY", ""}:
            equity_quotes.append(quote)

    q_lower = q.lower()
    q_upper = q.upper()

    def _rank(quote):
        symbol = str(quote.get("symbol") or "").upper()
        long_name = str(quote.get("longname") or quote.get("longName") or "").strip()
        short_name = str(quote.get("shortname") or quote.get("shortName") or "").strip()
        names = [long_name.lower(), short_name.lower()]
        if symbol == q_upper:
            return (0, 0)
        if q_lower in names:
            return (1, 0)
        if any(name.startswith(q_lower) for name in names if name):
            return (2, min((len(name) for name in names if name), default=999))
        if any(q_lower in name for name in names if name):
            return (3, min((len(name) for name in names if name), default=999))
        return (4, 999)

    if equity_quotes:
        equity_quotes = sorted(equity_quotes, key=_rank)
        match = equity_quotes[0]
        return {
            "symbol": str(match.get("symbol") or q_upper).strip().upper(),
            "name": (
                match.get("longname")
                or match.get("longName")
                or match.get("shortname")
                or match.get("shortName")
                or q
            ),
            "exchange": match.get("exchDisp") or match.get("exchange") or "",
        }

    looks_like_ticker = (
        len(q) <= 15
        and " " not in q
        and q.replace(".", "").replace("-", "").isalnum()
    )
    if looks_like_ticker:
        return {"symbol": q_upper, "name": q_upper, "exchange": ""}

    return {"symbol": "", "name": q, "exchange": ""}


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    d = close.diff()
    gain = d.clip(lower=0).ewm(alpha=1/n, adjust=False).mean()
    loss = (-d.clip(upper=0)).ewm(alpha=1/n, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    return (100 - 100/(1+rs)).fillna(50)


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    prev = df["Close"].shift(1)
    tr = pd.concat([
        df["High"] - df["Low"],
        (df["High"] - prev).abs(),
        (df["Low"] - prev).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1/n, adjust=False).mean()


def stock_trend_channel(df: pd.DataFrame, window: int = 90) -> Dict:
    if df is None or len(df) < 30:
        return {"direction":"UNAVAILABLE","position":np.nan,"support":np.nan,"resistance":np.nan,"width_pct":np.nan,"rr":np.nan,"touches":0,"quality":"LOW","state":"NONE","slope_pct":np.nan}
    d = df.tail(min(window, len(df))).copy()
    for col in ("High", "Low", "Close"):
        d[col] = pd.to_numeric(d[col], errors="coerce")
    d = d.dropna(subset=["High", "Low", "Close"])
    if len(d) < 30:
        return {"direction":"UNAVAILABLE","position":np.nan,"support":np.nan,"resistance":np.nan,"width_pct":np.nan,"rr":np.nan,"touches":0,"quality":"LOW","state":"NONE","slope_pct":np.nan}
    x = np.arange(len(d), dtype=float)
    y = d["Close"].to_numpy(dtype=float)
    slope, intercept = np.polyfit(x, y, 1)
    centre = intercept + slope * x
    upper = centre + float(np.quantile(d["High"].to_numpy(dtype=float) - centre, 0.90))
    lower = centre + float(np.quantile(d["Low"].to_numpy(dtype=float) - centre, 0.10))
    support, resistance, price = float(lower[-1]), float(upper[-1]), float(y[-1])
    width = resistance - support
    if width <= 0 or price <= 0:
        return {"direction":"UNAVAILABLE","position":np.nan,"support":support,"resistance":resistance,"width_pct":np.nan,"rr":np.nan,"touches":0,"quality":"LOW","state":"NONE","slope_pct":np.nan}
    slope_pct = slope * max(len(d)-1,1) / max(float(centre[0]),1e-12) * 100
    direction = "RISING" if slope_pct >= 3 else "FALLING" if slope_pct <= -3 else "SIDEWAYS"
    position = (price-support)/width*100
    tol = max(width*0.08, price*0.005)
    touches = int((np.abs(d["Low"].to_numpy(dtype=float)-lower) <= tol).sum() + (np.abs(d["High"].to_numpy(dtype=float)-upper) <= tol).sum())
    ss_res = float(np.sum((y-centre)**2)); ss_tot = float(np.sum((y-np.mean(y))**2))
    r2 = max(0.0, 1-ss_res/ss_tot) if ss_tot > 0 else 0.0
    if direction == "SIDEWAYS":
        quality = "HIGH" if touches >= 6 else "MEDIUM" if touches >= 4 else "LOW"
    else:
        quality = "HIGH" if touches >= 6 and r2 >= 0.45 else "MEDIUM" if touches >= 4 and r2 >= 0.20 else "LOW"
    state = "ABOVE CHANNEL" if price > resistance+tol else "BELOW CHANNEL" if price < support-tol else "INSIDE"
    downside = max(price-support, price*0.001); upside = max(resistance-price,0.0)
    return {"direction":direction,"position":round(position,1),"support":support,"resistance":resistance,"width_pct":round(width/price*100,2),"rr":round(upside/downside,2),"touches":touches,"quality":quality,"state":state,"slope_pct":round(slope_pct,2),"lower_series":lower.tolist(),"upper_series":upper.tolist(),"start":len(df)-len(d)}


def latest_completed_daily_candle_signal(df: pd.DataFrame) -> Dict:
    if df is None or df.empty or len(df) < 2:
        return {
            "candle_pattern": "UNAVAILABLE",
            "candle_caution": False,
            "candle_detail": "Not enough daily candle history",
        }

    x = df.dropna(subset=["Open", "High", "Low", "Close"]).copy()
    if len(x) < 2:
        return {
            "candle_pattern": "UNAVAILABLE",
            "candle_caution": False,
            "candle_detail": "Not enough completed daily candles",
        }

    idx = pd.to_datetime(x.index, errors="coerce", utc=True)
    today_utc = pd.Timestamp.now(tz="UTC").normalize()
    if len(idx) and pd.notna(idx[-1]) and idx[-1].normalize() >= today_utc:
        candle = x.iloc[-2]
    else:
        candle = x.iloc[-1]

    o = safe(candle["Open"])
    h = safe(candle["High"])
    l = safe(candle["Low"])
    close = safe(candle["Close"])
    candle_range = max(h - l, 0.0)

    if candle_range <= 0:
        return {
            "candle_pattern": "OTHER",
            "candle_caution": False,
            "candle_detail": "Flat completed daily candle",
        }

    body = abs(close - o)
    upper_wick = h - max(o, close)
    lower_wick = min(o, close) - l
    red = close < o

    shooting_star = (
        red
        and body / candle_range <= 0.35
        and upper_wick >= max(body * 2.0, candle_range * 0.45)
        and lower_wick <= candle_range * 0.20
    )

    if shooting_star:
        return {
            "candle_pattern": "RED SHOOTING STAR",
            "candle_caution": True,
            "candle_detail": (
                f"Latest completed daily candle rejected higher prices: "
                f"upper wick {upper_wick / candle_range * 100:.0f}% of range; red close."
            ),
        }

    return {
        "candle_pattern": "OTHER",
        "candle_caution": False,
        "candle_detail": "No red shooting-star warning on latest completed daily candle",
    }


def technical_from_df(df: pd.DataFrame) -> Optional[Dict]:
    if df is None or len(df) < 80:
        return None

    df = df.dropna(subset=["Open", "High", "Low", "Close", "Volume"]).copy()
    if len(df) < 80:
        return None

    candle_signal = latest_completed_daily_candle_signal(df)
    channel = stock_trend_channel(df, 90)

    close = df["Close"]
    df["SMA20"] = close.rolling(20).mean()
    df["SMA50"] = close.rolling(50).mean()
    df["SMA200"] = close.rolling(200).mean()
    df["RSI"] = rsi(close)
    df["ATR"] = atr(df)

    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False).mean()
    df["MACD_H"] = macd - signal

    mid = close.rolling(20).mean()
    sd = close.rolling(20).std()
    df["BBM"] = mid
    df["BBU"] = mid + 2*sd
    df["BBL"] = mid - 2*sd
    df["BBW_PCT"] = (df["BBU"] - df["BBL"]) / df["BBM"].replace(0, np.nan) * 100

    price = safe(close.iloc[-1])
    sma20 = safe(df["SMA20"].iloc[-1])
    sma50 = safe(df["SMA50"].iloc[-1])
    sma200 = safe(df["SMA200"].iloc[-1])
    rsi_now = safe(df["RSI"].iloc[-1])
    atr_now = safe(df["ATR"].iloc[-1])
    bbu_now = safe(df["BBU"].iloc[-1])
    bbl_now = safe(df["BBL"].iloc[-1])
    bbm_now = safe(df["BBM"].iloc[-1])
    bbw_now = safe(df["BBW_PCT"].iloc[-1])
    bb_range = bbu_now - bbl_now if not np.isnan(bbu_now) and not np.isnan(bbl_now) else np.nan
    bb_position = (
        (price - bbl_now) / bb_range * 100
        if math.isfinite(bb_range) and bb_range > 0 else np.nan
    )
    bbw_hist = df["BBW_PCT"].dropna().tail(120)
    bbw_percentile = (
        float((bbw_hist <= bbw_now).mean() * 100)
        if len(bbw_hist) >= 20 and not np.isnan(bbw_now) else np.nan
    )
    bbw_recent = df["BBW_PCT"].dropna().tail(6)
    bb_expanding = (
        len(bbw_recent) >= 4
        and bbw_recent.iloc[-1] > bbw_recent.iloc[0] * 1.12
    )
    if math.isfinite(bbw_percentile) and bbw_percentile <= 20:
        bb_regime = "SQUEEZE"
    elif bb_expanding:
        bb_regime = "EXPANDING"
    else:
        bb_regime = "NORMAL"
    vol20 = safe(df["Volume"].iloc[-20:].mean(), 0)
    vol_now = safe(df["Volume"].iloc[-1], 0)

    if any(np.isnan(x) for x in [price, sma20, sma50, rsi_now, atr_now]) or price <= 0:
        return None

    macd_now = safe(df["MACD_H"].iloc[-1], 0)
    macd_prev = safe(df["MACD_H"].iloc[-4], 0)

    recent20 = df.iloc[-21:-1]
    recent50 = df.iloc[-51:-1]
    support20 = safe(recent20["Low"].min())
    support50 = safe(recent50["Low"].min())
    resistance20 = safe(recent20["High"].max())
    resistance50 = safe(recent50["High"].max())

    supports = [x for x in [sma20, sma50, support20, support50] if not np.isnan(x) and 0 < x < price]
    channel_support = safe(channel.get("support"))
    if (
        channel.get("quality") in ("HIGH", "MEDIUM")
        and channel.get("direction") != "FALLING"
        and not np.isnan(channel_support)
        and 0 < channel_support < price
    ):
        supports.append(channel_support)
    supports = sorted(set(round(x, 8) for x in supports), reverse=True)
    support1 = supports[0] if supports else max(0.01, price - atr_now)
    support2 = supports[1] if len(supports) > 1 else max(0.01, price - 2*atr_now)

    preferred_low = max(0.01, support1 - 0.35*atr_now)
    preferred_high = support1 + 0.35*atr_now
    strong_low = max(0.01, support2 - 0.35*atr_now)
    strong_high = support2 + 0.35*atr_now
    invalidation = max(0.01, support2 - atr_now)

    resistance = max(resistance20, resistance50)
    channel_resistance = safe(channel.get("resistance"))
    if (
        channel.get("quality") in ("HIGH", "MEDIUM")
        and not np.isnan(channel_resistance)
        and channel_resistance > price * 1.01
    ):
        resistance = min(resistance, channel_resistance)
    risk = max(price - invalidation, 0.01)
    two_r = price + 2*risk
    swing_target = min(resistance, two_r) if resistance > price * 1.03 else two_r
    upside = (swing_target / price - 1) * 100

    score = 0.0

    if price > sma20:
        score += 7
    if sma20 > sma50:
        score += 7
    if np.isnan(sma200) or sma50 > sma200:
        score += 6

    dist_support = (price / support1 - 1) * 100 if support1 > 0 else 99
    if 0 <= dist_support <= 3:
        score += 12
    elif dist_support <= 6:
        score += 8
    elif dist_support <= 10:
        score += 4

    if 42 <= rsi_now <= 58:
        score += 8
    elif 35 <= rsi_now <= 65:
        score += 5

    bbu = bbu_now
    bbl = bbl_now
    if not np.isnan(bbu) and not np.isnan(bbl) and bbl <= price <= bbu:
        score += 5

    if macd_now > macd_prev:
        score += 8
    if macd_now > 0:
        score += 4
    if rsi_now > safe(df["RSI"].iloc[-5], rsi_now):
        score += 3

    vr = vol_now / vol20 if vol20 else 1
    if 0.7 <= vr <= 1.8:
        score += 5
    if vr > 1.15 and close.iloc[-1] > close.iloc[-2]:
        score += 5

    rr = max((swing_target-price)/risk, 0)
    if rr >= 2.5:
        score += 12
    elif rr >= 2:
        score += 10
    elif rr >= 1.5:
        score += 6

    if upside >= 15:
        score += 8
    elif upside >= 10:
        score += 6
    elif upside >= 5:
        score += 3

    extension = (price/sma20 - 1)*100 if sma20 else 0
    if extension <= 3:
        score += 10
    elif extension <= 6:
        score += 6
    elif extension <= 10:
        score += 3

    avg_turnover = safe((df["Close"].iloc[-20:] * df["Volume"].iloc[-20:]).mean(), 0)

    return {
        "history": df,
        "price": price,
        "trade_score": round(min(score, 100), 1),
        "rsi": round(rsi_now, 1),
        "sma20": sma20,
        "sma50": sma50,
        "sma200": sma200,
        "bb_mid": bbm_now,
        "bb_upper": bbu_now,
        "bb_lower": bbl_now,
        "bb_width_pct": round(bbw_now, 2) if math.isfinite(bbw_now) else np.nan,
        "bb_width_percentile": round(bbw_percentile, 1) if math.isfinite(bbw_percentile) else np.nan,
        "bb_position_pct": round(float(bb_position), 1) if math.isfinite(bb_position) else np.nan,
        "bb_regime": bb_regime,
        "support1": support1,
        "support2": support2,
        "preferred_low": preferred_low,
        "preferred_high": preferred_high,
        "strong_low": strong_low,
        "strong_high": strong_high,
        "invalidation": invalidation,
        "swing_target": swing_target,
        "upside_pct": round(upside, 1),
        "rr": round(rr, 2),
        "volume_ratio": round(vr, 2),
        "avg_turnover": avg_turnover,
        "in_preferred_zone": bool(preferred_low <= price <= preferred_high),
        "in_strong_zone": bool(strong_low <= price <= strong_high),
        "candle_pattern": candle_signal["candle_pattern"],
        "candle_caution": bool(candle_signal["candle_caution"]),
        "candle_detail": candle_signal["candle_detail"],
        "channel_direction": channel.get("direction", "UNAVAILABLE"),
        "channel_position_pct": channel.get("position", np.nan),
        "channel_support": channel.get("support", np.nan),
        "channel_resistance": channel.get("resistance", np.nan),
        "channel_width_pct": channel.get("width_pct", np.nan),
        "channel_rr": channel.get("rr", np.nan),
        "channel_touches": channel.get("touches", 0),
        "channel_quality": channel.get("quality", "LOW"),
        "channel_state": channel.get("state", "NONE"),
        "channel_slope_pct": channel.get("slope_pct", np.nan),
    }


def technical_analysis(symbol: str) -> Optional[Dict]:
    try:
        t = yf.Ticker(symbol)
        df = t.history(period="1y", interval="1d", auto_adjust=False)
    except Exception:
        return None
    return technical_from_df(df)


def fundamental_analysis(symbol: str, price: float) -> Dict:
    t = yf.Ticker(symbol)
    try:
        info = t.info or {}
    except Exception:
        info = {}

    market_cap = safe(info.get("marketCap"))
    revenue_growth = pct(info.get("revenueGrowth"))
    earnings_growth = pct(info.get("earningsGrowth"))
    margin = pct(info.get("profitMargins"))
    debt_equity = safe(info.get("debtToEquity"))
    fcf = safe(info.get("freeCashflow"))
    div_yield = pct(info.get("dividendYield"))
    payout = pct(info.get("payoutRatio"))
    target = safe(info.get("targetMeanPrice"))
    analysts = safe(info.get("numberOfAnalystOpinions"), 0)
    target_upside = (target / price - 1) * 100 if not np.isnan(target) and price > 0 else np.nan

    score = 0.0

    if not np.isnan(market_cap):
        if market_cap >= 10e9:
            score += 10
        elif market_cap >= 3e9:
            score += 8
        elif market_cap >= 1e9:
            score += 5

    if revenue_growth is not None:
        if revenue_growth >= 20:
            score += 15
        elif revenue_growth >= 10:
            score += 12
        elif revenue_growth >= 5:
            score += 9
        elif revenue_growth > 0:
            score += 4

    if earnings_growth is not None:
        if earnings_growth >= 30:
            score += 15
        elif earnings_growth >= 20:
            score += 13
        elif earnings_growth >= 10:
            score += 9
        elif earnings_growth > 0:
            score += 5

    if margin is not None:
        if margin >= 20:
            score += 15
        elif margin >= 10:
            score += 12
        elif margin >= 5:
            score += 7
        elif margin > 0:
            score += 4

    if not np.isnan(debt_equity):
        if debt_equity <= 50:
            score += 10
        elif debt_equity <= 100:
            score += 8
        elif debt_equity <= 150:
            score += 6
        elif debt_equity <= 250:
            score += 3
    else:
        score += 4

    if not np.isnan(fcf):
        if fcf > 0:
            score += 10
    else:
        score += 3

    if not np.isnan(target_upside):
        if target_upside >= 25:
            score += 10
        elif target_upside >= 15:
            score += 8
        elif target_upside >= 10:
            score += 6
        elif target_upside > 0:
            score += 3

    if analysts >= 10:
        score += 5
    elif analysts >= 5:
        score += 3

    if div_yield is None or np.isnan(div_yield):
        score += 5
    elif 2 <= div_yield <= 6:
        score += 6
    elif 0 < div_yield < 2:
        score += 4

    if payout is None or np.isnan(payout):
        score += 3
    elif 0 <= payout <= 75:
        score += 4

    return {
        "hold_score": round(min(score, 100), 1),
        "name": info.get("longName") or info.get("shortName") or symbol,
        "currency": info.get("currency") or "",
        "market_cap": market_cap,
        "revenue_growth": revenue_growth,
        "earnings_growth": earnings_growth,
        "profit_margin": margin,
        "debt_equity": debt_equity,
        "free_cash_flow": fcf,
        "dividend_yield": div_yield,
        "payout_ratio": payout,
        "analyst_target": target,
        "analyst_count": int(analysts) if not np.isnan(analysts) else 0,
        "analyst_upside": target_upside,
        "sector": info.get("sector"),
        "industry": info.get("industry"),
    }


def classify(trade: float, hold: float) -> Scores:
    opp = round(0.55*trade + 0.45*hold, 1)
    if trade >= 75 and hold >= 65:
        c = "Swing-to-hold"
    elif trade >= 75:
        c = "Swing only"
    elif hold >= 80 and trade >= 55:
        c = "Core opportunity"
    elif trade >= 60 and hold >= 60:
        c = "Developing"
    else:
        c = "Watch / wait"
    return Scores(trade, hold, opp, c)


@st.cache_data(ttl=900, show_spinner=False)
def analyse_symbol(symbol: str) -> Optional[Dict]:
    symbol = symbol.strip().upper()
    tech = technical_analysis(symbol)
    if not tech:
        return None
    fund = fundamental_analysis(symbol, tech["price"])
    sc = classify(tech["trade_score"], fund["hold_score"])
    return {
        "symbol": symbol,
        **tech,
        **fund,
        "opportunity_score": sc.opportunity,
        "classification": sc.classification,
    }


def quick_trade_decision(res: Dict) -> Dict:
    """Translate the existing Quick Analyse scores into a simple UX decision."""
    classification = str(res.get("classification") or "Watch / wait")
    in_zone = bool(res.get("in_preferred_zone") or res.get("in_strong_zone"))
    trade_score = safe(res.get("trade_score"))
    hold_score = safe(res.get("hold_score"))
    rr = safe(res.get("rr"))
    upside = safe(res.get("upside_pct"))

    buy_classification = classification in {"Swing-to-hold", "Swing only"}
    if buy_classification and in_zone:
        return {
            "action": "BUY",
            "reason": "The setup is strong enough and the price is inside one of our entry zones.",
        }

    if buy_classification and not in_zone:
        return {
            "action": "WAIT",
            "reason": "The setup is strong enough, but the price is outside our entry zones. Do not chase it.",
        }
    if classification == "Core opportunity":
        return {
            "action": "WAIT",
            "reason": "The longer-term quality is stronger than the current technical setup. Wait for a better entry signal.",
        }
    if classification == "Developing":
        return {
            "action": "WAIT",
            "reason": "The setup is developing, but it has not reached the strength required for an entry yet.",
        }

    details = []
    if math.isfinite(trade_score):
        details.append(f"technical score {trade_score:.0f}/100")
    if math.isfinite(hold_score):
        details.append(f"hold quality {hold_score:.0f}/100")
    if math.isfinite(rr):
        details.append(f"risk/reward {rr:.2f}:1")
    if math.isfinite(upside):
        details.append(f"modelled upside {upside:.1f}%")
    suffix = " · ".join(details)
    return {
        "action": "WAIT",
        "reason": "The current trade setup is not strong enough yet." + (f" {suffix}." if suffix else ""),
    }


def quick_investment_decision(row: Dict | None) -> Dict:
    if not row:
        return {
            "action": "UNAVAILABLE",
            "reason": "Investment analysis could not be completed from the available market data.",
        }

    action = str(row.get("Action") or "UNAVAILABLE").upper()
    valuation_gate = str(row.get("Valuation gate") or "").upper()
    hard_gates = str(row.get("Hard gates") or "").upper()
    manual_review = str(row.get("Manual review required") or "").strip()

    if action == "BUY CANDIDATE":
        reason = (
            "The measurable long-term quality gates and valuation margin-of-safety gate pass. "
            "Complete the manual review before buying."
        )
    elif action == "PASS":
        failures = str(row.get("Hard-gate failures") or "").strip()
        reason = "One or more non-negotiable long-term quality gates failed."
        if failures:
            reason += f" {failures}"
    elif action == "WAIT" and valuation_gate != "PASS":
        reason = (
            "The price does not meet our valuation requirements. "
            "The margin of safety is below the level we require."
        )
    elif action == "WAIT" and hard_gates == "PASS":
        reason = (
            "The measurable quality gates pass, but more evidence or specialist review is required before buying."
        )
        if manual_review:
            reason += " Open the investment detail below to see the outstanding checks."
    else:
        reason = str(row.get("Decision reason") or "").strip() or "The investment case needs more evidence before a decision."

    return {"action": action, "reason": reason}


def render_decision_card(title: str, action: str, reason: str):
    action_upper = str(action or "UNAVAILABLE").upper()
    if action_upper in {"BUY", "BUY CANDIDATE"}:
        border, background, text_colour, icon = "#2e7d32", "#eef8f0", "#1b5e20", "🟢"
    elif action_upper in {"WAIT", "WATCH", "HOLD"}:
        border, background, text_colour, icon = "#d48a00", "#fff8e1", "#7a4d00", "🟠"
    elif action_upper in {"PASS", "AVOID", "SELL", "BLOCKED"}:
        border, background, text_colour, icon = "#c62828", "#fff0f0", "#8e1b1b", "🔴"
    else:
        border, background, text_colour, icon = "#6b7280", "#f5f5f5", "#374151", "⚪"

    st.markdown(
        f"""
        <div style="
            border: 2px solid {border};
            background: {background};
            border-radius: 14px;
            padding: 18px 20px;
            min-height: 190px;
            margin-bottom: 8px;
        ">
            <div style="font-size: 0.95rem; font-weight: 700; opacity: 0.78; margin-bottom: 4px;">
                {title}
            </div>
            <div style="
                font-size: 2.7rem;
                line-height: 1.05;
                font-weight: 900;
                color: {text_colour};
                margin: 6px 0 12px 0;
                letter-spacing: -0.03em;
            ">
                {icon} {action_upper}
            </div>
            <div style="font-size: 1.02rem; line-height: 1.45; color: #313131;">
                {reason}
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    render_decision_guidance(action_upper, reason)


def normalise_us_symbol(s: str) -> str:
    return str(s).strip().replace(".", "-")


def normalise_lse_symbol(s: str) -> str:
    return str(s).strip().replace(".", "-") + ".L"


class _SimpleTableParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tables = []
        self._table = None
        self._row = None
        self._cell = None
        self._in_table = False
        self._in_row = False
        self._in_cell = False

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag == "table":
            self._in_table = True
            self._table = []
        elif tag == "tr" and self._in_table:
            self._in_row = True
            self._row = []
        elif tag in ("td", "th") and self._in_row:
            self._in_cell = True
            self._cell = []

    def handle_data(self, data):
        if self._in_cell and self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in ("td", "th") and self._in_cell:
            txt = " ".join("".join(self._cell).split())
            self._row.append(txt)
            self._cell = None
            self._in_cell = False
        elif tag == "tr" and self._in_row:
            if self._row:
                self._table.append(self._row)
            self._row = None
            self._in_row = False
        elif tag == "table" and self._in_table:
            if self._table:
                self.tables.append(self._table)
            self._table = None
            self._in_table = False


@st.cache_data(ttl=86400, show_spinner=False)
def wikipedia_symbols(url: str, ticker_names: tuple[str, ...], suffix: str = "", min_count: int = 10, replace_dot: bool = True) -> List[str]:
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    parser = _SimpleTableParser()
    parser.feed(r.text)

    wanted = [x.lower() for x in ticker_names]
    for table in parser.tables:
        if not table:
            continue
        header = [str(x).strip() for x in table[0]]
        lower = [x.lower() for x in header]
        idx = None
        for name in wanted:
            if name in lower:
                idx = lower.index(name)
                break
        if idx is None:
            continue

        out = []
        for row in table[1:]:
            if idx >= len(row):
                continue
            s = str(row[idx]).strip()
            if not s:
                continue
            if replace_dot:
                s = s.replace(".", "-")
            if suffix and not s.endswith(suffix):
                s += suffix
            out.append(s)

        if len(out) >= min_count:
            return sorted(set(out))
    return []


@st.cache_data(ttl=86400, show_spinner=False)
def us_all_listed() -> List[str]:
    urls = [
        ("https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt", "Symbol"),
        ("https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt", "ACT Symbol"),
    ]
    all_syms = []
    for url, symbol_col in urls:
        r = requests.get(url, headers=HEADERS, timeout=30)
        r.raise_for_status()
        df = pd.read_csv(io.StringIO(r.text), sep="|")
        if symbol_col not in df.columns:
            continue
        if "ETF" in df.columns:
            df = df[df["ETF"].fillna("N").eq("N")]
        if "Test Issue" in df.columns:
            df = df[df["Test Issue"].fillna("N").eq("N")]
        name_col = "Security Name" if "Security Name" in df.columns else None
        if name_col:
            bad = df[name_col].fillna("").str.contains(
                "Warrant|Right|Units|Preferred|Depositary Shares|Notes due|Bond|Debenture",
                case=False,
                regex=True,
            )
            df = df[~bad]
        vals = df[symbol_col].dropna().astype(str)
        vals = vals[~vals.str.contains("File Creation Time", case=False, regex=False)]
        all_syms.extend(normalise_us_symbol(x) for x in vals)
    return sorted(set(s for s in all_syms if s and "$" not in s and len(s) <= 12))


def _filter_us_company_symbols(df: pd.DataFrame, symbol_col: str) -> List[str]:
    """Return operating-company symbols and remove funds/special securities."""
    if symbol_col not in df.columns:
        return []
    if "ETF" in df.columns:
        df = df[df["ETF"].fillna("N").eq("N")]
    if "Test Issue" in df.columns:
        df = df[df["Test Issue"].fillna("N").eq("N")]
    if "Security Name" in df.columns:
        bad = df["Security Name"].fillna("").str.contains(
            "Warrant|Right|Units|Preferred|Depositary Shares|Notes due|Bond|Debenture|ETF|Fund",
            case=False,
            regex=True,
        )
        df = df[~bad]
    vals = df[symbol_col].dropna().astype(str)
    vals = vals[~vals.str.contains("File Creation Time", case=False, regex=False)]
    return sorted(set(
        symbol for symbol in (normalise_us_symbol(x) for x in vals)
        if symbol and "$" not in symbol and len(symbol) <= 12
    ))


@st.cache_data(ttl=86400, show_spinner=False)
def us_exchange_listed(exchange: str) -> List[str]:
    """Load NASDAQ or NYSE company symbols from Nasdaq Trader's daily files."""
    if exchange == "nasdaq":
        url = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
        symbol_col = "Symbol"
    elif exchange == "nyse":
        url = "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"
        symbol_col = "ACT Symbol"
    else:
        return []

    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    df = pd.read_csv(io.StringIO(r.text), sep="|")
    if exchange == "nyse" and "Exchange" in df.columns:
        # Nasdaq Trader code N is the New York Stock Exchange. Exclude NYSE
        # American, NYSE Arca and regional exchanges from this universe.
        df = df[df["Exchange"].fillna("").eq("N")]
    return _filter_us_company_symbols(df, symbol_col)


TRADINGVIEW_UNIVERSES = {
    "otc": ("america", "OTC", ""),
    "xetra": ("germany", "XETR", ".DE"),
    # Gettex is the electronic market of Börse München; Yahoo uses .MU.
    "gettex": ("germany", "GETTEX", ".MU"),
    "tsx": ("canada", "TSX", ".TO"),
    "euronext_paris": ("france", "EURONEXT", ".PA"),
    "six": ("switzerland", "SIX", ".SW"),
    "madrid": ("spain", "BME", ".MC"),
    "euronext_brussels": ("belgium", "EURONEXT", ".BR"),
    "vienna": ("austria", "VIE", ".VI"),
    "euronext_amsterdam": ("netherlands", "EURONEXT", ".AS"),
    "euronext_lisbon": ("portugal", "EURONEXT", ".LS"),
}


def yahoo_exchange_symbol(symbol: str, suffix: str) -> str:
    """Convert an exchange ticker into the format accepted by Yahoo Finance."""
    symbol = str(symbol).strip().upper()
    if not symbol or any(ch in symbol for ch in ("/", " ", ":")):
        return ""
    if not suffix:
        return normalise_us_symbol(symbol)
    symbol = symbol.replace(".", "-")
    return symbol if symbol.endswith(suffix) else f"{symbol}{suffix}"


def tradingview_company_rows(market: str, exchange: str, include_indexes: bool = False) -> List[dict]:
    """Return all common-stock rows currently published for one exchange."""
    columns = ["name", "description", "country", "currency", "market_cap_basic"]
    if include_indexes:
        columns.append("indexes")
    payload = {
        "filter": [
            {"left": "exchange", "operation": "equal", "right": exchange},
            {"left": "type", "operation": "equal", "right": "stock"},
            {"left": "subtype", "operation": "equal", "right": "common"},
        ],
        "options": {"lang": "en"},
        "markets": [market],
        "symbols": {"query": {"types": []}, "tickers": []},
        "columns": columns,
        "sort": {"sortBy": "name", "sortOrder": "asc"},
        "range": [0, 50000],
    }
    response = requests.post(
        f"https://scanner.tradingview.com/{market}/scan",
        json=payload,
        headers=HEADERS,
        timeout=45,
    )
    response.raise_for_status()
    rows = []
    for item in response.json().get("data", []):
        values = item.get("d", [])
        if not values:
            continue
        row = dict(zip(columns, values))
        row["provider_symbol"] = item.get("s", "")
        rows.append(row)
    return rows


@st.cache_data(ttl=86400, show_spinner=False)
def london_exchange_data() -> Dict[str, dict]:
    """Split London shares and retain their exchange-supplied market caps."""
    rows = tradingview_company_rows("uk", "LSE", include_indexes=True)
    main, aim = [], []
    market_caps = {"lse": {}, "lse_aim": {}}
    for row in rows:
        # Keep sterling London listings and discard the exchange's international
        # quote lines, which are duplicate listings from other home markets.
        if row.get("currency") not in {"GBX", "GBP"}:
            continue
        symbol = yahoo_exchange_symbol(row.get("name", ""), ".L")
        if not symbol:
            continue
        indexes = row.get("indexes") or []
        is_aim = any("AIM" in str(index.get("name", "")).upper() for index in indexes if isinstance(index, dict))
        kind = "lse_aim" if is_aim else "lse"
        (aim if is_aim else main).append(symbol)
        market_cap = safe(row.get("market_cap_basic"))
        if not np.isnan(market_cap) and market_cap > 0:
            market_caps[kind][symbol] = market_cap
    return {
        "lse": {"symbols": sorted(set(main)), "market_caps": market_caps["lse"]},
        "lse_aim": {"symbols": sorted(set(aim)), "market_caps": market_caps["lse_aim"]},
    }


@st.cache_data(ttl=86400, show_spinner=False)
def tradingview_exchange_data(kind: str) -> dict:
    market, exchange, suffix = TRADINGVIEW_UNIVERSES[kind]
    rows = tradingview_company_rows(market, exchange)
    symbols = []
    market_caps = {}
    for row in rows:
        symbol = yahoo_exchange_symbol(row.get("name", ""), suffix)
        if not symbol:
            continue
        symbols.append(symbol)
        market_cap = safe(row.get("market_cap_basic"))
        if not np.isnan(market_cap) and market_cap > 0:
            market_caps[symbol] = market_cap
    return {"symbols": sorted(set(symbols)), "market_caps": market_caps}


@st.cache_data(ttl=86400, show_spinner=False)
def us_exchange_market_caps(kind: str) -> Dict[str, float]:
    rows = tradingview_company_rows("america", kind.upper())
    market_caps = {}
    for row in rows:
        symbol = yahoo_exchange_symbol(row.get("name", ""), "")
        market_cap = safe(row.get("market_cap_basic"))
        if symbol and not np.isnan(market_cap) and market_cap > 0:
            market_caps[symbol] = market_cap
    return market_caps


@st.cache_data(ttl=86400, show_spinner=False)
def get_universe(kind: str) -> List[str]:
    if kind in {"nyse", "nasdaq"}:
        return us_exchange_listed(kind)
    if kind in {"lse", "lse_aim"}:
        return london_exchange_data()[kind]["symbols"]
    if kind in TRADINGVIEW_UNIVERSES:
        return tradingview_exchange_data(kind)["symbols"]
    return []


def get_universe_market_caps(kind: str) -> Dict[str, float]:
    """Return exchange-directory market caps keyed by Yahoo-formatted symbol."""
    if kind in {"nyse", "nasdaq"}:
        return us_exchange_market_caps(kind)
    if kind in {"lse", "lse_aim"}:
        return london_exchange_data()[kind]["market_caps"]
    if kind in TRADINGVIEW_UNIVERSES:
        return tradingview_exchange_data(kind)["market_caps"]
    return {}


def extract_ticker_frame(batch: pd.DataFrame, symbol: str) -> Optional[pd.DataFrame]:
    if batch is None or batch.empty:
        return None
    if isinstance(batch.columns, pd.MultiIndex):
        level0 = set(str(x) for x in batch.columns.get_level_values(0))
        level1 = set(str(x) for x in batch.columns.get_level_values(1))
        if symbol in level0:
            d = batch[symbol].copy()
        elif symbol in level1:
            d = batch.xs(symbol, level=1, axis=1).copy()
        else:
            return None
    else:
        d = batch.copy()
    d = d.rename(columns={str(c): str(c).title() for c in d.columns})
    needed = {"Open", "High", "Low", "Close", "Volume"}
    if not needed.issubset(set(d.columns)):
        return None
    return d


@st.cache_data(ttl=1800, show_spinner=False)
def technical_market_scan(symbols_tuple: tuple[str, ...], max_symbols: int, min_turnover: float) -> pd.DataFrame:
    universe_symbols = list(symbols_tuple)
    if max_symbols > 0 and max_symbols < len(universe_symbols):
        idx = np.linspace(0, len(universe_symbols) - 1, max_symbols, dtype=int)
        symbols = [universe_symbols[i] for i in idx]
    else:
        symbols = universe_symbols
    rows = []
    chunk_size = 80

    for start in range(0, len(symbols), chunk_size):
        chunk = symbols[start:start + chunk_size]
        try:
            data = yf.download(
                tickers=chunk,
                period="1y",
                interval="1d",
                group_by="ticker",
                auto_adjust=False,
                threads=True,
                progress=False,
            )
        except Exception:
            continue

        for sym in chunk:
            try:
                d = extract_ticker_frame(data, sym)
                tech = technical_from_df(d)
                if not tech:
                    continue
                if tech["avg_turnover"] < min_turnover:
                    continue
                rows.append({
                    "Ticker": sym,
                    "Trade": tech["trade_score"],
                    "Price": tech["price"],
                    "RSI": tech["rsi"],
                    "R:R": tech["rr"],
                    "Upside %": tech["upside_pct"],
                    "Avg turnover": tech["avg_turnover"],
                    "Preferred low": tech["preferred_low"],
                    "Preferred high": tech["preferred_high"],
                    "Strong low": tech["strong_low"],
                    "Strong high": tech["strong_high"],
                    "Invalidation": tech["invalidation"],
                    "Target": tech["swing_target"],
                    "Preferred now": tech["in_preferred_zone"],
                    "Strong now": tech["in_strong_zone"],
                    "Candle caution": "CAUTION" if tech.get("candle_caution") else "CLEAR",
                    "Last candle": tech.get("candle_pattern", "UNAVAILABLE"),
                    "Candle detail": tech.get("candle_detail", ""),
                    "Channel": tech.get("channel_direction", "UNAVAILABLE"),
                    "Channel pos %": tech.get("channel_position_pct", np.nan),
                    "Channel support": tech.get("channel_support", np.nan),
                    "Channel resistance": tech.get("channel_resistance", np.nan),
                    "Channel R:R": tech.get("channel_rr", np.nan),
                    "Channel quality": tech.get("channel_quality", "LOW"),
                    "Channel state": tech.get("channel_state", "NONE"),
                    "BB regime": tech.get("bb_regime", "UNAVAILABLE"),
                    "BB width %": tech.get("bb_width_pct", np.nan),
                    "BB width percentile": tech.get("bb_width_percentile", np.nan),
                    "BB position %": tech.get("bb_position_pct", np.nan),
                })
            except Exception:
                continue

    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values(["Trade", "Upside %"], ascending=[False, False]).reset_index(drop=True)


TRADE_MARKET_CONTEXT = {
    "nasdaq": {"benchmark": "^GSPC", "currency": "USD", "price_scale": 1.0},
    "nyse": {"benchmark": "^GSPC", "currency": "USD", "price_scale": 1.0},
    "otc": {"benchmark": "^GSPC", "currency": "USD", "price_scale": 1.0},
    "lse": {"benchmark": "^FTSE", "currency": "GBP", "price_scale": 0.01},
    "lse_aim": {"benchmark": "^FTSE", "currency": "GBP", "price_scale": 0.01},
    "xetra": {"benchmark": "^GDAXI", "currency": "EUR", "price_scale": 1.0},
    "gettex": {"benchmark": "^GDAXI", "currency": "EUR", "price_scale": 1.0},
    "tsx": {"benchmark": "^GSPTSE", "currency": "CAD", "price_scale": 1.0},
    "euronext_paris": {"benchmark": "^FCHI", "currency": "EUR", "price_scale": 1.0},
    "six": {"benchmark": "^SSMI", "currency": "CHF", "price_scale": 1.0},
    "madrid": {"benchmark": "^IBEX", "currency": "EUR", "price_scale": 1.0},
    "euronext_brussels": {"benchmark": "^BFX", "currency": "EUR", "price_scale": 1.0},
    "vienna": {"benchmark": "^ATX", "currency": "EUR", "price_scale": 1.0},
    "euronext_amsterdam": {"benchmark": "^AEX", "currency": "EUR", "price_scale": 1.0},
    "euronext_lisbon": {"benchmark": "PSI20.LS", "currency": "EUR", "price_scale": 1.0},
}


@st.cache_data(ttl=1800, show_spinner=False)
def trade_fx_to_gbp() -> Dict[str, float]:
    """Return quote-currency multipliers for conversion into pounds."""
    output = {"GBP": 1.0, "GBX": 0.01, "GBPENCE": 0.01}
    tickers = {"USD": "GBPUSD=X", "EUR": "GBPEUR=X", "CAD": "GBPCAD=X", "CHF": "GBPCHF=X"}
    try:
        rates = yf.download(
            list(tickers.values()), period="5d", interval="1d", auto_adjust=False,
            group_by="ticker", progress=False, threads=True,
        )
        for currency, ticker in tickers.items():
            frame = extract_ticker_frame(rates, ticker)
            if frame is not None and not frame.empty:
                quote_per_gbp = safe(frame["Close"].dropna().iloc[-1])
                if quote_per_gbp > 0:
                    output[currency] = 1.0 / quote_per_gbp
    except Exception:
        pass
    return output


@st.cache_data(ttl=1800, show_spinner=False)
def trade_benchmark_frame(symbol: str) -> pd.DataFrame:
    try:
        data = yf.download(symbol, period="3y", interval="1d", auto_adjust=False, progress=False)
        frame = extract_ticker_frame(data, symbol)
        return frame if frame is not None else pd.DataFrame()
    except Exception:
        return pd.DataFrame()


def _is_yahoo_rate_limit_error(exc: Exception) -> bool:
    """Recognise Yahoo/yfinance throttling without depending on one yfinance version."""
    name = type(exc).__name__.lower()
    message = str(exc).lower()
    return (
        "yfratelimiterror" in name
        or "too many requests" in message
        or "rate limit" in message
        or "http 429" in message
    )


@st.cache_data(ttl=21600, show_spinner=False)
def trade_fundamental_snapshot(symbol: str, model_version: str):
    """Cache Yahoo fallback data by Trade-rule build so stale snapshots cannot survive a rules update."""
    _ = model_version
    return build_fundamental_snapshot(symbol, yf.Ticker(symbol))


TRADE_TV_SOURCE = {
    "nasdaq": ("america", "NASDAQ"),
    "nyse": ("america", "NYSE"),
    "lse": ("uk", "LSE"),
    "lse_aim": ("uk", "LSE"),
    **{
        kind: (market, exchange)
        for kind, (market, exchange, _suffix) in TRADINGVIEW_UNIVERSES.items()
    },
}

TRADE_TV_COLUMNS = [
    "name",
    "description",
    "sector",
    "industry",
    "fundamental_currency_code",
    "market_cap_basic",
    "return_of_invested_capital_percent_ttm",
    "return_on_equity",
    "operating_margin_ttm",
    "free_cash_flow_ttm",
    "net_debt",
    "current_ratio_fq",
    "total_revenue_ttm",
    "total_revenue_fy_h",
    "net_income_fy_h",
    "free_cash_flow_fy_h",
    "earnings_per_share_basic_fy_h",
    "ebitda_fy_h",
    "earnings_release_next_calendar_date",
    "price_earnings_current",
    "price_sales_current",
]


def _tv_symbol(symbol: str, exchange: str) -> str:
    """Translate Yahoo-style tickers into TradingView's exchange:symbol notation."""
    value = str(symbol).strip().upper()
    suffixes = (".L", ".TO", ".PA", ".DE", ".MU", ".SW", ".MC", ".BR", ".VI", ".AS", ".LS")
    for suffix in suffixes:
        if value.endswith(suffix):
            value = value[:-len(suffix)]
            break
    # US class shares commonly use '-' in Yahoo and '.' in TradingView.
    if exchange in {"NASDAQ", "NYSE"}:
        value = value.replace("-", ".")
    return f"{exchange}:{value}"


def _tv_numeric_history(value) -> list[float]:
    """Flatten TradingView num_slice fields while preserving their reported order."""
    output: list[float] = []

    def visit(item):
        if item is None:
            return
        if isinstance(item, bool):
            return
        if isinstance(item, (int, float, np.number)):
            number = safe(item)
            if math.isfinite(number):
                output.append(float(number))
            return
        if isinstance(item, dict):
            # num_slice payloads can be nested; values are the useful portion.
            for child in item.values():
                visit(child)
            return
        if isinstance(item, (list, tuple)):
            for child in item:
                visit(child)

    visit(value)
    return output


def _tv_growth(values: list[float]) -> float:
    if len(values) < 2:
        return np.nan
    latest, previous = values[0], values[1]
    if not math.isfinite(latest) or not math.isfinite(previous) or previous <= 0:
        return np.nan
    return latest / previous - 1.0


def _tv_profit_growth(values: list[float]) -> float:
    """Measure deterioration without treating an improving loss as positive growth."""
    if len(values) < 2:
        return np.nan
    latest, previous = values[0], values[1]
    if not math.isfinite(latest) or not math.isfinite(previous):
        return np.nan
    if previous > 0:
        return latest / previous - 1.0
    if latest >= previous:
        return 0.0
    denominator = max(abs(previous), 1e-12)
    return (latest - previous) / denominator


def _tv_timestamp(value):
    if value is None:
        return None
    try:
        if isinstance(value, (int, float, np.number)) and math.isfinite(float(value)):
            raw = float(value)
            unit = "ms" if abs(raw) > 10_000_000_000 else "s"
            ts = pd.to_datetime(raw, unit=unit, utc=True, errors="coerce")
        else:
            ts = pd.to_datetime(value, utc=True, errors="coerce")
        if pd.isna(ts):
            return None
        return pd.Timestamp(ts).tz_localize(None)
    except Exception:
        return None


def _tv_snapshot(symbol: str, row: Dict) -> FundamentalSnapshot:
    revenue_history = _tv_numeric_history(row.get("total_revenue_fy_h"))
    income_history = _tv_numeric_history(row.get("net_income_fy_h"))
    fcf_history = _tv_numeric_history(row.get("free_cash_flow_fy_h"))
    eps_history = _tv_numeric_history(row.get("earnings_per_share_basic_fy_h"))
    ebitda_history = _tv_numeric_history(row.get("ebitda_fy_h"))

    fcf_ttm = safe(row.get("free_cash_flow_ttm"))
    revenue_ttm = safe(row.get("total_revenue_ttm"))
    net_debt = safe(row.get("net_debt"))
    net_debt_data_available = math.isfinite(net_debt) and math.isfinite(fcf_ttm)
    fcf_margin = fcf_ttm / revenue_ttm if revenue_ttm > 0 and math.isfinite(fcf_ttm) else np.nan
    net_debt_to_fcf = max(0.0, net_debt) / fcf_ttm if math.isfinite(net_debt) and fcf_ttm > 0 else np.nan

    implied_shares = []
    for income, eps in zip(income_history, eps_history):
        if math.isfinite(income) and math.isfinite(eps) and abs(eps) > 1e-12 and income * eps > 0:
            implied = income / eps
            if math.isfinite(implied) and implied > 0:
                implied_shares.append(implied)
    share_change = _tv_growth(implied_shares)

    revenue_growth = _tv_growth(revenue_history)
    earnings_growth = _tv_profit_growth(income_history)
    # TradingView exposes multi-year EBITDA history but not operating-income history.
    # EBITDA trend is used only as the operating-profit trend proxy for the existing
    # deterioration hard gate; the source label makes that fallback explicit.
    operating_growth = _tv_profit_growth(ebitda_history)

    missing: list[str] = []
    required = {
        "three annual FCF periods": len(fcf_history) >= 3,
        "trailing FCF": math.isfinite(fcf_ttm),
        "net debt and FCF": net_debt_data_available,
        "two share-count periods": len(implied_shares) >= 2 and math.isfinite(share_change),
        "revenue trend": math.isfinite(revenue_growth),
        "earnings trend": math.isfinite(earnings_growth),
        "operating-profit trend": math.isfinite(operating_growth),
    }
    for label, available in required.items():
        if not available:
            missing.append(label)

    earnings_date = _tv_timestamp(row.get("earnings_release_next_calendar_date"))
    raw_sector = str(row.get("sector") or "UNAVAILABLE")
    normalized_sector = "Financial Services" if raw_sector.strip().lower() == "finance" else raw_sector
    return FundamentalSnapshot(
        symbol=symbol,
        company=str(row.get("description") or row.get("name") or symbol),
        sector=normalized_sector,
        industry=str(row.get("industry") or "UNAVAILABLE"),
        currency=str(row.get("fundamental_currency_code") or "UNAVAILABLE").upper(),
        market_cap=safe(row.get("market_cap_basic")),
        roic=safe(row.get("return_of_invested_capital_percent_ttm")) / 100.0,
        roe=safe(row.get("return_on_equity")) / 100.0,
        operating_margin=safe(row.get("operating_margin_ttm")) / 100.0,
        fcf_margin=fcf_margin,
        annual_fcf=fcf_history[:10],
        annual_net_income=income_history[:3],
        net_debt_to_fcf=net_debt_to_fcf,
        interest_coverage=np.nan,
        no_interest_expense=False,
        current_ratio=safe(row.get("current_ratio_fq")),
        revenue_growth=revenue_growth,
        earnings_growth=earnings_growth,
        operating_growth=operating_growth,
        growth_source="TRADINGVIEW FY / EBITDA OPERATING-PROFIT PROXY",
        share_change=share_change,
        distribution_ratio=np.nan,
        earnings_date=earnings_date,
        earnings_source="TRADINGVIEW CALENDAR" if earnings_date is not None else "UNVERIFIED",
        trailing_pe=safe(row.get("price_earnings_current")),
        price_sales=safe(row.get("price_sales_current")),
        missing_hard_inputs=missing,
        trailing_fcf=fcf_ttm,
    )


@st.cache_data(ttl=21600, show_spinner=False)
def trade_tradingview_snapshots(
    symbols_tuple: tuple[str, ...], universe_kind: str, model_version: str
) -> Dict[str, FundamentalSnapshot]:
    """Fetch Trade fundamentals in one request; rule build is part of the cache key."""
    _ = model_version
    source = TRADE_TV_SOURCE.get(universe_kind)
    if not source or not symbols_tuple:
        return {}

    market, exchange = source
    tv_to_yahoo = {_tv_symbol(symbol, exchange): symbol for symbol in symbols_tuple}
    payload = {
        "symbols": {"tickers": list(tv_to_yahoo.keys()), "query": {"types": []}},
        "columns": TRADE_TV_COLUMNS,
        "options": {"lang": "en"},
        "range": [0, max(len(tv_to_yahoo), 1)],
    }
    response = requests.post(
        f"https://scanner.tradingview.com/{market}/scan",
        json=payload,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 StockOpportunityScreener/1.0",
            "Origin": "https://www.tradingview.com",
            "Referer": "https://www.tradingview.com/",
        },
        timeout=20,
    )
    response.raise_for_status()
    body = response.json()
    output: Dict[str, FundamentalSnapshot] = {}
    for item in body.get("data", []):
        tv_name = str(item.get("s") or "").upper()
        symbol = tv_to_yahoo.get(tv_name)
        values = item.get("d") or []
        if symbol is None or not isinstance(values, list):
            continue
        row = dict(zip(TRADE_TV_COLUMNS, values))
        output[symbol] = _tv_snapshot(symbol, row)
    return output


def trade_snapshot_to_record(snapshot: FundamentalSnapshot) -> Dict:
    """Serialize one Trade fundamental snapshot for the nightly prepared cache."""
    earnings_date = ""
    if snapshot.earnings_date is not None and not pd.isna(snapshot.earnings_date):
        earnings_date = pd.Timestamp(snapshot.earnings_date).isoformat()
    return {
        "Ticker": snapshot.symbol,
        "Company": snapshot.company,
        "Sector": snapshot.sector,
        "Industry": snapshot.industry,
        "Currency": snapshot.currency,
        "Market cap": snapshot.market_cap,
        "ROIC": snapshot.roic,
        "ROE": snapshot.roe,
        "Operating margin": snapshot.operating_margin,
        "FCF margin": snapshot.fcf_margin,
        "Annual FCF": json.dumps(snapshot.annual_fcf),
        "Annual net income": json.dumps(snapshot.annual_net_income),
        "Net debt / FCF": snapshot.net_debt_to_fcf,
        "Interest coverage": snapshot.interest_coverage,
        "No interest expense": bool(snapshot.no_interest_expense),
        "Current ratio": snapshot.current_ratio,
        "Revenue growth": snapshot.revenue_growth,
        "Earnings growth": snapshot.earnings_growth,
        "Operating growth": snapshot.operating_growth,
        "Growth source": snapshot.growth_source,
        "Share change": snapshot.share_change,
        "Distribution ratio": snapshot.distribution_ratio,
        "Earnings date": earnings_date,
        "Earnings source": snapshot.earnings_source,
        "Trailing PE": snapshot.trailing_pe,
        "Price sales": snapshot.price_sales,
        "Missing hard inputs": json.dumps(snapshot.missing_hard_inputs),
        "Trailing FCF": snapshot.trailing_fcf,
    }


def _prepared_json_list(value) -> list:
    if isinstance(value, list):
        return value
    if value is None:
        return []
    try:
        if pd.isna(value):
            return []
    except Exception:
        pass
    try:
        parsed = json.loads(str(value))
        return parsed if isinstance(parsed, list) else []
    except Exception:
        return []


def _prepared_text(value, default: str) -> str:
    if value is None:
        return default
    try:
        if pd.isna(value):
            return default
    except Exception:
        pass
    text_value = str(value).strip()
    return text_value or default


def trade_snapshot_from_record(row: Dict) -> FundamentalSnapshot:
    """Rehydrate a nightly Trade fundamental snapshot."""
    symbol = _prepared_text(row.get("Ticker"), "")
    earnings_raw = row.get("Earnings date")
    earnings_date = None
    try:
        parsed = pd.to_datetime(earnings_raw, errors="coerce")
        if not pd.isna(parsed):
            earnings_date = pd.Timestamp(parsed)
    except Exception:
        earnings_date = None

    no_interest_raw = row.get("No interest expense")
    no_interest = str(no_interest_raw).strip().lower() in {"true", "1", "yes"}

    return FundamentalSnapshot(
        symbol=symbol,
        company=_prepared_text(row.get("Company"), symbol),
        sector=_prepared_text(row.get("Sector"), "UNAVAILABLE"),
        industry=_prepared_text(row.get("Industry"), "UNAVAILABLE"),
        currency=_prepared_text(row.get("Currency"), "UNAVAILABLE").upper(),
        market_cap=safe(row.get("Market cap")),
        roic=safe(row.get("ROIC")),
        roe=safe(row.get("ROE")),
        operating_margin=safe(row.get("Operating margin")),
        fcf_margin=safe(row.get("FCF margin")),
        annual_fcf=[safe(value) for value in _prepared_json_list(row.get("Annual FCF")) if math.isfinite(safe(value))],
        annual_net_income=[
            safe(value) for value in _prepared_json_list(row.get("Annual net income"))
            if math.isfinite(safe(value))
        ],
        net_debt_to_fcf=safe(row.get("Net debt / FCF")),
        interest_coverage=safe(row.get("Interest coverage")),
        no_interest_expense=no_interest,
        current_ratio=safe(row.get("Current ratio")),
        revenue_growth=safe(row.get("Revenue growth")),
        earnings_growth=safe(row.get("Earnings growth")),
        operating_growth=safe(row.get("Operating growth")),
        growth_source=_prepared_text(row.get("Growth source"), "PREPARED"),
        share_change=safe(row.get("Share change")),
        distribution_ratio=safe(row.get("Distribution ratio")),
        earnings_date=earnings_date,
        earnings_source=_prepared_text(row.get("Earnings source"), "UNVERIFIED"),
        trailing_pe=safe(row.get("Trailing PE")),
        price_sales=safe(row.get("Price sales")),
        missing_hard_inputs=[
            str(value) for value in _prepared_json_list(row.get("Missing hard inputs"))
        ],
        trailing_fcf=safe(row.get("Trailing FCF")),
    )


def prepared_trade_path(kind: str) -> Path:
    return TRADE_PREPARED_DIR / f"{kind}.csv.gz"


def prepared_trade_metadata(kind: str) -> dict:
    manifest_path = TRADE_PREPARED_DIR / "manifest.json"
    if not manifest_path.exists():
        return {}
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        return manifest.get("exchanges", {}).get(kind, {})
    except Exception:
        return {}


@st.cache_data(ttl=900, show_spinner=False)
def load_prepared_trade_snapshots(
    kind: str, modified_ns: int, model_version: str
) -> Dict[str, FundamentalSnapshot]:
    """Load the nightly Trade fundamentals; file timestamp and rule build invalidate cache."""
    _ = (modified_ns, model_version)
    path = prepared_trade_path(kind)
    if not path.exists():
        return {}
    try:
        frame = pd.read_csv(path, compression="gzip")
    except Exception:
        return {}
    output: Dict[str, FundamentalSnapshot] = {}
    for row in frame.to_dict(orient="records"):
        try:
            snapshot = trade_snapshot_from_record(row)
            if snapshot.symbol:
                output[snapshot.symbol] = snapshot
        except Exception:
            continue
    return output


def approved_trade_market_scan(
    symbols_tuple: tuple[str, ...], max_symbols: int, universe_kind: str,
    model_version: str, _progress_callback=None,
) -> pd.DataFrame:
    # Intentionally not cached: this function drives a live Streamlit progress
    # callback and refreshes current technical data on every Trade Search run.
    # Caching a function that invokes a UI closure can raise CacheReplayClosureError.
    # Keep the model version in the call signature so scheduled and interactive
    # Trade paths remain tied to the same rulebook build.
    _ = model_version
    universe_symbols = list(symbols_tuple)
    if max_symbols > 0 and max_symbols < len(universe_symbols):
        indexes = np.linspace(0, len(universe_symbols) - 1, max_symbols, dtype=int)
        symbols = [universe_symbols[index] for index in indexes]
    else:
        symbols = universe_symbols

    context = TRADE_MARKET_CONTEXT[universe_kind]
    fx_rates = trade_fx_to_gbp()
    quote_to_gbp = fx_rates.get(context["currency"], np.nan)
    benchmark = trade_benchmark_frame(context["benchmark"])
    price_rows: List[Dict] = []
    deep_candidates: List[tuple[str, Dict]] = []
    technical_reason_counts: Dict[str, int] = {}
    technical_state_counts: Dict[str, int] = {}
    price_history_failures = 0
    liquidity_failures = 0
    fundamental_pass_count = 0

    # Use the completed Trade fundamental backfill as the first gate. Previously
    # Trade Search downloaded three years of prices for the entire exchange and
    # only then applied fundamentals, which made an "All" scan unnecessarily slow.
    prepared_all: Dict[str, FundamentalSnapshot] = {}
    prepared_path = prepared_trade_path(universe_kind)
    if prepared_path.exists():
        prepared_all = load_prepared_trade_snapshots(
            universe_kind,
            prepared_path.stat().st_mtime_ns,
            TRADE_RULEBOOK_BUILD,
        )

    scan_symbols = symbols
    if prepared_all:
        peer_snapshots = list(prepared_all.values())
        selected_set = set(symbols)
        fundamentally_eligible: List[str] = []
        for snapshot in peer_snapshots:
            if snapshot.symbol not in selected_set:
                continue
            rate = fx_rates.get(snapshot.currency, np.nan)
            if not math.isfinite(rate):
                continue
            fundamental = score_fundamental_snapshot(
                snapshot,
                peer_snapshots,
                rate,
                earnings_sessions=None,
                official_event_verified=False,
                apply_event_gate=False,
            )
            failures = list(fundamental.get("fundamental_failures", []))
            if not failures and float(fundamental.get("fundamental_score", 0) or 0) >= 65:
                fundamentally_eligible.append(snapshot.symbol)
        scan_symbols = fundamentally_eligible
        fundamental_pass_count = len(fundamentally_eligible)
        if _progress_callback:
            _progress_callback(
                0,
                max(len(scan_symbols), 1),
                0,
                f"Prepared fundamentals: {len(scan_symbols):,} of {len(symbols):,} selected shares passed · starting technical scan",
            )

    if not prepared_all:
        fundamental_pass_count = len(scan_symbols)

    total_scan_symbols = len(scan_symbols)
    completed_scan_symbols = 0

    for start in range(0, len(scan_symbols), 60):
        chunk = scan_symbols[start:start + 60]
        try:
            data = yf.download(
                tickers=chunk,
                period="3y",
                interval="1d",
                group_by="ticker",
                auto_adjust=False,
                threads=True,
                progress=False,
            )
        except Exception:
            continue
        for symbol in chunk:
            try:
                frame = extract_ticker_frame(data, symbol)
                if frame is None or len(frame.dropna(subset=["Close", "Volume"])) < 252:
                    price_history_failures += 1
                    continue
                raw_turnover = (frame["Close"] * frame["Volume"]).dropna().tail(20).median()
                turnover_gbp = raw_turnover * context["price_scale"] * quote_to_gbp
                if not math.isfinite(turnover_gbp) or turnover_gbp < 5_000_000:
                    liquidity_failures += 1
                    continue
                technical = evaluate_price_setup(frame, benchmark)
                technical_state = str(technical.get("technical_state") or "BLOCKED")
                technical_reason = str(technical.get("technical_reason") or "UNAVAILABLE")
                technical_state_counts[technical_state] = technical_state_counts.get(technical_state, 0) + 1
                technical_reason_counts[technical_reason] = technical_reason_counts.get(technical_reason, 0) + 1
                if technical.get("technical_state") == "WATCH":
                    technical["turnover_gbp"] = turnover_gbp
                    deep_candidates.append((symbol, technical))
                elif technical.get("technical_state") in {"ENTRY READY", "AWAITING NEXT OPEN"}:
                    exact_turnover_gbp = technical["turnover_median_20"] * context["price_scale"] * quote_to_gbp
                    if not math.isfinite(exact_turnover_gbp) or exact_turnover_gbp < 5_000_000:
                        continue
                    technical["turnover_gbp"] = exact_turnover_gbp
                    deep_candidates.append((symbol, technical))
            except Exception:
                continue

        completed_scan_symbols += len(chunk)
        if _progress_callback:
            _progress_callback(
                completed_scan_symbols,
                max(total_scan_symbols, 1),
                len(deep_candidates),
                f"Technical scan: {completed_scan_symbols:,} of {total_scan_symbols:,} · {len(deep_candidates):,} current setups found",
            )

    snapshots = []
    snapshot_by_symbol = {}
    snapshot_error_by_symbol = {}

    candidate_symbols = tuple(symbol for symbol, _technical in deep_candidates)

    # Reuse the prepared exchange fundamentals as the peer set and candidate source.
    snapshots.extend(prepared_all.values())
    for symbol in candidate_symbols:
        snapshot = prepared_all.get(symbol)
        if snapshot is not None:
            snapshot_by_symbol[symbol] = snapshot

    # Live TradingView is now only a fallback for candidates absent from the nightly
    # cache, so the normal Trade Search avoids repeating slow fundamentals work.
    live_needed = tuple(symbol for symbol in candidate_symbols if symbol not in snapshot_by_symbol)
    if live_needed:
        try:
            tv_snapshots = trade_tradingview_snapshots(
                live_needed, universe_kind, TRADE_RULEBOOK_BUILD
            )
        except Exception as exc:
            tv_snapshots = {}
            tv_batch_error = type(exc).__name__
        else:
            tv_batch_error = None
        snapshots.extend(tv_snapshots.values())
        snapshot_by_symbol.update(tv_snapshots)
    else:
        tv_batch_error = None

    # Yahoo remains the last fallback only for symbols neither the nightly cache nor
    # TradingView returned.
    provider_cooldown = False
    missing_symbols = [symbol for symbol in candidate_symbols if symbol not in snapshot_by_symbol]
    for fallback_index, symbol in enumerate(missing_symbols):
        if provider_cooldown:
            snapshot_by_symbol[symbol] = None
            snapshot_error_by_symbol[symbol] = "YAHOO RATE LIMIT — RETRY LATER"
            continue

        if fallback_index:
            time.sleep(0.50)

        last_exc = None
        for delay in (0.0, 2.0):
            if delay:
                time.sleep(delay)
            try:
                snapshot = trade_fundamental_snapshot(symbol, TRADE_RULEBOOK_BUILD)
                snapshots.append(snapshot)
                snapshot_by_symbol[symbol] = snapshot
                last_exc = None
                break
            except Exception as exc:
                last_exc = exc
                if not _is_yahoo_rate_limit_error(exc):
                    break

        if last_exc is not None:
            snapshot_by_symbol[symbol] = None
            if _is_yahoo_rate_limit_error(last_exc):
                snapshot_error_by_symbol[symbol] = type(last_exc).__name__
                provider_cooldown = True
            else:
                detail = type(last_exc).__name__
                if tv_batch_error:
                    detail = f"TRADINGVIEW {tv_batch_error}; YAHOO {detail}"
                snapshot_error_by_symbol[symbol] = detail

    for symbol, technical in deep_candidates:
        snapshot = snapshot_by_symbol.get(symbol)
        if snapshot is None:
            provider_error = snapshot_error_by_symbol.get(symbol, "UNKNOWN")
            price_rows.append({
                "Status": "RETRY",
                "Ticker": symbol,
                "Reason": "TEMPORARY FUNDAMENTAL DATA PROVIDER ERROR — RETRY "
                          f"({provider_error})",
                "Price": technical.get("price"),
                "RSI": technical.get("rsi"),
                "Median traded value £m": technical["turnover_gbp"] / 1_000_000,
                "Technical score": technical.get("technical_score"),
                "Tier": technical.get("technical_tier"),
            })
            continue
        rate = fx_rates.get(snapshot.currency, np.nan)
        sessions = business_sessions_until(snapshot.earnings_date)
        # Score once using the established rule function, then separate numerical
        # failures from event-only failures. This keeps WATCH setups visible while
        # deferring event verification until a setup is candidate-ready.
        fundamental = score_fundamental_snapshot(
            snapshot,
            snapshots,
            rate,
            sessions,
            official_event_verified=False,
        )
        all_failures = list(fundamental["fundamental_failures"])
        event_failures = [
            failure for failure in all_failures
            if failure == "EARNINGS DATE UNVERIFIED"
            or failure.startswith("EARNINGS WAIT")
            or failure.startswith("FAIL-SAFE EVENT BLOCK")
        ]
        failures = [failure for failure in all_failures if failure not in event_failures]

        if "EXCLUDED SECTOR" in failures:
            status = "BLOCKED"
            reason = "EXCLUDED SECTOR"
        elif failures:
            status = "BLOCKED"
            reason = "; ".join(failures)
        elif technical["technical_state"] == "WATCH":
            status = "WATCH"
            reason = technical["technical_reason"]
        elif event_failures:
            status = "BLOCKED"
            reason = "; ".join(event_failures)
        elif technical["technical_state"] == "AWAITING NEXT OPEN":
            status = "WATCH"
            reason = "VALID DAILY CLOSE — AWAITING NEXT OPEN"
        else:
            status = "PAPER CANDIDATE"
            reason = "ALL APPROVED GATES PASS"
        price_rows.append({
            "Status": status,
            "Ticker": symbol,
            "Company": snapshot.company,
            "Sector": snapshot.sector,
            "Industry": snapshot.industry,
            "Reason": reason,
            "Setup stage": technical["technical_reason"],
            "Score status": "PENDING CROSSOVER" if technical["technical_state"] == "WATCH" else "CALCULATED",
            "Warnings": "; ".join(fundamental["fundamental_warnings"]) or "—",
            "Signal date": pd.Timestamp(technical["signal_date"]).date() if technical.get("signal_date") is not None else "PENDING",
            "Entry date": pd.Timestamp(technical["entry_date"]).date() if pd.notna(technical.get("entry_date")) else "PENDING",
            "Entry": technical.get("entry"),
            "Stop": technical.get("stop"),
            "Target": technical.get("target"),
            "Target basis": technical.get("target_source"),
            "Stop distance %": technical.get("stop_distance_pct"),
            "Upside %": technical.get("upside_pct"),
            "R:R": technical.get("reward_risk"),
            "RSI": technical.get("rsi"),
            "Median traded value £m": technical["turnover_gbp"] / 1_000_000,
            "Market cap £m": fundamental["market_cap_gbp"] / 1_000_000 if math.isfinite(fundamental["market_cap_gbp"]) else np.nan,
            "Fundamental score": fundamental["fundamental_score"],
            "Technical score": technical.get("technical_score"),
            "Tier": technical.get("technical_tier"),
            "Market regime": technical.get("market_state"),
            "RS recovery %": technical.get("relative_strength_pct"),
            "Volume ratio": technical.get("volume_ratio"),
            "FCF evidence years": fundamental["fcf_evidence_years"],
            "Margin benchmark": fundamental["operating_margin_basis"],
            "FCF benchmark": fundamental["fcf_margin_basis"],
            "Earnings date": snapshot.earnings_date.date() if snapshot.earnings_date is not None else "UNVERIFIED",
            "Earnings source": snapshot.earnings_source,
            "Event check": "UNVERIFIED — FAIL-SAFE BLOCK",
        })

    scan_diagnostics = {
        "selected_symbols": len(symbols),
        "fundamental_pass": fundamental_pass_count,
        "technical_scan_target": total_scan_symbols,
        "technical_evaluated": int(sum(technical_state_counts.values())),
        "technical_state_counts": technical_state_counts,
        "technical_reason_counts": technical_reason_counts,
        "price_history_failures": price_history_failures,
        "liquidity_failures": liquidity_failures,
        "current_setups": len(deep_candidates),
    }

    if not price_rows:
        empty = pd.DataFrame()
        empty.attrs["scan_diagnostics"] = scan_diagnostics
        return empty
    output = pd.DataFrame(price_rows)
    order = {"PAPER CANDIDATE": 0, "WATCH": 1, "BLOCKED": 2}
    output["_status_order"] = output["Status"].map(order).fillna(9)
    output = output.sort_values(
        ["_status_order", "Technical score"], ascending=[True, False], na_position="last"
    ).drop(columns="_status_order")
    output = output.reset_index(drop=True)
    output.attrs["scan_diagnostics"] = scan_diagnostics
    return output


def deep_score_shortlist(pre: pd.DataFrame, n: int) -> pd.DataFrame:
    if pre.empty:
        return pd.DataFrame()
    out = []
    for _, row in pre.head(n).iterrows():
        sym = row["Ticker"]
        try:
            fund = fundamental_analysis(sym, float(row["Price"]))
            sc = classify(float(row["Trade"]), fund["hold_score"])
            out.append({
                "Ticker": sym,
                "Trade": float(row["Trade"]),
                "Hold": fund["hold_score"],
                "Opportunity": sc.opportunity,
                "Type": sc.classification,
                "Price": float(row["Price"]),
                "Preferred entry": f"{fmt_price(row['Preferred low'])}–{fmt_price(row['Preferred high'])}",
                "Strong entry": f"{fmt_price(row['Strong low'])}–{fmt_price(row['Strong high'])}",
                "Target": float(row["Target"]),
                "Upside %": float(row["Upside %"]),
                "R:R": float(row["R:R"]),
                "RSI": float(row["RSI"]),
                "Preferred now": bool(row["Preferred now"]),
                "Strong now": bool(row["Strong now"]),
                "Analyst target": fund["analyst_target"],
                "Analyst upside %": fund["analyst_upside"],
                "Analysts": fund["analyst_count"],
                "Revenue growth %": fund["revenue_growth"],
                "EPS growth %": fund["earnings_growth"],
                "Profit margin %": fund["profit_margin"],
                "Market cap": fund["market_cap"],
            })
        except Exception:
            continue
    if not out:
        return pd.DataFrame()
    return pd.DataFrame(out).sort_values(["Opportunity", "Trade"], ascending=[False, False]).reset_index(drop=True)


def _fast_info_number(fast, *keys) -> float:
    for key in keys:
        try:
            value = fast[key]
        except Exception:
            try:
                value = getattr(fast, key)
            except Exception:
                continue
        number = safe(value)
        if not np.isnan(number):
            return number
    return np.nan


def _batch_latest_prices(symbols: List[str], chunk_size: int = 200) -> tuple[Dict[str, float], int]:
    prices = {}
    rate_limit_errors = 0
    for start in range(0, len(symbols), chunk_size):
        chunk = symbols[start:start + chunk_size]
        try:
            batch = yf.download(
                tickers=chunk,
                period="5d",
                interval="1d",
                auto_adjust=False,
                progress=False,
                threads=True,
                group_by="column",
            )
            if batch is None or batch.empty:
                continue
            if isinstance(batch.columns, pd.MultiIndex) and "Close" in batch.columns.get_level_values(0):
                closes = batch["Close"]
                for sym in chunk:
                    if sym in closes.columns:
                        series = pd.to_numeric(closes[sym], errors="coerce").dropna()
                        if not series.empty:
                            prices[sym] = safe(series.iloc[-1])
            elif "Close" in batch.columns and len(chunk) == 1:
                series = pd.to_numeric(batch["Close"], errors="coerce").dropna()
                if not series.empty:
                    prices[chunk[0]] = safe(series.iloc[-1])
        except Exception as exc:
            if "ratelimit" in exc.__class__.__name__.lower() or "too many requests" in str(exc).lower():
                rate_limit_errors += 1
    return prices, rate_limit_errors


def _investment_company_result(
    sym: str,
    initial_price: float,
    min_market_cap: float,
    directory_market_caps: Dict[str, float],
) -> dict:
    """Analyse one company with one shared yfinance Ticker object."""
    stage = "price"
    for attempt in range(3):
        try:
            ticker = yf.Ticker(sym)
            price = safe(initial_price)
            if np.isnan(price) or price <= 0:
                try:
                    price = _fast_info_number(ticker.fast_info, "last_price", "lastPrice")
                except Exception:
                    pass
            if np.isnan(price) or price <= 0:
                hist = ticker.history(period="5d", interval="1d", auto_adjust=False)
                if hist is not None and not hist.empty and "Close" in hist.columns:
                    close_s = pd.to_numeric(hist["Close"], errors="coerce").dropna()
                    if not close_s.empty:
                        price = safe(close_s.iloc[-1])
            if np.isnan(price) or price <= 0:
                return {"status": "price_failure", "symbol": sym}

            stage = "company fundamentals"
            try:
                fund = valuation_fundamental_analysis(sym, price, ticker=ticker)
            except TypeError as exc:
                # Streamlit can briefly retain the previous imported module
                # during a hot deployment. Keep scans working until its module
                # cache is refreshed, then use the shared Ticker path normally.
                if "unexpected keyword argument 'ticker'" not in str(exc):
                    raise
                fund = valuation_fundamental_analysis(sym, price)
            market_cap = safe(fund.get("market_cap"))
            if np.isnan(market_cap) or market_cap <= 0:
                market_cap = safe(directory_market_caps.get(sym))
            if np.isnan(market_cap) or market_cap <= 0:
                return {"status": "market_cap_failure", "symbol": sym}
            if market_cap < min_market_cap:
                return {"status": "below_market_cap", "symbol": sym}
            fund["market_cap"] = market_cap

            stage = "long-term analysis"
            try:
                lt = long_term_analysis(sym, price, fund, _ticker=ticker)
            except TypeError as exc:
                if "unexpected keyword argument '_ticker'" not in str(exc):
                    raise
                lt = long_term_analysis(sym, price, fund)
            merged = {**fund, **lt, "price": price}
            stage = "decision logic"
            decision = investment_decision(merged)
            row = {
                "Ticker": sym,
                "Company": fund.get("name") or sym,
                "Action": decision["action"],
                "Price": price,
                "Quality score": lt.get("investment_quality_score", lt.get("long_term_score", np.nan)),
                "Moat score": lt.get("moat_score", np.nan),
                "Quant moat confidence": lt.get("moat_confidence", "LOW"),
                "Structural moat review": lt.get("structural_moat_status", "UNVERIFIED"),
                "Description moat clues": lt.get("moat_mechanisms", "Needs manual verification"),
                "Hard gates": "PASS" if lt.get("hard_gate_pass") else "FAIL",
                "Hard-gate failures": lt.get("hard_gate_failures", ""),
                "Base intrinsic value": lt.get("dcf_base"),
                "Bear intrinsic value": lt.get("dcf_bear"),
                "Bull intrinsic value": lt.get("dcf_bull"),
                "Base margin of safety %": lt.get("margin_of_safety_base_pct"),
                "Required margin of safety %": lt.get("required_margin_of_safety_pct"),
                "Bear margin of safety %": lt.get("margin_of_safety_bear_pct"),
                "Valuation gate": "PASS" if lt.get("valuation_gate_pass") else "WAIT",
                "Valuation model version": lt.get("valuation_model_version", VALUATION_MODEL_VERSION),
                "Valuation FX status": lt.get("valuation_fx_status", "FX UNAVAILABLE"),
                "Valuation FX rate": lt.get("valuation_fx_rate"),
                "Valuation FX pair": lt.get("valuation_fx_pair", ""),
                "Quote currency": lt.get("quote_currency", fund.get("quote_currency", "")),
                "Financial currency": lt.get("financial_currency", fund.get("financial_currency", "")),
                "Resilience": lt.get("resilience_score", np.nan),
                "Reinvestment": lt.get("reinvestment_score", np.nan),
                "Capital allocation": lt.get("capital_allocation_score", np.nan),
                "Cash quality": lt.get("cash_quality_score", np.nan),
                "ROIC %": lt.get("roic_pct"),
                "ROIC trend %": lt.get("roic_trend_pct"),
                "FCF/share CAGR %": lt.get("fcf_per_share_cagr_pct"),
                "Positive FCF years %": lt.get("positive_fcf_years_pct"),
                "Dilution CAGR %": lt.get("dilution_cagr_pct"),
                "Net debt / FCF": lt.get("net_debt_to_fcf"),
                "Evidence years": lt.get("evidence_years"),
                "Sector model": lt.get("sector_model", "Generic"),
                "Exchange Country": fund.get("exchange_country", "Other / Unknown"),
                "Sector": fund.get("sector") or "—",
                "Industry": fund.get("industry") or "—",
                "Market cap": market_cap,
                "Review flags": lt.get("hard_gate_warnings", ""),
                "Manual review required": lt.get("qualitative_review_items", ""),
                "Decision reason": lt.get("action_reason", ""),
            }
            return {"status": "row", "symbol": sym, "row": row}
        except Exception as exc:
            rate_limited = "ratelimit" in exc.__class__.__name__.lower() or "too many requests" in str(exc).lower()
            if attempt < 2:
                time.sleep((attempt + 1) * (4 if rate_limited else 1))
                continue
            return {
                "status": "error",
                "symbol": sym,
                "rate_limited": rate_limited,
                "message": f"{sym} @ {stage}: {exc.__class__.__name__}: {str(exc)[:220]}",
            }
    return {"status": "error", "symbol": sym, "rate_limited": False, "message": f"{sym}: unknown error"}


def fundamental_market_scan(
    symbols_tuple: tuple[str, ...],
    max_symbols: int,
    min_market_cap: float,
    directory_market_caps_tuple: tuple[tuple[str, float], ...] = (),
    progress_callback=None,
    max_workers: int = 4,
) -> pd.DataFrame:
    """Independent 10-years-to-forever investment scan.

    This deliberately separates business quality from valuation. Hard-gate
    failures cannot be rescued by a high weighted score.
    """
    universe_symbols = list(symbols_tuple)
    if max_symbols > 0 and max_symbols < len(universe_symbols):
        idx = np.linspace(0, len(universe_symbols) - 1, max_symbols, dtype=int)
        symbols = [universe_symbols[i] for i in idx]
    else:
        symbols = universe_symbols
    directory_market_caps = dict(directory_market_caps_tuple)
    rows = []
    rate_limit_errors = 0
    other_errors = 0
    price_failures = 0
    market_cap_failures = 0
    below_min_market_cap = 0
    error_samples = []

    batch_prices, batch_rate_limit_errors = _batch_latest_prices(symbols)
    rate_limit_errors += batch_rate_limit_errors

    workers = max(1, min(int(max_workers), 6))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(
                _investment_company_result,
                sym,
                safe(batch_prices.get(sym)),
                min_market_cap,
                directory_market_caps,
            ): sym
            for sym in symbols
        }
        for completed, future in enumerate(as_completed(futures), start=1):
            result = future.result()
            status = result.get("status")
            if status == "row":
                rows.append(result["row"])
            elif status == "price_failure":
                price_failures += 1
            elif status == "market_cap_failure":
                market_cap_failures += 1
            elif status == "below_market_cap":
                below_min_market_cap += 1
            elif status == "error":
                if result.get("rate_limited"):
                    rate_limit_errors += 1
                else:
                    other_errors += 1
                if len(error_samples) < 5:
                    error_samples.append(result.get("message", f"{result.get('symbol')}: unknown error"))
            if progress_callback is not None:
                progress_callback(completed, len(symbols), len(rows))

    if not rows:
        out = pd.DataFrame()
        out.attrs["scan_diagnostics"] = {
            "requested": len(symbols),
            "rate_limit_errors": rate_limit_errors,
            "other_errors": other_errors,
            "price_failures": price_failures,
            "market_cap_failures": market_cap_failures,
            "below_min_market_cap": below_min_market_cap,
            "error_samples": error_samples,
        }
        return out

    out = pd.DataFrame(rows)
    out.attrs["scan_diagnostics"] = {
        "requested": len(symbols),
        "returned": len(rows),
        "rate_limit_errors": rate_limit_errors,
        "other_errors": other_errors,
        "price_failures": price_failures,
        "market_cap_failures": market_cap_failures,
        "below_min_market_cap": below_min_market_cap,
        "error_samples": error_samples,
    }
    action_rank = {"BUY CANDIDATE": 0, "WAIT": 1, "PASS": 2}
    out["_action_rank"] = out["Action"].map(action_rank).fillna(3)
    out = out.sort_values(
        ["_action_rank", "Quality score", "Base margin of safety %"],
        ascending=[True, False, False],
        na_position="last",
    ).drop(columns=["_action_rank"]).reset_index(drop=True)
    return out


def prepared_scan_path(kind: str) -> Path:
    return PREPARED_SCAN_DIR / f"{kind}.csv.gz"


def prepared_scan_metadata(kind: str) -> dict:
    manifest_path = PREPARED_SCAN_DIR / "manifest.json"
    if not manifest_path.exists():
        return {}
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        return manifest.get("exchanges", {}).get(kind, {})
    except Exception:
        return {}


@st.cache_data(ttl=900, show_spinner=False)
def load_prepared_investment_scan(kind: str, modified_ns: int) -> pd.DataFrame:
    path = prepared_scan_path(kind)
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path, compression="gzip")


def refresh_prepared_investment_prices(frame: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Refresh only prices and price-dependent investment decisions."""
    if frame.empty or "Ticker" not in frame.columns:
        return frame, {"updated": 0, "missing": 0, "rate_limit_errors": 0}
    out = frame.copy()
    symbols = out["Ticker"].dropna().astype(str).tolist()
    prices, rate_limit_errors = _batch_latest_prices(symbols)
    updated = 0
    missing = 0
    for idx, row in out.iterrows():
        symbol = str(row.get("Ticker") or "")
        if str(row.get("Valuation model version") or "") != VALUATION_MODEL_VERSION:
            out.at[idx, "Action"] = "WAIT"
            out.at[idx, "Valuation gate"] = "WAIT"
            out.at[idx, "Decision reason"] = "valuation model is stale; full FX-safe valuation refresh required"
            continue
        if str(row.get("Valuation FX status") or "").upper() != "PASS":
            out.at[idx, "Action"] = "WAIT"
            out.at[idx, "Valuation gate"] = "WAIT"
            out.at[idx, "Decision reason"] = "valuation currency conversion is unavailable or unverified"
            continue
        price = safe(prices.get(symbol))
        if np.isnan(price) or price <= 0:
            missing += 1
            continue
        base_value = safe(row.get("Base intrinsic value"))
        bear_value = safe(row.get("Bear intrinsic value"))
        required_mos = safe(row.get("Required margin of safety %"))
        base_mos = np.nan if np.isnan(base_value) or base_value <= 0 else (1 - price / base_value) * 100
        bear_mos = np.nan if np.isnan(bear_value) or bear_value <= 0 else (1 - price / bear_value) * 100
        valuation_pass = (
            not np.isnan(base_mos)
            and not np.isnan(required_mos)
            and base_mos >= required_mos
            and not np.isnan(bear_mos)
            and bear_mos >= 0
        )
        hard_gate_pass = str(row.get("Hard gates") or "").upper() == "PASS"
        specialist = str(row.get("Sector model") or "Generic") != "Generic"
        if not hard_gate_pass:
            action = "PASS"
            decision_reason = str(row.get("Hard-gate failures") or "one or more hard gates failed")
        elif specialist:
            action = "WAIT"
            decision_reason = "specialist sector review required before a buy decision"
            valuation_pass = False
        elif valuation_pass:
            action = "BUY CANDIDATE"
            decision_reason = "quantitative hard gates and DCF margin-of-safety gate passed; complete manual review before buying"
        else:
            action = "WAIT"
            decision_reason = "quality may qualify, but valuation / bear-case margin of safety is insufficient"
        out.at[idx, "Price"] = price
        out.at[idx, "Base margin of safety %"] = None if np.isnan(base_mos) else round(base_mos, 1)
        out.at[idx, "Bear margin of safety %"] = None if np.isnan(bear_mos) else round(bear_mos, 1)
        out.at[idx, "Valuation gate"] = "PASS" if valuation_pass else "WAIT"
        out.at[idx, "Action"] = action
        out.at[idx, "Decision reason"] = decision_reason
        updated += 1
    action_rank = {"BUY CANDIDATE": 0, "WAIT": 1, "PASS": 2}
    out["_action_rank"] = out["Action"].map(action_rank).fillna(3)
    out = out.sort_values(
        ["_action_rank", "Quality score", "Base margin of safety %"],
        ascending=[True, False, False],
        na_position="last",
    ).drop(columns=["_action_rank"]).reset_index(drop=True)
    return out, {"updated": updated, "missing": missing, "rate_limit_errors": rate_limit_errors}


@st.cache_data(ttl=120, show_spinner=False)
def latest_portfolio_price(symbol: str) -> float:
    """Retrieve the latest independent market quote for a portfolio holding.

    Prefer recent intraday trade data over fast_info because fast_info can lag
    or temporarily surface stale values for some non-US listings. Fall back to
    the latest daily close, then fast_info only if history is unavailable.
    """
    ticker = yf.Ticker(symbol)

    for period, interval in (("5d", "5m"), ("10d", "1d")):
        try:
            history = ticker.history(
                period=period,
                interval=interval,
                auto_adjust=False,
                prepost=False,
            )
            if history is not None and not history.empty and "Close" in history.columns:
                closes = pd.to_numeric(history["Close"], errors="coerce").dropna()
                if not closes.empty:
                    price = safe(closes.iloc[-1])
                    if not np.isnan(price) and price > 0:
                        return price
        except Exception:
            continue

    try:
        price = safe(ticker.fast_info.get("last_price"))
        if not np.isnan(price) and price > 0:
            return price
    except Exception:
        pass
    return np.nan


@st.cache_data(ttl=1800, show_spinner=False)
def currency_to_gbp_rate(currency: str) -> float:
    """Return the approximate GBP value of one unit of a quote currency."""
    code = str(currency or "").strip()
    if code in {"GBp", "GBX"}:
        return 0.01
    code = code.upper()
    if code == "GBP":
        return 1.0
    if not code:
        return np.nan

    for pair, invert in ((f"{code}GBP=X", False), (f"GBP{code}=X", True)):
        try:
            fx = yf.Ticker(pair)
            rate = safe(fx.fast_info.get("last_price"))
            if np.isnan(rate) or rate <= 0:
                history = fx.history(period="5d", interval="1d", auto_adjust=False)
                if history is not None and not history.empty and "Close" in history.columns:
                    closes = pd.to_numeric(history["Close"], errors="coerce").dropna()
                    if not closes.empty:
                        rate = safe(closes.iloc[-1])
            if not np.isnan(rate) and rate > 0:
                return 1 / rate if invert else rate
        except Exception:
            continue
    return np.nan


def analyse_portfolio_holding(symbol: str, shares: float, average_cost: float) -> dict:
    """Run the long-term framework for one existing investment position.

    Average cost is entered exactly as the broker displays it. For London
    shares Yahoo commonly quotes prices in GBp (pence), while brokers such as
    Trading 212 display the same price in GBP. Convert only for internal maths.
    """
    input_symbol = str(symbol).strip().upper()
    symbol = resolve_portfolio_symbol(input_symbol)
    price = latest_portfolio_price(symbol)
    if np.isnan(price) or price <= 0:
        if symbol != input_symbol:
            raise ValueError(f"current price unavailable (resolved {input_symbol} to {symbol})")
        raise ValueError("current price unavailable")

    fund = valuation_fundamental_analysis(symbol, price)
    quote_currency = str(fund.get("quote_currency") or "")
    is_pence_quote = quote_currency in {"GBp", "GBX"}
    quote_average_cost = average_cost * 100 if average_cost > 0 and is_pence_quote else average_cost
    display_price = price / 100 if is_pence_quote else price
    display_currency = "GBP" if is_pence_quote else (quote_currency or "Unknown")

    lt = long_term_analysis(symbol, price, fund)
    merged = {**fund, **lt, "price": price}
    decision = investment_decision(
        merged,
        owned=True,
        average_buy_price=quote_average_cost if quote_average_cost > 0 else None,
    )

    if decision["action"] == "SELL":
        action = "REVIEW FOR SALE"
    elif decision["action"] == "REASSESS":
        action = "REASSESS"
    elif lt.get("valuation_gate_pass"):
        action = "BUY MORE"
    else:
        action = "HOLD"

    gbp_rate = currency_to_gbp_rate(quote_currency)
    native_value = price * shares
    market_value_gbp = native_value * gbp_rate if not np.isnan(gbp_rate) else np.nan
    cost_value_gbp = quote_average_cost * shares * gbp_rate if quote_average_cost > 0 and not np.isnan(gbp_rate) else np.nan
    pnl_gbp = market_value_gbp - cost_value_gbp if not np.isnan(cost_value_gbp) else np.nan
    return_pct = (price / quote_average_cost - 1) * 100 if quote_average_cost > 0 else np.nan

    dcf_base = lt.get("dcf_base")
    dcf_bear = lt.get("dcf_bear")
    if is_pence_quote:
        dcf_base = dcf_base / 100 if dcf_base is not None and not np.isnan(safe(dcf_base)) else dcf_base
        dcf_bear = dcf_bear / 100 if dcf_bear is not None and not np.isnan(safe(dcf_bear)) else dcf_bear

    reasons = list(decision.get("reasons") or [])
    if action == "BUY MORE":
        reasons.insert(0, "the investment still passes the quality and valuation gates for adding")
    elif action == "HOLD":
        reasons.insert(0, "the measurable thesis remains intact, but the current price does not qualify for adding")
    elif action == "REASSESS" and not reasons:
        reasons.append("one or more measurable thesis checks needs review")

    return {
        "Ticker": input_symbol,
        "Resolved ticker": symbol,
        "Position type": "INVESTMENT",
        "Company": fund.get("name") or input_symbol,
        "Action": action,
        "Shares": shares,
        "Average cost": average_cost,
        "Amount invested": average_cost * shares,
        "Price": display_price,
        "Current price": display_price,
        "Return %": return_pct,
        "Market value £": market_value_gbp,
        "Cost basis £": cost_value_gbp,
        "Unrealised P/L £": pnl_gbp,
        "Quote currency": display_currency,
        "Quality score": lt.get("investment_quality_score", lt.get("long_term_score", np.nan)),
        "Hard gates": "PASS" if lt.get("hard_gate_pass") else "FAIL",
        "Base intrinsic value": dcf_base,
        "Bear intrinsic value": dcf_bear,
        "Base margin of safety %": lt.get("margin_of_safety_base_pct"),
        "Required margin of safety %": lt.get("required_margin_of_safety_pct"),
        "Bear margin of safety %": lt.get("margin_of_safety_bear_pct"),
        "Sector": fund.get("sector") or "Unknown",
        "Industry": fund.get("industry") or "Unknown",
        "Country": fund.get("exchange_country") or "Unknown",
        "Review reason": "; ".join(dict.fromkeys(reason for reason in reasons if reason)),
        "Manual review required": lt.get("qualitative_review_items", ""),
    }


def analyse_trade_portfolio_holding(symbol: str, shares: float, average_cost: float) -> dict:
    """Run the owned-position trade framework without mixing it into investment allocation."""
    input_symbol = str(symbol).strip().upper()
    symbol = resolve_portfolio_symbol(input_symbol)
    tech = technical_analysis(symbol)
    if not tech:
        raise ValueError("trade technical data unavailable")

    price = safe(tech.get("price"))
    if np.isnan(price) or price <= 0:
        raise ValueError("current price unavailable")

    fund = valuation_fundamental_analysis(symbol, price)
    quote_currency = str(fund.get("quote_currency") or "")
    is_pence_quote = quote_currency in {"GBp", "GBX"}
    quote_average_cost = average_cost * 100 if average_cost > 0 and is_pence_quote else average_cost
    display_currency = "GBP" if is_pence_quote else (quote_currency or "Unknown")

    trade_context = {
        **tech,
        **fund,
        "one_year_hold_score": fund.get("hold_score", 0),
        "valuation_score": fund.get("valuation_score", 0),
        "price": price,
    }
    decision = trade_decision(
        trade_context,
        owned=True,
        average_buy_price=quote_average_cost if quote_average_cost > 0 else None,
    )

    def display_quote(value):
        x = safe(value)
        if np.isnan(x):
            return np.nan
        return x / 100 if is_pence_quote else x

    gbp_rate = currency_to_gbp_rate(quote_currency)
    market_value_gbp = price * shares * gbp_rate if not np.isnan(gbp_rate) else np.nan
    cost_value_gbp = quote_average_cost * shares * gbp_rate if quote_average_cost > 0 and not np.isnan(gbp_rate) else np.nan
    pnl_gbp = market_value_gbp - cost_value_gbp if not np.isnan(cost_value_gbp) else np.nan
    return_pct = (price / quote_average_cost - 1) * 100 if quote_average_cost > 0 else np.nan

    reasons = list(decision.get("reasons") or [])
    if decision.get("action") == "HOLD" and not reasons:
        reasons.append("price remains above invalidation and the owned-trade hold checks remain intact")
    if decision.get("action") == "EXIT / TARGET REACHED":
        reasons.insert(0, "the modelled profit target has been reached")
    if decision.get("action") == "EXIT / REASSESS":
        reasons.insert(0, "price is at or below the trade invalidation level")

    preferred_low = display_quote(tech.get("preferred_low"))
    preferred_high = display_quote(tech.get("preferred_high"))
    entry_zone = (
        f"{preferred_low:.4f}–{preferred_high:.4f}"
        if not np.isnan(preferred_low) and not np.isnan(preferred_high)
        else "—"
    )

    return {
        "Ticker": input_symbol,
        "Resolved ticker": symbol,
        "Position type": "TRADE",
        "Company": fund.get("name") or input_symbol,
        "Action": decision.get("action") or "REASSESS",
        "Shares": shares,
        "Average cost": average_cost,
        "Amount invested": average_cost * shares,
        "Price": display_quote(price),
        "Current price": display_quote(price),
        "Return %": return_pct,
        "Market value £": market_value_gbp,
        "Cost basis £": cost_value_gbp,
        "Unrealised P/L £": pnl_gbp,
        "Quote currency": display_currency,
        "Sector": fund.get("sector") or "Unknown",
        "Industry": fund.get("industry") or "Unknown",
        "Country": fund.get("exchange_country") or "Unknown",
        "Technical score": tech.get("trade_score", np.nan),
        "R:R": tech.get("rr", np.nan),
        "Entry zone": entry_zone,
        "Target": display_quote(tech.get("swing_target")),
        "Invalidation": display_quote(tech.get("invalidation")),
        "Trade reason": "; ".join(dict.fromkeys(reason for reason in reasons if reason)),
    }

def chart(result: Dict) -> go.Figure:
    d = result["history"].tail(120)
    fig = go.Figure()
    fig.add_trace(go.Candlestick(
        x=d.index,
        open=d["Open"],
        high=d["High"],
        low=d["Low"],
        close=d["Close"],
        name=result["symbol"],
    ))
    channel = stock_trend_channel(d, 90)
    if channel.get("lower_series") and channel.get("upper_series"):
        start = int(channel.get("start", 0))
        channel_dates = d.index[start:]
        fig.add_trace(go.Scatter(
            x=channel_dates, y=channel["lower_series"], mode="lines",
            name="Channel support", line=dict(dash="dot"),
        ))
        fig.add_trace(go.Scatter(
            x=channel_dates, y=channel["upper_series"], mode="lines",
            name="Channel resistance", line=dict(dash="dot"),
        ))
    bb_mid = d["Close"].rolling(20).mean()
    bb_sd = d["Close"].rolling(20).std()
    fig.add_trace(go.Scatter(x=d.index, y=bb_mid + 2 * bb_sd, mode="lines", name="BB upper", line=dict(dash="dot")))
    fig.add_trace(go.Scatter(x=d.index, y=bb_mid, mode="lines", name="BB mid"))
    fig.add_trace(go.Scatter(x=d.index, y=bb_mid - 2 * bb_sd, mode="lines", name="BB lower", line=dict(dash="dot")))
    fig.add_trace(go.Scatter(x=d.index, y=d["SMA20"], mode="lines", name="SMA20"))
    fig.add_trace(go.Scatter(x=d.index, y=d["SMA50"], mode="lines", name="SMA50"))
    fig.add_hrect(y0=result["preferred_low"], y1=result["preferred_high"], opacity=.12, line_width=0, annotation_text="Preferred entry")
    fig.add_hrect(y0=result["strong_low"], y1=result["strong_high"], opacity=.08, line_width=0, annotation_text="Strong entry")
    fig.add_hline(y=result["invalidation"], line_dash="dot", annotation_text="Reassess / invalidation")
    fig.add_hline(y=result["swing_target"], line_dash="dash", annotation_text="Swing target")
    fig.update_layout(height=520, xaxis_rangeslider_visible=False, margin=dict(l=10, r=10, t=30, b=10))
    return fig


render_module_header(
    "Stocks",
    "📈",
    "Find better entry points for trades and long-term investments without digging through all the data yourself.",
)

st.markdown("""
<style>
.stock-home-actions-grid {
  display:grid;
  grid-template-columns:repeat(3,minmax(0,1fr));
  gap:14px;
  margin:8px 0 24px 0;
}
.stock-home-action-card,
.stock-summary-card {
  display:flex;
  flex-direction:column;
  color:inherit !important;
  text-decoration:none !important;
  background:#ffffff;
  border:1px solid #e2e8f0;
  border-radius:16px;
  transition:border-color .16s ease, box-shadow .16s ease, transform .16s ease;
}
.stock-home-action-card {
  min-height:178px;
  padding:20px;
}
.stock-home-action-card:hover,
.stock-summary-card:hover {
  border-color:#2f7bf2;
  box-shadow:0 10px 26px rgba(15,73,160,.10);
  transform:translateY(-2px);
}
.stock-home-action-card:focus-visible,
.stock-summary-card:focus-visible {
  outline:3px solid rgba(47,123,242,.25);
  outline-offset:3px;
}
.stock-home-action-title {
  color:#0a1735;
  font-size:1.12rem;
  font-weight:850;
  margin-bottom:8px;
}
.stock-home-action-copy {
  color:#64748b;
  line-height:1.5;
  flex:1 1 auto;
}
.stock-home-action-cta {
  color:#1757ad;
  font-weight:850;
  margin-top:14px;
}
.stock-home-opportunity-counts {
  display:flex;
  flex-wrap:wrap;
  gap:8px;
  margin-top:13px;
}
.stock-home-opportunity-counts span {
  display:inline-flex;
  align-items:center;
  gap:5px;
  padding:6px 9px;
  border-radius:999px;
  background:#f4f7fb;
  border:1px solid #dce5f0;
  color:#40566f;
  font-size:.78rem;
  font-weight:700;
}
.stock-home-opportunity-counts strong {
  color:#0a1735;
  font-size:.92rem;
}
.stock-summary-grid {
  display:grid;
  grid-template-columns:repeat(6,minmax(0,1fr));
  gap:10px;
  margin:10px 0 12px 0;
}
.live-trade-summary-grid {
  display:grid;
  grid-template-columns:repeat(5,minmax(0,1fr));
  gap:10px;
  margin:10px 0 12px 0;
}
.stock-summary-card {
  min-height:116px;
  padding:14px;
}
.stock-summary-card.is-active {
  border-color:#2f7bf2;
  background:#f7faff;
  box-shadow:0 8px 22px rgba(15,73,160,.08);
}
.stock-summary-label-row {
  display:flex;
  align-items:flex-start;
  justify-content:space-between;
  gap:8px;
  color:#64748b;
  font-size:.76rem;
  font-weight:800;
  text-transform:uppercase;
  letter-spacing:.035em;
}
.stock-summary-value {
  color:#0a1735;
  font-size:1.85rem;
  line-height:1;
  font-weight:900;
  margin-top:auto;
  padding-top:18px;
}
.stock-info-dot {
  display:inline-flex;
  align-items:center;
  justify-content:center;
  width:18px;
  height:18px;
  flex:0 0 18px;
  border-radius:50%;
  border:1px solid #b8c7da;
  color:#315f9f;
  background:#f6f9fd;
  font-size:.70rem;
  font-weight:900;
  text-transform:none;
  cursor:help;
}
.stock-focus-panel {
  scroll-margin-top:18px;
}
@media (max-width: 1100px) {
  .stock-summary-grid,
  .live-trade-summary-grid { grid-template-columns:repeat(3,minmax(0,1fr)); }
}
@media (max-width: 700px) {
  .block-container { padding-top: 1rem; padding-left: .7rem; padding-right: .7rem; }
  h1 { font-size: 1.8rem !important; }
  div[data-testid="stMetricValue"] { font-size: 1.3rem; }
  .stock-home-actions-grid { grid-template-columns:1fr; }
  .stock-home-action-card { min-height:0; }
  .stock-summary-grid,
  .live-trade-summary-grid { grid-template-columns:repeat(2,minmax(0,1fr)); }
}
</style>
""", unsafe_allow_html=True)

@st.cache_data(ttl=300, show_spinner=False)
def load_stock_freshness_summary() -> Dict[str, str]:
    def _read_json(path: Path) -> dict:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    trade_fund = _read_json(TRADE_PREPARED_DIR / "manifest.json")
    trade_tech = _read_json(TRADE_TECHNICAL_DIR / "manifest.json")
    investment = _read_json(PREPARED_SCAN_DIR / "manifest.json")
    changes = _read_json(TRADE_PREPARED_DIR / "fundamental_changes.json")

    return {
        "trade_fundamentals": str(trade_fund.get("updated_at") or ""),
        "trade_technicals": str(trade_tech.get("updated_at") or ""),
        "investment_prices": str(investment.get("price_refreshed_at") or ""),
        "fundamental_check": str(changes.get("generated_at") or ""),
    }


def format_freshness_time(value: str) -> str:
    if not value:
        return "not yet available"
    try:
        ts = pd.Timestamp(value)
        if ts.tzinfo is None:
            ts = ts.tz_localize("UTC")
        ts = ts.tz_convert("Europe/London")
        return ts.strftime("%d %b %Y %H:%M")
    except Exception:
        return str(value)


@st.cache_data(ttl=270, show_spinner=False)
def fetch_live_trade_quotes(symbols_tuple: tuple[str, ...]) -> Dict[str, Dict]:
    """Fetch a light 5-minute quote overlay without recalculating daily indicators."""
    symbols = [str(symbol).strip().upper() for symbol in symbols_tuple if str(symbol).strip()]
    output: Dict[str, Dict] = {}
    if not symbols:
        return output

    now_utc = pd.Timestamp.now(tz="UTC")
    for start in range(0, len(symbols), 80):
        chunk = symbols[start:start + 80]
        try:
            data = yf.download(
                tickers=chunk,
                period="1d",
                interval="5m",
                group_by="ticker",
                auto_adjust=False,
                threads=True,
                progress=False,
            )
        except Exception:
            continue

        for symbol in chunk:
            try:
                frame = extract_ticker_frame(data, symbol)
                if frame is None or frame.empty:
                    continue
                frame = frame.copy()
                for column in ("Open", "Close"):
                    if column in frame.columns:
                        frame[column] = pd.to_numeric(frame[column], errors="coerce")
                frame = frame.dropna(subset=["Open", "Close"])
                if frame.empty:
                    continue

                timestamp = pd.Timestamp(frame.index[-1])
                if timestamp.tzinfo is None:
                    timestamp = timestamp.tz_localize("UTC")
                else:
                    timestamp = timestamp.tz_convert("UTC")

                age_minutes = max((now_utc - timestamp).total_seconds() / 60.0, 0.0)
                output[symbol] = {
                    "price": safe(frame["Close"].iloc[-1]),
                    "session_open": safe(frame["Open"].iloc[0]),
                    "quote_time": timestamp.isoformat(),
                    "age_minutes": round(age_minutes, 1),
                    # Allows common delayed feeds while still preventing old-session
                    # quotes from changing an intraday state.
                    "fresh": age_minutes <= 35.0,
                }
            except Exception:
                continue
    return output


def _overnight_trade_state(row: pd.Series) -> str:
    technical_state = str(row.get("Technical state") or "").upper()
    if technical_state == "WATCH":
        return "WATCH"
    if technical_state in {"AWAITING NEXT OPEN", "ENTRY READY"}:
        return "READY TO VERIFY"
    return str(row.get("Status") or "WATCH").upper()


def apply_live_trade_overlay(
    frame: pd.DataFrame,
    quotes: Dict[str, Dict],
) -> pd.DataFrame:
    """Overlay live price location on the completed-daily-candle Trade setup.

    This deliberately never recalculates RSI, MACD, moving averages or candle
    confirmation intraday. Those remain overnight/close-of-market decisions.
    """
    if frame.empty:
        return frame.copy()

    out = frame.copy()
    live_rows = []
    for _, row in out.iterrows():
        symbol = str(row.get("Ticker") or "").upper()
        quote = quotes.get(symbol)
        baseline = _overnight_trade_state(row)

        live_state = baseline
        live_reason = "Overnight completed-candle setup retained."
        live_price = np.nan
        session_open = np.nan
        quote_time = ""
        quote_status = "DATA STALE"
        remaining_upside = np.nan
        live_rr = np.nan

        if not quote:
            live_state = "DATA STALE"
            live_reason = "No usable 5-minute quote was returned; overnight setup is not being promoted intraday."
        else:
            live_price = safe(quote.get("price"))
            session_open = safe(quote.get("session_open"))
            quote_time = str(quote.get("quote_time") or "")
            is_fresh = bool(quote.get("fresh"))
            quote_status = "LIVE / DELAYED" if is_fresh else "LATEST SESSION"

            if not is_fresh:
                live_reason = "Latest intraday quote is from an older session; overnight setup retained."
            elif not math.isfinite(live_price) or live_price <= 0:
                live_state = "DATA STALE"
                live_reason = "Latest quote is unusable; overnight setup is not being promoted intraday."
            else:
                technical_state = str(row.get("Technical state") or "").upper()
                entry = safe(row.get("Entry"))
                stop = safe(row.get("Stop"))
                target = safe(row.get("Target"))
                atr20 = safe(row.get("ATR20"))
                zone_high = safe(row.get("MA zone high"))
                confirmation = safe(row.get("Price"))

                if math.isfinite(stop) and live_price <= stop:
                    live_state = "INVALIDATED"
                    live_reason = "Current price is at or below the overnight structural invalidation level."
                elif technical_state == "WATCH":
                    live_state = "WATCH"
                    live_reason = "WATCH remains WATCH intraday; a completed daily MACD crossover is still required."
                elif technical_state == "AWAITING NEXT OPEN":
                    needed = [session_open, stop, target, atr20, zone_high, confirmation]
                    if not all(math.isfinite(value) for value in needed) or atr20 <= 0:
                        live_state = "READY TO VERIFY"
                        live_reason = "Valid daily close is awaiting opening-entry checks; full live setup fields are not yet available."
                    else:
                        gap_atr = abs(session_open - confirmation) / atr20
                        stop_distance = (session_open - stop) / session_open if session_open > 0 else np.nan
                        remaining_upside = (target - session_open) / session_open * 100 if session_open > 0 else np.nan
                        risk = session_open - stop
                        live_rr = (target - session_open) / risk if risk > 0 else np.nan

                        if session_open <= stop:
                            live_state = "INVALIDATED"
                            live_reason = "The opening price was at or below the structural stop."
                        elif session_open > zone_high + atr20:
                            live_state = "EXTENDED"
                            live_reason = "The opening price is more than 1 ATR above the approved MA support zone."
                        elif gap_atr > 0.5:
                            live_state = "EXTENDED" if session_open > confirmation else "INVALIDATED"
                            live_reason = "The opening gap exceeds the approved 0.5 ATR limit."
                        elif stop_distance > 0.10:
                            live_state = "INVALIDATED"
                            live_reason = "The opening price would require a structural stop wider than 10%."
                        elif remaining_upside < 10 or not math.isfinite(live_rr) or live_rr < 2:
                            live_state = "EXTENDED"
                            live_reason = "At the opening price, remaining upside / reward-risk no longer meets the entry gates."
                        else:
                            live_state = "READY TO VERIFY"
                            live_reason = "Opening-entry checks still pass; verify current event/news conditions before acting."
                elif technical_state == "ENTRY READY":
                    if math.isfinite(target) and live_price > 0:
                        remaining_upside = (target - live_price) / live_price * 100
                    if math.isfinite(stop) and math.isfinite(target):
                        risk = live_price - stop
                        live_rr = (target - live_price) / risk if risk > 0 else np.nan

                    if math.isfinite(target) and live_price >= target:
                        live_state = "EXTENDED"
                        live_reason = "Price has already reached or exceeded the planned technical target; do not chase."
                    elif (
                        math.isfinite(remaining_upside) and remaining_upside < 10
                    ) or (
                        math.isfinite(live_rr) and live_rr < 2
                    ):
                        live_state = "EXTENDED"
                        live_reason = "Current price no longer offers the approved 10% upside / 2:1 reward-risk for a new entry."
                    elif math.isfinite(entry) and math.isfinite(atr20) and atr20 > 0:
                        if abs(live_price - entry) <= 0.5 * atr20:
                            live_state = "ENTRY ZONE"
                            live_reason = "Current price remains within 0.5 ATR of the approved following-open entry."
                        elif live_price > entry + 0.5 * atr20:
                            live_state = "EXTENDED"
                            live_reason = "Current price has moved more than 0.5 ATR above the approved entry."
                        else:
                            live_state = "READY TO VERIFY"
                            live_reason = "Price is below the planned entry but above invalidation; verify the setup before acting."
                    else:
                        live_state = "READY TO VERIFY"
                        live_reason = "Overnight entry setup remains valid; verify the current price before acting."

        live_rows.append({
            "Live state": live_state,
            "Live price": live_price,
            "Session open": session_open,
            "Live quote status": quote_status,
            "Live quote time": quote_time,
            "Remaining upside %": remaining_upside,
            "Live R:R": live_rr,
            "Live reason": live_reason,
        })

    return pd.concat([out.reset_index(drop=True), pd.DataFrame(live_rows)], axis=1)


@st.fragment(run_every="5m")
def render_live_trade_monitor(key_prefix: str = "home", show_table: bool = True) -> None:
    trade_frame = load_all_trade_opportunities()
    watchlist = load_browser_watchlist()
    active_symbols = (
        trade_frame["Ticker"].dropna().astype(str).str.upper().tolist()
        if not trade_frame.empty and "Ticker" in trade_frame.columns else []
    )
    symbols = tuple(dict.fromkeys(active_symbols + [str(x).upper() for x in watchlist]))

    st.markdown("#### ⚡ Live Trade monitor")
    if st.button(
        "Refresh live prices",
        key=f"{key_prefix}_refresh_live_trade",
        use_container_width=False,
    ):
        fetch_live_trade_quotes.clear()

    if not symbols:
        st.info("No active Trade candidates or watchlist companies need live monitoring right now.")
        return

    quotes = fetch_live_trade_quotes(symbols)
    live = apply_live_trade_overlay(trade_frame, quotes) if not trade_frame.empty else pd.DataFrame()

    if live.empty:
        st.info("No overnight Trade setups are active. Watchlist quotes are still refreshed in the Watchlist tab.")
        return

    counts = live["Live state"].value_counts()
    live_focus = _query_param_text("live_trade_focus").strip().upper()
    allowed_live_focus = {
        "ENTRY ZONE", "READY TO VERIFY", "WATCH", "EXTENDED", "INVALIDATED"
    }
    if live_focus not in allowed_live_focus:
        live_focus = ""

    live_cards = [
        (
            "ENTRY ZONE",
            "Entry Zone",
            int(counts.get("ENTRY ZONE", 0)),
            "Current price is still inside the approved live entry tolerance. This is the closest live state to an actionable setup, but current news/event checks still matter.",
        ),
        (
            "READY TO VERIFY",
            "Ready to Verify",
            int(counts.get("READY TO VERIFY", 0)),
            "The completed-candle setup remains valid, but the current live price or opening conditions still need verification before a new entry.",
        ),
        (
            "WATCH",
            "Trades to Watch",
            int(counts.get("WATCH", 0)),
            "The setup is developing, but a required completed-daily-candle confirmation has not happened yet.",
        ),
        (
            "EXTENDED",
            "Extended",
            int(counts.get("EXTENDED", 0)),
            "Price has moved too far from the approved entry, or the remaining upside / reward-risk no longer meets the Trade gates. Do not chase.",
        ),
        (
            "INVALIDATED",
            "Invalidated",
            int(counts.get("INVALIDATED", 0)),
            "The live price or opening conditions have broken the current setup's structural risk rules, so the setup is no longer valid for a new entry.",
        ),
    ]

    card_html = []
    for state, label, count, help_text in live_cards:
        active_class = " is-active" if live_focus == state else ""
        card_html.append(
            f'<a class="stock-summary-card{active_class}" '
            f'href="{stock_live_trade_href(state)}" target="_self" aria-label="View {label}">'
            f'<div class="stock-summary-label-row">'
            f'<span>{label}</span>'
            f'<span class="stock-info-dot" title="{help_text}">?</span>'
            f'</div>'
            f'<div class="stock-summary-value">{count}</div>'
            f'</a>'
        )

    st.caption("Click any live status to see the companies currently in that group.")
    live_cards_markup = '<div class="live-trade-summary-grid">' + "".join(card_html) + "</div>"
    st.markdown(live_cards_markup, unsafe_allow_html=True)

    quote_times = [
        pd.Timestamp(value.get("quote_time"))
        for value in quotes.values()
        if value.get("quote_time") and value.get("fresh")
    ]
    if quote_times:
        latest_quote = max(quote_times)
        if latest_quote.tzinfo is None:
            latest_quote = latest_quote.tz_localize("UTC")
        latest_quote = latest_quote.tz_convert("Europe/London")
        quote_text = latest_quote.strftime("%d %b %Y %H:%M")
    else:
        quote_text = "latest market session"

    stale_count = int(counts.get("DATA STALE", 0))
    st.caption(
        f"Live/delayed quote layer: {quote_text} · refreshes every 5 minutes while the app is open · "
        "daily RSI/MACD/SMA/candle rules remain locked to the completed-candle scan."
        + (f" · {stale_count} candidate{'s' if stale_count != 1 else ''} have no usable live quote." if stale_count else "")
    )

    if show_table:
        live_cols = [
            "Live state", "Ticker", "Company", "Exchange", "Technical state",
            "Live price", "Entry", "Stop", "Target", "Remaining upside %",
            "Live R:R", "Live quote status", "Live reason",
        ]
        visible = [column for column in live_cols if column in live.columns]
        display = live[visible].copy()
        order = {
            "ENTRY ZONE": 0,
            "READY TO VERIFY": 1,
            "WATCH": 2,
            "EXTENDED": 3,
            "INVALIDATED": 4,
            "DATA STALE": 5,
        }
        display["_order"] = display["Live state"].map(order).fillna(9)
        display = display.sort_values(
            ["_order", "Technical score"] if "Technical score" in display.columns else ["_order"]
        ).drop(columns="_order")

        st.markdown('<div id="live-trade-focus"></div>', unsafe_allow_html=True)
        if live_focus:
            selected = display[display["Live state"] == live_focus].copy()
            selected_label = next(
                (label for state, label, _, _ in live_cards if state == live_focus),
                live_focus.title(),
            )
            st.markdown(f"##### {selected_label}")
            if selected.empty:
                st.info(f"No companies are currently in {selected_label}.")
            else:
                styled = selected.style.map(action_cell_style, subset=["Live state"])
                st.dataframe(styled, hide_index=True, use_container_width=True)
        else:
            with st.expander("See all live Trade candidates", expanded=False):
                styled = display.style.map(action_cell_style, subset=["Live state"])
                st.dataframe(styled, hide_index=True, use_container_width=True)


@st.fragment(run_every="5m")
def render_live_watchlist_quotes() -> None:
    watchlist = load_browser_watchlist()
    if not watchlist:
        return
    symbols = tuple(dict.fromkeys(str(value).upper() for value in watchlist))
    quotes = fetch_live_trade_quotes(symbols)
    active = load_all_trade_opportunities()
    active_by_symbol = {
        str(row.get("Ticker") or "").upper(): row
        for row in active.to_dict(orient="records")
    } if not active.empty else {}

    rows = []
    for symbol in symbols:
        quote = quotes.get(symbol, {})
        active_row = active_by_symbol.get(symbol)
        rows.append({
            "Ticker": symbol,
            "Live price": safe(quote.get("price")),
            "Quote status": (
                "LIVE / DELAYED" if quote.get("fresh")
                else "LATEST SESSION" if quote
                else "DATA STALE"
            ),
            "Overnight Trade setup": (
                _overnight_trade_state(pd.Series(active_row))
                if active_row else "NO ACTIVE TRADE SETUP"
            ),
        })
    st.caption("Watchlist prices refresh every 5 minutes while this page is open.")
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)


@st.cache_data(ttl=300, show_spinner=False)
def load_all_trade_opportunities() -> pd.DataFrame:
    """Combine prepared Trade technicals across exchanges for a simple user-facing opportunity feed."""
    frames = []
    labels_by_kind = {kind: label for label, kind in EXCHANGE_UNIVERSES.items()}
    for kind, label in labels_by_kind.items():
        technical_path = TRADE_TECHNICAL_DIR / f"{kind}.csv.gz"
        fundamental_path = TRADE_PREPARED_DIR / f"{kind}.csv.gz"
        if not technical_path.exists():
            continue
        try:
            technical = pd.read_csv(technical_path, compression="gzip")
        except Exception:
            continue
        if technical.empty or "Ticker" not in technical.columns or "Technical state" not in technical.columns:
            continue

        technical = technical[
            technical["Technical state"].isin(["WATCH", "AWAITING NEXT OPEN", "ENTRY READY"])
        ].copy()
        if technical.empty:
            continue

        technical["Exchange"] = label
        technical["Status"] = technical["Technical state"].map(
            {
                "WATCH": "WATCH",
                "AWAITING NEXT OPEN": "READY TO VERIFY",
                "ENTRY READY": "READY TO VERIFY",
            }
        ).fillna("WATCH")

        if fundamental_path.exists():
            try:
                fundamentals = pd.read_csv(fundamental_path, compression="gzip")
                keep = [
                    column for column in
                    ["Ticker", "Company", "Sector", "Industry", "Currency"]
                    if column in fundamentals.columns
                ]
                if "Ticker" in keep:
                    fundamentals = fundamentals[keep].drop_duplicates("Ticker", keep="last")
                    technical = technical.merge(fundamentals, on="Ticker", how="left")
            except Exception:
                pass

        frames.append(technical)

    if not frames:
        return pd.DataFrame()

    output = pd.concat(frames, ignore_index=True)
    output["Company"] = output.get("Company", output["Ticker"]).fillna(output["Ticker"])
    output["Sector"] = output.get("Sector", pd.Series("—", index=output.index)).fillna("—")
    output["Technical score"] = pd.to_numeric(output.get("Technical score"), errors="coerce")
    output["Fundamental score"] = pd.to_numeric(output.get("Fundamental score"), errors="coerce")
    output["_status_order"] = output["Status"].map({"READY TO VERIFY": 0, "WATCH": 1}).fillna(9)
    output = output.sort_values(
        ["_status_order", "Technical score", "Fundamental score"],
        ascending=[True, False, False],
        na_position="last",
    ).drop(columns="_status_order")
    return output.reset_index(drop=True)


_LEGAL_COMPANY_SUFFIXES = {
    "inc", "incorporated", "corp", "corporation", "co", "company",
    "ltd", "limited", "plc", "ag", "aktiengesellschaft", "sa", "spa",
    "nv", "bv", "ab", "publ", "oyj", "asa", "pte", "pt", "tbk",
}


def canonical_company_key(name: str) -> str:
    """
    Conservative cross-listing key.

    Remove punctuation and trailing legal-form words only.  Do not remove
    business descriptors such as Group/Holdings, so related but separately
    listed companies are not accidentally merged.
    """
    tokens = re.findall(r"[a-z0-9]+", str(name or "").lower())

    # Punctuated legal forms such as S.A. / N.V. become separate one-letter
    # tokens under punctuation stripping, so collapse the common endings first.
    legal_pairs = {
        ("s", "a"), ("n", "v"), ("a", "s"), ("s", "p", "a"),
    }
    changed = True
    while changed and tokens:
        changed = False
        for pair in sorted(legal_pairs, key=len, reverse=True):
            if len(tokens) >= len(pair) and tuple(tokens[-len(pair):]) == pair:
                del tokens[-len(pair):]
                changed = True
                break

    while tokens and tokens[-1] in _LEGAL_COMPANY_SUFFIXES:
        tokens.pop()
    return "".join(tokens)


@st.cache_data(ttl=30, show_spinner=False)
def load_investment_validation_coverage() -> Dict:
    audit_path = PREPARED_SCAN_DIR / "investment_audit.json"
    priority_path = PRIORITY_INVESTMENT_DIR / "summary.json"

    audit = {}
    priority = {}
    try:
        if audit_path.exists():
            audit = json.loads(audit_path.read_text(encoding="utf-8"))
    except Exception:
        audit = {}
    try:
        if priority_path.exists():
            priority = json.loads(priority_path.read_text(encoding="utf-8"))
    except Exception:
        priority = {}

    totals = audit.get("totals", {}) if isinstance(audit, dict) else {}
    priority_counts = priority.get("counts", {}) if isinstance(priority, dict) else {}

    priority_rows = priority.get("rows", []) if isinstance(priority, dict) else []
    unique_buy_keys = set()
    for row in priority_rows:
        if str(row.get("Action") or "") != "BUY CANDIDATE":
            continue
        if safe(row.get("Quality score")) < MIN_INVESTMENT_QUALITY_SCORE:
            continue
        company_key = canonical_company_key(row.get("Company") or row.get("Ticker") or "")
        unique_buy_keys.add(company_key or str(row.get("Ticker") or ""))

    return {
        "prepared_rows": int(totals.get("rows", 0) or 0),
        "hard_gate_pass": int(totals.get("hard_gate_pass", 0) or 0),
        "old_buy_pool": int(totals.get("old_buy_candidate", 0) or 0),
        "old_wait_pool": int(totals.get("old_wait", 0) or 0),
        "fx_safe_revalued": int(priority.get("total", 0) or 0),
        "fx_safe_buys": sum(
            1 for row in priority_rows
            if str(row.get("Action") or "") == "BUY CANDIDATE"
            and safe(row.get("Quality score")) >= MIN_INVESTMENT_QUALITY_SCORE
        ),
        "fx_safe_unique_buys": len(unique_buy_keys),
        "fx_safe_waits": int(priority_counts.get("WAIT", 0) or 0),
    }


@st.cache_data(ttl=30, show_spinner=False)
def load_all_investment_opportunities() -> pd.DataFrame:
    """Combine prepared Investment results across exchanges into a simple opportunity feed."""
    frames = []
    labels_by_kind = {kind: label for label, kind in EXCHANGE_UNIVERSES.items()}
    for kind, label in labels_by_kind.items():
        path = PREPARED_SCAN_DIR / f"{kind}.csv.gz"
        if not path.exists():
            continue
        try:
            frame = pd.read_csv(path, compression="gzip")
        except Exception:
            continue

        # A small priority-revaluation lane can refresh the current shortlist
        # independently of the much larger market-wide backfill.  Priority rows
        # replace the same ticker from the broad prepared file.
        priority_path = PRIORITY_INVESTMENT_DIR / f"{kind}.csv.gz"
        if priority_path.exists():
            try:
                priority = pd.read_csv(priority_path, compression="gzip")
            except Exception:
                priority = pd.DataFrame()
            if not priority.empty and "Ticker" in priority.columns:
                priority_symbols = set(priority["Ticker"].dropna().astype(str))
                if "Ticker" in frame.columns:
                    frame = frame[
                        ~frame["Ticker"].astype(str).isin(priority_symbols)
                    ].copy()
                frame = pd.concat([frame, priority], ignore_index=True)

        if frame.empty or "Action" not in frame.columns:
            continue

        frame = frame[
            frame["Action"].isin(["BUY CANDIDATE", "WAIT", "REVALUE"])
            & frame.get("Hard gates", pd.Series("", index=frame.index)).eq("PASS")
        ].copy()
        if frame.empty:
            continue
        frame["Exchange"] = label
        frames.append(frame)

    if not frames:
        return pd.DataFrame()

    output = pd.concat(frames, ignore_index=True)
    output["Quality score"] = pd.to_numeric(output.get("Quality score"), errors="coerce")
    output["Moat score"] = pd.to_numeric(output.get("Moat score"), errors="coerce")
    output["Base margin of safety %"] = pd.to_numeric(
        output.get("Base margin of safety %"), errors="coerce"
    )
    output["Required margin of safety %"] = pd.to_numeric(
        output.get("Required margin of safety %"), errors="coerce"
    )
    # Positive means the achieved margin of safety exceeds the required hurdle.
    output["MOS gap %"] = (
        output["Base margin of safety %"] - output["Required margin of safety %"]
    )

    # Old prepared rows used a DCF that could compare different currencies.
    # Keep them visible as research candidates while the corrected model rebuilds,
    # but never allow stale/unverified valuation data to remain a BUY.
    if "Valuation model version" not in output.columns:
        output["Valuation model version"] = ""
    current_model = output["Valuation model version"].astype(str).eq(VALUATION_MODEL_VERSION)

    if "Valuation FX status" not in output.columns:
        output["Valuation FX status"] = ""
    fx_pass = output["Valuation FX status"].astype(str).str.upper().eq("PASS")

    stale_or_invalid = ~(current_model & fx_pass)

    # Enforce the new quality-first rule immediately even on already prepared
    # FX-safe rows, so stale action labels cannot remain BUY until the next scan.
    low_quality = (
        current_model
        & fx_pass
        & output["Quality score"].lt(MIN_INVESTMENT_QUALITY_SCORE)
    )
    output.loc[low_quality, "Action"] = "PASS"
    output.loc[low_quality, "Valuation gate"] = "PASS"
    output.loc[low_quality, "Decision reason"] = (
        "investment quality score below the minimum 70 threshold"
    )

    output.loc[stale_or_invalid, "Action"] = "REVALUE"
    output.loc[stale_or_invalid, "Valuation gate"] = "REVALUE"
    output.loc[stale_or_invalid, "Valuation FX status"] = "REFRESH REQUIRED"
    output.loc[stale_or_invalid, "Base intrinsic value"] = np.nan
    output.loc[stale_or_invalid, "Bear intrinsic value"] = np.nan
    output.loc[stale_or_invalid, "Bull intrinsic value"] = np.nan
    output.loc[stale_or_invalid, "Base margin of safety %"] = np.nan
    output.loc[stale_or_invalid, "Bear margin of safety %"] = np.nan
    output.loc[stale_or_invalid, "MOS gap %"] = np.nan
    output.loc[stale_or_invalid, "Decision reason"] = (
        "FX-safe valuation refresh required; quality evidence retained, valuation decision withheld"
    )

    output["_action_order"] = output["Action"].map(
        {"BUY CANDIDATE": 0, "WAIT": 1, "REVALUE": 2}
    ).fillna(9)
    output["_company_key"] = output.apply(
        lambda row: canonical_company_key(
            row.get("Company") or row.get("Ticker") or ""
        ) or str(row.get("Ticker") or "").lower(),
        axis=1,
    )
    output["_fx_order"] = output.get(
        "Valuation FX status", pd.Series("", index=output.index)
    ).astype(str).str.upper().eq("PASS").map({True: 0, False: 1})
    output = output.sort_values(
        ["_action_order", "_fx_order", "Quality score", "Moat score", "MOS gap %"],
        ascending=[True, True, False, False, False],
        na_position="last",
    )

    # One underlying company should appear once even when OTC/Gettex/etc. expose
    # multiple secondary listings. Preserve the alternatives for execution choice.
    alternatives = (
        output.groupby("_company_key", dropna=False)["Ticker"]
        .apply(lambda s: ", ".join(dict.fromkeys(str(x) for x in s if str(x))))
        .to_dict()
    )
    output["Alternate listings"] = output["_company_key"].map(alternatives)
    output = output.drop_duplicates(subset=["_company_key"], keep="first")
    output = output[output["Action"].isin(["BUY CANDIDATE", "WAIT", "REVALUE"])].copy()
    output = output.drop(columns=["_action_order", "_fx_order", "_company_key"])
    return output.reset_index(drop=True)


def _query_param_text(name: str) -> str:
    try:
        value = st.query_params.get(name, "")
    except Exception:
        return ""
    if isinstance(value, list):
        value = value[-1] if value else ""
    return str(value or "")


def stock_home_focus_href(focus: str) -> str:
    """Build a same-page dashboard link without dropping browser-saved watchlist state."""
    params = {}
    try:
        for key, value in st.query_params.items():
            if key == "stock_focus":
                continue
            if isinstance(value, list):
                value = value[-1] if value else ""
            if value not in (None, ""):
                params[str(key)] = str(value)
    except Exception:
        params = {}

    focus_value = str(focus or "").strip()
    if focus_value:
        params["stock_focus"] = focus_value

    query = urlencode(params)
    return (f"?{query}" if query else "?") + "#stock-focus"


def stock_live_trade_href(live_state: str) -> str:
    """Link a live-monitor summary card to its filtered candidates without losing page state."""
    params = {}
    try:
        for key, value in st.query_params.items():
            if key == "live_trade_focus":
                continue
            if isinstance(value, list):
                value = value[-1] if value else ""
            if value not in (None, ""):
                params[str(key)] = str(value)
    except Exception:
        params = {}

    state = str(live_state or "").strip().upper()
    if state:
        params["live_trade_focus"] = state

    query = urlencode(params)
    return (f"?{query}" if query else "?") + "#live-trade-focus"


def _encode_browser_state(value) -> str:
    """Compact small watchlist state into the page URL so it survives reruns/redeploys."""
    try:
        raw = json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        compressed = zlib.compress(raw, 9)
        return base64.urlsafe_b64encode(compressed).decode("ascii").rstrip("=")
    except Exception:
        return ""


def _decode_browser_state(value: str, default):
    if not value:
        return default
    try:
        padded = value + "=" * (-len(value) % 4)
        raw = zlib.decompress(base64.urlsafe_b64decode(padded.encode("ascii")))
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return default


def load_browser_watchlist() -> List[str]:
    value = _decode_browser_state(_query_param_text("wl"), [])
    if not isinstance(value, list):
        return []
    clean = [str(item).strip() for item in value if str(item).strip()][:40]
    legacy = {item.upper() for item in clean}
    if legacy == {"FLNC", "SPCX"} and len(clean) == 2:
        return []
    return clean


def save_browser_watchlist(entries: List[str]) -> None:
    clean = [str(item).strip() for item in entries[:40] if str(item).strip()]
    if not clean:
        try:
            del st.query_params["wl"]
        except Exception:
            pass
        return
    token = _encode_browser_state(clean)
    if token:
        st.query_params["wl"] = token


def browser_watchlist_symbol_set() -> set[str]:
    return {
        str(item).strip().upper()
        for item in load_browser_watchlist()
        if str(item).strip()
    }


def set_browser_watchlist_symbol(symbol: str, enabled: bool) -> bool:
    """Add/remove one canonical ticker while preserving the rest of the saved watchlist."""
    ticker = str(symbol or "").strip().upper()
    if not ticker:
        return False
    current = load_browser_watchlist()
    current_by_upper = {str(item).strip().upper(): str(item).strip() for item in current if str(item).strip()}
    before = set(current_by_upper)
    if enabled:
        current_by_upper[ticker] = ticker
    else:
        current_by_upper.pop(ticker, None)
    after = set(current_by_upper)
    if before == after:
        return False
    ordered = [str(item).strip() for item in current if str(item).strip().upper() in current_by_upper]
    if enabled and ticker not in {item.upper() for item in ordered}:
        ordered.append(ticker)
    ordered = [item for item in ordered if item.upper() in current_by_upper]
    save_browser_watchlist(ordered)
    return True


@st.cache_data(ttl=3600, show_spinner=False)
def watchlist_company_name(symbol: str) -> str:
    """Best-effort company name for a saved ticker, preferring prepared data before live lookup."""
    ticker = str(symbol or "").strip().upper()
    if not ticker:
        return ""

    for directory in (TRADE_PREPARED_DIR, PREPARED_SCAN_DIR):
        try:
            for path in directory.glob("*.csv.gz"):
                frame = pd.read_csv(path, compression="gzip", usecols=lambda col: col in {"Ticker", "Company"})
                if frame.empty or "Ticker" not in frame.columns:
                    continue
                match = frame[frame["Ticker"].astype(str).str.upper().eq(ticker)]
                if not match.empty and "Company" in match.columns:
                    name = str(match.iloc[-1].get("Company") or "").strip()
                    if name and name.lower() != "nan":
                        return name
        except Exception:
            continue

    try:
        resolved = resolve_company_query(ticker)
        name = str(resolved.get("name") or "").strip()
        if name:
            return name
    except Exception:
        pass
    return ticker


def remove_browser_watchlist_symbol(symbol: str) -> bool:
    """Remove one ticker and clear its saved status snapshot; keep historical alert events."""
    ticker = str(symbol or "").strip().upper()
    if not ticker:
        return False
    changed = set_browser_watchlist_symbol(ticker, False)
    snapshot = load_browser_watch_status()
    if ticker in snapshot:
        snapshot.pop(ticker, None)
        save_browser_watch_status(snapshot)
        changed = True
    return changed


def render_watchlist_selector(
    frame: pd.DataFrame,
    key: str,
    column_config: Dict | None = None,
) -> pd.DataFrame:
    """Render a result table with an editable Watch checkbox and persist changes immediately."""
    if frame is None or frame.empty or "Ticker" not in frame.columns:
        st.dataframe(frame, hide_index=True, use_container_width=True)
        return frame

    shown = frame.copy().reset_index(drop=True)
    watched = browser_watchlist_symbol_set()
    shown.insert(
        0,
        "Watch",
        shown["Ticker"].astype(str).str.upper().isin(watched),
    )
    config = {
        "Watch": st.column_config.CheckboxColumn(
            "Watch",
            help="Tick to add this company to your watchlist; untick to remove it.",
            default=False,
        )
    }
    if column_config:
        config.update(column_config)

    edited = st.data_editor(
        shown,
        hide_index=True,
        use_container_width=True,
        disabled=[column for column in shown.columns if column != "Watch"],
        column_config=config,
        key=key,
        num_rows="fixed",
    )

    changed = False
    for _, row in edited.iterrows():
        ticker = str(row.get("Ticker") or "").strip().upper()
        desired = bool(row.get("Watch"))
        if ticker and ((ticker in watched) != desired):
            changed = set_browser_watchlist_symbol(ticker, desired) or changed
            if desired:
                watched.add(ticker)
            else:
                watched.discard(ticker)
    if changed:
        st.toast("Watchlist updated")
    return edited


def load_browser_watch_status() -> Dict:
    value = _decode_browser_state(_query_param_text("wls"), {})
    return value if isinstance(value, dict) else {}


def save_browser_watch_status(snapshot: Dict) -> None:
    compact = {
        str(symbol): {
            "trade": str(state.get("trade") or ""),
            "investment": str(state.get("investment") or ""),
        }
        for symbol, state in list(snapshot.items())[:40]
        if isinstance(state, dict)
    }
    token = _encode_browser_state(compact)
    if token:
        st.query_params["wls"] = token


def load_browser_watch_events() -> List[Dict]:
    value = _decode_browser_state(_query_param_text("wle"), [])
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)][-15:]


def save_browser_watch_events(events: List[Dict]) -> None:
    token = _encode_browser_state(events[-15:])
    if token:
        st.query_params["wle"] = token


def investment_watch_buy_price(row: Dict | None) -> float:
    """Highest simple valuation price that satisfies the base MOS and non-negative bear-case gates."""
    if not row or str(row.get("Hard gates") or "").upper() != "PASS":
        return np.nan
    base_value = safe(row.get("Base intrinsic value"))
    bear_value = safe(row.get("Bear intrinsic value"))
    required_mos = safe(row.get("Required margin of safety %"))
    if np.isnan(base_value) or np.isnan(required_mos):
        return np.nan
    base_limit = base_value * (1 - required_mos / 100)
    limits = [base_limit]
    if math.isfinite(bear_value) and bear_value > 0:
        limits.append(bear_value)
    valid = [value for value in limits if math.isfinite(value) and value > 0]
    return min(valid) if valid else np.nan


def watchlist_next_step(
    trade_result: Dict,
    trade_decision: Dict,
    investment_row: Dict | None,
    investment_summary: Dict,
) -> str:
    """Plain-English next action for a watchlist row."""
    notes = []
    trade_action = str(trade_decision.get("action") or "UNAVAILABLE").upper()
    if trade_action == "BUY":
        notes.append("Trade: setup is in a buy zone now")
    elif trade_action == "WAIT":
        classification = str(trade_result.get("classification") or "")
        if classification in {"Swing-to-hold", "Swing only"}:
            notes.append(
                f"Trade: wait for price {fmt_price(trade_result.get('preferred_low'))}–"
                f"{fmt_price(trade_result.get('preferred_high'))}"
            )
        elif classification == "Developing":
            notes.append("Trade: wait for the technical setup to strengthen")
        elif classification == "Core opportunity":
            notes.append("Trade: wait for a better entry signal")
        else:
            notes.append("Trade: no approved entry yet")

    investment_action = str(investment_summary.get("action") or "UNAVAILABLE").upper()
    if investment_action == "BUY CANDIDATE":
        notes.append("Investment: valuation gates pass; complete manual review")
    elif investment_action == "WAIT":
        buy_price = investment_watch_buy_price(investment_row)
        valuation_gate = str((investment_row or {}).get("Valuation gate") or "").upper()
        if valuation_gate != "PASS" and math.isfinite(buy_price):
            notes.append(f"Investment: wait for price at or below {fmt_price(buy_price)}")
        else:
            notes.append("Investment: further evidence/review required")
    elif investment_action == "PASS":
        notes.append("Investment: hard quality gate failed")

    return " · ".join(notes) or "No current action"


with st.sidebar:
    st.header("Stock Screener")
    st.write("**Trade Search:** technical setups, entries, targets and risk/reward.")
    st.write("**Investment Search:** 10-years-to-forever quality gates, resilience and DCF valuation.")
    st.divider()
    saved_watchlist = load_browser_watchlist()
    if not saved_watchlist and _query_param_text("wl"):
        save_browser_watchlist([])
    st.caption(f"Watchlist: **{len(saved_watchlist)}** compan{'y' if len(saved_watchlist) == 1 else 'ies'}")
    with st.expander("Add to watchlist manually", expanded=False):
        manual_watch_key = f"manual_watch_{_query_param_text('wl')[:12]}"
        manual_watch_text = st.text_area(
            "Company names or tickers",
            value=", ".join(saved_watchlist),
            key=manual_watch_key,
            help="Checkboxes beside companies are the easiest way to build the watchlist. This box is a fallback for manual entry.",
        )
        if st.button("Save manual watchlist", key="save_manual_watchlist", use_container_width=True):
            raw_entries = [
                value.strip()
                for value in manual_watch_text.replace("\n", ",").split(",")
                if value.strip()
            ]
            canonical = []
            for entry in raw_entries[:40]:
                resolved = resolve_company_query(entry)
                symbol = str(resolved.get("symbol") or entry).strip().upper()
                if symbol and symbol not in canonical:
                    canonical.append(symbol)
            save_browser_watchlist(canonical)
            st.success(f"Saved {len(canonical)} watchlist compan{'y' if len(canonical) == 1 else 'ies'}.")
    st.success("Broker-independent mode: ON")
    st.caption("No Trading 212 credentials are used or stored.")

tab_home, tab1, tab_opportunities, tab2, tab5, tab3, tab4 = st.tabs(
    [
        "Home",
        "Quick Analysis",
        "Opportunities",
        "Watchlist",
        "Portfolio",
        "Advanced Trade",
        "Advanced Investment",
    ]
)

with tab_home:
    home_trade = load_all_trade_opportunities()
    home_investment = load_all_investment_opportunities()
    home_watchlist = load_browser_watchlist()
    home_events = load_browser_watch_events()

    ready_count = (
        int((home_trade["Status"] == "READY TO VERIFY").sum())
        if not home_trade.empty and "Status" in home_trade.columns else 0
    )
    trade_watch_count = (
        int((home_trade["Status"] == "WATCH").sum())
        if not home_trade.empty and "Status" in home_trade.columns else 0
    )
    investment_buy_count = (
        int((home_investment["Action"] == "BUY CANDIDATE").sum())
        if not home_investment.empty and "Action" in home_investment.columns else 0
    )
    investment_wait_count = (
        int((home_investment["Action"] == "WAIT").sum())
        if not home_investment.empty and "Action" in home_investment.columns else 0
    )
    investment_refresh_count = (
        int((home_investment["Action"] == "REVALUE").sum())
        if not home_investment.empty and "Action" in home_investment.columns else 0
    )
    recent_alert_count = len(home_events)

    st.markdown("### What do you want to do?")
    st.caption("Choose a starting point. The whole card is clickable.")

    analyse_href = stock_home_focus_href("analyse")
    opportunities_href = stock_home_focus_href("opportunities")
    watchlist_href = stock_home_focus_href("watchlist")
    st.markdown(
        f"""
        <div class="stock-home-actions-grid">
          <a class="stock-home-action-card" href="{analyse_href}" target="_self" aria-label="Analyse a company">
            <div class="stock-home-action-title">🔎 Analyse a company</div>
            <div class="stock-home-action-copy">Search by company name or ticker and get the Trade and Investment decision first.</div>
            <div class="stock-home-action-cta">Analyse a company →</div>
          </a>
          <a class="stock-home-action-card" href="{opportunities_href}" target="_self" aria-label="Find opportunities">
            <div class="stock-home-action-title">🎯 Find opportunities</div>
            <div class="stock-home-action-copy">Jump straight to the latest Trade setups and long-term Investment candidates found by the screener.</div>
            <div class="stock-home-opportunity-counts">
              <span><strong>{ready_count}</strong> Trades to Buy</span>
              <span><strong>{investment_buy_count}</strong> Investments to Buy</span>
            </div>
            <div class="stock-home-action-cta">View opportunities →</div>
          </a>
          <a class="stock-home-action-card" href="{watchlist_href}" target="_self" aria-label="Open my watchlist">
            <div class="stock-home-action-title">⭐ My watchlist</div>
            <div class="stock-home-action-copy">See the companies you are tracking, their latest available prices and current Trade setup status.</div>
            <div class="stock-home-action-cta">Open watchlist →</div>
          </a>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.markdown("### Your Dashboard")
    st.caption("Click any category to jump directly to the companies behind the number.")

    st.markdown(
        f"""
        <div class="stock-summary-grid">
          <a class="stock-summary-card" href="{stock_home_focus_href('trade-buy')}" target="_self" aria-label="View Trades to Buy">
            <div class="stock-summary-label-row">
              <span>Trades to Buy</span>
              <span class="stock-info-dot" title="Prepared Trade setups at the ready-to-verify stage. Run Quick Analysis before acting so current price and event gates are checked.">?</span>
            </div>
            <div class="stock-summary-value">{ready_count}</div>
          </a>
          <a class="stock-summary-card" href="{stock_home_focus_href('trade-watch')}" target="_self" aria-label="View Trades to Watch">
            <div class="stock-summary-label-row">
              <span>Trades to Watch</span>
              <span class="stock-info-dot" title="Developing Trade setups that pass the prepared filters but have not yet reached the confirmation stage.">?</span>
            </div>
            <div class="stock-summary-value">{trade_watch_count}</div>
          </a>
          <a class="stock-summary-card" href="{stock_home_focus_href('investment-buy')}" target="_self" aria-label="View Investments to Buy">
            <div class="stock-summary-label-row">
              <span>Investments to Buy</span>
              <span class="stock-info-dot" title="Long-term candidates whose quality gates, FX-safe valuation and margin-of-safety requirements are currently passing.">?</span>
            </div>
            <div class="stock-summary-value">{investment_buy_count}</div>
          </a>
          <a class="stock-summary-card" href="{stock_home_focus_href('investment-watch')}" target="_self" aria-label="View Investments to Watch">
            <div class="stock-summary-label-row">
              <span>Investments to Watch</span>
              <span class="stock-info-dot" title="Quality candidates on WAIT or awaiting revaluation. They are worth monitoring but are not currently validated buys.">?</span>
            </div>
            <div class="stock-summary-value">{investment_wait_count + investment_refresh_count}</div>
          </a>
          <a class="stock-summary-card" href="{stock_home_focus_href('watchlist')}" target="_self" aria-label="View Watchlist">
            <div class="stock-summary-label-row">
              <span>Watchlist</span>
              <span class="stock-info-dot" title="Companies you have chosen to track so you can monitor what changes without starting the research again.">?</span>
            </div>
            <div class="stock-summary-value">{len(home_watchlist)}</div>
          </a>
          <a class="stock-summary-card" href="{stock_home_focus_href('alerts')}" target="_self" aria-label="View Recent Alerts">
            <div class="stock-summary-label-row">
              <span>Recent Alerts</span>
              <span class="stock-info-dot" title="Saved Trade or Investment status changes detected on your watchlist during previous refreshes.">?</span>
            </div>
            <div class="stock-summary-value">{recent_alert_count}</div>
          </a>
        </div>
        """,
        unsafe_allow_html=True,
    )

    stock_freshness = load_stock_freshness_summary()
    st.caption(
        "Data freshness · "
        f"Trade technicals: {format_freshness_time(stock_freshness['trade_technicals'])} · "
        f"Investment closing prices: {format_freshness_time(stock_freshness['investment_prices'])} · "
        f"Fundamental change check: {format_freshness_time(stock_freshness['fundamental_check'])}"
    )

    home_focus = _query_param_text("stock_focus").strip().lower()
    home_query = ""
    home_analyse = False

    st.markdown('<div id="stock-focus" class="stock-focus-panel"></div>', unsafe_allow_html=True)

    trade_focus_columns = [
        "Status", "Ticker", "Company", "Exchange", "Sector",
        "Technical reason", "Fundamental score", "Technical score",
        "Price", "Entry", "Stop", "Target", "R:R", "Upside %", "RSI",
    ]
    investment_focus_columns = [
        "Action", "Ticker", "Company", "Exchange", "Sector",
        "Price", "Quality score", "Moat score",
        "Base margin of safety %", "Required margin of safety %",
        "MOS gap %", "Valuation gate", "Valuation FX status", "Decision reason",
    ]

    if home_focus == "analyse":
        st.markdown("#### 🔎 Analyse a company")
        st.caption("Search by company name or ticker. You do not need to know the exchange code.")
        q1, q2 = st.columns([4, 1])
        with q1:
            home_query = st.text_input(
                "Company",
                value="",
                placeholder="e.g. Apple, AAPL, Rolls-Royce",
                key="home_company_search",
                label_visibility="collapsed",
            )
        with q2:
            home_analyse = st.button(
                "Analyse company",
                type="primary",
                use_container_width=True,
                key="home_analyse_button",
            )

    elif home_focus in {"trade-buy", "trade-watch"}:
        wanted_status = "READY TO VERIFY" if home_focus == "trade-buy" else "WATCH"
        heading = "Trades to Buy" if home_focus == "trade-buy" else "Trades to Watch"
        st.markdown(f"#### 🎯 {heading}")
        if home_focus == "trade-buy":
            st.caption("These prepared setups are at the ready-to-verify stage. Run Quick Analysis before acting.")
        else:
            st.caption("These setups are developing but have not reached confirmation.")
        shown_trade = (
            home_trade[home_trade["Status"] == wanted_status].copy()
            if not home_trade.empty and "Status" in home_trade.columns
            else pd.DataFrame()
        )
        if shown_trade.empty:
            st.info(f"No {heading.lower()} are available right now.")
        else:
            visible = [column for column in trade_focus_columns if column in shown_trade.columns]
            render_watchlist_selector(
                shown_trade[visible],
                key=f"home_focus_{home_focus}_{_query_param_text('wl')[:8]}",
            )

    elif home_focus in {"investment-buy", "investment-watch"}:
        if home_focus == "investment-buy":
            heading = "Investments to Buy"
            shown_investment = (
                home_investment[home_investment["Action"] == "BUY CANDIDATE"].copy()
                if not home_investment.empty and "Action" in home_investment.columns
                else pd.DataFrame()
            )
            st.markdown(f"#### 💼 {heading}")
            st.caption("These candidates currently pass the long-term quality and validated valuation gates.")
        else:
            heading = "Investments to Watch"
            shown_investment = (
                home_investment[home_investment["Action"].isin(["WAIT", "REVALUE"])].copy()
                if not home_investment.empty and "Action" in home_investment.columns
                else pd.DataFrame()
            )
            st.markdown(f"#### 👀 {heading}")
            st.caption("These candidates are worth monitoring but are not currently validated buys.")
        if shown_investment.empty:
            st.info(f"No {heading.lower()} are available right now.")
        else:
            visible = [column for column in investment_focus_columns if column in shown_investment.columns]
            render_watchlist_selector(
                shown_investment[visible],
                key=f"home_focus_{home_focus}_{_query_param_text('wl')[:8]}",
                column_config={
                    "Quality score": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.1f"),
                    "Moat score": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.1f"),
                    "Base margin of safety %": st.column_config.NumberColumn(format="%.1f%%"),
                    "Required margin of safety %": st.column_config.NumberColumn(format="%.1f%%"),
                    "MOS gap %": st.column_config.NumberColumn(format="%.1f%%"),
                },
            )

    elif home_focus == "opportunities":
        st.markdown("#### 🎯 Find opportunities")
        st.caption("The latest prepared Trade and Investment shortlists, brought together in one place.")
        home_trade_tab, home_investment_tab = st.tabs(["Trade opportunities", "Investment opportunities"])
        with home_trade_tab:
            if home_trade.empty:
                st.info("No prepared Trade opportunities are available right now.")
            else:
                home_trade_buy = int((home_trade["Status"] == "READY TO VERIFY").sum())
                home_trade_watch = int((home_trade["Status"] == "WATCH").sum())
                home_trade_markets = int(home_trade["Exchange"].nunique()) if "Exchange" in home_trade.columns else 0
                ht1, ht2, ht3, ht4 = st.columns(4)
                ht1.metric(
                    "Trades to Buy",
                    home_trade_buy,
                    help="Prepared Trade setups at the ready-to-verify stage. Run Quick Analysis before acting so the current price and event gates are checked.",
                )
                ht2.metric(
                    "Trades to Watch",
                    home_trade_watch,
                    help="Developing Trade setups that have passed the prepared filters but have not reached confirmation yet.",
                )
                ht3.metric(
                    "Total opportunities",
                    len(home_trade),
                    help="All current prepared Trade opportunities shown in this shortlist, including Buy and Watch statuses.",
                )
                ht4.metric(
                    "Markets represented",
                    home_trade_markets,
                    help="The number of different stock-market universes represented by the current Trade shortlist.",
                )
                visible = [column for column in trade_focus_columns if column in home_trade.columns]
                render_watchlist_selector(
                    home_trade[visible].head(50),
                    key=f"home_focus_opportunities_trade_{_query_param_text('wl')[:8]}",
                )
        with home_investment_tab:
            if home_investment.empty:
                st.info("No prepared Investment opportunities are available right now.")
            else:
                home_invest_buy = int((home_investment["Action"] == "BUY CANDIDATE").sum())
                home_invest_watch = int((home_investment["Action"] == "WAIT").sum())
                home_invest_revalue = int((home_investment["Action"] == "REVALUE").sum())
                home_invest_markets = int(home_investment["Exchange"].nunique()) if "Exchange" in home_investment.columns else 0
                hi1, hi2, hi3, hi4 = st.columns(4)
                hi1.metric(
                    "Investments to Buy",
                    home_invest_buy,
                    help="Long-term candidates whose quality gates, FX-safe valuation and margin-of-safety requirements are currently passing.",
                )
                hi2.metric(
                    "Investments to Watch",
                    home_invest_watch,
                    help="Quality candidates currently on WAIT because the valuation or another decision condition is not yet strong enough.",
                )
                hi3.metric(
                    "Revaluation pending",
                    home_invest_revalue,
                    help="Quality research is retained, but the valuation is being withheld until the FX-safe DCF refresh is complete.",
                )
                hi4.metric(
                    "Markets represented",
                    home_invest_markets,
                    help="The number of different stock-market universes represented by the current Investment shortlist.",
                )
                visible = [column for column in investment_focus_columns if column in home_investment.columns]
                render_watchlist_selector(
                    home_investment[visible].head(50),
                    key=f"home_focus_opportunities_investment_{_query_param_text('wl')[:8]}",
                    column_config={
                        "Quality score": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.1f"),
                        "Moat score": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.1f"),
                        "Base margin of safety %": st.column_config.NumberColumn(format="%.1f%%"),
                        "Required margin of safety %": st.column_config.NumberColumn(format="%.1f%%"),
                        "MOS gap %": st.column_config.NumberColumn(format="%.1f%%"),
                    },
                )

    elif home_focus == "watchlist":
        st.markdown("#### ⭐ My watchlist")
        if home_watchlist:
            home_watch_symbols = tuple(dict.fromkeys(str(value).upper() for value in home_watchlist))
            home_watch_quotes = fetch_live_trade_quotes(home_watch_symbols)
            home_trade_by_symbol = {
                str(row.get("Ticker") or "").upper(): row
                for row in home_trade.to_dict(orient="records")
            } if not home_trade.empty else {}
            home_watch_rows = []
            for symbol in home_watch_symbols:
                quote = home_watch_quotes.get(symbol, {})
                active_row = home_trade_by_symbol.get(symbol)
                home_watch_rows.append({
                    "Ticker": symbol,
                    "Company": watchlist_company_name(symbol),
                    "Live price": safe(quote.get("price")),
                    "Quote status": (
                        "LIVE / DELAYED" if quote.get("fresh")
                        else "LATEST SESSION" if quote
                        else "DATA STALE"
                    ),
                    "Trade setup": (
                        _overnight_trade_state(pd.Series(active_row))
                        if active_row else "NO ACTIVE TRADE SETUP"
                    ),
                })
            st.dataframe(pd.DataFrame(home_watch_rows), hide_index=True, use_container_width=True)
            st.caption("Use the dedicated Watchlist tab for a full status refresh, alert history and removals.")
        else:
            st.info("Your watchlist is empty. Tick Watch beside any company in an opportunity table or analysis to add it.")

    elif home_focus == "alerts":
        st.markdown("#### 🔔 Recent alerts")
        if home_events:
            for event in reversed(home_events):
                st.write(
                    f"**{event.get('time', '—')} · {event.get('type', 'STATUS')}** — "
                    f"{event.get('message', '')}"
                )
        else:
            st.info("No saved watchlist status changes yet.")

    render_live_trade_monitor("home", show_table=True)

    if home_analyse and home_query:
        resolved = resolve_company_query(home_query)
        home_symbol = str(resolved.get("symbol") or "").strip().upper()
        if not home_symbol:
            st.error("I couldn't match that company. Try a more specific company name or ticker.")
        else:
            home_name = str(resolved.get("name") or home_symbol)
            with st.spinner(f"Analysing {home_name} ({home_symbol})…"):
                home_res = analyse_symbol(home_symbol)
                home_inv_result = None
                home_inv_row = None
                if home_res:
                    home_inv_result = _investment_company_result(
                        home_symbol,
                        float(home_res["price"]),
                        0.0,
                        {},
                    )
                    if home_inv_result.get("status") == "row":
                        home_inv_row = home_inv_result.get("row")

            if home_res:
                st.markdown("---")
                title_col, watch_col = st.columns([5, 1])
                with title_col:
                    st.markdown(f"### {home_res.get('name') or home_name} ({home_symbol})")
                    st.caption(
                        f"Current price: {fmt_price_with_currency(home_res.get('price'), home_res.get('currency'))}"
                    )
                with watch_col:
                    home_is_watched = home_symbol in browser_watchlist_symbol_set()
                    home_watch = st.checkbox(
                        "Watch",
                        value=home_is_watched,
                        key=f"home_watch_{home_symbol}_{_query_param_text('wl')[:8]}",
                    )
                    if home_watch != home_is_watched:
                        set_browser_watchlist_symbol(home_symbol, home_watch)
                        st.toast("Added to watchlist" if home_watch else "Removed from watchlist")

                home_trade_decision = quick_trade_decision(home_res)
                home_investment_decision = quick_investment_decision(home_inv_row)
                d1, d2 = st.columns(2)
                with d1:
                    render_decision_card(
                        "TRADE DECISION",
                        home_trade_decision["action"],
                        home_trade_decision["reason"],
                    )
                with d2:
                    render_decision_card(
                        "INVESTMENT DECISION",
                        home_investment_decision["action"],
                        home_investment_decision["reason"],
                    )
                st.caption("Use Quick Analysis above if you want the full trade plan, valuation and detailed evidence.")
            else:
                st.error("I couldn't retrieve enough market data for that company.")

    st.markdown("### How it works")
    h1, h2, h3 = st.columns(3)
    with h1:
        st.markdown("**1 · Search a company**")
        st.caption("Use a company name or ticker. You do not need to know market codes.")
    with h2:
        st.markdown("**2 · See the decision**")
        st.caption("Trade and Investment decisions appear first; detailed evidence stays underneath.")
    with h3:
        st.markdown("**3 · Watch what matters**")
        st.caption("If it is not ready, add it to your watchlist and track what needs to change.")

with tab1:
    st.markdown("### Quick Analysis")
    st.caption("Full company analysis. Search by company name or ticker; the decision comes first and deeper evidence stays expandable.")

    c1, c2 = st.columns([4, 1])
    with c1:
        manual = st.text_input(
            "Search a company",
            value="",
            placeholder="e.g. Apple, AAPL, Rolls-Royce, RR.L",
            help="You can type either the company name or its ticker.",
        )
    with c2:
        st.write("")
        st.write("")
        analyse_clicked = st.button("Analyse company", type="primary", use_container_width=True)

    if analyse_clicked and manual:
        resolved = resolve_company_query(manual)
        symbol = str(resolved.get("symbol") or "").strip().upper()

        if not symbol:
            st.error("I couldn't match that company name to a listed share. Try a more specific company name or its ticker.")
        else:
            resolved_name = str(resolved.get("name") or symbol)
            resolved_exchange = str(resolved.get("exchange") or "")
            with st.spinner(f"Analysing {resolved_name} ({symbol})…"):
                res = analyse_symbol(symbol)
                investment_result = None
                investment_row = None
                if res:
                    investment_result = _investment_company_result(
                        symbol,
                        float(res["price"]),
                        0.0,
                        {},
                    )
                    if investment_result.get("status") == "row":
                        investment_row = investment_result.get("row")

            if res:
                currency = res.get("currency") or ""
                company_name = res.get("name") or resolved_name or symbol
                exchange_suffix = f" · {resolved_exchange}" if resolved_exchange else ""

                company_col, watch_col = st.columns([5, 1])
                with company_col:
                    st.subheader(f"{company_name} ({symbol})")
                    st.caption(
                        f"Current price: {fmt_price_with_currency(res['price'], currency)}{exchange_suffix}"
                    )
                with watch_col:
                    current_watch = symbol in browser_watchlist_symbol_set()
                    quick_watch = st.checkbox(
                        "Watch",
                        value=current_watch,
                        key=f"quick_watch_{symbol}_{_query_param_text('wl')[:8]}",
                        help="Add or remove this company from your watchlist.",
                    )
                    if quick_watch != current_watch:
                        set_browser_watchlist_symbol(symbol, quick_watch)
                        st.toast("Added to watchlist" if quick_watch else "Removed from watchlist")

                trade_decision = quick_trade_decision(res)
                investment_decision_summary = quick_investment_decision(investment_row)

                left, right = st.columns(2)
                with left:
                    render_decision_card(
                        "TRADE DECISION",
                        trade_decision["action"],
                        trade_decision["reason"],
                    )
                with right:
                    render_decision_card(
                        "INVESTMENT DECISION",
                        investment_decision_summary["action"],
                        investment_decision_summary["reason"],
                    )

                st.markdown("#### Trade plan")
                p1, p2, p3 = st.columns(3)
                p1.metric(
                    "Entry zone",
                    f"{fmt_price_with_currency(res['preferred_low'], currency)}–{fmt_price_with_currency(res['preferred_high'], currency)}",
                    help="The preferred price range for opening the trade.",
                )
                p2.metric(
                    "Profit target",
                    fmt_price_with_currency(res["swing_target"], currency),
                    f"{res['upside_pct']:.1f}% from current price",
                    help="The current modelled swing target.",
                )
                p3.metric(
                    "Downside / reassess",
                    fmt_price_with_currency(res["invalidation"], currency),
                    help="If price falls through this level, the current trade setup should be reassessed rather than held blindly.",
                )

                if res["in_preferred_zone"]:
                    st.success("Price is currently inside the preferred entry zone.")
                elif res["in_strong_zone"]:
                    st.success("Price is currently inside the deeper / strong entry zone.")

                if investment_row:
                    st.markdown("#### Investment valuation")
                    i1, i2, i3, i4 = st.columns(4)
                    i1.metric(
                        "Current price",
                        fmt_price_with_currency(investment_row.get("Price"), currency),
                    )
                    i2.metric(
                        "Base intrinsic value",
                        fmt_price_with_currency(investment_row.get("Base intrinsic value"), currency),
                    )
                    base_mos = safe(investment_row.get("Base margin of safety %"))
                    required_mos = safe(investment_row.get("Required margin of safety %"))
                    i3.metric(
                        "Current margin of safety",
                        "—" if np.isnan(base_mos) else f"{base_mos:.1f}%",
                    )
                    i4.metric(
                        "Required margin of safety",
                        "—" if np.isnan(required_mos) else f"{required_mos:.1f}%",
                    )
                elif investment_result:
                    status = str(investment_result.get("status") or "unavailable").replace("_", " ")
                    st.info(f"Investment analysis unavailable for this lookup ({status}). Trade analysis is still shown below.")

                with st.expander("Explore the trade analysis"):
                    a, b, c, d = st.columns(4)
                    a.metric("Trade setup", f"{res['trade_score']:.0f}/100")
                    b.metric("Hold quality", f"{res['hold_score']:.0f}/100")
                    c.metric("Opportunity", f"{res['opportunity_score']:.0f}/100")
                    d.metric("Classification", res["classification"])

                    e1, e2, e3, e4 = st.columns(4)
                    e1.metric(
                        "Deeper / strong entry",
                        f"{fmt_price_with_currency(res['strong_low'], currency)}–{fmt_price_with_currency(res['strong_high'], currency)}",
                    )
                    e2.metric("RSI", res["rsi"])
                    e3.metric("Risk / reward", f"{res['rr']:.2f}:1")
                    if not np.isnan(safe(res["analyst_target"])):
                        e4.metric(
                            "Analyst mean target",
                            fmt_price_with_currency(res["analyst_target"], currency),
                            f"{res['analyst_upside']:.1f}%",
                        )
                    else:
                        e4.metric("Analyst mean target", "—")

                    st.plotly_chart(chart(res), use_container_width=True)

                    rows = {
                        "Market cap": "—" if np.isnan(safe(res["market_cap"])) else f"{res['market_cap']/1e9:.1f}bn",
                        "Revenue growth": "—" if res["revenue_growth"] is None else f"{res['revenue_growth']:.1f}%",
                        "EPS growth": "—" if res["earnings_growth"] is None else f"{res['earnings_growth']:.1f}%",
                        "Profit margin": "—" if res["profit_margin"] is None else f"{res['profit_margin']:.1f}%",
                        "Debt / equity": "—" if np.isnan(safe(res["debt_equity"])) else f"{res['debt_equity']:.1f}%",
                        "Free cash flow": "—" if np.isnan(safe(res["free_cash_flow"])) else f"{res['free_cash_flow']/1e6:.1f}m",
                        "Dividend yield": "—" if res["dividend_yield"] is None else f"{res['dividend_yield']:.2f}%",
                        "Analysts": res["analyst_count"],
                    }
                    st.dataframe(
                        pd.DataFrame({"Metric": rows.keys(), "Value": rows.values()}),
                        hide_index=True,
                        use_container_width=True,
                    )

                if investment_row:
                    with st.expander("Explore the investment analysis"):
                        q1, q2, q3, q4 = st.columns(4)
                        quality_score = safe(investment_row.get("Quality score"))
                        moat_score = safe(investment_row.get("Moat score"))
                        q1.metric("Quality score", "—" if np.isnan(quality_score) else f"{quality_score:.0f}/100")
                        q2.metric("Hard gates", investment_row.get("Hard gates") or "—")
                        q3.metric("Valuation gate", investment_row.get("Valuation gate") or "—")
                        q4.metric("Quant moat confidence", investment_row.get("Quant moat confidence") or "—")

                        inv_rows = {
                            "Base intrinsic value": fmt_price_with_currency(investment_row.get("Base intrinsic value"), currency),
                            "Bear intrinsic value": fmt_price_with_currency(investment_row.get("Bear intrinsic value"), currency),
                            "Bull intrinsic value": fmt_price_with_currency(investment_row.get("Bull intrinsic value"), currency),
                            "Base margin of safety": "—" if np.isnan(safe(investment_row.get("Base margin of safety %"))) else f"{safe(investment_row.get('Base margin of safety %')):.1f}%",
                            "Required margin of safety": "—" if np.isnan(safe(investment_row.get("Required margin of safety %"))) else f"{safe(investment_row.get('Required margin of safety %')):.1f}%",
                            "Bear margin of safety": "—" if np.isnan(safe(investment_row.get("Bear margin of safety %"))) else f"{safe(investment_row.get('Bear margin of safety %')):.1f}%",
                            "ROIC": "—" if np.isnan(safe(investment_row.get("ROIC %"))) else f"{safe(investment_row.get('ROIC %')):.1f}%",
                            "FCF/share CAGR": "—" if np.isnan(safe(investment_row.get("FCF/share CAGR %"))) else f"{safe(investment_row.get('FCF/share CAGR %')):.1f}%",
                            "Hard-gate failures": investment_row.get("Hard-gate failures") or "None",
                            "Review flags": investment_row.get("Review flags") or "None",
                            "Manual review required": investment_row.get("Manual review required") or "None",
                        }
                        st.dataframe(
                            pd.DataFrame({"Metric": inv_rows.keys(), "Value": inv_rows.values()}),
                            hide_index=True,
                            use_container_width=True,
                        )
            else:
                st.error("I couldn't retrieve enough market history for that company.")

with tab_opportunities:
    st.subheader("Opportunities")
    st.caption(
        "The screener brings the prepared markets together for you. "
        "You do not need to choose an exchange unless you want to use the advanced searches."
    )

    trade_feed_tab, investment_feed_tab = st.tabs(
        ["Trade opportunities", "Investment opportunities"]
    )

    with trade_feed_tab:
        trade_opportunities = load_all_trade_opportunities()
        if trade_opportunities.empty:
            st.info(
                "No prepared Trade WATCH or ready-to-verify setups are available right now. "
                "That means the current rules are not finding a developing setup across the prepared markets."
            )
        else:
            ready_count = int((trade_opportunities["Status"] == "READY TO VERIFY").sum())
            watch_count = int((trade_opportunities["Status"] == "WATCH").sum())
            market_count = int(trade_opportunities["Exchange"].nunique())

            t1, t2, t3, t4 = st.columns(4)
            t1.metric(
                "Trades to Buy",
                ready_count,
                help="Prepared Trade setups at the ready-to-verify stage. Run Quick Analysis before acting so current price and event gates are checked.",
            )
            t2.metric(
                "Trades to Watch",
                watch_count,
                help="Developing Trade setups that pass the prepared filters but have not yet reached the confirmation stage.",
            )
            t3.metric(
                "Total opportunities",
                len(trade_opportunities),
                help="All current prepared Trade opportunities in this shortlist, including Buy and Watch statuses.",
            )
            t4.metric(
                "Markets represented",
                market_count,
                help="The number of different stock-market universes represented by the current Trade shortlist.",
            )

            st.caption(
                "READY TO VERIFY means the prepared technical setup has reached the confirmation stage. "
                "Run Quick Analysis or Advanced Trade Search before acting so current price and event gates are checked. "
                "WATCH means the setup is developing but is not ready yet."
            )

            trade_status_filter = st.radio(
                "Show",
                ["Best opportunities", "Ready to verify", "WATCH", "All"],
                horizontal=True,
                key="opportunity_trade_filter",
            )
            shown_trade = trade_opportunities.copy()
            if trade_status_filter == "Ready to verify":
                shown_trade = shown_trade[shown_trade["Status"] == "READY TO VERIFY"]
            elif trade_status_filter == "WATCH":
                shown_trade = shown_trade[shown_trade["Status"] == "WATCH"]
            elif trade_status_filter == "Best opportunities":
                shown_trade = shown_trade.head(25)

            trade_cols = [
                "Status", "Ticker", "Company", "Exchange", "Sector",
                "Technical reason", "Fundamental score", "Technical score",
                "Price", "Entry", "Stop", "Target", "R:R", "Upside %", "RSI",
            ]
            visible_trade_cols = [col for col in trade_cols if col in shown_trade.columns]
            render_watchlist_selector(
                shown_trade[visible_trade_cols],
                key=f"trade_opportunity_watch_{trade_status_filter}_{_query_param_text('wl')[:8]}",
            )
            st.caption(
                "For a specific company, use Quick Analysis from the first tab for the clearest current explanation."
            )

    with investment_feed_tab:
        investment_opportunities = load_all_investment_opportunities()
        if investment_opportunities.empty:
            st.info(
                "No prepared Investment BUY or WAIT opportunities with passing hard quality gates are available yet."
            )
        else:
            buy_count = int((investment_opportunities["Action"] == "BUY CANDIDATE").sum())
            wait_count = int((investment_opportunities["Action"] == "WAIT").sum())
            refresh_count = int((investment_opportunities["Action"] == "REVALUE").sum())
            investment_market_count = int(investment_opportunities["Exchange"].nunique())

            i1, i2, i3, i4 = st.columns(4)
            i1.metric(
                "Investments to Buy",
                buy_count,
                help="Long-term candidates whose quality gates, FX-safe valuation and margin-of-safety requirements are currently passing.",
            )
            i2.metric(
                "Investments to Watch",
                wait_count,
                help="Quality candidates currently on WAIT because the valuation or another decision condition is not yet strong enough.",
            )
            i3.metric(
                "Revaluation pending",
                refresh_count,
                help="Quality research is retained, but the valuation is being withheld until the FX-safe DCF refresh is complete.",
            )
            i4.metric(
                "Markets represented",
                investment_market_count,
                help="The number of different stock-market universes represented by the current Investment shortlist.",
            )

            validation = load_investment_validation_coverage()
            if validation.get("prepared_rows"):
                st.caption(
                    "Validation coverage · "
                    f"{validation['prepared_rows']:,} prepared companies · "
                    f"{validation['hard_gate_pass']:,} pass hard quality gates · "
                    f"{validation['old_buy_pool']:,} prior BUY candidates awaiting/under FX-safe review · "
                    f"{validation['fx_safe_revalued']:,} priority names currently FX-safe revalued · "
                    f"{validation.get('fx_safe_unique_buys', validation['fx_safe_buys']):,} validated unique BUY"
                    f"{'s' if validation.get('fx_safe_unique_buys', validation['fx_safe_buys']) != 1 else ''} "
                    f"({validation['fx_safe_buys']:,} listing-level BUY rows)."
                )

            st.caption(
                "BUY CANDIDATE requires Investment Quality 70+, the measurable hard gates, FX-safe DCF valuation and margin-of-safety gate to pass. "
                "Quality comes before valuation: cheapness cannot rescue a sub-70 business. WAIT is a current, validated valuation that is not yet cheap enough or needs review. "
                "REVALUE means the quality research is retained but the old valuation is deliberately withheld until the FX-safe DCF refresh completes."
            )

            investment_filter = st.radio(
                "Show",
                ["Best opportunities", "BUY CANDIDATE", "WAIT", "REVALUE", "All"],
                horizontal=True,
                key="opportunity_investment_filter",
            )
            shown_investment = investment_opportunities.copy()
            if investment_filter == "BUY CANDIDATE":
                shown_investment = shown_investment[shown_investment["Action"] == "BUY CANDIDATE"]
            elif investment_filter == "WAIT":
                shown_investment = shown_investment[shown_investment["Action"] == "WAIT"]
            elif investment_filter == "REVALUE":
                shown_investment = shown_investment[shown_investment["Action"] == "REVALUE"]
            elif investment_filter == "Best opportunities":
                validated = shown_investment[
                    shown_investment["Action"].isin(["BUY CANDIDATE", "WAIT"])
                ]
                shown_investment = validated.head(25) if not validated.empty else shown_investment.head(25)

            investment_cols = [
                "Action", "Ticker", "Company", "Exchange", "Sector",
                "Price", "Quality score", "Moat score",
                "Base margin of safety %", "Required margin of safety %",
                "MOS gap %", "Valuation gate", "Valuation FX status",
                "Financial currency", "Quote currency", "Alternate listings", "Decision reason",
            ]
            visible_investment_cols = [
                col for col in investment_cols if col in shown_investment.columns
            ]
            render_watchlist_selector(
                shown_investment[visible_investment_cols],
                key=f"investment_opportunity_watch_{investment_filter}_{_query_param_text('wl')[:8]}",
                column_config={
                    "Quality score": st.column_config.ProgressColumn(
                        min_value=0, max_value=100, format="%.1f"
                    ),
                    "Moat score": st.column_config.ProgressColumn(
                        min_value=0, max_value=100, format="%.1f"
                    ),
                    "Base margin of safety %": st.column_config.NumberColumn(format="%.1f%%"),
                    "Required margin of safety %": st.column_config.NumberColumn(format="%.1f%%"),
                    "MOS gap %": st.column_config.NumberColumn(format="%.1f%%"),
                },
            )
            st.caption(
                "Prepared Investment results are a market-wide shortlist. Use Quick Analysis before making a decision so the company is refreshed individually."
            )


with tab2:
    st.subheader("Watchlist")
    st.caption(
        "Track companies you care about in plain English. The watchlist shows the current decision, "
        "what price or signal you are waiting for, and whether the status changed since your previous saved refresh."
    )

    watch_entries = load_browser_watchlist()

    if watch_entries:
        render_live_watchlist_quotes()

    refresh_watchlist = False
    if not watch_entries:
        st.info("Your watchlist is empty. Add a company by ticking **Watch** beside it anywhere in the screener.")
    else:
        refresh_watchlist = st.button(
            "Refresh watchlist",
            type="primary",
            use_container_width=True,
            key="refresh_watchlist_top",
        )

    if watch_entries and refresh_watchlist:
        rows = []
        failed = []
        total = len(watch_entries)
        prog = st.progress(0.0, text=f"Checking 0 of {total:,}")

        save_browser_watchlist(watch_entries)
        previous_snapshot = load_browser_watch_status()
        previous_events = load_browser_watch_events()
        current_snapshot = dict(previous_snapshot)
        new_alerts = []

        for index, raw_entry in enumerate(watch_entries, start=1):
            try:
                resolved = resolve_company_query(raw_entry)
                symbol = str(resolved.get("symbol") or "").strip().upper()
                if not symbol:
                    failed.append(raw_entry)
                    prog.progress(index / max(total, 1), text=f"Checking {index:,} of {total:,}")
                    continue

                trade_result = analyse_symbol(symbol)
                if not trade_result:
                    failed.append(raw_entry)
                    prog.progress(index / max(total, 1), text=f"Checking {index:,} of {total:,}")
                    continue

                trade_summary = quick_trade_decision(trade_result)
                investment_result = _investment_company_result(
                    symbol,
                    float(trade_result["price"]),
                    0.0,
                    {},
                )
                investment_row = (
                    investment_result.get("row")
                    if investment_result.get("status") == "row"
                    else None
                )
                investment_summary = quick_investment_decision(investment_row)

                trade_action = str(trade_summary.get("action") or "UNAVAILABLE").upper()
                investment_action = str(investment_summary.get("action") or "UNAVAILABLE").upper()
                current_state = {
                    "trade": trade_action,
                    "investment": investment_action,
                }
                current_snapshot[symbol] = current_state

                prior = previous_snapshot.get(symbol)
                if prior is None:
                    change = "NEW"
                elif prior != current_state:
                    change = (
                        f"{prior.get('trade', '—')}/{prior.get('investment', '—')} → "
                        f"{trade_action}/{investment_action}"
                    )
                else:
                    change = "No change"

                if prior is not None:
                    if str(prior.get("trade") or "") != trade_action:
                        new_alerts.append({
                            "time": pd.Timestamp.now(tz="Europe/London").strftime("%d %b %H:%M"),
                            "ticker": symbol,
                            "type": "TRADE",
                            "message": f"{symbol} Trade changed {prior.get('trade', '—')} → {trade_action}",
                        })
                    if str(prior.get("investment") or "") != investment_action:
                        new_alerts.append({
                            "time": pd.Timestamp.now(tz="Europe/London").strftime("%d %b %H:%M"),
                            "ticker": symbol,
                            "type": "INVESTMENT",
                            "message": f"{symbol} Investment changed {prior.get('investment', '—')} → {investment_action}",
                        })

                currency = str(trade_result.get("currency") or "")
                investment_buy_price = investment_watch_buy_price(investment_row)

                rows.append({
                    "Change": change,
                    "Company": trade_result.get("name") or resolved.get("name") or symbol,
                    "Ticker": symbol,
                    "Trade": trade_action,
                    "Investment": investment_action,
                    "Current price": fmt_price_with_currency(trade_result.get("price"), currency),
                    "Trade buy zone": (
                        f"{fmt_price_with_currency(trade_result.get('preferred_low'), currency)}–"
                        f"{fmt_price_with_currency(trade_result.get('preferred_high'), currency)}"
                    ),
                    "Trade target": fmt_price_with_currency(trade_result.get("swing_target"), currency),
                    "Reassess below": fmt_price_with_currency(trade_result.get("invalidation"), currency),
                    "Investment buy price": (
                        "—"
                        if not math.isfinite(investment_buy_price)
                        else fmt_price_with_currency(investment_buy_price, currency)
                    ),
                    "What are we waiting for?": watchlist_next_step(
                        trade_result,
                        trade_summary,
                        investment_row,
                        investment_summary,
                    ),
                })
            except Exception:
                failed.append(raw_entry)

            prog.progress(
                index / max(total, 1),
                text=f"Checking {index:,} of {total:,}",
            )

        prog.empty()
        save_browser_watch_status(current_snapshot)
        if new_alerts:
            save_browser_watch_events(previous_events + new_alerts)

        if rows:
            watch_frame = pd.DataFrame(rows)
            trade_buy_count = int((watch_frame["Trade"] == "BUY").sum())
            investment_buy_count = int((watch_frame["Investment"] == "BUY CANDIDATE").sum())
            changed_count = int(
                watch_frame["Change"].astype(str).str.contains("→", regex=False).sum()
            )

            w1, w2, w3, w4 = st.columns(4)
            w1.metric("Watching", len(watch_frame))
            w2.metric("Trade BUY", trade_buy_count)
            w3.metric("Investment BUY", investment_buy_count)
            w4.metric("New alerts", len(new_alerts))

            if new_alerts:
                st.success(
                    f"{len(new_alerts)} watchlist status change{'s' if len(new_alerts) != 1 else ''} detected."
                )
                with st.expander("New alerts", expanded=True):
                    for alert in new_alerts:
                        st.write(f"**{alert['time']} · {alert['type']}** — {alert['message']}")

            recent_events = load_browser_watch_events()
            if recent_events:
                with st.expander("Recent watchlist history", expanded=False):
                    for event in reversed(recent_events):
                        st.write(
                            f"**{event.get('time', '—')} · {event.get('type', 'STATUS')}** — "
                            f"{event.get('message', '')}"
                        )

            st.dataframe(
                watch_frame.style
                    .map(action_cell_style, subset=["Trade", "Investment"])
                    .map(
                        lambda value: (
                            "background-color: #e7f5ff; font-weight: 700"
                            if str(value) not in {"No change", "NEW"} and "→" in str(value)
                            else ""
                        ),
                        subset=["Change"],
                    ),
                hide_index=True,
                use_container_width=True,
            )

            with st.expander("How to read the watchlist", expanded=False):
                st.write(
                    "**Trade buy zone** is the preferred entry range. **Reassess below** is the level where the current trade idea should be reviewed rather than held blindly."
                )
                st.write(
                    "**Investment buy price** is the highest simple price that satisfies the current base margin-of-safety requirement and non-negative bear-case valuation gate. Other quality/manual-review gates still apply."
                )
                st.write(
                    "**Change** compares the current Trade/Investment statuses with your previous saved refresh. Recent changes are kept in the browser-linked watchlist history."
                )
                st.write(
                    "This MVP saves the watchlist state in the page link rather than a user account. Keep using/bookmarking the current app URL to retain it. Account sync will replace this when login is added."
                )

        if failed:
            st.warning(
                "I couldn't complete the following watchlist entries: "
                + ", ".join(failed)
            )

        if not rows:
            st.warning("No watchlist companies returned enough data for analysis.")

    if watch_entries:
        st.markdown("#### Manage watchlist")
        st.caption("Remove any company here without running a new analysis.")
        for watch_index, watch_symbol in enumerate(watch_entries):
            company_name = watchlist_company_name(watch_symbol)
            name_col, remove_col = st.columns([5, 1])
            with name_col:
                st.write(f"**{company_name} ({watch_symbol})**")
            with remove_col:
                if st.button(
                    "Remove",
                    key=f"remove_watch_{watch_symbol}_{watch_index}",
                    use_container_width=True,
                ):
                    remove_browser_watchlist_symbol(watch_symbol)
                    st.toast(f"{company_name} ({watch_symbol}) removed from watchlist")
                    st.rerun()

with tab3:
    st.subheader("Advanced Trade Search")
    st.caption(f"Rulebook build: {TRADE_RULEBOOK_BUILD}")
    st.caption(
        "Approved value-driven swing rulebook. The scanner identifies and ranks paper-trade candidates; "
        "it does not size positions or place orders."
    )

    u1, u2, u3 = st.columns(3)
    with u1:
        universe_label = st.selectbox("Universe", list(PUBLIC_UNIVERSES.keys()), index=0)
    with u2:
        cap_choice = st.selectbox(
            "Maximum symbols", [250, 500, 1000, 2000, 0], index=1,
            format_func=lambda x: "All" if x == 0 else f"{x:,}",
        )
    with u3:
        st.metric("Mode", "PAPER OBSERVATION")

    selected_trade_kind = PUBLIC_UNIVERSES[universe_label]
    trade_cache_meta = prepared_trade_metadata(selected_trade_kind)
    if trade_cache_meta.get("completed_at"):
        coverage = trade_cache_meta.get("coverage_pct")
        coverage_text = (
            f" Coverage: {coverage:.1f}%."
            if isinstance(coverage, (int, float))
            else ""
        )
        st.caption(
            f"Nightly Trade fundamentals updated {trade_cache_meta['completed_at']}."
            f"{coverage_text} Backfilled fundamentals are applied first, so live technical checks run only on shares that pass the Trade quality gates."
        )

    with st.expander("Active hard gates and ranking model", expanded=False):
        st.markdown(
            """
- **Universe:** excludes Financial Services and Real Estate; market cap ≥ £500m; median 20-session traded value ≥ £5m; at least 252 daily sessions.
- **Fundamentals:** quality score ≥65, positive-FCF tests, net debt/FCF ≤4×, dilution ≤5%, deterioration and extreme-risk gates.
- **Setup:** rising SMA180 and SMA200, MA-zone contact, valid sub-30 RSI recovery, current-session confirmed MACD crossover.
- **Entry/risk:** following open within ±0.5 ATR, no more than 1 ATR above the MA zone, structural stop ≤10% away.
- **Target:** nearest verified resistance or 52-week-high fallback, buffered by 0.25 ATR; at least 10% upside and 2:1 reward/risk.
- **Ranking only:** support confluence, 10-session relative strength, MACD location, volume, candle structure and three-state market regime.
- **Event safety:** earnings inside five sessions block. An incomplete official-announcement check triggers the approved fail-safe block.
            """
        )

    st.info(
        "The official global company-announcement feed is not configured in this app. "
        "Event verification is therefore deferred until a share first passes the numerical "
        "Trade gates; only provisional candidates are then fail-safe blocked if the official "
        "check cannot be completed."
    )

    if st.button("Run Trade Search", type="primary", use_container_width=True):
        with st.spinner("Loading public stock universe…"):
            universe_kind = PUBLIC_UNIVERSES[universe_label]
            universe = get_universe(universe_kind)

        if not universe:
            st.error("The public universe list could not be loaded right now.")
        else:
            limit_text = "all" if cap_choice == 0 else f"{min(cap_choice, len(universe)):,}"
            st.info(f"Universe loaded: {len(universe):,} tickers. Scanning {limit_text} symbols.")

            trade_progress = st.progress(
                0.0,
                text="Preparing Trade Search from backfilled fundamentals…",
            )

            def update_trade_progress(completed, total, qualified, message):
                trade_progress.progress(
                    min(completed / max(total, 1), 1.0),
                    text=message,
                )

            pre = approved_trade_market_scan(
                tuple(universe),
                cap_choice,
                universe_kind,
                TRADE_RULEBOOK_BUILD,
                _progress_callback=update_trade_progress,
            )
            trade_progress.progress(
                1.0,
                text=f"Trade Search complete · {len(pre):,} current setups returned",
            )

            if pre.empty:
                diag = pre.attrs.get("scan_diagnostics", {})
                selected = int(diag.get("selected_symbols", 0))
                fundamental_pass = int(diag.get("fundamental_pass", 0))
                technical_evaluated = int(diag.get("technical_evaluated", 0))
                price_history_failures = int(diag.get("price_history_failures", 0))
                liquidity_failures = int(diag.get("liquidity_failures", 0))
                state_counts = diag.get("technical_state_counts", {}) or {}
                watch_states = int(state_counts.get("WATCH", 0))
                ready_states = int(state_counts.get("ENTRY READY", 0)) + int(state_counts.get("AWAITING NEXT OPEN", 0))

                st.warning(
                    "No current WATCH or confirmed crossover setups met the approved Trade rules."
                )
                st.info(
                    f"This was a completed scan, not a failed search: "
                    f"{fundamental_pass:,} of {selected:,} selected shares passed the prepared fundamental gates; "
                    f"{technical_evaluated:,} had usable liquid price history and were technically evaluated; "
                    f"{watch_states:,} reached WATCH and {ready_states:,} reached an entry-ready/next-open state."
                )

                reason_counts = diag.get("technical_reason_counts", {}) or {}
                if reason_counts:
                    top_reasons = sorted(
                        reason_counts.items(),
                        key=lambda item: item[1],
                        reverse=True,
                    )[:5]
                    with st.expander("Why nothing qualified", expanded=False):
                        for reason, count in top_reasons:
                            st.write(f"**{count:,}** — {reason}")
                        if price_history_failures:
                            st.write(f"**{price_history_failures:,}** — insufficient usable price history")
                        if liquidity_failures:
                            st.write(f"**{liquidity_failures:,}** — below the £5m median traded-value gate")
            else:
                paper_count = int((pre["Status"] == "PAPER CANDIDATE").sum())
                watch_count = int((pre["Status"] == "WATCH").sum())
                blocked_count = int((pre["Status"] == "BLOCKED").sum())
                retry_count = int((pre["Status"] == "RETRY").sum())
                st.success(
                    f"Trade Search complete: {paper_count} paper candidates, "
                    f"{watch_count} watch setups, {blocked_count} blocked setups "
                    f"and {retry_count} provider retries."
                )
                if retry_count:
                    st.warning(
                        "Some technically eligible shares could not complete the fundamental "
                        "check because the data provider throttled requests. RETRY rows are not "
                        "trade rejects; rerun the scan after the provider cooldown."
                    )
                st.subheader("Trade rulebook results")
                quick_cols = [
                    "Status", "Ticker", "Company", "Sector", "Reason", "Score status", "Tier", "Fundamental score",
                    "Technical score", "Entry", "Stop", "Target", "R:R", "Upside %",
                    "RSI", "Market regime", "Median traded value £m",
                ]
                visible_quick = [column for column in quick_cols if column in pre.columns]
                render_watchlist_selector(
                    pre[visible_quick].head(50),
                    key=f"advanced_trade_watch_{universe_kind}_{cap_choice}_{_query_param_text('wl')[:8]}",
                )
                with st.expander("Full rule evidence", expanded=False):
                    st.dataframe(pre, hide_index=True, use_container_width=True)
                    st.download_button(
                        "Download Trade Search evidence (CSV)",
                        pre.to_csv(index=False).encode("utf-8"),
                        file_name="trade_search_rulebook_results.csv",
                        mime="text/csv",
                        use_container_width=True,
                    )

                st.caption(
                    "WATCH means the trigger or next-open validation is not complete. PAPER CANDIDATE is "
                    "reserved for a fully validated paper setup. BLOCKED always includes the failed gate in Reason."
                )

with tab4:
    st.subheader("Advanced Investment Search — 10 Years to Forever")
    st.caption(
        "Independent long-term investment search. Business quality is tested first; "
        "valuation is a separate hard gate. Technical setup does not affect the result."
    )

    prepared_available = PREPARED_SCAN_DIR.exists() and any(PREPARED_SCAN_DIR.glob("*.csv.gz"))
    scan_source = st.radio(
        "Scan source",
        ["Prepared nightly scan", "Live full analysis"],
        index=0 if prepared_available else 1,
        horizontal=True,
        help="Prepared scans reuse the nightly financial analysis and refresh only current prices and valuation decisions.",
    )
    use_prepared_scan = scan_source == "Prepared nightly scan"

    f1, f2, f3, f4 = st.columns(4)
    with f1:
        fundamental_universe_label = st.selectbox(
            "Investment universe",
            list(INVESTMENT_UNIVERSES.keys()),
            index=0,
            key="fundamental_universe",
        )
    with f2:
        fundamental_cap = st.selectbox(
            "Companies to analyse",
            [25, 50, 100, 250, 500, 750, 1000],
            index=1,
            format_func=lambda x: f"{x:,}",
            key="fundamental_cap",
            disabled=use_prepared_scan,
            help="The nightly scan already analyses the full prepared universe.",
        )
    with f3:
        min_market_cap_bn = st.number_input(
            "Minimum market cap (bn)",
            min_value=0.0,
            max_value=100.0,
            value=0.5,
            step=0.5,
            key="fundamental_min_cap",
        )
    with f4:
        min_quality_score = st.slider(
            "Minimum quality score",
            min_value=0,
            max_value=100,
            value=60,
            step=5,
            key="fundamental_min_score",
        )

    st.caption(
        "BUY CANDIDATE means the measurable hard gates and DCF margin-of-safety gate passed; "
        "it still requires the listed manual review before buying. "
        "WAIT means the business may qualify but price, evidence or specialist review is not good enough yet. "
        "PASS means a measurable non-negotiable failed."
    )
    st.info(
        "The numerical Quality and Moat scores now use measurable financial evidence only. "
        "Structural moat, technology disruption, key-person risk, concentration, governance, regulatory dependence "
        "and market-share trends are shown separately under Manual review required and do not change the score."
    )
    st.caption(
        "Larger 500–1,000 company scans remain evenly distributed across the full universe, "
        "but take longer and may return fewer results if Yahoo temporarily rate-limits requests."
    )
    st.caption(
        "Exchange choices focus on operating companies rather than ETFs, warrants and other specialist securities. "
        "Each choice loads its own live exchange-level company directory and converts the symbols "
        "to Yahoo's format for financial and valuation data."
    )

    if st.button("Run Investment Search", type="primary", use_container_width=True):
        fundamental_universe_kind = INVESTMENT_UNIVERSES[fundamental_universe_label]
        if use_prepared_scan:
            prepared_path = prepared_scan_path(fundamental_universe_kind)
            if not prepared_path.exists():
                fundamental_results = pd.DataFrame()
                fundamental_results.attrs["scan_diagnostics"] = {
                    "other_errors": 1,
                    "error_samples": [f"No completed nightly scan is available yet for {fundamental_universe_label}."],
                }
            else:
                with st.spinner("Loading the nightly analysis and refreshing current prices…"):
                    fundamental_results = load_prepared_investment_scan(
                        fundamental_universe_kind,
                        prepared_path.stat().st_mtime_ns,
                    )
                    fundamental_results = fundamental_results[
                        pd.to_numeric(fundamental_results["Market cap"], errors="coerce")
                        >= min_market_cap_bn * 1_000_000_000
                    ].copy()
                    fundamental_results, refresh_diag = refresh_prepared_investment_prices(fundamental_results)
                    fundamental_results.attrs["scan_diagnostics"] = {
                        "requested": len(fundamental_results),
                        "returned": len(fundamental_results),
                        "price_failures": refresh_diag["missing"],
                        "rate_limit_errors": refresh_diag["rate_limit_errors"],
                        "market_cap_failures": 0,
                        "below_min_market_cap": 0,
                        "other_errors": 0,
                        "error_samples": [],
                    }
                metadata = prepared_scan_metadata(fundamental_universe_kind)
                if metadata.get("completed_at"):
                    coverage = metadata.get("coverage_pct")
                    coverage_text = f" Prepared coverage: {coverage:.1f}%." if isinstance(coverage, (int, float)) else ""
                    st.caption(
                        f"Prepared fundamentals batch updated {metadata['completed_at']}; "
                        f"live prices refreshed for {refresh_diag['updated']:,} companies."
                        f"{coverage_text}"
                    )
        else:
            with st.spinner("Loading public stock universe…"):
                fundamental_universe = get_universe(fundamental_universe_kind)
                fundamental_market_caps = get_universe_market_caps(fundamental_universe_kind)
            if not fundamental_universe:
                fundamental_results = pd.DataFrame()
                fundamental_results.attrs["scan_diagnostics"] = {
                    "other_errors": 1,
                    "error_samples": ["The public universe list could not be loaded right now."],
                }
            else:
                progress_bar = st.progress(0.0, text=f"Analysed 0 of {min(fundamental_cap, len(fundamental_universe)):,}")

                def update_investment_progress(completed, total, qualified):
                    progress_bar.progress(
                        completed / max(total, 1),
                        text=f"Analysed {completed:,} of {total:,} · {qualified:,} returned",
                    )

                fundamental_results = fundamental_market_scan(
                    tuple(fundamental_universe),
                    fundamental_cap,
                    min_market_cap_bn * 1_000_000_000,
                    tuple(sorted(fundamental_market_caps.items())),
                    progress_callback=update_investment_progress,
                    max_workers=4,
                )
                progress_bar.empty()

        if fundamental_results is not None:
            if fundamental_results.empty:
                diag = fundamental_results.attrs.get("scan_diagnostics", {})
                rate_limited = int(diag.get("rate_limit_errors", 0))
                price_failures = int(diag.get("price_failures", 0))
                market_cap_failures = int(diag.get("market_cap_failures", 0))
                below_min_market_cap = int(diag.get("below_min_market_cap", 0))
                other_errors = int(diag.get("other_errors", 0))
                error_samples = diag.get("error_samples", [])
                if rate_limited or price_failures:
                    st.error(
                        f"Investment Search could not retrieve usable Yahoo data for this batch. "
                        f"Rate-limit errors: {rate_limited}; price-data failures: {price_failures}; "
                        f"market-cap failures: {market_cap_failures}; "
                        f"other company errors: {other_errors}. This is a data-provider problem, not a zero-candidate result."
                    )
                    st.info("Try 25 companies first. If Yahoo is temporarily rate-limiting the Streamlit server, wait a few minutes before running another batch.")
                else:
                    st.warning(
                        f"No companies returned enough investment data under these filters. "
                        f"Missing market cap: {market_cap_failures}; below minimum market cap: "
                        f"{below_min_market_cap}; other company errors: {other_errors}."
                    )
                if error_samples:
                    st.code("\n".join(error_samples), language="text")
            else:
                diag = fundamental_results.attrs.get("scan_diagnostics", {})
                market_cap_failures = int(diag.get("market_cap_failures", 0))
                below_min_market_cap = int(diag.get("below_min_market_cap", 0))
                filtered_fundamentals = fundamental_results[
                    fundamental_results["Quality score"] >= min_quality_score
                ].copy()

                if market_cap_failures or below_min_market_cap:
                    st.caption(
                        f"Market-cap gate excluded {below_min_market_cap} companies below your minimum "
                        f"and {market_cap_failures} whose market cap could not be verified."
                    )

                buy_candidates = int((filtered_fundamentals["Action"] == "BUY CANDIDATE").sum()) if not filtered_fundamentals.empty else 0
                waits = int((filtered_fundamentals["Action"] == "WAIT").sum()) if not filtered_fundamentals.empty else 0
                passes = int((filtered_fundamentals["Action"] == "PASS").sum()) if not filtered_fundamentals.empty else 0

                c1, c2, c3, c4 = st.columns(4)
                c1.metric("BUY CANDIDATE", buy_candidates)
                c2.metric("WAIT", waits)
                c3.metric("PASS", passes)
                c4.metric("Analysed", len(fundamental_results))

                if not fundamental_results.empty and "Exchange Country" in fundamental_results.columns:
                    mix = fundamental_results["Exchange Country"].fillna("Unknown").value_counts().to_dict()
                    mix_text = " · ".join(f"{k}: {v}" for k, v in mix.items())
                    st.caption("Successful market mix: " + mix_text)

                if filtered_fundamentals.empty:
                    st.info("No company currently meets your selected minimum quality score.")
                else:
                    st.caption(
                        "Action status: 🟢 BUY CANDIDATE · 🟠 WAIT · 🔴 PASS"
                    )
                    render_watchlist_selector(
                        filtered_fundamentals,
                        key=f"advanced_investment_watch_{fundamental_universe_kind}_{scan_source}_{_query_param_text('wl')[:8]}",
                        column_config={
                            "Quality score": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.1f"),
                            "Moat score": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.1f"),
                            "Resilience": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.1f"),
                            "Reinvestment": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.1f"),
                            "Capital allocation": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.1f"),
                            "Cash quality": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.1f"),
                            "Base margin of safety %": st.column_config.NumberColumn(format="%.1f%%"),
                            "Required margin of safety %": st.column_config.NumberColumn(format="%.1f%%"),
                            "Bear margin of safety %": st.column_config.NumberColumn(format="%.1f%%"),
                            "ROIC %": st.column_config.NumberColumn(format="%.1f%%"),
                            "ROIC trend %": st.column_config.NumberColumn(format="%.1f%%"),
                            "FCF/share CAGR %": st.column_config.NumberColumn(format="%.1f%%"),
                            "Positive FCF years %": st.column_config.NumberColumn(format="%.0f%%"),
                            "Dilution CAGR %": st.column_config.NumberColumn(format="%.2f%%"),
                            "Market cap": st.column_config.NumberColumn(format="%.0f"),
                        },
                    )


with tab5:
    st.subheader("Portfolio")
    st.caption(
        "Keep long-term investments and active trades separate. "
        "Investment diversification is calculated from INVESTMENT positions only."
    )

    # Create these containers now so the investment dashboard stays visually at
    # the very top even though the editor and review logic execute before it is filled.
    investment_top = st.container()
    position_manager = st.container()
    investment_holdings_area = st.container()
    trading_portfolio_area = st.container()

    with position_manager:
        st.markdown("### Manage positions")
        st.caption(
            "Choose INVESTMENT for your long-term portfolio or TRADE for a time-limited technical position. "
            "The same ticker can exist once in each portfolio."
        )

        # Browser persistence must only initialise inside a real Streamlit session.
        # Scheduled GitHub Actions import this module for the technical engine and
        # have no browser/session context.
        browser_runtime_active = get_script_run_ctx() is not None
        if browser_runtime_active:
            portfolio_cookie = CookieController(key="cl_signal_portfolio_cookie_controller")
            portfolio_local_storage = LocalStorage(key="cl_signal_portfolio_local_storage_init")
            persisted_local_payload = portfolio_local_storage.getItem(
                PORTFOLIO_LOCAL_STORAGE_KEY
            )
            persisted_cookie_payload = portfolio_cookie.get(PORTFOLIO_COOKIE_NAME)
        else:
            portfolio_cookie = None
            portfolio_local_storage = None
            persisted_local_payload = None
            persisted_cookie_payload = None
        # Browser localStorage is the primary persistence layer. Keep the
        # existing cookie as a migration/fallback path for portfolios saved
        # before this change.
        persisted_portfolio_payload = (
            persisted_local_payload
            if persisted_local_payload not in (None, "")
            else persisted_cookie_payload
        )

        if "portfolio_editor_version" not in st.session_state:
            st.session_state["portfolio_editor_version"] = 0
        if "portfolio_holdings_store" not in st.session_state:
            st.session_state["portfolio_holdings_store"] = pd.DataFrame([
                {"Ticker": "", "Position type": "INVESTMENT", "Shares": 0.0, "Average cost": 0.0},
            ])
            st.session_state["_portfolio_cookie_loaded"] = False
            st.session_state["_portfolio_user_modified"] = False

        if (
            persisted_portfolio_payload is not None
            and not st.session_state.get("_portfolio_cookie_loaded", False)
            and not st.session_state.get("_portfolio_user_modified", False)
        ):
            saved_portfolio = portfolio_holdings_from_payload(persisted_portfolio_payload)
            if saved_portfolio is not None:
                st.session_state["portfolio_holdings_store"] = saved_portfolio
                st.session_state["portfolio_editor_version"] += 1
                st.session_state["_portfolio_saved_payload"] = persisted_portfolio_payload
            st.session_state["_portfolio_cookie_loaded"] = True

        uploaded_portfolio = st.file_uploader(
            "Import positions CSV (optional)",
            type=["csv"],
            key="portfolio_csv_upload",
            help="Required columns: Ticker, Shares and Average cost. Position type is optional and defaults to INVESTMENT.",
        )

        portfolio_seed = normalise_portfolio_holdings(
            st.session_state.get("portfolio_holdings_store")
        )
        if portfolio_seed.empty:
            portfolio_seed = pd.DataFrame([
                {"Ticker": "", "Position type": "INVESTMENT", "Shares": 0.0, "Average cost": 0.0},
            ])

        upload_identity = "saved"
        if uploaded_portfolio is not None:
            try:
                imported = pd.read_csv(uploaded_portfolio)
                aliases = {
                    "ticker": "Ticker",
                    "symbol": "Ticker",
                    "position type": "Position type",
                    "position_type": "Position type",
                    "type": "Position type",
                    "shares": "Shares",
                    "quantity": "Shares",
                    "average cost": "Average cost",
                    "average_cost": "Average cost",
                    "avg cost": "Average cost",
                    "avg_cost": "Average cost",
                }
                imported = imported.rename(
                    columns={column: aliases.get(str(column).strip().lower(), column) for column in imported.columns}
                )
                missing_columns = {"Ticker", "Shares", "Average cost"} - set(imported.columns)
                if missing_columns:
                    raise ValueError("missing columns: " + ", ".join(sorted(missing_columns)))
                portfolio_seed = normalise_portfolio_holdings(imported)
                if portfolio_seed.empty:
                    portfolio_seed = pd.DataFrame([
                        {"Ticker": "", "Position type": "INVESTMENT", "Shares": 0.0, "Average cost": 0.0},
                    ])
                upload_identity = f"{uploaded_portfolio.name}_{getattr(uploaded_portfolio, 'size', 0)}"
            except Exception as exc:
                st.error(f"The portfolio CSV could not be loaded: {exc}")

        def clear_portfolio_results():
            st.session_state.pop("portfolio_results", None)
            st.session_state.pop("portfolio_errors", None)

        def portfolio_editor_changed():
            clear_portfolio_results()
            st.session_state["_portfolio_dirty"] = True
            st.session_state["_portfolio_user_modified"] = True

        editor_key = (
            f"portfolio_editor_{upload_identity}_"
            f"{st.session_state.get('portfolio_editor_version', 0)}"
        )
        edited_holdings = st.data_editor(
            portfolio_seed,
            num_rows="dynamic",
            hide_index=True,
            use_container_width=True,
            key=editor_key,
            on_change=portfolio_editor_changed,
            column_config={
                "Ticker": st.column_config.TextColumn(
                    "Ticker",
                    width="small",
                    help="Broker ticker is fine. The app resolves exchange suffixes such as .L or .SW when market data needs them.",
                ),
                "Position type": st.column_config.SelectboxColumn(
                    "Position type",
                    options=["INVESTMENT", "TRADE"],
                    required=True,
                    width="medium",
                    help="INVESTMENT counts toward the long-term diversification charts. TRADE is tracked separately.",
                ),
                "Shares": st.column_config.NumberColumn("Shares", min_value=0.0, format="%.4f", width="small"),
                "Average cost": st.column_config.NumberColumn(
                    "Average cost (broker)",
                    min_value=0.0,
                    format="%.4f",
                    width="medium",
                    help="Enter the average price exactly as your broker displays it, e.g. 42.18 for a £42.18 UK share or 487.50 for CHF 487.50.",
                ),
            },
        )

        current_portfolio_payload = portfolio_holdings_payload(edited_holdings)
        seed_portfolio_payload = portfolio_holdings_payload(portfolio_seed)
        editor_changed = (
            st.session_state.pop("_portfolio_dirty", False)
            or current_portfolio_payload != seed_portfolio_payload
        )
        if editor_changed:
            # Save to browser localStorage so positions survive closing the tab
            # or browser. Keep the cookie copy as a secondary fallback.
            if portfolio_local_storage is not None:
                portfolio_local_storage.setItem(
                    PORTFOLIO_LOCAL_STORAGE_KEY,
                    current_portfolio_payload,
                    key="cl_signal_portfolio_local_storage_autosave",
                )
            if portfolio_cookie is not None:
                portfolio_cookie.set(
                    PORTFOLIO_COOKIE_NAME,
                    current_portfolio_payload,
                    expires=datetime.datetime.now() + datetime.timedelta(days=PORTFOLIO_COOKIE_DAYS),
                )
            st.session_state["portfolio_holdings_store"] = normalise_portfolio_holdings(edited_holdings)
            st.session_state["_portfolio_saved_payload"] = current_portfolio_payload
            st.session_state["_portfolio_cookie_loaded"] = True
            st.session_state["_portfolio_user_modified"] = True

        d1, d2 = st.columns(2)
        with d1:
            analyse_portfolio_clicked = st.button(
                "Review Portfolios",
                type="primary",
                use_container_width=True,
            )
        with d2:
            st.download_button(
                "Download Positions CSV",
                data=normalise_portfolio_holdings(edited_holdings).to_csv(index=False).encode("utf-8"),
                file_name="stock_portfolio_positions.csv",
                mime="text/csv",
                use_container_width=True,
            )

        st.info(
            "Investment actions and diversification are separate. BUY MORE / HOLD / REASSESS / REVIEW FOR SALE "
            "come from the investment framework. A red allocation slice only means the position or sector is above "
            "your diversification target; it does not turn the company into a sell."
        )

        if analyse_portfolio_clicked:
            current_portfolio_payload = portfolio_holdings_payload(edited_holdings)
            if portfolio_local_storage is not None:
                portfolio_local_storage.setItem(
                    PORTFOLIO_LOCAL_STORAGE_KEY,
                    current_portfolio_payload,
                    key="cl_signal_portfolio_local_storage_review",
                )
            if portfolio_cookie is not None:
                portfolio_cookie.set(
                    PORTFOLIO_COOKIE_NAME,
                    current_portfolio_payload,
                    expires=datetime.datetime.now() + datetime.timedelta(days=PORTFOLIO_COOKIE_DAYS),
                )
            st.session_state["portfolio_holdings_store"] = normalise_portfolio_holdings(edited_holdings)
            st.session_state["_portfolio_saved_payload"] = current_portfolio_payload
            st.session_state["_portfolio_cookie_loaded"] = True
            st.session_state["_portfolio_user_modified"] = True

            clean_holdings = normalise_portfolio_holdings(edited_holdings)
            duplicate_rows = clean_holdings.loc[
                clean_holdings.duplicated(subset=["Ticker", "Position type"], keep=False),
                ["Ticker", "Position type"],
            ]

            results = []
            errors = []
            if clean_holdings.empty:
                errors.append("Add at least one position before running the review.")
            if not duplicate_rows.empty:
                duplicate_text = ", ".join(
                    f"{row['Ticker']} ({row['Position type']})"
                    for _, row in duplicate_rows.drop_duplicates().iterrows()
                )
                errors.append(
                    "Use one row per ticker per position type. "
                    "The same ticker may appear once as INVESTMENT and once as TRADE. "
                    "Duplicates: " + duplicate_text
                )

            if not errors:
                progress = st.progress(0)
                total_rows = len(clean_holdings)
                with st.spinner("Updating current prices, returns and portfolio decisions…"):
                    for position, (_, holding) in enumerate(clean_holdings.iterrows(), start=1):
                        ticker = holding["Ticker"]
                        position_type = str(holding.get("Position type") or "INVESTMENT").upper()
                        shares = safe(holding.get("Shares"), 0.0)
                        average_cost = safe(holding.get("Average cost"), 0.0)

                        if shares <= 0:
                            errors.append(f"{ticker} ({position_type}): shares must be greater than zero")
                        elif average_cost <= 0:
                            errors.append(
                                f"{ticker} ({position_type}): average cost must be greater than zero so return and P/L can be calculated"
                            )
                        else:
                            try:
                                if position_type == "TRADE":
                                    result = analyse_trade_portfolio_holding(ticker, shares, average_cost)
                                else:
                                    result = analyse_portfolio_holding(ticker, shares, average_cost)
                                results.append(result)
                            except Exception as exc:
                                errors.append(
                                    f"{ticker} ({position_type}): {exc.__class__.__name__}: {str(exc)[:180]}"
                                )
                        progress.progress(position / total_rows)
                progress.empty()

            st.session_state["portfolio_results"] = pd.DataFrame(results)
            st.session_state["portfolio_errors"] = errors

        portfolio_errors = st.session_state.get("portfolio_errors", [])
        for error in portfolio_errors:
            st.warning(error)

    portfolio_results = st.session_state.get("portfolio_results")
    if isinstance(portfolio_results, pd.DataFrame):
        portfolio_results = portfolio_results.copy()
    else:
        portfolio_results = pd.DataFrame()

    required_portfolio_result_columns = {
        "Ticker", "Position type", "Average cost", "Amount invested", "Current price",
        "Market value £", "Cost basis £", "Unrealised P/L £", "Quote currency",
    }
    if (
        not portfolio_results.empty
        and not required_portfolio_result_columns.issubset(portfolio_results.columns)
    ):
        # Results created by the previous portfolio layout are deliberately
        # discarded so stale None values / old REDUCE-REBALANCE actions cannot
        # leak into the redesigned page.
        st.session_state.pop("portfolio_results", None)
        portfolio_results = pd.DataFrame()

    investment_results = (
        portfolio_results[portfolio_results["Position type"] == "INVESTMENT"].copy()
        if not portfolio_results.empty
        else pd.DataFrame()
    )
    trade_results = (
        portfolio_results[portfolio_results["Position type"] == "TRADE"].copy()
        if not portfolio_results.empty
        else pd.DataFrame()
    )

    # ---------------------------
    # Investment Portfolio — top
    # ---------------------------
    with investment_top:
        st.markdown("## Investment Portfolio")
        st.caption(
            "Your long-term diversification philosophy: no individual stock above 5% and no sector above 20%. "
            "Allocation colours are separate from the investment action."
        )

        if investment_results.empty:
            st.info(
                "Add or review an INVESTMENT position to populate the allocation charts. "
                "TRADE positions never count toward these diversification percentages."
            )
        else:
            all_values_converted = investment_results["Market value £"].notna().all()
            total_value = investment_results["Market value £"].sum() if all_values_converted else np.nan
            total_cost_known = investment_results["Cost basis £"].notna().all()
            total_cost = investment_results["Cost basis £"].sum() if total_cost_known else np.nan
            total_pnl = (
                total_value - total_cost
                if not np.isnan(total_value) and not np.isnan(total_cost)
                else np.nan
            )
            total_return = (
                total_pnl / total_cost * 100
                if not np.isnan(total_pnl) and total_cost > 0
                else np.nan
            )

            if all_values_converted and total_value > 0:
                investment_results["Weight %"] = investment_results["Market value £"] / total_value * 100
            else:
                investment_results["Weight %"] = np.nan

            buy_more_count = int((investment_results["Action"] == "BUY MORE").sum())
            review_count = int(
                investment_results["Action"].isin(["REASSESS", "REVIEW FOR SALE"]).sum()
            )
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Portfolio value", "—" if np.isnan(total_value) else f"£{total_value:,.2f}")
            m2.metric(
                "Unrealised return",
                "—" if np.isnan(total_return) else f"{total_return:+.1f}%",
                None if np.isnan(total_pnl) else f"£{total_pnl:+,.2f}",
            )
            m3.metric("Buy more", buy_more_count)
            m4.metric("Actions to review", review_count)

            if all_values_converted and total_value > 0:
                sector_allocation = (
                    investment_results.groupby("Sector", dropna=False)["Market value £"]
                    .sum()
                    .sort_values(ascending=False)
                    .rename("Market value £")
                    .reset_index()
                )
                sector_allocation["Sector"] = sector_allocation["Sector"].fillna("Unknown")

                pie1, pie2 = st.columns(2, gap="large")
                with pie1:
                    st.plotly_chart(
                        portfolio_donut(
                            investment_results,
                            "Ticker",
                            "Allocation by company",
                            INVESTMENT_STOCK_TARGET_PCT,
                        ),
                        use_container_width=True,
                        config={"displayModeBar": False},
                    )
                with pie2:
                    st.plotly_chart(
                        portfolio_donut(
                            sector_allocation,
                            "Sector",
                            "Allocation by sector",
                            INVESTMENT_SECTOR_TARGET_PCT,
                        ),
                        use_container_width=True,
                        config={"displayModeBar": False},
                    )

                st.caption(
                    "🟢 comfortably within target · 🟠 approaching the target · 🔴 above the long-term target. "
                    "Red is a diversification warning, not a sell signal. While the portfolio is being built, "
                    "new contributions can improve the balance without changing the underlying investment action."
                )
            else:
                st.warning(
                    "At least one quote currency could not be converted to GBP, so allocation percentages and charts are hidden."
                )

    # ---------------------------
    # Investment holdings table
    # ---------------------------
    with investment_holdings_area:
        st.markdown("### Investment holdings")
        if investment_results.empty:
            st.caption("No reviewed INVESTMENT positions yet.")
        else:
            investment_main = investment_results[[
                "Ticker", "Company", "Action", "Weight %", "Shares", "Average cost",
                "Amount invested", "Current price", "Return %", "Market value £", "Unrealised P/L £", "Quote currency",
            ]].copy()
            investment_main = investment_main.rename(columns={"Quote currency": "Currency"})

            styled_investment = investment_main.style.map(
                portfolio_action_cell_style,
                subset=["Action"],
            ).map(
                allocation_cell_style,
                subset=["Weight %"],
            )

            st.caption(
                "Action = investment quality/valuation decision. Weight = diversification only. "
                "Current price is fetched independently from recent market data each time you review the portfolio. "
                "Target: ≤5% per stock."
            )
            st.dataframe(
                styled_investment,
                hide_index=True,
                use_container_width=True,
                column_config={
                    "Ticker": st.column_config.TextColumn(width="small"),
                    "Company": st.column_config.TextColumn(width="large"),
                    "Action": st.column_config.TextColumn(width="medium"),
                    "Weight %": st.column_config.NumberColumn(format="%.1f%%", width="small"),
                    "Shares": st.column_config.NumberColumn(format="%.4f", width="small"),
                    "Average cost": st.column_config.NumberColumn(format="%.4f", width="small"),
                    "Amount invested": st.column_config.NumberColumn(
                        format="%.2f",
                        width="small",
                        help="Shares × broker average cost, shown in the position's quote currency.",
                    ),
                    "Current price": st.column_config.NumberColumn(format="%.4f", width="small"),
                    "Return %": st.column_config.NumberColumn(format="%+.1f%%", width="small"),
                    "Market value £": st.column_config.NumberColumn(format="£%.2f", width="small"),
                    "Unrealised P/L £": st.column_config.NumberColumn(format="£%+.2f", width="small"),
                    "Currency": st.column_config.TextColumn(width="medium"),
                },
            )

            with st.expander("Investment analysis details"):
                detail_columns = [
                    "Ticker", "Quality score", "Hard gates", "Base intrinsic value",
                    "Bear intrinsic value", "Base margin of safety %",
                    "Required margin of safety %", "Bear margin of safety %",
                    "Sector", "Industry", "Country", "Review reason", "Manual review required",
                ]
                st.dataframe(
                    investment_results[detail_columns],
                    hide_index=True,
                    use_container_width=True,
                    column_config={
                        "Quality score": st.column_config.ProgressColumn(
                            min_value=0, max_value=100, format="%.1f", width="medium"
                        ),
                        "Base margin of safety %": st.column_config.NumberColumn(format="%.1f%%"),
                        "Required margin of safety %": st.column_config.NumberColumn(format="%.1f%%"),
                        "Bear margin of safety %": st.column_config.NumberColumn(format="%.1f%%"),
                        "Review reason": st.column_config.TextColumn(width="large"),
                        "Manual review required": st.column_config.TextColumn(width="large"),
                    },
                )

    # ---------------------------
    # Trading Portfolio
    # ---------------------------
    with trading_portfolio_area:
        st.markdown("## Trading Portfolio")
        st.caption(
            "Active trades are tracked separately and never alter the Investment Portfolio's 5% stock or 20% sector allocation."
        )

        if trade_results.empty:
            st.info("No reviewed TRADE positions yet.")
        else:
            trade_values_known = trade_results["Market value £"].notna().all()
            trade_cost_known = trade_results["Cost basis £"].notna().all()
            trade_value = trade_results["Market value £"].sum() if trade_values_known else np.nan
            trade_cost = trade_results["Cost basis £"].sum() if trade_cost_known else np.nan
            trade_pnl = (
                trade_value - trade_cost
                if not np.isnan(trade_value) and not np.isnan(trade_cost)
                else np.nan
            )
            trade_return = (
                trade_pnl / trade_cost * 100
                if not np.isnan(trade_pnl) and trade_cost > 0
                else np.nan
            )
            trade_exit_count = int(
                trade_results["Action"].isin(["EXIT / REASSESS", "EXIT / TARGET REACHED"]).sum()
            )

            t1, t2, t3 = st.columns(3)
            t1.metric("Active trade value", "—" if np.isnan(trade_value) else f"£{trade_value:,.2f}")
            t2.metric(
                "Trade return",
                "—" if np.isnan(trade_return) else f"{trade_return:+.1f}%",
                None if np.isnan(trade_pnl) else f"£{trade_pnl:+,.2f}",
            )
            t3.metric("Exit / review signals", trade_exit_count)

            trade_main = trade_results[[
                "Ticker", "Company", "Action", "Shares", "Average cost",
                "Amount invested", "Current price", "Return %", "Market value £", "Unrealised P/L £", "Quote currency",
            ]].copy().rename(columns={"Quote currency": "Currency"})

            styled_trade = trade_main.style.map(
                trade_portfolio_action_cell_style,
                subset=["Action"],
            )
            st.dataframe(
                styled_trade,
                hide_index=True,
                use_container_width=True,
                column_config={
                    "Ticker": st.column_config.TextColumn(width="small"),
                    "Company": st.column_config.TextColumn(width="large"),
                    "Action": st.column_config.TextColumn(width="medium"),
                    "Shares": st.column_config.NumberColumn(format="%.4f", width="small"),
                    "Average cost": st.column_config.NumberColumn(format="%.4f", width="small"),
                    "Amount invested": st.column_config.NumberColumn(
                        format="%.2f",
                        width="small",
                        help="Shares × broker average cost, shown in the position's quote currency.",
                    ),
                    "Current price": st.column_config.NumberColumn(format="%.4f", width="small"),
                    "Return %": st.column_config.NumberColumn(format="%+.1f%%", width="small"),
                    "Market value £": st.column_config.NumberColumn(format="£%.2f", width="small"),
                    "Unrealised P/L £": st.column_config.NumberColumn(format="£%+.2f", width="small"),
                    "Currency": st.column_config.TextColumn(width="medium"),
                },
            )

            with st.expander("Trade plan details", expanded=True):
                trade_detail = trade_results[[
                    "Ticker", "Technical score", "R:R", "Entry zone", "Target",
                    "Invalidation", "Sector", "Industry", "Trade reason",
                ]]
                st.dataframe(
                    trade_detail,
                    hide_index=True,
                    use_container_width=True,
                    column_config={
                        "Technical score": st.column_config.ProgressColumn(
                            min_value=0, max_value=100, format="%.1f", width="medium"
                        ),
                        "R:R": st.column_config.NumberColumn(format="%.2f", width="small"),
                        "Trade reason": st.column_config.TextColumn(width="large"),
                    },
                )

    st.caption(
        "Positions save automatically to this browser and should restore after you close and reopen the app on the same browser/device. "
        "Download the CSV only as a backup or to move the portfolios to another browser or device."
    )


with st.expander("How the scores work"):
    st.markdown("""
**Trade Setup /100** rewards constructive trend, proximity to support, RSI in a usable entry zone, improving MACD, healthy volume, risk/reward, realistic upside and avoiding overextended entries.

**12-month Hold Quality /100** is retained only for the TRADE workflow as a secondary check on whether a technical trade has enough fundamental support for a longer trade hold.

**Investment Search** uses a separate 10-years-to-forever framework: hard gates first, then moat/durability, resilience, reinvestment, capital allocation and cash quality, followed by bear/base/bull DCF valuation and a dynamic margin-of-safety hurdle.

**Classification**
- **Swing-to-hold:** strong trade setup and fundamentals good enough to justify a longer hold.
- **Swing only:** strong trade setup, but fundamentals are not strong enough to turn a failed trade into an investment.
- **Core opportunity:** excellent hold quality with an acceptable entry.
- **Developing / Watch:** not strong enough yet.

**Trade Search and Investment Search are independent.** Trade Search looks for technical setups. Investment Search applies the 10-years-to-forever quality gates and DCF valuation without requiring a technical setup. Quick Analyse can still bring both sides together for a specific ticker.
""")

st.caption("Screening aid only, not financial advice. Public market data and analyst estimates can be delayed, incomplete or unavailable for some listings.")
