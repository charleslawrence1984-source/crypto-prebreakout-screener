from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Dict, Iterable, List

import numpy as np
import pandas as pd
import requests
import streamlit as st


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


def pair_age_hours(pair_created_at) -> float:
    ts = safe(pair_created_at)
    if np.isnan(ts):
        return np.nan
    now_ms = datetime.now(timezone.utc).timestamp() * 1000
    return max(0.0, (now_ms - ts) / 3_600_000)


def score_candidate(pair: Dict, meta: Dict, cfg: Dict) -> Dict:
    liq = safe((pair.get("liquidity") or {}).get("usd"), 0)
    mcap = safe(pair.get("marketCap"))
    if np.isnan(mcap) or mcap <= 0:
        mcap = safe(pair.get("fdv"))
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
    if not np.isnan(age_h) and age_h < cfg["min_pair_age_hours"]:
        gates.append("Pair too new")

    too_late = ch1 > cfg["max_1h_change"] or ch24 > cfg["max_24h_change"]
    if too_late:
        gates.append("Already pumping / chase risk")

    score = 0.0

    # Liquidity quality: 20
    liq_ratio = liq / mcap if mcap and not np.isnan(mcap) else 0
    score += min(12, max(0, liq_ratio / 0.10 * 12))
    score += min(8, max(0, math.log10(max(liq, 1) / 25_000) * 4))

    # Real activity: 20
    vol_liq = vol24 / liq if liq > 0 else 0
    score += min(12, max(0, vol_liq / 2.0 * 12))
    score += min(8, max(0, total_tx / 2000 * 8))

    # Buy pressure: 15. Strong but not one-sided.
    if 0.53 <= buy_ratio <= 0.72:
        score += 15
    elif 0.50 <= buy_ratio < 0.53 or 0.72 < buy_ratio <= 0.80:
        score += 10
    elif buy_ratio > 0.80:
        score += 4
    else:
        score += max(0, buy_ratio / 0.50 * 6)

    # Community footprint: 15
    score += min(12, socials["Social count"] * 3)
    if meta.get("profile_description"):
        score += 3

    # Discovery/catalyst signals: 10, deliberately capped because boosts are paid.
    if meta.get("community_takeover"):
        score += 5
    if active_boost > 0 or meta.get("boost_total", 0) > 0:
        score += 3
    if len(meta.get("sources", [])) >= 2:
        score += 2

    # Constructive momentum, without rewarding an already vertical chart: 15
    if -2 <= ch1 <= 8:
        score += 5
    elif -5 <= ch1 <= 12:
        score += 3
    if -5 <= ch6 <= 20:
        score += 5
    elif -10 <= ch6 <= 30:
        score += 2
    if -10 <= ch24 <= 35:
        score += 5
    elif -20 <= ch24 <= 45:
        score += 2

    # Pair maturity: 5
    if not np.isnan(age_h):
        if 24 <= age_h <= 24 * 180:
            score += 5
        elif 6 <= age_h < 24 or age_h <= 24 * 365:
            score += 3

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
        "Community Takeover": bool(meta.get("community_takeover")),
        "Boost": active_boost if active_boost > 0 else meta.get("boost_total", 0),
        "Discovery": ", ".join(sorted(meta.get("sources", []))),
        "Gate Reasons": "; ".join(gates) if gates else "",
        "Risk Flags": "; ".join(risk_flags) if risk_flags else "",
        "DexScreener": pair.get("url") or "",
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
}

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
            "Ticker", "Name", "Chain", "Decision", "Score", "Gate",
            "Price USD", "Market Cap", "Liquidity", "Liquidity/Cap %",
            "24h Volume", "Vol/Liq", "Buy %", "1h %", "6h %", "24h %",
            "Pair Age h", "Community Takeover", "Boost", "Risk Flags", "Gate Reasons",
        ]
        st.subheader("Ranked candidates")
        st.dataframe(df[main_cols], hide_index=True, use_container_width=True)

        st.subheader("Community / discovery detail")
        community_cols = [
            "Ticker", "Chain", "X", "Telegram", "Discord", "Website",
            "Community Takeover", "Discovery", "Boost", "DEX", "Pair", "Token Address", "DexScreener",
        ]
        st.dataframe(df[community_cols], hide_index=True, use_container_width=True)

with st.expander("How v0.1 scores candidates"):
    st.markdown(
        """
**100-point preliminary model**

- **20 — Liquidity quality:** absolute liquidity plus liquidity relative to market cap.
- **20 — Real activity:** 24h volume relative to liquidity plus transaction count.
- **15 — Buy pressure:** constructive demand is rewarded; extremely one-sided flow is not.
- **15 — Community footprint:** visible X/Telegram/Discord/website plus a populated profile.
- **10 — Discovery/catalyst:** community takeover, active boost and appearing across multiple discovery feeds. Paid boosts are deliberately capped.
- **15 — Momentum without chasing:** constructive 1h/6h/24h movement scores better than a vertical pump.
- **5 — Pair maturity:** enough history to reduce immediate-launch noise.

**Hard gates** currently cover liquidity, volume, market-cap range, minimum pair age and anti-chase limits.

This is **v0.1**, not the final meme-coin model. Narrative quality, holder distribution, LP lock/burn, contract/security checks, influencer quality, community growth/engagement and migration/relaunch rules are intentionally left as the next modular layers rather than being guessed.
"""
    )

st.caption("Screening aid only. Meme coins are exceptionally speculative; live DEX data can be incomplete, manipulated or change rapidly.")
