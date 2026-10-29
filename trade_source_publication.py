"""Fail-closed research-only publication contract for real Stocks TRADE rows.

This is a separate proposed integration, not a claim of exchange-verified prices,
PIT fundamentals, official events, FX or authorized brokerage execution.
Cached source assertions and caller-supplied booleans can never create BUY.
"""
from __future__ import annotations

from datetime import datetime, timezone
import math

import pandas as pd

from trade_completed_bar_guard import expected_completed_session


def _session(value):
    try:
        if value is None or str(value).strip().lower() in ("", "nan", "none", "nat"):
            return None
        return pd.Timestamp(str(value)).date().isoformat()
    except (ValueError, TypeError, OverflowError):
        return None


def cached_source_assessment(record: dict, venue: str, now=None) -> dict:
    """Report structural source receipt; a PASS claim is *not* authentication."""
    result = {
        "status": "UNVERIFIED",
        "reason": "MISSING_OR_UNTRUSTED_COMPLETED_BAR_PROVENANCE",
        "expected_completed_session": None,
        "recorded_completed_session": _session(record.get("Last completed exchange session")),
        "price_independently_exchange_verified": False,
        "approved_trade_buy": False,
    }
    try:
        expected, calendar = expected_completed_session(venue, now)
        result["expected_completed_session"] = expected
        result["calendar"] = calendar
    except (ValueError, KeyError, RuntimeError, OverflowError):
        result["reason"] = "UNSUPPORTED_OR_UNAVAILABLE_EXCHANGE_CALENDAR"
        return result
    recorded = result["recorded_completed_session"]
    if not recorded:
        result["reason"] = "MISSING_COMPLETED_EXCHANGE_SESSION"
    elif recorded != expected:
        result["reason"] = "STALE_FUTURE_OR_MISMATCHED_EXCHANGE_SESSION"
    elif str(record.get("Price session status") or "").strip().upper() != "PASS":
        result["reason"] = "UNVERIFIED_SOURCE_SESSION_STATUS"
    elif not str(record.get("Completed bar OHLCV SHA256") or "").strip():
        result["reason"] = "MISSING_SOURCE_BAR_CHECKSUM"
    elif record.get("Price independently exchange verified") is True:
        # A self-declared boolean cannot substitute for official source evidence.
        result["status"] = "SOURCE_CLAIM_PRESENT_NOT_AUTHENTICATED"
        result["reason"] = "CLAIMED_EXCHANGE_VERIFICATION_WITHOUT_INDEPENDENT_SOURCE"
    else:
        result["status"] = "SOURCE_SESSION_PRESENT_NOT_AUTHENTICATED"
        result["reason"] = "YAHOO_CALENDAR_AND_CHECKSUM_NOT_INDEPENDENT_VERIFICATION"
    return result


def research_only_cached_trade_frame(frame: pd.DataFrame, venue: str, now=None) -> pd.DataFrame:
    """Preserve original technical detail; explicitly block all public actions."""
    if not isinstance(frame, pd.DataFrame):
        raise TypeError("CACHED_FRAME_MUST_BE_DATAFRAME")
    output = frame.copy(deep=True)
    if output.empty:
        return output
    assessments = [cached_source_assessment(record, venue, now)
                   for record in output.to_dict(orient="records")]
    output["Data verification"] = [x["status"] for x in assessments]
    output["Source check reason"] = [x["reason"] for x in assessments]
    output["Expected completed session"] = [
        x["expected_completed_session"] for x in assessments
    ]
    output["Approved Trade action"] = "WAIT"
    output["Original technical status (research)"] = (
        output["Technical state"] if "Technical state" in output.columns
        else output["Status"] if "Status" in output.columns
        else "UNKNOWN"
    )
    output["Status"] = "WAIT"
    output["Trade safety note"] = (
        "RESEARCH ONLY — price provenance, issuer/events/FX and execution "
        "receipts are not independently authenticated. No approved BUY."
    )
    return output


def protect_scan_result_rows(frame: pd.DataFrame, source_checks: dict,
                             now=None) -> pd.DataFrame:
    """After the ORIGINAL technical/fundamental engine, guard display fields."""
    if not isinstance(frame, pd.DataFrame):
        raise TypeError("SCAN_FRAME_MUST_BE_DATAFRAME")
    out = frame.copy(deep=True)
    if out.empty:
        return out
    checked = [source_checks.get(str(row.get("Ticker") or ""), {})
               for row in out.to_dict(orient="records")]
    out["Price source status"] = [x.get("status", "BLOCKED") for x in checked]
    out["Last completed exchange session"] = [
        x.get("last_completed_session") for x in checked
    ]
    out["Expected completed exchange session"] = [
        x.get("expected_completed_session") for x in checked
    ]
    out["Completed bar OHLCV SHA256"] = [
        x.get("last_completed_ohlcv_sha256") for x in checked
    ]
    out["Independent exchange authentication"] = False
    out["Original technical outcome (research)"] = (
        out["Status"] if "Status" in out.columns else "UNKNOWN"
    )
    original_reason = out.get("Reason",pd.Series("",index=out.index))
    out["Research-only original reason"] = original_reason
    if "Status" in out.columns:
        # RETRY/BLOCKED may be useful for diagnosis, but no positive state
        # from an unverified source/issuer can be interpreted as BUY.
        out["Status"] = out["Status"].replace({
            "PAPER CANDIDATE": "WAIT",
            "WATCH": "WAIT",
            "ENTRY READY": "WAIT",
            "READY TO VERIFY": "WAIT",
            "AWAITING NEXT OPEN": "WAIT",
        })
    else:
        out["Status"] = "WAIT"
    out["Reason"] = (
        "RESEARCH ONLY — source session is vendor-observed, not independently "
        "authenticated; issuer, official event, FX and broker opening-risk "
        "evidence remain unverified. No Trade BUY authorized."
    )
    out["Approved Trade action"] = "WAIT"
    return out
