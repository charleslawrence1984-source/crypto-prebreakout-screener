from __future__ import annotations

import math
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
import requests


def _safe(value, default=np.nan):
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except Exception:
        return default


def fetch_coingecko_tokenomics(limit_pages: int = 2) -> Dict[str, Dict]:
    """Bulk market/tokenomics snapshot keyed by uppercase ticker."""
    rows: List[dict] = []
    for page in range(1, limit_pages + 1):
        try:
            response = requests.get(
                "https://api.coingecko.com/api/v3/coins/markets",
                params={
                    "vs_currency": "usd",
                    "order": "market_cap_desc",
                    "per_page": 250,
                    "page": page,
                    "sparkline": "false",
                },
                headers={"User-Agent": "cl-signal-crypto/1.0"},
                timeout=20,
            )
            response.raise_for_status()
            payload = response.json() or []
            if isinstance(payload, list):
                rows.extend(payload)
        except Exception:
            continue

    by_symbol: Dict[str, Dict] = {}
    for item in rows:
        symbol = str(item.get("symbol") or "").upper().strip()
        if not symbol:
            continue
        existing = by_symbol.get(symbol)
        rank = item.get("market_cap_rank") or 10**9
        existing_rank = (existing.get("market_cap_rank") or 10**9) if existing else 10**9
        if existing is None or rank < existing_rank:
            by_symbol[symbol] = item
    return by_symbol


def fetch_coingecko_category_leaders() -> Dict[str, List[str]]:
    """CoinGecko coin-id -> category names where it is currently top-3."""
    try:
        response = requests.get(
            "https://api.coingecko.com/api/v3/coins/categories",
            headers={"User-Agent": "cl-signal-crypto/1.0"},
            timeout=20,
        )
        response.raise_for_status()
        rows = response.json() or []
    except Exception:
        return {}

    leaders: Dict[str, List[str]] = {}
    for row in rows if isinstance(rows, list) else []:
        category = str(row.get("name") or "").strip()
        for coin_id in row.get("top_3_coins_id") or []:
            coin_id = str(coin_id or "").strip()
            if coin_id:
                leaders.setdefault(coin_id, [])
                if category and category not in leaders[coin_id]:
                    leaders[coin_id].append(category)
    return leaders


def tokenomics_context(
    base: str,
    snapshot: Dict[str, Dict],
    category_leaders: Optional[Dict[str, List[str]]] = None,
) -> Dict:
    base = str(base or "").upper().strip()
    item = snapshot.get(base) or {}

    circulating = _safe(item.get("circulating_supply"))
    total = _safe(item.get("total_supply"))
    max_supply = _safe(item.get("max_supply"))
    market_cap = _safe(item.get("market_cap"))
    fdv = _safe(item.get("fully_diluted_valuation"))

    denominator = total if math.isfinite(total) and total > 0 else max_supply
    circulating_pct = (
        circulating / denominator * 100
        if math.isfinite(circulating)
        and math.isfinite(denominator)
        and denominator > 0
        else np.nan
    )
    fdv_mcap = (
        fdv / market_cap
        if math.isfinite(fdv) and fdv > 0 and math.isfinite(market_cap) and market_cap > 0
        else np.nan
    )

    coin_id = str(item.get("id") or "")
    leader_categories = (
        (category_leaders or {}).get(coin_id, [])
        if coin_id else []
    )
    category_leader = (
        "TOP 3" if leader_categories
        else "NOT TOP 3" if category_leaders
        else "UNKNOWN"
    )

    # Conservative meme exception: only apply the user's 10% minimum when
    # CoinGecko category evidence explicitly identifies the asset as meme-related.
    meme_category = any(
        "meme" in category.lower()
        or "dog" in category.lower()
        or "cat" in category.lower()
        for category in leader_categories
    )
    minimum_circulating_pct = 10.0 if meme_category else 25.0

    if not item or not math.isfinite(circulating_pct):
        tokenomics_gate = "UNKNOWN"
    elif circulating_pct < minimum_circulating_pct:
        tokenomics_gate = "FAIL"
    elif math.isfinite(fdv_mcap) and fdv_mcap >= 4.0:
        tokenomics_gate = "FAIL"
    elif not math.isfinite(fdv_mcap):
        tokenomics_gate = "REVIEW"
    else:
        tokenomics_gate = "PASS"

    risks = []
    if math.isfinite(circulating_pct) and circulating_pct < minimum_circulating_pct:
        risks.append(
            f"Low float {circulating_pct:.1f}% < {minimum_circulating_pct:.0f}% minimum"
        )
    if math.isfinite(fdv_mcap) and fdv_mcap >= 4.0:
        risks.append("High FDV / market-cap ratio >=4x")
    if not item:
        risks.append("Tokenomics data unavailable")
    elif not math.isfinite(fdv_mcap):
        risks.append("FDV / market-cap needs review")

    return {
        "tokenomics_gate": tokenomics_gate,
        "circulating_pct": round(float(circulating_pct), 1) if math.isfinite(circulating_pct) else np.nan,
        "minimum_circulating_pct": minimum_circulating_pct,
        "fdv_mcap": round(float(fdv_mcap), 2) if math.isfinite(fdv_mcap) else np.nan,
        "market_cap": market_cap,
        "fdv": fdv,
        "coingecko_id": coin_id,
        "category_leader": category_leader,
        "leader_categories": ", ".join(leader_categories),
        "meme_supply_exception": bool(meme_category),
        "tokenomics_risks": "; ".join(risks),
        "unlock_review": "UNVERIFIED — specialist unlock/vesting data not scored",
    }


def _rs_component(technical: Dict) -> float:
    weighted = []
    for key, weight in [
        ("rs_vs_btc_30d_pct", 0.25),
        ("rs_vs_btc_90d_pct", 0.35),
        ("rs_vs_btc_180d_pct", 0.40),
    ]:
        value = _safe(technical.get(key))
        if math.isfinite(value):
            weighted.append((value, weight))
    if not weighted:
        return 0.40
    denominator = sum(weight for _, weight in weighted)
    rs_value = sum(value * weight for value, weight in weighted) / denominator
    if rs_value >= 15:
        return 1.0
    if rs_value >= 5:
        return 0.90
    if rs_value >= 0:
        return 0.80
    if rs_value >= -10:
        return 0.60
    if rs_value >= -25:
        return 0.35
    return 0.10


def _tokenomics_component(context: Dict) -> float:
    circulating = _safe(context.get("circulating_pct"))
    minimum = _safe(context.get("minimum_circulating_pct"), 25.0)
    fdv_mcap = _safe(context.get("fdv_mcap"))

    if math.isfinite(circulating):
        if circulating >= 70:
            circ_score = 1.0
        elif circulating >= 50:
            circ_score = 0.90
        elif circulating >= minimum:
            circ_score = 0.75
        else:
            circ_score = 0.0
    else:
        circ_score = 0.25

    if math.isfinite(fdv_mcap):
        if fdv_mcap <= 1.5:
            fdv_score = 1.0
        elif fdv_mcap <= 2.5:
            fdv_score = 0.80
        elif fdv_mcap < 4.0:
            fdv_score = 0.50
        else:
            fdv_score = 0.0
    else:
        fdv_score = 0.35

    return 0.60 * circ_score + 0.40 * fdv_score


def _exchange_component(context: Dict) -> float:
    count = int(_safe(context.get("major_venue_listing_count"), 0))
    checked = int(_safe(context.get("major_venues_checked"), 0))
    if checked <= 0:
        return 0.40
    if count >= 4:
        return 1.0
    if count == 3:
        return 0.85
    if count == 2:
        return 0.65
    if count == 1:
        return 0.35
    return 0.10


def _category_component(context: Dict) -> float:
    status = str(context.get("category_leader") or "UNKNOWN").upper()
    if status == "TOP 3":
        return 1.0
    if status == "NOT TOP 3":
        return 0.45
    return 0.40


def _freshness_component(technical: Dict) -> float:
    status = str(technical.get("project_freshness") or "UNKNOWN").upper()
    return {
        "NEW": 1.0,
        "RECENT": 0.85,
        "MATURE": 0.60,
        "LEGACY": 0.40,
        "UNKNOWN": 0.50,
    }.get(status, 0.50)


def score_accumulation(
    technical: Dict,
    context: Dict,
    execution_liquidity_pass: bool,
) -> Dict:
    """
    Full accumulation decision.

    40% confirmed base/structure
    20% tokenomics
    15% long-term RS vs BTC
    10% major-exchange legitimacy
    10% category leadership
     5% project freshness

    The 4Y price-range statistic remains reference-only and is not scored.
    """
    base_score = _safe(technical.get("bottom_score"), 0.0)
    base_component = max(0.0, min(base_score / 100.0, 1.0))
    tokenomics_component = _tokenomics_component(context)
    rs_component = _rs_component(technical)
    exchange_component = _exchange_component(context)
    category_component = _category_component(context)
    freshness_component = _freshness_component(technical)

    components = {
        "Base / structure": round(40 * base_component, 1),
        "Tokenomics": round(20 * tokenomics_component, 1),
        "RS vs BTC": round(15 * rs_component, 1),
        "Exchange legitimacy": round(10 * exchange_component, 1),
        "Category leadership": round(10 * category_component, 1),
        "Project freshness": round(5 * freshness_component, 1),
    }
    quality_score = round(float(sum(components.values())), 1)

    base_ready = (
        base_score >= 70
        and bool(technical.get("in_accumulation_zone"))
    )
    base_developing = (
        base_score >= 50
        or bool(technical.get("in_accumulation_zone"))
    )
    tokenomics_gate = str(context.get("tokenomics_gate") or "UNKNOWN").upper()
    tokenomics_pass = tokenomics_gate == "PASS"
    quality_pass = quality_score >= 70

    if base_ready and tokenomics_pass and quality_pass and execution_liquidity_pass:
        status = "ACCUMULATE"
        reason = (
            "Confirmed base, accumulation quality >=70, tokenomics PASS and "
            "Kraken/Crypto.com execution liquidity all pass."
        )
    elif base_ready:
        status = "QUALITY WATCH"
        blockers = []
        if not tokenomics_pass:
            blockers.append(f"tokenomics {tokenomics_gate}")
        if not quality_pass:
            blockers.append(f"quality score {quality_score:.1f} below 70")
        if not execution_liquidity_pass:
            blockers.append("execution liquidity not ready")
        reason = "Technical base is ready, but " + ", ".join(blockers or ["quality confirmation is incomplete"]) + "."
    elif base_developing and quality_score >= 55:
        status = "BASE DEVELOPING"
        reason = "Project/quality evidence is sufficient to monitor, but the technical accumulation base is not confirmed."
    else:
        status = "PASS"
        reason = "The current base and accumulation-quality evidence are not strong enough."

    return {
        "accumulation_model_status": status,
        "accumulation_quality_score": quality_score,
        "accumulation_quality_components": components,
        "accumulation_base_score": round(float(base_score), 1),
        "accumulation_base_ready": bool(base_ready),
        "accumulation_quality_pass": bool(quality_pass),
        "accumulation_tokenomics_pass": bool(tokenomics_pass),
        "accumulation_reason": reason,
        **context,
    }
