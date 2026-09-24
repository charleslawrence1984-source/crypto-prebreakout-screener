from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional

import numpy as np
import pandas as pd
import requests
import streamlit as st
from cl_signal_ui import render_module_header, render_signal_decision_card
import plotly.graph_objects as go


st.set_page_config(page_title="CL Signal · Meme Coins", page_icon="🐸", layout="wide")



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
    "min_pair_age_hours": 2.0,
    "max_1h_change": 12.0,
    "max_24h_change": 45.0,
    "shortlist_score": 65.0,
    "min_circulating_pct": 10.0,
    "trade_plan_count": 50,
}


def safe(v, default=np.nan):
    try:
        x = float(v)
        return x if math.isfinite(x) else default
    except Exception:
        return default


def fmt_meme_price(value, fallback="Unavailable"):
    number = safe(value)
    if np.isnan(number):
        return fallback
    if number >= 1:
        return f"$" + f"{number:,.4f}"
    if number >= 0.01:
        return f"$" + f"{number:.6f}"
    return f"$" + f"{number:.10g}"


def meme_cell_style(value, column: str) -> str:
    green = "background-color: #d8f3dc; color: #16351c; font-weight: 600"
    amber = "background-color: #fff3bf; color: #5f4500; font-weight: 600"
    red = "background-color: #ffd6d6; color: #5c1717; font-weight: 600"
    label = str(value).upper()

    if column == "Decision":
        if label in ("HIGH PRIORITY", "SHORTLIST"):
            return green
        return amber if label == "WATCH" else red
    if column in ("Gate", "Tokenomics Gate"):
        return green if label == "PASS" else red if label == "FAIL" else amber
    if column == "Narrative Strength":
        if label in ("ICONIC POTENTIAL", "STRONG NARRATIVE"):
            return green
        return amber if label == "DEVELOPING" else red
    if column == "Community Strength":
        if label in ("VERY STRONG", "STRONG"):
            return green
        return amber if label == "DEVELOPING" else red

    number = safe(value)
    if np.isnan(number):
        return ""

    if column == "Score":
        return green if number >= 75 else amber if number >= 60 else red
    if column == "Narrative Score":
        return green if number >= 12 else amber if number >= 8 else red
    if column == "Community Score":
        return green if number >= 18 else amber if number >= 12 else red
    if column == "Social Breadth":
        return green if number >= 3 else amber if number >= 2 else red
    if column == "Circulating % (proxy)":
        return green if number >= 10 else red
    if column == "FDV / MCap":
        return green if number <= 4 else amber if number < 10 else red
    if column == "Liquidity":
        return green if number >= 250_000 else amber if number >= 50_000 else red
    if column == "Liquidity/Cap %":
        return green if number >= 5 else amber if number >= 2 else red
    if column == "24h Volume":
        return green if number >= 500_000 else amber if number >= 100_000 else red
    if column == "Vol/Liq":
        return green if 0.5 <= number <= 3 else amber if 0.2 <= number <= 5 else red
    if column == "Buy %":
        return green if 53 <= number <= 72 else amber if 45 <= number <= 80 else red
    if column == "1h %":
        return green if -2 <= number <= 8 else amber if -5 <= number <= 12 else red
    if column == "6h %":
        return green if -5 <= number <= 20 else amber if -10 <= number <= 30 else red
    if column == "24h %":
        return green if -10 <= number <= 35 else amber if -20 <= number <= 45 else red
    if column == "Pair Age h":
        return green if number >= 24 else amber if number >= 6 else red
    return ""


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


def aggregate_ohlcv(frame: pd.DataFrame, rule: str = "4h") -> pd.DataFrame:
    """Aggregate lower-timeframe OHLCV locally to avoid a second API request."""
    if frame is None or frame.empty or "timestamp" not in frame.columns:
        return pd.DataFrame()
    work = frame.copy()
    work["timestamp"] = pd.to_datetime(work["timestamp"], utc=True, errors="coerce")
    for col in ["open", "high", "low", "close", "volume"]:
        work[col] = pd.to_numeric(work[col], errors="coerce")
    work = work.dropna(subset=["timestamp", "open", "high", "low", "close"])
    if work.empty:
        return pd.DataFrame()

    return (
        work.set_index("timestamp")
        .resample(rule, label="right", closed="right")
        .agg({
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum",
        })
        .dropna(subset=["open", "high", "low", "close"])
        .reset_index()
    )


def fetch_meme_trade_plan(
    chain: str,
    pair_address: str,
    token_address: str,
    liquidity: float,
    position_size: float,
    round_trip_fees_pct: float,
) -> Dict:
    """One API call per coin: fetch 1h candles and build 4h structure locally."""
    hourly = fetch_pool_ohlcv(chain, pair_address, token_address, "1h")
    structural_4h = aggregate_ohlcv(hourly, "4h")
    return meme_trade_plan(
        structural_4h,
        hourly,
        liquidity=liquidity,
        position_size=position_size,
        round_trip_fees_pct=round_trip_fees_pct,
    )


def meme_trade_plan(
    df4h: pd.DataFrame,
    df1h: Optional[pd.DataFrame] = None,
    liquidity: float = np.nan,
    position_size: float = 250.0,
    round_trip_fees_pct: float = 1.0,
) -> Dict:
    """
    Structure-first meme trade plan.
    4h defines structural support/resistance and invalidation; 1h refines entry.
    Net ROI includes a conservative liquidity-based slippage estimate plus user-set fees/gas.
    """
    empty_plan = {
        "Plan Status": "UNAVAILABLE",
        "Entry Low": np.nan,
        "Entry High": np.nan,
        "Entry Price": np.nan,
        "Negative Exit": np.nan,
        "Positive Exit": np.nan,
        "Stretch Exit": np.nan,
        "Gross ROI %": np.nan,
        "Net ROI %": np.nan,
        "Potential ROI %": np.nan,
        "Gross R:R": np.nan,
        "Net R:R": np.nan,
        "R:R": np.nan,
        "ATR %": np.nan,
        "Risk to Stop %": np.nan,
        "Estimated Slippage %": np.nan,
        "Estimated Total Costs %": np.nan,
        "Potential Profit $": np.nan,
        "Potential Loss $": np.nan,
        "Plan Basis": "Not enough candle history",
    }

    structural = df4h.copy() if df4h is not None else pd.DataFrame()
    timing = df1h.copy() if df1h is not None else pd.DataFrame()

    if len(structural) < 24:
        structural = timing.copy()
    if len(structural) < 24:
        return empty_plan
    if len(timing) < 24:
        timing = structural.copy()

    def _clean(frame: pd.DataFrame) -> pd.DataFrame:
        out = frame.tail(min(160, len(frame))).copy()
        for col in ["open", "high", "low", "close"]:
            out[col] = pd.to_numeric(out[col], errors="coerce")
        return out.dropna(subset=["open", "high", "low", "close"])

    structural = _clean(structural)
    timing = _clean(timing)
    if len(structural) < 24 or len(timing) < 24:
        plan = empty_plan.copy()
        plan["Plan Basis"] = "Not enough clean candle history"
        return plan

    def _atr14(frame: pd.DataFrame) -> float:
        prev_close = frame["close"].shift(1)
        tr = pd.concat(
            [
                frame["high"] - frame["low"],
                (frame["high"] - prev_close).abs(),
                (frame["low"] - prev_close).abs(),
            ],
            axis=1,
        ).max(axis=1)
        return safe(tr.rolling(14).mean().iloc[-1])

    structural_atr = _atr14(structural)
    timing_atr = _atr14(timing)
    price = safe(timing["close"].iloc[-1])
    if (
        not math.isfinite(price) or price <= 0
        or not math.isfinite(structural_atr) or structural_atr <= 0
        or not math.isfinite(timing_atr) or timing_atr <= 0
    ):
        plan = empty_plan.copy()
        plan["Plan Basis"] = "Invalid price/volatility data"
        return plan

    # Entry uses 1h timing support; stop and targets use broader structure.
    timing_recent = timing.tail(min(72, len(timing)))
    timing_ema20 = safe(timing["close"].ewm(span=20, adjust=False).mean().iloc[-1])
    timing_bb_mid = safe(timing["close"].rolling(20).mean().iloc[-1])
    timing_support = safe(timing_recent["low"].quantile(0.30))

    entry_candidates = [
        x for x in [timing_ema20, timing_bb_mid, timing_support]
        if math.isfinite(x) and 0 < x <= price * 1.02
    ]
    entry_anchor = max(entry_candidates) if entry_candidates else price

    # If support is far below current price, do not invent an entry at market.
    if entry_anchor < price * 0.85:
        entry_anchor = price * 0.90

    entry_low = max(entry_anchor - 0.25 * timing_atr, 0)
    entry_high = min(entry_anchor + 0.25 * timing_atr, price * 1.01)
    if entry_high <= entry_low:
        entry_high = entry_anchor
    entry_price = (entry_low + entry_high) / 2

    structural_recent = structural.tail(min(60, len(structural)))
    swing_support = safe(structural_recent["low"].quantile(0.12))
    structural_stop = (
        swing_support - 0.30 * structural_atr
        if math.isfinite(swing_support)
        else entry_price - 1.40 * structural_atr
    )
    volatility_stop = entry_price - 1.40 * structural_atr
    negative_exit = min(structural_stop, volatility_stop)
    negative_exit = max(negative_exit, entry_price * 0.65)

    risk = entry_price - negative_exit
    if risk <= 0:
        plan = empty_plan.copy()
        plan.update({
            "Entry Low": entry_low,
            "Entry High": entry_high,
            "Entry Price": entry_price,
            "Plan Basis": "Could not define a valid downside invalidation",
        })
        return plan

    risk_pct = risk / entry_price * 100

    # Use actual completed swing highs first. Do not force a 2R target through resistance.
    resistance_source = structural.iloc[-min(100, len(structural)):-1].copy()
    highs = resistance_source["high"]
    pivot_mask = (
        (highs > highs.shift(1))
        & (highs >= highs.shift(2))
        & (highs > highs.shift(-1))
        & (highs >= highs.shift(-2))
    )
    resistance_levels = sorted({
        float(x) for x in highs[pivot_mask].dropna().tolist()
        if float(x) > entry_price * 1.02
    })

    if not resistance_levels:
        for q in (0.80, 0.90, 0.97):
            level = safe(highs.quantile(q))
            if math.isfinite(level) and level > entry_price * 1.02:
                resistance_levels.append(float(level))
        resistance_levels = sorted(set(resistance_levels))

    two_r_target = entry_price + 2.0 * risk
    if resistance_levels:
        positive_exit = resistance_levels[0]
        stretch_candidates = [x for x in resistance_levels[1:] if x > positive_exit * 1.01]
        stretch_exit = stretch_candidates[0] if stretch_candidates else max(two_r_target, positive_exit)
        basis = "First completed-swing resistance; stretch target shown separately"
    else:
        positive_exit = two_r_target
        stretch_exit = entry_price + 3.0 * risk
        basis = "No clear overhead swing resistance; volatility R-multiple target"

    gross_roi = (positive_exit / entry_price - 1) * 100
    gross_rr = (positive_exit - entry_price) / risk

    # Approximate AMM price impact from trade size versus half of reported pool liquidity.
    liq = safe(liquidity)
    size = max(safe(position_size, 250.0), 0.0)
    if math.isfinite(liq) and liq > 0 and size > 0:
        quote_reserve_proxy = liq / 2.0
        one_way_slippage = size / (quote_reserve_proxy + size) * 100
        estimated_slippage = min(50.0, 2.0 * one_way_slippage)
    else:
        estimated_slippage = 0.0

    fees_pct = max(safe(round_trip_fees_pct, 0.0), 0.0)
    total_cost_pct = estimated_slippage + fees_pct
    net_roi = gross_roi - total_cost_pct
    net_risk_pct = risk_pct + total_cost_pct
    net_rr = net_roi / net_risk_pct if net_risk_pct > 0 else np.nan

    potential_profit = size * net_roi / 100 if size > 0 else np.nan
    potential_loss = size * net_risk_pct / 100 if size > 0 else np.nan

    chase_pct = (price / entry_high - 1) * 100 if entry_high > 0 else 0
    if estimated_slippage >= 5:
        status = "LIQUIDITY / SLIPPAGE — WAIT"
    elif risk_pct > 20:
        status = "HIGH VOLATILITY — WAIT"
    elif gross_rr < 1.5 or (math.isfinite(net_rr) and net_rr < 1.25):
        status = "POOR R:R — WAIT"
    elif chase_pct > 5:
        status = "WAIT FOR ENTRY"
    else:
        status = "ENTRY AREA"

    return {
        "Plan Status": status,
        "Entry Low": entry_low,
        "Entry High": entry_high,
        "Entry Price": entry_price,
        "Negative Exit": negative_exit,
        "Positive Exit": positive_exit,
        "Stretch Exit": stretch_exit,
        "Gross ROI %": round(gross_roi, 1),
        "Net ROI %": round(net_roi, 1),
        "Potential ROI %": round(net_roi, 1),
        "Gross R:R": round(gross_rr, 2),
        "Net R:R": round(net_rr, 2) if math.isfinite(net_rr) else np.nan,
        "R:R": round(net_rr, 2) if math.isfinite(net_rr) else round(gross_rr, 2),
        "ATR %": round(structural_atr / price * 100, 1),
        "Risk to Stop %": round(risk_pct, 1),
        "Estimated Slippage %": round(estimated_slippage, 2),
        "Estimated Total Costs %": round(total_cost_pct, 2),
        "Potential Profit $": round(potential_profit, 2) if math.isfinite(potential_profit) else np.nan,
        "Potential Loss $": round(potential_loss, 2) if math.isfinite(potential_loss) else np.nan,
        "Plan Basis": basis,
    }


def meme_price_chart(
    df: pd.DataFrame,
    ticker: str,
    timeframe_label: str,
    trade_plan: Optional[Dict] = None,
) -> go.Figure:
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
    if trade_plan and trade_plan.get("Plan Status") != "UNAVAILABLE":
        entry_low = safe(trade_plan.get("Entry Low"))
        entry_high = safe(trade_plan.get("Entry High"))
        negative_exit = safe(trade_plan.get("Negative Exit"))
        positive_exit = safe(trade_plan.get("Positive Exit"))
        stretch_exit = safe(trade_plan.get("Stretch Exit"))
        if math.isfinite(entry_low) and math.isfinite(entry_high):
            fig.add_hrect(
                y0=entry_low,
                y1=entry_high,
                opacity=0.12,
                line_width=0,
                annotation_text="Entry zone",
            )
        if math.isfinite(negative_exit):
            fig.add_hline(
                y=negative_exit,
                line_dash="dash",
                annotation_text="Negative exit / stop",
            )
        if math.isfinite(positive_exit):
            fig.add_hline(
                y=positive_exit,
                line_dash="dash",
                annotation_text="First exit target",
            )
        if math.isfinite(stretch_exit) and (
            not math.isfinite(positive_exit) or stretch_exit > positive_exit * 1.005
        ):
            fig.add_hline(
                y=stretch_exit,
                line_dash="dot",
                annotation_text="Stretch target",
            )
    fig.update_layout(
        height=560,
        xaxis_rangeslider_visible=False,
        margin=dict(l=10, r=10, t=45, b=10),
        title=f"{ticker} — {timeframe_label} candles",
        yaxis_title="Price (USD)",
    )
    return fig


def attach_bulk_trade_plans(
    ranked: pd.DataFrame,
    max_candidates: int,
    position_size: float,
    round_trip_fees_pct: float,
) -> pd.DataFrame:
    """
    Calculate entry / exit zones for the highest-ranked discovery candidates.

    Ranking is completed before this enrichment step, so unavailable candle data
    cannot change the candidate score or decision. Trade-plan fields are an
    execution overlay, not a rescue mechanism for weak candidates.
    """
    if ranked is None or ranked.empty:
        return ranked

    out = ranked.copy()
    plan_columns = {
        "Plan Status": "NOT CALCULATED",
        "Entry Low": np.nan,
        "Entry High": np.nan,
        "Entry Price": np.nan,
        "Negative Exit": np.nan,
        "Positive Exit": np.nan,
        "Stretch Exit": np.nan,
        "Gross ROI %": np.nan,
        "Net ROI %": np.nan,
        "Potential ROI %": np.nan,
        "Gross R:R": np.nan,
        "Net R:R": np.nan,
        "R:R": np.nan,
        "ATR %": np.nan,
        "Risk to Stop %": np.nan,
        "Estimated Slippage %": np.nan,
        "Estimated Total Costs %": np.nan,
        "Potential Profit $": np.nan,
        "Potential Loss $": np.nan,
        "Plan Basis": "Outside bulk trade-plan coverage",
    }
    for column, default in plan_columns.items():
        if column not in out.columns:
            out[column] = default

    # Bulk trade plans are execution work. Failed hard-gate candidates stay
    # visible for discovery but do not consume scarce public OHLCV requests.
    gate_pass_mask = out.get("Gate", pd.Series("", index=out.index)).eq("PASS")
    out.loc[~gate_pass_mask, "Plan Status"] = "NOT ELIGIBLE"
    out.loc[~gate_pass_mask, "Plan Basis"] = (
        "Hard gate failed — no bulk trade plan calculated"
    )

    decision_rank = {"HIGH PRIORITY": 0, "SHORTLIST": 1, "WATCH": 2, "PASS": 3}
    candidates = out.loc[gate_pass_mask].copy()
    candidates["_plan_decision_rank"] = candidates["Decision"].map(decision_rank).fillna(9)
    candidates = candidates.sort_values(
        ["_plan_decision_rank", "Score", "Liquidity"],
        ascending=[True, False, False],
        na_position="last",
    )

    # Public GeckoTerminal is approximately 10 calls/minute. Use one request
    # per eligible coin and retain headroom for Quick Analysis/chart requests.
    public_api_budget = 8
    requested_limit = max(0, int(max_candidates))
    plan_limit = min(requested_limit, public_api_budget)
    selected_indices = list(candidates.head(plan_limit).index)

    deferred_indices = list(candidates.iloc[plan_limit:requested_limit].index)
    if deferred_indices:
        out.loc[deferred_indices, "Plan Status"] = "DEFERRED"
        out.loc[deferred_indices, "Plan Basis"] = (
            "Public API request budget reached — use Quick Analysis for an on-demand plan"
        )

    for idx in selected_indices:
        row = out.loc[idx]
        pair_address = str(row.get("Pair Address") or "")
        token_address = str(row.get("Token Address") or "")
        chain = str(row.get("Chain") or "")
        if not pair_address or not chain:
            out.at[idx, "Plan Status"] = "UNAVAILABLE"
            out.at[idx, "Plan Basis"] = "Missing pool/chain metadata"
            continue
        try:
            plan = fetch_meme_trade_plan(
                chain,
                pair_address,
                token_address,
                liquidity=row.get("Liquidity", np.nan),
                position_size=position_size,
                round_trip_fees_pct=round_trip_fees_pct,
            )
        except Exception as exc:
            plan = {
                "Plan Status": "UNAVAILABLE",
                "Plan Basis": f"Trade-plan candle data unavailable: {type(exc).__name__}",
            }

        for key, value in plan.items():
            if key in out.columns:
                out.at[idx, key] = value
            else:
                out[key] = np.nan
                out.at[idx, key] = value

    return out


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


def load_meme_watchlist() -> List[str]:
    try:
        raw = st.query_params.get("mwl", "")
    except Exception:
        raw = ""
    if isinstance(raw, list):
        raw = raw[-1] if raw else ""
    return [
        item.strip().upper()
        for item in str(raw or "").split(",")
        if item.strip()
    ][:30]


def save_meme_watchlist(symbols: List[str]) -> None:
    clean = []
    for symbol in symbols[:30]:
        value = str(symbol or "").strip().upper()
        if value and value not in clean:
            clean.append(value)
    try:
        if clean:
            st.query_params["mwl"] = ",".join(clean)
        elif "mwl" in st.query_params:
            del st.query_params["mwl"]
    except Exception:
        pass


def set_meme_watchlist_symbol(symbol: str, enabled: bool) -> None:
    ticker = str(symbol or "").strip().upper()
    current = load_meme_watchlist()
    current_set = set(current)
    if enabled:
        current_set.add(ticker)
    else:
        current_set.discard(ticker)
    ordered = [item for item in current if item in current_set]
    if enabled and ticker not in ordered:
        ordered.append(ticker)
    save_meme_watchlist(ordered)


render_module_header(
    "Meme Coins",
    "🐸",
    "Early-discovery screening for meme-coin candidates, with deeper analysis and shortlist tools available underneath.",
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
    st.caption("Market cap has no minimum or maximum gate. It is shown for context only.")
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
    st.divider()
    st.header("Trade plan assumptions")
    planned_position_size = st.number_input(
        "Planned position size ($)",
        min_value=25.0,
        value=250.0,
        step=25.0,
        help="Used to estimate liquidity-driven slippage and potential dollar profit/loss.",
    )
    round_trip_fees_pct = st.number_input(
        "Estimated round-trip fees + gas (%)",
        min_value=0.0,
        value=1.0,
        step=0.25,
        help="Your estimated buy + sell trading costs, excluding the model's liquidity-based slippage estimate.",
    )
    trade_plan_count = st.selectbox(
        "Maximum eligible trade plans per scan",
        [5, 8],
        index=1,
        help=(
            "Only hard-gate PASS candidates receive bulk trade plans. The public OHLCV API is "
            "rate-limited, so bulk planning is capped conservatively; Quick Analysis can build "
            "an on-demand plan for any individual coin."
        ),
    )

cfg = {
    "min_liquidity": float(min_liq),
    "min_volume_24h": float(min_vol),
    "min_pair_age_hours": float(min_age),
    "max_1h_change": float(max_1h),
    "max_24h_change": float(max_24h),
    "shortlist_score": float(threshold),
    "min_circulating_pct": float(min_circ),
    "trade_plan_count": int(trade_plan_count),
}

if "meme_scan_df" not in st.session_state:
    st.session_state.meme_scan_df = pd.DataFrame()

tab_meme_home, tab_meme_quick, tab_meme_opportunities, tab_meme_watchlist, tab_meme_advanced = st.tabs(
    ["Home", "Quick Analysis", "Opportunities", "Watchlist", "Advanced Meme Screener"]
)

with tab_meme_home:
    st.markdown("### Your meme-coin dashboard")
    st.caption(
        "Start with a coin, review what the latest discovery scan found, or revisit coins you are watching."
    )

    meme_home_scan = st.session_state.meme_scan_df.copy()
    meme_home_watchlist = load_meme_watchlist()

    high_priority_count = (
        int((meme_home_scan["Decision"] == "HIGH PRIORITY").sum())
        if not meme_home_scan.empty and "Decision" in meme_home_scan.columns else 0
    )
    shortlist_count = (
        int((meme_home_scan["Decision"] == "SHORTLIST").sum())
        if not meme_home_scan.empty and "Decision" in meme_home_scan.columns else 0
    )
    watch_count = (
        int((meme_home_scan["Decision"] == "WATCH").sum())
        if not meme_home_scan.empty and "Decision" in meme_home_scan.columns else 0
    )

    mh1, mh2, mh3, mh4 = st.columns(4)
    mh1.metric("High priority", high_priority_count)
    mh2.metric("Shortlist", shortlist_count)
    mh3.metric("Watch", watch_count)
    mh4.metric("Watchlist", len(meme_home_watchlist))

    home_analyse, home_opps, home_watch = st.columns(3)

    with home_analyse:
        with st.container(border=True):
            st.markdown("#### 🔎 Analyse a coin")
            st.write(
                "Use **Quick Analysis** above to search by coin name, ticker or contract address "
                "and see the current decision first."
            )
            st.caption("No strategy rules are changed by using Quick Analysis.")

    with home_opps:
        with st.container(border=True):
            st.markdown("#### 🎯 Find opportunities")
            if meme_home_scan.empty:
                st.info("No discovery scan is loaded yet.")
                st.caption("Run a scan from **Advanced Meme Screener**.")
            else:
                if high_priority_count:
                    st.success(
                        f"{high_priority_count} high-priority candidate"
                        f"{'s' if high_priority_count != 1 else ''}"
                    )
                elif shortlist_count:
                    st.success(
                        f"{shortlist_count} shortlist candidate"
                        f"{'s' if shortlist_count != 1 else ''}"
                    )
                else:
                    st.info("No high-priority or shortlist candidate is ready in the latest scan.")
                st.caption("Open **Opportunities** above for the cleaner shortlist view.")

    with home_watch:
        with st.container(border=True):
            st.markdown("#### ⭐ My watchlist")
            if meme_home_watchlist:
                st.write(
                    f"You are following **{len(meme_home_watchlist)}** meme coin"
                    f"{'s' if len(meme_home_watchlist) != 1 else ''}."
                )
                st.caption(
                    ", ".join(meme_home_watchlist[:6])
                    + ("…" if len(meme_home_watchlist) > 6 else "")
                )
            else:
                st.write("Your meme-coin watchlist is empty.")
                st.caption("Use **Watch** after analysing a coin to save it for later.")

    st.markdown("### How it works")
    mhw1, mhw2, mhw3 = st.columns(3)
    with mhw1:
        st.markdown("**1 · Search a coin**")
        st.caption("Use Quick Analysis for one specific token or pair.")
    with mhw2:
        st.markdown("**2 · See the decision**")
        st.caption("The current model decision appears before the detailed evidence.")
    with mhw3:
        st.markdown("**3 · Watch what matters**")
        st.caption("Save interesting coins rather than repeatedly starting the research again.")


with tab_meme_quick:
    st.markdown("### Quick Analysis")
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

                result_ticker = str(result.get("Ticker") or "").strip().upper()
                result_title_col, result_watch_col = st.columns([5, 1], vertical_alignment="center")
                with result_title_col:
                    st.markdown(
                        f"### {result_ticker or 'Selected coin'}"
                        + (f" · {result.get('Chain')}" if result.get("Chain") else "")
                    )
                with result_watch_col:
                    meme_is_watched = result_ticker in set(load_meme_watchlist())
                    meme_watch_now = st.checkbox(
                        "Watch",
                        value=meme_is_watched,
                        key=f"meme_watch_{result_ticker}_{selected_pair_address}",
                    )
                    if meme_watch_now != meme_is_watched:
                        set_meme_watchlist_symbol(result_ticker, meme_watch_now)
                        st.toast(
                            "Added to meme-coin watchlist"
                            if meme_watch_now
                            else "Removed from meme-coin watchlist"
                        )

                if result["Gate"] == "FAIL":
                    meme_decision_reason = (
                        "This coin currently fails one or more preliminary gates: "
                        + (result["Gate Reasons"] or "see the detailed evidence below.")
                    )
                elif result["Decision"] in ("HIGH PRIORITY", "SHORTLIST"):
                    meme_decision_reason = (
                        "This coin currently passes the preliminary gates and reaches "
                        "the model's current shortlist threshold."
                    )
                else:
                    meme_decision_reason = (
                        "This coin can be analysed, but the current model does not rank "
                        "it as a shortlist candidate yet."
                    )

                render_signal_decision_card(
                    "MEME COIN DECISION",
                    result["Decision"],
                    meme_decision_reason,
                )
                st.caption(
                    "Decision first. The liquidity, activity, community, narrative and "
                    "tokenomics evidence below explains the result."
                )

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
                trade_plan = {
                    "Plan Status": "UNAVAILABLE",
                    "Entry Low": np.nan,
                    "Entry High": np.nan,
                    "Entry Price": np.nan,
                    "Negative Exit": np.nan,
                    "Positive Exit": np.nan,
                    "Stretch Exit": np.nan,
                    "Gross ROI %": np.nan,
                    "Net ROI %": np.nan,
                    "Potential ROI %": np.nan,
                    "Gross R:R": np.nan,
                    "Net R:R": np.nan,
                    "R:R": np.nan,
                    "Estimated Slippage %": np.nan,
                    "Estimated Total Costs %": np.nan,
                    "Potential Profit $": np.nan,
                    "Potential Loss $": np.nan,
                    "Plan Basis": "No candle history",
                }
                plan_timeframe = "4h structure + 1h entry timing"
                if pair_address and result.get("Chain"):
                    try:
                        trade_plan = fetch_meme_trade_plan(
                            result["Chain"],
                            pair_address,
                            token_address,
                            liquidity=result.get("Liquidity", np.nan),
                            position_size=planned_position_size,
                            round_trip_fees_pct=round_trip_fees_pct,
                        )
                        plan_timeframe = "4h structure aggregated from 1h + 1h entry timing"
                    except Exception:
                        trade_plan["Plan Basis"] = "Trade-plan candle data unavailable"

                result.update(trade_plan)

                st.subheader("Trade Plan")
                tp1, tp2, tp3, tp4, tp5, tp6 = st.columns(6)
                tp1.metric("Current Price", fmt_meme_price(result.get("Price USD")))
                tp2.metric("Entry Price", fmt_meme_price(trade_plan.get("Entry Price")))
                tp3.metric("Negative Exit / Stop", fmt_meme_price(trade_plan.get("Negative Exit")))
                tp4.metric("First Exit Target", fmt_meme_price(trade_plan.get("Positive Exit")))
                tp5.metric(
                    "Net ROI",
                    f"{safe(trade_plan.get('Net ROI %')):.1f}%"
                    if math.isfinite(safe(trade_plan.get("Net ROI %")))
                    else "Unavailable",
                )
                tp6.metric(
                    "Net R:R",
                    f"{safe(trade_plan.get('Net R:R')):.2f}:1"
                    if math.isfinite(safe(trade_plan.get("Net R:R")))
                    else "Unavailable",
                )

                tc1, tc2, tc3, tc4, tc5, tc6 = st.columns(6)
                tc1.metric("Stretch Target", fmt_meme_price(trade_plan.get("Stretch Exit")))
                tc2.metric(
                    "Gross ROI",
                    f"{safe(trade_plan.get('Gross ROI %')):.1f}%"
                    if math.isfinite(safe(trade_plan.get("Gross ROI %")))
                    else "Unavailable",
                )
                tc3.metric(
                    "Est. Slippage",
                    f"{safe(trade_plan.get('Estimated Slippage %')):.2f}%"
                    if math.isfinite(safe(trade_plan.get("Estimated Slippage %")))
                    else "Unavailable",
                )
                tc4.metric(
                    "Est. Total Costs",
                    f"{safe(trade_plan.get('Estimated Total Costs %')):.2f}%"
                    if math.isfinite(safe(trade_plan.get("Estimated Total Costs %")))
                    else "Unavailable",
                )
                tc5.metric(
                    "Potential Profit",
                    ("$" + format(safe(trade_plan.get("Potential Profit $")), ",.2f"))
                    if math.isfinite(safe(trade_plan.get("Potential Profit $")))
                    else "Unavailable",
                )
                tc6.metric(
                    "Potential Loss",
                    ("$" + format(safe(trade_plan.get("Potential Loss $")), ",.2f"))
                    if math.isfinite(safe(trade_plan.get("Potential Loss $")))
                    else "Unavailable",
                )
                st.caption(
                    f"Plan status: **{trade_plan.get('Plan Status', 'UNAVAILABLE')}** · "
                    f"Entry zone: {fmt_meme_price(trade_plan.get('Entry Low'))} – "
                    f"{fmt_meme_price(trade_plan.get('Entry High'))} · "
                    f"Basis timeframe: {plan_timeframe} · "
                    f"{trade_plan.get('Plan Basis', '')}"
                )
                risk_to_stop = safe(trade_plan.get("Risk to Stop %"))
                atr_pct = safe(trade_plan.get("ATR %"))
                if math.isfinite(risk_to_stop) or math.isfinite(atr_pct):
                    st.caption(
                        "Volatility context: "
                        + (
                            f"risk to stop {risk_to_stop:.1f}%"
                            if math.isfinite(risk_to_stop)
                            else ""
                        )
                        + (
                            f" · ATR {atr_pct:.1f}%"
                            if math.isfinite(atr_pct)
                            else ""
                        )
                    )
                if trade_plan.get("Plan Status") == "HIGH VOLATILITY — WAIT":
                    st.warning(
                        "The structural stop is very wide for this meme coin. "
                        "Treat the setup as WAIT rather than forcing a high-risk entry."
                    )
                elif trade_plan.get("Plan Status") == "LIQUIDITY / SLIPPAGE — WAIT":
                    st.warning(
                        "Your planned trade size is large relative to this pool's liquidity. "
                        "Estimated slippage is too high for a clean entry/exit."
                    )
                elif trade_plan.get("Plan Status") == "POOR R:R — WAIT":
                    st.warning(
                        "The first realistic resistance target does not offer enough reward "
                        "for the structural downside risk. The model will not invent a higher target."
                    )
                elif trade_plan.get("Plan Status") == "WAIT FOR ENTRY":
                    st.info(
                        "Current price is above the preferred entry zone. Wait for the pullback "
                        "rather than chasing the move."
                    )

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

                            chart_key = (
                                "meme_price_chart_"
                                + str(result.get("Chain", "")).replace("/", "_")
                                + "_"
                                + str(pair_address).replace("/", "_")
                                + "_"
                                + chart_tf
                            )
                            st.plotly_chart(
                                meme_price_chart(
                                    chart_df,
                                    result["Ticker"],
                                    chart_tf,
                                    trade_plan=trade_plan,
                                ),
                                use_container_width=True,
                                key=chart_key,
                            )
                            st.caption(
                                "Native on-chain candlestick chart with EMA20/EMA50 and Bollinger Bands. "
                                "RSI and Bollinger readings are context only and do not alter the meme score."
                            )
                    except Exception as exc:
                        st.warning(f"Chart data is temporarily unavailable for this pair: {exc}")

                detail_cols = [
                    "Ticker", "Name", "Chain", "DEX", "Pair", "Decision", "Score", "Gate",
                    "Price USD", "Entry Low", "Entry High", "Entry Price", "Negative Exit",
                    "Positive Exit", "Stretch Exit", "Gross ROI %", "Net ROI %",
                    "Potential ROI %", "Gross R:R", "Net R:R", "R:R",
                    "Estimated Slippage %", "Estimated Total Costs %",
                    "Potential Profit $", "Potential Loss $", "Plan Status", "Plan Basis",
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

                st.caption("Detailed evidence shown above. Use Watch if you want to revisit this coin later.")
        else:
            st.warning("No DexScreener pairs matched that search.")

with tab_meme_opportunities:
    st.markdown("### Opportunities")
    st.caption(
        "A cleaner view of the latest Meme Coin discovery scan. Run a fresh scan from "
        "**Advanced Meme Screener** whenever you want to update the market data."
    )

    meme_opportunities = st.session_state.meme_scan_df.copy()
    if meme_opportunities.empty:
        st.info(
            "No discovery scan results are loaded yet. Open **Advanced Meme Screener** "
            "and run a scan to populate this page."
        )
    else:
        mo1, mo2, mo3, mo4 = st.columns(4)
        mo1.metric(
            "High priority",
            int((meme_opportunities["Decision"] == "HIGH PRIORITY").sum()),
        )
        mo2.metric(
            "Shortlist",
            int((meme_opportunities["Decision"] == "SHORTLIST").sum()),
        )
        mo3.metric(
            "Watch",
            int((meme_opportunities["Decision"] == "WATCH").sum()),
        )
        mo4.metric("Tokens checked", len(meme_opportunities))

        opportunity_filter = st.radio(
            "Show",
            ["Best opportunities", "High priority", "Shortlist", "Watch", "All"],
            horizontal=True,
            key="meme_opportunity_filter",
        )

        shown_meme = meme_opportunities.copy()
        if opportunity_filter == "High priority":
            shown_meme = shown_meme[shown_meme["Decision"] == "HIGH PRIORITY"]
        elif opportunity_filter == "Shortlist":
            shown_meme = shown_meme[shown_meme["Decision"] == "SHORTLIST"]
        elif opportunity_filter == "Watch":
            shown_meme = shown_meme[shown_meme["Decision"] == "WATCH"]
        elif opportunity_filter == "Best opportunities":
            shown_meme = shown_meme[
                shown_meme["Decision"].isin(["HIGH PRIORITY", "SHORTLIST", "WATCH"])
            ].head(50)

        meme_opportunity_cols = [
            "Ticker", "Name", "Chain", "Decision", "Score",
            "Narrative Strength", "Community Strength", "Gate",
            "Price USD", "Market Cap", "Liquidity", "24h Volume",
            "Buy %", "1h %", "6h %", "24h %", "Plan Status",
            "Entry Low", "Entry High", "Negative Exit", "Positive Exit",
            "Stretch Exit", "R:R", "Net ROI %", "Risk Flags",
        ]
        visible_meme_cols = [
            col for col in meme_opportunity_cols if col in shown_meme.columns
        ]

        if shown_meme.empty:
            st.info("No candidates match that view in the latest scan.")
        else:
            meme_opportunity_display = shown_meme[visible_meme_cols]
            meme_opportunity_styled = meme_opportunity_display.style
            for _col in [
                "Decision", "Score", "Narrative Strength", "Community Strength",
                "Gate", "Liquidity", "24h Volume", "Buy %", "1h %", "6h %", "24h %",
            ]:
                if _col in meme_opportunity_display.columns:
                    meme_opportunity_styled = meme_opportunity_styled.map(
                        lambda value, col=_col: meme_cell_style(value, col),
                        subset=[_col],
                    )
            st.dataframe(
                meme_opportunity_styled,
                hide_index=True,
                use_container_width=True,
            )


with tab_meme_watchlist:
    st.markdown("### Watchlist")
    st.caption("Save meme coins you want to revisit without changing the screener rules.")

    meme_watchlist = load_meme_watchlist()

    with st.container(border=True):
        watch_title_col, watch_manage_col = st.columns([4, 1], vertical_alignment="center")
        with watch_title_col:
            st.markdown("#### ⭐ My watchlist")
            if meme_watchlist:
                st.write(
                    f"You are following **{len(meme_watchlist)}** meme coin"
                    f"{'s' if len(meme_watchlist) != 1 else ''}."
                )
                st.caption(", ".join(meme_watchlist[:8]) + ("…" if len(meme_watchlist) > 8 else ""))
            else:
                st.write("Your meme-coin watchlist is empty.")
                st.caption("Analyse a coin and tick **Watch** to save it for later.")

    if meme_watchlist:
        with st.expander("Manage meme-coin watchlist", expanded=False):
            for meme_watch_index, meme_watch_symbol in enumerate(meme_watchlist):
                meme_name_col, meme_remove_col = st.columns([5, 1], vertical_alignment="center")
                with meme_name_col:
                    st.write(f"**{meme_watch_symbol}**")
                with meme_remove_col:
                    if st.button(
                        "Remove",
                        key=f"remove_meme_watch_{meme_watch_symbol}_{meme_watch_index}",
                        use_container_width=True,
                    ):
                        set_meme_watchlist_symbol(meme_watch_symbol, False)
                        st.rerun()

with tab_meme_advanced:
    st.markdown("### Advanced Meme Screener")
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
            with st.spinner(
                f"Calculating entry / exit zones for up to {cfg['trade_plan_count']} hard-gate PASS candidates…"
            ):
                df = attach_bulk_trade_plans(
                    df,
                    max_candidates=cfg["trade_plan_count"],
                    position_size=planned_position_size,
                    round_trip_fees_pct=round_trip_fees_pct,
                )
            st.session_state.meme_scan_df = df.copy()

            c1, c2, c3, c4, c5 = st.columns(5)
            c1.metric("High priority", int((df["Decision"] == "HIGH PRIORITY").sum()))
            c2.metric("Shortlist", int((df["Decision"] == "SHORTLIST").sum()))
            c3.metric("Watch", int((df["Decision"] == "WATCH").sum()))
            c4.metric("Tokens checked", len(df))
            c5.metric(
                "Trade plans",
                int(pd.to_numeric(df["Entry Price"], errors="coerce").notna().sum())
                if "Entry Price" in df.columns else 0,
            )

            main_cols = [
                "Ticker", "Name", "Chain", "Decision", "Score",
                "Narrative Strength", "Narrative Score",
                "Community Strength", "Community Score", "Social Breadth", "Gate", "Price USD", "Market Cap", "FDV", "Circulating % (proxy)", "FDV / MCap", "Tokenomics Gate", "Liquidity", "Liquidity/Cap %",
                "24h Volume", "Vol/Liq", "Buy %", "1h %", "6h %", "24h %",
                "Pair Age h", "Plan Status", "Entry Low", "Entry High", "Negative Exit",
                "Positive Exit", "Stretch Exit", "R:R", "Net ROI %",
                "Community Takeover", "Boost", "Risk Flags", "Gate Reasons",
            ]
            st.subheader("Ranked candidates")
            meme_display = df[main_cols]
            meme_styled = meme_display.style
            for _col in [
                "Decision", "Score", "Narrative Strength", "Narrative Score",
                "Community Strength", "Community Score", "Social Breadth",
                "Gate", "Circulating % (proxy)", "FDV / MCap", "Tokenomics Gate",
                "Liquidity", "Liquidity/Cap %", "24h Volume", "Vol/Liq",
                "Buy %", "1h %", "6h %", "24h %", "Pair Age h",
            ]:
                if _col in meme_display.columns:
                    meme_styled = meme_styled.map(
                        lambda value, col=_col: meme_cell_style(value, col),
                        subset=[_col],
                    )
            st.caption("Colour key: 🟢 strong / healthy · 🟠 acceptable or watch · 🔴 weak / higher risk")
            st.dataframe(meme_styled, hide_index=True, use_container_width=True)

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

    **Hard gates** currently cover liquidity, volume, minimum pair age, anti-chase limits and the meme tokenomics rule: **at least 10% circulating float**. **Market cap itself has no minimum or maximum rule and is informational only.** Because very new DEX tokens often lack a verified supply feed, v0.1 estimates circulating float as **market cap ÷ FDV** when both values are available. If it cannot verify the ratio, tokenomics is UNKNOWN and the coin does not pass the hard gate. A **FDV/market-cap ratio of 10x or more** is flagged as high-FDV/low-float risk.

    Detailed VC allocations and insider unlock schedules are not guessed; they require a specialist verified tokenomics/unlock source and are marked for separate review.

    **Community warning:** visible socials alone do not prove a real community. Follower counts can be bought or botted, so the model deliberately rewards actual transaction participation and multi-channel presence rather than treating raw followers as truth.

    **Narrative warning:** the cultural score is heuristic. It can identify simple, recognisable, shareable meme structures, but it cannot know in advance which joke, mascot or cultural reference will genuinely go viral.

    **Technical context:** Quick Analyse now shows RSI and 20-period/2-standard-deviation Bollinger Bands for the selected on-chain timeframe. They are deliberately **not part of the meme ranking score** because meme coins can remain overbought or highly volatile for long periods; community, narrative, liquidity and real activity remain more important.

    This is **v0.1**, not the final meme-coin model. Narrative quality, holder distribution, LP lock/burn, contract/security checks, influencer quality, community growth/engagement and migration/relaunch rules are intentionally left as the next modular layers rather than being guessed.
    """
        )

    st.caption("Screening aid only. Meme coins are exceptionally speculative; live DEX data can be incomplete, manipulated or change rapidly.")
