"""Auditable bridge from real source-time snapshots to a future paper gate.

A source snapshot is not a trade. This reader never fabricates executions or
uses a post-signal future price for an entry. It reports exactly which
independent proofs are missing for each original cohort member.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

NEEDS=(
    "AUTHORIZED_PRIMARY_OR_LICENSED_CONSOLIDATED_DAILY_OHLCV",
    "MATCHED_INSTRUMENT_VENUE_RAW_ADJUSTMENT_CURRENCY",
    "POINT_IN_TIME_ISSUER_FINANCIALS_AND_OFFICIAL_EVENTS",
    "EXCHANGE_SESSION_AND_FRESH_BENCHMARK_AT_DECISION",
    "GBP_FX_LIQUIDITY_AND_NEXT_OPEN_GAP",
    "PAPER_BROKER_FILL_STOP_TARGET_FEES_AND_RISK_RULES",
    "EXPLICIT_OWNER_PAPER_TRACKING_AUTHORITY",
)
PRE_READY=frozenset({"PRE-CROSS READY","ENTRY READY"})


def eligibility(source:dict,listing:dict) -> dict:
    cohort=source.get("observations")
    if not isinstance(cohort,list) or not source.get("capture_created_utc"):
        raise ValueError("NO_REAL_TIME_STAMPED_PROSPECTIVE_COHORT")
    known={
        row["symbol"] for row in listing.get("listing",{}).get("listing_symbols_observed",[])
        if row.get("exchange_listing_observed") is True
    }
    out=[]
    for row in cohort:
        symbol=row.get("symbol")
        tech=row.get("technical_state")
        failures=list(NEEDS)
        if not (symbol in known and row.get("venue")=="nasdaq"):
            failures.insert(0,"NO_FIRST_PARTY_CURRENT_NASDAQ_LISTING_MATCH")
        if row.get("source_validation")!="PASS" or not row.get("source_session"):
            failures.insert(0,"NO_COMPLETED_UNADJUSTED_PROVIDER_HISTORY_ACCEPTED")
        if tech not in PRE_READY:
            failures.insert(0,"ORIGINAL_TRADE_TECHNICAL_SETUP_NOT_ENTRY_READY")
        out.append({
            "symbol":symbol,
            "observed_at_utc":row.get("first_captured_utc"),
            "session":row.get("source_session"),
            "technical_state_original":tech,
            "exchange_listing_source_matched": symbol in known,
            "independently_authorized_price_verified":False,
            "approved_paper_entry":False,
            "paper_order_submitted":False,
            "paper_fill_observed":False,
            "paper_return_realized":None,
            "eligibility":"BLOCKED_MISSING_ORIGINAL_ASOF_SOURCE_EVIDENCE",
            "blocking_evidence":failures,
        })
    return {
        "scope":"FROZEN_PROSPECTIVE_COHORT_FULL_PAPER_ELIGIBILITY_AUDIT",
        "source_captured_at_utc":source["capture_created_utc"],
        "source_cohort_size":len(out),
        "candidate_paper_entries":0,
        "approved_paper_entries":0,
        "recorded_fills":0,
        "realized_paper_returns":0,
        "live_trading_enabled":False,
        "status":"BLOCKED_AWAITING_AUTHORISED_FIRST_PARTY_PRICE_AND_FULL_ASOF_RULES",
        "rows":out,
        "limitations":[
            "Nasdaq current symbol directory is first-party listing, not historical membership or OHLCV",
            "Source provider OHLCV checksum is not independent price authentication",
            "No historical original-time financial, official event, GBP FX or next-open fill",
            "No fully approved paper trade can be created from captured source-only snapshots",
        ],
    }


def run(source:Path,listing:Path,output:Path) -> dict:
    source_bytes=source.read_bytes()
    listing_bytes=listing.read_bytes()
    report=eligibility(json.loads(source_bytes),json.loads(listing_bytes))
    report["source_snapshot_sha256"]=hashlib.sha256(source_bytes).hexdigest()
    report["listing_probe_sha256"]=hashlib.sha256(listing_bytes).hexdigest()
    output.write_text(json.dumps(report,sort_keys=True,indent=2)+"\n")
    return report


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--source",type=Path,required=True)
    p.add_argument("--listing",type=Path,required=True)
    p.add_argument("--output",type=Path,default=Path("trade_independent_paper_gate.json"))
    args=p.parse_args()
    out=run(args.source,args.listing,args.output)
    print(json.dumps({"status":out["status"],"samples":out["source_cohort_size"],
                      "approved_paper_entries":out["approved_paper_entries"]},indent=2))


if __name__=="__main__":
    main()
