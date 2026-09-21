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
from cl_signal_ui import render_module_header, render_decision_guidance


st.set_page_config(page_title="CL Signal · Crypto", page_icon="⚡", layout="wide")



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

MAJOR_CEX = {
    "Binance": "binance",
    "Coinbase": "coinbase",
    "Kraken": "kraken",
    "OKX": "okx",
    "Bybit": "bybit",
    "Gate": "gate",
    "Bitget": "bitget",
    "MEXC": "mexc",
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


def _streamlit_secret(name: str) -> str:
    try:
        return str(st.secrets.get(name, "") or "").strip()
    except Exception:
        return ""


@st.cache_data(ttl=900, show_spinner=False)
def coinmarketcal_upcoming_events(api_key: str) -> Tuple[List[Dict], str]:
    if not api_key:
        return [], "NOT CONNECTED"
    try:
        r = requests.get(
            "https://api.coinmarketcal.com/v2/events",
            params={"sortBy": "date_asc", "limit": 100},
            headers={
                "x-api-key": api_key,
                "Accept": "application/json",
                "User-Agent": "pre-breakout-screener/1.0",
            },
            timeout=20,
        )
        r.raise_for_status()
        payload = r.json() or {}
        data = payload.get("data") or []
        return (data if isinstance(data, list) else []), "CONNECTED"
    except Exception as exc:
        return [], f"ERROR: {type(exc).__name__}"


def catalyst_event_index(events: List[Dict]) -> Dict[str, List[Dict]]:
    out: Dict[str, List[Dict]] = {}
    for event in events or []:
        for coin in event.get("coins") or []:
            symbol = str(coin.get("symbol") or "").upper().strip()
            if symbol:
                out.setdefault(symbol, []).append(event)
    return out


def catalyst_info(symbol: str, event_index: Dict[str, List[Dict]], source_status: str) -> Dict:
    base = str(symbol).split("/")[0].upper()
    if source_status != "CONNECTED":
        return {
            "catalyst_status": source_status,
            "catalyst_count": 0,
            "next_catalyst": "",
            "catalyst_date": "",
            "catalyst_days": np.nan,
            "catalyst_categories": "",
            "catalyst_impact": "",
        }

    events = event_index.get(base, [])
    if not events:
        return {
            "catalyst_status": "NONE FOUND",
            "catalyst_count": 0,
            "next_catalyst": "",
            "catalyst_date": "",
            "catalyst_days": np.nan,
            "catalyst_categories": "",
            "catalyst_impact": "",
        }

    def event_sort_key(event):
        try:
            return pd.Timestamp(event.get("date") or "")
        except Exception:
            return pd.Timestamp.max.tz_localize("UTC")

    events = sorted(events, key=event_sort_key)
    event = events[0]
    title = str(event.get("title") or "")
    displayed_date = str(event.get("displayedDate") or event.get("date") or "")
    categories = event.get("categories") or []
    impact = event.get("impact")

    days = np.nan
    try:
        event_ts = pd.Timestamp(event.get("date"))
        now_ts = pd.Timestamp.now(tz="UTC")
        if event_ts.tzinfo is None:
            event_ts = event_ts.tz_localize("UTC")
        days = max(0.0, (event_ts - now_ts).total_seconds() / 86400)
    except Exception:
        pass

    lower = title.lower()
    risk_words = ("unlock", "vesting", "emission", "token release")
    strong_words = (
        "mainnet", "launch", "upgrade", "release", "integration",
        "listing", "partnership", "staking", "testnet", "roadmap",
        "governance", "proposal", "migration", "airdrop",
    )

    if any(word in lower for word in risk_words):
        status = "RISK EVENT"
    elif math.isfinite(days) and days <= 21 and any(word in lower for word in strong_words):
        status = "HIGH CATALYST"
    elif math.isfinite(days) and days <= 30:
        status = "CATALYST WATCH"
    else:
        status = "UPCOMING"

    return {
        "catalyst_status": status,
        "catalyst_count": len(events),
        "next_catalyst": title,
        "catalyst_date": displayed_date,
        "catalyst_days": round(float(days), 1) if math.isfinite(days) else np.nan,
        "catalyst_categories": ", ".join(str(x) for x in categories),
        "catalyst_impact": str(impact) if impact is not None else "",
    }


@st.cache_data(ttl=3600, show_spinner=False)
def coingecko_project_links(coin_id: str) -> Dict:
    if not coin_id:
        return {}
    try:
        r = requests.get(
            f"https://api.coingecko.com/api/v3/coins/{coin_id}",
            params={
                "localization": "false",
                "tickers": "false",
                "market_data": "false",
                "community_data": "false",
                "developer_data": "false",
                "sparkline": "false",
            },
            headers={"User-Agent": "pre-breakout-screener/1.0"},
            timeout=15,
        )
        r.raise_for_status()
        payload = r.json() or {}
        links = payload.get("links") or {}
        repos = links.get("repos_url") or {}
        homepages = [x for x in (links.get("homepage") or []) if x]
        githubs = [x for x in (repos.get("github") or []) if x]
        return {
            "twitter": str(links.get("twitter_screen_name") or "").strip(),
            "homepage": homepages[0] if homepages else "",
            "github": githubs[0] if githubs else "",
            "subreddit": str(links.get("subreddit_url") or "").strip(),
            "official_forum": next((x for x in (links.get("official_forum_url") or []) if x), ""),
        }
    except Exception:
        return {}


@st.cache_data(ttl=600, show_spinner=False)
def x_official_catalyst_posts(handle: str, bearer_token: str) -> Tuple[List[Dict], str]:
    handle = str(handle or "").lstrip("@").strip()
    if not handle:
        return [], "NO OFFICIAL X HANDLE"
    if not bearer_token:
        return [], "X API NOT CONNECTED"

    query = (
        f"from:{handle} "
        "(announce OR announcement OR launch OR mainnet OR testnet OR upgrade OR "
        "integration OR partnership OR roadmap OR release OR listing OR \"coming soon\") "
        "-is:retweet"
    )
    try:
        r = requests.get(
            "https://api.x.com/2/tweets/search/recent",
            params={
                "query": query,
                "max_results": 10,
                "tweet.fields": "created_at,public_metrics",
            },
            headers={"Authorization": f"Bearer {bearer_token}"},
            timeout=20,
        )
        r.raise_for_status()
        payload = r.json() or {}
        posts = payload.get("data") or []
        return (posts if isinstance(posts, list) else []), "CONNECTED"
    except Exception as exc:
        return [], f"ERROR: {type(exc).__name__}"


async def major_cex_presence() -> Tuple[Dict[str, set], List[str]]:
    """
    Load active spot-market base symbols for the major CEX basket once per scan.
    Failures are tolerated so one unavailable venue cannot break the screener.
    """
    presence: Dict[str, set] = {}
    errors: List[str] = []

    async def load_one(name: str, exchange_id: str):
        exchange = None
        try:
            # CCXT renamed Gate.io's exchange id from "gateio" to "gate" in
            # newer releases. Resolve the configured id defensively so an
            # unavailable/renamed venue is skipped instead of breaking the
            # whole scan or Quick Analyse.
            candidate_ids = [exchange_id]
            if exchange_id == "gate":
                candidate_ids.append("gateio")
            elif exchange_id == "gateio":
                candidate_ids.append("gate")

            cls = None
            resolved_id = exchange_id
            for candidate_id in candidate_ids:
                cls = getattr(ccxt, candidate_id, None)
                if cls is not None:
                    resolved_id = candidate_id
                    break
            if cls is None:
                return name, set(), (
                    f"{name}: exchange adapter not available in installed ccxt "
                    f"(tried {', '.join(candidate_ids)})"
                )

            exchange = cls({"enableRateLimit": True, "options": {"defaultType": "spot"}})
            markets = await asyncio.wait_for(exchange.load_markets(), timeout=25)
            bases = {
                str(market.get("base") or "").upper()
                for market in markets.values()
                if market.get("spot") and market.get("active") is not False
            }
            return name, bases, ""
        except Exception as exc:
            return name, set(), f"{name}: {type(exc).__name__}: {exc}"
        finally:
            if exchange is not None:
                try:
                    await exchange.close()
                except Exception:
                    pass

    results = await asyncio.gather(
        *(load_one(name, exchange_id) for name, exchange_id in MAJOR_CEX.items())
    )
    for name, bases, error in results:
        if bases:
            presence[name] = bases
        if error:
            errors.append(error)
    return presence, errors


def exchange_listing_info(symbol: str, presence: Dict[str, set]) -> Dict:
    base = str(symbol).split("/")[0].upper()
    listed_on = sorted(
        name for name, bases in presence.items()
        if base in bases
    )
    count = len(listed_on)
    venues_checked = len(presence)

    if venues_checked < 4:
        gate = "UNKNOWN"
        quality = "DATA LIMITED"
    elif count >= 4:
        gate = "PASS"
        quality = "STRONG"
    elif count == 3:
        gate = "PASS"
        quality = "GOOD"
    elif count == 2:
        gate = "PASS"
        quality = "ACCEPTABLE"
    else:
        gate = "FAIL"
        quality = "WEAK"

    return {
        "major_cex_count": count,
        "major_cex_list": ", ".join(listed_on),
        "major_cex_quality": quality,
        "major_cex_gate": gate,
        "major_cex_checked": venues_checked,
    }


@st.cache_data(ttl=900, show_spinner=False)
def coingecko_category_leaders() -> Dict[str, List[str]]:
    """
    Map CoinGecko coin IDs to category names where the coin is currently
    one of CoinGecko's published top 3 coins for that category.
    """
    try:
        r = requests.get(
            "https://api.coingecko.com/api/v3/coins/categories",
            headers={"User-Agent": "pre-breakout-screener/1.0"},
            timeout=20,
        )
        r.raise_for_status()
        rows = r.json() or []
    except Exception:
        return {}

    leaders: Dict[str, List[str]] = {}
    for row in rows if isinstance(rows, list) else []:
        category_name = str(row.get("name") or "").strip()
        for coin_id in row.get("top_3_coins_id") or []:
            cid = str(coin_id or "").strip()
            if not cid:
                continue
            leaders.setdefault(cid, [])
            if category_name and category_name not in leaders[cid]:
                leaders[cid].append(category_name)
    return leaders


@st.cache_data(ttl=300, show_spinner=False)
def coingecko_trending_attention() -> Dict[str, Dict]:
    try:
        r = requests.get(
            "https://api.coingecko.com/api/v3/search/trending",
            headers={"User-Agent": "pre-breakout-screener/1.0"},
            timeout=15,
        )
        r.raise_for_status()
        payload = r.json() or {}
    except Exception:
        return {}

    out: Dict[str, Dict] = {}
    for rank, row in enumerate(payload.get("coins") or [], start=1):
        item = row.get("item") or {}
        symbol = str(item.get("symbol") or "").upper().strip()
        if symbol:
            out[symbol] = {
                "trending_rank": rank,
                "trending_name": str(item.get("name") or ""),
            }
    return out


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


def tokenomics_from_market(
    symbol: str,
    snapshot: Dict[str, Dict],
    category_leaders: Optional[Dict[str, List[str]]] = None,
) -> Dict:
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

    coin_id = item.get("id") or ""
    leader_categories = (
        (category_leaders or {}).get(coin_id, [])
        if coin_id else []
    )
    if category_leaders:
        category_leader = "TOP 3" if leader_categories else "NOT TOP 3"
    else:
        category_leader = "UNKNOWN"

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
        "coingecko_id": coin_id,
        "category_leader": category_leader,
        "leader_categories": ", ".join(leader_categories),
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


def period_return_pct(df: pd.DataFrame, days: int) -> float:
    if df is None or df.empty or "close" not in df.columns:
        return np.nan
    s = pd.to_numeric(df["close"], errors="coerce").dropna()
    if len(s) < 2:
        return np.nan
    lookback = min(days, len(s) - 1)
    old = float(s.iloc[-1 - lookback])
    new = float(s.iloc[-1])
    if old <= 0:
        return np.nan
    return (new / old - 1) * 100


def category_rotation_table(df: pd.DataFrame) -> pd.DataFrame:
    """
    Build a category-rotation view from scanned coins that are currently in
    CoinGecko's top 3 for at least one category. Relative returns are measured
    versus BTC over ~30/90/180 days.
    """
    required = {
        "Category leader", "Leader categories", "Coin",
        "RS vs BTC 30d %", "RS vs BTC 90d %", "RS vs BTC 180d %",
    }
    if df is None or df.empty or not required.issubset(set(df.columns)):
        return pd.DataFrame()

    leaders = df[
        (df["Category leader"] == "TOP 3")
        & df["Leader categories"].fillna("").ne("")
    ].copy()
    if leaders.empty:
        return pd.DataFrame()

    leaders["Category"] = leaders["Leader categories"].str.split(", ")
    exploded = leaders.explode("Category")
    exploded = exploded[exploded["Category"].fillna("").ne("")].copy()
    if exploded.empty:
        return pd.DataFrame()

    records = []
    for category, grp in exploded.groupby("Category"):
        rs30 = pd.to_numeric(grp["RS vs BTC 30d %"], errors="coerce").dropna()
        rs90 = pd.to_numeric(grp["RS vs BTC 90d %"], errors="coerce").dropna()
        rs180 = pd.to_numeric(grp["RS vs BTC 180d %"], errors="coerce").dropna()
        if rs30.empty and rs90.empty and rs180.empty:
            continue

        med30 = float(rs30.median()) if not rs30.empty else np.nan
        med90 = float(rs90.median()) if not rs90.empty else np.nan
        med180 = float(rs180.median()) if not rs180.empty else np.nan
        monthly30 = med30 if math.isfinite(med30) else np.nan
        monthly90 = med90 / 3 if math.isfinite(med90) else np.nan
        monthly180 = med180 / 6 if math.isfinite(med180) else np.nan
        breadth30 = float((rs30 > 0).mean() * 100) if not rs30.empty else np.nan

        # Momentum score emphasizes recent rotation while retaining 3m/6m context.
        score = 0.0
        weight = 0.0
        if math.isfinite(monthly30):
            score += 45 * clamp_score((monthly30 + 5) / 20)
            weight += 45
        if math.isfinite(monthly90):
            score += 30 * clamp_score((monthly90 + 3) / 15)
            weight += 30
        if math.isfinite(monthly180):
            score += 15 * clamp_score((monthly180 + 2) / 12)
            weight += 15
        if math.isfinite(breadth30):
            score += 10 * (breadth30 / 100)
            weight += 10
        momentum_score = round(score / weight * 100, 1) if weight > 0 else np.nan

        if (
            math.isfinite(monthly30)
            and monthly30 > 2
            and (
                not math.isfinite(monthly90)
                or monthly30 > max(2, monthly90 * 1.25)
            )
            and (
                not math.isfinite(monthly180)
                or monthly30 > max(2, monthly180 * 1.25)
            )
        ):
            status = "ROTATING IN"
        elif (
            math.isfinite(med30) and med30 > 0
            and math.isfinite(med90) and med90 > 0
            and math.isfinite(med180) and med180 > 0
        ):
            status = "LEADING"
        elif (
            math.isfinite(med30) and med30 < 0
            and (
                (math.isfinite(med90) and med90 > 0)
                or (math.isfinite(med180) and med180 > 0)
            )
        ):
            status = "FADING"
        elif (
            math.isfinite(med30) and med30 <= 0
            and (not math.isfinite(med90) or med90 <= 0)
            and (not math.isfinite(med180) or med180 <= 0)
        ):
            status = "WEAK"
        else:
            status = "MIXED"

        leaders_observed = sorted(set(grp["Coin"].astype(str)))
        coverage = len(leaders_observed)
        confidence = "HIGH" if coverage >= 3 else "MEDIUM" if coverage == 2 else "LOW"

        records.append({
            "Category": category,
            "Rotation status": status,
            "Category momentum": momentum_score,
            "RS vs BTC 30d %": round(med30, 2) if math.isfinite(med30) else np.nan,
            "RS vs BTC 90d %": round(med90, 2) if math.isfinite(med90) else np.nan,
            "RS vs BTC 180d %": round(med180, 2) if math.isfinite(med180) else np.nan,
            "30d leader breadth %": round(breadth30, 1) if math.isfinite(breadth30) else np.nan,
            "Leaders observed": coverage,
            "Confidence": confidence,
            "Observed leaders": ", ".join(leaders_observed),
        })

    if not records:
        return pd.DataFrame()

    out = pd.DataFrame(records)
    status_rank = {
        "ROTATING IN": 0,
        "LEADING": 1,
        "MIXED": 2,
        "FADING": 3,
        "WEAK": 4,
    }
    out["_status_rank"] = out["Rotation status"].map(status_rank).fillna(9)
    return out.sort_values(
        ["_status_rank", "Category momentum"],
        ascending=[True, False],
    ).drop(columns=["_status_rank"])


def project_freshness(dfd: pd.DataFrame, dfw: Optional[pd.DataFrame] = None) -> Dict:
    """
    Practical freshness proxy based on available spot-price history on the selected
    exchange. This is not the project's true launch age, but it is useful for
    distinguishing newer listings from long-established assets without one extra
    API request per coin.
    """
    source = dfw if dfw is not None and not dfw.empty else dfd
    if source is None or source.empty or "timestamp" not in source.columns:
        return {
            "freshness": "UNKNOWN",
            "history_days": np.nan,
            "freshness_score": np.nan,
            "freshness_basis": "No exchange-history data",
        }

    ts = pd.to_datetime(source["timestamp"], errors="coerce", utc=True).dropna()
    if len(ts) < 2:
        return {
            "freshness": "UNKNOWN",
            "history_days": np.nan,
            "freshness_score": np.nan,
            "freshness_basis": "Insufficient exchange-history data",
        }

    history_days = max(0.0, (ts.iloc[-1] - ts.iloc[0]).total_seconds() / 86400)
    if history_days < 180:
        label, score = "NEW", 100.0
    elif history_days < 540:
        label, score = "RECENT", 80.0
    elif history_days < 1095:
        label, score = "MATURE", 55.0
    else:
        label, score = "LEGACY", 35.0

    return {
        "freshness": label,
        "history_days": round(history_days, 0),
        "freshness_score": score,
        "freshness_basis": "Available spot history on selected exchange",
    }


def trend_channel(df: pd.DataFrame, window: int = 80, high_col: str = "high", low_col: str = "low", close_col: str = "close") -> Dict:
    if df is None or len(df) < 30:
        return {"direction":"UNAVAILABLE","position":np.nan,"support":np.nan,"resistance":np.nan,"width_pct":np.nan,"rr":np.nan,"touches":0,"quality":"LOW","state":"NONE","slope_pct":np.nan}
    d = df.tail(min(window, len(df))).copy()
    for col in (high_col, low_col, close_col):
        d[col] = pd.to_numeric(d[col], errors="coerce")
    d = d.dropna(subset=[high_col, low_col, close_col])
    if len(d) < 30:
        return {"direction":"UNAVAILABLE","position":np.nan,"support":np.nan,"resistance":np.nan,"width_pct":np.nan,"rr":np.nan,"touches":0,"quality":"LOW","state":"NONE","slope_pct":np.nan}
    x = np.arange(len(d), dtype=float)
    y = d[close_col].to_numpy(dtype=float)
    slope, intercept = np.polyfit(x, y, 1)
    centre = intercept + slope * x
    upper = centre + float(np.quantile(d[high_col].to_numpy(dtype=float) - centre, 0.90))
    lower = centre + float(np.quantile(d[low_col].to_numpy(dtype=float) - centre, 0.10))
    support, resistance, price = float(lower[-1]), float(upper[-1]), float(y[-1])
    width = resistance - support
    if width <= 0 or price <= 0:
        return {"direction":"UNAVAILABLE","position":np.nan,"support":support,"resistance":resistance,"width_pct":np.nan,"rr":np.nan,"touches":0,"quality":"LOW","state":"NONE","slope_pct":np.nan}
    slope_pct = slope * max(len(d)-1,1) / max(float(centre[0]),1e-12) * 100
    direction = "RISING" if slope_pct >= 3 else "FALLING" if slope_pct <= -3 else "SIDEWAYS"
    position = (price - support) / width * 100
    tol = max(width * 0.08, price * 0.005)
    touches = int((np.abs(d[low_col].to_numpy(dtype=float)-lower) <= tol).sum() + (np.abs(d[high_col].to_numpy(dtype=float)-upper) <= tol).sum())
    ss_res = float(np.sum((y-centre)**2)); ss_tot = float(np.sum((y-np.mean(y))**2))
    r2 = max(0.0, 1-ss_res/ss_tot) if ss_tot > 0 else 0.0
    if direction == "SIDEWAYS":
        quality = "HIGH" if touches >= 6 else "MEDIUM" if touches >= 4 else "LOW"
    else:
        quality = "HIGH" if touches >= 6 and r2 >= 0.45 else "MEDIUM" if touches >= 4 and r2 >= 0.20 else "LOW"
    state = "ABOVE CHANNEL" if price > resistance + tol else "BELOW CHANNEL" if price < support - tol else "INSIDE"
    downside = max(price-support, price*0.001); upside = max(resistance-price,0.0)
    return {"direction":direction,"position":round(position,1),"support":support,"resistance":resistance,"width_pct":round(width/price*100,2),"rr":round(upside/downside,2),"touches":touches,"quality":quality,"state":state,"slope_pct":round(slope_pct,2),"lower_series":lower.tolist(),"upper_series":upper.tolist(),"start":len(df)-len(d)}


def latest_completed_4h_candle_signal(df4h: pd.DataFrame) -> Dict:
    """
    Detect a bearish red shooting star on the latest completed 4h candle.
    A live/incomplete candle is ignored so a temporary wick cannot create a false warning.
    """
    if df4h is None or df4h.empty or len(df4h) < 2:
        return {
            "candle_pattern": "UNAVAILABLE",
            "candle_caution": False,
            "candle_detail": "Not enough 4h candle history",
        }

    x = df4h.copy()
    timestamps = pd.to_datetime(x["timestamp"], errors="coerce", utc=True)
    now = pd.Timestamp.now(tz="UTC")
    completed_mask = timestamps + pd.Timedelta(hours=4) <= now
    completed = x.loc[completed_mask]

    if completed.empty:
        candle = x.iloc[-2]
    else:
        candle = completed.iloc[-1]

    o = float(candle["open"])
    h = float(candle["high"])
    l = float(candle["low"])
    close = float(candle["close"])
    candle_range = max(h - l, 0.0)

    if candle_range <= 0:
        return {
            "candle_pattern": "OTHER",
            "candle_caution": False,
            "candle_detail": "Flat completed 4h candle",
        }

    body = abs(close - o)
    upper_wick = h - max(o, close)
    lower_wick = min(o, close) - l
    red = close < o

    # Shooting-star geometry: small body near the low, long upper rejection wick,
    # little lower wick. Require the candle to close red for the caution rule.
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
                f"Latest completed 4h candle rejected higher prices: "
                f"upper wick {upper_wick / candle_range * 100:.0f}% of range; red close."
            ),
        }

    return {
        "candle_pattern": "OTHER",
        "candle_caution": False,
        "candle_detail": "No red shooting-star warning on latest completed 4h candle",
    }


def bollinger_context(df: pd.DataFrame, close_col: str = "close", window: int = 20) -> Dict:
    if df is None or len(df) < max(window + 5, 30):
        return {
            "bb_mid": np.nan, "bb_upper": np.nan, "bb_lower": np.nan,
            "bb_width_pct": np.nan, "bb_position_pct": np.nan,
            "bb_width_percentile": np.nan, "bb_regime": "UNAVAILABLE",
        }
    d = df.copy()
    close = pd.to_numeric(d[close_col], errors="coerce")
    mid = close.rolling(window).mean()
    sd = close.rolling(window).std()
    upper = mid + 2 * sd
    lower = mid - 2 * sd
    width_pct_series = (upper - lower) / mid.replace(0, np.nan) * 100

    price = _safe_float(close.iloc[-1], np.nan)
    mid_now = _safe_float(mid.iloc[-1], np.nan)
    upper_now = _safe_float(upper.iloc[-1], np.nan)
    lower_now = _safe_float(lower.iloc[-1], np.nan)
    width_now = _safe_float(width_pct_series.iloc[-1], np.nan)

    if not all(math.isfinite(v) for v in [price, mid_now, upper_now, lower_now, width_now]):
        return {
            "bb_mid": mid_now, "bb_upper": upper_now, "bb_lower": lower_now,
            "bb_width_pct": width_now, "bb_position_pct": np.nan,
            "bb_width_percentile": np.nan, "bb_regime": "UNAVAILABLE",
        }

    band_range = upper_now - lower_now
    position = (price - lower_now) / band_range * 100 if band_range > 0 else np.nan
    hist_width = width_pct_series.dropna().tail(120)
    percentile = (
        float((hist_width <= width_now).mean() * 100)
        if len(hist_width) >= 20 else np.nan
    )

    recent = width_pct_series.dropna().tail(6)
    expanding = len(recent) >= 4 and recent.iloc[-1] > recent.iloc[0] * 1.12
    if math.isfinite(percentile) and percentile <= 20:
        regime = "SQUEEZE"
    elif expanding:
        regime = "EXPANDING"
    else:
        regime = "NORMAL"

    return {
        "bb_mid": mid_now,
        "bb_upper": upper_now,
        "bb_lower": lower_now,
        "bb_width_pct": round(width_now, 2),
        "bb_position_pct": round(float(position), 1) if math.isfinite(position) else np.nan,
        "bb_width_percentile": round(float(percentile), 1) if math.isfinite(percentile) else np.nan,
        "bb_regime": regime,
    }


def ascending_triangle_pattern(df4h: pd.DataFrame, window: int = 60) -> Dict:
    if df4h is None or len(df4h) < 36:
        return {
            "triangle_label": "NO TRIANGLE",
            "triangle_score": 0.0,
            "triangle_resistance": np.nan,
            "triangle_support_now": np.nan,
            "triangle_touches": 0,
            "triangle_flatness_pct": np.nan,
            "triangle_compression_pct": np.nan,
            "triangle_measured_target": np.nan,
            "triangle_detail": "Insufficient 4h history",
        }

    d = df4h.tail(min(window, len(df4h))).copy()
    for col in ("high", "low", "close", "volume"):
        d[col] = pd.to_numeric(d[col], errors="coerce")
    d = d.dropna(subset=["high", "low", "close", "volume"])
    if len(d) < 36:
        return {
            "triangle_label": "NO TRIANGLE",
            "triangle_score": 0.0,
            "triangle_resistance": np.nan,
            "triangle_support_now": np.nan,
            "triangle_touches": 0,
            "triangle_flatness_pct": np.nan,
            "triangle_compression_pct": np.nan,
            "triangle_measured_target": np.nan,
            "triangle_detail": "Insufficient clean 4h history",
        }

    x = np.arange(len(d), dtype=float)
    highs = d["high"].to_numpy(dtype=float)
    lows = d["low"].to_numpy(dtype=float)
    closes = d["close"].to_numpy(dtype=float)
    price = float(closes[-1])

    resistance = float(np.quantile(highs, 0.93))
    top_mask = highs >= resistance * 0.985
    top_x = x[top_mask]
    top_y = highs[top_mask]
    # Count separate resistance-test clusters rather than every adjacent candle.
    prior_hit = np.concatenate(([False], top_mask[:-1]))
    touches = int(np.sum(top_mask & ~prior_hit))

    if touches >= 2:
        top_slope, top_intercept = np.polyfit(top_x, top_y, 1)
        top_start = top_intercept
        top_end = top_intercept + top_slope * (len(d) - 1)
        flatness_pct = abs(top_end - top_start) / max(resistance, 1e-12) * 100
    else:
        top_slope = 0.0
        flatness_pct = 99.0

    # Rising support is based on lower quantile points so one wick does not define the triangle.
    low_cutoff = float(np.quantile(lows, 0.35))
    low_mask = lows <= low_cutoff
    low_x = x[low_mask]
    low_y = lows[low_mask]
    if len(low_x) >= 4:
        support_slope, support_intercept = np.polyfit(low_x, low_y, 1)
    else:
        support_slope, support_intercept = np.polyfit(x, lows, 1)

    support_start = float(support_intercept)
    support_now = float(support_intercept + support_slope * (len(d) - 1))
    start_gap = max(resistance - support_start, price * 0.001)
    end_gap = max(resistance - support_now, price * 0.001)
    compression_pct = (1 - end_gap / start_gap) * 100

    support_rise_pct = (
        (support_now / support_start - 1) * 100
        if support_start > 0 else -99.0
    )
    distance_to_resistance_pct = (resistance - price) / price * 100

    vol_recent = float(d["volume"].iloc[-10:].mean())
    vol_early = float(d["volume"].iloc[: max(10, len(d)//3)].mean())
    volume_ratio = vol_recent / vol_early if vol_early > 0 else 1.0

    flat_component = clamp_score((2.5 - flatness_pct) / 2.5)
    touch_component = clamp_score((touches - 1) / 3)
    rising_low_component = clamp_score((support_rise_pct + 1.0) / 8.0)
    compression_component = clamp_score(compression_pct / 45.0)
    volume_component = clamp_score((1.20 - volume_ratio) / 0.55)
    location_component = (
        1.0 if 0.20 <= distance_to_resistance_pct <= 4.0
        else 0.65 if 0 <= distance_to_resistance_pct <= 6.0
        else 0.20
    )

    score = round(
        25 * flat_component
        + 20 * touch_component
        + 20 * rising_low_component
        + 15 * compression_component
        + 10 * volume_component
        + 10 * location_component,
        1,
    )

    structural_ok = (
        touches >= 2
        and flatness_pct <= 3.0
        and support_slope > 0
        and compression_pct > 5
        and price <= resistance * 1.01
    )
    if structural_ok and score >= 75:
        label = "ASCENDING TRIANGLE — STRONG"
    elif structural_ok and score >= 60:
        label = "ASCENDING TRIANGLE — DEVELOPING"
    elif score >= 45 and touches >= 2 and support_slope > 0:
        label = "POSSIBLE ASCENDING TRIANGLE"
    else:
        label = "NO TRIANGLE"

    base_low = float(np.quantile(lows[: max(12, len(lows)//3)], 0.20))
    height = max(resistance - base_low, 0.0)
    measured_target = resistance + height if structural_ok and height > 0 else np.nan

    return {
        "triangle_label": label,
        "triangle_score": score,
        "triangle_resistance": resistance,
        "triangle_support_now": support_now,
        "triangle_support_start": support_start,
        "triangle_window_bars": len(d),
        "triangle_touches": touches,
        "triangle_flatness_pct": round(flatness_pct, 2),
        "triangle_compression_pct": round(compression_pct, 1),
        "triangle_volume_ratio": round(volume_ratio, 2),
        "triangle_measured_target": measured_target,
        "triangle_detail": (
            f"{touches} resistance touches · resistance flatness {flatness_pct:.2f}% · "
            f"rising support {support_rise_pct:.1f}% · compression {compression_pct:.1f}% · "
            f"recent/early volume {volume_ratio:.2f}x"
        ),
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

    label = str(value).upper()
    if column == "Status":
        return green if label == "BUY" else amber if label == "WAIT" else red
    if column == "Pattern":
        if label == "ASCENDING TRIANGLE — STRONG":
            return green
        if label in ("ASCENDING TRIANGLE — DEVELOPING", "POSSIBLE ASCENDING TRIANGLE"):
            return amber
        return red
    if column == "SMA regime":
        if label in ("BULLISH STACK", "GOLDEN CROSS — PULLBACK", "ABOVE 200D"):
            return green
        if label in ("EARLY RECOVERY", "ABOVE 50D", "UNAVAILABLE"):
            return amber
        return red
    if column in ("BB 4h regime", "BB DAILY REGIME", "BB Daily regime"):
        return green if label == "SQUEEZE" else amber if label == "NORMAL" else red if label == "EXPANDING" else ""
    if column in ("4h Channel", "Daily Channel"):
        return green if label == "RISING" else amber if label == "SIDEWAYS" else red if label == "FALLING" else ""
    if column == "4h Channel quality":
        return green if label == "HIGH" else amber if label == "MEDIUM" else red if label == "LOW" else ""
    if column == "Category leader":
        return green if label == "TOP 3" else amber if label == "UNKNOWN" else red
    if column == "Major CEX quality":
        if label in ("STRONG", "GOOD"):
            return green
        if label in ("ACCEPTABLE", "DATA LIMITED"):
            return amber
        return red
    if column == "Macro regime":
        if label in ("EXPANSION", "IMPROVING"):
            return green
        if label in ("MIXED/NEUTRAL", "DATA LIMITED"):
            return amber
        return red
    if column == "Catalyst status":
        if label in ("HIGH CATALYST", "CATALYST WATCH"):
            return green
        if label in ("UPCOMING", "NONE FOUND", "NOT CONNECTED"):
            return amber
        if label == "RISK EVENT":
            return red
        return ""
    if column == "Tokenomics gate":
        return green if label == "PASS" else red if label == "FAIL" else amber
    if column == "Candle caution":
        return red if label == "CAUTION" else green if label == "CLEAR" else amber

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
    if column in (
        "RS vs BTC %", "RS vs BTC 96h %",
        "RS vs BTC 30d %", "RS vs BTC 90d %", "RS vs BTC 180d %",
    ):
        return green if number > 0 else amber if number >= -2 else red
    if column in ("R:R", "4h Channel R:R"):
        return green if number >= 2 else amber if number >= 1.5 else red
    if column == "Score":
        return green if number >= 85 else amber if number >= 80 else red
    if column == "Signal agreement %":
        return green if number >= 80 else amber if number >= 65 else red
    if column == "Conflict count":
        return green if number == 0 else amber if number <= 2 else red
    if column == "Non-TA confirmations":
        return green if number >= 3 else amber if number >= 1 else red
    if column == "Triangle score":
        return green if number >= 75 else amber if number >= 55 else red
    if column == "ROI %":
        return green if number >= 30 else amber if number >= 15 else red
    if column == "Macro score":
        return green if number >= 58 else amber if number >= 42 else red
    if column == "4h Channel pos %":
        return green if 10 <= number <= 65 else amber if 0 <= number <= 85 else red
    if column == "BB 4h width percentile":
        return green if number <= 20 else amber if number <= 50 else red
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
    freshness_info = project_freshness(dfd, dfw)
    candle_signal = latest_completed_4h_candle_signal(df4h)
    triangle = ascending_triangle_pattern(df4h, 60)
    bb_4h = bollinger_context(df4h)
    bb_daily = bollinger_context(dfd)
    channel_4h = trend_channel(df4h, 80)
    channel_daily = trend_channel(dfd, 90)

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
    d["sma50"] = d["close"].rolling(50).mean()
    d["sma200"] = d["close"].rolling(200).mean()
    d["rsi"] = rsi(d["close"])
    d["atr"] = atr(d)
    d["obv"] = obv(d)
    daily_price = float(d["close"].iloc[-1])
    daily_ema = float(d["ema20"].iloc[-1])
    daily_sma50 = _safe_float(d["sma50"].iloc[-1], np.nan)
    daily_sma200 = _safe_float(d["sma200"].iloc[-1], np.nan)
    daily_rsi = float(d["rsi"].iloc[-1])

    if math.isfinite(daily_sma50) and math.isfinite(daily_sma200):
        if daily_price > daily_sma50 > daily_sma200:
            sma_regime = "BULLISH STACK"
        elif daily_sma50 > daily_sma200 and daily_price <= daily_sma50:
            sma_regime = "GOLDEN CROSS — PULLBACK"
        elif daily_price > daily_sma50 and daily_sma50 <= daily_sma200:
            sma_regime = "EARLY RECOVERY"
        elif daily_price > daily_sma200:
            sma_regime = "ABOVE 200D"
        else:
            sma_regime = "BELOW 200D"
    elif math.isfinite(daily_sma50):
        sma_regime = "ABOVE 50D" if daily_price > daily_sma50 else "BELOW 50D"
    else:
        sma_regime = "UNAVAILABLE"
    if daily_price >= daily_ema and daily_rsi <= 70:
        daily_component = 1.0
    elif daily_price >= daily_ema:
        daily_component = 0.6
    else:
        daily_component = 0.25
    daily_score = 5 * daily_component

    coin_ret_30 = period_return_pct(dfd, 30)
    coin_ret_90 = period_return_pct(dfd, 90)
    coin_ret_180 = period_return_pct(dfd, 180)
    btc_ret_30 = period_return_pct(btcd, 30) if btcd is not None else np.nan
    btc_ret_90 = period_return_pct(btcd, 90) if btcd is not None else np.nan
    btc_ret_180 = period_return_pct(btcd, 180) if btcd is not None else np.nan
    rs_btc_30 = (
        coin_ret_30 - btc_ret_30
        if math.isfinite(coin_ret_30) and math.isfinite(btc_ret_30)
        else np.nan
    )
    rs_btc_90 = (
        coin_ret_90 - btc_ret_90
        if math.isfinite(coin_ret_90) and math.isfinite(btc_ret_90)
        else np.nan
    )
    rs_btc_180 = (
        coin_ret_180 - btc_ret_180
        if math.isfinite(coin_ret_180) and math.isfinite(btc_ret_180)
        else np.nan
    )

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
        "Daily SMA50": daily_sma50,
        "Daily SMA200": daily_sma200,
        "20-day support cluster": float(d["low"].iloc[-20:].quantile(0.35)),
    }
    if channel_4h.get("quality") in ("HIGH", "MEDIUM") and channel_4h.get("direction") != "FALLING":
        support_candidates["4h channel support"] = _safe_float(channel_4h.get("support"))
    if channel_daily.get("quality") in ("HIGH", "MEDIUM") and channel_daily.get("direction") != "FALLING":
        support_candidates["Daily channel support"] = _safe_float(channel_daily.get("support"))
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
    triangle_target = _safe_float(triangle.get("triangle_measured_target"), np.nan)
    if (
        triangle.get("triangle_label") in (
            "ASCENDING TRIANGLE — STRONG",
            "ASCENDING TRIANGLE — DEVELOPING",
        )
        and math.isfinite(triangle_target)
        and triangle_target > price
    ):
        credible_targets.append(float(triangle_target))
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
            else "Ascending-triangle measured move meeting the 30% rule"
            if math.isfinite(triangle_target)
            and abs(projected_target - triangle_target) < max(triangle_target * 1e-8, 1e-12)
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
        "return_30d_pct": round(float(coin_ret_30), 2) if math.isfinite(coin_ret_30) else np.nan,
        "return_90d_pct": round(float(coin_ret_90), 2) if math.isfinite(coin_ret_90) else np.nan,
        "return_180d_pct": round(float(coin_ret_180), 2) if math.isfinite(coin_ret_180) else np.nan,
        "rs_vs_btc_30d_pct": round(float(rs_btc_30), 2) if math.isfinite(rs_btc_30) else np.nan,
        "rs_vs_btc_90d_pct": round(float(rs_btc_90), 2) if math.isfinite(rs_btc_90) else np.nan,
        "rs_vs_btc_180d_pct": round(float(rs_btc_180), 2) if math.isfinite(rs_btc_180) else np.nan,
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
        "project_freshness": freshness_info["freshness"],
        "history_days": freshness_info["history_days"],
        "freshness_score": freshness_info["freshness_score"],
        "freshness_basis": freshness_info["freshness_basis"],
        "candle_pattern": candle_signal["candle_pattern"],
        "candle_caution": bool(candle_signal["candle_caution"]),
        "candle_detail": candle_signal["candle_detail"],
        "triangle_label": triangle.get("triangle_label", "NO TRIANGLE"),
        "triangle_score": triangle.get("triangle_score", 0.0),
        "triangle_resistance": triangle.get("triangle_resistance", np.nan),
        "triangle_support_now": triangle.get("triangle_support_now", np.nan),
        "triangle_touches": triangle.get("triangle_touches", 0),
        "triangle_flatness_pct": triangle.get("triangle_flatness_pct", np.nan),
        "triangle_compression_pct": triangle.get("triangle_compression_pct", np.nan),
        "triangle_measured_target": triangle.get("triangle_measured_target", np.nan),
        "triangle_detail": triangle.get("triangle_detail", ""),
        "sma50": daily_sma50,
        "sma200": daily_sma200,
        "sma_regime": sma_regime,
        "bb_4h_regime": bb_4h.get("bb_regime", "UNAVAILABLE"),
        "bb_4h_mid": bb_4h.get("bb_mid", np.nan),
        "bb_4h_upper": bb_4h.get("bb_upper", np.nan),
        "bb_4h_lower": bb_4h.get("bb_lower", np.nan),
        "bb_4h_width_pct": bb_4h.get("bb_width_pct", np.nan),
        "bb_4h_position_pct": bb_4h.get("bb_position_pct", np.nan),
        "bb_4h_width_percentile": bb_4h.get("bb_width_percentile", np.nan),
        "bb_daily_regime": bb_daily.get("bb_regime", "UNAVAILABLE"),
        "bb_daily_width_pct": bb_daily.get("bb_width_pct", np.nan),
        "bb_daily_position_pct": bb_daily.get("bb_position_pct", np.nan),
        "price_vs_sma50_pct": (
            round((daily_price / daily_sma50 - 1) * 100, 2)
            if math.isfinite(daily_sma50) and daily_sma50 > 0 else np.nan
        ),
        "price_vs_sma200_pct": (
            round((daily_price / daily_sma200 - 1) * 100, 2)
            if math.isfinite(daily_sma200) and daily_sma200 > 0 else np.nan
        ),
        "channel_4h_direction": channel_4h.get("direction", "UNAVAILABLE"),
        "channel_4h_position": channel_4h.get("position", np.nan),
        "channel_4h_support": channel_4h.get("support", np.nan),
        "channel_4h_resistance": channel_4h.get("resistance", np.nan),
        "channel_4h_width_pct": channel_4h.get("width_pct", np.nan),
        "channel_4h_rr": channel_4h.get("rr", np.nan),
        "channel_4h_touches": channel_4h.get("touches", 0),
        "channel_4h_quality": channel_4h.get("quality", "LOW"),
        "channel_4h_state": channel_4h.get("state", "NONE"),
        "channel_daily_direction": channel_daily.get("direction", "UNAVAILABLE"),
        "channel_daily_position": channel_daily.get("position", np.nan),
        "channel_daily_support": channel_daily.get("support", np.nan),
        "channel_daily_resistance": channel_daily.get("resistance", np.nan),
        "channel_daily_quality": channel_daily.get("quality", "LOW"),
        "channel_daily_state": channel_daily.get("state", "NONE"),
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
        tokenomics = tokenomics_from_market(
            symbol,
            coingecko_tokenomics_snapshot(),
            coingecko_category_leaders(),
        )
        result.update(tokenomics)
        cex_presence, cex_errors = await major_cex_presence()
        result.update(exchange_listing_info(symbol, cex_presence))
        result["major_cex_errors"] = cex_errors
        cmcal_events, cmcal_status = await asyncio.to_thread(
            coinmarketcal_upcoming_events,
            _streamlit_secret("COINMARKETCAL_API_KEY"),
        )
        result.update(
            catalyst_info(symbol, catalyst_event_index(cmcal_events), cmcal_status)
        )
        return symbol, result, {"4h": df4, "1d": dfd, "1w": dfw}
    finally:
        await exchange.close()


async def scan_exchange(cfg: ScreenerConfig, progress=None) -> Tuple[pd.DataFrame, Dict[str, Dict[str, pd.DataFrame]], List[str]]:
    deadline = asyncio.get_running_loop().time() + 300
    universe, _, _ = await asyncio.wait_for(fetch_market_universe(cfg), timeout=45)
    tokenomics_snapshot, category_leaders, cex_result, cmcal_result = await asyncio.gather(
        asyncio.to_thread(coingecko_tokenomics_snapshot),
        asyncio.to_thread(coingecko_category_leaders),
        major_cex_presence(),
        asyncio.to_thread(
            coinmarketcal_upcoming_events,
            _streamlit_secret("COINMARKETCAL_API_KEY"),
        ),
    )
    cex_presence, cex_errors = cex_result
    cmcal_events, cmcal_status = cmcal_result
    cmcal_index = catalyst_event_index(cmcal_events)
    cls = getattr(ccxt, cfg.exchange_id)
    exchange = cls({"enableRateLimit": True, "options": {"defaultType": "spot"}})
    errors: List[str] = list(cex_errors)
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
                    result.update(
                        tokenomics_from_market(symbol, tokenomics_snapshot, category_leaders)
                    )
                    result.update(exchange_listing_info(symbol, cex_presence))
                    result.update(catalyst_info(symbol, cmcal_index, cmcal_status))
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
                "Major CEX gate": r.get("major_cex_gate", "UNKNOWN"),
                "Major CEX count": r.get("major_cex_count", 0),
                "Major CEX quality": r.get("major_cex_quality", "DATA LIMITED"),
                "Major CEX listings": r.get("major_cex_list", ""),
                "Category leader": r.get("category_leader", "UNKNOWN"),
                "Leader categories": r.get("leader_categories", ""),
                "Project freshness": r.get("project_freshness", "UNKNOWN"),
                "History days": r.get("history_days", np.nan),
                "Freshness score": r.get("freshness_score", np.nan),
                "Freshness basis": r.get("freshness_basis", ""),
                "Catalyst status": r.get("catalyst_status", "NOT CONNECTED"),
                "Catalyst count": r.get("catalyst_count", 0),
                "Next catalyst": r.get("next_catalyst", ""),
                "Catalyst date": r.get("catalyst_date", ""),
                "Catalyst days": r.get("catalyst_days", np.nan),
                "Catalyst categories": r.get("catalyst_categories", ""),
                "Catalyst impact": r.get("catalyst_impact", ""),
                "Candle caution": "CAUTION" if r.get("candle_caution") else "CLEAR",
                "Last 4h candle": r.get("candle_pattern", "UNAVAILABLE"),
                "Candle detail": r.get("candle_detail", ""),
                "Pattern": r.get("triangle_label", "NO TRIANGLE"),
                "Triangle score": r.get("triangle_score", 0.0),
                "Triangle resistance": r.get("triangle_resistance", np.nan),
                "Triangle support": r.get("triangle_support_now", np.nan),
                "Triangle touches": r.get("triangle_touches", 0),
                "Triangle compression %": r.get("triangle_compression_pct", np.nan),
                "Triangle target": r.get("triangle_measured_target", np.nan),
                "Triangle detail": r.get("triangle_detail", ""),
                "SMA regime": r.get("sma_regime", "UNAVAILABLE"),
                "SMA50": r.get("sma50", np.nan),
                "SMA200": r.get("sma200", np.nan),
                "Price vs SMA50 %": r.get("price_vs_sma50_pct", np.nan),
                "Price vs SMA200 %": r.get("price_vs_sma200_pct", np.nan),
                "BB 4h regime": r.get("bb_4h_regime", "UNAVAILABLE"),
                "BB 4h width %": r.get("bb_4h_width_pct", np.nan),
                "BB 4h width percentile": r.get("bb_4h_width_percentile", np.nan),
                "BB 4h position %": r.get("bb_4h_position_pct", np.nan),
                "BB Daily regime": r.get("bb_daily_regime", "UNAVAILABLE"),
                "BB Daily width %": r.get("bb_daily_width_pct", np.nan),
                "BB Daily position %": r.get("bb_daily_position_pct", np.nan),
                "4h Channel": r.get("channel_4h_direction", "UNAVAILABLE"),
                "4h Channel pos %": r.get("channel_4h_position", np.nan),
                "4h Channel support": r.get("channel_4h_support", np.nan),
                "4h Channel resistance": r.get("channel_4h_resistance", np.nan),
                "4h Channel R:R": r.get("channel_4h_rr", np.nan),
                "4h Channel quality": r.get("channel_4h_quality", "LOW"),
                "4h Channel state": r.get("channel_4h_state", "NONE"),
                "Daily Channel": r.get("channel_daily_direction", "UNAVAILABLE"),
                "Daily Channel pos %": r.get("channel_daily_position", np.nan),
                "Price": r["price"],
                "Entry Price": (r["entry_low"] + r["entry_high"]) / 2,
                "Exit / Stop": r["invalidation"],
                "Price Target": r["projected_target"],
                "ROI %": r["target_upside_pct"],
                "To resistance %": r["distance_pct"],
                "Tests": r["resistance_tests"],
                "RSI": r["rsi"],
                "ATR ratio": r["atr_ratio"],
                "Vol ratio": r["volume_ratio"],
                "RS vs BTC %": r["rs_vs_btc_pct"],
                "RS vs BTC 96h %": r.get("rs_vs_btc_96h_pct", np.nan),
                "Return 30d %": r.get("return_30d_pct", np.nan),
                "Return 90d %": r.get("return_90d_pct", np.nan),
                "Return 180d %": r.get("return_180d_pct", np.nan),
                "RS vs BTC 30d %": r.get("rs_vs_btc_30d_pct", np.nan),
                "RS vs BTC 90d %": r.get("rs_vs_btc_90d_pct", np.nan),
                "RS vs BTC 180d %": r.get("rs_vs_btc_180d_pct", np.nan),
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

    if timeframe_label == "1d":
        full = df.copy()
        full["SMA50_chart"] = full["close"].rolling(50).mean()
        full["SMA200_chart"] = full["close"].rolling(200).mean()
        chart_ma = full.tail(max_bars)
        fig.add_trace(go.Scatter(
            x=chart_ma["timestamp"], y=chart_ma["SMA50_chart"],
            mode="lines", name="SMA50",
        ))
        fig.add_trace(go.Scatter(
            x=chart_ma["timestamp"], y=chart_ma["SMA200_chart"],
            mode="lines", name="SMA200",
        ))

    if timeframe_label == "4h":
        tri = ascending_triangle_pattern(d, 60)
        if tri.get("triangle_label") != "NO TRIANGLE":
            tri_bars = int(tri.get("triangle_window_bars", 0))
            if tri_bars > 1:
                tri_dates = d["timestamp"].iloc[-tri_bars:]
                support_line = np.linspace(
                    float(tri.get("triangle_support_start")),
                    float(tri.get("triangle_support_now")),
                    tri_bars,
                )
                fig.add_trace(go.Scatter(
                    x=tri_dates, y=support_line, mode="lines",
                    name="Triangle rising support", line=dict(dash="dash"),
                ))
                fig.add_hline(
                    y=float(tri.get("triangle_resistance")),
                    line_dash="dash",
                    annotation_text="Triangle resistance",
                )

    if timeframe_label in ("4h", "1d"):
        bb_close = pd.to_numeric(d["close"], errors="coerce")
        bb_mid = bb_close.rolling(20).mean()
        bb_sd = bb_close.rolling(20).std()
        bb_upper = bb_mid + 2 * bb_sd
        bb_lower = bb_mid - 2 * bb_sd
        fig.add_trace(go.Scatter(
            x=d["timestamp"], y=bb_upper, mode="lines",
            name="BB upper", line=dict(dash="dot"),
        ))
        fig.add_trace(go.Scatter(
            x=d["timestamp"], y=bb_mid, mode="lines",
            name="BB mid",
        ))
        fig.add_trace(go.Scatter(
            x=d["timestamp"], y=bb_lower, mode="lines",
            name="BB lower", line=dict(dash="dot"),
        ))

        channel_window = 80 if timeframe_label == "4h" else 90
        ch = trend_channel(d, channel_window)
        if ch.get("lower_series") and ch.get("upper_series"):
            start = int(ch.get("start", 0))
            channel_dates = d["timestamp"].iloc[start:]
            fig.add_trace(go.Scatter(
                x=channel_dates,
                y=ch["lower_series"],
                mode="lines",
                name="Channel support",
                line=dict(dash="dot"),
            ))
            fig.add_trace(go.Scatter(
                x=channel_dates,
                y=ch["upper_series"],
                mode="lines",
                name="Channel resistance",
                line=dict(dash="dot"),
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


def assess_ta_limitations(row: pd.Series, macro_now: Dict) -> Dict:
    """
    Keep technical setup quality separate from confidence that the setup is usable.
    This is an agreement/context overlay, not a probability forecast.
    """
    constructive: List[str] = []
    conflicts: List[str] = []
    neutral: List[str] = []

    if str(row.get("Candle caution", "CLEAR")) == "CAUTION":
        conflicts.append("bearish rejection candle")
    else:
        constructive.append("no bearish rejection candle")

    coin = str(row.get("Coin", "")).upper()
    rs = _safe_float(row.get("RS vs BTC %"), np.nan)
    if coin == "BTC":
        neutral.append("BTC relative-strength gate exempt")
    elif math.isfinite(rs) and rs > 0:
        constructive.append("beating BTC")
    elif math.isfinite(rs):
        conflicts.append("not beating BTC")

    coin_trend = str(row.get("Coin trend", "UNAVAILABLE")).upper()
    if coin_trend == "UPTREND":
        constructive.append("coin uptrend")
    elif coin_trend == "DOWNTREND":
        conflicts.append("coin downtrend")
    else:
        neutral.append("coin trend sideways/unclear")

    market_trend = str(row.get("Market trend", "UNAVAILABLE")).upper()
    if market_trend == "UPTREND":
        constructive.append("BTC market uptrend")
    elif market_trend == "DOWNTREND":
        conflicts.append("BTC market downtrend")
    else:
        neutral.append("BTC market sideways/unclear")

    sma_regime = str(row.get("SMA regime", "UNAVAILABLE")).upper()
    if sma_regime in ("BULLISH STACK", "GOLDEN CROSS — PULLBACK", "ABOVE 200D"):
        constructive.append("constructive 50/200-day structure")
    elif sma_regime == "BELOW 200D":
        conflicts.append("below 200-day SMA")
    else:
        neutral.append("50/200-day structure not fully confirmed")

    channel = str(row.get("4h Channel", "UNAVAILABLE")).upper()
    if channel == "RISING":
        constructive.append("rising 4h channel")
    elif channel == "FALLING":
        conflicts.append("falling 4h channel")
    else:
        neutral.append("sideways/unclear 4h channel")

    rsi_value = _safe_float(row.get("RSI"), np.nan)
    if math.isfinite(rsi_value):
        if 40 <= rsi_value <= 65:
            constructive.append("RSI constructive")
        elif rsi_value > 70:
            conflicts.append("RSI extended")
        elif rsi_value < 30:
            conflicts.append("RSI weak/oversold")
        else:
            neutral.append("RSI neutral")

    bb_regime = str(row.get("BB 4h regime", "UNAVAILABLE")).upper()
    bb_position = _safe_float(row.get("BB 4h position %"), np.nan)
    if bb_regime == "SQUEEZE":
        constructive.append("Bollinger squeeze")
    elif bb_regime == "EXPANDING" and math.isfinite(bb_position) and bb_position >= 95:
        conflicts.append("expanding bands near/above upper band")
    else:
        neutral.append("Bollinger state not conflicting")

    pattern = str(row.get("Pattern", "NO TRIANGLE")).upper()
    if "ASCENDING TRIANGLE — STRONG" in pattern:
        constructive.append("strong ascending triangle")
    elif "ASCENDING TRIANGLE — DEVELOPING" in pattern:
        constructive.append("developing ascending triangle")
    else:
        neutral.append("no confirmed ascending triangle")

    macro_regime = str(macro_now.get("regime", "DATA LIMITED")).upper()
    if macro_regime in ("EXPANSION", "IMPROVING"):
        constructive.append("supportive macro liquidity")
    elif macro_regime in ("DETERIORATING", "CONTRACTION"):
        conflicts.append("weak macro liquidity")
    else:
        neutral.append("macro liquidity mixed/data-limited")

    catalyst_status = str(row.get("Catalyst status", "NOT CONNECTED")).upper()
    catalyst_days = _safe_float(row.get("Catalyst days"), np.nan)
    if catalyst_status == "RISK EVENT":
        event_risk = "HIGH"
        conflicts.append("known token/unlock-style risk event")
    elif math.isfinite(catalyst_days) and catalyst_days <= 3:
        event_risk = "MEDIUM"
        neutral.append("near-dated catalyst can create event volatility")
    elif catalyst_status in ("NOT CONNECTED",) or catalyst_status.startswith("ERROR"):
        event_risk = "UNKNOWN"
    else:
        event_risk = "LOW"

    non_ta: List[str] = []
    if row.get("Tokenomics gate") == "PASS":
        non_ta.append("tokenomics")
    if row.get("Major CEX gate") == "PASS":
        non_ta.append("major-exchange breadth")
    if row.get("Category leader") == "TOP 3":
        non_ta.append("category leadership")
    if macro_regime in ("EXPANSION", "IMPROVING"):
        non_ta.append("macro liquidity")

    directional = len(constructive) + len(conflicts)
    agreement = (
        len(constructive) / directional * 100
        if directional > 0 else 50.0
    )

    if event_risk == "HIGH" or len(conflicts) >= 3:
        confidence = "LOW"
    elif event_risk == "MEDIUM" or len(conflicts) >= 1 or agreement < 75:
        confidence = "MEDIUM"
    else:
        confidence = "HIGH"

    news_coverage = (
        "STRUCTURED EVENTS ONLY"
        if catalyst_status not in ("NOT CONNECTED",) and not catalyst_status.startswith("ERROR")
        else "LIMITED"
    )

    return {
        "Context confidence": confidence,
        "Signal agreement %": round(float(agreement), 1),
        "Constructive signals": len(constructive),
        "Conflict count": len(conflicts),
        "Conflicts": "; ".join(conflicts),
        "Known event risk": event_risk,
        "Non-TA confirmations": len(non_ta),
        "Non-TA detail": "; ".join(non_ta),
        "News coverage": news_coverage,
        "Sentiment coverage": "PROXY ONLY",
        "TA limitation note": (
            "TA is backward-looking; unexpected news cannot be predicted. "
            "Use the defined invalidation/stop if the setup fails."
        ),
    }


def apply_ta_context_overlay(df: pd.DataFrame, macro_now: Dict) -> pd.DataFrame:
    if df.empty:
        return df
    overlay = df.apply(
        lambda row: pd.Series(assess_ta_limitations(row, macro_now)),
        axis=1,
    )
    return pd.concat([df.reset_index(drop=True), overlay.reset_index(drop=True)], axis=1)


def load_crypto_watchlist() -> List[str]:
    try:
        raw = st.query_params.get("cwl", "")
    except Exception:
        raw = ""
    if isinstance(raw, list):
        raw = raw[-1] if raw else ""
    return [
        item.strip().upper()
        for item in str(raw or "").split(",")
        if item.strip()
    ][:40]


def save_crypto_watchlist(symbols: List[str]) -> None:
    clean = []
    for symbol in symbols[:40]:
        value = str(symbol or "").strip().upper()
        if value and value not in clean:
            clean.append(value)
    try:
        if clean:
            st.query_params["cwl"] = ",".join(clean)
        elif "cwl" in st.query_params:
            del st.query_params["cwl"]
    except Exception:
        pass


def set_crypto_watchlist_symbol(symbol: str, enabled: bool) -> None:
    ticker = str(symbol or "").strip().upper()
    current = load_crypto_watchlist()
    current_set = set(current)
    if enabled:
        current_set.add(ticker)
    else:
        current_set.discard(ticker)
    ordered = [item for item in current if item in current_set]
    if enabled and ticker not in ordered:
        ordered.append(ticker)
    save_crypto_watchlist(ordered)


def crypto_trade_decision(result: Dict, macro_now: Dict) -> Dict:
    if not result or "score" not in result:
        return {"action": "UNAVAILABLE", "reason": "Not enough market data to score this coin."}

    symbol = str(result.get("symbol") or result.get("Symbol") or "")
    base = symbol.split("/")[0].upper()
    overlay = assess_ta_limitations(
        pd.Series({
            "Coin": base,
            "Candle caution": "CAUTION" if result.get("candle_caution") else "CLEAR",
            "RS vs BTC %": result.get("rs_vs_btc_pct", np.nan),
            "Coin trend": result.get("coin_trend", "UNAVAILABLE"),
            "Market trend": result.get("market_trend", "UNAVAILABLE"),
            "SMA regime": result.get("sma_regime", "UNAVAILABLE"),
            "4h Channel": result.get("channel_4h_direction", "UNAVAILABLE"),
            "RSI": result.get("rsi", np.nan),
            "BB 4h regime": result.get("bb_4h_regime", "UNAVAILABLE"),
            "BB 4h position %": result.get("bb_4h_position_pct", np.nan),
            "Pattern": result.get("triangle_label", "NO TRIANGLE"),
            "Catalyst status": result.get("catalyst_status", "NOT CONNECTED"),
            "Catalyst days": result.get("catalyst_days", np.nan),
            "Tokenomics gate": result.get("tokenomics_gate", "UNKNOWN"),
            "Major CEX gate": result.get("major_cex_gate", "UNKNOWN"),
            "Category leader": result.get("category_leader", "UNKNOWN"),
        }),
        macro_now,
    )
    rs_pass = base == "BTC" or _safe_float(result.get("rs_vs_btc_pct"), -999) > 0
    if (
        result.get("eligible")
        and rs_pass
        and result.get("tokenomics_gate") == "PASS"
        and result.get("major_cex_gate") == "PASS"
        and not result.get("candle_caution")
        and overlay.get("Context confidence") != "LOW"
        and overlay.get("Known event risk") != "HIGH"
        and macro_now.get("allows_new_swing_risk", True)
    ):
        return {
            "action": "BUY",
            "reason": "The pre-breakout setup, relative strength, tokenomics, exchange breadth, context and macro gates currently pass.",
        }

    if result.get("eligible"):
        reasons = []
        if not rs_pass:
            reasons.append("not beating BTC")
        if result.get("tokenomics_gate") != "PASS":
            reasons.append("tokenomics gate not passed")
        if result.get("major_cex_gate") != "PASS":
            reasons.append("major-exchange breadth not passed")
        if result.get("candle_caution"):
            reasons.append("4h candle rejection caution")
        if overlay.get("Context confidence") == "LOW" or overlay.get("Known event risk") == "HIGH":
            reasons.append("context/event-risk gate")
        if not macro_now.get("allows_new_swing_risk", True):
            reasons.append("macro liquidity")
        return {
            "action": "WAIT",
            "reason": "The technical setup is developing, but " + ", ".join(reasons or ["one or more confirmation gates"]) + " still needs to improve.",
        }

    return {
        "action": "PASS",
        "reason": result.get("reason", "The current pre-breakout shape does not meet the approved setup rules."),
    }


def render_crypto_decision_card(title: str, action: str, reason: str) -> None:
    action_upper = str(action or "UNAVAILABLE").upper()
    if action_upper in {"BUY", "ACCUMULATE"}:
        border, background, text_colour, icon = "#2e7d32", "#eef8f0", "#1b5e20", "🟢"
    elif action_upper in {"WAIT", "WATCH"}:
        border, background, text_colour, icon = "#d48a00", "#fff8e1", "#7a4d00", "🟠"
    elif action_upper == "PASS":
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
            min-height: 180px;
            margin-bottom: 8px;
        ">
            <div style="font-size:0.95rem;font-weight:700;opacity:.78;margin-bottom:4px;">{title}</div>
            <div style="font-size:2.4rem;line-height:1.05;font-weight:900;color:{text_colour};margin:6px 0 12px 0;">
                {icon} {action_upper}
            </div>
            <div style="font-size:1.02rem;line-height:1.45;color:#313131;">{reason}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    render_decision_guidance(action_upper, reason)


def crypto_dashboard_summary(frame: pd.DataFrame, cfg: ScreenerConfig, macro_now: Dict) -> Dict:
    summary = {
        "scanned": 0,
        "swing_buy": 0,
        "accumulation": 0,
        "best_score": np.nan,
        "btc_trend": "UNAVAILABLE",
    }
    if frame is None or frame.empty:
        return summary
    try:
        df = apply_ta_context_overlay(frame.copy(), macro_now)
        summary["scanned"] = len(df)
        summary["best_score"] = pd.to_numeric(df.get("Score"), errors="coerce").max()
        if "Market trend" in df.columns and not df["Market trend"].dropna().empty:
            summary["btc_trend"] = str(df["Market trend"].dropna().iloc[0])

        technical = df[
            (df["Trade verdict"] == "QUALIFIES — 30%+ GROSS TARGET")
            & (pd.to_numeric(df["Score"], errors="coerce") >= cfg.score_threshold)
        ].copy()
        if not technical.empty:
            rs_pass = (technical["Coin"] == "BTC") | (pd.to_numeric(technical["RS vs BTC %"], errors="coerce") > 0)
            final = technical[
                rs_pass
                & technical["Tokenomics gate"].eq("PASS")
                & technical["Major CEX gate"].eq("PASS")
                & technical["Candle caution"].ne("CAUTION")
                & technical["Context confidence"].ne("LOW")
                & technical["Known event risk"].ne("HIGH")
            ]
            if macro_now.get("allows_new_swing_risk", True):
                summary["swing_buy"] = len(final)

        summary["accumulation"] = int(
            (df["Accumulation verdict"] == "ACCUMULATION READY").sum()
        )
    except Exception:
        pass
    return summary


# ---------------- UI ----------------
render_module_header(
    "Crypto",
    "⚡",
    "Find pre-breakout swing entries and accumulation setups without digging through all the market data yourself.",
)

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
    cmcal_connected = bool(_streamlit_secret("COINMARKETCAL_API_KEY"))
    x_connected = bool(_streamlit_secret("X_BEARER_TOKEN"))
    st.caption(
        "Catalyst feeds: "
        + ("CoinMarketCal connected" if cmcal_connected else "CoinMarketCal not connected")
        + " · "
        + ("X official-post search connected" if x_connected else "X search not connected")
    )
    st.caption(
        "Exchange market data remains public/no-key. Optional catalyst credentials "
        "must be stored in Streamlit Secrets, never in GitHub."
    )

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

tab_crypto_home, tab_crypto_quick, tab_crypto_opportunities, tab_crypto_watchlist, tab_crypto_advanced = st.tabs(
    ["Home", "Quick Analysis", "Opportunities", "Watchlist", "Advanced Crypto"]
)

with tab_crypto_home:
    st.markdown("### Your crypto dashboard")
    st.caption("Start with a coin, see what the screener is finding, or track the coins you want to revisit.")

    crypto_summary = crypto_dashboard_summary(st.session_state.scan_df, cfg, macro)
    crypto_watchlist = load_crypto_watchlist()

    scan_loaded = not st.session_state.scan_df.empty
    d1, d2, d3, d4, d5, d6 = st.columns(6)
    d1.metric("Swing BUY", crypto_summary["swing_buy"] if scan_loaded else "—")
    d2.metric("Accumulation", crypto_summary["accumulation"] if scan_loaded else "—")
    d3.metric(
        "Best swing score",
        (
            "—"
            if not scan_loaded or not math.isfinite(_safe_float(crypto_summary["best_score"]))
            else f"{crypto_summary['best_score']:.1f}/100"
        ),
    )
    d4.metric("BTC trend", crypto_summary["btc_trend"] if scan_loaded else "—")
    d5.metric("Macro regime", macro.get("regime", "DATA LIMITED"))
    d6.metric("Watchlist", len(crypto_watchlist))

    if st.session_state.last_scan is not None:
        scan_time = st.session_state.last_scan.astimezone().strftime("%d %b %Y %H:%M:%S %Z")
        selected = int(st.session_state.scan_df.attrs.get("markets_selected", len(st.session_state.scan_df)))
        completed = int(st.session_state.scan_df.attrs.get("markets_completed", len(st.session_state.scan_df)))
        st.caption(
            f"Crypto market scan: {scan_time} · {len(st.session_state.scan_df)} scored · "
            f"{completed}/{selected} markets completed"
        )
    else:
        st.info(
            "No market-wide Crypto scan is loaded in this session yet. "
            "The dashes above mean **not scanned**, not zero opportunities."
        )

    home_a, home_b, home_c = st.columns(3)
    with home_a:
        with st.container(border=True):
            st.markdown("#### 🔎 Analyse a coin")
            st.write("Search by coin name or ticker and get the current swing and accumulation decision first.")
            crypto_home_query = st.text_input(
                "Coin",
                value="",
                placeholder="e.g. Solana, SOL, SOL/USDT",
                key="crypto_home_query",
                label_visibility="collapsed",
            )
            crypto_home_analyse = st.button(
                "Analyse coin",
                type="primary",
                use_container_width=True,
                key="crypto_home_analyse",
            )

    with home_b:
        with st.container(border=True):
            st.markdown("#### 🎯 Find opportunities")
            if crypto_summary["swing_buy"]:
                st.success(
                    f"{crypto_summary['swing_buy']} swing BUY setup"
                    f"{'s' if crypto_summary['swing_buy'] != 1 else ''}"
                )
            else:
                st.info("No swing BUY setup is ready right now.")
            if crypto_summary["accumulation"]:
                st.success(
                    f"{crypto_summary['accumulation']} accumulation setup"
                    f"{'s' if crypto_summary['accumulation'] != 1 else ''}"
                )
            st.caption("Open **Advanced Crypto** to run or refresh the market scan.")

    with home_c:
        with st.container(border=True):
            st.markdown("#### ⭐ My watchlist")
            if crypto_watchlist:
                st.write(
                    f"You are following **{len(crypto_watchlist)}** coin"
                    f"{'s' if len(crypto_watchlist) != 1 else ''}."
                )
                st.caption(", ".join(crypto_watchlist[:6]) + ("…" if len(crypto_watchlist) > 6 else ""))
            else:
                st.write("Your crypto watchlist is empty.")
                st.caption("Use **Quick Analysis** and tick **Watch** to start tracking a coin.")
            st.caption("Open **Watchlist** above to manage the coins you are following.")

    if crypto_home_analyse and crypto_home_query:
        with st.spinner(f"Analysing {crypto_home_query.strip()}…"):
            try:
                home_symbol, home_result, _home_raw = asyncio.run(
                    analyse_individual_coin(cfg, crypto_home_query)
                )
                home_result = dict(home_result or {})
                home_result["symbol"] = home_symbol
            except Exception as exc:
                home_symbol, home_result = "", {}
                st.error(f"{type(exc).__name__}: {exc}")

        if home_symbol and home_result:
            st.markdown("---")
            title_col, watch_col = st.columns([5, 1])
            with title_col:
                st.markdown(f"### {home_symbol.split('/')[0]} on {exchange_name}")
                if "price" in home_result:
                    st.caption(f"Current price: {fmt_price(home_result['price'])}")
            with watch_col:
                watched_now = home_symbol.split("/")[0].upper() in set(crypto_watchlist)
                watch_now = st.checkbox(
                    "Watch",
                    value=watched_now,
                    key=f"crypto_home_watch_{home_symbol.split('/')[0]}",
                )
                if watch_now != watched_now:
                    set_crypto_watchlist_symbol(home_symbol.split("/")[0], watch_now)
                    st.toast("Added to crypto watchlist" if watch_now else "Removed from crypto watchlist")

            trade_decision = crypto_trade_decision(home_result, macro)
            accumulation_verdict = str(
                home_result.get("accumulation_verdict", "NOT READY TO ACCUMULATE")
            )
            if accumulation_verdict == "ACCUMULATION READY":
                accumulation_action = "ACCUMULATE"
                accumulation_reason = "The confirmed daily base and accumulation score currently meet the model rules."
            elif accumulation_verdict.startswith("WATCH"):
                accumulation_action = "WAIT"
                accumulation_reason = "The longer-term base is developing but is not ready yet."
            else:
                accumulation_action = "PASS"
                accumulation_reason = "The current daily base does not meet the accumulation rules."

            dc1, dc2 = st.columns(2)
            with dc1:
                render_crypto_decision_card(
                    "SWING DECISION",
                    trade_decision["action"],
                    trade_decision["reason"],
                )
            with dc2:
                render_crypto_decision_card(
                    "ACCUMULATION DECISION",
                    accumulation_action,
                    accumulation_reason,
                )

            if "score" in home_result:
                hm1, hm2, hm3, hm4 = st.columns(4)
                hm1.metric("Swing score", f"{home_result.get('score', 0):.1f}/100")
                hm2.metric("RS vs BTC", f"{home_result.get('rs_vs_btc_pct', np.nan):+.2f}%")
                hm3.metric("RSI", f"{home_result.get('rsi', np.nan):.1f}")
                hm4.metric(
                    "Potential ROI",
                    "—" if not math.isfinite(_safe_float(home_result.get("target_upside_pct"))) else f"{home_result.get('target_upside_pct'):.1f}%",
                )
            st.caption("Open **Quick Analysis** for the full single-coin view, or **Advanced Crypto** for the complete screener.")

    st.markdown("### How it works")
    hw1, hw2, hw3 = st.columns(3)
    with hw1:
        st.markdown("**1 · Search a coin**")
        st.caption("Use a coin name or ticker. The screener resolves the active market for you.")
    with hw2:
        st.markdown("**2 · See the decision**")
        st.caption("Swing and accumulation decisions come first; the detailed evidence remains available below.")
    with hw3:
        st.markdown("**3 · Watch what matters**")
        st.caption("If the setup is not ready, add it to your crypto watchlist rather than chasing the price.")

with tab_crypto_opportunities:
    st.markdown("### Opportunities")
    st.caption(
        "A cleaner view of the latest Crypto scan. Run a fresh scan from **Advanced Crypto** "
        "when you want to update the market data."
    )
    if st.session_state.last_scan is not None and not st.session_state.scan_df.empty:
        opp_scan_time = st.session_state.last_scan.astimezone().strftime("%d %b %Y %H:%M:%S %Z")
        opp_selected = int(st.session_state.scan_df.attrs.get("markets_selected", len(st.session_state.scan_df)))
        opp_completed = int(st.session_state.scan_df.attrs.get("markets_completed", len(st.session_state.scan_df)))
        st.caption(
            f"Last market-wide scan: {opp_scan_time} · "
            f"{opp_completed}/{opp_selected} selected markets completed"
        )

    opportunity_scan = st.session_state.scan_df.copy()
    if opportunity_scan.empty:
        st.info(
            "No Crypto scan results are loaded yet. Open **Advanced Crypto** and run a scan "
            "to populate this page."
        )
    else:
        opportunity_scan = apply_ta_context_overlay(opportunity_scan, macro)

        technical_opportunities = opportunity_scan[
            (opportunity_scan["Trade verdict"] == "QUALIFIES — 30%+ GROSS TARGET")
            & (pd.to_numeric(opportunity_scan["Score"], errors="coerce") >= cfg.score_threshold)
        ].copy()

        if not technical_opportunities.empty:
            swing_buy_mask = (
                ((technical_opportunities["Coin"] == "BTC")
                 | (pd.to_numeric(technical_opportunities["RS vs BTC %"], errors="coerce") > 0))
                & technical_opportunities["Tokenomics gate"].eq("PASS")
                & technical_opportunities["Major CEX gate"].eq("PASS")
                & technical_opportunities["Candle caution"].ne("CAUTION")
                & technical_opportunities["Context confidence"].ne("LOW")
                & technical_opportunities["Known event risk"].ne("HIGH")
            )
            if not macro.get("allows_new_swing_risk", True):
                swing_buy_mask = swing_buy_mask & False
            technical_opportunities["Status"] = np.where(swing_buy_mask, "BUY", "WAIT")
            technical_opportunities = technical_opportunities.sort_values(
                ["Status", "Score"], ascending=[True, False]
            )

        accumulation_opportunities = opportunity_scan[
            opportunity_scan["Accumulation verdict"] == "ACCUMULATION READY"
        ].copy().sort_values("Accumulation score", ascending=False)

        om1, om2, om3 = st.columns(3)
        om1.metric(
            "Swing BUY",
            int((technical_opportunities["Status"] == "BUY").sum())
            if not technical_opportunities.empty else 0,
        )
        om2.metric(
            "Swing WAIT",
            int((technical_opportunities["Status"] == "WAIT").sum())
            if not technical_opportunities.empty else 0,
        )
        om3.metric("Accumulation ready", len(accumulation_opportunities))

        swing_view, accumulation_view = st.tabs(["Swing opportunities", "Accumulation"])

        with swing_view:
            if technical_opportunities.empty:
                st.info("No swing setup currently reaches the technical opportunity threshold.")
            else:
                swing_cols = [
                    "Status", "Coin", "Score", "Price", "RS vs BTC %",
                    "Trade verdict", "Trade reason", "Tokenomics gate",
                    "Major CEX gate", "Context confidence", "Known event risk",
                    "Distance %", "Target upside %",
                ]
                visible_swing_cols = [
                    col for col in swing_cols if col in technical_opportunities.columns
                ]
                st.dataframe(
                    technical_opportunities[visible_swing_cols],
                    hide_index=True,
                    use_container_width=True,
                )

        with accumulation_view:
            if accumulation_opportunities.empty:
                st.info("No accumulation setup is currently marked ACCUMULATION READY.")
            else:
                accumulation_cols = [
                    "Coin", "Accumulation score", "Price", "Accumulation low",
                    "Accumulation high", "Coin trend", "Tokenomics gate",
                    "Project freshness",
                ]
                visible_accumulation_cols = [
                    col for col in accumulation_cols if col in accumulation_opportunities.columns
                ]
                st.dataframe(
                    accumulation_opportunities[visible_accumulation_cols],
                    hide_index=True,
                    use_container_width=True,
                )


with tab_crypto_watchlist:
    st.markdown("### Watchlist")
    st.caption(
        "Keep interesting coins here while you wait for the setup to improve. "
        "Removing a coin does not change any screener result."
    )

    managed_crypto_watchlist = load_crypto_watchlist()
    if not managed_crypto_watchlist:
        st.info(
            "Your Crypto watchlist is empty. Use **Watch** after analysing a coin to add it here."
        )
    else:
        wm1, wm2 = st.columns([1, 3])
        wm1.metric("Coins watched", len(managed_crypto_watchlist))
        with wm2:
            st.caption(
                "Use Quick Analysis to re-check a watched coin against the latest market conditions."
            )

        for crypto_watch_index, crypto_watch_symbol in enumerate(managed_crypto_watchlist):
            with st.container(border=True):
                watch_name_col, watch_remove_col = st.columns(
                    [5, 1], vertical_alignment="center"
                )
                with watch_name_col:
                    st.markdown(f"#### {crypto_watch_symbol}")
                    st.caption("Saved for review")
                with watch_remove_col:
                    if st.button(
                        "Remove",
                        key=f"remove_crypto_watch_{crypto_watch_symbol}_{crypto_watch_index}",
                        use_container_width=True,
                    ):
                        set_crypto_watchlist_symbol(crypto_watch_symbol, False)
                        st.rerun()


with tab_crypto_quick:
    st.markdown("### Quick Analysis")
    st.caption("Search any active coin on the selected exchange. The decision comes first; detailed evidence follows underneath.")

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
        run_quick_analysis = st.button("Analyse coin", type="primary", use_container_width=True)

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
            qa_overlay = assess_ta_limitations(
                pd.Series({
                    "Coin": qa_symbol.split("/")[0].upper(),
                    "Candle caution": "CAUTION" if qa_result.get("candle_caution") else "CLEAR",
                    "RS vs BTC %": qa_result.get("rs_vs_btc_pct", np.nan),
                    "Coin trend": qa_result.get("coin_trend", "UNAVAILABLE"),
                    "Market trend": qa_result.get("market_trend", "UNAVAILABLE"),
                    "SMA regime": qa_result.get("sma_regime", "UNAVAILABLE"),
                    "4h Channel": qa_result.get("channel_4h_direction", "UNAVAILABLE"),
                    "RSI": qa_result.get("rsi", np.nan),
                    "BB 4h regime": qa_result.get("bb_4h_regime", "UNAVAILABLE"),
                    "BB 4h position %": qa_result.get("bb_4h_position_pct", np.nan),
                    "Pattern": qa_result.get("triangle_label", "NO TRIANGLE"),
                    "Catalyst status": qa_result.get("catalyst_status", "NOT CONNECTED"),
                    "Catalyst days": qa_result.get("catalyst_days", np.nan),
                    "Tokenomics gate": qa_result.get("tokenomics_gate", "UNKNOWN"),
                    "Major CEX gate": qa_result.get("major_cex_gate", "UNKNOWN"),
                    "Category leader": qa_result.get("category_leader", "UNKNOWN"),
                }),
                macro_now,
            )
            trade_decision = crypto_trade_decision(qa_result, macro_now)
            accumulation_verdict = str(
                qa_result.get("accumulation_verdict", "NOT READY TO ACCUMULATE")
            )
            if accumulation_verdict == "ACCUMULATION READY":
                accumulation_action = "ACCUMULATE"
                accumulation_reason = (
                    "The confirmed daily base and accumulation score currently meet the model rules."
                )
            elif accumulation_verdict.startswith("WATCH"):
                accumulation_action = "WAIT"
                accumulation_reason = (
                    "The longer-term base is developing but is not ready yet."
                )
            else:
                accumulation_action = "PASS"
                accumulation_reason = (
                    "The current daily base does not meet the accumulation rules."
                )

            decision_left, decision_right = st.columns(2)
            with decision_left:
                render_crypto_decision_card(
                    "SWING DECISION",
                    trade_decision["action"],
                    trade_decision["reason"],
                )
            with decision_right:
                render_crypto_decision_card(
                    "ACCUMULATION DECISION",
                    accumulation_action,
                    accumulation_reason,
                )

            st.caption(
                "Decision first. The metrics and detailed technical evidence below explain why."
            )

            q1, q2, q3, q4 = st.columns(4)
            q1.metric("Trade setup score", f"{qa_result['score']:.1f}/100")
            q2.metric("Price", fmt_price(qa_result["price"]))
            q3.metric("To resistance", f"{qa_result['distance_pct']:.2f}%")
            q4.metric("RSI", f"{qa_result['rsi']:.1f}")

            cf1, cf2, cf3, cf4 = st.columns(4)
            cf1.metric("Context confidence", qa_overlay["Context confidence"])
            cf2.metric("Signal agreement", f"{qa_overlay['Signal agreement %']:.1f}%")
            cf3.metric("Known event risk", qa_overlay["Known event risk"])
            cf4.metric("Non-TA confirmations", qa_overlay["Non-TA confirmations"])
            if qa_overlay["Conflicts"]:
                st.caption("Conflicting signals: " + qa_overlay["Conflicts"])
            st.caption(
                f"News coverage: {qa_overlay['News coverage']} · "
                f"Sentiment coverage: {qa_overlay['Sentiment coverage']} · "
                "Unexpected news cannot be predicted by technical analysis."
            )

            cd1, cd2 = st.columns(2)
            cd1.metric(
                "Latest completed 4h candle",
                qa_result.get("candle_pattern", "UNAVAILABLE"),
            )
            cd2.metric(
                "Candle caution",
                "CAUTION" if qa_result.get("candle_caution") else "CLEAR",
            )
            st.caption(qa_result.get("candle_detail", ""))

            pt1, pt2, pt3, pt4 = st.columns(4)
            pt1.metric("Pattern", qa_result.get("triangle_label", "NO TRIANGLE"))
            pt2.metric("Triangle quality", f"{qa_result.get('triangle_score', 0):.1f}/100")
            pt3.metric("Resistance touches", int(qa_result.get("triangle_touches", 0)))
            pt4.metric(
                "Triangle compression",
                f"{qa_result.get('triangle_compression_pct', np.nan):.1f}%"
                if pd.notna(qa_result.get("triangle_compression_pct", np.nan))
                else "Unavailable",
            )
            if qa_result.get("triangle_detail"):
                st.caption(qa_result.get("triangle_detail"))

            ma1, ma2, ma3 = st.columns(3)
            ma1.metric("Daily SMA regime", qa_result.get("sma_regime", "UNAVAILABLE"))
            ma2.metric(
                "SMA50",
                fmt_optional_price(qa_result.get("sma50", np.nan), "Unavailable"),
                f"{qa_result.get('price_vs_sma50_pct', np.nan):+.2f}%"
                if pd.notna(qa_result.get("price_vs_sma50_pct", np.nan)) else None,
            )
            ma3.metric(
                "SMA200",
                fmt_optional_price(qa_result.get("sma200", np.nan), "Unavailable"),
                f"{qa_result.get('price_vs_sma200_pct', np.nan):+.2f}%"
                if pd.notna(qa_result.get("price_vs_sma200_pct", np.nan)) else None,
            )

            bb1, bb2, bb3, bb4 = st.columns(4)
            bb1.metric("4h Bollinger", qa_result.get("bb_4h_regime", "UNAVAILABLE"))
            bb2.metric(
                "Band width",
                f"{qa_result.get('bb_4h_width_pct', np.nan):.2f}%"
                if pd.notna(qa_result.get("bb_4h_width_pct", np.nan)) else "Unavailable",
            )
            bb3.metric(
                "Width percentile",
                f"{qa_result.get('bb_4h_width_percentile', np.nan):.1f}%"
                if pd.notna(qa_result.get("bb_4h_width_percentile", np.nan)) else "Unavailable",
            )
            bb4.metric(
                "Price in bands",
                f"{qa_result.get('bb_4h_position_pct', np.nan):.1f}%"
                if pd.notna(qa_result.get("bb_4h_position_pct", np.nan)) else "Unavailable",
            )
            st.caption(
                "Low Bollinger-width percentile = volatility compression/squeeze; "
                "0% is the lower band and 100% is the upper band."
            )

            ch1, ch2, ch3, ch4 = st.columns(4)
            ch1.metric("4h channel", qa_result.get("channel_4h_direction", "UNAVAILABLE"))
            ch2.metric("4h channel position", str(qa_result.get("channel_4h_position", "Unavailable")))
            ch3.metric("4h channel quality", qa_result.get("channel_4h_quality", "LOW"))
            ch4.metric("4h channel R:R", str(qa_result.get("channel_4h_rr", "Unavailable")))
            dch1, dch2 = st.columns(2)
            dch1.metric("Daily channel", qa_result.get("channel_daily_direction", "UNAVAILABLE"))
            dch2.metric("Daily channel position", str(qa_result.get("channel_daily_position", "Unavailable")))

            rs1, rs2 = st.columns(2)
            rs1.metric("RS vs BTC — 48h", f"{qa_result.get('rs_vs_btc_pct', np.nan):+.2f}%")
            rs2.metric("RS vs BTC — 96h", f"{qa_result.get('rs_vs_btc_96h_pct', np.nan):+.2f}%")

            lrs1, lrs2, lrs3 = st.columns(3)
            lrs1.metric("RS vs BTC — 30d", f"{qa_result.get('rs_vs_btc_30d_pct', np.nan):+.2f}%")
            lrs2.metric("RS vs BTC — 90d", f"{qa_result.get('rs_vs_btc_90d_pct', np.nan):+.2f}%")
            lrs3.metric("RS vs BTC — 180d", f"{qa_result.get('rs_vs_btc_180d_pct', np.nan):+.2f}%")

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

            ex1, ex2, ex3 = st.columns(3)
            ex1.metric("Major CEX quality", qa_result.get("major_cex_quality", "DATA LIMITED"))
            ex2.metric("Major CEX count", int(qa_result.get("major_cex_count", 0)))
            ex3.metric("Major CEX gate", qa_result.get("major_cex_gate", "UNKNOWN"))
            st.caption(
                "Verified major CEX listings: "
                + (qa_result.get("major_cex_list") or "None / unavailable")
            )

            lead1, lead2 = st.columns(2)
            lead1.metric("Category leadership", qa_result.get("category_leader", "UNKNOWN"))
            lead2.metric(
                "Leader categories",
                qa_result.get("leader_categories") or "None identified",
            )

            fresh1, fresh2, fresh3 = st.columns(3)
            fresh1.metric("Project freshness", qa_result.get("project_freshness", "UNKNOWN"))
            history_days = qa_result.get("history_days", np.nan)
            fresh2.metric(
                "Exchange history",
                f"{history_days:.0f} days" if pd.notna(history_days) else "Unavailable",
            )
            fresh3.metric(
                "Freshness score",
                f"{qa_result.get('freshness_score', np.nan):.0f}/100"
                if pd.notna(qa_result.get("freshness_score", np.nan))
                else "Unavailable",
            )
            st.caption(
                "Freshness is an exchange-history proxy, not the project's exact launch age."
            )

            cat1, cat2, cat3 = st.columns(3)
            cat1.metric("Catalyst radar", qa_result.get("catalyst_status", "NOT CONNECTED"))
            cat2.metric(
                "Next catalyst",
                qa_result.get("next_catalyst") or "None in available window",
            )
            cat3.metric(
                "Catalyst date",
                qa_result.get("catalyst_date") or "Unavailable",
            )
            if qa_result.get("catalyst_categories") or qa_result.get("catalyst_impact"):
                st.caption(
                    f"CoinMarketCal category: {qa_result.get('catalyst_categories') or 'Unavailable'} · "
                    f"Impact: {qa_result.get('catalyst_impact') or 'Unavailable on current plan'}"
                )

            coin_id = qa_result.get("coingecko_id", "")
            project_links = coingecko_project_links(coin_id)
            official_x = project_links.get("twitter", "")
            with st.expander("Information advantage — official sources"):
                link1, link2, link3 = st.columns(3)
                link1.link_button("CoinMarketCap Events", "https://coinmarketcap.com/events/")
                link2.link_button("CoinMarketCal", "https://coinmarketcal.com/")
                if official_x:
                    link3.link_button("Official X", f"https://x.com/{official_x}")
                else:
                    link3.caption("Official X unavailable")

                if official_x:
                    st.write(f"Official X: **@{official_x}**")
                if project_links.get("homepage"):
                    st.write("Official website:", project_links["homepage"])
                if project_links.get("github"):
                    st.write("GitHub:", project_links["github"])
                if project_links.get("official_forum"):
                    st.write("Official forum:", project_links["official_forum"])

                x_token = _streamlit_secret("X_BEARER_TOKEN")
                x_posts, x_status = x_official_catalyst_posts(official_x, x_token)
                st.caption(f"Official-X catalyst search: {x_status}")
                if x_posts:
                    post_rows = []
                    for post in x_posts[:5]:
                        metrics = post.get("public_metrics") or {}
                        post_rows.append({
                            "Created": post.get("created_at", ""),
                            "Post": post.get("text", ""),
                            "Likes": metrics.get("like_count", 0),
                            "Reposts": metrics.get("retweet_count", 0),
                        })
                    st.dataframe(
                        pd.DataFrame(post_rows),
                        hide_index=True,
                        use_container_width=True,
                    )
                elif official_x and not x_token:
                    st.info(
                        "Add X_BEARER_TOKEN to Streamlit Secrets to search the official "
                        "project account's last 7 days for planned announcements."
                    )
                if qa_result.get("catalyst_status") == "NOT CONNECTED":
                    st.info(
                        "Add COINMARKETCAL_API_KEY to Streamlit Secrets to activate the "
                        "structured upcoming-event feed."
                    )

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

with tab_crypto_advanced:
    st.markdown("### Advanced Crypto")
    st.caption("The full pre-breakout engine, macro analysis, scan controls, charts and research detail live here.")

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
        required_scan_columns = {
            "Trade reason", "Coin trend", "Market trend", "Tokenomics gate",
            "Circulating %", "RS vs BTC 96h %", "Major CEX gate", "Major CEX count",
            "Category leader", "Leader categories", "Project freshness", "Catalyst status",
            "Candle caution", "Last 4h candle", "Pattern", "Triangle score",
            "SMA regime", "SMA50", "SMA200", "BB 4h regime", "BB 4h width %",
            "4h Channel", "4h Channel pos %",
            "RS vs BTC 30d %", "RS vs BTC 90d %", "RS vs BTC 180d %",
        }
        needs_candidate_refresh = (
            not st.session_state.scan_df.empty
            and not required_scan_columns.issubset(set(st.session_state.scan_df.columns))
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

        macro_now = st.session_state.get("macro_liquidity") or {}
        df = apply_ta_context_overlay(df, macro_now)

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
        cex_qualified_setups = tokenomics_qualified_setups[
            tokenomics_qualified_setups["Major CEX gate"] == "PASS"
        ].copy()
        candle_qualified_setups = cex_qualified_setups[
            cex_qualified_setups["Candle caution"] != "CAUTION"
        ].copy()
        context_qualified_setups = candle_qualified_setups[
            (candle_qualified_setups["Context confidence"] != "LOW")
            & (candle_qualified_setups["Known event risk"] != "HIGH")
        ].copy()
        macro_allows_new_risk = bool(macro_now.get("allows_new_swing_risk", True))
        swing_setups = (
            context_qualified_setups
            if macro_allows_new_risk
            else context_qualified_setups.iloc[0:0].copy()
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
                    + (
                        (
                            f"Major-exchange breadth is {row.get('Major CEX quality', 'DATA LIMITED')} "
                            f"({int(row.get('Major CEX count', 0))} major CEX listing(s)); "
                            "at least 2 verified major CEX listings are required for BUY. "
                        )
                        if (
                            row["Symbol"] in set(tokenomics_qualified_setups["Symbol"])
                            and row.get("Major CEX gate") != "PASS"
                        )
                        else ""
                    )
                    + (
                        (
                            "Latest completed 4h candle is a red shooting star near the setup zone; "
                            "buyers were rejected higher up, so wait for confirmation. "
                        )
                        if (
                            row["Symbol"] in set(cex_qualified_setups["Symbol"])
                            and row.get("Candle caution") == "CAUTION"
                        )
                        else ""
                    )
                    + (
                        (
                            f"TA limitation overlay is {row.get('Context confidence', 'MEDIUM')}: "
                            + (
                                f"known event risk is {row.get('Known event risk', 'UNKNOWN')}. "
                                if row.get("Known event risk") == "HIGH"
                                else ""
                            )
                            + (
                                f"Conflicts: {row.get('Conflicts', '')}. "
                                if row.get("Context confidence") == "LOW" and row.get("Conflicts")
                                else ""
                            )
                        )
                        if (
                            row["Symbol"] in set(candle_qualified_setups["Symbol"])
                            and (
                                row.get("Context confidence") == "LOW"
                                or row.get("Known event risk") == "HIGH"
                            )
                        )
                        else ""
                    )
                    + (
                        f"Technical setup qualifies, but macro liquidity is "
                        f"{macro_now.get('regime', 'DATA LIMITED')} "
                        f"({macro_now.get('score', np.nan):.1f}/100). "
                        if (
                            row["Symbol"] in set(context_qualified_setups["Symbol"])
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
        swing_candidates["_confidence_rank"] = swing_candidates["Context confidence"].map(
            {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
        ).fillna(3)
        swing_candidates["_leader_rank"] = swing_candidates["Category leader"].map(
            {"TOP 3": 0, "NOT TOP 3": 1, "UNKNOWN": 2}
        ).fillna(2)
        swing_candidates["_catalyst_rank"] = swing_candidates["Catalyst status"].map(
            {
                "HIGH CATALYST": 0,
                "CATALYST WATCH": 1,
                "UPCOMING": 2,
                "NONE FOUND": 3,
                "NOT CONNECTED": 4,
            }
        ).fillna(5)
        swing_candidates["_freshness_rank"] = swing_candidates["Project freshness"].map(
            {"NEW": 0, "RECENT": 1, "MATURE": 2, "LEGACY": 3, "UNKNOWN": 4}
        ).fillna(4)
        swing_candidates["_triangle_rank"] = swing_candidates["Pattern"].map(
            {
                "ASCENDING TRIANGLE — STRONG": 0,
                "ASCENDING TRIANGLE — DEVELOPING": 1,
                "POSSIBLE ASCENDING TRIANGLE": 2,
                "NO TRIANGLE": 3,
            }
        ).fillna(4)
        swing_candidates["_bb_rank"] = swing_candidates["BB 4h regime"].map(
            {"SQUEEZE": 0, "NORMAL": 1, "EXPANDING": 2, "UNAVAILABLE": 3}
        ).fillna(3)

        def _channel_rank(row):
            direction = str(row.get("4h Channel", "UNAVAILABLE"))
            quality = str(row.get("4h Channel quality", "LOW"))
            pos = _safe_float(row.get("4h Channel pos %"), np.nan)
            state = str(row.get("4h Channel state", "NONE"))
            if direction == "RISING" and quality in ("HIGH", "MEDIUM") and math.isfinite(pos) and 10 <= pos <= 65:
                return 0
            if state == "ABOVE CHANNEL":
                return 1
            if direction == "SIDEWAYS" and quality in ("HIGH", "MEDIUM") and math.isfinite(pos) and pos <= 55:
                return 1
            if direction == "RISING" and math.isfinite(pos) and pos <= 85:
                return 2
            if direction == "SIDEWAYS":
                return 3
            if direction == "RISING":
                return 4
            if direction == "FALLING":
                return 5
            return 6

        swing_candidates["_channel_rank"] = swing_candidates.apply(_channel_rank, axis=1)
        swing_candidates = swing_candidates.sort_values(
            ["Status", "_confidence_rank", "_leader_rank", "_catalyst_rank", "_triangle_rank", "_bb_rank", "_channel_rank", "_freshness_rank", "Score"],
            ascending=[True, True, True, True, True, True, True, True, False],
        ).drop(columns=[
            "_confidence_rank", "_leader_rank", "_catalyst_rank", "_triangle_rank",
            "_bb_rank", "_channel_rank", "_freshness_rank"
        ])
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

        st.subheader("Category rotation")
        st.caption(
            "Looks for category leadership and acceleration using CoinGecko top-3 category "
            "leaders in the scanned universe. Relative performance is measured versus BTC "
            "over approximately 30, 90 and 180 days. ROTATING IN aims to highlight a "
            "category whose recent leadership is accelerating before it becomes an obvious "
            "six-month winner."
        )
        category_df = category_rotation_table(df)
        if category_df.empty:
            st.info("Not enough category-leader performance data is available in this scan yet.")
        else:
            rotating = category_df[category_df["Rotation status"] == "ROTATING IN"]
            leading = category_df[category_df["Rotation status"] == "LEADING"]
            cr1, cr2, cr3 = st.columns(3)
            cr1.metric("Rotating in", len(rotating))
            cr2.metric("Leading categories", len(leading))
            cr3.metric(
                "Top category",
                str(category_df.iloc[0]["Category"]) if not category_df.empty else "Unavailable",
            )
            st.dataframe(
                category_df,
                hide_index=True,
                use_container_width=True,
                column_config={
                    "Category momentum": st.column_config.ProgressColumn(
                        "Category momentum", min_value=0, max_value=100, format="%.1f"
                    ),
                    "RS vs BTC 30d %": st.column_config.NumberColumn(format="%.2f%%"),
                    "RS vs BTC 90d %": st.column_config.NumberColumn(format="%.2f%%"),
                    "RS vs BTC 180d %": st.column_config.NumberColumn(format="%.2f%%"),
                    "30d leader breadth %": st.column_config.NumberColumn(format="%.1f%%"),
                },
            )

        swing_tab, accumulation_tab = st.tabs(["Swing trades", "Accumulation"])

        with swing_tab:
            st.subheader("Swing-trade candidates")
            st.caption(
                f"All {len(df)} analysed coins are shown. BUY requires a trade score of "
                f"{cfg.score_threshold}+ and the existing shape and 30% gross-target rules. "
                "A technical qualifier is only promoted to BUY when an altcoin is beating BTC over "
                "the 48h relative-strength window, circulating supply is at least 25% of total/max "
                "supply, the coin is verified on at least 2 major CEXs, and the macro-liquidity "
                "regime is not deteriorating/contracting. "
                "Unknown tokenomics remain WAIT rather than passing by assumption. "
                "WAIT candidates remain visible with their reasons. "
                "The first columns show the trade plan: current price, planned entry, stop/exit, "
                "price target, projected ROI and reward/risk. A red shooting star on the latest "
                "completed 4h candle forces an otherwise-qualified setup to WAIT for confirmation. "
                "The TA limitation overlay also keeps LOW-confidence / high-event-risk setups at WAIT "
                "when too many signals conflict or a known risk event could invalidate the chart. "
                "Green = preferred, amber = borderline, red = weak or extended."
            )
            if swing_setups.empty:
                rs_blocked = len(technical_swing_setups) - len(rs_qualified_setups)
                tokenomics_blocked = len(rs_qualified_setups) - len(tokenomics_qualified_setups)
                cex_blocked = len(tokenomics_qualified_setups) - len(cex_qualified_setups)
                candle_blocked = len(cex_qualified_setups) - len(candle_qualified_setups)
                context_blocked = len(candle_qualified_setups) - len(context_qualified_setups)
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
                elif cex_blocked > 0:
                    st.info(
                        f"{cex_blocked} otherwise-qualified setup(s) remain WAIT because they "
                        "do not have at least 2 verified listings across the major CEX basket."
                    )
                elif candle_blocked > 0:
                    st.info(
                        f"{candle_blocked} otherwise-qualified setup(s) remain WAIT because the "
                        "latest completed 4h candle is a red shooting star."
                    )
                elif context_blocked > 0:
                    st.info(
                        f"{context_blocked} otherwise-qualified setup(s) remain WAIT because the "
                        "TA limitation overlay is LOW confidence or a known high-risk event is present."
                    )
                elif not context_qualified_setups.empty and not macro_allows_new_risk:
                    st.info(
                        f"{len(context_qualified_setups)} technical setup(s) currently meet the "
                        f"{cfg.score_threshold}+, 30% target, relative-strength, tokenomics, "
                        "major-CEX, candle and conflict rules, but macro liquidity is "
                        f"{macro_now.get('regime', 'DATA LIMITED')}; they remain WAIT."
                    )
                else:
                    st.info(
                        f"No swing-trade setup currently meets the {cfg.score_threshold}+ "
                        "BUY rules and 30% gross-target requirement."
                    )
            swing_cols = [
                "Coin", "Status", "Context confidence", "Known event risk",
                "Price", "Entry Price", "Exit / Stop", "Price Target", "ROI %", "R:R",
                "Score", "Signal agreement %", "Conflict count", "Non-TA confirmations",
                "Pattern", "Triangle score", "Triangle touches", "Triangle compression %",
                "Candle caution", "Last 4h candle",
                "SMA regime", "SMA50", "SMA200", "Price vs SMA50 %", "Price vs SMA200 %",
                "BB 4h regime", "BB 4h width %", "BB 4h width percentile", "BB 4h position %",
                "BB Daily regime", "4h Channel", "4h Channel pos %", "4h Channel support",
                "4h Channel resistance", "4h Channel R:R", "4h Channel quality",
                "Daily Channel", "Daily Channel pos %",
                "Entry low", "Entry high", "Breakout", "First resistance target", "Stretch target",
                "Reason",
                "Category leader", "Leader categories",
                "Project freshness", "History days", "Freshness score",
                "Catalyst status", "Next catalyst", "Catalyst date", "Catalyst days",
                "Catalyst categories", "Catalyst impact",
                "Coin trend", "Market trend",
                "Major CEX quality", "Major CEX count", "Major CEX listings",
                "Tokenomics gate", "Circulating %", "FDV / MCap", "Tokenomics risks",
                "Macro regime", "Macro score", "Conflicts", "Non-TA detail",
                "News coverage", "Sentiment coverage", "TA limitation note",
                "To resistance %", "Tests", "RSI", "ATR ratio", "Vol ratio",
                "RS vs BTC %", "RS vs BTC 96h %",
                "RS vs BTC 30d %", "RS vs BTC 90d %", "RS vs BTC 180d %",
                "Triangle resistance", "Triangle support", "Triangle target", "Triangle detail",
                "Entry basis", "Invalidation", "Sell target", "Target upside %", "Target basis",
            ]
            styled_swing = swing_candidates[swing_cols].style
            styled_swing = styled_swing.map(
                lambda value: (
                    "background-color: #d8f3dc; color: #16351c; font-weight: 700"
                    if str(value) == "HIGH"
                    else "background-color: #fff3bf; color: #5f4500; font-weight: 700"
                    if str(value) == "MEDIUM"
                    else "background-color: #ffd6d6; color: #5c1717; font-weight: 700"
                    if str(value) == "LOW"
                    else ""
                ),
                subset=["Context confidence"],
            )
            styled_swing = styled_swing.map(
                lambda value: (
                    "background-color: #ffd6d6; color: #5c1717; font-weight: 700"
                    if str(value) == "HIGH"
                    else "background-color: #fff3bf; color: #5f4500; font-weight: 600"
                    if str(value) in ("MEDIUM", "UNKNOWN")
                    else "background-color: #d8f3dc; color: #16351c; font-weight: 600"
                    if str(value) == "LOW"
                    else ""
                ),
                subset=["Known event risk"],
            )
            styled_swing = styled_swing.map(
                lambda value: (
                    "background-color: #ffd6d6; color: #5c1717; font-weight: 700"
                    if str(value) == "CAUTION"
                    else "background-color: #d8f3dc; color: #16351c; font-weight: 600"
                    if str(value) == "CLEAR"
                    else ""
                ),
                subset=["Candle caution"],
            )
            for trend_column in ["Coin trend", "Market trend"]:
                styled_swing = styled_swing.map(
                    lambda value, column=trend_column: scan_cell_style(value, column),
                    subset=[trend_column],
                )
            for traffic_column in [
                "Status", "Pattern", "SMA regime", "BB 4h regime", "BB Daily regime",
                "4h Channel", "4h Channel quality", "Daily Channel",
                "Category leader", "Major CEX quality", "Macro regime", "Catalyst status",
            ]:
                if traffic_column in swing_candidates.columns:
                    styled_swing = styled_swing.map(
                        lambda value, column=traffic_column: scan_cell_style(value, column),
                        subset=[traffic_column],
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
                "Score", "Signal agreement %", "Conflict count", "Non-TA confirmations",
                "Triangle score", "ROI %", "Macro score", "4h Channel R:R",
                "4h Channel pos %", "BB 4h width percentile",
                "Tests", "RSI", "ATR ratio", "Vol ratio", "RS vs BTC %", "RS vs BTC 96h %",
                "RS vs BTC 30d %", "RS vs BTC 90d %", "RS vs BTC 180d %", "R:R",
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
                    "Signal agreement %": st.column_config.ProgressColumn(
                        "Signal agreement", min_value=0, max_value=100, format="%.1f"
                    ),
                    "Score": st.column_config.ProgressColumn(
                        "Trade score", min_value=0, max_value=100, format="%.1f"
                    ),
                    "To resistance %": st.column_config.NumberColumn(format="%.2f%%"),
                    "RS vs BTC %": st.column_config.NumberColumn("RS vs BTC 48h %", format="%.2f%%"),
                    "RS vs BTC 96h %": st.column_config.NumberColumn(format="%.2f%%"),
                    "RS vs BTC 30d %": st.column_config.NumberColumn(format="%.2f%%"),
                    "RS vs BTC 90d %": st.column_config.NumberColumn(format="%.2f%%"),
                    "RS vs BTC 180d %": st.column_config.NumberColumn(format="%.2f%%"),
                    "Catalyst days": st.column_config.NumberColumn(format="%.1f"),
                    "History days": st.column_config.NumberColumn(format="%.0f"),
                    "Freshness score": st.column_config.ProgressColumn(
                        "Freshness", min_value=0, max_value=100, format="%.0f"
                    ),
                    "R:R": st.column_config.NumberColumn(format="%.2f"),
                    "Price": st.column_config.NumberColumn("Current Price", format="%.8g"),
                    "Entry Price": st.column_config.NumberColumn("Entry Price", format="%.8g"),
                    "Exit / Stop": st.column_config.NumberColumn("Exit / Stop", format="%.8g"),
                    "Price Target": st.column_config.NumberColumn("Price Target", format="%.8g"),
                    "ROI %": st.column_config.NumberColumn("ROI %", format="%.2f%%"),
                    "4h Channel pos %": st.column_config.NumberColumn(format="%.1f%%"),
                    "4h Channel support": st.column_config.NumberColumn(format="%.8g"),
                    "4h Channel resistance": st.column_config.NumberColumn(format="%.8g"),
                    "4h Channel R:R": st.column_config.NumberColumn(format="%.2f"),
                    "Daily Channel pos %": st.column_config.NumberColumn(format="%.1f%%"),
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
                "Coin", "Status", "Category leader", "Leader categories",
                "Project freshness", "History days", "Freshness score", "Coin trend", "Market trend", "Major CEX quality", "Major CEX count", "Major CEX listings", "Tokenomics gate", "Circulating %", "FDV / MCap", "Tokenomics risks", "Accumulation score", "Reason", "Price", "Accumulation signal",
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
                    "History days": st.column_config.NumberColumn(format="%.0f"),
                    "Freshness score": st.column_config.ProgressColumn(
                        "Freshness", min_value=0, max_value=100, format="%.0f"
                    ),
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

        candle1, candle2 = st.columns(2)
        candle1.metric("Last completed 4h candle", row.get("Last 4h candle", "UNAVAILABLE"))
        candle2.metric("Candle caution", row.get("Candle caution", "CLEAR"))
        if row.get("Candle detail"):
            st.caption(str(row.get("Candle detail")))

        channel1, channel2, channel3, channel4 = st.columns(4)
        channel1.metric("4h channel", row.get("4h Channel", "UNAVAILABLE"))
        channel2.metric("Channel position", row.get("4h Channel pos %", "Unavailable"))
        channel3.metric("Channel quality", row.get("4h Channel quality", "LOW"))
        channel4.metric("Channel R:R", row.get("4h Channel R:R", "Unavailable"))

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

    #### Project freshness — prefer newer narratives, with risk controls

    The scanner now labels assets **NEW, RECENT, MATURE or LEGACY** using the amount of spot-price history available on the selected exchange. This is a **freshness proxy**, not the project's exact launch date. Newer/recent projects are ranked ahead of otherwise similar older assets because fresh narratives often attract more speculative capital, but freshness is **not a hard BUY gate** and cannot override low-float tokenomics, weak liquidity, poor relative strength or a bad technical setup.

    #### Information advantage — upcoming catalyst radar

    Swing trades now have an **Upcoming Catalyst** layer. When a server-side CoinMarketCal API key is configured, the scanner pulls the upcoming event catalog once per scan and maps events to coins. Mainnet launches, releases, upgrades, integrations, listings, partnerships, roadmap items and similar near-dated events are labelled **HIGH CATALYST / CATALYST WATCH**; token unlock or vesting-style events are labelled **RISK EVENT**. Catalyst presence is a ranking advantage, not a hard BUY gate and not automatically bullish because markets can price events early or sell the news.

    Quick Analyse also exposes official project links and can search the **official X account's last 7 days** for planned-announcement language when an optional X bearer token is configured. X search is intentionally on-demand rather than run across the full universe.

    #### Category rotation — find the narrative before selecting the coin

    The scanner now builds a **Category Rotation** table from CoinGecko's current top-3 category leaders that are present in the scanned universe. It compares those leaders with BTC over roughly **30, 90 and 180 days**. **ROTATING IN** means recent category relative strength is accelerating versus its 3-month and 6-month pace; **LEADING** means the category is outperforming BTC across all three horizons; **FADING** means longer-term leadership remains but the latest 30-day relative strength has turned negative. Coverage/confidence shows how many of that category's leaders were actually observed in the current scan.

    This is intentionally used to answer **which category is attracting capital first**, before choosing the strongest coin inside that category.

    #### Category leadership — prefer leaders over copycats

    CoinGecko publishes the current **top 3 coins in each crypto category**. The scanner now marks a coin **TOP 3** when its CoinGecko ID appears in that published leader set and shows the categories where it leads. Category leadership is a **strong ranking preference rather than a hard BUY gate** because categories overlap and leadership can rotate; TOP 3 candidates are ranked ahead of otherwise similar non-leaders.

    #### Major-exchange breadth — legitimacy / liquidity quality

    The scanner checks active spot listings across **Binance, Coinbase, Kraken, OKX, Bybit, Gate, Bitget and MEXC**. A main-screener BUY requires at least **2 verified major CEX listings**. **4+ = STRONG**, 3 = GOOD, 2 = ACCEPTABLE, 1 = WEAK. This is treated as a legitimacy/liquidity-quality gate rather than proof that the project has intrinsically strong fundamentals.

    #### Relative strength gate — altcoin must beat Bitcoin

    For an **altcoin** to become a BUY, its 48-hour return must be stronger than BTC's over the same period (**RS vs BTC > 0%**). BTC itself is exempt. The 96-hour reading remains confirmation: positive on both windows is stronger; positive 48h with weaker 96h can indicate early rotation. A technically good altcoin that is not beating BTC remains WAIT.

    #### Tokenomics gate — supply quality

    For altcoin BUY decisions, the scanner now requires **at least 25% of total supply (or max supply when total supply is unavailable) to be circulating**. Below 25% is treated as low float and remains WAIT; missing supply data is UNKNOWN and also remains WAIT rather than being assumed safe. The scanner also flags **FDV / market-cap ratios of 4x or more** as high-FDV/low-float risk. Detailed VC allocations and future insider unlock schedules require a specialist verified dataset and are shown as needing separate verification rather than guessed.

    #### Technical-analysis limitations — confidence, not certainty

    The screener deliberately separates **technical setup score** from **context confidence**. TA is backward-looking and can fail when news, token events, sentiment shocks or market-regime changes overwhelm the chart. The overlay therefore checks for conflicting signals across BTC relative strength, coin/market trend, SMA50/200 structure, channel direction, RSI, Bollinger state, candle rejection, ascending-triangle structure and macro liquidity.

    - **HIGH context confidence:** strong agreement with few/no material conflicts.
    - **MEDIUM:** usable setup, but one or more signals or event conditions deserve caution.
    - **LOW:** too many conflicts or a known high-risk event; an otherwise technical BUY remains WAIT.
    - **Known event risk:** explicit unlock/vesting-style risk events are treated as HIGH risk; near-dated catalysts are treated as event-volatility caution.
    - **News coverage:** the full scan can only see structured known events. Unexpected breaking news cannot be predicted.
    - **Sentiment coverage:** the full scan currently uses proxies rather than pretending it has complete market-wide social sentiment.
    - **Invalidation remains mandatory:** no confidence label removes the need to exit when the trade thesis fails.

    The confidence overlay is **not a win probability** and does not rewrite the raw technical score. Its purpose is to stop a good-looking chart from being treated as sufficient evidence on its own.

    #### Bollinger Bands — compression before expansion

    The crypto swing model now calculates **20-period Bollinger Bands with 2 standard deviations** on both 4h and daily data. The main pre-breakout signal is the **4h band-width percentile**: very low relative width is labelled **SQUEEZE**, normal compression is **NORMAL**, and a clear increase in band width is **EXPANDING**. Price position is shown from 0% at the lower band to 100% at the upper band. SQUEEZE is a ranking preference, not a hard BUY rule, because Bollinger compression overlaps with the ATR/range-compression logic already in the technical score.

    #### Trend channels — swing structure and location

    The scanner uses a reproducible regression channel rather than hand-picked trendlines. Crypto uses an **80-bar 4h channel for execution** and a **90-day channel for broader swing structure**. It reports channel direction, support, resistance, price position, validation quality and channel-based reward/risk. Reliable channel support can help define the planned entry. Better lower/middle-channel locations rank ahead of otherwise similar upper-channel setups, while falling channels are treated cautiously. A move above the channel is not automatically rejected because breakout/retest trades can still be valid.

    #### Latest 4h candle — rejection caution

    The scanner checks the **latest completed 4h candle**, ignoring an unfinished live candle. A **red shooting star** requires a red close, a relatively small body near the low of the candle and a long upper wick showing rejection of higher prices. Because the screener is deliberately looking for entries close to resistance, an otherwise-qualified setup with this candle pattern is held at **WAIT** until the next candles confirm that the rejection has been absorbed. The raw technical score is left unchanged; this is a separate execution-risk gate.

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
