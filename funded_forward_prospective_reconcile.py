"""Join only NEW immutable prospective coverage and public-candle paper journals.

A newly reported original trigger is not a funded order; a WATCH isn't BUY.
Historical paper status with no completed follow-up bars is CENSORED, NOT loss
or no trade. A 4h observed-bin gap is UNKNOWN, not missed profitable entry.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

from funded_forward_slot_integrity import parse_utc


def reconcile_coverage_and_paper(coverage, paper):
    if coverage.get("schema")!="cl-signal-funded-prospective-4h-coverage-v1":
        raise ValueError("not the authentic source-only 4h coverage report")
    if paper.get("schema")!="cl-signal-funded-forward-daily-journal-v1":
        raise ValueError("not the original source-only public candle journal")
    if paper.get("research_only") is not True or paper.get("broker_trades_executed")!=0:
        raise ValueError("invalid risk of treating paper trades as broker orders")
    if coverage.get("funded_market_quotes_or_execution_verified") is not False:
        raise ValueError("broker-quote evidence cannot be assumed")
    scan_time=parse_utc(coverage["audited_at_utc"])
    paper_time=parse_utc(paper["evaluated_at_utc"])
    if paper_time<scan_time:
        raise ValueError("public future-candle evaluation preceded frozen signal audit")
    evidence=paper.get("performance_evidence") or {}
    closed=evidence.get("distinct_closed_paper_positions")
    if not isinstance(closed,int) or closed<0:
        raise ValueError("distinct closed paper position count absent/invalid")
    paper_triggers=paper.get("original_technical_trigger_observations")
    if not isinstance(paper_triggers,int) or paper_triggers<0:
        raise ValueError("original frozen trigger evidence absent")
    notes=[]
    if coverage["unobserved_crypto_4h_bins"]>0:
        notes.append("UNOBSERVED_4H_BIN_IS_UNKNOWN_NOT_NO_SIGNAL")
    if coverage["multiple_crypto_captures_in_same_4h_bin"]>0:
        notes.append("DUPLICATE_CAPTURE_BUCKET_NOT_INDEPENDENT_TRADE")
    if closed==0:
        notes.append("NO_REAL_CLOSED_FORWARD_PAPER_OUTCOME_YET")
    if (coverage["source_crypto_scans"] != paper.get("crypto_snapshots")
            or coverage["source_stock_scans"] != paper.get("stock_snapshots")):
        notes.append("CAPTURE_SOURCE_UNIVERSES_CHANGED_BETWEEN_TWO_READ_ONLY_RUNS")
    # Each observed original crypto BUY is a subset of all original stock+coin triggers
    if coverage["original_BUY_capture_observations"]>paper_triggers:
        notes.append("PAPER_JOURNAL_LACKS_LATEST_CRYPTO_TRIGGER_COHORT")
    result={
        "schema":"cl-signal-funded-prospective-reconciliation-v1",
        "captured_source_audit_utc":scan_time.isoformat(),
        "later_public_candle_research_evaluation_utc":paper_time.isoformat(),
        "original_crypto_trigger_observations":coverage["original_BUY_capture_observations"],
        "original_coin_and_bar_unique_triggers":coverage["original_BUY_unique_asset_plus_completed_bar"],
        "paper_all_asset_original_trigger_observations":paper_triggers,
        "historical_distinct_closed_paper_positions":closed,
        "paper_shadow_status_counts":paper.get("shadow_status_counts",{}),
        "crypto_4h_capture_coverage_pct":coverage["4h_capture_coverage_pct"],
        "unobserved_4h_capture_bins":coverage["unobserved_crypto_4h_bins"],
        "warnings":notes,
        "fully_verified_Funded_broker_market_eligibility":False,
        "broker_equity_or_orders_accessed":False,
        "evidence_of_positive_forward_edge":False,
        "new_live_trading_rules_applied":False,
    }
    return result


def main():
    import argparse
    parser=argparse.ArgumentParser()
    parser.add_argument("--coverage-json",required=True)
    parser.add_argument("--paper-json",required=True)
    parser.add_argument("--out-dir",default="funded-forward-evidence-integrity")
    args=parser.parse_args()
    def one_json(path):
        path=Path(path)
        if path.is_dir():
            matches=list(path.glob("*.json"))
            if len(matches)!=1:
                raise ValueError("Ambiguous/empty new paper observation journals")
            path=matches[0]
        return json.loads(path.read_text(encoding="utf-8"))
    data=reconcile_coverage_and_paper(
        one_json(args.coverage_json),one_json(args.paper_json))
    out=Path(args.out_dir);out.mkdir(exist_ok=True,parents=True)
    (out/"current_prospective_paper_vs_capture_audit.json").write_text(
        json.dumps(data,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    print(json.dumps(data,sort_keys=True))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
