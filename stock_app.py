from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st
import yfinance as yf

st.set_page_config(page_title="Stock Opportunity Screener", page_icon="📈", layout="wide")

PRIORITY_DEFAULT = "FLNC, SPCX"
T212_URL = "https://live.trading212.com/api/v0/equity/metadata/instruments"


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


def technical_analysis(symbol: str) -> Optional[Dict]:
    t = yf.Ticker(symbol)
    df = t.history(period="1y", interval="1d", auto_adjust=False)
    if df is None or len(df) < 80:
        return None

    df = df.dropna(subset=["Open","High","Low","Close","Volume"]).copy()
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
    df["BBU"] = mid + 2*sd
    df["BBL"] = mid - 2*sd

    price = float(close.iloc[-1])
    sma20 = float(df["SMA20"].iloc[-1])
    sma50 = float(df["SMA50"].iloc[-1])
    sma200 = safe(df["SMA200"].iloc[-1])
    rsi_now = float(df["RSI"].iloc[-1])
    atr_now = float(df["ATR"].iloc[-1])
    vol20 = float(df["Volume"].iloc[-20:].mean())
    vol_now = float(df["Volume"].iloc[-1])
    macd_now = float(df["MACD_H"].iloc[-1])
    macd_prev = float(df["MACD_H"].iloc[-4])

    recent20 = df.iloc[-21:-1]
    recent50 = df.iloc[-51:-1]
    support20 = float(recent20["Low"].min())
    support50 = float(recent50["Low"].min())
    resistance20 = float(recent20["High"].max())
    resistance50 = float(recent50["High"].max())

    supports = [x for x in [sma20, sma50, support20, support50] if not np.isnan(x) and x < price]
    supports = sorted(set(supports), reverse=True)
    support1 = supports[0] if supports else price - atr_now
    support2 = supports[1] if len(supports) > 1 else max(price - 2*atr_now, support1 - atr_now)

    preferred_low = max(0.01, support1 - 0.35*atr_now)
    preferred_high = support1 + 0.35*atr_now
    strong_low = max(0.01, support2 - 0.35*atr_now)
    strong_high = support2 + 0.35*atr_now
    invalidation = max(0.01, support2 - 1.0*atr_now)

    resistance = max(resistance20, resistance50)
    risk = max(price - invalidation, 0.01)
    two_r = price + 2*risk
    swing_target = min(resistance, two_r) if resistance > price * 1.03 else two_r
    upside = (swing_target / price - 1) * 100

    score = 0.0
    # Trend / structure 20
    if price > sma20: score += 7
    if sma20 > sma50: score += 7
    if np.isnan(sma200) or sma50 > sma200: score += 6

    # Entry quality 25
    dist_support = (price / support1 - 1) * 100 if support1 > 0 else 99
    if 0 <= dist_support <= 3: score += 12
    elif dist_support <= 6: score += 8
    elif dist_support <= 10: score += 4
    if 42 <= rsi_now <= 58: score += 8
    elif 35 <= rsi_now <= 65: score += 5
    if price <= float(df["BBU"].iloc[-1]) and price >= float(df["BBL"].iloc[-1]): score += 5

    # Momentum 15
    if macd_now > macd_prev: score += 8
    if macd_now > 0: score += 4
    if rsi_now > float(df["RSI"].iloc[-5]): score += 3

    # Volume 10
    vr = vol_now / vol20 if vol20 else 1
    if 0.7 <= vr <= 1.8: score += 5
    if vr > 1.15 and close.iloc[-1] > close.iloc[-2]: score += 5

    # R:R / upside 20
    rr = max((swing_target-price)/risk, 0)
    if rr >= 2.5: score += 12
    elif rr >= 2: score += 10
    elif rr >= 1.5: score += 6
    if upside >= 15: score += 8
    elif upside >= 10: score += 6
    elif upside >= 5: score += 3

    # Not overextended 10
    extension = (price/sma20 - 1)*100 if sma20 else 0
    if extension <= 3: score += 10
    elif extension <= 6: score += 6
    elif extension <= 10: score += 3

    return {
        "history": df,
        "price": price,
        "trade_score": round(min(score, 100), 1),
        "rsi": round(rsi_now, 1),
        "sma20": sma20,
        "sma50": sma50,
        "support1": support1,
        "support2": support2,
        "preferred_low": preferred_low,
        "preferred_high": preferred_high,
        "strong_low": strong_low,
        "strong_high": strong_high,
        "invalidation": invalidation,
        "swing_target": swing_target,
        "upside_pct": round(upside, 1),
        "rr": round(max((swing_target-price)/risk, 0), 2),
        "volume_ratio": round(vr, 2),
    }


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
    # Market cap 10
    if not np.isnan(market_cap):
        if market_cap >= 10e9: score += 10
        elif market_cap >= 3e9: score += 8
        elif market_cap >= 1e9: score += 5

    # Revenue growth 15
    if revenue_growth is not None:
        if revenue_growth >= 20: score += 15
        elif revenue_growth >= 10: score += 12
        elif revenue_growth >= 5: score += 9
        elif revenue_growth > 0: score += 4

    # EPS growth 15
    if earnings_growth is not None:
        if earnings_growth >= 30: score += 15
        elif earnings_growth >= 20: score += 13
        elif earnings_growth >= 10: score += 9
        elif earnings_growth > 0: score += 5

    # Margin 15
    if margin is not None:
        if margin >= 20: score += 15
        elif margin >= 10: score += 12
        elif margin >= 5: score += 7
        elif margin > 0: score += 4

    # Debt 10. Yahoo debtToEquity is normally percentage points.
    if not np.isnan(debt_equity):
        if debt_equity <= 50: score += 10
        elif debt_equity <= 100: score += 8
        elif debt_equity <= 150: score += 6
        elif debt_equity <= 250: score += 3
    else:
        score += 4

    # FCF 10
    if not np.isnan(fcf):
        if fcf > 0: score += 10
    else:
        score += 3

    # Analyst outlook 15
    if not np.isnan(target_upside):
        if target_upside >= 25: score += 10
        elif target_upside >= 15: score += 8
        elif target_upside >= 10: score += 6
        elif target_upside > 0: score += 3
    if analysts >= 10: score += 5
    elif analysts >= 5: score += 3

    # Dividend / payout 10; growth companies are not punished for no dividend.
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
    return {"symbol": symbol, **tech, **fund, "opportunity_score": sc.opportunity, "classification": sc.classification}


def t212_available() -> bool:
    return "T212_API_KEY" in st.secrets and "T212_API_SECRET" in st.secrets


@st.cache_data(ttl=3600, show_spinner=False)
def get_t212_instruments() -> pd.DataFrame:
    if not t212_available():
        return pd.DataFrame()
    r = requests.get(
        T212_URL,
        auth=(st.secrets["T212_API_KEY"], st.secrets["T212_API_SECRET"]),
        timeout=30,
    )
    r.raise_for_status()
    data = r.json()
    df = pd.DataFrame(data)
    if df.empty:
        return df
    return df[df["type"].eq("STOCK")].copy()


def chart(result: Dict) -> go.Figure:
    d = result["history"].tail(120)
    fig = go.Figure()
    fig.add_trace(go.Candlestick(x=d.index, open=d["Open"], high=d["High"], low=d["Low"], close=d["Close"], name=result["symbol"]))
    fig.add_trace(go.Scatter(x=d.index, y=d["SMA20"], mode="lines", name="SMA20"))
    fig.add_trace(go.Scatter(x=d.index, y=d["SMA50"], mode="lines", name="SMA50"))
    fig.add_hrect(y0=result["preferred_low"], y1=result["preferred_high"], opacity=.12, line_width=0, annotation_text="Preferred entry")
    fig.add_hrect(y0=result["strong_low"], y1=result["strong_high"], opacity=.08, line_width=0, annotation_text="Strong entry")
    fig.add_hline(y=result["invalidation"], line_dash="dot", annotation_text="Reassess / invalidation")
    fig.add_hline(y=result["swing_target"], line_dash="dash", annotation_text="Swing target")
    fig.update_layout(height=520, xaxis_rangeslider_visible=False, margin=dict(l=10,r=10,t=30,b=10))
    return fig


st.title("📈 Stock Opportunity Screener")
st.caption("Finds attractive swing entries, while separately scoring whether the fundamentals justify holding longer.")

with st.sidebar:
    st.header("Scoring")
    st.write("**Trade Setup:** technical entry, momentum, volume, support and risk/reward.")
    st.write("**Hold Quality:** growth, margins, debt, cash flow, analyst upside and shareholder quality.")
    st.divider()
    watch_text = st.text_area("Priority watchlist", value=PRIORITY_DEFAULT, help="Comma-separated Yahoo-style tickers. Add any stock here without changing the screener code.")
    st.caption("Trading 212 universe: " + ("Connected" if t212_available() else "Not connected yet"))

tab1, tab2, tab3 = st.tabs(["Quick analyse", "Watchlist screener", "Trading 212 universe"])

with tab1:
    c1, c2 = st.columns([3,1])
    with c1:
        manual = st.text_input("Ticker", value="FLNC", placeholder="e.g. FLNC, SPCX, AAPL")
    with c2:
        go_btn = st.button("Analyse", type="primary", use_container_width=True)

    if manual:
        with st.spinner(f"Analysing {manual.upper()}…"):
            res = analyse_symbol(manual)
        if res:
            a,b,c,d = st.columns(4)
            a.metric("Trade Setup", f"{res['trade_score']:.0f}/100")
            b.metric("Hold Quality", f"{res['hold_score']:.0f}/100")
            c.metric("Opportunity", f"{res['opportunity_score']:.0f}/100")
            d.metric("Classification", res["classification"])

            st.subheader(f"{res['name']} ({res['symbol']})")
            p1,p2,p3,p4 = st.columns(4)
            p1.metric("Current", fmt_price(res["price"]))
            p2.metric("Preferred entry", f"{fmt_price(res['preferred_low'])}–{fmt_price(res['preferred_high'])}")
            p3.metric("Strong entry", f"{fmt_price(res['strong_low'])}–{fmt_price(res['strong_high'])}")
            p4.metric("Swing target", fmt_price(res["swing_target"]), f"{res['upside_pct']:.1f}%")

            st.plotly_chart(chart(res), use_container_width=True)

            m1,m2,m3,m4 = st.columns(4)
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
            prog.progress((i+1)/max(len(symbols),1))
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
            } for r in output]).sort_values("Opportunity", ascending=False)
            st.dataframe(rows, hide_index=True, use_container_width=True)
        else:
            st.warning("No watchlist symbols returned enough data.")

with tab3:
    if not t212_available():
        st.info("The Trading 212 integration is built in but not connected yet. Once we add your read-only API key and secret to Streamlit Secrets, this tab can pull the official list of stocks available in your Trading 212 account.")
        st.write("You can still analyse any ticker immediately in **Quick analyse** or add it to the watchlist without changing the code.")
    else:
        try:
            instruments = get_t212_instruments()
            st.success(f"Trading 212 connected — {len(instruments):,} stock instruments found.")
            search = st.text_input("Search Trading 212 stocks", placeholder="Ticker or company name")
            view = instruments
            if search:
                q = search.lower()
                view = view[
                    view["shortName"].fillna("").str.lower().str.contains(q) |
                    view["name"].fillna("").str.lower().str.contains(q)
                ]
            st.dataframe(view[["shortName","name","currencyCode","isin","ticker"]].head(500), hide_index=True, use_container_width=True)
            st.caption("Full-market automated batch ranking is the next optimisation step after we confirm the Trading 212 ticker mapping and data-provider coverage.")
        except Exception as e:
            st.error(f"Trading 212 connection error: {e}")

with st.expander("How the scores work"):
    st.markdown("""
**Trade Setup /100** rewards constructive trend, proximity to support, RSI in a usable entry zone, improving MACD, healthy volume, risk/reward, realistic upside and avoiding overextended entries.

**Hold Quality /100** rewards market size, revenue and EPS growth, profit margin, manageable debt, positive free cash flow, analyst upside/coverage and sensible dividend/payout characteristics. Growth stocks are not automatically penalised for paying no dividend.

**Classification**
- **Swing-to-hold:** strong trade setup and fundamentals good enough to justify a longer hold.
- **Swing only:** strong trade setup, but fundamentals are not strong enough to turn a failed trade into an investment.
- **Core opportunity:** excellent hold quality with an acceptable entry.
- **Developing / Watch:** not strong enough yet.
""")

st.caption("Screening aid only, not financial advice. Market data and analyst estimates can be delayed or incomplete.")
