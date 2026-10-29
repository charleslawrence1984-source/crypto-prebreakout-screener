"""Compare two genuinely captured original Trade observation JSON snapshots.

The source-only journal can observe market closing-price changes. It cannot
call them approved paper returns, because no dated broker fill/gap/risk event,
issuer PIT evidence, or authenticated execution entry exists.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import pandas as pd

from trade_forward_paper_observation import later_price_observation


def _ts(value):
    stamp=pd.Timestamp(value)
    if stamp.tzinfo is None:
        raise ValueError("NAIVE_CAPTURE_TIME")
    return stamp.tz_convert("UTC")


def compare_cohorts(prior: dict, later: dict) -> dict:
    old=_ts(prior["capture_created_utc"])
    now=_ts(later["capture_created_utc"])
    if now <= old:
        raise ValueError("FOLLOWUP_MUST_HAVE_LATER_ACTUAL_CAPTURE_TIME")
    baseline=prior.get("observations")
    followup=later.get("observations")
    if not isinstance(baseline,list) or not isinstance(followup,list):
        raise ValueError("MISSING_IMMUTABLE_OBSERVATIONS")
    if prior.get("approved_trade_entries") != 0 or later.get("approved_trade_entries") != 0:
        raise ValueError("SOURCE_OBSERVATIONS_CANNOT_BE_APPROVED_PAPER_TRADES")
    first={}
    for row in baseline:
        if row.get("symbol") in first or not row.get("observation_id"):
            raise ValueError("DUPLICATE_OR_UNHASHED_FIRST_COHORT")
        if _ts(row["first_captured_utc"]) != old:
            raise ValueError("BASELINE_ROW_TIMESTAMP_MISMATCH")
        first[row["symbol"]]=row
    latest={}
    for row in followup:
        if row.get("symbol") in latest or not row.get("observation_id"):
            raise ValueError("DUPLICATE_OR_UNHASHED_LATER_COHORT")
        if _ts(row["first_captured_utc"]) != now:
            raise ValueError("FOLLOWUP_ROW_TIMESTAMP_MISMATCH")
        latest[row["symbol"]]=row
    if set(first)!=set(latest):
        raise ValueError("SURVIVORSHIP_COHORT_MEMBERSHIP_CHANGED")
    comparisons=[]
    for symbol in sorted(first):
        if first[symbol]["venue"]!=latest[symbol]["venue"]:
            raise ValueError("EXCHANGE_VENUE_CHANGED")
        change=later_price_observation(first[symbol],latest[symbol])
        change["symbol"]=symbol
        change["original_baseline_observation_id"]=first[symbol]["observation_id"]
        change["later_observation_id"]=latest[symbol]["observation_id"]
        comparisons.append(change)
    return {
        "scope":"SOURCE_ONLY_DESCRIPTIVE_FORWARD_COMPARISON",
        "earlier_captured_utc":old.isoformat(),
        "later_captured_utc":now.isoformat(),
        "readiness":"NOT_VERIFIED",
        "approved_paper_trades":0,
        "realized_strategy_returns_available":False,
        "comparisons":comparisons,
        "historical_backfill_or_survivor_reselection":False,
    }


def compare_files(prior_path: Path | None, later_path: Path | None,
                  expected_prior_sha256: str | None=None) -> dict:
    if prior_path is None or later_path is None:
        return {
            "scope":"NO_PREEXISTING_LATER_IMMUTABLE_SNAPSHOT",
            "readiness":"NOT_VERIFIED","approved_paper_trades":0,
            "status":"BLOCKED_MISSING_REQUIRED_TWO_SNAPSHOTS",
        }
    prior_bytes=prior_path.read_bytes()
    later_bytes=later_path.read_bytes()
    first_sha=hashlib.sha256(prior_bytes).hexdigest()
    if not expected_prior_sha256:
        raise ValueError("PRIOR_SNAPSHOT_EXPECTED_HASH_REQUIRED")
    if first_sha!=expected_prior_sha256:
        raise ValueError("PRIOR_SNAPSHOT_SHA256_MISMATCH")
    report=compare_cohorts(json.loads(prior_bytes),json.loads(later_bytes))
    report["prior_file_sha256"]=first_sha
    report["later_file_sha256"]=hashlib.sha256(later_bytes).hexdigest()
    return report


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--prior",type=Path)
    p.add_argument("--later",type=Path)
    p.add_argument("--prior-sha256")
    p.add_argument("--output",type=Path,default=Path("trade_forward_cohort_comparison.json"))
    args=p.parse_args()
    result=compare_files(args.prior,args.later,args.prior_sha256)
    args.output.write_text(json.dumps(result,sort_keys=True,indent=2)+"\n")
    print(json.dumps({
        "readiness":result["readiness"],
        "status":result.get("status","COMPARISON_ONLY"),
        "paper_trades":result["approved_paper_trades"],
        "cohort":len(result.get("comparisons",[])),
    },indent=2))

if __name__=="__main__":
    main()
