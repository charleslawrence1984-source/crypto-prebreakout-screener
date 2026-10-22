"""Original Kraken Funded signal-reason evidence, NOT a rule-tuning engine.

Follows frozen original outcomes from PR #48, paired with cost-adjusted
hypothetical trade outcomes from PR #49. Individual numeric gates such as
MACD, volume expansion or RR cannot be isolated from a broad reason label.
"""
from __future__ import annotations

import pandas as pd

SCOPES = {
    "too far below resistance":"RESISTANCE_DISTANCE",
    "pre-breakout candidate":"PREFILTER_CREDIBLE_TARGET",
    "broken resistance was lost":"RECLAIM_ACCEPTANCE",
    "bullish retest":"RETEST",
    "extended after breakout":"EXTENSION_RESET",
    "too late":"TOO_LATE",
    "shape filter":"SHAPE",
    "fresh resistance breakout":"BREAKOUT",
    "already above resistance":"RESISTANCE_ABOVE",
}


def reason_category(value):
    t=str(value or "").lower().split("; execution:")[0].strip()
    return next((label for phrase,label in SCOPES.items() if phrase in t),
                "UNCLASSIFIED_REASON")


def gate_report(decisions, outcomes, risk_results):
    required={"Coin","Signal open UTC","Research class","Original reason","Research fold",
              "Original technical score","Original R:R","24h OHLCV quote volume USD proxy"}
    if not required.issubset(decisions):
        raise ValueError("missing frozen point-in-time rulebook observations")
    anchor={"Coin","Decision bar open","Horizon 4h bars","Mover threshold %","Later mover"}
    if not anchor.issubset(outcomes):
        raise ValueError("missing independent future labels")
    if decisions.duplicated(["Coin","Signal open UTC"]).any():
        raise ValueError("duplicate signal source")
    base=decisions.copy()
    base["UTC"]=pd.to_datetime(base["Signal open UTC"],utc=True)
    base["Rule reason group"]=base["Original reason"].map(reason_category)
    labels=outcomes.loc[
        (outcomes["Horizon 4h bars"]==20)
        & (outcomes["Mover threshold %"]==10),
        ["Coin","Decision bar open","Later mover"]
    ].copy()
    labels["UTC"]=pd.to_datetime(labels["Decision bar open"],utc=True)
    if labels.duplicated(["Coin","UTC"]).any():
        raise ValueError("duplicate future label")
    joined=base.merge(labels.drop(columns="Decision bar open"),
                      on=["Coin","UTC"],validate="one_to_one",how="left")
    if joined["Later mover"].isna().any():
        raise ValueError("full historical cohort has missing outcomes")
    risk=risk_results.loc[
        (risk_results["Cost roundtrip %"]==.8)
        & (risk_results["Decision"]=="MODELED_EXIT"),
        ["Coin","Signal bar","Counterfactual cohort","Net R proxy","Exit reason"]
    ].copy()
    risk["UTC"]=pd.to_datetime(risk["Signal bar"],utc=True)
    if risk.duplicated(["Coin","UTC"]).any():
        raise ValueError("duplicate cost-adjusted hypothetical plan")
    joined=joined.merge(risk.drop(columns="Signal bar"),on=["Coin","UTC"],
                        validate="one_to_one",how="left")
    groups=[]
    for (reason,cls,fold),g in joined.groupby(
        ["Rule reason group","Research class","Research fold"],sort=True):
        observed=g[g["Net R proxy"].notna()]
        groups.append({
            "Reason category":reason,"Original status":cls,"Fold":fold,
            "Original as-of observations":len(g),
            "Future +10% close movers":int(g["Later mover"].sum()),
            "Future +10% close nonmovers":int((~g["Later mover"].astype(bool)).sum()),
            "Original at-time R:R present":int(g["Original R:R"].notna().sum()),
            "Original 24h volume proxy present":int(
                g["24h OHLCV quote volume USD proxy"].notna().sum()),
            "Modeled valid risk-aware entries":len(observed),
            "Modeled mean net R at 0.8%":(
                round(float(observed["Net R proxy"].mean()),4)
                if len(observed) else None),
            "Independent single-gate causal effect identified":False,
            "MACD gate time-series provided":False,
            "Resistance touches time-series provided":False,
            "Spot volume expansion gate time-series provided":False,
            "Cross-asset outcomes independent":False,
        })
    return joined,pd.DataFrame(groups)


def recommend_next_diagnostic(groups):
    if groups.empty:
        return "NO VALID HISTORICAL REASON COHORT"
    return ("Acquire frozen per-decision indicator/gate booleans from the "
            "original engine, then compare one gate at a time on genuinely "
            "new forward dates. Do not tune the already-examined holdout.")
