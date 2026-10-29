"""Conservative publication contract for legacy Stocks Trade UI surfaces.

The full approved Trade screener has its own WATCH/BLOCKED/PAPER CANDIDATE
states. Legacy score-only paths must never manufacture BUY independently.
This function is a display guard, NOT an engine/scoring rule change and NOT
an assertion that receipts supplied by callers were independently verified.
No caller may treat READY TO VERIFY as an order instruction.
"""
from __future__ import annotations

from collections.abc import Mapping

# These must be provided by a separate independently audited as-of verifier.
# Existing score-only legacy dictionaries do not contain such receipts.
REQUIRED_RECEIPTS = (
    "completed_exchange_bar_verified",
    "approved_asof_technical_verified",
    "asof_fundamentals_verified",
    "official_event_and_news_verified",
    "historical_fx_and_liquidity_verified",
    "opening_gap_and_risk_verified",
    "broker_venue_orderability_verified",
)
APPROVED_REVIEW_STATES = frozenset({"READY TO VERIFY", "PAPER CANDIDATE"})


def guarded_trade_publication(record: Mapping | None) -> dict[str, object]:
    """Return WAIT by default, or a *manual-review* state; never BUY.

    Even all positive receipts still yield READY TO VERIFY. The caller must
    obtain genuine source evidence and user review before any trade; booleans
    are not cryptographic proof and are not accepted as execution authority.
    """
    if not isinstance(record, Mapping):
        return {"action": "WAIT", "reason": "Approved Trade research evidence unavailable."}

    state = str(record.get("approved_trade_status") or "").strip().upper()
    if state not in APPROVED_REVIEW_STATES:
        return {
            "action": "WAIT",
            "reason": "Legacy scores cannot authorise BUY; no verified original Trade review status.",
        }

    missing = [key for key in REQUIRED_RECEIPTS if record.get(key) is not True]
    if missing:
        return {
            "action": "WAIT",
            "reason": "Full approved Trade source evidence is missing or unverified: "
                      + ", ".join(missing) + ".",
        }

    return {
        "action": "READY TO VERIFY",
        "reason": "All claimed Trade evidence receipts need independent review; "
                  "manual verification required, not a BUY order.",
    }
