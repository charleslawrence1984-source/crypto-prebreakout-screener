"""Missed-mover/nonmover *descriptive* paired-cohort audit.

The technical-only original-rule historical pilot counts each decision-time
state before looking at later price outcomes. We must not call any such
post-hoc result a true missed Trade BUY without point-in-time corporate,
venue, earnings, FX, liquidity, broker, and delisted-constituent evidence.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def summarize_paired_cohorts(report: dict) -> dict:
    output={
        "scope":"CURRENT_SURVIVORS_TECHNICAL_ONLY_PAIRED_MOVERS_NONMOVERS",
        "readiness":"NOT_VERIFIED",
        "approved_historical_trade_entries":0,
        "historical_constituents_reconstructed":False,
        "asof_fundamentals_and_events_verified":False,
        "rows":[],
        "censored":[],
        "limitations":[
            "No contemporary PIT issuer/earnings/news/FX/broker evidence",
            "Cohorts include current surviving symbols only, not historical universe",
            "Future closes used to LABEL outcomes after as-of states; never to make a decision",
            "Daily completed OHLCV Yahoo adjusted proxy is not independent source",
            "Historical data prior to past corporate actions can be adjusted retrospectively",
        ],
    }
    symbols=report.get("symbols",[])
    for s in symbols:
        ticker=s.get("symbol","UNKNOWN")
        counts=s.get("counters",{})
        if "error" in s:
            output["censored"].append({"ticker":ticker,"reason":"SOURCE_FAILURE","type":type(s["error"]).__name__})
            continue
        for sessions in (5,10,20):
            anchors=int(counts.get(f"mover_{sessions}bar_ANCHORS",0))
            censored=int(counts.get(f"mover_{sessions}bar_CENSORED",0))
            output["censored"].append({"ticker":ticker,"sessions":sessions,"anchors":anchors,
                                       "censored":censored})
            for threshold in (5,10,20):
                bucket={}
                for key,value in counts.items():
                    suffix=f"_{sessions}bar_{threshold}pct_"
                    if not key.startswith("paired"+suffix):
                        continue
                    tail=key[len("paired"+suffix):]
                    if tail.endswith("_NONMOVER"):
                        state=tail[:-len("_NONMOVER")]
                        tag="NONMOVER"
                    elif tail.endswith("_MOVER"):
                        state=tail[:-len("_MOVER")]
                        tag="MOVER"
                    else:
                        raise ValueError("UNKNOWN_OUTCOME_LABEL")
                    bucket.setdefault(state,{"MOVER":0,"NONMOVER":0})[tag]+=int(value)
                total=sum(sum(x.values()) for x in bucket.values())
                if total!=anchors:
                    raise ValueError(f"PAIR_COUNT_MISMATCH:{ticker}:{sessions}:{threshold}:{total}/{anchors}")
                for state in sorted(bucket):
                    observed=bucket[state]
                    n=observed["MOVER"]+observed["NONMOVER"]
                    output["rows"].append({
                        "ticker":ticker,
                        "forward_sessions":sessions,"rise_threshold_pct":threshold,
                        "original_asof_technical_state":state,
                        "future_mover":observed["MOVER"],
                        "future_nonmover":observed["NONMOVER"],
                        "total_completed_future_labels":n,
                        "descriptive_mover_share_pct": (
                            round(observed["MOVER"]/n*100,5) if n else None),
                        "interpretation":"EX_POST_LABEL_ONLY_NOT_MISSED_APPROVED_BUY",
                    })
    return output


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--pilot",type=Path)
    parser.add_argument("--output",type=Path,default=Path("trade_mover_pair_diagnostics.json"))
    args=parser.parse_args()
    if args.pilot is None or not args.pilot.is_file():
        result={
            "readiness":"NOT_VERIFIED","scope":"NO_SOURCE_HISTORICAL_PILOT",
            "status":"BLOCKED_NO_GENUINE_HISTORICAL_RESEARCH_INPUT","rows":[],
        }
    else:
        result=summarize_paired_cohorts(json.loads(args.pilot.read_text()))
    args.output.write_text(json.dumps(result,sort_keys=True,indent=2)+"\n")
    print(json.dumps({"status":result.get("status","DESCRIPTIVE_ONLY"),
                      "readiness":result["readiness"],
                      "strata":len(result["rows"])},indent=2))
if __name__=="__main__":
    main()
