"""Draft-only composition of CL Signal Stocks TRADE safeguards.

No production Trade entrypoint calls this module. It integrates the proposed
unmerged venue-session source guard, the ORIGINAL technical rulebook, the
source-time PIT metadata filter and the same display publication contract used
by the three legacy Trade surfaces. It never authenticates source documents or
permits a BUY/order/paper fill. Real complete evidence must be verified through
distinct services before this can become a production candidate.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pandas as pd

from trade_action_contract import REQUIRED_RECEIPTS, guarded_trade_publication
from trade_completed_bar_guard import validate_completed_trade_frame
from trade_pit_evidence import audit_receipt
from trade_rules import evaluate_price_setup

PROMOTABLE_TECH_STATES = frozenset({
    "ENTRY READY", "AWAITING NEXT OPEN", "PRE-CROSS READY",
})


def inspect_unreleased_trade(
    symbol: str,
    venue: str,
    price_history: pd.DataFrame | None,
    benchmark_history: pd.DataFrame | None,
    captured_at: datetime,
    pit_receipt: dict[str, Any] | None = None,
    claimed_publication_receipts: dict[str, Any] | None = None,
    technical_evaluator=evaluate_price_setup,
) -> dict[str, Any]:
    """Same single review-only action for quick, direct and ranked surfaces.

    Untrusted caller-claimed booleans are deliberately IGNORED.
    A completed Yahoo source is not proof of market authenticity.
    Synthetic metadata that looks complete is not a real issuer filing.
    """
    ts = pd.Timestamp(captured_at)
    if ts.tzinfo is None:
        raise ValueError("CAPTURE_TIME_MUST_HAVE_UTC_OFFSET")
    now = ts.tz_convert("UTC")
    output: dict[str, Any] = {
        "symbol": symbol, "venue": venue, "captured_utc": now.isoformat(),
        "scope": "UNMERGED_RESEARCH_INTEGRATION_NO_EXECUTION",
        "approved_order": False, "approved_paper_entry": False,
        "live_readiness": "BLOCKED",
        "technical_state": "NOT_EVALUATED",
        "technical_reason": "SOURCE_UNVERIFIED",
        "price_session": None,
        "pit_evidence_status": "MISSING",
        "publication_receipts_authenticated": False,
        "source_independently_authenticated": False,
    }
    price, source = validate_completed_trade_frame(price_history, venue, now)
    output["source_check"] = source
    if price is not None:
        output["price_session"] = source["last_completed_session"]
        if benchmark_history is None:
            output["technical_reason"] = "BENCHMARK_PRICE_HISTORY_UNAVAILABLE"
        else:
            benchmark_venue = "nyse" if venue in ("nasdaq", "nyse") else venue
            benchmark, bench_source = validate_completed_trade_frame(
                benchmark_history, benchmark_venue, now)
            output["benchmark_source_check"] = bench_source
            if benchmark is None:
                output["technical_reason"] = "BENCHMARK_" + bench_source["reason"]
            else:
                technical = technical_evaluator(price, benchmark)
                output["technical_state"] = str(technical.get("technical_state") or "BLOCKED")
                output["technical_reason"] = str(technical.get("technical_reason") or "UNKNOWN")
                output["technical_score"] = technical.get("technical_score")
    else:
        output["technical_reason"] = "PRICE_" + source["reason"]
    pit_result = audit_receipt(pit_receipt) if isinstance(pit_receipt, dict) else None
    if pit_result is not None:
        output["pit_evidence_status"] = pit_result["status"]
        output["pit_issues"] = pit_result["issues"]
    else:
        output["pit_issues"] = ["MISSING_AUTHENTIC_DATED_FILING_EVENT_FX_MEMBERSHIP_EVIDENCE"]

    output["untrusted_claims_supplied"] = bool(claimed_publication_receipts)
    technical_eligible = (
        output["technical_state"] in PROMOTABLE_TECH_STATES
        and price is not None
        and output.get("benchmark_source_check", {}).get("status") == "PASS"
    )
    proposed_state = "PAPER CANDIDATE" if technical_eligible else "BLOCKED"
    # All original 7 required receipts have no independently audited emitter
    # yet. Even complete claimed caller dictionaries must not override that.
    verified_receipts = {field: False for field in REQUIRED_RECEIPTS}
    projected = guarded_trade_publication({
        "approved_trade_status": proposed_state, **verified_receipts,
    })
    actions = {
        "quick_analyse": projected["action"],
        "direct_ticker": projected["action"],
        "ranked_trade": projected["action"],
    }
    output["rendered_action_contract"] = actions
    output["decision_reason"] = projected["reason"]
    output["blocked_gates"] = [
        "NO_INDEPENDENT_SOURCE_OHLCV_AUTHENTICITY",
        "NO_AUTHENTIC_PIT_FINANCIAL_EVENTS_MEMBERSHIP_FX_LIQUIDITY",
        "NO_VERIFIED_ORDERABLE_NEXT_OPEN_AND_COSTS",
        "NO_MATURE_APPROVED_FORWARD_PAPER_PERFORMANCE",
        "STOCKS_TRADE_PENSION_SEPARATE_ROUTE_UNRELEASED",
        "OWNER_RELEASE_APPROVAL_NOT_GIVEN",
    ]
    if source["status"] != "PASS":
        output["blocked_gates"].insert(0, "UNVERIFIED_COMPLETED_EXCHANGE_BAR")
    if not technical_eligible:
        output["blocked_gates"].insert(0, "ORIGINAL_APPROVED_TECHNICAL_SETUP_NOT_ELIGIBLE")
    if len(set(actions.values())) != 1 or list(actions.values()) != ["WAIT"] * 3:
        raise AssertionError("INTEGRATION_MUST_FAIL_CLOSED_NO_BUY")
    return output
