"""Kraken APP Funded: conservative 4h mark-to-market risk bounds (RESEARCH).

Historic public Kraken OHLCVT is NOT an authentic Kraken Funded quote series.
This deliberately contrasts a previous idealized, assumed stop-first paper exit
with an *unprotected* 4h-low intratrade account-equity stress bound: the
Kraken APP Funded programme does not offer a native stop-loss order, so even
a technically credible stop is not proof of a corresponding actual fill.

Never label an OHLC-derived low below $970 as a CONFIRMED funded breach;
the timestamp ordering versus a hypothetical manual stop cannot be known.
Never label a closing target as CONFIRMED Kraken Funded challenge PASS.
"""
from __future__ import annotations

from hashlib import sha256
from io import BytesIO
import json
import math
from pathlib import Path
from zipfile import ZipFile

import pandas as pd

BARS = ("timestamp","open","high","low","close","volume","trades")
COSTS = (.4,.8,1.6)
RISK_PCTS = (.25,.5,1.0)
TARGET_UP = .12
FLOOR_DOWN = .03


def authentic_public_4h(zipfile_obj, coin, *, full_manifest=None):
    """Require exact archived bytes, no fuzzy coin substitution or future bars."""
    manifest = full_manifest or json.loads(zipfile_obj.read("FULL_selection_manifest.json"))
    if manifest.get("source_zip_full_sha256_verified") is not False or not manifest.get("zip_crc_integrity_verified"):
        raise ValueError("Kraken FULL archive partial-range verification caveat missing")
    name=f"FULL_{coin}_240.csv"
    item=manifest.get("selected_sources",{}).get(f"{coin}:240")
    if item is None or item.get("file")!=name:
        raise ValueError("current USD coin not in frozen approved source manifest")
    data=zipfile_obj.read(name)
    if sha256(data).hexdigest()!=item["sha256"]:
        raise ValueError("original OHLCV CSV SHA mismatch")
    frame=pd.read_csv(BytesIO(data),names=BARS,header=None)
    if frame.empty:
        raise ValueError("empty Kraken historical 4h")
    ts=pd.to_numeric(frame["timestamp"],errors="raise")
    if not ts.between(1e9,2e10).all():
        raise ValueError("Kraken archive timestamp not Unix seconds")
    frame["timestamp"]=pd.to_datetime(ts,unit="s",utc=True)
    frame=frame.sort_values("timestamp")
    if frame["timestamp"].duplicated().any():
        raise ValueError("duplicated Kraken candle timestamps")
    for col in ("open","high","low","close","volume"):
        frame[col]=pd.to_numeric(frame[col],errors="raise")
        if not frame[col].map(math.isfinite).all():
            raise ValueError("nonfinite spot price")
    if (frame[["open","high","low","close"]] <= 0).any().any() or (frame["volume"]<0).any():
        raise ValueError("nonpositive spot price or negative volume")
    if ((frame["high"] < frame[["open","close","low"]].max(axis=1)).any() or
        (frame["low"] > frame[["open","close","high"]].min(axis=1)).any()):
        raise ValueError("incorrect bar high or low")
    return frame.set_index("timestamp",drop=False)


def simulate_intrabar_account(rows, public_4h_by_coin, *,
                              cohort="ORIGINAL_BUY", risk_pct=.5,
                              roundtrip_cost=.8, initial=1000.0):
    """Serial unlevered long-only *stress bound*, not historical broker replay.

    Uses the ORIGINAL frozen 4h next-open paper plans/stop/target and modeled
    closed R; prevents buying more than the account balance (no leverage).
    For every bar of a paper position it records the most adverse mark against
    its intrabar low, assuming exit-bid friction but NO intrabar stop fill.
    """
    if not 0 < risk_pct <= 3 or roundtrip_cost not in COSTS or initial<=0:
        raise ValueError("unsupported account study assumptions")
    fields={"Coin","Signal bar","Counterfactual cohort","Decision",
            "Cost roundtrip %","Next 4h open proxy","Original stop",
            "Net R proxy","Bars to exit"}
    if not fields.issubset(rows):
        raise ValueError("missing frozen original risk study columns")
    df=rows.loc[(rows["Counterfactual cohort"]==cohort)
                & (rows["Decision"]=="MODELED_EXIT")
                & (rows["Cost roundtrip %"]==roundtrip_cost)].copy()
    if df.empty:
        return pd.DataFrame(),{
            "Account outcome":"NO_PAPER_ENTRIES","Research cohort":cohort,
            "Real Funded account outcome verified":False}
    df["stamp"]=pd.to_datetime(df["Signal bar"],utc=True)
    if df.duplicated(["Coin","stamp"]).any():
        raise ValueError("duplicated historical original candidate")
    df=df.sort_values(["stamp","Coin"]).reset_index(drop=True)
    equity=initial
    floor=initial*(1-FLOOR_DOWN)
    target=initial*(1+TARGET_UP)
    next_free=pd.Timestamp("1970-01-01T00:00:00Z")
    records=[]
    ignored=0
    final_state="NO_CLOSED_EQUITY_THRESHOLD"
    adverse_marks=0
    for _,row in df.iterrows():
        t=row["stamp"]+pd.Timedelta(hours=4)
        duration=int(row["Bars to exit"])
        if duration<1 or duration>90:
            raise ValueError("bad historic holding duration")
        if t<next_free:
            ignored+=1
            continue
        history=public_4h_by_coin.get(row["Coin"])
        if history is None:
            raise ValueError(f"missing public spot candle series {row['Coin']}")
        observed=history.loc[history.index>=t].head(duration)
        if len(observed)!=duration or not observed.index.equals(
                pd.date_range(t,periods=duration,freq="4h",tz="UTC")):
            raise ValueError("future 4h series missing or gapped — cannot assess intrabar")
        opening=float(row["Next 4h open proxy"])
        stop=float(row["Original stop"])
        rr=float(row["Net R proxy"])
        if not all(math.isfinite(x) for x in (opening,stop,rr)) or not 0<stop<opening:
            raise ValueError("invalid original paper account plan")
        if abs(float(observed["open"].iloc[0])/opening-1) > 1e-6:
            raise ValueError("unverified/original next-open changed")
        buy_price=opening*(1+roundtrip_cost/200)
        risk_per_unit=buy_price-stop
        if risk_per_unit<=0:
            raise ValueError("stop loss budget not feasible after entry cost")
        # Avoid accidental leveraged sizing; both are hypothetical caps.
        units=min((initial*risk_pct/100)/risk_per_unit,
                  equity / buy_price)
        planned_loss_usd=units*risk_per_unit
        mark_low_price=float(observed["low"].min())*(1-roundtrip_cost/200)
        min_public_mark=equity+units*(mark_low_price-buy_price)
        equity_start=equity
        equity=equity+planned_loss_usd*rr
        worst_breach=min_public_mark<=floor
        adverse_marks+=int(worst_breach)
        next_free=t+pd.Timedelta(hours=duration*4)
        records.append({
            "Coin":str(row["Coin"]),"Original signal UTC":row["stamp"].isoformat(),
            "Model entry UTC":t.isoformat(),
            "Model last holding bar UTC":str(observed["timestamp"].iloc[-1]),
            "Cohort":cohort,"Assumed cost roundtrip %":roundtrip_cost,
            "Assumed risk % starting equity":risk_pct,
            "Original cash funded equity pre-trade":round(equity_start,6),
            "Price entry with half-spread":round(buy_price,8),
            "Original fixed invalidation":round(stop,8),
            "Notional % pre-trade equity":round(units*buy_price/equity_start*100,4),
            "Planned original stop-risk USD":round(planned_loss_usd,6),
            "Minimum 4h intrabar mark if no executed stop":round(min_public_mark,6),
            "Possible 3% loss-line contact (public proxy)":bool(worst_breach),
            "Closed modeled equity":round(equity,6),
            "Already official funded breach proven":False,
            "Actual intrabar stop execution or quote verified":False,
        })
        if equity<=floor:
            final_state="CLOSED_EQUITY_BELOW_STATIC_FLOOR_PROXY"
            break
        if equity>=target:
            final_state="CLOSED_EQUITY_TARGET_PROXY_ONLY"
            break
    return pd.DataFrame(records),{
        "Account outcome":final_state,"Research cohort":cohort,
        "Historical paper positions evaluated":len(records),
        "Overlap excluded":ignored,
        "Possible intrabar public low crossings":adverse_marks,
        "Initial modeled equity":initial,
        "Fixed starting-equity floor":floor,
        "Fixed starting-equity profit target":target,
        "Final closed-equity proxy":round(equity,6),
        "Strict unlevered capital cap":True,
        "Available native protective stop order":False,
        "Real Funded account outcome verified":False,
        "Quote/price vintage/historical availability proven":False,
    }


def sensitivity(rows, candles):
    totals=[]
    for cohort in ("ORIGINAL_BUY","WATCH_READY_BYPASS"):
        for r in RISK_PCTS:
            for c in COSTS:
                _,summary=simulate_intrabar_account(
                    rows,candles,cohort=cohort,risk_pct=r,roundtrip_cost=c)
                totals.append({"Modeled risk pct":r,"Modeled roundtrip cost pct":c,
                               **summary})
    return pd.DataFrame(totals)
