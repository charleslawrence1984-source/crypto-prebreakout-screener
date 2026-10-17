"""Reproducible GBP total-return accounting for a future pension backtest.

Accepts END-OF-SESSION verified portfolio NAV values including distributions,
dealing costs, FX, taxes and cash, with external contributions/withdrawals
booked at SESSION START. It does not create share picks, pricing, or a
historical portfolio. Each external flow is excluded from investment return.

A matched benchmark must be GBP-denominated TOTAL RETURN (dividends included).
This module is an accounting/test foundation, NOT evidence of CL Signal alpha.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from math import exp, isfinite, log, sqrt
from statistics import stdev
from typing import Sequence


TRADING_DAYS_PER_YEAR = 252
CALENDAR_DAYS_PER_YEAR = 365.25


@dataclass(frozen=True)
class PensionNAV:
    day: date
    closing_nav_gbp: float
    external_flow_at_open_gbp: float = 0.0
    includes_all_costs: bool = True
    includes_all_dividends: bool = True


@dataclass(frozen=True)
class BenchmarkTotalReturn:
    day: date
    gbp_total_return_index: float
    verified_total_return: bool = True


@dataclass(frozen=True)
class MeasuredReturn:
    start: date
    end: date
    strategy_total_return_pct: float
    strategy_cagr_pct: float
    strategy_max_drawdown_pct: float
    strategy_annual_volatility_pct: float
    benchmark_total_return_pct: float
    benchmark_cagr_pct: float
    benchmark_max_drawdown_pct: float
    excess_cagr_percentage_points: float
    trading_periods: int


def _require_number(value: float, label: str, *, positive: bool = False) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError, OverflowError) as err:
        raise ValueError(f"{label}: cannot convert to number") from err
    if not isfinite(v) or (positive and v <= 0):
        raise ValueError(f"{label}: invalid, missing, or nonpositive value")
    return v


def _drawdown_fraction(wealth_curve: Sequence[float]) -> float:
    peak = 0.0
    worst = 0.0
    for wealth in wealth_curve:
        peak = max(peak, wealth)
        worst = min(worst, wealth / peak - 1.0)
    return worst


def _date_order(days: Sequence[date], name: str):
    if len(days) < 2 or any(a >= b for a,b in zip(days, days[1:])):
        raise ValueError(f"{name}: at least two strictly increasing dates are required")


def compare_verified_gbp_returns(
    portfolio: Sequence[PensionNAV],
    benchmark: Sequence[BenchmarkTotalReturn],
) -> MeasuredReturn:
    """Compute portfolio time-weighted returns and matched GBP benchmark.

    Does not compare risk levels or investment suitability. All input sources
    still require independent point-in-time and audit validation.
    """
    p = list(portfolio)
    b = list(benchmark)
    _date_order([x.day for x in p], "portfolio")
    _date_order([x.day for x in b], "benchmark")
    if [x.day for x in p] != [x.day for x in b]:
        raise ValueError("benchmark must have exactly matching session dates in GBP")
    if any(not (x.includes_all_costs and x.includes_all_dividends) for x in p):
        raise ValueError("portfolio NAV missing expenses, distributions or corporate actions")
    if any(not x.verified_total_return for x in b):
        raise ValueError("benchmark total-return index not independently verified")

    wealth = [1.0]
    daily_returns = []
    prev_nav = _require_number(p[0].closing_nav_gbp, "opening portfolio NAV", positive=True)
    if p[0].external_flow_at_open_gbp != 0:
        raise ValueError("first NAV is initial funding; external flow must equal zero")
    idx = []
    for i, observation in enumerate(b):
        idx.append(_require_number(observation.gbp_total_return_index, f"benchmark index #{i}", positive=True))

    for i, rec in enumerate(p[1:], 1):
        nav = _require_number(rec.closing_nav_gbp, f"portfolio NAV #{i}", positive=True)
        flow = _require_number(rec.external_flow_at_open_gbp, f"external flow #{i}")
        investable_base = prev_nav + flow
        if investable_base <= 0:
            raise ValueError(f"external flow #{i}: impossible negative opening capital")
        r = nav / investable_base - 1.0
        daily_returns.append(r)
        wealth.append(wealth[-1] * (1 + r))
        prev_nav = nav

    benchmark_wealth = [x / idx[0] for x in idx]
    years = (p[-1].day - p[0].day).days / CALENDAR_DAYS_PER_YEAR
    if years <= 0:
        raise ValueError("nonpositive performance measurement period")
    strategy_total = wealth[-1] - 1.0
    benchmark_total = benchmark_wealth[-1] - 1.0
    strategy_cagr = wealth[-1] ** (1.0 / years) - 1.0
    benchmark_cagr = benchmark_wealth[-1] ** (1.0 / years) - 1.0
    volatility = (
        stdev(daily_returns) * sqrt(TRADING_DAYS_PER_YEAR)
        if len(daily_returns) >= 2 else 0.0
    )
    return MeasuredReturn(
        start=p[0].day,
        end=p[-1].day,
        strategy_total_return_pct=round(strategy_total * 100, 4),
        strategy_cagr_pct=round(strategy_cagr * 100, 4),
        strategy_max_drawdown_pct=round(_drawdown_fraction(wealth) * 100, 4),
        strategy_annual_volatility_pct=round(volatility * 100, 4),
        benchmark_total_return_pct=round(benchmark_total * 100, 4),
        benchmark_cagr_pct=round(benchmark_cagr * 100, 4),
        benchmark_max_drawdown_pct=round(_drawdown_fraction(benchmark_wealth) * 100, 4),
        excess_cagr_percentage_points=round((strategy_cagr - benchmark_cagr) * 100, 4),
        trading_periods=len(p) - 1,
    )
