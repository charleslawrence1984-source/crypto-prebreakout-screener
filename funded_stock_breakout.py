"""Kraken Funded STOCKS ONLY: conservative completed-bar breakout research.

Never treats a projected next-open price as a fill or an immediate BUY. Uses
historical OHLCV exclusively up to and including the latest completed bar.
"""
from __future__ import annotations

import math
import numpy as np
import pandas as pd

LOOKBACK = 60
MIN_HISTORY = 252


def _rsi(close: pd.Series, length: int = 14) -> float:
    delta = close.diff()
    gains = delta.clip(lower=0).ewm(alpha=1 / length, adjust=False, min_periods=length).mean()
    losses = (-delta.clip(upper=0)).ewm(alpha=1 / length, adjust=False, min_periods=length).mean()
    loss = float(losses.iloc[-1])
    gain = float(gains.iloc[-1])
    if not (math.isfinite(gain) and math.isfinite(loss)):
        return math.nan
    return 100.0 if loss == 0 else 100.0 - 100.0 / (1 + gain / loss)


def _resistance_tests(high: pd.Series, resistance: float, tolerance: float) -> int:
    """Count separated tests, never treating adjacent candles as separate touches."""
    indices = np.flatnonzero((high >= resistance - tolerance).to_numpy())
    selected = []
    for pos in indices:
        if not selected or pos - selected[-1] >= 5:
            selected.append(int(pos))
    return len(selected)


def evaluate_funded_breakout(frame: pd.DataFrame, benchmark: pd.DataFrame | None = None) -> dict | None:
    """Return only a confirmed completed-daily-bar setup, never an executable BUY."""
    if frame is None or len(frame) < MIN_HISTORY:
        return None
    data = frame.copy()
    if not {"Open", "High", "Low", "Close", "Volume"}.issubset(data.columns):
        return None
    for field in ("Open", "High", "Low", "Close", "Volume"):
        data[field] = pd.to_numeric(data[field], errors="coerce")
    data = data.dropna(subset=["Open", "High", "Low", "Close", "Volume"]).sort_index()
    if len(data) < MIN_HISTORY:
        return None

    close, high, low, volume = (data[column] for column in ("Close", "High", "Low", "Volume"))
    price = float(close.iloc[-1])
    yesterday_close = float(close.iloc[-2])
    recent_high = high.iloc[-LOOKBACK - 1:-1]
    resistance = float(recent_high.max())
    if not (price > 0 and resistance > 0 and price > resistance and yesterday_close <= resistance):
        return None

    previous_close = close.shift(1)
    tr = pd.concat(
        [(high - low), (high - previous_close).abs(), (low - previous_close).abs()],
        axis=1,
    ).max(axis=1)
    atr = float(tr.ewm(alpha=1 / 20, adjust=False, min_periods=20).mean().iloc[-1])
    ma180 = close.rolling(180).mean()
    ma200 = close.rolling(200).mean()
    rsi = _rsi(close)
    previous_vol = float(volume.iloc[-21:-1].median())
    vol_ratio = float(volume.iloc[-1]) / previous_vol if previous_vol > 0 else math.nan
    # Confirm that previous resistance was tested more than once, well apart.
    touches = _resistance_tests(recent_high, resistance, max(0.35 * atr, 0.003 * resistance))
    range_current = float(tr.iloc[-11:-1].mean())
    range_previous = float(tr.iloc[-31:-11].mean())
    compressed = range_previous > 0 and range_current <= range_previous * 0.95
    higher_lows = float(low.iloc[-11:-1].min()) >= float(low.iloc[-31:-11].min()) * 1.003
    above_rising_ma = (
        price > float(ma180.iloc[-1]) and price > float(ma200.iloc[-1])
        and float(ma180.iloc[-1]) > float(ma180.iloc[-21]) * 1.005
        and float(ma200.iloc[-1]) > float(ma200.iloc[-21]) * 1.005
    )
    day_range = float(high.iloc[-1] - low.iloc[-1])
    close_location = (price - float(low.iloc[-1])) / day_range if day_range > 0 else 0
    advance = price - resistance
    extension = advance / atr if atr > 0 else math.inf
    macd = close.ewm(span=12, adjust=False).mean() - close.ewm(span=26, adjust=False).mean()
    histogram = macd - macd.ewm(span=9, adjust=False).mean()
    positive_momentum = bool(histogram.iloc[-1] > histogram.iloc[-2] and histogram.iloc[-1] > 0)
    rs_positive = False
    if benchmark is not None and "Close" in benchmark.columns:
        ref = pd.to_numeric(benchmark["Close"], errors="coerce")
        compared = pd.concat([close.rename("stock"), ref.rename("bench")], axis=1).dropna()
        if len(compared) >= 21:
            rs_positive = (
                compared["stock"].iloc[-1] / compared["stock"].iloc[-21]
                >= compared["bench"].iloc[-1] / compared["bench"].iloc[-21]
            )

    reasons = []
    if touches < 2:
        reasons.append("RESISTANCE NOT TESTED TWICE")
    if not (higher_lows or compressed):
        reasons.append("NO CONSTRUCTIVE BASE")
    if not above_rising_ma:
        reasons.append("180/200-DAY TREND NOT CONFIRMED")
    if not math.isfinite(rsi) or not 50 <= rsi <= 75:
        reasons.append("RSI OUTSIDE 50–75")
    if not math.isfinite(vol_ratio) or vol_ratio < 1.35:
        reasons.append("BREAKOUT VOLUME BELOW 1.35X")
    if not positive_momentum:
        reasons.append("MOMENTUM NOT CONFIRMED")
    if not rs_positive:
        reasons.append("NO VERIFIED RELATIVE STRENGTH")
    if close_location < 0.7 or price <= float(data["Open"].iloc[-1]):
        reasons.append("WEAK BREAKOUT CANDLE")
    if not 0.12 <= extension <= 0.85 or advance / resistance > 0.025:
        reasons.append("BREAKOUT TOO WEAK OR EXTENDED")

    base_low = float(low.iloc[-31:-1].min())
    recent_support = float(low.iloc[-11:-1].min())
    stop = min(recent_support - 0.2 * atr, resistance - 0.7 * atr)
    height = resistance - base_low
    target = resistance + min(0.8 * height, 5.0 * atr)
    stop_pct = (price - stop) / price * 100
    upside_pct = (target - price) / price * 100
    rr = (target - price) / (price - stop) if price > stop else math.nan
    if not 0.4 <= stop_pct <= 8.0:
        reasons.append("STRUCTURAL STOP OUTSIDE 0.4–8%")
    if upside_pct < 4.0:
        reasons.append("PROJECTED UPSIDE BELOW 4%")
    if not math.isfinite(rr) or rr < 2.0:
        reasons.append("PROJECTED R:R BELOW 2")

    score = (15 * above_rising_ma + 10 * higher_lows + 5 * compressed
             + 10 * (touches >= 2) + 15 * (vol_ratio >= 1.35)
             + 10 * positive_momentum + 10 * rs_positive
             + 10 * (50 <= rsi <= 75) + 10 * (close_location >= 0.7)
             + 5 * (0.12 <= extension <= 0.85))
    return {
        "technical_state": "FUNDED BREAKOUT PENDING OPEN" if not reasons else "FUNDED BREAKOUT REVIEW",
        "technical_reason": (
            "CONFIRMED DAILY BREAKOUT — MANUAL LIVE ENTRY REQUIRED"
            if not reasons else "; ".join(reasons)
        ),
        "technical_score": float(score),
        "macd_progress": "BREAKOUT CONFIRMED" if not reasons else "BREAKOUT REVIEW",
        "rsi": round(rsi, 1),
        "distance_to_resistance_pct": round((resistance - price) / resistance * 100, 2),
        "resistance": round(resistance, 4),
        "volume_ratio": round(vol_ratio, 2) if math.isfinite(vol_ratio) else math.nan,
        "reference_entry": round(price, 4),
        "reference_stop": round(stop, 4),
        # Research-only structural references. They are NOT used by the
        # live approved stop/target plan or quote validation.
        "research_stop_support": round(recent_support - 0.2 * atr, 4),
        "research_stop_retest": round(resistance - 0.7 * atr, 4),
        "reference_target": round(target, 4),
        "reference_reward_risk": round(rr, 2) if math.isfinite(rr) else math.nan,
        "reference_target_basis": "0.8x 30-day base height; capped at 5x ATR (projection)",
        "breakout_signal_date": str(data.index[-1].date()),
        "breakout_tests": touches,
        "pre_cross_blocker": "LIVE MARKET PRICE NOT VERIFIED" if not reasons else "; ".join(reasons),
    }


def validate_breakout_quote(breakout: dict, live_price: float) -> dict:
    """Validate a manually verified LIVE executable quote against an old daily close.

    Deliberately excludes commissions, FX and overnight gaps from guaranteed losses.
    """
    try:
        price = float(live_price)
        prior_entry = float(breakout["reference_entry"])
        resistance = float(breakout["resistance"])
        stop = float(breakout["reference_stop"])
        target = float(breakout["reference_target"])
    except (KeyError, TypeError, ValueError):
        return {"ok": False, "reason": "MISSING QUOTE OR PLAN"}
    if not all(math.isfinite(v) for v in (price, prior_entry, resistance, stop, target)):
        return {"ok": False, "reason": "NONFINITE QUOTE OR PLAN"}
    if price <= 0 or prior_entry <= 0 or not stop < price < target:
        return {"ok": False, "reason": "INVALID PRICE / STOP / TARGET"}
    # Never chase a >1% adverse move from the breakout close, or a >2%
    # gap across the reference resistance; reject a lost breakout too.
    if price > prior_entry * 1.01 or price > resistance * 1.02:
        return {"ok": False, "reason": "TOO LATE / GAP ABOVE MAX ENTRY"}
    if price < resistance * 0.998:
        return {"ok": False, "reason": "BREAKOUT LEVEL LOST"}
    rr = (target - price) / (price - stop)
    risk_pct = (price - stop) / price * 100
    if rr < 2.0 or not 0.4 <= risk_pct <= 8.0:
        return {"ok": False, "reason": "LIVE PRICE FAILS R:R OR STOP LIMIT"}
    return {"ok": True, "entry": price, "stop": stop, "target": target,
            "reward_risk": round(rr, 2)}
