"""Conservative same-period pension-fund return comparison (no personal data).

Never compare a member's balance gain against a managed fund's published
time-weighted return. Do not call an unadjusted total return the 'best
pension': asset allocation, risk, access, protection and fees matter separately.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from math import isfinite
from typing import Sequence


@dataclass(frozen=True)
class FundPerformance:
    provider: str
    fund: str
    period_start: date
    period_end: date
    cumulative_total_return_pct: float
    reporting_currency: str
    includes_distributions: bool
    adjusted_for_all_member_fees: bool


def rank_comparable_returns(
    observations: Sequence[FundPerformance],
    *,
    expected_providers: Sequence[str] = ("Aviva", "Standard Life", "Smart Pension"),
) -> list[FundPerformance]:
    """Rank only directly comparable realised fund returns, highest first.

    This only ranks matched historical performance, NOT best pension provider,
    suitability, expected return or risk-adjusted results.
    """
    if not observations:
        raise ValueError("no published fund returns supplied")
    providers = [x.provider for x in observations]
    if len(providers) != len(set(providers)):
        raise ValueError("duplicate provider performance observation")
    missing = set(expected_providers) - set(providers)
    if missing:
        raise ValueError("missing comparable provider return(s): " + ", ".join(sorted(missing)))

    start = observations[0].period_start
    end = observations[0].period_end
    if start >= end:
        raise ValueError("return period must be positive")
    for row in observations:
        if row.period_start != start or row.period_end != end:
            raise ValueError("fund returns cover different observation periods")
        if row.reporting_currency.upper() != "GBP":
            raise ValueError("fund return is not normalised to GBP")
        if not row.includes_distributions:
            raise ValueError("total return requires reinvested distributions")
        if not row.adjusted_for_all_member_fees:
            raise ValueError("return is not net of relevant member-level costs")
        if not isfinite(row.cumulative_total_return_pct) or row.cumulative_total_return_pct <= -100:
            raise ValueError("invalid fund total return")
        if not row.provider.strip() or not row.fund.strip():
            raise ValueError("provider or fund is unnamed")
    return sorted(observations, key=lambda x: x.cumulative_total_return_pct, reverse=True)
