from __future__ import annotations

import io
import json
import math
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from html.parser import HTMLParser
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st
import yfinance as yf
from valuation import fundamental_analysis as valuation_fundamental_analysis
from strategy_scores_v3 import long_term_analysis
from two_strategy import investment_decision
from trade_rules import (
    FundamentalSnapshot,
    build_fundamental_snapshot,
    business_sessions_until,
    evaluate_price_setup,
    score_fundamental_snapshot,
)

st.set_page_config(page_title="Stock Opportunity Screener", page_icon="📈", layout="wide")

PRIORITY_DEFAULT = "FLNC, SPCX"
PREPARED_SCAN_DIR = Path(__file__).resolve().parent / "prepared_scans"
TRADE_RULEBOOK_BUILD = "2026.09.18.8"

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


def action_cell_style(value) -> str:
    """Return the RAG colour for a screener action without changing its value."""
    action = str(value).strip().upper()
    if action in {"BUY", "BUY CANDIDATE", "PAPER CANDIDATE"}:
        return "background-color: #d8f3dc; color: #16351c; font-weight: 700"
    if action in {"WAIT", "WATCH", "HOLD", "EARNINGS WAIT", "RETRY"}:
        return "background-color: #fff3bf; color: #5f4500; font-weight: 700"
    if action in {"PASS", "AVOID", "SELL", "BLOCKED"}:
        return "background-color: #ffd6d6; color: #5c1717; font-weight: 700"
    return ""


def portfolio_action_cell_style(value) -> str:
    """Colour owned-position actions by urgency."""
    action = str(value).strip().upper()
    if action in {"ADD CANDIDATE", "HOLD"}:
        return "background-color: #d8f3dc; color: #16351c; font-weight: 700"
    if action == "REASSESS":
        return "background-color: #fff3bf; color: #5f4500; font-weight: 700"
    if action in {"REVIEW FOR SALE", "REDUCE / REBALANCE"}:
        return "background-color: #ffd6d6; color: #5c1717; font-weight: 700"
    return ""


def allocation_cell_style(value) -> str:
    """Highlight position concentration using portfolio weight."""
    weight = safe(value)
    if np.isnan(weight):
        return ""
    if weight > 25:
        return "background-color: #ffd6d6; color: #5c1717; font-weight: 700"
    if weight > 15:
        return "background-color: #fff3bf; color: #5f4500; font-weight: 700"
    return "background-color: #d8f3dc; color: #16351c; font-weight: 600"


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
def trade_fundamental_snapshot(symbol: str):
    """Cache only successful Yahoo snapshots; Yahoo is fallback, not the primary batch source."""
    return build_fundamental_snapshot(symbol, yf.Ticker(symbol))


TRADE_TV_SOURCE = {
    "nasdaq": ("america", "NASDAQ"),
    "nyse": ("america", "NYSE"),
    "otc": ("america", "OTC"),
    "lse": ("uk", "LSE"),
    "lse_aim": ("uk", "LSE"),
    "tsx": ("canada", "TSX"),
    "xetra": ("germany", "XETR"),
    "gettex": ("germany", "GETTEX"),
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
    suffixes = (".L", ".TO", ".PA", ".DE", ".SW", ".MC", ".BR", ".VI", ".AS", ".LS")
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
        "latest positive FCF": math.isfinite(fcf_ttm),
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
    )


@st.cache_data(ttl=21600, show_spinner=False)
def trade_tradingview_snapshots(symbols_tuple: tuple[str, ...], universe_kind: str) -> Dict[str, FundamentalSnapshot]:
    """Fetch Trade fundamentals in one request instead of one Yahoo request per company."""
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


@st.cache_data(ttl=300, show_spinner=False)
def approved_trade_market_scan(
    symbols_tuple: tuple[str, ...], max_symbols: int, universe_kind: str,
    model_version: str,
) -> pd.DataFrame:
    # The explicit version is part of Streamlit's cache key. Bumping it prevents
    # results produced by an earlier rule ordering from being reused.
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

    for start in range(0, len(symbols), 60):
        chunk = symbols[start:start + 60]
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
                    continue
                raw_turnover = (frame["Close"] * frame["Volume"]).dropna().tail(20).median()
                turnover_gbp = raw_turnover * context["price_scale"] * quote_to_gbp
                if not math.isfinite(turnover_gbp) or turnover_gbp < 5_000_000:
                    continue
                technical = evaluate_price_setup(frame, benchmark)
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

    snapshots = []
    snapshot_by_symbol = {}
    snapshot_error_by_symbol = {}

    # Primary source: one TradingView batch request for every technically eligible
    # symbol. This avoids Yahoo's per-company quote-summary throttling.
    candidate_symbols = tuple(symbol for symbol, _technical in deep_candidates)
    try:
        tv_snapshots = trade_tradingview_snapshots(candidate_symbols, universe_kind)
    except Exception as exc:
        tv_snapshots = {}
        tv_batch_error = type(exc).__name__
    else:
        tv_batch_error = None

    snapshots.extend(tv_snapshots.values())
    snapshot_by_symbol.update(tv_snapshots)

    # Yahoo remains a fallback only for symbols TradingView did not return or markets
    # not yet covered by the batch source.
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
                snapshot = trade_fundamental_snapshot(symbol)
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
        fundamental = score_fundamental_snapshot(
            snapshot,
            snapshots,
            rate,
            sessions,
            # Event verification is intentionally deferred until a share has passed
            # the numerical fundamental gates and reached provisional-candidate stage.
            official_event_verified=False,
            apply_event_gate=False,
        )
        failures = fundamental["fundamental_failures"]
        if "EXCLUDED SECTOR" in failures:
            status = "BLOCKED"
            reason = "EXCLUDED SECTOR"
        elif failures:
            status = "BLOCKED"
            reason = "; ".join(failures)
        elif technical["technical_state"] == "WATCH":
            status = "WATCH"
            reason = technical["technical_reason"]
        else:
            candidate_check = score_fundamental_snapshot(
                snapshot,
                snapshots,
                rate,
                sessions,
                official_event_verified=False,
                apply_event_gate=True,
            )
            event_failures = [
                failure for failure in candidate_check["fundamental_failures"]
                if failure not in failures
            ]
            if event_failures:
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

    if not price_rows:
        return pd.DataFrame()
    output = pd.DataFrame(price_rows)
    order = {"PAPER CANDIDATE": 0, "WATCH": 1, "BLOCKED": 2}
    output["_status_order"] = output["Status"].map(order).fillna(9)
    output = output.sort_values(
        ["_status_order", "Technical score"], ascending=[True, False], na_position="last"
    ).drop(columns="_status_order")
    return output.reset_index(drop=True)


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


@st.cache_data(ttl=900, show_spinner=False)
def latest_portfolio_price(symbol: str) -> float:
    """Retrieve a recent quoted price for a portfolio holding."""
    ticker = yf.Ticker(symbol)
    price = np.nan
    try:
        price = safe(ticker.fast_info.get("last_price"))
    except Exception:
        pass
    if np.isnan(price) or price <= 0:
        history = ticker.history(period="5d", interval="1d", auto_adjust=False)
        if history is not None and not history.empty and "Close" in history.columns:
            closes = pd.to_numeric(history["Close"], errors="coerce").dropna()
            if not closes.empty:
                price = safe(closes.iloc[-1])
    return price


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


@st.cache_data(ttl=1800, show_spinner=False)
def analyse_portfolio_holding(symbol: str, shares: float, average_cost: float) -> dict:
    """Run the long-term framework for one existing portfolio position."""
    symbol = str(symbol).strip().upper()
    price = latest_portfolio_price(symbol)
    if np.isnan(price) or price <= 0:
        raise ValueError("current price unavailable")

    fund = valuation_fundamental_analysis(symbol, price)
    lt = long_term_analysis(symbol, price, fund)
    merged = {**fund, **lt, "price": price}
    decision = investment_decision(
        merged,
        owned=True,
        average_buy_price=average_cost if average_cost > 0 else None,
    )

    if decision["action"] == "SELL":
        action = "REVIEW FOR SALE"
    elif decision["action"] == "REASSESS":
        action = "REASSESS"
    elif lt.get("valuation_gate_pass"):
        action = "ADD CANDIDATE"
    else:
        action = "HOLD"

    quote_currency = str(fund.get("quote_currency") or "")
    gbp_rate = currency_to_gbp_rate(quote_currency)
    native_value = price * shares
    market_value_gbp = native_value * gbp_rate if not np.isnan(gbp_rate) else np.nan
    cost_value_gbp = average_cost * shares * gbp_rate if average_cost > 0 and not np.isnan(gbp_rate) else np.nan
    pnl_gbp = market_value_gbp - cost_value_gbp if not np.isnan(cost_value_gbp) else np.nan
    return_pct = (price / average_cost - 1) * 100 if average_cost > 0 else np.nan

    reasons = list(decision.get("reasons") or [])
    if action == "ADD CANDIDATE":
        reasons.insert(0, "hard gates and the strict DCF add-price gate currently pass")
    elif action == "HOLD":
        reasons.insert(0, "measurable thesis gates remain intact, but the current price does not qualify for adding")
    elif action == "REASSESS" and not reasons:
        reasons.append("one or more measurable thesis checks needs review")

    return {
        "Ticker": symbol,
        "Company": fund.get("name") or symbol,
        "Action": action,
        "Shares": shares,
        "Average cost": average_cost if average_cost > 0 else np.nan,
        "Price": price,
        "Return %": return_pct,
        "Market value £": market_value_gbp,
        "Cost basis £": cost_value_gbp,
        "Unrealised P/L £": pnl_gbp,
        "Quote currency": quote_currency or "Unknown",
        "Quality score": lt.get("investment_quality_score", lt.get("long_term_score", np.nan)),
        "Hard gates": "PASS" if lt.get("hard_gate_pass") else "FAIL",
        "Base intrinsic value": lt.get("dcf_base"),
        "Bear intrinsic value": lt.get("dcf_bear"),
        "Base margin of safety %": lt.get("margin_of_safety_base_pct"),
        "Required margin of safety %": lt.get("required_margin_of_safety_pct"),
        "Bear margin of safety %": lt.get("margin_of_safety_bear_pct"),
        "Sector": fund.get("sector") or "Unknown",
        "Industry": fund.get("industry") or "Unknown",
        "Country": fund.get("exchange_country") or "Unknown",
        "Review reason": "; ".join(dict.fromkeys(reason for reason in reasons if reason)),
        "Manual review required": lt.get("qualitative_review_items", ""),
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


st.title("📈 Stock Opportunity Screener")
st.caption("Swing-trade entries + fundamental hold quality. No broker connection or brokerage credentials required.")

st.markdown("""
<style>
@media (max-width: 700px) {
  .block-container { padding-top: 1rem; padding-left: .7rem; padding-right: .7rem; }
  h1 { font-size: 1.8rem !important; }
  div[data-testid="stMetricValue"] { font-size: 1.3rem; }
}
</style>
""", unsafe_allow_html=True)

with st.sidebar:
    st.header("Stock Screener")
    st.write("**Trade Search:** technical setups, entries, targets and risk/reward.")
    st.write("**Investment Search:** 10-years-to-forever quality gates, resilience and DCF valuation.")
    st.divider()
    watch_text = st.text_area(
        "Priority watchlist",
        value=PRIORITY_DEFAULT,
        help="Comma-separated Yahoo-style tickers. Add anything here without changing the code.",
    )
    st.success("Broker-independent mode: ON")
    st.caption("No Trading 212 credentials are used or stored.")

tab1, tab2, tab3, tab4, tab5 = st.tabs(
    ["Quick analyse", "Watchlist", "Trade Search", "Investment Search", "Portfolio Review"]
)

with tab1:
    c1, c2 = st.columns([3, 1])
    with c1:
        manual = st.text_input("Ticker", value="FLNC", placeholder="e.g. FLNC, AAPL, RR.L")
    with c2:
        st.write("")
        st.write("")
        analyse_clicked = st.button("Analyse", type="primary", use_container_width=True)

    if analyse_clicked and manual:
        with st.spinner(f"Analysing {manual.upper()}…"):
            res = analyse_symbol(manual)

        if res:
            a, b, c, d = st.columns(4)
            a.metric("Trade Setup", f"{res['trade_score']:.0f}/100")
            b.metric("Hold Quality", f"{res['hold_score']:.0f}/100")
            c.metric("Opportunity", f"{res['opportunity_score']:.0f}/100")
            d.metric("Classification", res["classification"])

            st.subheader(f"{res['name']} ({res['symbol']})")
            p1, p2, p3, p4 = st.columns(4)
            p1.metric("Current", fmt_price(res["price"]))
            p2.metric("Preferred entry", f"{fmt_price(res['preferred_low'])}–{fmt_price(res['preferred_high'])}")
            p3.metric("Strong entry", f"{fmt_price(res['strong_low'])}–{fmt_price(res['strong_high'])}")
            p4.metric("Swing target", fmt_price(res["swing_target"]), f"{res['upside_pct']:.1f}%")

            if res["in_preferred_zone"]:
                st.success("Current price is inside the preferred entry zone.")
            elif res["in_strong_zone"]:
                st.success("Current price is inside the strong entry zone.")

            st.plotly_chart(chart(res), use_container_width=True)

            m1, m2, m3, m4 = st.columns(4)
            m1.metric("RSI", res["rsi"])
            m2.metric("Risk / reward", f"{res['rr']:.2f}:1")
            m3.metric("Reassess below", fmt_price(res["invalidation"]))
            if not np.isnan(safe(res["analyst_target"])):
                m4.metric("Analyst mean target", fmt_price(res["analyst_target"]), f"{res['analyst_upside']:.1f}%")
            else:
                m4.metric("Analyst mean target", "—")

            with st.expander("Fundamental detail"):
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
                st.dataframe(pd.DataFrame({"Metric": rows.keys(), "Value": rows.values()}), hide_index=True, use_container_width=True)
        else:
            st.error("I couldn't retrieve enough market history for that ticker.")

with tab2:
    symbols = [x.strip().upper() for x in watch_text.replace("\n", ",").split(",") if x.strip()]
    if st.button("Scan watchlist", type="primary"):
        output = []
        prog = st.progress(0)
        for i, s in enumerate(symbols):
            try:
                r = analyse_symbol(s)
                if r:
                    output.append(r)
            except Exception:
                pass
            prog.progress((i + 1) / max(len(symbols), 1))
        prog.empty()

        if output:
            rows = pd.DataFrame([{
                "Ticker": r["symbol"],
                "Company": r["name"],
                "Trade": r["trade_score"],
                "Hold": r["hold_score"],
                "Opportunity": r["opportunity_score"],
                "Type": r["classification"],
                "Price": r["price"],
                "Preferred entry": f"{fmt_price(r['preferred_low'])}–{fmt_price(r['preferred_high'])}",
                "Target": r["swing_target"],
                "Upside %": r["upside_pct"],
                "Preferred now": r["in_preferred_zone"],
                "Candle caution": "CAUTION" if r.get("candle_caution") else "CLEAR",
                "Last candle": r.get("candle_pattern", "UNAVAILABLE"),
                "Channel": r.get("channel_direction", "UNAVAILABLE"),
                "Channel pos %": r.get("channel_position_pct", np.nan),
                "Channel R:R": r.get("channel_rr", np.nan),
                "Channel quality": r.get("channel_quality", "LOW"),
            } for r in output]).sort_values("Opportunity", ascending=False)
            st.dataframe(rows, hide_index=True, use_container_width=True)
        else:
            st.warning("No watchlist symbols returned enough data.")

with tab3:
    st.subheader("Trade Search")
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

            with st.spinner("Running the approved price, liquidity, fundamental, target and event gates…"):
                pre = approved_trade_market_scan(
                    tuple(universe), cap_choice, universe_kind, TRADE_RULEBOOK_BUILD
                )

            if pre.empty:
                st.warning("No current WATCH or confirmed crossover setups met the approved scan conditions.")
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
                st.dataframe(
                    pre[visible_quick].head(50).style.map(action_cell_style, subset=["Status"]),
                    hide_index=True,
                    use_container_width=True,
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
    st.subheader("Investment Search — 10 Years to Forever")
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
                    styled_fundamentals = filtered_fundamentals.style.map(
                        action_cell_style,
                        subset=["Action"],
                    )
                    st.caption(
                        "Action status: 🟢 BUY CANDIDATE · 🟠 WAIT · 🔴 PASS"
                    )
                    st.dataframe(
                        styled_fundamentals,
                        hide_index=True,
                        use_container_width=True,
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
    st.subheader("Portfolio Review")
    st.caption(
        "Enter one row per holding. Average cost must use the same quoted units as Yahoo "
        "(for example, pence for a London share quoted in GBp). No broker connection is required."
    )

    uploaded_portfolio = st.file_uploader(
        "Import holdings CSV (optional)",
        type=["csv"],
        key="portfolio_csv_upload",
        help="Required columns: Ticker, Shares and Average cost.",
    )
    portfolio_seed = pd.DataFrame([
        {"Ticker": "", "Shares": 0.0, "Average cost": 0.0},
    ])
    if uploaded_portfolio is not None:
        try:
            imported = pd.read_csv(uploaded_portfolio)
            aliases = {
                "ticker": "Ticker",
                "symbol": "Ticker",
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
            portfolio_seed = imported[["Ticker", "Shares", "Average cost"]].copy()
        except Exception as exc:
            st.error(f"The portfolio CSV could not be loaded: {exc}")

    def clear_portfolio_results():
        st.session_state.pop("portfolio_results", None)
        st.session_state.pop("portfolio_errors", None)

    upload_identity = (
        f"{uploaded_portfolio.name}_{getattr(uploaded_portfolio, 'size', 0)}"
        if uploaded_portfolio is not None
        else "manual"
    )
    edited_holdings = st.data_editor(
        portfolio_seed,
        num_rows="dynamic",
        hide_index=True,
        use_container_width=True,
        key=f"portfolio_editor_{upload_identity}",
        on_change=clear_portfolio_results,
        column_config={
            "Ticker": st.column_config.TextColumn(
                "Ticker",
                help="Use the Yahoo ticker, including suffixes such as .L or .TO.",
            ),
            "Shares": st.column_config.NumberColumn("Shares", min_value=0.0, format="%.4f"),
            "Average cost": st.column_config.NumberColumn(
                "Average cost",
                min_value=0.0,
                format="%.4f",
                help="Your average price per share in the stock's quoted currency/units.",
            ),
        },
    )

    d1, d2 = st.columns(2)
    with d1:
        analyse_portfolio_clicked = st.button(
            "Review Portfolio",
            type="primary",
            use_container_width=True,
        )
    with d2:
        st.download_button(
            "Download Holdings CSV",
            data=edited_holdings.to_csv(index=False).encode("utf-8"),
            file_name="stock_portfolio_holdings.csv",
            mime="text/csv",
            use_container_width=True,
        )

    st.info(
        "Portfolio actions: ADD CANDIDATE means the same strict hard-gate and DCF entry tests pass again; "
        "HOLD means the measurable thesis remains intact but the price is not cheap enough to add; "
        "REASSESS flags deterioration or specialist review; REVIEW FOR SALE flags extreme overvaluation for review, not an automatic order."
    )

    if analyse_portfolio_clicked:
        clean_holdings = edited_holdings.copy()
        clean_holdings["Ticker"] = clean_holdings["Ticker"].fillna("").astype(str).str.strip().str.upper()
        clean_holdings = clean_holdings[clean_holdings["Ticker"] != ""]
        duplicate_tickers = clean_holdings.loc[
            clean_holdings["Ticker"].duplicated(keep=False), "Ticker"
        ].unique().tolist()

        results = []
        errors = []
        if clean_holdings.empty:
            errors.append("Add at least one holding before running the review.")
        if duplicate_tickers:
            errors.append("Use one row per ticker. Duplicate rows: " + ", ".join(duplicate_tickers))

        if not errors:
            progress = st.progress(0)
            total_rows = len(clean_holdings)
            with st.spinner("Reviewing current prices, financial evidence and valuation…"):
                for position, (_, holding) in enumerate(clean_holdings.iterrows(), start=1):
                    ticker = holding["Ticker"]
                    shares = safe(holding.get("Shares"), 0.0)
                    average_cost = safe(holding.get("Average cost"), 0.0)
                    if shares <= 0:
                        errors.append(f"{ticker}: shares must be greater than zero")
                    else:
                        try:
                            results.append(analyse_portfolio_holding(ticker, shares, average_cost))
                        except Exception as exc:
                            errors.append(f"{ticker}: {exc.__class__.__name__}: {str(exc)[:180]}")
                    progress.progress(position / total_rows)
            progress.empty()

        st.session_state["portfolio_results"] = pd.DataFrame(results)
        st.session_state["portfolio_errors"] = errors

    portfolio_errors = st.session_state.get("portfolio_errors", [])
    for error in portfolio_errors:
        st.warning(error)

    portfolio_results = st.session_state.get("portfolio_results")
    if isinstance(portfolio_results, pd.DataFrame) and not portfolio_results.empty:
        portfolio_results = portfolio_results.copy()
        all_values_converted = portfolio_results["Market value £"].notna().all()
        total_value = portfolio_results["Market value £"].sum() if all_values_converted else np.nan
        total_cost_known = portfolio_results["Cost basis £"].notna().all()
        total_cost = portfolio_results["Cost basis £"].sum() if total_cost_known else np.nan
        total_pnl = total_value - total_cost if not np.isnan(total_value) and not np.isnan(total_cost) else np.nan
        total_return = total_pnl / total_cost * 100 if not np.isnan(total_pnl) and total_cost > 0 else np.nan

        if not np.isnan(total_value) and total_value > 0:
            portfolio_results["Weight %"] = portfolio_results["Market value £"] / total_value * 100

            for row_index, row in portfolio_results.iterrows():
                weight = safe(row.get("Weight %"))
                current_action = str(row.get("Action") or "")
                reason = str(row.get("Review reason") or "").strip()
                concentration_reason = ""
                if weight > 25:
                    concentration_reason = (
                        f"position is {weight:.1f}% of the portfolio; review reducing or rebalancing "
                        "to control single-stock concentration"
                    )
                    if current_action in {"ADD CANDIDATE", "HOLD"}:
                        portfolio_results.at[row_index, "Action"] = "REDUCE / REBALANCE"
                elif weight > 15 and current_action == "ADD CANDIDATE":
                    concentration_reason = (
                        f"position is already {weight:.1f}% of the portfolio; do not add before "
                        "reviewing concentration"
                    )
                    portfolio_results.at[row_index, "Action"] = "HOLD"
                if concentration_reason:
                    portfolio_results.at[row_index, "Review reason"] = "; ".join(
                        item for item in (concentration_reason, reason) if item
                    )
        else:
            portfolio_results["Weight %"] = np.nan

        add_count = int((portfolio_results["Action"] == "ADD CANDIDATE").sum())
        review_count = int(
            portfolio_results["Action"].isin(
                ["REASSESS", "REVIEW FOR SALE", "REDUCE / REBALANCE"]
            ).sum()
        )
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Portfolio value", "—" if np.isnan(total_value) else f"£{total_value:,.2f}")
        m2.metric(
            "Unrealised return",
            "—" if np.isnan(total_return) else f"{total_return:.1f}%",
            None if np.isnan(total_pnl) else f"£{total_pnl:,.2f}",
        )
        m3.metric("Add candidates", add_count)
        m4.metric("Actions to review", review_count)

        if not all_values_converted:
            st.warning(
                "At least one quote currency could not be converted to GBP. Portfolio totals, weights and concentration checks are hidden for safety."
            )

        display_columns = [
            "Ticker", "Company", "Action", "Weight %", "Shares", "Average cost", "Price",
            "Return %", "Market value £", "Unrealised P/L £", "Quote currency", "Quality score",
            "Hard gates", "Base intrinsic value", "Bear intrinsic value", "Base margin of safety %",
            "Required margin of safety %", "Bear margin of safety %", "Sector", "Industry", "Country",
            "Review reason", "Manual review required",
        ]
        styled_portfolio = portfolio_results[display_columns].style.map(
            portfolio_action_cell_style,
            subset=["Action"],
        ).map(
            allocation_cell_style,
            subset=["Weight %"],
        )
        st.caption(
            "Action status: 🟢 ADD CANDIDATE / HOLD · 🟠 REASSESS · "
            "🔴 REVIEW FOR SALE / REDUCE / REBALANCE. "
            "Position weight: 🟢 ≤15% · 🟠 >15% · 🔴 >25%."
        )
        st.dataframe(
            styled_portfolio,
            hide_index=True,
            use_container_width=True,
            column_config={
                "Weight %": st.column_config.NumberColumn(format="%.1f%%"),
                "Shares": st.column_config.NumberColumn(format="%.4f"),
                "Average cost": st.column_config.NumberColumn(format="%.4f"),
                "Price": st.column_config.NumberColumn(format="%.4f"),
                "Return %": st.column_config.NumberColumn(format="%.1f%%"),
                "Market value £": st.column_config.NumberColumn(format="£%.2f"),
                "Unrealised P/L £": st.column_config.NumberColumn(format="£%.2f"),
                "Quality score": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.1f"),
                "Base margin of safety %": st.column_config.NumberColumn(format="%.1f%%"),
                "Required margin of safety %": st.column_config.NumberColumn(format="%.1f%%"),
                "Bear margin of safety %": st.column_config.NumberColumn(format="%.1f%%"),
            },
        )

        if all_values_converted and total_value > 0:
            sector_allocation = (
                portfolio_results.groupby("Sector", dropna=False)["Market value £"]
                .sum()
                .sort_values(ascending=False)
                .rename("Market value £")
                .reset_index()
            )
            sector_allocation["Weight %"] = sector_allocation["Market value £"] / total_value * 100
            st.subheader("Sector allocation")
            st.dataframe(
                sector_allocation,
                hide_index=True,
                use_container_width=True,
                column_config={
                    "Market value £": st.column_config.NumberColumn(format="£%.2f"),
                    "Weight %": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.1f%%"),
                },
            )

    st.caption(
        "Holdings remain in the current browser session. Download the CSV after editing so you can restore the portfolio after an app restart or redeployment."
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
