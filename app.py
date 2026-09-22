from __future__ import annotations

import asyncio
import io
import json
import math
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import ccxt.async_support as ccxt
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st
from cl_signal_ui import render_module_header, render_decision_guidance
from crypto_macro import snapshot_age_minutes, unavailable_macro
from crypto_universe_rules import is_crypto_universe_asset
from crypto_rule_engine import score_setup as shared_score_setup
from crypto_accumulation_model import (
    ACCUMULATION_MODEL_VERSION,
    score_accumulation,
    tokenomics_context as shared_tokenomics_context,
)
from pre_pump_research import (
    build_feature_frame,
    feature_comparison,
    label_forward_outcomes,
    research_summary,
    snapshot_table,
)


st.set_page_config(page_title="CL Signal · Crypto", page_icon="⚡", layout="wide")


PREPARED_CRYPTO_DIR = Path(__file__).resolve().parent / "prepared_crypto"


EXCHANGES = {
    "Binance": "binance",
    "Bybit": "bybit",
    "OKX": "okx",
    "Kraken": "kraken",
    "Crypto.com": "cryptocom",
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
    "Crypto.com": "cryptocom",
}


def make_public_ccxt_exchange(exchange_id: str):
    cls = getattr(ccxt, exchange_id)
    config = {
        "enableRateLimit": True,
        "options": {
            "defaultType": "spot",
            "fetchMarkets": {"types": ["spot"]},
            "fetchCurrencies": False,
            "fetchMargins": False,
        },
    }
    if exchange_id == "bybit":
        config["hostname"] = "bytick.com"
    exchange = cls(config)
    if exchange_id == "binance":
        api_urls = exchange.urls.get("api", {})
        if isinstance(api_urls, dict):
            api_urls["public"] = "https://data-api.binance.vision/api/v3"
            api_urls["v1"] = "https://data-api.binance.vision/api/v1"
    return exchange


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


@st.cache_data(ttl=60, show_spinner=False)
def load_crypto_pipeline_state() -> Dict:
    raw_base = (
        "https://raw.githubusercontent.com/"
        "charleslawrence1984-source/crypto-prebreakout-screener/"
        "crypto-data/prepared_crypto/"
    )

    names = {
        "manifest": "manifest.json",
        "audit": "audit.json",
        "macro": "macro.json",
        "active": "active_monitor.csv.gz",
        "discovery": "discovery.csv.gz",
        "universe": "universe.csv.gz",
        "scores": "deep_scores.csv.gz",
        "swing": "swing_opportunities.csv.gz",
        "accumulation": "accumulation_opportunities.csv.gz",
    }

    def fetch(name: str) -> bytes | None:
        try:
            response = requests.get(raw_base + name, timeout=3)
            response.raise_for_status()
            return response.content
        except Exception:
            return None

    remote = {}
    with ThreadPoolExecutor(max_workers=len(names)) as executor:
        futures = {
            executor.submit(fetch, filename): key
            for key, filename in names.items()
        }
        for future in as_completed(futures):
            remote[futures[future]] = future.result()

    def read_json(key: str) -> dict:
        try:
            payload = remote.get(key)
            if payload:
                return json.loads(payload.decode("utf-8"))
        except Exception:
            pass
        try:
            return json.loads(
                (PREPARED_CRYPTO_DIR / names[key]).read_text(encoding="utf-8")
            )
        except Exception:
            return {}

    def read_csv(key: str) -> pd.DataFrame:
        try:
            payload = remote.get(key)
            if payload:
                return pd.read_csv(io.BytesIO(payload), compression="gzip")
        except Exception:
            pass
        try:
            return pd.read_csv(PREPARED_CRYPTO_DIR / names[key], compression="gzip")
        except Exception:
            return pd.DataFrame()

    manifest = read_json("manifest")
    audit = read_json("audit")
    macro = read_json("macro")
    active = read_csv("active")
    discovery = read_csv("discovery")
    universe = read_csv("universe")
    scores = read_csv("scores")
    swing = read_csv("swing")
    accumulation = read_csv("accumulation")
    return {
        "manifest": manifest,
        "audit": audit,
        "macro": macro,
        "active": active,
        "discovery": discovery,
        "universe": universe,
        "scores": scores,
        "swing": swing,
        "accumulation": accumulation,
    }


def crypto_pipeline_age_minutes(manifest: dict) -> float:
    value = manifest.get("updated_at")
    if not value:
        return np.nan
    try:
        timestamp = pd.Timestamp(value)
        if timestamp.tzinfo is None:
            timestamp = timestamp.tz_localize("UTC")
        return max(
            (pd.Timestamp.now(tz="UTC") - timestamp.tz_convert("UTC")).total_seconds() / 60,
            0.0,
        )
    except Exception:
        return np.nan


def crypto_pipeline_health_label(manifest: dict) -> str:
    age = crypto_pipeline_age_minutes(manifest)
    status = str(manifest.get("status") or "NOT READY").upper()
    if not math.isfinite(age):
        return "NOT READY"
    if age > 20:
        return "STALE"
    if status == "PARTIAL":
        return "PARTIAL"
    return "CURRENT"


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

            exchange = make_public_ccxt_exchange(resolved_id)
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
    """Use the shared tokenomics context so app and background accumulation agree."""
    base = str(symbol).split("/")[0].upper()
    return shared_tokenomics_context(base, snapshot, category_leaders)


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
        return green if number >= 3 else amber if number >= 1 else ""
    if column == "RSI":
        return green if 45 <= number <= 68 else amber if 38 <= number <= 74 else red
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
    """Use the shared headless rule engine so UI and background scans stay identical."""
    return shared_score_setup(df4h, dfd, btc4h, cfg, dfw=dfw, btcd=btcd)

async def fetch_market_universe(cfg: ScreenerConfig) -> Tuple[List[Tuple[str, float]], Dict[str, dict], dict]:
    exchange = make_public_ccxt_exchange(cfg.exchange_id)
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
            if not is_crypto_universe_asset(base, cfg.exchange_id):
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
    exchange = make_public_ccxt_exchange(cfg.exchange_id)
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
            and is_crypto_universe_asset(
                str(market.get("base", "")).upper(),
                cfg.exchange_id,
            )
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
        result.update(prepared_execution_info(symbol.split("/")[0]))
        result = apply_full_accumulation_model(result)
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
    exchange = make_public_ccxt_exchange(cfg.exchange_id)
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
                    result.update(prepared_execution_info(symbol.split("/")[0]))
                    result = apply_full_accumulation_model(result)
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
            accumulation_ready = result.get("accumulation_model_status") == "ACCUMULATE"
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
                    else "ACCUMULATE" if r.get("accumulation_model_status") == "ACCUMULATE"
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
                "Accumulation score": r.get("accumulation_quality_score", np.nan),
                "Accumulation base score": r.get("accumulation_base_score", r["bottom_score"]),
                "Accumulation quality pass": r.get("accumulation_quality_pass", False),
                "Accumulation tokenomics pass": r.get("accumulation_tokenomics_pass", False),
                "Accumulation reason": r.get("accumulation_reason", ""),
                "Accumulation model version": r.get("accumulation_model_version", ACCUMULATION_MODEL_VERSION),
                "Accumulation low": r["accumulation_low"],
                "Accumulation high": r["accumulation_high"],
                "In accumulation zone": r["in_accumulation_zone"],
                "Cycle accumulation low": r["cycle_accumulation_low"],
                "Cycle accumulation high": r["cycle_accumulation_high"],
                "In cycle accumulation zone": r["in_cycle_accumulation_zone"],
                "Cycle accumulation basis": r["cycle_accumulation_basis"],
                "Accumulation verdict": r.get("accumulation_model_status", "PASS"),
                "Base verdict": r["accumulation_verdict"],
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
    exchange = make_public_ccxt_exchange(exchange_id)
    try:
        await exchange.load_markets()
        coin4 = ohlcv_to_df(await exchange.fetch_ohlcv(symbol, timeframe="4h", limit=500))
        coind = ohlcv_to_df(await exchange.fetch_ohlcv(symbol, timeframe="1d", limit=365))
        btc4 = ohlcv_to_df(await exchange.fetch_ohlcv(f"BTC/{quote}", timeframe="4h", limit=500))
        return coin4, coind, btc4
    finally:
        await exchange.close()


async def fetch_prepump_research_data(
    exchange_id: str,
    symbol: str,
    quote: str,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Fetch a deeper daily history for point-in-time pre-pump research."""
    exchange = make_public_ccxt_exchange(exchange_id)
    try:
        await exchange.load_markets()
        if symbol not in exchange.markets:
            raise ValueError(f"{symbol} is not available on {exchange_id}.")
        btc_symbol = f"BTC/{quote}"
        if btc_symbol not in exchange.markets:
            raise ValueError(f"{btc_symbol} benchmark is not available on {exchange_id}.")
        coin_raw, btc_raw = await asyncio.gather(
            exchange.fetch_ohlcv(symbol, timeframe="1d", limit=1000),
            exchange.fetch_ohlcv(btc_symbol, timeframe="1d", limit=1000),
        )
        return ohlcv_to_df(coin_raw), ohlcv_to_df(btc_raw)
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


def _boolish(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes", "y"}


def prepared_execution_info(base: str) -> Dict:
    """Latest background liquidity/listing check for the user's execution venues."""
    base = str(base or "").strip().upper()
    state = load_crypto_pipeline_state()
    universe = state.get("universe", pd.DataFrame())
    if universe is None or universe.empty or "Base" not in universe.columns:
        return {
            "execution_available": False,
            "execution_liquidity_pass": False,
            "execution_venues": "",
            "execution_reason": "Background execution-liquidity check not available yet",
            "kraken_available": False,
            "cryptocom_available": False,
            "major_venue_listing_count": 0,
            "major_venue_listings": "",
            "major_venues_checked": 0,
            "cross_exchange_quote_volume": np.nan,
            "execution_max_quote_volume": np.nan,
        }

    match = universe[universe["Base"].astype(str).str.upper() == base]
    if match.empty:
        return {
            "execution_available": False,
            "execution_liquidity_pass": False,
            "execution_venues": "",
            "execution_reason": "Coin is not yet in the prepared discovery universe",
            "kraken_available": False,
            "cryptocom_available": False,
            "major_venue_listing_count": 0,
            "major_venue_listings": "",
            "major_venues_checked": 0,
            "cross_exchange_quote_volume": np.nan,
            "execution_max_quote_volume": np.nan,
        }

    row = match.iloc[0]
    return {
        "execution_available": _boolish(row.get("Execution available", False)),
        "execution_liquidity_pass": _boolish(row.get("Execution liquidity pass", False)),
        "execution_venues": str(row.get("Execution venues", "") or ""),
        "execution_reason": str(row.get("Execution reason", "") or ""),
        "kraken_available": _boolish(row.get("Kraken available", False)),
        "cryptocom_available": _boolish(row.get("Crypto.com available", False)),
        "kraken_quote_volume": _safe_float(row.get("Kraken USD-like 24h volume"), 0.0),
        "cryptocom_quote_volume": _safe_float(row.get("Crypto.com USD-like 24h volume"), 0.0),
        "major_venue_listing_count": int(_safe_float(row.get("Major venue listing count"), 0)),
        "major_venue_listings": str(row.get("Major venue listings", "") or ""),
        "major_venues_checked": int(_safe_float(row.get("Major venues checked"), 0)),
        "cross_exchange_quote_volume": _safe_float(row.get("Cross-exchange quote volume"), np.nan),
        "execution_max_quote_volume": _safe_float(row.get("Execution max USD-like 24h volume"), np.nan),
    }


def attach_prepared_execution_columns(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    state = load_crypto_pipeline_state()
    universe = state.get("universe", pd.DataFrame())
    if universe is None or universe.empty or "Base" not in universe.columns:
        out = df.copy()
        out["Execution liquidity pass"] = False
        out["Execution venues"] = ""
        out["Execution reason"] = "Background execution-liquidity check not available yet"
        return out

    cols = [
        "Base", "Cross-exchange quote volume", "Kraken available",
        "Crypto.com available", "Kraken USD-like 24h volume",
        "Crypto.com USD-like 24h volume", "Execution available",
        "Execution venues", "Execution max USD-like 24h volume",
        "Execution liquidity pass", "Execution reason",
    ]
    available_cols = [col for col in cols if col in universe.columns]
    meta = universe[available_cols].copy()
    meta["Base"] = meta["Base"].astype(str).str.upper()
    out = df.copy()
    if "Coin" in out.columns:
        out["_Execution base"] = out["Coin"].astype(str).str.upper()
    elif "Base" in out.columns:
        out["_Execution base"] = out["Base"].astype(str).str.upper()
    else:
        out["_Execution base"] = ""
    out = out.merge(meta, left_on="_Execution base", right_on="Base", how="left", suffixes=("", "_exec"))
    out = out.drop(columns=["_Execution base", "Base_exec"], errors="ignore")
    if "Execution liquidity pass" in out.columns:
        out["Execution liquidity pass"] = out["Execution liquidity pass"].map(_boolish)
    return out


def apply_full_accumulation_model(result: Dict) -> Dict:
    if not result:
        return result
    context = {
        "tokenomics_gate": result.get("tokenomics_gate", "UNKNOWN"),
        "circulating_pct": result.get("circulating_pct", np.nan),
        "minimum_circulating_pct": result.get("minimum_circulating_pct", 25.0),
        "fdv_mcap": result.get("fdv_mcap", np.nan),
        "market_cap": result.get("market_cap", np.nan),
        "fdv": result.get("fdv", np.nan),
        "coingecko_id": result.get("coingecko_id", ""),
        "category_leader": result.get("category_leader", "UNKNOWN"),
        "leader_categories": result.get("leader_categories", ""),
        "meme_supply_exception": result.get("meme_supply_exception", False),
        "tokenomics_risks": result.get("tokenomics_risks", ""),
        "unlock_review": result.get(
            "unlock_review",
            result.get("vc_unlock_review", "UNVERIFIED — specialist unlock/vesting data not scored"),
        ),
        "major_venue_listing_count": result.get(
            "major_venue_listing_count",
            result.get("major_cex_count", 0),
        ),
        "major_venue_listings": result.get(
            "major_venue_listings",
            result.get("major_cex_list", ""),
        ),
        "major_venues_checked": result.get(
            "major_venues_checked",
            result.get("major_cex_checked", 0),
        ),
    }
    model = score_accumulation(
        result,
        context,
        bool(result.get("execution_liquidity_pass", False)),
    )
    result.update(model)
    return result


def crypto_trade_decision(result: Dict, macro_now: Dict, score_threshold: float = 80.0) -> Dict:
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

    score_pass = _safe_float(result.get("score"), 0.0) >= float(score_threshold)
    rs_pass = base == "BTC" or _safe_float(result.get("rs_vs_btc_pct"), -999) > 0
    candle_pass = not bool(result.get("candle_caution"))
    execution_pass = bool(result.get("execution_liquidity_pass", False))

    # Swing BUY is driven by the technical setup, but a personal BUY also requires
    # safe execution liquidity on Kraken or Crypto.com. Tokenomics, CEX breadth, macro,
    # catalysts and general context are warnings/confidence only; they do not rescue
    # a poor chart and they do not veto an otherwise valid technical setup.
    if result.get("eligible") and score_pass and rs_pass and candle_pass and execution_pass:
        warnings = []
        if result.get("tokenomics_gate") != "PASS":
            warnings.append("tokenomics risk/unknown")
        if result.get("major_cex_gate") != "PASS":
            warnings.append("limited major-exchange breadth")
        if overlay.get("Context confidence") == "LOW":
            warnings.append("low context confidence")
        if overlay.get("Known event risk") == "HIGH":
            warnings.append("known event risk")
        if not macro_now.get("allows_new_swing_risk", True):
            warnings.append("macro liquidity headwind")
        suffix = (
            " Context warnings: " + ", ".join(warnings) + "."
            if warnings else ""
        )
        return {
            "action": "BUY",
            "reason": (
                f"Technical pre-breakout rules pass: score {result.get('score', 0):.1f} "
                f">= {float(score_threshold):.0f}, positive relative strength, a credible 30%+ target, "
                "and execution liquidity passes on Kraken/Crypto.com."
                + suffix
            ),
        }

    if result.get("eligible"):
        reasons = []
        if not score_pass:
            reasons.append(f"technical score below {float(score_threshold):.0f}")
        if not rs_pass:
            reasons.append("not beating BTC")
        if not candle_pass:
            reasons.append("4h candle rejection caution")
        if not execution_pass:
            reasons.append(
                result.get("execution_reason")
                or "execution liquidity not confirmed on Kraken/Crypto.com"
            )
        return {
            "action": "WAIT",
            "reason": "The pre-breakout shape exists, but " + ", ".join(reasons or ["the technical entry is not ready"]) + ".",
        }

    return {
        "action": "PASS",
        "reason": result.get("reason", "The current pre-breakout shape does not meet the approved setup rules."),
    }


def crypto_accumulation_decision(result: Dict) -> Dict:
    status = str(result.get("accumulation_model_status") or "PASS").upper()
    reason = str(result.get("accumulation_reason") or "Accumulation evidence is incomplete.")
    if status == "ACCUMULATE":
        return {"action": "ACCUMULATE", "reason": reason}
    if status in {"QUALITY WATCH", "BASE DEVELOPING"}:
        return {"action": "WAIT", "reason": reason}
    return {"action": "PASS", "reason": reason}

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
    universe_size = st.select_slider("Manual live refresh limit", options=[25, 50, 75, 100, 150, 200, 250], value=200)
    st.caption("This controls only the optional Advanced Trade live refresh; it is not the size of the automatic discovery universe.")
    min_vol_m = st.number_input("Manual refresh pair-volume floor ($m)", min_value=1.0, max_value=500.0, value=5.0, step=1.0)
    threshold = st.slider("Minimum BUY score", 80, 95, 80, 1)
    max_distance = st.slider("Maximum distance below resistance (%)", 1.0, 8.0, 5.0, 0.25)
    max_rsi = st.slider("Maximum RSI", 60, 75, 69, 1)
    refresh_minutes = st.selectbox(
        "Formal scan refresh",
        [5, 10, 15, 30],
        index=1,
        format_func=lambda x: f"Manual · previous setting {x} min",
        disabled=True,
        help="The background crypto rule engine updates automatically. Advanced Trade lets you run an optional live refresh.",
    )
    st.caption("24/7 discovery refreshes every 5 min. Full CL Signal strategy scans run only when you press **Run scan now**.")
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

tab_crypto_home, tab_crypto_quick, tab_crypto_opportunities, tab_crypto_watchlist, tab_crypto_portfolio, tab_crypto_advanced_trade, tab_crypto_advanced_accumulation = st.tabs(
    ["Home", "Quick Analysis", "Opportunities", "Watchlist", "Portfolio", "Advanced Trade", "Advanced Accumulation"]
)
# Research and the existing full scanner now live inside Advanced Trade rather than
# occupying separate top-level navigation.
tab_crypto_research = tab_crypto_advanced_trade
tab_crypto_advanced = tab_crypto_advanced_trade

# Draw the module navigation before any remote data is requested. A cold macro
# refresh previously ran here and could block the entire page for 60-90 seconds,
# making Crypto look blank. The scheduled pipeline now supplies the macro snapshot;
# an optional live refresh remains available in Advanced Trade.
pipeline_state = load_crypto_pipeline_state()
prepared_macro = pipeline_state.get("macro") or {}
macro_override = st.session_state.get("macro_liquidity_override") or {}
if macro_override.get("regime"):
    macro = macro_override
elif prepared_macro.get("regime"):
    macro = prepared_macro
else:
    macro = unavailable_macro(["Prepared macro snapshot is initialising."])
st.session_state.macro_liquidity = macro

prepared_swing_feed = pipeline_state.get("swing", pd.DataFrame()).copy()
prepared_accumulation_feed = pipeline_state.get("accumulation", pd.DataFrame()).copy()
prepared_deep_scores = pipeline_state.get("scores", pd.DataFrame()).copy()

with tab_crypto_home:
    st.markdown("### Your crypto dashboard")
    st.caption("Start with a coin, browse what the background screener is finding, or check the coins you are already watching.")

    crypto_watchlist = load_crypto_watchlist()
    pipeline_manifest = pipeline_state["manifest"]

    swing_buy_count = (
        int((prepared_swing_feed["Swing status"] == "BUY").sum())
        if not prepared_swing_feed.empty and "Swing status" in prepared_swing_feed.columns else 0
    )
    swing_watch_count = (
        int((prepared_swing_feed["Swing status"] == "WATCH").sum())
        if not prepared_swing_feed.empty and "Swing status" in prepared_swing_feed.columns else 0
    )
    accumulation_ready_count = (
        int((prepared_accumulation_feed["Accumulation status"] == "ACCUMULATE").sum())
        if not prepared_accumulation_feed.empty and "Accumulation status" in prepared_accumulation_feed.columns else 0
    )
    accumulation_watch_count = (
        int((prepared_accumulation_feed["Accumulation status"] == "WATCH").sum())
        if not prepared_accumulation_feed.empty and "Accumulation status" in prepared_accumulation_feed.columns else 0
    )

    d1, d2, d3, d4, d5, d6 = st.columns(6)
    d1.metric("Swing BUY", swing_buy_count)
    d2.metric("Swing WATCH", swing_watch_count)
    d3.metric("Accumulation READY", accumulation_ready_count)
    d4.metric("Accumulation WATCH", accumulation_watch_count)
    d5.metric("Watchlist", len(crypto_watchlist))
    d6.metric("Pipeline", crypto_pipeline_health_label(pipeline_manifest))

    if pipeline_manifest:
        pipeline_time = (
            pd.Timestamp(pipeline_manifest.get("updated_at")).tz_convert("Europe/London").strftime("%d %b %Y %H:%M")
            if pipeline_manifest.get("updated_at") else "not yet available"
        )
        st.caption(
            "Data freshness · "
            f"Background rule engine: {pipeline_time} · "
            f"{int(pipeline_manifest.get('deep_scores_current', 0) or 0)} coins deep-scored · "
            f"{int(pipeline_manifest.get('unique_eligible_coins', 0) or 0)} eligible coins in the rotating universe"
        )
    else:
        st.caption("Background rule engine is initialising.")

    home_a, home_b, home_c = st.columns(3)
    with home_a:
        with st.container(border=True):
            st.markdown("#### 🔎 Analyse a coin")
            st.write("Search by coin name or ticker and get the Swing and Accumulation decision first.")
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
            if swing_buy_count:
                st.success(f"{swing_buy_count} Swing BUY setup{'s' if swing_buy_count != 1 else ''}")
            elif swing_watch_count:
                st.info(f"{swing_watch_count} Swing setup{'s' if swing_watch_count != 1 else ''} developing on WATCH")
            else:
                st.info("No Swing setup is ready right now.")
            if accumulation_ready_count:
                st.success(
                    f"{accumulation_ready_count} Accumulation setup"
                    f"{'s' if accumulation_ready_count != 1 else ''} READY"
                )
            elif accumulation_watch_count:
                st.info(
                    f"{accumulation_watch_count} Accumulation setup"
                    f"{'s' if accumulation_watch_count != 1 else ''} developing"
                )
            st.caption("Open **Opportunities** above to see the full automatically prepared shortlist.")

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

            trade_decision = crypto_trade_decision(home_result, macro, cfg.score_threshold)
            accumulation_decision = crypto_accumulation_decision(home_result)
            accumulation_action = accumulation_decision["action"]
            accumulation_reason = accumulation_decision["reason"]

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
            st.caption("Open **Quick Analysis** for the full single-coin view. **Advanced Trade** and **Advanced Accumulation** hold the deeper tools.")

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
    st.subheader("Opportunities")
    st.caption(
        "The background rule engine continuously scans the eligible crypto universe and feeds this page automatically. "
        "You do not need to run an Advanced scan first."
    )

    swing_feed_tab, accumulation_feed_tab = st.tabs(
        ["Swing opportunities", "Accumulation opportunities"]
    )

    with swing_feed_tab:
        swing_opportunities = prepared_swing_feed.copy()
        if swing_opportunities.empty:
            st.info(
                "No prepared Swing BUY/WATCH setups are available yet. "
                "The background pipeline may still be building its first deep-score sweep."
            )
        else:
            buy_count = int((swing_opportunities["Swing status"] == "BUY").sum())
            watch_count = int((swing_opportunities["Swing status"] == "WATCH").sum())
            scored_count = int(pipeline_manifest.get("deep_scores_current", 0) or 0)

            s1, s2, s3 = st.columns(3)
            s1.metric("BUY", buy_count)
            s2.metric("WATCH", watch_count)
            s3.metric("Coins deep-scored", scored_count)

            swing_filter = st.radio(
                "Show",
                ["Best opportunities", "BUY", "WATCH", "All"],
                horizontal=True,
                key="crypto_opportunity_swing_filter",
            )
            shown_swing = swing_opportunities.copy()
            if swing_filter == "BUY":
                shown_swing = shown_swing[shown_swing["Swing status"] == "BUY"]
            elif swing_filter == "WATCH":
                shown_swing = shown_swing[shown_swing["Swing status"] == "WATCH"]
            elif swing_filter == "Best opportunities":
                shown_swing = shown_swing.head(25)

            swing_cols = [
                "Swing status", "Opportunity stage", "Base", "Exchange", "Swing score", "Price",
                "Planned entry", "Invalidation", "Target", "Target upside %", "R:R",
                "RS vs BTC %", "RSI", "ATR ratio", "Vol ratio", "Distance %",
                "Resistance tests", "Coin trend", "Pattern", "Candle caution",
                "Execution venues", "Execution liquidity pass", "Execution reason",
                "Cross-exchange quote volume", "Kraken available", "Crypto.com available",
                "Swing reason", "deep_scored_at",
            ]
            visible_swing_cols = [col for col in swing_cols if col in shown_swing.columns]
            st.dataframe(
                shown_swing[visible_swing_cols],
                hide_index=True,
                use_container_width=True,
            )
            st.caption(
                "BUY means the approved technical-first Swing rules pass in the scheduled rule engine. "
                "WATCH means the setup is developing or close, but is not actionable yet. "
                "Use Quick Analysis for the freshest single-coin confirmation before acting."
            )

    with accumulation_feed_tab:
        accumulation_opportunities = prepared_accumulation_feed.copy()
        if accumulation_opportunities.empty:
            st.info(
                "No prepared Accumulation READY/WATCH setups are available yet. "
                "The rotating deep-score sweep is still building coverage or no current bases meet the rules."
            )
        else:
            ready_count = int(
                (accumulation_opportunities["Accumulation status"] == "ACCUMULATE").sum()
            )
            watch_count = int(
                (accumulation_opportunities["Accumulation status"] == "WATCH").sum()
            )
            a1, a2, a3 = st.columns(3)
            a1.metric("READY", ready_count)
            a2.metric("WATCH", watch_count)
            a3.metric(
                "Deep-score coverage",
                int(pipeline_manifest.get("deep_scores_current", 0) or 0),
            )

            accumulation_filter = st.radio(
                "Show",
                ["Best opportunities", "READY", "WATCH", "All"],
                horizontal=True,
                key="crypto_opportunity_accumulation_filter",
            )
            shown_acc = accumulation_opportunities.copy()
            if accumulation_filter == "READY":
                shown_acc = shown_acc[
                    shown_acc["Accumulation status"] == "ACCUMULATE"
                ]
            elif accumulation_filter == "WATCH":
                shown_acc = shown_acc[
                    shown_acc["Accumulation status"] == "WATCH"
                ]
            elif accumulation_filter == "Best opportunities":
                shown_acc = shown_acc.head(25)

            accumulation_cols = [
                "Accumulation status", "Opportunity stage", "Base", "Exchange", "Accumulation score",
                "Price", "Accumulation low", "Accumulation high",
                "In accumulation zone", "Coin trend", "RS vs BTC %", "RSI",
                "Execution venues", "Execution liquidity pass", "Execution reason",
                "Cross-exchange quote volume", "Kraken available", "Crypto.com available",
                "4Y cycle position %", "Project freshness", "deep_scored_at",
            ]
            visible_acc_cols = [
                col for col in accumulation_cols if col in shown_acc.columns
            ]
            st.dataframe(
                shown_acc[visible_acc_cols],
                hide_index=True,
                use_container_width=True,
            )
            st.caption(
                "Accumulation is a separate strategy from Swing. READY requires the confirmed base/zone rules; "
                "WATCH means the longer-term structure is developing."
            )

    with st.expander("Background scan coverage", expanded=False):
        manifest = pipeline_state.get("manifest", {})
        active = pipeline_state.get("active", pd.DataFrame())
        discovery = pipeline_state.get("discovery", pd.DataFrame())
        if manifest:
            st.write(
                f"Eligible coins: **{int(manifest.get('unique_eligible_coins', 0) or 0)}** · "
                f"Discovery coverage: **{float(manifest.get('discovery_coverage_pct', 0) or 0):.1f}%** · "
                f"Active fast-monitor candidates: **{int(manifest.get('active_candidates', 0) or 0)}** · "
                f"Deep-scored: **{int(manifest.get('deep_scores_current', 0) or 0)}**"
            )
        if not active.empty:
            st.caption("The active monitor is the fast technical discovery layer feeding repeated deep analysis.")
            cols = [
                col for col in [
                    "Monitor state", "Base", "Exchange", "Current price",
                    "Live distance to resistance %", "discovery_rank",
                    "rs_vs_btc_48h_pct", "24h quote volume",
                ]
                if col in active.columns
            ]
            st.dataframe(active[cols].head(50), hide_index=True, use_container_width=True)


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
            trade_decision = crypto_trade_decision(qa_result, macro_now, cfg.score_threshold)
            accumulation_decision = crypto_accumulation_decision(qa_result)
            accumulation_action = accumulation_decision["action"]
            accumulation_reason = accumulation_decision["reason"]

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

with tab_crypto_portfolio:
    st.subheader("Portfolio Review")
    st.caption(
        "Track your crypto holdings and review each one against the same current Swing and Accumulation rules. "
        "No exchange connection is required."
    )

    uploaded_crypto_portfolio = st.file_uploader(
        "Import holdings CSV (optional)",
        type=["csv"],
        key="crypto_portfolio_csv_upload",
        help="Required columns: Coin, Quantity and Average cost.",
    )
    crypto_portfolio_seed = pd.DataFrame([
        {"Coin": "", "Quantity": 0.0, "Average cost": 0.0},
    ])
    if uploaded_crypto_portfolio is not None:
        try:
            imported = pd.read_csv(uploaded_crypto_portfolio)
            aliases = {
                "coin": "Coin",
                "symbol": "Coin",
                "ticker": "Coin",
                "quantity": "Quantity",
                "amount": "Quantity",
                "average cost": "Average cost",
                "average_cost": "Average cost",
                "avg cost": "Average cost",
                "avg_cost": "Average cost",
            }
            imported = imported.rename(
                columns={
                    column: aliases.get(str(column).strip().lower(), column)
                    for column in imported.columns
                }
            )
            missing = {"Coin", "Quantity", "Average cost"} - set(imported.columns)
            if missing:
                raise ValueError("missing columns: " + ", ".join(sorted(missing)))
            crypto_portfolio_seed = imported[["Coin", "Quantity", "Average cost"]].copy()
        except Exception as exc:
            st.error(f"The portfolio CSV could not be loaded: {exc}")

    edited_crypto_holdings = st.data_editor(
        crypto_portfolio_seed,
        num_rows="dynamic",
        hide_index=True,
        use_container_width=True,
        key="crypto_portfolio_editor",
        column_config={
            "Coin": st.column_config.TextColumn(
                "Coin",
                help="Use a coin ticker such as BTC, ETH, HYPE or NEAR.",
            ),
            "Quantity": st.column_config.NumberColumn("Quantity", min_value=0.0, format="%.8f"),
            "Average cost": st.column_config.NumberColumn(
                "Average cost",
                min_value=0.0,
                format="%.8f",
                help="Your average cost per coin in USDT-equivalent quoted units.",
            ),
        },
    )

    pc1, pc2 = st.columns(2)
    with pc1:
        review_crypto_portfolio = st.button(
            "Review Portfolio",
            type="primary",
            use_container_width=True,
            key="review_crypto_portfolio",
        )
    with pc2:
        st.download_button(
            "Download Holdings CSV",
            data=edited_crypto_holdings.to_csv(index=False).encode("utf-8"),
            file_name="crypto_portfolio_holdings.csv",
            mime="text/csv",
            use_container_width=True,
            key="download_crypto_portfolio",
        )

    if review_crypto_portfolio:
        holdings = edited_crypto_holdings.copy()
        holdings["Coin"] = holdings["Coin"].fillna("").astype(str).str.strip().str.upper()
        holdings["Quantity"] = pd.to_numeric(holdings["Quantity"], errors="coerce").fillna(0.0)
        holdings["Average cost"] = pd.to_numeric(holdings["Average cost"], errors="coerce").fillna(0.0)
        holdings = holdings[(holdings["Coin"] != "") & (holdings["Quantity"] > 0)].head(20)

        if holdings.empty:
            st.info("Add at least one holding with a quantity above zero.")
        else:
            portfolio_rows = []
            portfolio_errors = []
            with st.spinner("Refreshing portfolio coins against the current crypto rules…"):
                for _, holding in holdings.iterrows():
                    coin = holding["Coin"]
                    try:
                        symbol, result, _raw = asyncio.run(analyse_individual_coin(cfg, coin))
                        result = dict(result or {})
                        result["symbol"] = symbol
                        trade_decision = crypto_trade_decision(result, macro, cfg.score_threshold)
                        acc_decision = crypto_accumulation_decision(result)
                        acc_action = acc_decision["action"]
                        price = _safe_float(result.get("price"), np.nan)
                        qty = float(holding["Quantity"])
                        avg = float(holding["Average cost"])
                        value = price * qty if math.isfinite(price) else np.nan
                        pnl_pct = ((price / avg) - 1) * 100 if math.isfinite(price) and avg > 0 else np.nan
                        portfolio_rows.append({
                            "Coin": coin,
                            "Exchange": exchange_name,
                            "Quantity": qty,
                            "Average cost": avg,
                            "Current price": price,
                            "Value": value,
                            "P/L %": pnl_pct,
                            "Swing": trade_decision["action"],
                            "Swing score": result.get("score", np.nan),
                            "Accumulation": acc_action,
                            "Accumulation score": result.get("bottom_score", np.nan),
                            "RS vs BTC %": result.get("rs_vs_btc_pct", np.nan),
                        })
                    except Exception as exc:
                        portfolio_errors.append(f"{coin}: {type(exc).__name__}: {exc}")

            portfolio_df = pd.DataFrame(portfolio_rows)
            if not portfolio_df.empty:
                total_value = pd.to_numeric(portfolio_df["Value"], errors="coerce").sum(min_count=1)
                total_cost = sum(
                    float(row["Quantity"]) * float(row["Average cost"])
                    for _, row in holdings.iterrows()
                    if float(row["Average cost"]) > 0
                )
                total_pnl = (
                    (total_value / total_cost - 1) * 100
                    if pd.notna(total_value) and total_cost > 0 else np.nan
                )
                pm1, pm2, pm3 = st.columns(3)
                pm1.metric("Portfolio value", "—" if pd.isna(total_value) else f"{total_value:,.2f} USDT")
                pm2.metric("Cost basis", f"{total_cost:,.2f} USDT" if total_cost > 0 else "—")
                pm3.metric("P/L", "—" if pd.isna(total_pnl) else f"{total_pnl:+.1f}%")
                st.dataframe(portfolio_df, hide_index=True, use_container_width=True)
            if portfolio_errors:
                with st.expander(f"{len(portfolio_errors)} portfolio data warning(s)"):
                    st.code("\n".join(portfolio_errors))


with tab_crypto_advanced:
    st.markdown("### Advanced Trade")
    st.caption("Deep Swing analysis, live refresh controls, charts and technical evidence. Home and Opportunities are fed automatically by the scheduled background rule engine.")

    st.subheader("Macro liquidity regime")
    macro_refresh_col, macro_status_col = st.columns([1, 4])
    with macro_refresh_col:
        refresh_macro_now = st.button(
            "Refresh macro now",
            use_container_width=True,
            key="refresh_crypto_macro",
        )
    with macro_status_col:
        macro_age = snapshot_age_minutes(macro)
        if math.isfinite(macro_age):
            st.caption(f"Macro snapshot age: {macro_age:.0f} minutes.")
        else:
            st.caption("Macro snapshot is still initialising. It is context only and does not block technical BUY signals.")

    if refresh_macro_now:
        with st.spinner("Refreshing macro-liquidity inputs…"):
            refreshed_macro = macro_liquidity_regime()
            refreshed_macro["generated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            st.session_state.macro_liquidity_override = refreshed_macro
        st.rerun()

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
                "Macro liquidity is currently a headwind. It is shown as context and does not override an otherwise valid technical Swing setup."
            )
    else:
        st.info(
            "Macro-liquidity data is currently incomplete. Swing decisions remain technical-first."
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

    @st.fragment
    def live_scan():
        # The scheduled all-market rule engine is the primary source for Home and Opportunities.
        # Advanced Trade keeps an optional manual refresh for on-demand inspection.
        # The prepared Crypto pipeline handles background freshness and opportunity feeds.
        # Do not launch the expensive on-demand scanner from a hidden Streamlit tab:
        # st.tabs renders every tab, so doing that can make Crypto Home appear blank
        # while a full market scan runs. The on-demand refresh remains manual.
        should_scan = bool(manual_scan)
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
        df = attach_prepared_execution_columns(df)

        technical_swing_setups = df[
            (df["Trade verdict"] == "QUALIFIES — 30%+ GROSS TARGET")
            & (df["Score"] >= cfg.score_threshold)
        ].copy().sort_values("Score", ascending=False)
        rs_qualified_setups = technical_swing_setups[
            (technical_swing_setups["Coin"] == "BTC")
            | (technical_swing_setups["RS vs BTC %"] > 0)
        ].copy()
        execution_qualified_setups = rs_qualified_setups[
            rs_qualified_setups["Execution liquidity pass"] == True
        ].copy()
        # Technical quality drives the setup; personal BUY also requires execution
        # safety on Kraken/Crypto.com and no latest completed 4h rejection candle.
        candle_qualified_setups = execution_qualified_setups[
            execution_qualified_setups["Candle caution"] != "CAUTION"
        ].copy()
        swing_setups = candle_qualified_setups.copy()
        accumulation_setups = df[
            (df["Accumulation verdict"] == "ACCUMULATION READY")
            & (df["Execution liquidity pass"] == True)
        ].copy().sort_values("Accumulation score", ascending=False)

        # Tables retain potential candidates even when no actionable setups exist.
        swing_candidates = df.copy()
        swing_candidates["Status"] = np.where(
            swing_candidates["Symbol"].isin(swing_setups["Symbol"]), "BUY", "WAIT"
        )
        swing_candidates["Macro regime"] = macro_now.get("regime", "DATA LIMITED")
        swing_candidates["Macro score"] = macro_now.get("score", np.nan)
        def swing_candidate_reason(row: pd.Series) -> str:
            is_buy = row["Status"] == "BUY"
            warnings = []
            if row.get("Tokenomics gate") != "PASS":
                warnings.append("tokenomics risk/unknown")
            if row.get("Major CEX gate") != "PASS":
                warnings.append("limited major-CEX breadth")
            if row.get("Context confidence") == "LOW":
                warnings.append("low context confidence")
            if row.get("Known event risk") == "HIGH":
                warnings.append("known event risk")
            if not macro_now.get("allows_new_swing_risk", True):
                warnings.append(f"macro {macro_now.get('regime', 'DATA LIMITED')}")

            if is_buy:
                base = (
                    f"Technical BUY: score {row['Score']:.1f} >= {cfg.score_threshold}, "
                    "30%+ target rule passes, RS vs BTC passes and no 4h rejection-candle gate."
                )
                return base + ((" Context warnings: " + ", ".join(warnings) + ".") if warnings else "")

            reasons = []
            if row["Score"] < cfg.score_threshold:
                reasons.append(f"score {row['Score']:.1f} below {cfg.score_threshold}")
            if (
                row["Symbol"] in set(technical_swing_setups["Symbol"])
                and row["Coin"] != "BTC"
                and row["RS vs BTC %"] <= 0
            ):
                reasons.append("not beating BTC over the 48h RS window")
            if (
                row["Symbol"] in set(rs_qualified_setups["Symbol"])
                and not _boolish(row.get("Execution liquidity pass", False))
            ):
                reasons.append(str(row.get("Execution reason") or "execution liquidity not confirmed on Kraken/Crypto.com"))
            if (
                row["Symbol"] in set(execution_qualified_setups["Symbol"])
                and row.get("Candle caution") == "CAUTION"
            ):
                reasons.append("latest completed 4h candle shows rejection")
            if row["Trade verdict"] != "QUALIFIES — 30%+ GROSS TARGET":
                reasons.append(str(row["Trade reason"]))
            if not reasons:
                reasons.append("technical entry not ready")
            suffix = (" Context notes: " + ", ".join(warnings) + ".") if warnings else ""
            return "WAIT: " + "; ".join(reasons) + "." + suffix

        swing_candidates["Reason"] = swing_candidates.apply(swing_candidate_reason, axis=1)
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
                "Meets accumulation rules and Kraken/Crypto.com execution liquidity passes"
                if row["Status"] == "ACCUMULATE"
                else (
                    (f"Base score {row['Accumulation score']:.1f} below 70. "
                     if row["Accumulation score"] < 70 else "")
                    + ("Price outside the confirmed daily base accumulation zone. "
                       if not row["In accumulation zone"] else "")
                    + (
                        str(row.get("Execution reason") or "Execution liquidity not confirmed.")
                        if (
                            row["Accumulation verdict"] == "ACCUMULATION READY"
                            and not _boolish(row.get("Execution liquidity pass", False))
                        )
                        else ""
                    )
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
                f"All {len(df)} analysed coins are shown. BUY is technical-first: score "
                f"{cfg.score_threshold}+, the approved pre-breakout shape, a credible 30%+ gross target, "
                "positive RS vs BTC for altcoins, and no latest-4h rejection-candle gate. "
                "Tokenomics, major-CEX breadth, catalysts/event risk, category leadership and macro "
                "remain visible as context warnings/confidence only; they do not rescue a poor chart "
                "and do not veto an otherwise valid technical BUY. Liquidity/tradeability is already "
                "handled upstream by the eligible-market universe filters. WAIT candidates remain visible "
                "with their technical reasons. Green = preferred, amber = borderline, red = weak or extended."
            )
            if swing_setups.empty:
                rs_blocked = len(technical_swing_setups) - len(rs_qualified_setups)
                execution_blocked = len(rs_qualified_setups) - len(execution_qualified_setups)
                candle_blocked = len(execution_qualified_setups) - len(candle_qualified_setups)
                if rs_blocked > 0:
                    st.info(
                        f"{rs_blocked} technical setup(s) currently qualify on score/target but remain "
                        "WAIT because the altcoin is not beating BTC over the 48h RS window."
                    )
                elif execution_blocked > 0:
                    st.info(
                        f"{execution_blocked} technically-qualified setup(s) remain WAIT because "
                        "Kraken/Crypto.com execution liquidity has not passed."
                    )
                elif candle_blocked > 0:
                    st.info(
                        f"{candle_blocked} otherwise-qualified setup(s) remain WAIT because the "
                        "latest completed 4h candle is a red shooting star / rejection candle."
                    )
                else:
                    st.info(
                        f"No swing-trade setup currently meets the {cfg.score_threshold}+ "
                        "technical BUY rules, 30% gross-target requirement and execution-liquidity gate."
                    )
            swing_cols = [
                "Coin", "Status", "Context confidence", "Known event risk",
                "Price", "Entry Price", "Exit / Stop", "Price Target", "ROI %", "R:R",
                "Score", "Signal agreement %", "Conflict count", "Non-TA confirmations",
                "Pattern", "Triangle score", "Triangle touches", "Triangle compression %",
                "Candle caution", "Last 4h candle",
                "Execution venues", "Execution liquidity pass", "Execution reason",
                "Cross-exchange quote volume", "Kraken available", "Crypto.com available",
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

        # A successful scheduled/manual scan updates session state inside this
        # fragment. Rerun the whole app once so Home and Opportunities immediately
        # reflect the same newly-scanned dataset rather than the previous snapshot.
        if should_scan:
            st.rerun()

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
    - **Known event risk:** explicit unlock/vesting-style events are shown as HIGH context risk; near-dated catalysts are shown as event-volatility caution. They are context warnings rather than automatic swing vetoes unless a separate hard liquidity/tradeability rule fails.
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

    - **20 raw pts — Structure:** higher lows, resistance interaction and 4h EMA structure. The number of resistance tests is measured and scored, not used as a binary pass/fail rule.
    - **15 raw pts — Compression:** ATR contraction and a tightening trading range.
    - **15 raw pts — Volume:** volume dries up during the coil, with preference for stronger volume on up-bars.
    - **15 raw pts — Relative strength:** coin return versus BTC over recent 4h windows.
    - **10 raw pts — Momentum:** RSI is treated as a broad momentum/extension feature rather than requiring a fixed 52–64 band; improving MACD histogram adds confirmation.
    - **5 raw pts — OBV:** accumulation proxy via rising on-balance volume.
    - **5 raw pts — Daily context:** daily trend constructive without being extremely stretched.
    - **10 raw pts — Entry / R:R:** distance to resistance and projected reward versus invalidation risk. R:R contributes to ranking but a universal 2:1 threshold is not assumed to be proven.

    Those weights total 95 raw technical points, which the app normalises to a genuine **0–100 technical score**. Swing decisions remain overwhelmingly technical. Context such as macro conditions, catalysts and token events is secondary and must never rescue a poor chart. A personal BUY also requires the separate execution-safety gate: at least **$5m combined market liquidity**, at least **$1m USD-like 24h liquidity on Kraken or Crypto.com**, and availability on at least one of those two execution platforms. Discovery uses lower thresholds so this execution rule cannot hide early setups.

    **Retest rule:** a bullish breakout retest from above and a bounce into broken support from below are treated as different structures. A first retest of major broken support from underneath is a caution / potential exit-liquidity zone, not an automatic long entry. A reclaim becomes stronger only after price closes back above the level, shows acceptance/follow-through and ideally holds a later retest.

    **Evidence rule:** RSI bands, resistance-test count, Fibonacci confluence, exact R:R thresholds, Volume Profile behaviour and first-versus-later retest behaviour are hypotheses to measure and backtest. They should only become hard gates if historical evidence shows that they materially improve expectancy.

    #### ACCUMULATE score — bottoming quality

    - **30 pts — Base proximity:** price is near its 60-day low.
    - **25 pts — Higher lows:** the recent daily low is improving versus the prior base.
    - **20 pts — Trend flattening:** the daily 20 EMA is stabilising or turning up.
    - **15 pts — RSI recovery:** daily momentum is recovering from a constructive level.
    - **10 pts — Daily OBV:** volume flow is improving.

    ACCUMULATE also requires the score to reach 70, price to be inside the confirmed daily base zone, and the same Kraken/Crypto.com execution-safety check to pass. A technically-ready base that fails execution safety remains WATCH rather than disappearing from discovery.

    #### Macro-liquidity regime — primary cycle framework

    The scanner no longer assumes crypto must follow a fixed four-year cycle. The macro-liquidity regime uses **US M2 (20 pts), Fed net liquidity (15), Chicago Fed financial conditions (20), 10Y real-yield direction (15), the broad US dollar (15), and stablecoin supply growth (15)**. Weak macro is displayed as context/headwind; it does not override an otherwise valid technical setup.
            """
        )

    st.caption("Trading tool only — not financial advice. Crypto can gap through technical levels; always size risk independently of the score.")


with tab_crypto_advanced_accumulation:
    st.markdown("### Advanced Accumulation")
    st.caption(
        "Longer-term base and accumulation analysis. This strategy is separate from Swing and is fed automatically by the rotating background deep-score sweep."
    )

    acc_feed = prepared_accumulation_feed.copy()
    aa1, aa2, aa3 = st.columns(3)
    aa1.metric(
        "READY",
        int((acc_feed["Accumulation status"] == "ACCUMULATE").sum())
        if not acc_feed.empty and "Accumulation status" in acc_feed.columns else 0,
    )
    aa2.metric(
        "WATCH",
        int((acc_feed["Accumulation status"] == "WATCH").sum())
        if not acc_feed.empty and "Accumulation status" in acc_feed.columns else 0,
    )
    aa3.metric("Deep-scored universe", int(pipeline_manifest.get("deep_scores_current", 0) or 0))

    with st.expander("Accumulation rule framework", expanded=False):
        st.markdown(
            """
- **Separate from Swing:** a coin can be a Swing BUY and an Accumulation PASS, or vice versa.
- **Base quality:** price location near a meaningful daily base, improving higher lows and trend flattening.
- **Momentum recovery:** RSI recovery is supporting evidence rather than a stand-alone reason to accumulate.
- **Volume flow:** daily OBV/participation should improve rather than confirm continued distribution.
- **Zone discipline:** READY requires the model's confirmed accumulation zone; being far below an old ATH is not enough.
- **Long-range context:** weekly support and long-range position are reference context, not automatic buy triggers.
            """
        )

    if acc_feed.empty:
        st.info("No prepared Accumulation READY/WATCH setups are available yet.")
    else:
        acc_cols = [
            "Accumulation status", "Base", "Exchange", "Accumulation score",
            "Price", "Accumulation low", "Accumulation high", "In accumulation zone",
            "Coin trend", "RS vs BTC %", "RSI", "4Y cycle position %",
            "Project freshness", "deep_scored_at",
        ]
        st.dataframe(
            acc_feed[[col for col in acc_cols if col in acc_feed.columns]].head(100),
            hide_index=True,
            use_container_width=True,
        )

    st.markdown("#### Analyse an accumulation candidate")
    ac1, ac2 = st.columns([4, 1])
    with ac1:
        advanced_acc_coin = st.text_input(
            "Coin",
            value="",
            placeholder="e.g. BTC, ETH, NEAR",
            key="advanced_accumulation_coin",
        )
    with ac2:
        run_advanced_acc = st.button(
            "Analyse",
            type="primary",
            use_container_width=True,
            key="run_advanced_accumulation",
        )

    if run_advanced_acc and advanced_acc_coin.strip():
        with st.spinner(f"Analysing {advanced_acc_coin.strip().upper()} accumulation structure…"):
            try:
                acc_symbol, acc_result, _ = asyncio.run(
                    analyse_individual_coin(cfg, advanced_acc_coin.strip())
                )
                acc_result = dict(acc_result or {})
                acc_decision = crypto_accumulation_decision(acc_result)
                action = acc_decision["action"]
                reason = acc_decision["reason"]
                render_crypto_decision_card("ACCUMULATION DECISION", action, reason)
                am1, am2, am3, am4 = st.columns(4)
                am1.metric("Accumulation score", f"{acc_result.get('bottom_score', 0):.1f}/100")
                am2.metric("Current price", fmt_price(acc_result.get("price", np.nan)))
                am3.metric(
                    "Zone",
                    f"{fmt_price(acc_result.get('accumulation_low', np.nan))} – {fmt_price(acc_result.get('accumulation_high', np.nan))}",
                )
                cycle_pos = _safe_float(acc_result.get("cycle_position_pct"), np.nan)
                am4.metric(
                    "Long-range position",
                    "—" if not math.isfinite(cycle_pos) else f"{cycle_pos:.1f}%",
                )
                comps = acc_result.get("bottom_components", {})
                if comps:
                    comp_df = pd.DataFrame({
                        "Factor": list(comps.keys()),
                        "Points": list(comps.values()),
                    })
                    st.bar_chart(comp_df.set_index("Factor"), horizontal=True)
            except Exception as exc:
                st.error(f"{type(exc).__name__}: {exc}")


with tab_crypto_research:
    st.markdown("### Pre-Pump Research Lab")
    st.caption("Optional research tool for testing which technical features historically appeared before large moves.")
    st.caption(
        "Research the technical fingerprint that existed before large moves. "
        "This is an exploratory point-in-time event study: it compares successful future moves "
        "with failed lookalikes and does not assume RSI, Fibonacci, resistance-test count or R:R are predictive."
    )

    rr1, rr2, rr3 = st.columns(3)
    with rr1:
        research_exchange_name = st.selectbox(
            "Research exchange",
            list(EXCHANGES.keys()),
            index=list(EXCHANGES.values()).index(cfg.exchange_id) if cfg.exchange_id in EXCHANGES.values() else 0,
            key="prepump_exchange",
        )
    with rr2:
        research_base = st.text_input(
            "Coin",
            value="HYPE",
            key="prepump_coin",
            help="Enter the base ticker only, for example HYPE, NEAR or VVV.",
        ).strip().upper()
    with rr3:
        research_target = st.selectbox(
            "Target move",
            [20, 30, 40, 50],
            index=1,
            format_func=lambda x: f"+{x}%",
            key="prepump_target",
        )

    rs1, rs2, rs3 = st.columns(3)
    with rs1:
        research_horizon = st.selectbox(
            "Forward window",
            [7, 14, 30, 45, 60],
            index=2,
            format_func=lambda x: f"{x} days",
            key="prepump_horizon",
        )
    with rs2:
        research_adverse = st.selectbox(
            "Maximum adverse move before target",
            [8, 10, 12, 15, 20, 25],
            index=3,
            format_func=lambda x: f"-{x}%",
            key="prepump_adverse",
            help="A setup is not counted as a clean winner if this downside level is breached before the target.",
        )
    with rs3:
        snapshot_mode = st.selectbox(
            "Case-study run",
            ["Latest qualifying run", "Strongest qualifying run"],
            key="prepump_snapshot_mode",
        )

    st.caption(
        "Primary question: what was measurable before the move? The research engine uses only technical/market data: "
        "relative strength vs BTC, structure, resistance proximity/tests, compression, volume, RSI, moving averages, "
        "OBV and price location. Context/catalysts are intentionally excluded from this 95%-technical research layer."
    )

    if st.button("Run pre-pump research", type="primary", key="run_prepump_research"):
        research_exchange_id = EXCHANGES[research_exchange_name]
        research_symbol = f"{research_base}/{cfg.quote}"
        with st.spinner(f"Replaying {research_symbol} history and comparing winners with failures…"):
            try:
                prepump_coin_d, prepump_btc_d = asyncio.run(
                    fetch_prepump_research_data(research_exchange_id, research_symbol, cfg.quote)
                )
                feature_frame = build_feature_frame(prepump_coin_d, prepump_btc_d)
                research_events = label_forward_outcomes(
                    feature_frame,
                    target_pct=float(research_target),
                    horizon_days=int(research_horizon),
                    adverse_limit_pct=float(research_adverse),
                )
                summary = research_summary(research_events)
                comparison = feature_comparison(research_events)
                snapshots, snap_meta = snapshot_table(
                    feature_frame,
                    research_events,
                    mode="strongest" if snapshot_mode.startswith("Strongest") else "latest",
                )

                st.session_state.prepump_research = {
                    "symbol": research_symbol,
                    "exchange": research_exchange_name,
                    "target": research_target,
                    "horizon": research_horizon,
                    "adverse": research_adverse,
                    "events": research_events,
                    "comparison": comparison,
                    "snapshots": snapshots,
                    "snap_meta": snap_meta,
                    "summary": summary,
                }
            except Exception as e:
                st.session_state.prepump_research = None
                st.error(f"Pre-pump research failed: {type(e).__name__}: {e}")

    prepump_result = st.session_state.get("prepump_research")
    if prepump_result:
        ps = prepump_result["summary"]
        p1, p2, p3, p4, p5 = st.columns(5)
        p1.metric("Daily setup samples", ps["samples"])
        p2.metric("Clean target hits", ps["winners"])
        p3.metric(
            "Observed hit rate",
            f"{ps['hit_rate_pct']:.1f}%" if pd.notna(ps["hit_rate_pct"]) else "N/A",
        )
        p4.metric(
            "Median MFE",
            f"{ps['median_mfe_pct']:.1f}%" if pd.notna(ps["median_mfe_pct"]) else "N/A",
        )
        p5.metric(
            "Median MAE",
            f"{ps['median_mae_pct']:.1f}%" if pd.notna(ps["median_mae_pct"]) else "N/A",
        )

        st.caption(
            f"{prepump_result['symbol']} on {prepump_result['exchange']} · "
            f"winner = +{prepump_result['target']}% within {prepump_result['horizon']} days "
            f"before a -{prepump_result['adverse']}% adverse breach. "
            "Daily windows overlap, so this is exploratory evidence rather than an independent-sample statistical proof."
        )

        comparison = prepump_result["comparison"]
        st.markdown("#### Which technical features separated winners from failures?")
        if comparison is None or comparison.empty:
            st.info("Not enough winner/failure observations in the available history for a useful comparison.")
        else:
            st.dataframe(comparison, use_container_width=True, hide_index=True)
            st.caption(
                "Standardised separation shows how far the winner median differed from the failure median after scaling "
                "by the feature's overall variation. Large absolute values are more interesting, but they are not automatically causal."
            )

        snapshots = prepump_result["snapshots"]
        st.markdown("#### Before a qualifying run")
        if snapshots is None or snapshots.empty:
            st.info("No clean qualifying run was found under these settings.")
        else:
            meta = prepump_result["snap_meta"]
            sm1, sm2, sm3, sm4 = st.columns(4)
            sm1.metric("Setup anchor", pd.Timestamp(meta["anchor_date"]).date().isoformat())
            sm2.metric("Forward MFE", f"{meta['mfe_pct']:.1f}%")
            sm3.metric("Forward MAE", f"{meta['mae_pct']:.1f}%")
            sm4.metric(
                "Days to target",
                f"{meta['time_to_target_days']:.0f}" if pd.notna(meta["time_to_target_days"]) else "N/A",
            )
            st.dataframe(snapshots, use_container_width=True, hide_index=True)
            st.caption(
                "T-30/T-14/T-7/T-3/T-1 are point-in-time snapshots before the selected setup anchor. "
                "T0 is the setup date itself; the outcome columns are never used to calculate the earlier technical features."
            )

        events = prepump_result["events"]
        st.markdown("#### Historical setup outcomes")
        if events is not None and not events.empty:
            event_cols = [
                "date", "success", "time_to_target_days", "mfe_pct", "mae_pct",
                "rs_btc_7d_pct", "rs_btc_30d_pct", "rsi14", "atr_compression",
                "bb_width_percentile_60d", "volume_ratio_5_20", "range_compression_10_20",
                "distance_to_resistance_pct", "resistance_tests_30d", "higher_low_pct",
                "close_position_30d_pct", "trend_regime",
            ]
            st.dataframe(
                events[event_cols].sort_values("date", ascending=False),
                use_container_width=True,
                hide_index=True,
            )
            st.download_button(
                "Download research results CSV",
                data=events.to_csv(index=False).encode("utf-8"),
                file_name=f"{prepump_result['symbol'].replace('/', '_')}_prepump_research.csv",
                mime="text/csv",
                key="download_prepump_csv",
            )

    st.info(
        "Use HYPE, NEAR and VVV as the first sanity-check cases. Send me the tables or CSV results and we can see "
        "whether the screener was detecting the technical fingerprint early enough before changing any scoring weights."
    )


