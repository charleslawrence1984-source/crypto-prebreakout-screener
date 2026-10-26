"""ONE-at-a-time diagnostic gate-conditioned historical paper return census.

Ablation here means *FILTERING previously documented entry-eligible WATCH
counterfactuals using one frozen pre-outcome observed feature at a time*.
It does NOT override an original veto to manufacture retrospective BUYs,
reconstruct causality, or tune the exposed holdout.
"""
from __future__ import annotations

import math
import numpy as np
import pandas as pd

BOOLEAN_DIAGNOSTICS={
    "MACD_HISTOGRAM_IMPROVING":"4h MACD histogram improving",
    "MACD_HISTOGRAM_POSITIVE":"4h MACD histogram above zero",
    "AT_LEAST_TWO_RESISTANCE_TESTS":"Number of resistance tests >=2",
    "SHAPE_ORIGINAL_VALID":"Shape original true",
    "RSI_4H_NOT_OVERBOUGHT":"RSI >80",
}
COSTS=(.4,.8,1.6)


def _bool_value(value):
    if isinstance(value,(bool,np.bool_)):return bool(value)
    if isinstance(value,str) and value.lower() in ("true","false"):
        return value.lower()=="true"
    raise ValueError("research gate boolean must be true/false, not a missing truthy string")


def observational_gate_conditioning(trace,risk):
    columns={"Coin","Historical 4h open UTC","Original historical research class",
             "Original research fold"} | set(BOOLEAN_DIAGNOSTICS.values())
    if not columns.issubset(trace):
        raise ValueError("missing frozen original numeric indicator history")
    needed={"Coin","Signal bar","Counterfactual cohort","Cost roundtrip %",
            "Decision","Net R proxy","Exit reason"}
    if not needed.issubset(risk):
        raise ValueError("missing original frozen stop-aware research outcomes")
    t=trace.copy()
    t["utc"]=pd.to_datetime(t["Historical 4h open UTC"],errors="raise",utc=True)
    if t.duplicated(["Coin","utc"]).any():
        raise ValueError("duplicate original scored technical observation")
    r=risk.loc[risk["Counterfactual cohort"].isin(["ORIGINAL_BUY","WATCH_READY_BYPASS"])].copy()
    r["utc"]=pd.to_datetime(r["Signal bar"],errors="coerce",utc=True)
    # rows where original plan never qualified are not fabricated trades
    r=r.loc[r["Decision"]=="MODELED_EXIT"]
    if r.duplicated(["Coin","utc","Cost roundtrip %"]).any():
        raise ValueError("duplicate frozen research outcome for one cost level")
    merge=t.merge(r[["Coin","utc","Counterfactual cohort","Cost roundtrip %",
                     "Net R proxy","Exit reason"]],
                  on=["Coin","utc"],how="inner",validate="one_to_many")
    if merge.empty:
        raise ValueError("no matched original as-of gated research positions")
    if not merge["Cost roundtrip %"].isin(COSTS).all():
        raise ValueError("unexpected non-reviewed cost assumption")
    if not (((merge["Original historical research class"]=="WATCH") &
             (merge["Counterfactual cohort"]=="WATCH_READY_BYPASS"))
             | ((merge["Original historical research class"]=="PROXY_BUY_UNVERIFIED") &
                (merge["Counterfactual cohort"]=="ORIGINAL_BUY"))).all():
        raise ValueError("counterfactual cohort mutated an original history state")
    results=[]
    for name,column in BOOLEAN_DIAGNOSTICS.items():
        flags=merge[column].map(_bool_value)
        # For "not overbought", invert original RSI >80 boolean.
        if name=="RSI_4H_NOT_OVERBOUGHT":
            flags=~flags
        work=merge.assign(_passes=flags)
        for (cohort,cost,fold),g in work.groupby(
                ["Counterfactual cohort","Cost roundtrip %","Original research fold"]):
            for selected in (True,False):
                segment=g.loc[g["_passes"]==selected]
                if not len(segment):
                    mean=None
                else:
                    mean=float(segment["Net R proxy"].mean())
                    if not math.isfinite(mean):raise ValueError("invalid modeled return")
                results.append({
                    "Frozen diagnostic feature":name,
                    "Feature passed at original historical clock":selected,
                    "Original strategy cohort":cohort,
                    "Research fold already examined":fold,
                    "Original assumed roundtrip cost %":cost,
                    "Observed stop-first paper examples":len(segment),
                    "Mean hypothetical R":round(mean,5) if mean is not None else None,
                    "Paper stop exits":int(segment["Exit reason"].astype(str).str.startswith("STOP").sum()),
                    "Paper target exits":int((segment["Exit reason"]=="TARGET").sum()),
                    "Minimum sample for even descriptive reporting met":len(segment)>=30,
                    "Independent single-rule causal change demonstrated":False,
                    "Originally unqualified events promoted to BUY":False,
                    "Funded app executable quote verified":False,
                })
    return merge,pd.DataFrame(results)
