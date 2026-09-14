from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Dict, Iterable, List

import numpy as np
import pandas as pd
import requests
import streamlit as st
import plotly.graph_objects as go


st.set_page_config(page_title="Meme Coin Screener", page_icon="🐸", layout="wide")

API = "https://api.dexscreener.com"
HEADERS = {"User-Agent": "MemeCoinScreener/1.0"}

CHAIN_OPTIONS = {
    "Solana": "solana",
    "Base": "base",
    "Ethereum": "ethereum",
    "BNB Chain": "bsc",
    "Robinhood Chain": "robinhood",
    "Arbitrum": "arbitrum",
    "Polygon": "polygon",
}

DEFAULTS = {
    "min_liquidity": 50_000.0,
    "min_volume_24h": 100_000.0,
    "min_market_cap": 100_000.0,
    "max_market_cap": 50_000_000.0,
    "min_pair_age_hours": 2.0,
    "max_1h_change": 12.0,
    "max_24h_change": 45.0,
    "shortlist_score": 65.0,
    "min_circulating_pct": 10.0,
}


def safe(v, default=np.nan):
    try:
        x = float(v)
        return x if math.isfinite(x) else default
    except Exception:
        return default


@st.cache_data(ttl=120, show_spinner=False)
def get_json(url: str):
    r = requests.get(url, headers=HEADERS, timeout=20)
    r.raise_for_status()
    return r.json()


def chunks(items: List[str], n: int) -> Iterable[List[str]]:
    for i in range(0, len(items), n):
        yield items[i : i + n]


def token_key(chain_id: str, token_address: str) -> str:
    return f"{chain_id}:{token_address}".lower()


@st.cache_data(ttl=120, show_spinner=False)
def dex_search(query: str) -> List[Dict]:
    query = str(query or "").strip()
    if not query:
        return []
    r = requests.get(
        API + "/latest/dex/search",
        params={"q": query},
        headers=HEADERS,
        timeout=20,
    )
    r.raise_for_status()
    data = r.json() or {}
    pairs = data.get("pairs") or []
    return pairs if isinstance(pairs, list) else []


@st.cache_data(ttl=120, show_spinner=False)
def discovery_universe() -> Dict[str, Dict]:
    """Discover emerging tokens from DexScreener's public discovery surfaces."""
    sources = {}

    endpoints = {
        "Profile": "/token-profiles/latest/v1",
        "Boost": "/token-boosts/latest/v1",
        "Top boost": "/token-boosts/top/v1",
        "Community takeover": "/community-takeovers/latest/v1",
    }

    for source, path in endpoints.items():
        try:
            data = get_json(API + path)
        except Exception:
            continue
        if isinstance(data, dict):
            data = [data]
        for item in data or []:
            chain = str(item.get("chainId") or "").lower()
            address = str(item.get("tokenAddress") or "")
            if not chain or not address:
                continue
            k = token_key(chain, address)
            row = sources.setdefault(
                k,
                {
                    "chainId": chain,
                    "tokenAddress": address,
                    "sources": set(),
                    "profile_description": "",
                    "profile_links": [],
                    "boost_amount": 0.0,
                    "boost_total": 0.0,
                    "community_takeover": False,
                },
            )
            row["sources"].add(source)
            if item.get("description"):
                row["profile_description"] = item.get("description") or ""
            if item.get("links"):
                row["profile_links"] = item.get("links") or []
            row["boost_amount"] = max(row["boost_amount"], safe(item.get("amount"), 0))
            row["boost_total"] = max(row["boost_total"], safe(item.get("totalAmount"), 0))
            if source == "Community takeover":
                row["community_takeover"] = True

    return sources


def fetch_pairs_for_tokens(tokens: Dict[str, Dict], allowed_chains: set[str]) -> List[Dict]:
    grouped: Dict[str, List[str]] = {}
    for meta in tokens.values():
        chain = meta["chainId"]
        if chain not in allowed_chains:
            continue
        grouped.setdefault(chain, []).append(meta["tokenAddress"])

    pairs: List[Dict] = []
    for chain, addresses in grouped.items():
        unique = list(dict.fromkeys(addresses))
        for batch in chunks(unique, 30):
            try:
                url = f"{API}/tokens/v1/{chain}/" + ",".join(batch)
                data = get_json(url)
            except Exception:
                continue
            if isinstance(data, list):
                pairs.extend(data)
    return pairs


def best_pair_per_token(pairs: List[Dict]) -> Dict[str, Dict]:
    out: Dict[str, Dict] = {}
    for p in pairs:
        chain = str(p.get("chainId") or "").lower()
        base = p.get("baseToken") or {}
        address = str(base.get("address") or "")
        if not chain or not address:
            continue
        k = token_key(chain, address)
        liq = safe((p.get("liquidity") or {}).get("usd"), 0)
        current = out.get(k)
        if current is None or liq > safe((current.get("liquidity") or {}).get("usd"), 0):
            out[k] = p
    return out


def social_flags(pair: Dict, meta: Dict):
    info = pair.get("info") or {}
    socials = info.get("socials") or []
    websites = info.get("websites") or []
    profile_links = meta.get("profile_links") or []

    labels = []
    for s in socials:
        platform = str(s.get("platform") or "").lower()
        if platform:
            labels.append(platform)
    for l in profile_links:
        label = str(l.get("type") or l.get("label") or "").lower()
        if label:
            labels.append(label)

    x_present = any(x in labels for x in ["twitter", "x"])
    telegram_present = any("telegram" in x for x in labels)
    discord_present = any("discord" in x for x in labels)
    website_present = len(websites) > 0

    return {
        "X": x_present,
        "Telegram": telegram_present,
        "Discord": discord_present,
        "Website": website_present,
        "Social count": int(x_present) + int(telegram_present) + int(discord_present) + int(website_present),
    }


def narrative_quality(pair: Dict, meta: Dict, socials: Dict) -> Dict:
    base = pair.get("baseToken") or {}
    name = str(base.get("name") or "").strip()
    symbol = str(base.get("symbol") or "").strip()
    description = str(meta.get("profile_description") or "").strip()

    score = 0.0
    signals = []

    if 2 <= len(symbol) <= 8:
        score += 3
        signals.append("short ticker")
    if 3 <= len(name) <= 22:
        score += 3
        signals.append("simple name")
    if name and not any(ch.isdigit() for ch in name):
        score += 1

    if 20 <= len(description) <= 400:
        score += 4
        signals.append("clear story/profile")
    elif description:
        score += 2

    sources = len(meta.get("sources", []))
    if sources >= 3:
        score += 3
        signals.append("spreading across discovery surfaces")
    elif sources >= 2:
        score += 2

    if meta.get("community_takeover"):
        score += 3
        signals.append("community-owned narrative")

    social_count = socials.get("Social count", 0)
    if social_count >= 3:
        score += 3
        signals.append("multi-channel identity")
    elif social_count >= 2:
        score += 1

    score = round(min(20.0, score), 1)
    if score >= 16:
        label = "ICONIC POTENTIAL"
    elif score >= 12:
        label = "STRONG NARRATIVE"
    elif score >= 8:
        label = "DEVELOPING"
    else:
        label = "WEAK / UNCLEAR"

    return {
        "Narrative Score": score,
        "Narrative Strength": label,
        "Narrative Signals": "; ".join(signals),
    }


def pair_age_hours(pair_created_at) -> float:
    ts = safe(pair_created_at)
    if np.isnan(ts):
        return np.nan
    now_ms = datetime.now(timezone.utc).timestamp() * 1000
    return max(0.0, (now_ms - ts) / 3_600_000)


GECKO_NETWORKS = {
    "solana": "solana",
    "base": "base",
    "ethereum": "eth",
    "bsc": "bsc",
    "robinhood": "robinhood",
    "arbitrum": "arbitrum",
    "polygon": "polygon_pos",
}

CHART_TIMEFRAMES = {
    "15m": ("minute", "15", 160),
    "1h": ("hour", "1", 180),
    "4h": ("hour", "4", 180),
    "1d": ("day", "1", 180),
}


@st.cache_data(ttl=60, show_spinner=False)
def fetch_pool_ohlcv(chain_id: str, pool_address: str, token_address: str, chart_tf: str) -> pd.DataFrame:
    network = GECKO_NETWORKS.get(str(chain_id).lower(), str(chain_id).lower())
    timeframe, aggregate, limit = CHART_TIMEFRAMES.get(chart_tf, CHART_TIMEFRAMES["1h"])
    url = (
        f"https://api.geckoterminal.com/api/v2/networks/{network}"
        f"/pools/{pool_address}/ohlcv/{timeframe}"
    )
    params = {
        "aggregate": aggregate,
        "limit": limit,
        "currency": "usd",
        "token": token_address or "base",
        "include_empty_intervals": "true",
    }
    headers = {
        "User-Agent": HEADERS["User-Agent"],
        "Accept": "application/json;version=20230203",
    }
    r = requests.get(url, params=params, headers=headers, timeout=20)
    r.raise_for_status()
    payload = r.json() or {}
    rows = (((payload.get("data") or {}).get("attributes") or {}).get("ohlcv_list") or [])
    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="s", utc=True)
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["timestamp", "open", "high", "low", "close"]).sort_values("timestamp")
    if df.empty:
        return df

    df["EMA20"] = df["close"].ewm(span=20, adjust=False).mean()
    df["EMA50"] = df["close"].ewm(span=50, adjust=False).mean()

    delta = df["close"].diff()
    gain = delta.clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1/14, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    df["RSI"] = (100 - 100 / (1 + rs)).fillna(50)

    df["BBM"] = df["close"].rolling(20).mean()
    bb_sd = df["close"].rolling(20).std()
    df["BBU"] = df["BBM"] + 2 * bb_sd
    df["BBL"] = df["BBM"] - 2 * bb_sd
    df["BBW_PCT"] = (df["BBU"] - df["BBL"]) / df["BBM"].replace(0, np.nan) * 100
    return df


def meme_price_chart(df: pd.DataFrame, ticker: str, timeframe_label: str) -> go.Figure:
    fig = go.Figure()
    fig.add_trace(go.Candlestick(
        x=df["timestamp"],
        open=df["open"],
        high=df["high"],
        low=df["low"],
        close=df["close"],
        name=ticker,
    ))
    fig.add_trace(go.Scatter(
        x=df["timestamp"],
        y=df["BBU"],
        mode="lines",
        name="BB upper",
        line=dict(dash="dot"),
    ))
    fig.add_trace(go.Scatter(
        x=df["timestamp"],
        y=df["BBM"],
        mode="lines",
        name="BB mid",
    ))
    fig.add_trace(go.Scatter(
        x=df["timestamp"],
        y=df["BBL"],
        mode="lines",
        name="BB lower",
        line=dict(dash="dot"),
    ))
    fig.add_trace(go.Scatter(
        x=df["timestamp"],
        y=df["EMA20"],
        mode="lines",
        name="EMA20",
    ))
    fig.add_trace(go.Scatter(
        x=df["timestamp"],
        y=df["EMA50"],
        mode="lines",
        name="EMA50",
    ))
    fig.update_layout(
        height=560,
        xaxis_rangeslider_visible=False,
        margin=dict(l=10, r=10, t=45, b=10),
        title=f"{ticker} — {timeframe_label} candles",
        yaxis_title="Price (USD)",
    )
    return fig


def score_candidate(pair: Dict, meta: Dict, cfg: Dict) -> Dict:
    liq = safe((pair.get("liquidity") or {}).get("usd"), 0)
    reported_mcap = safe(pair.get("marketCap"))
    fdv = safe(pair.get("fdv"))
    mcap = reported_mcap
    if np.isnan(mcap) or mcap <= 0:
        mcap = fdv

    circulating_pct_proxy = (
        reported_mcap / fdv * 100
        if math.isfinite(reported_mcap) and reported_mcap > 0
        and math.isfinite(fdv) and fdv > 0
        else np.nan
    )
    fdv_mcap = (
        fdv / reported_mcap
        if math.isfinite(fdv) and fdv > 0
        and math.isfinite(reported_mcap) and reported_mcap > 0
        else np.nan
    )
    tokenomics_gate = (
        "PASS" if math.isfinite(circulating_pct_proxy)
        and circulating_pct_proxy >= cfg["min_circulating_pct"]
        else "FAIL" if math.isfinite(circulating_pct_proxy)
        else "UNKNOWN"
    )

    vol24 = safe((pair.get("volume") or {}).get("h24"), 0)
    vol6 = safe((pair.get("volume") or {}).get("h6"), 0)
    pc = pair.get("priceChange") or {}
    ch1 = safe(pc.get("h1"), 0)
    ch6 = safe(pc.get("h6"), 0)
    ch24 = safe(pc.get("h24"), 0)
    tx = pair.get("txns") or {}
    tx24 = tx.get("h24") or {}
    buys = safe(tx24.get("buys"), 0)
    sells = safe(tx24.get("sells"), 0)
    total_tx = buys + sells
    buy_ratio = buys / total_tx if total_tx > 0 else 0
    age_h = pair_age_hours(pair.get("pairCreatedAt"))
    socials = social_flags(pair, meta)
    narrative = narrative_quality(pair, meta, socials)
    active_boost = safe((pair.get("boosts") or {}).get("active"), 0)

    gates = []
    if liq < cfg["min_liquidity"]:
        gates.append("Low liquidity")
    if vol24 < cfg["min_volume_24h"]:
        gates.append("Low 24h volume")
    if np.isnan(mcap) or mcap < cfg["min_market_cap"]:
        gates.append("Market cap too small/unknown")
    elif mcap > cfg["max_market_cap"]:
        gates.append("Market cap above target range")
    if tokenomics_gate == "FAIL":
        gates.append(
            f"Low circulating float (<{cfg['min_circulating_pct']:.0f}%)"
        )
    elif tokenomics_gate == "UNKNOWN":
        gates.append("Circulating float could not be verified")
    if not np.isnan(age_h) and age_h < cfg["min_pair_age_hours"]:
        gates.append("Pair too new")

    too_late = ch1 > cfg["max_1h_change"] or ch24 > cfg["max_24h_change"]
    if too_late:
        gates.append("Already pumping / chase risk")

    score = 0.0

    # Liquidity quality: 12
    liq_ratio = liq / mcap if mcap and not np.isnan(mcap) else 0
    liquidity_score = 0.0
    liquidity_score += min(7, max(0, liq_ratio / 0.10 * 7))
    liquidity_score += min(5, max(0, math.log10(max(liq, 1) / 25_000) * 2.5))
    score += liquidity_score

    # Real activity: 10
    vol_liq = vol24 / liq if liq > 0 else 0
    activity_score = 0.0
    activity_score += min(6, max(0, vol_liq / 2.0 * 6))
    activity_score += min(4, max(0, total_tx / 2000 * 4))
    score += activity_score

    # Buy pressure: 8. Strong but not one-sided.
    if 0.53 <= buy_ratio <= 0.72:
        buy_pressure_score = 8.0
    elif 0.50 <= buy_ratio < 0.53 or 0.72 < buy_ratio <= 0.80:
        buy_pressure_score = 5.5
    elif buy_ratio > 0.80:
        buy_pressure_score = 2.5
    else:
        buy_pressure_score = max(0, buy_ratio / 0.50 * 3)
    score += buy_pressure_score

    # Community strength: 30
    # We deliberately combine social breadth with actual on-chain participation
    # rather than trusting follower counts alone, which are easy to manipulate.
    community_score = 0.0
    community_score += 3 if socials["X"] else 0
    community_score += 3 if socials["Telegram"] else 0
    community_score += 2 if socials["Discord"] else 0
    community_score += 2 if socials["Website"] else 0
    community_score += 3 if meta.get("profile_description") else 0
    community_score += 4 if meta.get("community_takeover") else 0

    if total_tx >= 5000:
        community_score += 8
    elif total_tx >= 2000:
        community_score += 6
    elif total_tx >= 500:
        community_score += 4
    elif total_tx >= 100:
        community_score += 2

    if buys >= 2500:
        community_score += 5
    elif buys >= 1000:
        community_score += 4
    elif buys >= 250:
        community_score += 3
    elif buys >= 50:
        community_score += 1

    community_score = min(30.0, community_score)
    score += community_score

    if community_score >= 24:
        community_strength = "VERY STRONG"
    elif community_score >= 18:
        community_strength = "STRONG"
    elif community_score >= 12:
        community_strength = "DEVELOPING"
    else:
        community_strength = "WEAK"

    # Narrative / cultural-icon potential: 20
    score += narrative["Narrative Score"]

    # Discovery/catalyst signals: 8, deliberately capped because boosts are paid.
    discovery_score = 0.0
    if active_boost > 0 or meta.get("boost_total", 0) > 0:
        discovery_score += 3
    if len(meta.get("sources", [])) >= 2:
        discovery_score += 3
    if "Top boost" in meta.get("sources", set()):
        discovery_score += 2
    score += min(8.0, discovery_score)

    # Constructive momentum, without rewarding an already vertical chart: 8
    if -2 <= ch1 <= 8:
        score += 3
    elif -5 <= ch1 <= 12:
        score += 2
    if -5 <= ch6 <= 20:
        score += 3
    elif -10 <= ch6 <= 30:
        score += 1
    if -10 <= ch24 <= 35:
        score += 2
    elif -20 <= ch24 <= 45:
        score += 1

    # Pair maturity: 4
    if not np.isnan(age_h):
        if 24 <= age_h <= 24 * 180:
            score += 4
        elif 6 <= age_h < 24 or age_h <= 24 * 365:
            score += 2

    risk_flags = []
    if liq_ratio < 0.02:
        risk_flags.append("Thin liquidity vs cap")
    if buy_ratio > 0.80 and total_tx >= 50:
        risk_flags.append("One-sided flow")
    if socials["Social count"] == 0:
        risk_flags.append("No visible socials")
    if active_boost > 0 or meta.get("boost_total", 0) > 0:
        risk_flags.append("Paid boost present")
    if ch24 > 80:
        risk_flags.append("Extreme 24h move")
    if math.isfinite(circulating_pct_proxy) and circulating_pct_proxy < cfg["min_circulating_pct"]:
        risk_flags.append("Low circulating float")
    if math.isfinite(fdv_mcap) and fdv_mcap >= 10.0:
        risk_flags.append("High FDV / low-float risk")

    score = round(min(100.0, score), 1)
    gate_pass = len(gates) == 0

    if gate_pass and score >= 80:
        label = "HIGH PRIORITY"
    elif gate_pass and score >= cfg["shortlist_score"]:
        label = "SHORTLIST"
    elif score >= 55:
        label = "WATCH"
    else:
        label = "PASS"

    base = pair.get("baseToken") or {}
    quote = pair.get("quoteToken") or {}

    return {
        "Ticker": base.get("symbol") or "—",
        "Name": base.get("name") or "—",
        "Chain": pair.get("chainId") or "—",
        "DEX": pair.get("dexId") or "—",
        "Pair": f"{base.get('symbol','?')}/{quote.get('symbol','?')}",
        "Score": score,
        "Decision": label,
        "Gate": "PASS" if gate_pass else "FAIL",
        "Price USD": safe(pair.get("priceUsd")),
        "Market Cap": mcap,
        "FDV": fdv,
        "Circulating % (proxy)": round(float(circulating_pct_proxy), 1) if math.isfinite(circulating_pct_proxy) else np.nan,
        "FDV / MCap": round(float(fdv_mcap), 2) if math.isfinite(fdv_mcap) else np.nan,
        "Tokenomics Gate": tokenomics_gate,
        "VC / Unlock Review": "UNVERIFIED — specialist allocation/unlock data required",
        "Liquidity": liq,
        "Liquidity/Cap %": round(liq_ratio * 100, 2),
        "24h Volume": vol24,
        "6h Volume": vol6,
        "Vol/Liq": round(vol_liq, 2),
        "24h Buys": int(buys),
        "24h Sells": int(sells),
        "Buy %": round(buy_ratio * 100, 1),
        "1h %": round(ch1, 2),
        "6h %": round(ch6, 2),
        "24h %": round(ch24, 2),
        "Pair Age h": round(age_h, 1) if not np.isnan(age_h) else np.nan,
        "X": socials["X"],
        "Telegram": socials["Telegram"],
        "Discord": socials["Discord"],
        "Website": socials["Website"],
        "Community Strength": community_strength,
        "Community Score": round(community_score, 1),
        "Social Breadth": socials["Social count"],
        "Narrative Strength": narrative["Narrative Strength"],
        "Narrative Score": narrative["Narrative Score"],
        "Narrative Signals": narrative["Narrative Signals"],
        "Community Takeover": bool(meta.get("community_takeover")),
        "Boost": active_boost if active_boost > 0 else meta.get("boost_total", 0),
        "Discovery": ", ".join(sorted(meta.get("sources", []))),
        "Gate Reasons": "; ".join(gates) if gates else "",
        "Risk Flags": "; ".join(risk_flags) if risk_flags else "",
        "DexScreener": pair.get("url") or "",
        "Pair Address": pair.get("pairAddress") or "",
        "Token Address": base.get("address") or meta.get("tokenAddress") or "",
    }


st.title("🐸 Meme Coin Screener — v0.1")
st.caption(
    "Early-discovery screener for meme-coin candidates. It is deliberately separate from the main crypto screener and is built to be expanded as your shortlist rules evolve."
)

with st.sidebar:
    st.header("Preliminary gates")
    selected_names = st.multiselect(
        "Chains",
        list(CHAIN_OPTIONS.keys()),
        default=["Solana", "Base", "Ethereum", "BNB Chain", "Robinhood Chain"],
    )
    min_liq = st.number_input("Minimum liquidity ($)", min_value=0, value=int(DEFAULTS["min_liquidity"]), step=10_000)
    min_vol = st.number_input("Minimum 24h volume ($)", min_value=0, value=int(DEFAULTS["min_volume_24h"]), step=25_000)
    min_cap = st.number_input("Minimum market cap ($)", min_value=0, value=int(DEFAULTS["min_market_cap"]), step=100_000)
    max_cap = st.number_input("Maximum market cap ($)", min_value=100_000, value=int(DEFAULTS["max_market_cap"]), step=1_000_000)
    min_age = st.number_input("Minimum pair age (hours)", min_value=0.0, value=DEFAULTS["min_pair_age_hours"], step=1.0)
    max_1h = st.number_input("Anti-chase: max 1h rise %", min_value=1.0, value=DEFAULTS["max_1h_change"], step=1.0)
    max_24h = st.number_input("Anti-chase: max 24h rise %", min_value=5.0, value=DEFAULTS["max_24h_change"], step=5.0)
    min_circ = st.number_input(
        "Minimum circulating float (%)",
        min_value=0.0,
        max_value=100.0,
        value=DEFAULTS["min_circulating_pct"],
        step=1.0,
        help="Meme-coin rule: at least 10% circulating. Estimated from market cap / FDV when both are available.",
    )
    threshold = st.slider("Shortlist score", 50, 90, int(DEFAULTS["shortlist_score"]))

cfg = {
    "min_liquidity": float(min_liq),
    "min_volume_24h": float(min_vol),
    "min_market_cap": float(min_cap),
    "max_market_cap": float(max_cap),
    "min_pair_age_hours": float(min_age),
    "max_1h_change": float(max_1h),
    "max_24h_change": float(max_24h),
    "shortlist_score": float(threshold),
    "min_circulating_pct": float(min_circ),
}

st.subheader("Quick Analyse")
st.caption("Search by coin name, ticker or contract address, then analyse the exact pair you want.")

if "meme_quick_analysis" not in st.session_state:
    st.session_state.meme_quick_analysis = None

quick_query = st.text_input(
    "Coin name, ticker or contract address",
    placeholder="e.g. PEPE, BONK, DOGE or paste a contract address",
    key="meme_quick_query",
)

if quick_query.strip():
    try:
        quick_pairs = dex_search(quick_query)
    except Exception as exc:
        quick_pairs = []
        st.error(f"Search failed: {exc}")

    if quick_pairs:
        quick_pairs = sorted(
            quick_pairs,
            key=lambda p: safe((p.get("liquidity") or {}).get("usd"), 0),
            reverse=True,
        )[:30]

        def _pair_label(p):
            base = p.get("baseToken") or {}
            quote = p.get("quoteToken") or {}
            chain = p.get("chainId") or "?"
            dex = p.get("dexId") or "?"
            liq = safe((p.get("liquidity") or {}).get("usd"), 0)
            return (
                f"{base.get('symbol','?')} — {base.get('name','?')} | "
                f"{chain} | {dex} | {base.get('symbol','?')}/{quote.get('symbol','?')} | "
                f"Liquidity ${liq:,.0f}"
            )

        selected_idx = st.selectbox(
            "Choose result",
            options=list(range(len(quick_pairs))),
            format_func=lambda i: _pair_label(quick_pairs[i]),
            key="meme_quick_pair",
        )
        selected_pair = quick_pairs[selected_idx]

        if st.button("Analyse coin", type="primary", key="analyse_meme_coin"):
            base = selected_pair.get("baseToken") or {}
            chain = str(selected_pair.get("chainId") or "").lower()
            address = str(base.get("address") or "")
            meta = {}
            try:
                discovered = discovery_universe()
                meta = discovered.get(token_key(chain, address), {})
            except Exception:
                meta = {}

            result = score_candidate(selected_pair, meta, cfg)
            st.session_state.meme_quick_analysis = {
                "pair_address": result.get("Pair Address") or "",
                "result": result,
            }

        saved_analysis = st.session_state.get("meme_quick_analysis")
        selected_pair_address = str(selected_pair.get("pairAddress") or "")
        if (
            saved_analysis
            and saved_analysis.get("pair_address") == selected_pair_address
        ):
            result = saved_analysis["result"]

            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Decision", result["Decision"])
            m2.metric("Meme Score", f"{result['Score']:.1f}/100")
            m3.metric("Market Cap", "—" if np.isnan(safe(result["Market Cap"])) else f"${result['Market Cap']:,.0f}")
            m4.metric("Liquidity", f"${result['Liquidity']:,.0f}")

            p1, p2, p3, p4 = st.columns(4)
            p1.metric("Price", "—" if np.isnan(safe(result["Price USD"])) else f"${result['Price USD']:.10g}")
            p2.metric("24h Volume", f"${result['24h Volume']:,.0f}")
            p3.metric("Buy %", f"{result['Buy %']:.1f}%")
            p4.metric("24h Move", f"{result['24h %']:+.2f}%")

            cm1, cm2, cm3, cm4 = st.columns(4)
            cm1.metric("Community strength", result["Community Strength"])
            cm2.metric("Community score", f"{result['Community Score']:.1f}/30")
            cm3.metric("24h transactions", f"{result['24h Buys'] + result['24h Sells']:,}")
            cm4.metric("Social breadth", f"{result['Social Breadth']}/4")

            nr1, nr2 = st.columns(2)
            nr1.metric("Narrative strength", result["Narrative Strength"])
            nr2.metric("Narrative score", f"{result['Narrative Score']:.1f}/20")
            if result.get("Narrative Signals"):
                st.caption("Narrative signals: " + result["Narrative Signals"])

            tk1, tk2, tk3, tk4 = st.columns(4)
            tk1.metric("Tokenomics gate", result["Tokenomics Gate"])
            circ_proxy = result.get("Circulating % (proxy)", np.nan)
            tk2.metric(
                "Circulating float",
                f"{circ_proxy:.1f}%" if pd.notna(circ_proxy) else "Unavailable",
                "Market cap / FDV proxy",
            )
            ratio = result.get("FDV / MCap", np.nan)
            tk3.metric("FDV / Market cap", f"{ratio:.2f}x" if pd.notna(ratio) else "Unavailable")
            tk4.metric("VC / unlock review", "Needs verification")

            st.write(
                f"**{result['Name']} ({result['Ticker']})** · "
                f"Chain: **{result['Chain']}** · DEX: **{result['DEX']}** · Pair: **{result['Pair']}**"
            )

            pair_address = result.get("Pair Address") or ""
            token_address = result.get("Token Address") or ""
            if pair_address and result.get("Chain"):
                st.subheader("Price Chart")
                chart_tf = st.selectbox(
                    "Chart timeframe",
                    list(CHART_TIMEFRAMES.keys()),
                    index=1,
                    key=f"meme_chart_tf_{result['Chain']}_{pair_address}",
                )
                try:
                    chart_df = fetch_pool_ohlcv(
                        result["Chain"],
                        pair_address,
                        token_address,
                        chart_tf,
                    )
                    if chart_df.empty:
                        st.info("No OHLCV candle history is available for this pair yet.")
                    else:
                        latest = chart_df.iloc[-1]
                        bbw = chart_df["BBW_PCT"].dropna()
                        bbw_now = safe(latest.get("BBW_PCT"))
                        bbw_pctile = (
                            float((bbw.tail(120) <= bbw_now).mean() * 100)
                            if len(bbw.tail(120)) >= 20 and math.isfinite(bbw_now)
                            else np.nan
                        )
                        recent_bbw = bbw.tail(6)
                        expanding = (
                            len(recent_bbw) >= 4
                            and recent_bbw.iloc[-1] > recent_bbw.iloc[0] * 1.12
                        )
                        bb_regime = (
                            "SQUEEZE" if math.isfinite(bbw_pctile) and bbw_pctile <= 20
                            else "EXPANDING" if expanding
                            else "NORMAL"
                        )
                        band_range = safe(latest.get("BBU")) - safe(latest.get("BBL"))
                        bb_position = (
                            (safe(latest.get("close")) - safe(latest.get("BBL"))) / band_range * 100
                            if math.isfinite(band_range) and band_range > 0
                            else np.nan
                        )

                        ta1, ta2, ta3, ta4 = st.columns(4)
                        ta1.metric("RSI", f"{safe(latest.get('RSI'), 50):.1f}")
                        ta2.metric("Bollinger", bb_regime)
                        ta3.metric(
                            "BB width percentile",
                            f"{bbw_pctile:.1f}%" if math.isfinite(bbw_pctile) else "Unavailable",
                        )
                        ta4.metric(
                            "Price in bands",
                            f"{bb_position:.1f}%" if math.isfinite(bb_position) else "Unavailable",
                        )

                        st.plotly_chart(
                            meme_price_chart(chart_df, result["Ticker"], chart_tf),
                            use_container_width=True,
                        )
                        st.caption(
                            "Native on-chain candlestick chart with EMA20/EMA50 and Bollinger Bands. "
                            "RSI and Bollinger readings are context only and do not alter the meme score."
                        )
                except Exception as exc:
                    st.warning(f"Chart data is temporarily unavailable for this pair: {exc}")

            detail_cols = [
                "Ticker", "Name", "Chain", "DEX", "Pair", "Decision", "Score", "Gate",
                "Market Cap", "FDV", "Circulating % (proxy)", "FDV / MCap", "Tokenomics Gate", "Liquidity", "Liquidity/Cap %", "24h Volume", "Vol/Liq",
                "24h Buys", "24h Sells", "Buy %", "1h %", "6h %", "24h %",
                "Pair Age h", "Narrative Strength", "Narrative Score", "Narrative Signals",
                "Community Strength", "Community Score", "Social Breadth", "Community Takeover", "Boost", "Gate Reasons", "Risk Flags",
            ]
            st.dataframe(
                pd.DataFrame([{k: result.get(k) for k in detail_cols}]),
                hide_index=True,
                use_container_width=True,
            )

            social_cols = [
                "X", "Telegram", "Discord", "Website", "Discovery",
                "Token Address", "DexScreener",
            ]
            st.dataframe(
                pd.DataFrame([{k: result.get(k) for k in social_cols}]),
                hide_index=True,
                use_container_width=True,
            )

            if result["Gate"] == "FAIL":
                st.warning("This coin currently fails one or more preliminary gates: " + (result["Gate Reasons"] or "see table above."))
            elif result["Decision"] in ("HIGH PRIORITY", "SHORTLIST"):
                st.success("This coin currently passes the preliminary gates and reaches the model's shortlist threshold.")
            else:
                st.info("This coin passes the lookup, but the current v0.1 model does not rank it as a shortlist candidate yet.")
    else:
        st.warning("No DexScreener pairs matched that search.")

st.divider()
st.subheader("Discovery Scan")
st.info(
    "v0.1 uses DexScreener's latest profiles, boosts and community-takeover feeds for discovery, then scores the most liquid pair for each token. "
    "Boosts are treated as a small discovery signal, not proof of quality."
)

if st.button("Run meme coin scan", type="primary", use_container_width=True):
    chains = {CHAIN_OPTIONS[n] for n in selected_names}
    with st.spinner("Finding emerging tokens and checking live pairs…"):
        universe = discovery_universe()
        pairs = fetch_pairs_for_tokens(universe, chains)
        best_pairs = best_pair_per_token(pairs)

    rows = []
    for k, p in best_pairs.items():
        meta = universe.get(k, {})
        rows.append(score_candidate(p, meta, cfg))

    if not rows:
        st.warning("No candidates returned from the selected discovery feeds/chains.")
    else:
        df = pd.DataFrame(rows)
        order = {"HIGH PRIORITY": 0, "SHORTLIST": 1, "WATCH": 2, "PASS": 3}
        df["_order"] = df["Decision"].map(order).fillna(9)
        df = df.sort_values(["_order", "Score", "Liquidity"], ascending=[True, False, False]).drop(columns=["_order"])

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("High priority", int((df["Decision"] == "HIGH PRIORITY").sum()))
        c2.metric("Shortlist", int((df["Decision"] == "SHORTLIST").sum()))
        c3.metric("Watch", int((df["Decision"] == "WATCH").sum()))
        c4.metric("Tokens checked", len(df))

        main_cols = [
            "Ticker", "Name", "Chain", "Decision", "Score",
            "Narrative Strength", "Narrative Score",
            "Community Strength", "Community Score", "Social Breadth", "Gate", "Price USD", "Market Cap", "FDV", "Circulating % (proxy)", "FDV / MCap", "Tokenomics Gate", "Liquidity", "Liquidity/Cap %",
            "24h Volume", "Vol/Liq", "Buy %", "1h %", "6h %", "24h %",
            "Pair Age h", "Community Takeover", "Boost", "Risk Flags", "Gate Reasons",
        ]
        st.subheader("Ranked candidates")
        st.dataframe(df[main_cols], hide_index=True, use_container_width=True)

        st.subheader("Community / discovery detail")
        community_cols = [
            "Ticker", "Chain", "Narrative Strength", "Narrative Score", "Narrative Signals",
            "Community Strength", "Community Score", "Social Breadth",
            "X", "Telegram", "Discord", "Website", "Community Takeover", "Discovery", "Boost", "DEX", "Pair", "Token Address", "DexScreener",
        ]
        st.dataframe(df[community_cols], hide_index=True, use_container_width=True)

with st.expander("How v0.1 scores candidates"):
    st.markdown(
        """
**100-point preliminary model**

- **30 — Community strength:** social breadth plus actual transaction participation. This remains the largest single factor.
- **20 — Narrative / cultural-icon potential:** simple branding, a clear story, community ownership and evidence the idea is spreading across multiple discovery surfaces.
- **12 — Liquidity quality:** absolute liquidity plus liquidity relative to market cap.
- **10 — Real activity:** 24h volume relative to liquidity plus transaction count.
- **8 — Buy pressure:** constructive demand is rewarded; extremely one-sided flow is not.
- **8 — Discovery/catalyst:** boosts and appearing across multiple discovery feeds. Paid boosts remain capped.
- **8 — Momentum without chasing:** constructive 1h/6h/24h movement scores better than a vertical pump.
- **4 — Pair maturity:** enough history to reduce immediate-launch noise.

**Hard gates** currently cover liquidity, volume, market-cap range, minimum pair age, anti-chase limits and the meme tokenomics rule: **at least 10% circulating float**. Because very new DEX tokens often lack a verified supply feed, v0.1 estimates circulating float as **market cap ÷ FDV** when both values are available. If it cannot verify the ratio, tokenomics is UNKNOWN and the coin does not pass the hard gate. A **FDV/market-cap ratio of 10x or more** is flagged as high-FDV/low-float risk.

Detailed VC allocations and insider unlock schedules are not guessed; they require a specialist verified tokenomics/unlock source and are marked for separate review.

**Community warning:** visible socials alone do not prove a real community. Follower counts can be bought or botted, so the model deliberately rewards actual transaction participation and multi-channel presence rather than treating raw followers as truth.

**Narrative warning:** the cultural score is heuristic. It can identify simple, recognisable, shareable meme structures, but it cannot know in advance which joke, mascot or cultural reference will genuinely go viral.

**Technical context:** Quick Analyse now shows RSI and 20-period/2-standard-deviation Bollinger Bands for the selected on-chain timeframe. They are deliberately **not part of the meme ranking score** because meme coins can remain overbought or highly volatile for long periods; community, narrative, liquidity and real activity remain more important.

This is **v0.1**, not the final meme-coin model. Narrative quality, holder distribution, LP lock/burn, contract/security checks, influencer quality, community growth/engagement and migration/relaunch rules are intentionally left as the next modular layers rather than being guessed.
"""
    )

st.caption("Screening aid only. Meme coins are exceptionally speculative; live DEX data can be incomplete, manipulated or change rapidly.")
