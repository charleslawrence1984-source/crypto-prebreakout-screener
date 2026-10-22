"""Kraken APP Funded evaluation: conservative, independent paper ledger.

Public Kraken app Funded terms (NOT the different Kraken PROP program):
https://support.kraken.com/articles/funded
12% profit target, fixed 3% starting-equity breach line; no daily loss reset,
no leverage, no advanced stop orders, 0.04% spread per side (minimum).
Actual personal instrument availability, broker quote path and full in-position
equity HIGH/LOW history are NOT verified by the historical R-only outcomes.
This reports *closed-equity boundary scenarios*, not a funded pass rate.
"""
from __future__ import annotations

import math
import pandas as pd

OFFICIAL_APP_FUNDED = {
    "target_pct": 12.0, "static_max_loss_pct": 3.0,
    "minimum_spread_one_side_pct": .04, "advanced_stop_orders": False,
    "leverage_permitted": False, "daily_loss_reset": False,
    "source": "https://support.kraken.com/articles/funded",
}
RISK_PCTS = (.25, .5, 1.0)


def simulate_closed_equity(rows, *, start_balance=1000.0, risk_pct=.5,
                           bucket="ORIGINAL_BUY", source_cost_pct=.8):
    """Single-position sequential exit ledger, with static original-equity floor.

    Prior trade is closed before a new one may open. Trade R already embeds
    assumed historical friction; we *do not* double-subtract spreads.
    Equity boundaries inspected at trade close ONLY. Intrabar unrealized
    equity / stop automation can't be recovered from this file, so even
    modeled PASS must be marked NOT BROKER VERIFIED and NOT CONTINUOUS CHECK.
    """
    if not 0 < risk_pct <= 3 or not start_balance > 0:
        raise ValueError("invalid starting account or risk")
    if source_cost_pct not in (.4, .8, 1.6):
        raise ValueError("unknown original modeled transaction cost")
    expected={"Coin","Signal bar","Counterfactual cohort","Decision","Cost roundtrip %",
              "Bars to exit","Net R proxy"}
    if not expected.issubset(rows):
        raise ValueError("missing authentic historical paper columns")
    filtered=rows.loc[
        (rows["Counterfactual cohort"] == bucket)
        & (rows["Decision"] == "MODELED_EXIT")
        & (rows["Cost roundtrip %"] == source_cost_pct)
    ].copy()
    if filtered.empty:
        return pd.DataFrame(), {
            "status":"NO_HISTORICAL_CLOSED_TRADES", "sample":0,
            "pass_verified":False, "funded_account_execution_verified":False,
        }
    filtered["start"] = pd.to_datetime(filtered["Signal bar"],errors="raise",utc=True)
    if filtered.duplicated(["Coin","start"]).any():
        raise ValueError("original signal repeated in same cost cohort")
    filtered = filtered.sort_values(["start","Coin"])
    capital=float(start_balance); floor=capital*.97; target=capital*1.12
    equity=capital; next_allowed=pd.Timestamp("1970-01-01",tz="UTC")
    out=[];ignored_overlap=0;chosen=0;status="UNRESOLVED_CLOSED_EQUITY"
    for _,event in filtered.iterrows():
        start=event["start"]+pd.Timedelta(hours=4)
        n=int(event["Bars to exit"])
        r=float(event["Net R proxy"])
        if not 1<=n<=90 or not math.isfinite(r):
            raise ValueError("invalid hypothetical exit / bars to exit")
        if start<next_allowed:
            ignored_overlap+=1
            continue
        finish=start+pd.Timedelta(hours=4*(n-1))
        risk_dollars=capital*risk_pct/100
        closed=equity+risk_dollars*r
        out.append({
            "Coin":event["Coin"],"Entry UTC":start.isoformat(),
            "Exit UTC":finish.isoformat(),"R outcome":r,
            "Account closed equity proxy":round(closed,6),
            "Closed-equity target touched":bool(closed>=target),
            "Closed-equity static floor touched":bool(closed<=floor),
            "Broker continuous mark-to-market observed":False,
        })
        chosen+=1
        equity=closed
        next_allowed=finish+pd.Timedelta(hours=4)
        if equity<=floor:
            status="CLOSED_EQUITY_BREACH_PROXY"
            break
        if equity>=target:
            status="CLOSED_EQUITY_TARGET_PROXY"
            break
    return pd.DataFrame(out), {
        "status":status,"start_balance":capital,
        "final_closed_equity_proxy":round(equity,6),
        "floor":floor,"target":target,
        "chosen_serial_positions":chosen, "overlapping_skipped":ignored_overlap,
        "risk_pct_original_capital":risk_pct,"historical_cost_pct":source_cost_pct,
        "funded_account_execution_verified":False,
        "pass_verified":False,
        "intrabar_breach_checked":False,
        "warning":"Closed-only equity is insufficient to assert Kraken Funded survival/pass",
    }


def sensitivity_table(rows, *, costs=(.4,.8,1.6), risks=RISK_PCTS):
    results=[]
    for cohort in ("ORIGINAL_BUY","WATCH_READY_BYPASS"):
        for cost in costs:
            for risk in risks:
                _,result=simulate_closed_equity(
                    rows, bucket=cohort, source_cost_pct=cost, risk_pct=risk)
                results.append({"Cohort":cohort,**result})
    return pd.DataFrame(results)
