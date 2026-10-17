"""STRICT accounting replay for PRECOMPUTED point-in-time Investment signals.

This simulates orders at the NEXT supplied historical session open; never at
the same bar that produced the signal. Decisions must have passed the PIT
financial-data contract. This module does not reconstruct the actual CL
Signal scores or independently certify its dataset. Synthetic tests cannot
establish strategy performance.

Assumptions (conservative and explicit):
- Entire book denominated in GBP; price feeds must already include historical FX.
- Whole-share execution only; execution cost is an assumed percentage PER SIDE,
  covering spread, slippage, dealing fees, any FX and relevant tax as specified.
- Cash dividends are credited on the historical EX-DIVIDEND SESSION to shares
  held at the PREVIOUS session close; actual payment settlement is abstracted.
  Both dividend series and price series must have compatible split factors.
- No margin, shorting, leverage, automatic rebalancing or forced exit.
- Missing held-position marks, invalid/missing metadata or stale next-session
  execution data STOP the backtest instead of manufacturing positive results.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from math import floor, isfinite
from typing import Sequence

from pension_guardrails import Holding, check_portfolio
from pension_point_in_time import (
    HistoricalDatasetManifest, HistoricalUniverseMember,
    HistoricalFundamental, HistoricalDecision, require_historical_inputs,
)
from pension_return_accounting import PensionNAV

MAX_DELAY_DAYS = 7


@dataclass(frozen=True)
class HistoricalPriceBar:
    day: date
    ticker: str
    sector: str
    opening_price_gbp: float
    closing_price_gbp: float
    dividend_ex_date_gbp_per_share: float = 0.0


@dataclass(frozen=True)
class HistoricalFill:
    day: date
    ticker: str
    action: str
    shares: int
    unit_execution_price_gbp: float
    execution_cost_gbp: float


@dataclass(frozen=True)
class HistoricalReplay:
    nav: tuple[PensionNAV, ...]
    fills: tuple[HistoricalFill, ...]
    end_cash_gbp: float
    end_share_counts: dict[str, int]
    unfilled_eligible_actions: int


def replay_point_in_time_decisions(
    manifest: HistoricalDatasetManifest,
    universe: Sequence[HistoricalUniverseMember],
    fundamentals: Sequence[HistoricalFundamental],
    decisions: Sequence[HistoricalDecision],
    price_bars: Sequence[HistoricalPriceBar],
    *,
    initial_cash_gbp: float,
    cost_pct_per_side: float = 0.0,
) -> HistoricalReplay:
    """Accounting-level simulation, not a full investment selection backtest."""
    require_historical_inputs(
        manifest=manifest, universe=universe,
        fundamentals=fundamentals, decisions=decisions,
    )
    if not isfinite(initial_cash_gbp) or initial_cash_gbp <= 0:
        raise ValueError("valid positive initial GBP investment required")
    if not isfinite(cost_pct_per_side) or not 0 <= cost_pct_per_side <= 5:
        raise ValueError("execution cost is not a finite permitted percentage")
    rate = cost_pct_per_side / 100.0
    calendar = sorted(set(bar.day for bar in price_bars))
    if len(calendar) < 2:
        raise ValueError("at least two historical pricing sessions required")
    if calendar[0] < manifest.start or calendar[-1] > manifest.end:
        raise ValueError("price calendar lies outside audited historical coverage")

    market: dict[date, dict[str, HistoricalPriceBar]] = {}
    for bar in price_bars:
        if not bar.ticker.strip() or not bar.sector.strip() or bar.sector.lower() == "unknown":
            raise ValueError("market price lacks ticker or independently classified sector")
        values = [bar.opening_price_gbp, bar.closing_price_gbp, bar.dividend_ex_date_gbp_per_share]
        if not all(isfinite(value) for value in values) or min(values[:2]) <= 0 or values[2] < 0:
            raise ValueError(f"{bar.ticker}: invalid open, close or dividend GBP data")
        by_ticker = market.setdefault(bar.day, {})
        if bar.ticker in by_ticker:
            raise ValueError("duplicate ticker/day pricing row")
        by_ticker[bar.ticker] = bar

    holdings: dict[str, int] = {}
    sectors: dict[str, str] = {}
    cash = float(initial_cash_gbp)
    fills: list[HistoricalFill] = []
    unfilled = 0
    positions_nav: list[PensionNAV] = []
    done: set[int] = set()
    active_actions = {"BUY CANDIDATE", "BUY MORE", "SELL"}

    # Input day 0 is initial funding, with no strategy trades that day.
    for session_ix, day in enumerate(calendar):
        bars = market[day]
        for ticker, count in holdings.items():
            if count > 0 and ticker not in bars:
                raise ValueError(f"{ticker} {day}: held security missing a price mark")

        # Ex-date distributions belong to previous-session owners, so an
        # opening sale still receives the dividend and an opening buy does not.
        if session_ix > 0:
            for ticker, count in holdings.items():
                if count > 0:
                    cash += count * bars[ticker].dividend_ex_date_gbp_per_share
            orders = []
            for n, decision in enumerate(decisions):
                if n in done or decision.action not in active_actions:
                    continue
                if decision.decided_at.date() >= day:
                    continue
                if (day - decision.decided_at.date()).days > MAX_DELAY_DAYS:
                    raise ValueError(f"{decision.ticker}: pending historical order exceeds safe delay")
                if decision.ticker in bars:
                    orders.append((n, decision))
            # Conflicting signals for one ticker at one execution open are
            # ambiguous. Avoid choosing the profitable one retrospectively.
            seen_tickers: set[str] = set()
            for n, order in orders:
                if order.ticker in seen_tickers:
                    raise ValueError(f"{order.ticker}: conflicting prior investment decisions")
                seen_tickers.add(order.ticker)

            orders.sort(key=lambda x: (0 if x[1].action == "SELL" else 1, x[1].ticker))
            for n, order in orders:
                done.add(n)
                bar = bars[order.ticker]
                ticker = order.ticker
                quantity = holdings.get(ticker, 0)
                if order.action == "SELL":
                    if quantity <= 0:
                        unfilled += 1
                        continue
                    notional = quantity * bar.opening_price_gbp
                    fee = notional * rate
                    cash += notional - fee
                    holdings[ticker] = 0
                    sectors.pop(ticker, None)
                    fills.append(HistoricalFill(day, ticker, "SELL", quantity, bar.opening_price_gbp, round(fee, 6)))
                    continue

                # A BUY MORE signal for an unowned security cannot turn into a
                # new BUY CANDIDATE without the Investment entry-gate evidence.
                if order.action == "BUY MORE" and quantity == 0:
                    unfilled += 1
                    continue
                if ticker in sectors and sectors[ticker] != bar.sector:
                    raise ValueError(f"{ticker}: unexplained change of sector classification")
                gross_nav = cash + sum(
                    count * bars[key].opening_price_gbp for key, count in holdings.items()
                    if count > 0
                )
                sector_value = sum(
                    count * bars[key].opening_price_gbp for key, count in holdings.items()
                    if count > 0 and sectors.get(key) == bar.sector
                )
                stock_value = quantity * bar.opening_price_gbp
                affordable_gbp = min(
                    cash / (1.0 + rate),
                    max(0.0, gross_nav * 0.05 - stock_value),
                    max(0.0, gross_nav * 0.20 - sector_value),
                )
                new_shares = floor(affordable_gbp / bar.opening_price_gbp)
                # Costs reduce book value, and may push *other* positions just
                # above 5%. Require the complete post-fill book to pass.
                while new_shares > 0:
                    cost = new_shares * bar.opening_price_gbp * (1.0 + rate)
                    provisional_cash = cash - cost
                    provisional = [
                        Holding(key, sectors.get(key, bar.sector if key == ticker else ""),
                                (count + (new_shares if key == ticker else 0)) * bars[key].opening_price_gbp)
                        for key,count in holdings.items() if count > 0
                    ]
                    if ticker not in holdings or quantity == 0:
                        provisional.append(Holding(ticker, bar.sector, new_shares * bar.opening_price_gbp))
                    if provisional_cash >= -1e-8 and not check_portfolio(provisional, max(0.0, provisional_cash)):
                        break
                    new_shares -= 1
                if new_shares == 0:
                    unfilled += 1
                    continue
                notional = new_shares * bar.opening_price_gbp
                fee = notional * rate
                cash -= notional + fee
                holdings[ticker] = quantity + new_shares
                sectors[ticker] = bar.sector
                fills.append(HistoricalFill(day, ticker, order.action, new_shares, bar.opening_price_gbp, round(fee, 6)))

        nav = cash + sum(
            count * bars[ticker].closing_price_gbp
            for ticker, count in holdings.items() if count > 0
        )
        if not isfinite(nav) or nav <= 0:
            raise ValueError(f"{day}: nonpositive or invalid pension NAV")
        positions_nav.append(PensionNAV(day=day,closing_nav_gbp=round(nav, 6)))

    # Ignore WAIT, REASSESS and PASS, but do not silently lose confirmed
    # orders whose execution date never arrived.
    missing = [
        d.ticker for n,d in enumerate(decisions)
        if n not in done and d.action in active_actions
    ]
    if missing:
        raise ValueError("unexecuted historical actions after final priced session: " + ", ".join(sorted(set(missing))))
    return HistoricalReplay(
        nav=tuple(positions_nav),
        fills=tuple(fills),
        end_cash_gbp=round(cash, 6),
        end_share_counts={t:n for t,n in holdings.items() if n > 0},
        unfilled_eligible_actions=unfilled,
    )
