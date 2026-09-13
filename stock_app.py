from __future__ import annotations

import io
import math
from dataclasses import dataclass
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st
import yfinance as yf

st.set_page_config(page_title="Stock Opportunity Screener", page_icon="📈", layout="wide")

PRIORITY_DEFAULT = "FLNC, SPCX"

PUBLIC_UNIVERSES = {
    "US large + mid (recommended)": "us_core",
    "FTSE 350": "uk_350",
    "US + UK broad": "us_uk",
    "US all listed (slower)": "us_all",
}

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


def technical_from_df(df: pd.DataFrame) -> Optional[Dict]:
    if df is None or len(df) < 80:
        return None

    df = df.dropna(subset=["Open", "High", "Low", "Close", "Volume"]).copy()
    if len(df) < 80:
        return None

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

    price = safe(close.iloc[-1])
    sma20 = safe(df["SMA20"].iloc[-1])
    sma50 = safe(df["SMA50"].iloc[-1])
    sma200 = safe(df["SMA200"].iloc[-1])
    rsi_now = safe(df["RSI"].iloc[-1])
    atr_now = safe(df["ATR"].iloc[-1])
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
    supports = sorted(set(round(x, 8) for x in supports), reverse=True)
    support1 = supports[0] if supports else max(0.01, price - atr_now)
    support2 = supports[1] if len(supports) > 1 else max(0.01, price - 2*atr_now)

    preferred_low = max(0.01, support1 - 0.35*atr_now)
    preferred_high = support1 + 0.35*atr_now
    strong_low = max(0.01, support2 - 0.35*atr_now)
    strong_high = support2 + 0.35*atr_now
    invalidation = max(0.01, support2 - atr_now)

    resistance = max(resistance20, resistance50)
    risk = max(price - invalidation, 0.01)
    two_r = price + 2*risk
    swing_target = min(resistance, two_r) if resistance > price * 1.03 else two_r
    upside = (swing_target / price - 1) * 100

    score = 0.0

    # Trend / structure: 20
    if price > sma20:
        score += 7
    if sma20 > sma50:
        score += 7
    if np.isnan(sma200) or sma50 > sma200:
        score += 6

    # Entry quality: 25
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

    bbu = safe(df["BBU"].iloc[-1])
    bbl = safe(df["BBL"].iloc[-1])
    if not np.isnan(bbu) and not np.isnan(bbl) and bbl <= price <= bbu:
        score += 5

    # Momentum: 15
    if macd_now > macd_prev:
        score += 8
    if macd_now > 0:
        score += 4
    if rsi_now > safe(df["RSI"].iloc[-5], rsi_now):
        score += 3

    # Volume: 10
    vr = vol_now / vol20 if vol20 else 1
    if 0.7 <= vr <= 1.8:
        score += 5
    if vr > 1.15 and close.iloc[-1] > close.iloc[-2]:
        score += 5

    # R:R / upside: 20
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

    # Avoid chasing: 10
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
    }


def technical_analysis(symbol: str) -> Optional[Dict]:
    t = yf.Ticker(symbol)
    df = t.history(period="1y", interval="1d", auto_adjust=False)
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


@st.cache_data(ttl=86400, show_spinner=False)
def wikipedia_symbols(url: str, ticker_names: tuple[str, ...], suffix: str = "") -> List[str]:
    tables = pd.read_html(url)
    for table in tables:
        cols = [str(c).strip() for c in table.columns]
        lookup = {str(c).strip().lower(): c for c in table.columns}
        chosen = None
        for name in ticker_names:
            if name.lower() in lookup:
                chosen = lookup[name.lower()]
                break
        if chosen is None:
            continue
        vals = table[chosen].dropna().astype(str).tolist()
        out = []
        for s in vals:
            s = s.strip()
            if not s or s.lower() == "nan":
                continue
            s = s.replace(".", "-")
            if suffix and not s.endswith(suffix):
                s += suffix
            out.append(s)
        if len(out) >= 50:
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


@st.cache_data(ttl=86400, show_spinner=False)
def get_universe(kind: str) -> List[str]:
    sp500 = wikipedia_symbols(
        "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
        ("Symbol",),
    )
    sp400 = wikipedia_symbols(
        "https://en.wikipedia.org/wiki/List_of_S%26P_400_companies",
        ("Ticker symbol", "Symbol", "Ticker"),
    )
    nasdaq100 = wikipedia_symbols(
        "https://en.wikipedia.org/wiki/Nasdaq-100",
        ("Ticker", "Ticker symbol", "Symbol"),
    )
    ftse100 = wikipedia_symbols(
        "https://en.wikipedia.org/wiki/FTSE_100_Index",
        ("Ticker", "EPIC", "Symbol"),
        ".L",
    )
    ftse250 = wikipedia_symbols(
        "https://en.wikipedia.org/wiki/FTSE_250_Index",
        ("Ticker", "EPIC", "Symbol"),
        ".L",
    )

    us_core = sorted(set(sp500 + sp400 + nasdaq100))
    uk350 = sorted(set(ftse100 + ftse250))

    if kind == "us_core":
        return us_core
    if kind == "uk_350":
        return uk350
    if kind == "us_uk":
        return sorted(set(us_core + uk350))
    if kind == "us_all":
        return us_all_listed()
    return us_core


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
    symbols = list(symbols_tuple)[:max_symbols] if max_symbols > 0 else list(symbols_tuple)
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
                })
            except Exception:
                continue

    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values(["Trade", "Upside %"], ascending=[False, False]).reset_index(drop=True)


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
    fig.add_trace(go.Scatter(x=d.index, y=d["SMA20"], mode="lines", name="SMA20"))
    fig.add_trace(go.Scatter(x=d.index, y=d["SMA50"], mode="lines", name="SMA50"))
    fig.add_hrect(
        y0=result["preferred_low"],
        y1=result["preferred_high"],
        opacity=.12,
        line_width=0,
        annotation_text="Preferred entry",
    )
    fig.add_hrect(
        y0=result["strong_low"],
        y1=result["strong_high"],
        opacity=.08,
        line_width=0,
        annotation_text="Strong entry",
    )
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
    st.header("Scoring")
    st.write("**Trade Setup:** entry, trend, momentum, volume, support and risk/reward.")
    st.write("**Hold Quality:** growth, margins, debt, cash flow and analyst outlook.")
    st.divider()
    watch_text = st.text_area(
        "Priority watchlist",
        value=PRIORITY_DEFAULT,
        help="Comma-separated Yahoo-style tickers. Add anything here without changing the code.",
    )
    st.success("Broker-independent mode: ON")
    st.caption("No Trading 212 credentials are used or stored.")

tab1, tab2, tab3 = st.tabs(["Quick analyse", "Watchlist", "Broad market screener"])

with tab1:
    c1, c2 = st.columns([3, 1])
    with c1:
        manual = st.text_input("Ticker", value="FLNC", placeholder="e.g. FLNC, AAPL, RR.L")
    with c2:
        st.write("")
        st.write("")
        st.button("Analyse", type="primary", use_container_width=True)

    if manual:
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
            } for r in output]).sort_values("Opportunity", ascending=False)
            st.dataframe(rows, hide_index=True, use_container_width=True)
        else:
            st.warning("No watchlist symbols returned enough data.")

with tab3:
    st.subheader("Broad market screener")
    st.caption("Stage 1 scans price/volume data across the selected public universe. Stage 2 runs slower fundamentals only on the strongest technical candidates.")

    u1, u2, u3, u4 = st.columns(4)
    with u1:
        universe_label = st.selectbox("Universe", list(PUBLIC_UNIVERSES.keys()), index=0)
    with u2:
        cap_choice = st.selectbox("Maximum symbols", [250, 500, 1000, 2000, 0], index=2, format_func=lambda x: "All" if x == 0 else f"{x:,}")
    with u3:
        min_turnover_m = st.number_input("Min avg daily turnover (m)", 0.1, 100.0, 1.0, 0.5)
    with u4:
        deep_n = st.selectbox("Deep-score top", [10, 15, 20, 30], index=2)

    st.caption("Recommended starting point: US large + mid, 1,000 symbols, £/$1m+ daily turnover, deep-score top 20.")

    if st.button("Run broad market scan", type="primary", use_container_width=True):
        with st.spinner("Loading public stock universe…"):
            universe = get_universe(PUBLIC_UNIVERSES[universe_label])

        if not universe:
            st.error("The public universe list could not be loaded right now.")
        else:
            limit_text = "all" if cap_choice == 0 else f"{min(cap_choice, len(universe)):,}"
            st.info(f"Universe loaded: {len(universe):,} tickers. Scanning {limit_text} symbols.")

            with st.spinner("Stage 1: scanning price, volume, support, momentum and risk/reward…"):
                pre = technical_market_scan(tuple(universe), cap_choice, min_turnover_m * 1_000_000)

            if pre.empty:
                st.warning("No symbols returned usable technical data under these filters.")
            else:
                st.success(f"Stage 1 complete: {len(pre):,} liquid stocks scored technically.")
                st.subheader("Best technical entries")
                quick_cols = ["Ticker", "Trade", "Price", "RSI", "R:R", "Upside %", "Preferred now", "Strong now", "Target"]
                st.dataframe(pre[quick_cols].head(30), hide_index=True, use_container_width=True)

                with st.spinner(f"Stage 2: checking fundamentals on the top {deep_n} technical setups…"):
                    ranked = deep_score_shortlist(pre, deep_n)

                if ranked.empty:
                    st.warning("Technical candidates were found, but fundamental data was unavailable for the shortlist.")
                else:
                    st.subheader("Best overall opportunities")
                    st.dataframe(
                        ranked[[
                            "Ticker", "Opportunity", "Trade", "Hold", "Type", "Price",
                            "Preferred entry", "Strong entry", "Target", "Upside %",
                            "R:R", "Analyst target", "Analyst upside %", "Analysts"
                        ]],
                        hide_index=True,
                        use_container_width=True,
                    )

                    st.subheader("Top 5 quick view")
                    for _, q in ranked.head(5).iterrows():
                        with st.expander(f"{q['Ticker']} — Opportunity {q['Opportunity']:.1f}/100 — {q['Type']}"):
                            c1, c2, c3 = st.columns(3)
                            c1.metric("Trade", f"{q['Trade']:.0f}/100")
                            c2.metric("Hold", f"{q['Hold']:.0f}/100")
                            c3.metric("Upside", f"{q['Upside %']:.1f}%")
                            st.write(f"**Current:** {fmt_price(q['Price'])}")
                            st.write(f"**Preferred entry:** {q['Preferred entry']}")
                            st.write(f"**Strong entry:** {q['Strong entry']}")
                            st.write(f"**Swing target:** {fmt_price(q['Target'])} · **R:R:** {q['R:R']:.2f}:1")

with st.expander("How the scores work"):
    st.markdown("""
**Trade Setup /100** rewards constructive trend, proximity to support, RSI in a usable entry zone, improving MACD, healthy volume, risk/reward, realistic upside and avoiding overextended entries.

**Hold Quality /100** rewards market size, revenue and EPS growth, profit margin, manageable debt, positive free cash flow, analyst upside/coverage and sensible dividend/payout characteristics. Growth stocks are not automatically penalised for paying no dividend.

**Opportunity Score** is currently 55% Trade Setup + 45% Hold Quality.

**Classification**
- **Swing-to-hold:** strong trade setup and fundamentals good enough to justify a longer hold.
- **Swing only:** strong trade setup, but fundamentals are not strong enough to turn a failed trade into an investment.
- **Core opportunity:** excellent hold quality with an acceptable entry.
- **Developing / Watch:** not strong enough yet.

The broad screener uses a two-stage process so it does not make thousands of slow fundamental-data calls. Technical data is used to narrow the universe first, then fundamentals are checked on the best setups.
""")

st.caption("Screening aid only, not financial advice. Public market data and analyst estimates can be delayed, incomplete or unavailable for some listings.")
