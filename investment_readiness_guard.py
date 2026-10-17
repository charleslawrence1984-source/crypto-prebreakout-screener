"""Fail-closed Investment publication gate for the long-term stock UI.

A current price stamp alone cannot prove the underlying fundamental or
technical research is complete. This module never changes the strategy's
scores, weights, holdings or the Trade scanner. It only withholds an
actionable Investment BUY label when its published research inputs fail
the existing cross-feed health contract.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from pipeline_publication_health import FEEDS, audit

_INVESTMENT_FEEDS = tuple(feed for feed in FEEDS if feed.key.startswith("investment_"))
_EXPECTED = {"investment_fundamentals", "investment_technicals", "investment_priority"}


def investment_publication_readiness(root: Path, *, now: datetime | None = None) -> dict:
    """Return explicit readiness and reasons, never inferring readiness from age alone."""
    if {feed.key for feed in _INVESTMENT_FEEDS} != _EXPECTED:
        return {"ready": False, "issues": ["Investment feed contract is incomplete"], "datasets": []}
    report = audit(root, now=now, definitions=_INVESTMENT_FEEDS)
    issues = []
    for feed in report["datasets"]:
        if feed["state"] == "CURRENT":
            continue
        details = "; ".join(str(s) for s in feed["issues"]) or "unverified publication"
        issues.append(f'{feed["feed"]}: {feed["state"]} ({details})')
    return {"ready": not issues, "issues": issues, "datasets": report["datasets"]}


def withhold_unverified_investment_buys(frame: Any, readiness: dict):
    """Keep a research row but downgrade a BUY until the underlying feeds pass.

    Operates on a copy so no prepared CSV/cache or historical audit is changed.
    This is display gating, NOT a strategy signal update or price calculation.
    """
    output = frame.copy()
    if output.empty or "Action" not in output.columns or readiness.get("ready") is True:
        return output

    candidates = output["Action"].astype(str).eq("BUY CANDIDATE")
    if not candidates.any():
        return output
    output.loc[candidates, "Action"] = "REVALUE"
    output.loc[candidates, "Valuation gate"] = "REVALUE"
    output.loc[candidates, "Decision reason"] = (
        "PUBLICATION NOT VERIFIED — Investment research feeds are stale, "
        "incomplete or unavailable; BUY label withheld until validation"
    )
    return output
