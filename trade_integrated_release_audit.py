"""Evidence-based draft Stocks TRADE integrated release-blocker report.

This runs on *actual checked-in cached gzip headers/rows* rather than
trusting manifest build timestamps. It never authenticates price vendors
or turns green unit tests into user-approved trading release permission.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
from pathlib import Path

from trade_lse_reference_contract import inspect_evidence
from trade_pit_evidence import audited_manifest

SESSION_FIELD="Last completed exchange session"
PASS_FIELD="Price session status"


def cached_price_provenance(root: Path) -> dict:
    directory=root/"prepared_trade_technicals"
    result={"cached_files_inspected":0,"cached_rows":0,
            "rows_without_completed_session":0,
            "rows_without_pass_status":0,
            "bad_files":[],
            "cached_dataset_independent_exchange_verified":False}
    for path in sorted(directory.glob("*.csv.gz")):
        result["cached_files_inspected"]+=1
        try:
            with gzip.open(path,"rt",encoding="utf-8-sig",newline="") as stream:
                data=csv.DictReader(stream)
                if not data.fieldnames or "Ticker" not in data.fieldnames:
                    result["bad_files"].append(path.name+":MISSING_TICKER_HEADER")
                    continue
                for row in data:
                    result["cached_rows"]+=1
                    if not (row.get(SESSION_FIELD) or "").strip():
                        result["rows_without_completed_session"]+=1
                    if (row.get(PASS_FIELD) or "").strip().upper()!="PASS":
                        result["rows_without_pass_status"]+=1
        except (OSError,UnicodeError,ValueError,csv.Error):
            result["bad_files"].append(path.name+":UNREADABLE_CACHED_SOURCE")
    return result


def audit_release(root: Path) -> dict:
    source=root/"stock_app.py"
    page=root/"stocks_page.py"
    nav=root/"stock_app_v2.py"
    actual_files=(source,page,nav)
    source_text=[p.read_text(encoding="utf-8") if p.is_file() else ""
                 for p in actual_files]
    cache=cached_price_provenance(root)
    lse=inspect_evidence(None,None,None)
    pit=audited_manifest(None)
    separate_pension_route=(
        ('url_path="pension"' in source_text[2]
         or "url_path='pension'" in source_text[2])
        and "investment_decision" not in source_text[1]
    )
    guard_staged=all(
        name.is_file() for name in (root/"trade_action_contract.py",
                                     root/"trade_completed_bar_guard.py",
                                     root/"trade_pit_evidence.py",
                                     root/"trade_forward_paper_observation.py")
    )
    blockers=[
        "INDEPENDENT_OHLCV_OR_LAST_COMPLETED_BAR_NOT_ESTABLISHED",
        "MAY_2024_LSE_OFFICIAL_HISTORICAL_SOURCE_NOT_OBTAINED",
        "HISTORICAL_PIT_FINANCIAL_EVENTS_FX_LIQUIDITY_NOT_AUTHENTICATED",
        "FULL_ASOF_RULEBOOK_DELISTINGS_EXECUTION_AND_HOLDOUT_NOT_TESTED",
        "NO_MATURE_APPROVED_FORWARD_PAPER_FILL_OUTCOMES",
        "REAL_BROWSER_PERSISTENCE_AND_MOBILE_UI_NOT_CERTIFIED",
        "SEPARATE_STOCKS_TRADE_PENSION_ROUTE_NOT_CERTIFIED",
        "OWNER_APPROVED_RELEASE_AND_PRODUCTION_DEPLOYMENT_NOT_PERFORMED",
    ]
    if cache["rows_without_completed_session"] or cache["rows_without_pass_status"] or cache["bad_files"]:
        blockers.insert(0,"CACHED_TRADE_PRICE_BAR_PROVENANCE_MISSING_OR_UNVERIFIED")
    if not guard_staged:
        blockers.insert(0,"INTEGRATION_SOURCE_MODULES_INCOMPLETE")
    if not separate_pension_route:
        blockers.insert(0,"STOCKS_PENSION_MIXED_LEGACY_ROUTE")
    return {
        "scope":"DRAFT_INTEGRATED_STOCKS_TRADE_RELEASE_AUDIT",
        "decision":"BLOCKED_NOT_LIVE_READY",
        "automated_tests_are_not_real_market_authentication":True,
        "actual_cache_provenance":cache,
        "lse_official_reference_status":lse["status"],
        "historical_pit_manifest_status":pit["blocking_reason"],
        "legacy_stocks_and_pension_separate":separate_pension_route,
        "all_isolated_trade_safety_modules_staged":guard_staged,
        "blocked_gates":blockers,
        "live_broker_order_permission":False,
        "production_deployed_by_this_workflow":False,
        "owner_release_approved":False,
        "source_sha256":{
            x.name:hashlib.sha256(x.read_bytes()).hexdigest() if x.exists() else None
            for x in actual_files
        },
    }


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--root",type=Path,default=Path("."))
    p.add_argument("--output",type=Path,default=Path("trade_integrated_release_audit.json"))
    args=p.parse_args()
    result=audit_release(args.root)
    args.output.write_text(json.dumps(result,indent=2,sort_keys=True)+"\n")
    print(json.dumps({"decision":result["decision"],
                      "cached_rows":result["actual_cache_provenance"]["cached_rows"],
                      "without_session":result["actual_cache_provenance"]["rows_without_completed_session"],
                      "unverified_gates":len(result["blocked_gates"])},indent=2))

if __name__=="__main__":
    main()
