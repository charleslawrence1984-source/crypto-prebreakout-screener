from __future__ import annotations


def trade_decision(res: dict, owned: bool = False, average_buy_price: float | None = None) -> dict:
    price = float(res.get("price", 0) or 0)
    target = float(res.get("swing_target", 0) or 0)
    invalidation = float(res.get("invalidation", 0) or 0)
    trade_score = float(res.get("trade_score", 0) or 0)
    hold_score = float(res.get("one_year_hold_score", res.get("hold_score", 0)) or 0)
    valuation = float(res.get("valuation_score", 0) or 0)
    rr = float(res.get("rr", 0) or 0)
    upside = float(res.get("upside_pct", 0) or 0)
    in_zone = bool(res.get("in_preferred_zone") or res.get("in_strong_zone"))

    technical_ok = trade_score >= 85 and rr >= 2.0 and in_zone and upside >= 10.0
    hold_ok = hold_score >= 70 and valuation >= 10
    qualifies = technical_ok and hold_ok

    reasons = []
    if trade_score < 85:
        reasons.append("technical score below 85")
    if rr < 2:
        reasons.append("risk/reward below 2:1")
    if not in_zone:
        reasons.append("price not in entry zone")
    if upside < 10:
        reasons.append("less than 10% modelled upside")
    if hold_score < 70:
        reasons.append("12-month fundamentals not strong enough")
    if valuation < 10:
        reasons.append("valuation not comfortable enough for a 12-month hold")

    if owned:
        if invalidation > 0 and price <= invalidation:
            action = "EXIT / REASSESS"
        elif target > 0 and price >= target:
            action = "EXIT / TARGET REACHED"
        elif hold_ok:
            action = "HOLD"
        else:
            action = "REASSESS"
    else:
        action = "BUY" if qualifies else "WAIT"

    actual_roi = None
    target_roi_from_cost = None
    if average_buy_price and average_buy_price > 0 and price > 0:
        actual_roi = (price / average_buy_price - 1) * 100
        if target > 0:
            target_roi_from_cost = (target / average_buy_price - 1) * 100

    return {
        "action": action,
        "qualifies": qualifies,
        "technical_ok": technical_ok,
        "hold_ok": hold_ok,
        "reasons": reasons,
        "actual_roi_pct": actual_roi,
        "target_roi_from_cost_pct": target_roi_from_cost,
    }


def investment_decision(res: dict, owned: bool = False, average_buy_price: float | None = None) -> dict:
    price = float(res.get("price", 0) or 0)
    quality = float(res.get("long_term_score", 0) or 0)
    entry = float(res.get("long_term_entry_score", 0) or 0)
    elite_gate = bool(res.get("elite_gate_pass"))
    sector_review_required = bool(res.get("sector_review_required"))

    buy_quality = quality >= 90 and elite_gate and not sector_review_required
    entry_ok = entry >= 70
    qualifies = buy_quality and entry_ok

    reasons = []
    if quality < 90:
        reasons.append("long-term quality below 90")
    if not elite_gate:
        reasons.append("elite quality gate not passed")
    if sector_review_required:
        reasons.append("specialist sector review required")
    if entry < 70:
        reasons.append("long-term entry not attractive enough")

    if owned:
        if quality >= 82:
            action = "HOLD"
        else:
            action = "REASSESS"
    else:
        action = "BUY" if qualifies else "WAIT"

    actual_roi = None
    if average_buy_price and average_buy_price > 0 and price > 0:
        actual_roi = (price / average_buy_price - 1) * 100

    return {
        "action": action,
        "qualifies": qualifies,
        "quality_ok": buy_quality,
        "entry_ok": entry_ok,
        "reasons": reasons,
        "actual_roi_pct": actual_roi,
    }
