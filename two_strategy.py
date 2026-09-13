from __future__ import annotations

def _num(v, default=None):
    try:
        x=float(v)
        return x
    except Exception:
        return default

def build_12m_target(res: dict) -> dict:
    price=_num(res.get("price"))
    tech=_num(res.get("swing_target"))
    val=_num(res.get("valuation_score"),10.0)
    rev=_num(res.get("revenue_growth"))
    eps=_num(res.get("earnings_growth"))
    analyst=_num(res.get("analyst_target"))
    analysts=int(_num(res.get("analyst_count"),0) or 0)
    if not price or price <= 0:
        return {"twelve_month_target": None, "twelve_month_roi_pct": None, "target_method":"Unavailable"}

    if eps is not None and rev is not None:
        growth=0.65*eps+0.35*rev
    elif eps is not None:
        growth=eps
    elif rev is not None:
        growth=rev
    else:
        growth=5.0
    growth=max(-10.0,min(25.0,growth))
    overlay=5.0 if val>=16 else 2.0 if val>=12 else 0.0 if val>=8 else -5.0
    f_up=max(-15.0,min(35.0,growth+overlay))
    f_target=price*(1+f_up/100)
    tech=tech if tech and tech>0 else price

    if analyst and analyst>0 and analysts>=5:
        a_up=max(-20.0,min(60.0,(analyst/price-1)*100))
        a_target=price*(1+a_up/100)
        if analysts>=10:
            target=0.40*tech+0.35*f_target+0.25*a_target
        else:
            target=0.45*tech+0.40*f_target+0.15*a_target
        method="Technical + fundamentals + analyst consensus"
    else:
        target=0.55*tech+0.45*f_target
        method="Technical + fundamentals"

    return {
        "twelve_month_target": round(target,4),
        "twelve_month_roi_pct": round((target/price-1)*100,1),
        "fundamental_target": round(f_target,4),
        "target_method": method,
    }


def trade_decision(res: dict, owned: bool = False, average_buy_price: float | None = None) -> dict:
    price = float(res.get("price", 0) or 0)
    target_info = build_12m_target(res)
    target = float(target_info.get("twelve_month_target", 0) or 0)
    invalidation = float(res.get("invalidation", 0) or 0)
    trade_score = float(res.get("trade_score", 0) or 0)
    hold_score = float(res.get("one_year_hold_score", res.get("hold_score", 0)) or 0)
    valuation = float(res.get("valuation_score", 0) or 0)
    rr = float(res.get("rr", 0) or 0)
    upside = float(target_info.get("twelve_month_roi_pct", 0) or 0)
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
        **target_info,
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
