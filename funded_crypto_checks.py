"""Kraken Funded crypto ONLY: completed-candle safeguards and audit summaries.

Used by the Funded crypto scan before invoking the unchanged general crypto
rule engine. CCXT OHLCV timestamps denote candle OPEN times; the last supplied
candle may still be forming. Do not score it as though it has closed.
"""
from __future__ import annotations

import math
import pandas as pd

DURATIONS = {
    "4h": pd.Timedelta(hours=4),
    "1d": pd.Timedelta(days=1),
    "1w": pd.Timedelta(days=7),
}
CLOCK_GRACE = pd.Timedelta(minutes=1)


def completed_funded_crypto_candles(frame, timeframe, now):
    """Return only completed candles, never a still-developing final bar.

    Fail closed if the latest fully completed bar is too old. 24/7 crypto
    markets are expected to provide bars without an exchange-session holiday.
    This is a data validity check, not a strategy eligibility adjustment.
    """
    if timeframe not in DURATIONS:
        raise ValueError(f"Unknown candle timeframe: {timeframe}")
    stamp = pd.Timestamp(now)
    if stamp.tzinfo is None:
        raise ValueError("An aware UTC observation clock is required")
    stamp = stamp.tz_convert("UTC")
    if frame is None or frame.empty or "timestamp" not in frame.columns:
        raise ValueError(f"Missing {timeframe} crypto candles")
    candles = frame.copy()
    candles["timestamp"] = pd.to_datetime(
        candles["timestamp"], errors="coerce", utc=True,
    )
    candles = candles.dropna(subset=["timestamp"]).sort_values("timestamp")
    candles = candles.drop_duplicates(subset=["timestamp"], keep="last")
    duration = DURATIONS[timeframe]
    completed = candles.loc[
        candles["timestamp"] + duration + CLOCK_GRACE <= stamp
    ].copy()
    if completed.empty:
        raise ValueError(f"No completed {timeframe} crypto candles")
    newest_end = completed["timestamp"].iloc[-1] + duration
    if stamp - newest_end > duration * 2:
        raise ValueError(f"Stale completed {timeframe} crypto candles")
    return completed.reset_index(drop=True)


def crypto_gate_audit(frame, near_resistance_pct=5.0, score_threshold=80.0):
    """Independent, potentially overlapping reasons from the displayed data.

    This diagnoses the strict funnel; it NEVER promotes a PASS/WATCH to BUY.
    The price-distance cut is the existing engine's default, not a new gate.
    """
    labels = [
        "Coins in report",
        "Outside 5% below 4h resistance",
        "Below required swing score of 80",
        "Timing not actionable",
        "Underlying swing shape not eligible",
        "Kraken spot-volume proxy below $1m",
        "Unusable or missing historical crypto candles",
        "Funded READY/A+ (all live checks)",
    ]
    if frame is None or frame.empty:
        return pd.DataFrame({
            "Diagnostic": labels,
            "Coins": [0] * len(labels),
        })
    score = pd.to_numeric(frame.get(
        "Score", pd.Series(math.nan, index=frame.index),
    ), errors="coerce")
    distance = pd.to_numeric(frame.get(
        "To resistance %", pd.Series(math.nan, index=frame.index),
    ), errors="coerce")
    timing = frame.get(
        "Entry timing actionable",
        pd.Series(False, index=frame.index),
    ).fillna(False).astype(bool)
    shape = frame.get(
        "Swing eligible",
        pd.Series(False, index=frame.index),
    ).fillna(False).astype(bool)
    liq = frame.get(
        "Execution liquidity pass",
        pd.Series(False, index=frame.index),
    ).fillna(False).astype(bool)
    status = frame.get(
        "Funded status", pd.Series("", index=frame.index),
    ).fillna("").astype(str)
    technical = frame.get(
        "CL Signal", pd.Series("", index=frame.index),
    ).fillna("").astype(str)
    failures = technical.str.contains("DATA UNAVAILABLE", case=False, regex=False)
    failures = failures | status.str.contains(
        "DATA UNAVAILABLE", case=False, regex=False,
    ) | status.str.contains("SCAN ERROR", case=False, regex=False)
    return pd.DataFrame({
        "Diagnostic": labels,
        "Coins": [
            len(frame),
            int((distance > near_resistance_pct).sum()),
            int((score < score_threshold).sum()),
            int((~timing).sum()),
            int((~shape).sum()),
            int((~liq).sum()),
            int(failures.sum()),
            int(status.isin(("🟢 A+ FUNDED READY", "🟢 FUNDED READY")).sum()),
        ],
    })


def crypto_distance_sensitivity(frame):
    """Research-only proximity distribution, never a replacement BUY gate."""
    columns = [
        "Hypothetical max distance to resistance", "Coins nearby",
        "Nearby coins with score >=80", "Trade approvals",
    ]
    if frame is None or frame.empty:
        return pd.DataFrame(columns=columns)
    distance = pd.to_numeric(frame.get(
        "To resistance %", pd.Series(math.nan, index=frame.index),
    ), errors="coerce")
    score = pd.to_numeric(frame.get(
        "Score", pd.Series(math.nan, index=frame.index),
    ), errors="coerce")
    return pd.DataFrame([
        {
            "Hypothetical max distance to resistance": f"{level}%",
            "Coins nearby": int(distance.between(0, level).sum()),
            "Nearby coins with score >=80": int((
                distance.between(0, level) & (score >= 80)
            ).sum()),
            "Trade approvals": "NONE — RESEARCH DISTRIBUTION ONLY",
        }
        for level in (3, 5, 8, 10, 15)
    ], columns=columns)
