"""Classify research audit completion without treating provider discrepancies as network failures.

Research-only: a successful job NEVER certifies a price feed or authorizes trading.
Operational failures, missing bars/sessions, bad data and unsafe state still fail.
"""
import argparse
import json
import os
from pathlib import Path

SYMBOLS = {"AAPL","MSFT","NVDA","GOOGL","AMZN"}
COMPLETE_STATES = {
    "RESEARCH_PAIRED_WITHIN_TOLERANCE",
    "REVIEW_PRICE_VOLUME_OR_TRIGGER_SENSITIVITY",
}

def evaluate(report):
    if not isinstance(report,dict):
        return False, "BLOCKED_INVALID_RECEIPT", []
    safety = {
        "credentials_present": True,
        "rights_acknowledged": True,
        "paper_account_verified": True,
        "public_data_licence_verified": False,
        "production_signals_modified": False,
        "paper_or_live_orders": False,
        "subscriptions_started": False,
    }
    if any(report.get(k) is not v for k,v in safety.items()):
        return False, "BLOCKED_CREDENTIAL_OR_RESEARCH_SAFETY_GUARD", []
    rows=report.get("rows")
    if not isinstance(rows,list) or len(rows)!=5:
        return False, "BLOCKED_INCOMPLETE_COHORT", []
    if {x.get("symbol") for x in rows if isinstance(x,dict)} != SYMBOLS:
        return False, "BLOCKED_INVALID_SYMBOL_SET", []
    if report.get("matched_session_pairs") != 5*252:
        return False, "BLOCKED_MISSING_PAIRED_HISTORY", []
    for row in rows:
        if not isinstance(row,dict) or row.get("status") not in COMPLETE_STATES:
            return False, "BLOCKED_PROVIDER_OR_CALENDAR_FAILURE", rows
        if row.get("matched_sessions") != 252:
            return False, "BLOCKED_MISSING_PAIRED_HISTORY", rows
        if row.get("missing_yahoo_dates") or row.get("extra_yahoo_dates"):
            return False, "BLOCKED_UNPAIRED_DATES", rows
        if row.get("approved_trade_signal") is not False or row.get("paper_or_live_order") is not False:
            return False, "BLOCKED_TRADE_SAFETY_GUARD", rows
    anomalies=sum(sum(row.get("field_mismatch_counts",{}).values()) for row in rows)
    sensitivity=sum(sum(row.get("technical_condition_disagreements",{}).values()) for row in rows)
    if anomalies or sensitivity:
        return True, "RESEARCH_COMPLETED_REVIEW_REQUIRED_NOT_PRICE_CERTIFIED", rows
    return True, "RESEARCH_COMPLETED_NO_THRESHOLD_FLAGS_NOT_PRICE_CERTIFIED", rows

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--receipt",type=Path,default=Path("alpaca_yahoo_252_metadata.json"))
    args=p.parse_args()
    try:
        report=json.loads(args.receipt.read_text())
    except (OSError,ValueError):
        report={}
    allowed,status,rows=evaluate(report)
    table=[
        "## Alpaca vs Yahoo: private research audit",
        "",
        "**"+status.replace("_"," ")+"**",
        "",
        "A completed research job is not exchange certification or a Trade BUY authorization.",
        "Threshold flags are held for independent review; no prices are patched.",
        "",
        "| Ticker | Paired sessions | Price-field flags | Volume flags | Technical sensitivity flags | Research status |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for row in rows:
        if not isinstance(row,dict): continue
        counts=row.get("field_mismatch_counts") or {}
        price=sum(int(counts.get(k,0)) for k in ("o","h","l","c"))
        volume=int(counts.get("v",0))
        tech=sum(int(x) for x in (row.get("technical_condition_disagreements") or {}).values())
        table.append("| {} | {} | {} | {} | {} | {} |".format(
            row.get("symbol"),row.get("matched_sessions",0),price,volume,tech,row.get("status")))
    table+=["","**Original trade release gate: CLOSED.** No signals or orders from this research audit."]
    summary=os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary,"a",encoding="utf8") as f:
            f.write("\n".join(table)+"\n")
    print(json.dumps({
        "status":status,"research_run_completed":allowed,
        "release_gate":"CLOSED_PENDING_INDEPENDENT_DATA_AND_ORIGINAL_TRADE_EVIDENCE",
        "flagged_symbols":[r["symbol"] for r in rows if r["status"]!="RESEARCH_PAIRED_WITHIN_TOLERANCE"],
        "paper_orders":0,"live_orders":0},sort_keys=True))
    if allowed and "REVIEW_REQUIRED" in status:
        print("::warning title=Alpaca/Yahoo research discrepancies::Comparison completed, but price/volume differences require review. Trade release remains blocked.")
    if not allowed:
        print("::error title=Alpaca/Yahoo audit cannot complete::"+status)
        raise SystemExit(1)

if __name__=="__main__":
    main()
