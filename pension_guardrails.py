"""Fail-closed portfolio and signal checks for CL Signal's long-term pension research.

These guards do NOT establish the accuracy of valuations or strategy returns.
The investment engine must be point-in-time backtested separately.
No broker orders or transfers are initiated by this module.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from math import isfinite
from typing import Mapping

MODEL_VERSION = "investment-fx-v2-2026-09-22"
MAX_STOCK_WEIGHT_PCT = 5.0
MAX_SECTOR_WEIGHT_PCT = 20.0
MAX_QUOTE_AGE = timedelta(days=5)


@dataclass(frozen=True)
class Holding:
    ticker: str
    sector: str
    market_value_gbp: float


def check_portfolio(holdings: list[Holding], cash_gbp: float = 0.0) -> list[str]:
    """Return all allocation violations, weighing holdings against stocks PLUS cash."""
    errors: list[str] = []
    if not isfinite(cash_gbp) or cash_gbp < 0:
        return ["cash balance must be a non-negative finite GBP amount"]

    total = cash_gbp
    per_sector: dict[str, float] = {}
    tickers: set[str] = set()
    for holding in holdings:
        ticker = str(holding.ticker).strip().upper()
        sector = str(holding.sector).strip()
        if not ticker or ticker.lower() in {"nan", "none", "null", "unknown"}:
            errors.append("every position requires a verified ticker")
        elif ticker in tickers:
            errors.append(f"duplicate holding: {ticker}")
        tickers.add(ticker)
        if not sector or sector.lower() in {"unknown", "nan", "none"}:
            errors.append(f"{ticker or 'unidentified'}: sector is unverified")
        if not isfinite(holding.market_value_gbp) or holding.market_value_gbp <= 0:
            errors.append(f"{ticker or 'unidentified'}: value must be positive and finite")
            continue
        total += holding.market_value_gbp
        per_sector[sector.casefold()] = per_sector.get(sector.casefold(), 0.0) + holding.market_value_gbp

    if total <= 0:
        errors.append("total portfolio value must be greater than zero")
        return errors
    for holding in holdings:
        if not isfinite(holding.market_value_gbp) or holding.market_value_gbp <= 0:
            continue
        pct = 100.0 * holding.market_value_gbp / total
        if pct > MAX_STOCK_WEIGHT_PCT + 1e-9:
            errors.append(f"{str(holding.ticker).strip().upper()}: stock weight {pct:.2f}% exceeds {MAX_STOCK_WEIGHT_PCT:.0f}%")
    for sector, value in sorted(per_sector.items()):
        pct = 100.0 * value / total
        if pct > MAX_SECTOR_WEIGHT_PCT + 1e-9:
            errors.append(f"{sector}: sector weight {pct:.2f}% exceeds {MAX_SECTOR_WEIGHT_PCT:.0f}%")
    return errors


def check_research_candidate(
    record: Mapping[str, object],
    *,
    quote_observed_at: datetime | None = None,
    asof: datetime | None = None,
    manual_research_approved: bool = False,
    broker_instrument_verified: bool = False,
) -> list[str]:
    """Do not promote an old research row into a pension buy recommendation.

    Explicit manual judgement, correct venue, fresh quote, compatible model
    and independent available-at-the-time fundamentals are all needed.
    """
    reasons: list[str] = []
    if str(record.get("Action", "")).upper() != "BUY CANDIDATE":
        reasons.append("not an Investment BUY CANDIDATE")
    if str(record.get("Valuation model version", "")) != MODEL_VERSION:
        reasons.append("valuation model version unverified or stale")
    if str(record.get("Valuation FX status", "")).upper() != "PASS":
        reasons.append("valuation FX missing or invalid")
    if str(record.get("Hard gates", "")).upper() != "PASS":
        reasons.append("long-term hard gates not confirmed")
    if str(record.get("Investability", "")).upper() != "✅ INVESTABLE":
        reasons.append("primary/broker-eligible listing not confirmed")
    if not manual_research_approved:
        reasons.append("qualitative investment research not signed off")
    if not broker_instrument_verified:
        reasons.append("SIPP instrument availability not verified")

    now = asof or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("asof must be timezone-aware")
    if quote_observed_at is None:
        reasons.append("quote timestamp unavailable")
    elif quote_observed_at.tzinfo is None:
        reasons.append("quote timestamp lacks timezone")
    elif quote_observed_at > now:
        reasons.append("quote timestamp is in the future")
    elif now - quote_observed_at > MAX_QUOTE_AGE:
        reasons.append("quote older than 5 calendar days")
    return reasons
