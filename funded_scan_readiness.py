"""Read-only Kraken Funded stock scan audit; never a trading permission gate.

Separates *missing/stale data* from *genuine technical rejection* and from
*manual-confirmation pending*. This avoids claiming a scanner works because a
unit test passes or mistakenly calling the absence of signals a data outage.

Only scans already captured by the user's live Streamlit session are measured;
no Kraken broker account connection or market requests are performed here.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable, Mapping

import pandas as pd


LIVE_SCAN_MAX_AGE = timedelta(minutes=15)
CONFIRMABLE_STATES = frozenset({"ENTRY READY", "FUNDED BREAKOUT PENDING OPEN"})
APPROVED_STATES = frozenset({"FUNDED BREAKOUT ENTRY CHECK", "FUNDED CORE ENTRY CHECK"})


@dataclass(frozen=True)
class FundedScanAudit:
    status: str
    total_expected: int
    rows_observed: int
    fresh_live_rows: int
    data_unavailable: int
    pending_live_quotes: int
    manual_review_ready_labels: int
    distinct_rejection_reasons: tuple[tuple[str, int], ...]
    blockers: tuple[str, ...]


def audit_funded_stock_scan(
    frame: pd.DataFrame | None,
    expected_tickers: Iterable[str],
    *,
    now: datetime | None = None,
) -> FundedScanAudit:
    """Audit scan observability; does NOT certify any entry or broker quote."""
    reference = now or datetime.now(timezone.utc)
    if reference.tzinfo is None or reference.utcoffset() is None:
        raise ValueError("UTC-aware audit timestamp required")
    expected = [str(t).strip().upper() for t in expected_tickers]
    if not expected or len(expected) != len(set(expected)):
        raise ValueError("expected funded universe requires unique stock tickers")
    expected_set = set(expected)
    if frame is None or frame.empty:
        return FundedScanAudit(
            "NO LIVE EVIDENCE",len(expected),0,0,0,0,0,(),
            ("No recorded current scan; run a stock-only scan to obtain evidence.",),
        )

    if "Ticker" not in frame.columns:
        return FundedScanAudit(
            "INVALID SCAN",len(expected),len(frame),0,0,0,0,(),
            ("Funded scanner output is missing its ticker column.",),
        )

    blockers: list[str] = []
    observed = [str(t).strip().upper() for t in frame["Ticker"]]
    duplicate = len(observed) != len(set(observed))
    unexpected = sorted(set(observed)-expected_set)
    missing = sorted(expected_set-set(observed))
    if duplicate:
        blockers.append("Duplicate ticker rows make scan coverage unreliable.")
    if unexpected:
        blockers.append("Unexpected ticker rows in the funded universe: " + ", ".join(unexpected[:8]))
    if missing:
        blockers.append(f"{len(missing)} of {len(expected)} eligible funded stocks are absent.")

    n_live, unavailable, pending, ready = 0,0,0,0
    rejects: Counter[str] = Counter()
    for _, row in frame.iterrows():
        ticker = str(row.get("Ticker") or "").strip().upper()
        if ticker not in expected_set:
            continue
        source = str(row.get("Scan source") or "").upper()
        stamp = pd.to_datetime(row.get("scanned_at"),errors="coerce",utc=True)
        fresh = False
        if not pd.isna(stamp) and source == "LIVE":
            age = pd.Timestamp(reference).tz_convert("UTC")-stamp
            fresh = pd.Timedelta(0) <= age <= pd.Timedelta(LIVE_SCAN_MAX_AGE)
        state = str(row.get("Technical state") or "").upper()
        result = str(row.get("Funded status") or "")
        if state == "DATA UNAVAILABLE" or "DATA UNAVAILABLE" in result:
            unavailable += 1
        if not fresh:
            if result.startswith("🟢"):
                blockers.append(f"{ticker}: READY label is not supported by a fresh live scan.")
            continue
        n_live += 1
        if state in CONFIRMABLE_STATES:
            pending += 1
        if state in APPROVED_STATES and result.startswith("🟢"):
            # This merely counts an apparent UI review label, not a verified
            # executable position or independently confirmed Kraken state.
            ready += 1
        elif state not in CONFIRMABLE_STATES:
            # Capture original technical reason instead of only the wrapper
            # status (which may say 'verify earnings/account' for all stocks).
            reason = str(row.get("Gate") or row.get("Technical state") or "UNCLASSIFIED")
            rejects[reason[:140]] += 1

    if n_live < len(expected):
        blockers.append(f"Fresh live data available for {n_live}/{len(expected)} funded stocks.")
    if unavailable:
        blockers.append(f"{unavailable} stock rows have missing/unusable price history.")
    if ready:
        blockers.append("App READY label(s) still require current Kraken account, bid/ask and earnings verification.")
    elif pending:
        blockers.append(f"{pending} setup(s) need current Kraken buy/sell quote and manual gate verification.")
    if not ready and not pending and n_live == len(expected) and unavailable == 0 and not blockers:
        blockers.append("All funded stocks scanned; no currently confirmed setup — do not force a trade.")

    data_blocked = duplicate or bool(unexpected or missing) or n_live != len(expected) or unavailable > 0
    status = (
        "DATA INCOMPLETE / STALE" if data_blocked
        else "MANUAL EXECUTION VERIFICATION" if ready or pending
        else "NO QUALIFIED LIVE SETUP"
    )
    return FundedScanAudit(
        status,len(expected),len(frame),n_live,unavailable,pending,ready,
        tuple(rejects.most_common(5)),tuple(dict.fromkeys(blockers)),
    )
