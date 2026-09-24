from __future__ import annotations

from typing import Dict, Iterable, List

import pandas as pd


def aggregate_ohlcv(frame: pd.DataFrame, rule: str = "4h") -> pd.DataFrame:
    """Aggregate lower-timeframe OHLCV into completed higher-timeframe candles."""
    if frame is None or frame.empty or "timestamp" not in frame.columns:
        return pd.DataFrame()

    work = frame.copy()
    work["timestamp"] = pd.to_datetime(work["timestamp"], utc=True, errors="coerce")
    for col in ["open", "high", "low", "close", "volume"]:
        work[col] = pd.to_numeric(work[col], errors="coerce")
    work = work.dropna(subset=["timestamp", "open", "high", "low", "close"])
    if work.empty:
        return pd.DataFrame()

    grouped = work.set_index("timestamp").resample(rule, label="left", closed="left")
    out = grouped.agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        volume=("volume", "sum"),
        _source_bars=("close", "count"),
    ).dropna(subset=["open", "high", "low", "close"])

    # For 1h -> 4h, exclude the still-forming partial 4h candle.
    if rule.lower() == "4h":
        out = out[out["_source_bars"] >= 4]

    return out.drop(columns=["_source_bars"]).reset_index()


def select_bulk_plan_indices(
    ranked: pd.DataFrame,
    requested_limit: int,
    api_budget: int = 8,
) -> Dict[str, List]:
    """
    Select only hard-gate PASS candidates for bulk trade planning.

    Priority: decision quality, score, then liquidity. Anything beyond the
    public-API budget is deferred rather than attempted and rate-limited.
    """
    if ranked is None or ranked.empty:
        return {"selected": [], "deferred": [], "ineligible": []}

    gate = ranked.get("Gate", pd.Series("", index=ranked.index))
    pass_mask = gate.eq("PASS")
    ineligible = list(ranked.index[~pass_mask])

    candidates = ranked.loc[pass_mask].copy()
    decision_rank = {
        "HIGH PRIORITY": 0,
        "SHORTLIST": 1,
        "TRADE WATCH": 2,
        "DISCOVERY WATCH": 3,
        "PASS": 4,
    }
    candidates["_plan_decision_rank"] = (
        candidates.get("Decision", pd.Series("", index=candidates.index))
        .map(decision_rank)
        .fillna(9)
    )
    if "Score" not in candidates.columns:
        candidates["Score"] = 0.0
    if "Liquidity" not in candidates.columns:
        candidates["Liquidity"] = 0.0

    candidates = candidates.sort_values(
        ["_plan_decision_rank", "Score", "Liquidity"],
        ascending=[True, False, False],
        na_position="last",
    )

    requested = max(0, int(requested_limit))
    budget = max(0, int(api_budget))
    selected = list(candidates.head(min(requested, budget)).index)
    deferred = list(candidates.iloc[min(requested, budget):requested].index)
    return {
        "selected": selected,
        "deferred": deferred,
        "ineligible": ineligible,
    }


def classify_meme_decision(
    gate_pass: bool,
    score: float,
    shortlist_score: float,
) -> str:
    """
    Keep trade readiness separate from discovery interest.

    TRADE WATCH means all hard gates pass but the score has not reached the
    shortlist threshold. DISCOVERY WATCH means the coin is interesting enough
    to monitor but currently fails at least one hard trading gate.
    """
    score_value = float(score)
    shortlist_value = float(shortlist_score)

    if gate_pass and score_value >= 80:
        return "HIGH PRIORITY"
    if gate_pass and score_value >= shortlist_value:
        return "SHORTLIST"
    if gate_pass and score_value >= 55:
        return "TRADE WATCH"
    if (not gate_pass) and score_value >= 55:
        return "DISCOVERY WATCH"
    return "PASS"
