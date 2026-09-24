from __future__ import annotations

import math
from typing import Dict

import numpy as np


LAUNCH_MAX_AGE_HOURS = 2.0

# These are conservative engineering defaults for the live launch lane, not
# empirically validated universal laws. They should be calibrated from the
# separate launch-research dataset as outcomes accumulate.
LAUNCH_DEFAULTS = {
    "max_age_hours": LAUNCH_MAX_AGE_HOURS,
    "min_liquidity_usd": 10_000.0,
    "min_5m_volume_usd": 2_000.0,
    "min_5m_transactions": 20,
    "max_5m_rise_pct": 35.0,
    "max_5m_drop_pct": 30.0,
    "max_1h_rise_pct": 120.0,
    "strong_score": 70.0,
    "watch_score": 55.0,
}


def _safe(value, default=np.nan) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except Exception:
        return default


def _age_hours(pair_created_at, now_ms: float) -> float:
    created = _safe(pair_created_at)
    if not math.isfinite(created):
        return np.nan
    return max(0.0, (now_ms - created) / 3_600_000.0)


def score_launch_candidate(
    pair: Dict,
    *,
    now_ms: float,
    cfg: Dict | None = None,
) -> Dict:
    """
    Score a brand-new meme pool using only information appropriate to 0-2h age.

    The model intentionally avoids 24h maturity rules and structural OHLCV
    trade plans. It is an early-discovery / launch-watch model, not a BUY
    engine. Exact thresholds remain provisional pending launch backtesting.
    """
    rules = dict(LAUNCH_DEFAULTS)
    if cfg:
        rules.update(cfg)

    liq = _safe((pair.get("liquidity") or {}).get("usd"), 0.0)
    volume = pair.get("volume") or {}
    vol5 = _safe(volume.get("m5"), 0.0)
    vol1h = _safe(volume.get("h1"), 0.0)

    txns = pair.get("txns") or {}
    tx5 = txns.get("m5") or {}
    tx1h = txns.get("h1") or {}
    buys5 = _safe(tx5.get("buys"), 0.0)
    sells5 = _safe(tx5.get("sells"), 0.0)
    buys1h = _safe(tx1h.get("buys"), 0.0)
    sells1h = _safe(tx1h.get("sells"), 0.0)
    total5 = buys5 + sells5
    total1h = buys1h + sells1h
    buy_share5 = buys5 / total5 if total5 > 0 else 0.0
    buy_share1h = buys1h / total1h if total1h > 0 else 0.0

    change = pair.get("priceChange") or {}
    ch5 = _safe(change.get("m5"), 0.0)
    ch1 = _safe(change.get("h1"), 0.0)
    age_h = _age_hours(pair.get("pairCreatedAt"), now_ms)

    vol_liq_5m = vol5 / liq if liq > 0 else 0.0
    tx_rate_5m = total5 / 5.0

    hard_failures = []
    cautions = []

    if math.isfinite(age_h) and age_h > rules["max_age_hours"]:
        hard_failures.append("Older than launch lane")
    if liq < rules["min_liquidity_usd"]:
        hard_failures.append("Very low launch liquidity")
    if vol5 < rules["min_5m_volume_usd"]:
        hard_failures.append("Weak 5m participation")
    if total5 < rules["min_5m_transactions"]:
        hard_failures.append("Too few 5m transactions")
    if ch5 > rules["max_5m_rise_pct"] or ch1 > rules["max_1h_rise_pct"]:
        hard_failures.append("Vertical / chase-risk launch")
    if ch5 < -abs(rules["max_5m_drop_pct"]):
        hard_failures.append("Severe 5m price collapse")

    if buy_share5 > 0.85 and total5 >= 20:
        cautions.append("Extremely one-sided 5m flow")
    elif buy_share5 < 0.45 and total5 >= 20:
        cautions.append("Seller-heavy 5m flow")
    if vol_liq_5m > 2.0:
        cautions.append("5m turnover extremely high vs liquidity")
    if -abs(rules["max_5m_drop_pct"]) <= ch5 <= -20.0:
        cautions.append("Heavy 5m drawdown")
    under_five_minutes = math.isfinite(age_h) and age_h < (5.0 / 60.0)
    if under_five_minutes:
        cautions.append("Under 5 minutes old — data still forming")

    score = 0.0

    # 30 points — quality-adjusted participation.
    score += min(12.0, max(0.0, math.log10(max(vol5, 1.0) / 1_000.0 + 1.0) * 8.0))
    score += min(10.0, max(0.0, total5 / 150.0 * 10.0))
    score += min(8.0, max(0.0, tx_rate_5m / 20.0 * 8.0))

    # 20 points — liquidity / capacity.
    score += min(14.0, max(0.0, math.log10(max(liq, 1.0) / 5_000.0 + 1.0) * 9.0))
    if 0.05 <= vol_liq_5m <= 1.0:
        score += 6.0
    elif 0.02 <= vol_liq_5m <= 1.5:
        score += 4.0
    elif vol_liq_5m > 0:
        score += 1.0

    # 20 points — balanced but positive flow.
    if 0.55 <= buy_share5 <= 0.72:
        score += 14.0
    elif 0.50 <= buy_share5 < 0.55 or 0.72 < buy_share5 <= 0.80:
        score += 10.0
    elif 0.45 <= buy_share5 <= 0.85:
        score += 5.0
    if total1h > 0 and 0.52 <= buy_share1h <= 0.75:
        score += 6.0
    elif total1h > 0 and 0.48 <= buy_share1h <= 0.82:
        score += 3.0

    # 20 points — constructive early price response without rewarding verticality.
    if -5 <= ch5 <= 12:
        score += 10.0
    elif -12 <= ch5 <= 25:
        score += 6.0
    elif -20 <= ch5 <= rules["max_5m_rise_pct"]:
        score += 2.0
    if -10 <= ch1 <= 40:
        score += 10.0
    elif -25 <= ch1 <= 75:
        score += 5.0

    # 10 points — enough time to have observations, without penalising genuine launches.
    if math.isfinite(age_h):
        if 5 / 60 <= age_h <= 0.5:
            score += 10.0
        elif 0.5 < age_h <= 1.0:
            score += 8.0
        elif 1.0 < age_h <= rules["max_age_hours"]:
            score += 6.0
        elif age_h < 5 / 60:
            score += 2.0

    score = round(min(100.0, score), 1)
    gate_pass = not hard_failures

    decision_constraint = ""
    if not gate_pass:
        decision = "LAUNCH AVOID"
    elif under_five_minutes:
        decision = "DATA BUILDING"
        decision_constraint = "Under 5 minutes old — decision capped at DATA BUILDING"
    elif score >= rules["strong_score"]:
        decision = "LAUNCH LEADER"
    elif score >= rules["watch_score"]:
        decision = "LAUNCH WATCH"
    else:
        decision = "DATA BUILDING"

    base = pair.get("baseToken") or {}
    quote = pair.get("quoteToken") or {}

    return {
        "Ticker": base.get("symbol") or "—",
        "Name": base.get("name") or "—",
        "Chain": pair.get("chainId") or "—",
        "DEX": pair.get("dexId") or "—",
        "Pair": f"{base.get('symbol','?')}/{quote.get('symbol','?')}",
        "Launch Decision": decision,
        "Launch Score": score,
        "Launch Gate": "PASS" if gate_pass else "FAIL",
        "Age min": round(age_h * 60.0, 1) if math.isfinite(age_h) else np.nan,
        "Price USD": _safe(pair.get("priceUsd")),
        "Liquidity": liq,
        "5m Volume": vol5,
        "1h Volume": vol1h,
        "5m Tx": int(total5),
        "5m Buys": int(buys5),
        "5m Sells": int(sells5),
        "5m Buy %": round(buy_share5 * 100.0, 1),
        "1h Tx": int(total1h),
        "1h Buy %": round(buy_share1h * 100.0, 1),
        "5m %": round(ch5, 2),
        "1h %": round(ch1, 2),
        "5m Vol/Liq": round(vol_liq_5m, 3),
        "5m Tx/min": round(tx_rate_5m, 1),
        "Launch Gate Reasons": "; ".join(hard_failures),
        "Launch Cautions": "; ".join(cautions),
        "Decision Constraint": decision_constraint,
        "Pair Address": pair.get("pairAddress") or "",
        "Token Address": base.get("address") or "",
        "DexScreener": pair.get("url") or "",
    }
