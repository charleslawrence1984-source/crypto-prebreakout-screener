"""Original Kraken Funded indicator-trace audit for frozen historical decisions.

Recompute original unmodified crypto_rule_engine.score_setup at each saved
as-of timestamp from Kraken FULL official 4h/1d source snapshots. Verify that
the unchanged original raw score/eligibility matches the stored research
decision BEFORE releasing diagnostic gate measurements. These are observed
features, not retrospective new BUY signals or one-gate causal experiments.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
from io import BytesIO
import json
import math
from pathlib import Path
from zipfile import ZipFile

import pandas as pd

from crypto_rule_engine import ScreenerConfig, score_setup, ema
from funded_forward_capture import funded_universe

COLS=["timestamp","open","high","low","close","volume","trades"]
DUR={"4h":pd.Timedelta(hours=4),"1d":pd.Timedelta(days=1),
     "1w":pd.Timedelta(days=7)}
WEEK_ANCHOR=pd.Timestamp("1970-01-01T00:00:00Z")
FIELDS=("eligible","shape_eligible","trade_target_eligible","reason",
        "score","resistance_tests","rsi","volume_ratio",
        "current_risk_reward","risk_reward","distance_pct","entry_timing",
        "entry_timing_actionable","breakout_age_bars","coin_trend",
        "market_trend","rs_vs_btc_pct","target_upside_pct","downside_to_invalidation_pct")


def source(z, manifest, coin, minutes):
    item=manifest["selected_sources"].get(f"{coin}:{minutes}")
    name=f"FULL_{coin}_{minutes}.csv"
    if not item or item["file"]!=name:
        raise ValueError(f"missing frozen official source {coin} {minutes}")
    raw=z.read(name)
    if sha256(raw).hexdigest()!=item["sha256"]:
        raise ValueError(f"invalid source digest {coin} {minutes}")
    df=pd.read_csv(BytesIO(raw),header=None,names=COLS)
    if df.empty:raise ValueError("empty official market series")
    stamp=pd.to_numeric(df["timestamp"],errors="raise")
    if not stamp.between(1e9,2e10).all():raise ValueError("bad historic Unix seconds")
    df["timestamp"]=pd.to_datetime(stamp,unit="s",utc=True)
    df=df[COLS[:-1]].sort_values("timestamp").reset_index(drop=True)
    if df["timestamp"].duplicated().any():raise ValueError("duplicate 4h or daily source")
    for c in ("open","high","low","close","volume"):
        df[c]=pd.to_numeric(df[c],errors="raise")
        if df[c].isna().any() or not df[c].map(math.isfinite).all():raise ValueError("nonfinite OHLCV")
    return df


def complete_native_week_from_daily(df):
    df=df.copy()
    dates=df["timestamp"]
    starts=WEEK_ANCHOR+pd.to_timedelta(((dates-WEEK_ANCHOR).dt.days//7)*7,unit="D")
    df["week"]=starts
    out=[]
    for w,g in df.groupby("week",sort=True):
        if len(g)!=7 or g["timestamp"].iloc[0]!=w:continue
        if not g["timestamp"].diff().iloc[1:].eq(pd.Timedelta(days=1)).all():continue
        out.append({"timestamp":w,"open":g["open"].iloc[0],
                    "high":g["high"].max(),"low":g["low"].min(),
                    "close":g["close"].iloc[-1],"volume":g["volume"].sum()})
    if not out:raise ValueError("no complete Thursday-anchored Kraken weeks")
    return pd.DataFrame(out)


def asof(series, clock, step, cap, minimum):
    rows=series.loc[series["timestamp"]+DUR[step]+pd.Timedelta(minutes=1)<=clock].tail(cap)
    if len(rows)<minimum:raise ValueError(f"insufficient {step} history")
    last=rows["timestamp"].iloc[-1]+DUR[step]
    if clock-last>DUR[step]+pd.Timedelta(minutes=1):raise ValueError(f"stale {step} candles")
    if not rows["timestamp"].tail(minimum).diff().iloc[1:].eq(DUR[step]).all():
        raise ValueError(f"gapped {step} source")
    return rows.reset_index(drop=True)


def trace_one(row, data, btc, scorer=score_setup):
    stamp=pd.Timestamp(row["Signal open UTC"])
    if stamp.tzinfo is None:raise ValueError("historic original clock naive")
    stamp=stamp.tz_convert("UTC")
    clock=stamp+pd.Timedelta(hours=4,minutes=1)
    x=asof(data["4h"],clock,"4h",180,70)
    d=asof(data["1d"],clock,"1d",365,200)
    w=asof(data["1w"],clock,"1w",220,8)
    b4=asof(btc["4h"],clock,"4h",180,70)
    bd=asof(btc["1d"],clock,"1d",365,200)
    if x["timestamp"].iloc[-1]!=stamp or b4["timestamp"].iloc[-1]!=stamp:
        raise ValueError("BTC and coin last complete bar did not align")
    cfg=ScreenerConfig(exchange_id="kraken",quote="USD",
                       universe_size=33,min_quote_volume=0,score_threshold=80)
    original=scorer(x,d,b4,cfg,dfw=w,btcd=bd)
    # Release diagnostics only if original raw technical output is consistent.
    expected_score=float(row["Original technical score"])
    if not math.isfinite(expected_score) or abs(float(original["score"])-expected_score)>.11:
        raise ValueError("original scored status/technical score divergence")
    if bool(original["eligible"])!=bool(row["Original eligible"]):
        raise ValueError("original eligibility divergence")
    if bool(original["entry_timing_actionable"])!=bool(row["Original timing actionable"]):
        raise ValueError("original timing divergence")
    if str(original["entry_timing"])!=str(row["Original entry timing"]):
        raise ValueError("original entry stage divergence")
    # Raw feature diagnostics as-of original time; none modifies original state.
    hist_macd = ema(x["close"],12)-ema(x["close"],26)
    hist_macd = hist_macd-ema(hist_macd,9)
    macd_change=float(hist_macd.iloc[-1]-hist_macd.iloc[-4])
    result={key:original.get(key) for key in FIELDS}
    result.update({
        "Coin":row["Coin"],"Historical 4h open UTC":stamp.isoformat(),
        "Original historical research class":row["Research class"],
        "Original research fold":row["Research fold"],
        "Original watch reason":row["Original reason"],
        "4h MACD histogram slope 4 bars":round(macd_change,8),
        "4h MACD histogram improving":bool(macd_change>0),
        "4h MACD histogram above zero":bool(hist_macd.iloc[-1]>0),
        "RSI >80":float(original["rsi"])>80,
        "Number of resistance tests >=2":int(original["resistance_tests"])>=2,
        "Shape original true":bool(original["shape_eligible"]),
        "Timing original actionable":bool(original["entry_timing_actionable"]),
        "Target original known":bool(original["trade_target_eligible"]),
        "Isolated single-gate effect tested":False,
        "Broker-funded readiness verified":False,
    })
    return result


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--source-zip",required=True)
    p.add_argument("--original-zip",required=True)
    p.add_argument("--max-per-coin",type=int,default=160)
    p.add_argument("--out-dir",default="funded-crypto-numeric-gates")
    args=p.parse_args()
    if not 1<=args.max_per_coin<=300:raise ValueError("unbounded gate evidence research")
    with ZipFile(args.original_zip) as z:
        saved=pd.read_csv(BytesIO(z.read("multi_year_original_asof_decisions.csv")))
        original_outcomes=pd.read_csv(BytesIO(
            z.read("multi_year_mover_vs_nonmover_controls.csv")))
    allow=set(funded_universe()["crypto"])
    if set(saved["Coin"])-allow:raise ValueError("unknown original crypto universe")
    result=[];failed=[];coverage=[]
    with ZipFile(args.source_zip) as z:
        manifest=json.loads(z.read("FULL_selection_manifest.json"))
        if manifest.get("source_zip_full_sha256_verified") is not False:
            raise ValueError("source provenance limitations not explicit")
        series={}
        for coin in sorted(saved["Coin"].unique()):
            try:
                d=source(z,manifest,coin,"1440")
                series[coin]={"4h":source(z,manifest,coin,"240"),
                              "1d":d,"1w":complete_native_week_from_daily(d)}
            except Exception as exc:
                failed.append({"Coin":coin,"Reason":"SOURCE_ERROR "+str(exc)[:130]})
        if "BTC" not in series:raise RuntimeError("authentic BTC benchmark unavailable")
        for coin,group in saved.groupby("Coin",sort=True):
            if coin not in series:continue
            group=group.sort_values("Signal open UTC").head(args.max_per_coin)
            counts={"Coin":coin,"Selected":len(group),"Verified":0,"Failed":0}
            for _,original_row in group.iterrows():
                try:
                    out=trace_one(original_row,series[coin],series["BTC"])
                    result.append(out);counts["Verified"]+=1
                except Exception as exc:
                    failed.append({"Coin":coin,"Date":str(original_row["Signal open UTC"]),
                                   "Reason":f"{type(exc).__name__}: {str(exc)[:160]}"})
                    counts["Failed"]+=1
            coverage.append(counts)
    out=Path(args.out_dir);out.mkdir(parents=True,exist_ok=True)
    df=pd.DataFrame(result)
    df.to_csv(out/"exact_original_rule_numeric_gates.csv",index=False)
    pd.DataFrame(coverage).to_csv(out/"original_gate_trace_coverage.csv",index=False)
    pd.DataFrame(failed).reindex(columns=["Coin","Date","Reason"]).to_csv(
        out/"original_gate_trace_failclosed.csv",index=False)
    audit_groups=[]
    if not df.empty:
        labels=original_outcomes.loc[
            (original_outcomes["Horizon 4h bars"]==20)
            & (original_outcomes["Mover threshold %"]==10),
            ["Coin","Decision bar open","Later mover"]].copy()
        labels["when"]=pd.to_datetime(labels["Decision bar open"],utc=True)
        if labels.duplicated(["Coin","when"]).any():
            raise ValueError("duplicate future original-archive outcome labels")
        df["when"]=pd.to_datetime(df["Historical 4h open UTC"],utc=True)
        merged=df.merge(labels[["Coin","when","Later mover"]],on=["Coin","when"],
                        validate="one_to_one",how="left")
        if merged["Later mover"].isna().any():
            raise ValueError("original feature record has no matched future mover/nonmover label")
        features=("4h MACD histogram improving","4h MACD histogram above zero",
                  "Number of resistance tests >=2","Shape original true",
                  "Timing original actionable","RSI >80")
        for field in features:
            for (fold,state),g in merged.groupby(
                    ["Original research fold",field],sort=True):
                audit_groups.append({
                    "Measured original feature":field,"Feature value":bool(state),
                    "Research fold":fold,"Historical decisions":len(g),
                    "Later +10% CLOSE movers in 20x4h":int(g["Later mover"].sum()),
                    "Later non-movers":int((~g["Later mover"].astype(bool)).sum()),
                    "Not an isolated gate change":True,
                    "Not account-executable return":True,
                })
    pd.DataFrame(audit_groups).to_csv(
        out/"numeric_features_vs_matched_movers_and_nonmovers.csv",index=False)
    meta={"research_only":True,"numeric_trace_evidence":len(df),
          "original_decisions":len(saved),"errors":len(failed),
          "numeric_feature_control_groups":len(audit_groups),
          "original_rule_changed":False,"no_backfill_of_live_triggers":True,
          "single_gate_causal_analysis_complete":False}
    (out/"metadata.json").write_text(json.dumps(meta,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(meta,sort_keys=True))
    if len(df)<min(len(saved),50):
        raise RuntimeError("numeric original feature parity audit too incomplete")
    return 0


if __name__=="__main__":
    raise SystemExit(main())
