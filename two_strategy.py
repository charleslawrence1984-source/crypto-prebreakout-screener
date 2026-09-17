from __future__ import annotations

import math


def _f(v, default=0.0):
    try:
        x = float(v)
        return x if math.isfinite(x) else default
    except Exception:
        return default


def trade_decision(res: dict, owned: bool = False, average_buy_price: float | None = None) -> dict:
    price = _f(res.get("price"))
    target = _f(res.get("swing_target"))
    invalidation = _f(res.get("invalidation"))
    trade_score = _f(res.get("trade_score"))
    hold_score = _f(res.get("one_year_hold_score", res.get("hold_score", 0)))
    valuation = _f(res.get("valuation_score"))
    rr = _f(res.get("rr"))
    upside = _f(res.get("upside_pct"))
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


def _dynamic_sell_premium(res: dict) -> float:
    moat = str(res.get("moat_confidence") or "LOW").upper()
    reinvestment = _f(res.get("reinvestment_score"))
    uncertainty = _f(res.get("valuation_uncertainty_pct"), 50.0)

    if moat == "HIGH" and reinvestment >= 80 and uncertainty <= 25:
        return 0.65
    if moat == "HIGH":
        return 0.50
    if moat == "MEDIUM" and reinvestment >= 70:
        return 0.40
    if moat == "MEDIUM":
        return 0.30
    return 0.20


def investment_decision(res: dict, owned: bool = False, average_buy_price: float | None = None) -> dict:
    price = _f(res.get("price"))
    quality = _f(res.get("investment_quality_score", res.get("long_term_score", 0)))
    hard_gate_pass = bool(res.get("hard_gate_pass", res.get("elite_gate_pass", False)))
    structural_moat = str(res.get("structural_moat_status") or "UNVERIFIED").upper()
    valuation_gate_pass = bool(res.get("valuation_gate_pass"))
    sector_review_required = bool(res.get("sector_review_required"))
    metadata_complete = bool(res.get("metadata_complete", True))

    base_value = _f(res.get("dcf_base"))
    required_mos = _f(res.get("required_margin_of_safety_pct"))
    base_mos = res.get("margin_of_safety_base_pct")
    bear_mos = res.get("margin_of_safety_bear_pct")
    failures = str(res.get("hard_gate_failures") or "").strip()
    warnings = str(res.get("hard_gate_warnings") or "").strip()

    quality_ok = (
        hard_gate_pass
        and not sector_review_required
        and metadata_complete
    )
    qualifies = quality_ok and valuation_gate_pass

    reasons = []
    if not hard_gate_pass:
        reasons.append(failures or "one or more non-negotiable investment gates failed")
    if not metadata_complete:
        reasons.append("company metadata incomplete; sector/industry context must be confirmed")
    if hard_gate_pass and structural_moat != "SUPPORTED":
        reasons.append("structural moat mechanism remains a separate manual check")
    if sector_review_required:
        reasons.append("specialist sector review required")
    if hard_gate_pass and not valuation_gate_pass:
        if required_mos:
            reasons.append(f"DCF margin of safety is below the required {required_mos:.1f}% hurdle or bear case is not protected")
        else:
            reasons.append("DCF valuation gate not passed")
    if warnings:
        reasons.append(warnings)

    sell_premium = _dynamic_sell_premium(res)
    overvaluation = None
    if price > 0 and base_value > 0:
        overvaluation = price / base_value - 1

    if owned:
        if not hard_gate_pass:
            action = "REASSESS"
        elif not metadata_complete:
            action = "REASSESS"
        elif sector_review_required:
            action = "REASSESS"
        elif warnings and "ROIC" in warnings.upper():
            action = "REASSESS"
        elif overvaluation is not None and overvaluation >= sell_premium:
            action = "SELL"
            reasons.append(
                f"price is {overvaluation * 100:.1f}% above base intrinsic value versus a dynamic sell ceiling of {sell_premium * 100:.0f}%"
            )
        else:
            action = "HOLD"
    else:
        if not hard_gate_pass:
            action = "PASS"
        elif qualifies:
            action = "BUY CANDIDATE"
        else:
            action = "WAIT"

    actual_roi = None
    if average_buy_price and average_buy_price > 0 and price > 0:
        actual_roi = (price / average_buy_price - 1) * 100

    return {
        "action": action,
        "qualifies": qualifies,
        "quality_ok": quality_ok,
        "entry_ok": valuation_gate_pass,
        "valuation_ok": valuation_gate_pass,
        "metadata_complete": metadata_complete,
        "reasons": reasons,
        "quality_score": quality,
        "base_intrinsic_value": base_value if base_value > 0 else None,
        "base_margin_of_safety_pct": base_mos,
        "bear_margin_of_safety_pct": bear_mos,
        "required_margin_of_safety_pct": required_mos,
        "dynamic_sell_premium_pct": round(sell_premium * 100, 1),
        "overvaluation_vs_base_pct": None if overvaluation is None else round(overvaluation * 100, 1),
        "actual_roi_pct": actual_roi,
    }
