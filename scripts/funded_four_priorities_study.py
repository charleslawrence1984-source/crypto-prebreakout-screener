"""One-off original-data 4-priority Kraken Funded research research runner.

Requires frozen PR #48 as-of original decisions, PR #49 risk outcomes, and
public prospective funnel and paper journals. Historical states are read only.
"""
from __future__ import annotations
import argparse
from io import BytesIO
import json
from pathlib import Path
from zipfile import ZipFile

import pandas as pd

from funded_app_challenge_scenarios import OFFICIAL_APP_FUNDED, sensitivity_table
from funded_gate_reason_evidence import gate_report, recommend_next_diagnostic
from funded_forward_evidence_qa import reconcile


def csv_from_zip(filename, member):
    with ZipFile(filename) as z:
        return pd.read_csv(BytesIO(z.read(member)))


def only_json(filename):
    with ZipFile(filename) as z:
        names=[n for n in z.namelist() if n.endswith(".json")]
        if len(names)!=1:
            raise ValueError("expected exactly one frozen JSON in research ZIP")
        return json.loads(z.read(names[0]))


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--original-zip",required=True)
    p.add_argument("--risk-zip",required=True)
    p.add_argument("--funnel-zip",required=True)
    p.add_argument("--paper-zip",required=True)
    p.add_argument("--out-dir",default="funded-four-research")
    args=p.parse_args()
    output=Path(args.out_dir);output.mkdir(parents=True,exist_ok=True)
    original=csv_from_zip(args.original_zip,"multi_year_original_asof_decisions.csv")
    outcomes=csv_from_zip(args.original_zip,"multi_year_mover_vs_nonmover_controls.csv")
    risk=csv_from_zip(args.risk_zip,"all_original_buy_and_watch_counterfactuals.csv")
    base,gate=gate_report(original,outcomes,risk)
    gate.to_csv(output/"original_gate_reason_vs_outcome.csv",index=False)
    base[["Coin","Signal open UTC","Research class","Rule reason group",
          "Research fold","Later mover","Net R proxy"]].to_csv(
          output/"all_original_signals_joined_to_movers_and_risk.csv",index=False)
    scenarios=sensitivity_table(risk)
    scenarios.to_csv(output/"kraken_app_funded_closed_equity_sensitivity.csv",
                     index=False)
    quality=reconcile(only_json(args.funnel_zip),only_json(args.paper_zip))
    (output/"prospective_coverage_and_outcomes.json").write_text(
        json.dumps(quality,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    meta={
        "original_33_coin_scorer_anchor_count":len(original),
        "future_label_anchors_20bars_10pct":int(
            ((outcomes["Horizon 4h bars"]==20)
             & (outcomes["Mover threshold %"]==10)).sum()),
        "comparison_report_rows":len(gate),
        "account_scenario_rows":len(scenarios),
        "kraken_app_funded_official_rules":OFFICIAL_APP_FUNDED,
        "diagnostic_next_step":recommend_next_diagnostic(gate),
        "historical_holdout_status":"EXPOSED_TO_DESCRIPTIVE_REVIEW_NO_TUNING",
        "official_broker_market_execution_verified":False,
        "historical_original_signal_rules_changed":False,
        "scheduler_or_live_system_changed":False,
    }
    (output/"readme_machine.json").write_text(
        json.dumps(meta,sort_keys=True,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({**meta,
        "account_scenario_preview":scenarios.to_dict("records")[:3],
        "prospective_evidence":quality,
    },sort_keys=True))
    if not len(gate) or not len(scenarios):
        raise RuntimeError("research lacked credible original-gate/simulator evidence")
    return 0


if __name__=="__main__":
    raise SystemExit(main())
