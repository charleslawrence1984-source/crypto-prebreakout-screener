"""Reconcile archived original numeric indicators, historical risk and 4h lows.

Uses only immutable public-provider archives produced in PRIOR successful
research runs; does not backfill strategy, place orders or run scheduled scans.
"""
from __future__ import annotations

import argparse
from io import BytesIO
import json
from pathlib import Path
from zipfile import ZipFile

import pandas as pd

from funded_intrabar_risk_research import (
    authentic_public_4h, simulate_intrabar_account, RISK_PCTS, COSTS)
from funded_single_gate_observational import observational_gate_conditioning


def csv_read(zip_path, member):
    with ZipFile(zip_path) as z:
        return pd.read_csv(BytesIO(z.read(member)))


def run(args):
    trace=csv_read(args.trace_zip,"exact_original_rule_numeric_gates.csv")
    risk=csv_read(args.risk_zip,"all_original_buy_and_watch_counterfactuals.csv")
    if len(trace)!=4514:
        raise ValueError(f"Expected exact 4514 frozen original numeric states; got {len(trace)}")
    joined, strata=observational_gate_conditioning(trace,risk)
    output=Path(args.output_dir)
    output.mkdir(parents=True,exist_ok=True)
    joined.to_csv(output/"original_technical_feature_to_risk_joins.csv",index=False)
    strata.to_csv(output/"single_at_time_feature_risk_descriptive_strata.csv",index=False)
    with ZipFile(args.source_zip) as z:
        manifest=json.loads(z.read("FULL_selection_manifest.json"))
        price_data={coin:authentic_public_4h(z,coin,full_manifest=manifest)
                    for coin in sorted(trace["Coin"].unique())}
    rows=[];totals=[]
    for cohort in ("ORIGINAL_BUY","WATCH_READY_BYPASS"):
        for risk_pct in RISK_PCTS:
            for cost in COSTS:
                path,meta=simulate_intrabar_account(
                    risk,price_data,cohort=cohort,risk_pct=risk_pct,
                    roundtrip_cost=cost)
                totals.append({"Risk pct of original equity":risk_pct,
                               "Roundtrip assumed friction %":cost,**meta})
                if len(path):
                    rows.extend(path.to_dict("records"))
    pd.DataFrame(totals).to_csv(
        output/"static_3pct_floor_intrabar_public_low_account_sensitivity.csv",
        index=False)
    pd.DataFrame(rows).to_csv(
        output/"per_position_unlevered_intrabar_public_low_proxies.csv",index=False)
    meta={
        "frozen_original_numeric_decisions":len(trace),
        "matched_orig_signal_entry_cost_rows":len(joined),
        "feature_fold_exit_group_rows":len(strata),
        "account_scenarios":len(totals),
        "historical_source":"Original archived Kraken FULL USD spot 4h CSV plus PR48/49/50 frozen results",
        "official_source_url":"https://support.kraken.com/articles/funded",
        "official_rules":"Kraken APP Funded +12% original equity target, fixed -3% initial equity loss floor, no leverage or advanced stop orders",
        "minimum_public_spread_basis":"0.04% each side; historical quote-vintage NOT authenticated",
        "no_live_trading_rule_edited":True,
        "real_broker_marks_and_execution_confirmed":False,
        "data_label":"NOT KRAKEN FUNDED REAL ACCOUNT BACKTEST",
        "holdout_clean_for_future_model_selection":False,
        "single_gate_causal_relaxation_tested":False,
    }
    (output/"research_integrity_and_limits.json").write_text(
        json.dumps(meta,sort_keys=True,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({
        "summary":meta,
        "account_scenarios":totals,
        "observational_feature_rows":strata.head(4).to_dict("records"),
    },sort_keys=True))
    return meta


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--trace-zip",required=True)
    p.add_argument("--risk-zip",required=True)
    p.add_argument("--source-zip",required=True)
    p.add_argument("--output-dir",default="funded-intrabar-gate-research")
    run(p.parse_args())


if __name__=="__main__":
    main()
