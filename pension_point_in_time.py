"""Fail-closed historical-data contract for CL Signal 10-years-to-forever research.

This gate checks whether *supplied* source observations COULD have been known
at a historical decision date. It cannot independently authenticate a vendor's
historical statements, corporate-action history or universe completeness.

Passing means DATA CHRONOLOGY CHECKED, not HISTORICAL ALPHA VERIFIED.
No live pension or broker interactions.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
from typing import Sequence


ALLOWED_ACTIONS = frozenset({"BUY CANDIDATE", "WAIT", "PASS", "HOLD", "BUY MORE", "REASSESS", "SELL"})
MAX_HISTORICAL_QUOTE_AGE_DAYS = 7


@dataclass(frozen=True)
class HistoricalFundamental:
    """One frozen, versioned public filing or point-in-time financial record."""
    record_id: str
    ticker: str
    accounting_period_end: date
    first_public_at: datetime
    effective_revision_at: datetime
    source_reference: str
    payload_hash: str


@dataclass(frozen=True)
class HistoricalUniverseMember:
    ticker: str
    active_from: date
    active_to: date | None
    source_reference: str


@dataclass(frozen=True)
class HistoricalDecision:
    ticker: str
    action: str
    decided_at: datetime
    fundamental_record_id: str
    quote_observed_at: datetime
    quote_source: str


@dataclass(frozen=True)
class HistoricalDatasetManifest:
    start: date
    end: date
    source_name: str
    point_in_time_financials_verified: bool
    includes_delisted_and_failed_companies: bool
    corporate_actions_and_splits_verified: bool
    cash_dividends_complete: bool
    historical_currency_rates_complete: bool


def _aware(value: datetime) -> bool:
    return isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() is not None


def audit_historical_inputs(
    manifest: HistoricalDatasetManifest,
    universe: Sequence[HistoricalUniverseMember],
    fundamentals: Sequence[HistoricalFundamental],
    decisions: Sequence[HistoricalDecision],
) -> list[str]:
    """Reject omissions, stale/future data and survivorship-bias shortcuts."""
    issues: list[str] = []
    if manifest.start >= manifest.end:
        issues.append("dataset date range is invalid")
    if not manifest.source_name.strip():
        issues.append("historical data source is unidentified")

    coverage_flags = {
        "point_in_time_financials_verified": manifest.point_in_time_financials_verified,
        "includes_delisted_and_failed_companies": manifest.includes_delisted_and_failed_companies,
        "corporate_actions_and_splits_verified": manifest.corporate_actions_and_splits_verified,
        "cash_dividends_complete": manifest.cash_dividends_complete,
        "historical_currency_rates_complete": manifest.historical_currency_rates_complete,
    }
    for label, passed in coverage_flags.items():
        if passed is not True:
            issues.append(f"required dataset evidence missing: {label}")
    if not universe:
        issues.append("historical universe is empty")
    if not fundamentals:
        issues.append("historical financial snapshots are empty")
    if not decisions:
        issues.append("historical strategy decisions are empty")

    for u in universe:
        if not u.ticker.strip() or not u.source_reference.strip():
            issues.append("universe member lacks ticker/provenance")
        if u.active_to is not None and u.active_to < u.active_from:
            issues.append(f"{u.ticker}: invalid listing interval")

    records: dict[str, HistoricalFundamental] = {}
    for f in fundamentals:
        if not f.record_id.strip() or f.record_id in records:
            issues.append(f"duplicate or missing financial record ID: {f.record_id}")
        records[f.record_id] = f
        if not _aware(f.first_public_at) or not _aware(f.effective_revision_at):
            issues.append(f"{f.record_id}: financial publication timestamps require timezones")
            continue
        if f.accounting_period_end > f.first_public_at.date():
            issues.append(f"{f.record_id}: financial report cannot predate its accounting period end")
        if f.effective_revision_at < f.first_public_at:
            issues.append(f"{f.record_id}: amendment predates first release")
        if not f.source_reference.strip() or len(f.payload_hash) != 64 or any(ch not in "0123456789abcdef" for ch in f.payload_hash.lower()):
            issues.append(f"{f.record_id}: verifiable source/hash missing")

    seen_decisions: set[tuple[str, datetime]] = set()
    for d in decisions:
        if not _aware(d.decided_at) or not _aware(d.quote_observed_at):
            issues.append(f"{d.ticker}: historical decision/quote timestamps require timezones")
            continue
        key = (d.ticker, d.decided_at)
        if key in seen_decisions:
            issues.append(f"{d.ticker}: duplicate decision timestamp")
        seen_decisions.add(key)
        if not (manifest.start <= d.decided_at.date() <= manifest.end):
            issues.append(f"{d.ticker}: decision outside historical sample")
        if d.action not in ALLOWED_ACTIONS:
            issues.append(f"{d.ticker}: unrecognised Investment action")
        if not d.quote_source.strip():
            issues.append(f"{d.ticker}: historical quote source unidentified")
        age = d.decided_at - d.quote_observed_at
        if age < timedelta(0):
            issues.append(f"{d.ticker}: quote is from the future")
        if age > timedelta(days=MAX_HISTORICAL_QUOTE_AGE_DAYS):
            issues.append(f"{d.ticker}: historical quote is stale")
        in_universe = any(
            u.ticker == d.ticker
            and u.active_from <= d.decided_at.date()
            and (u.active_to is None or d.decided_at.date() <= u.active_to)
            for u in universe
        )
        if not in_universe:
            issues.append(f"{d.ticker}: ticker absent from point-in-time investable universe")

        f = records.get(d.fundamental_record_id)
        if f is None:
            issues.append(f"{d.ticker}: missing dated fundamental record")
            continue
        if f.ticker != d.ticker:
            issues.append(f"{d.ticker}: financial record refers to a different ticker")
        if not (_aware(f.first_public_at) and _aware(f.effective_revision_at)):
            continue
        if f.first_public_at > d.decided_at:
            issues.append(f"{d.ticker}: future earnings/financial filing look-ahead")
        if f.effective_revision_at > d.decided_at:
            issues.append(f"{d.ticker}: financial amendment/revision look-ahead")
    return issues


def require_historical_inputs(**kwargs) -> None:
    """Raise instead of silently producing a credible-looking backtest."""
    issues = audit_historical_inputs(**kwargs)
    if issues:
        raise ValueError("HISTORICAL BACKTEST NOT VALIDATED: " + " | ".join(issues[:15]))
