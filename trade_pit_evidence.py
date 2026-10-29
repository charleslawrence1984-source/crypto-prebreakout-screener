"""Strict source-time intake for FULL original Stocks Trade historical replays.

This is a *separate prerequisite* to the technical-only original Trade
research. It neither makes fabricated historic BUY decisions nor authenticates
documents: machine-checked metadata and SHA claims require manual source
review. A report whose data only exists today must never satisfy a 2024 date.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

REQUIRED_FIELDS = (
    "ticker", "venue_mic", "isin", "decision_close_utc",
    "technical_last_bar_session", "technical_source_capture_utc",
    "membership_source", "membership_asof_utc", "listing_effective_date",
    "financial_source", "financial_published_utc", "financial_period_end",
    "earnings_calendar_source", "earnings_calendar_observed_utc",
    "official_event_review_source", "official_event_review_observed_utc",
    "fx_source", "fx_session", "fx_observed_utc",
    "trailing_liquidity_window_end", "trailing_liquidity_source",
    "historical_cost_schedule_id", "source_evidence_sha256",
)
TIME_FIELDS = (
    "decision_close_utc", "technical_source_capture_utc",
    "membership_asof_utc", "financial_published_utc",
    "earnings_calendar_observed_utc", "official_event_review_observed_utc",
    "fx_observed_utc",
)
# Trading days must be verified with a venue-specific calendar by a distinct
# source-session contract. This metadata gate does NOT infer calendar sessions.


def utc_time(value: object) -> datetime:
    stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        raise ValueError("UTC_OFFSET_MISSING")
    return stamp.astimezone(timezone.utc)


def audit_receipt(row: dict) -> dict:
    """One prospective historical as-of eligibility receipt; fail closed."""
    issues = []
    for field in REQUIRED_FIELDS:
        if row.get(field) is None or row.get(field) == "":
            issues.append("MISSING_" + field.upper())
    times = {}
    for field in TIME_FIELDS:
        try:
            times[field] = utc_time(row.get(field))
        except (ValueError, TypeError, OverflowError):
            issues.append("BAD_TIMESTAMP_" + field.upper())
    decision = times.get("decision_close_utc")
    if decision is not None:
        for field in TIME_FIELDS:
            if field == "decision_close_utc" or field not in times:
                continue
            if times[field] > decision:
                issues.append("LOOKAHEAD_" + field.upper())
        dates = {
            "technical_last_bar_session": True,
            "listing_effective_date": False,
            "financial_period_end": False,
            "fx_session": False,
            "trailing_liquidity_window_end": False,
        }
        for field, exact in dates.items():
            try:
                day = datetime.strptime(str(row[field]), "%Y-%m-%d").date()
            except (ValueError, TypeError, KeyError):
                issues.append("BAD_DATE_" + field.upper())
                continue
            if day > decision.date():
                issues.append("FUTURE_DATE_" + field.upper())
            if exact and day != decision.date():
                issues.append("UNVERIFIED_SESSION_" + field.upper())
    if not re.fullmatch(r"[0-9a-fA-F]{64}", str(row.get("source_evidence_sha256") or "")):
        issues.append("INVALID_SOURCE_HASH")
    if not str(row.get("venue_mic") or "").isalnum() or len(str(row.get("venue_mic") or "")) != 4:
        issues.append("UNVERIFIED_MARKET_MIC")
    if not re.fullmatch("[A-Z]{2}[A-Z0-9]{10}", str(row.get("isin") or "")):
        issues.append("UNVERIFIED_ISIN")
    if not row.get("historical_cost_schedule_id"):
        issues.append("COST_SCHEDULE_UNAVAILABLE")
    return {
        "ticker": str(row.get("ticker") or ""),
        "status": (
            "FORMAT_COMPLETE_SOURCE_AUTHENTICITY_UNVERIFIED"
            if not issues else "BLOCKED_MISSING_OR_LOOKAHEAD_EVIDENCE"
        ),
        "issues": sorted(set(issues)),
        "independent_reference_verified": False,
        "approved_trade_buy_authorised": False,
    }


def audited_manifest(path: Path | None) -> dict:
    """Inspect declared primary-source receipts; never extrapolate missing rows."""
    summary = {
        "scope": "HISTORICAL_ASOF_EVIDENCE_GATE_RESEARCH_ONLY",
        "readiness": "NOT_VERIFIED", "manifest_available": False,
        "input_rows": 0, "format_complete_not_authenticated": 0,
        "blocked": 0, "records": [],
        "gates_missing": [
            "Independent source document and historical listing proof",
            "Official earnings/news and financial disclosure source archives",
            "Archived exchange-adjusted candle and FX source vintage",
            "Historical inactive/delisted universe and matched nonmover membership",
            "Actual next-open GBP settlement/spread, fees, slippage and portfolio drawdown",
            "Sufficient independent prospective paper results",
        ],
    }
    if path is None or not path.is_file():
        summary["blocking_reason"] = "NO_AUTHENTIC_POINT_IN_TIME_SOURCE_RECEIPT_MANIFEST"
        return summary
    try:
        blob = json.loads(path.read_text(encoding="utf-8"))
        records = blob["records"]
        if not isinstance(records, list):
            raise ValueError("RECORDS_NOT_LIST")
        summary["manifest_available"] = True
        summary["input_rows"] = len(records)
        summary["records"] = [audit_receipt(row) if isinstance(row, dict) else {
            "status": "BLOCKED_INVALID_RECORD", "issues": ["RECORD_NOT_OBJECT"],
            "independent_reference_verified": False,
            "approved_trade_buy_authorised": False,
        } for row in records]
        summary["blocked"] = sum(
            entry["status"].startswith("BLOCKED") for entry in summary["records"])
        summary["format_complete_not_authenticated"] = sum(
            entry["status"].startswith("FORMAT_COMPLETE") for entry in summary["records"])
        summary["manifest_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        return summary
    except (OSError, KeyError, TypeError, ValueError) as exc:
        summary["blocking_reason"] = "INVALID_MANIFEST_" + type(exc).__name__
        return summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--receipt-manifest", type=Path)
    parser.add_argument("--output", type=Path, default=Path("trade_pit_evidence_audit.json"))
    args = parser.parse_args()
    result = audited_manifest(args.receipt_manifest)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps({
        "status": result["readiness"], "input_rows": result["input_rows"],
        "missing": result.get("blocking_reason"),
        "not_authenticated": result["format_complete_not_authenticated"],
    }, indent=2))


if __name__ == "__main__":
    main()
