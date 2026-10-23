"""Read-only frozen forward funnel / outcome reconciliation.

Never upgrade missing research results to profits or BUY. Report date-censoring
when the prospective outcomes journal was evaluated before new captured scans.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json


def utc(raw):
    val=datetime.fromisoformat(str(raw).replace("Z","+00:00"))
    if val.tzinfo is None:
        raise ValueError("naive capture/evaluation timestamp")
    return val.astimezone(timezone.utc)


def reconcile(funnel, outcomes):
    if funnel.get("research_only") is not True or outcomes.get("research_only") is not True:
        raise ValueError("input is not frozen read-only research")
    if funnel.get("broker_trades_verified") != 0 or outcomes.get("broker_trades_executed") != 0:
        raise ValueError("historical broker verification requires separate source")
    observed=utc(funnel["audited_at_utc"])
    evaluated=utc(outcomes["evaluated_at_utc"])
    triggers=funnel.get("research_entry_trigger_observations",[])
    unique={(x["asset_type"],x["asset"],x["captured_at_utc"])
            for x in triggers}
    if len(unique)!=len(triggers):
        raise ValueError("duplicate frozen technical-trigger observation in funnel")
    paper_count=int(outcomes.get("original_technical_trigger_observations",0))
    evidence=outcomes.get("performance_evidence") or {}
    verified_closed=int(evidence.get("distinct_closed_paper_positions",0))
    if verified_closed>paper_count:
        raise ValueError("more closed positions than research triggers")
    gaps=funnel.get("observed_crypto_gaps_over_5_5h",[])
    reasons=[]
    if gaps:reasons.append("MISSED_RESEARCH_COVERAGE_WINDOWS")
    if observed>evaluated:reasons.append("OUTCOME_JOURNAL_OLDER_THAN_FUNNEL")
    if verified_closed==0:reasons.append("NO_HISTORICAL_CLOSED_PAPER_POSITIONS")
    return {
        "funnel_audit_utc":observed.isoformat(),
        "outcome_evaluation_utc":evaluated.isoformat(),
        "funnel_original_trigger_observations":len(triggers),
        "funnel_distinct_symbol_capture_observations":len(unique),
        "outcome_earlier_trigger_observations":paper_count,
        "distinct_closed_paper_positions":verified_closed,
        "unobserved_crypto_capture_gaps_over_5p5h":len(gaps),
        "risks":reasons,
        "research_coverage_complete":False if gaps else None,
        "profitability_established":False,
        "broker_funded_readiness_confirmed":False,
        "advice":"Keep original frozen snapshots; re-evaluate only when future candles exist. Do not substitute historic reruns for actually archived 4h signals.",
    }


def main():
    import argparse, pathlib, zipfile
    p=argparse.ArgumentParser()
    p.add_argument("--funnel-zip",required=True)
    p.add_argument("--outcomes-zip",required=True)
    p.add_argument("--out-dir",default="funded-forward-check")
    args=p.parse_args()
    def one_json(path):
        with zipfile.ZipFile(path) as z:
            matches=[n for n in z.namelist() if n.endswith(".json")]
            if len(matches)!=1:
                raise ValueError("expected exactly one frozen research JSON in artifact")
            return json.loads(z.read(matches[0]))
    result=reconcile(one_json(args.funnel_zip),one_json(args.outcomes_zip))
    out=pathlib.Path(args.out_dir)
    out.mkdir(parents=True,exist_ok=True)
    (out/"prospective_signal_quality.json").write_text(
        json.dumps(result,sort_keys=True,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(result,sort_keys=True))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
