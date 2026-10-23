"""Research-only Kraken Funded WATCH-vs-BUY stop-aware counterfactual.

Inputs must be frozen original-rule as-of decisions (draft research PR #48),
plus unchanged SHA-checked original Kraken 4h CSV source snapshots. No
retrospective re-scoring, order execution, rule edits or broker-fill claims.

WATCH_READY_BYPASS hypothetically removes ALL remaining original WATCH
objections as a group, NOT a one-gate causal test. EARLY, PASS, incomplete
history and missing candles are NOT promoted to tradeable decisions.
"""
from __future__ import annotations

from hashlib import sha256
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile
import argparse
import json
import math

import pandas as pd

SOURCE_FIELDS = ["timestamp", "open", "high", "low", "close", "volume", "trades"]
APPROVED_STAGES = {"READY", "RETEST", "FRESH BREAKOUT"}
ALLOWED_CLASSES = {"WATCH", "REJECTED_PASS", "PROXY_BUY_UNVERIFIED"}
COSTS = (0.4, 0.8, 1.6)
HOLD_BARS = 20


def classify(row):
    state = str(row["Research class"])
    if state not in ALLOWED_CLASSES:
        raise ValueError("unknown original research state")
    if state == "PROXY_BUY_UNVERIFIED":
        return "ORIGINAL_BUY"
    if state != "WATCH":
        return "PASS_REJECTED_NO_PLAN"
    if not bool(row["Original eligible"]) or not bool(row["Original timing actionable"]):
        return "WATCH_NOT_ELIGIBLE"
    stage = str(row["Original entry timing"])
    if stage in APPROVED_STAGES:
        return "WATCH_READY_BYPASS"
    return "WATCH_EARLY_OR_NONENTRY"


def load_history(zipped, coin):
    manifest = json.loads(zipped.read("FULL_selection_manifest.json"))
    if manifest.get("source_zip_full_sha256_verified") is not False or not manifest.get("zip_crc_integrity_verified"):
        raise ValueError("source archive verification limitations not explicit")
    name = f"FULL_{coin}_240.csv"
    item = manifest["selected_sources"].get(f"{coin}:240")
    if not item or item["file"] != name:
        raise ValueError("missing authentic original 4h source manifest member")
    data = zipped.read(name)
    if sha256(data).hexdigest() != item["sha256"]:
        raise ValueError("historic 4h member content SHA mismatch")
    frame = pd.read_csv(BytesIO(data), header=None, names=SOURCE_FIELDS)
    if frame.empty:
        raise ValueError("historical source empty")
    value = pd.to_numeric(frame["timestamp"], errors="raise")
    if not value.between(1e9, 2e10).all():
        raise ValueError("historic Kraken timestamp not seconds")
    frame["timestamp"] = pd.to_datetime(value, unit="s", utc=True)
    if frame["timestamp"].duplicated().any():
        raise ValueError("duplicate historical 4h candle")
    if not frame["timestamp"].is_monotonic_increasing:
        frame = frame.sort_values("timestamp")
    for name in ["open", "high", "low", "close", "volume"]:
        frame[name] = pd.to_numeric(frame[name], errors="raise")
        if not frame[name].map(math.isfinite).all():
            raise ValueError("invalid numeric source candle")
    if (frame[["open","high","low","close"]] <= 0).any().any() or (frame["volume"] < 0).any():
        raise ValueError("impossible historical Kraken OHLCV")
    if (frame["high"] < frame[["open","close","low"]].max(axis=1)).any() or (frame["low"] > frame[["open","close","high"]].min(axis=1)).any():
        raise ValueError("inconsistent historical high/low")
    return frame.set_index("timestamp", drop=False)


def hypothetical_exit(row, bars, *, cost_pct=0.8, hold_bars=HOLD_BARS,
                      max_entry_deviation_pct=None):
    """Evaluate a *conditional* long at next 4h open; stop-first intrabar.

    An original BUY was a technical proxy, not account/broker eligibility.
    Cost is split across next-open buy and exit sell. No future bar is ever
    used to change the original frozen cohort selection.
    """
    if not 0 <= cost_pct <= 5 or not 1 <= hold_bars <= 90:
        raise ValueError("invalid risk model parameters")
    if max_entry_deviation_pct is not None and not 0 < max_entry_deviation_pct <= 10:
        raise ValueError("invalid ex-ante planned-entry distance")
    cohort = classify(row)
    base = {"Coin": str(row["Coin"]), "Research fold": str(row["Research fold"]),
            "Original reason": str(row["Original reason"]),
            "Original technical state": str(row["Original technical state"]),
            "Counterfactual cohort": cohort, "Cost roundtrip %": cost_pct,
            "Historic Kraken Funded fill verified": False}
    if cohort not in {"ORIGINAL_BUY", "WATCH_READY_BYPASS"}:
        return {**base, "Decision": "NO_HYPOTHETICAL_ENTRY"}
    if not bool(row["Proxy liquidity >= $1m"]):
        return {**base, "Decision": "LIQUIDITY_PROXY_BELOW_1M"}
    asof = pd.Timestamp(row["Signal open UTC"])
    if asof.tzinfo is None:
        raise ValueError("naive historical original decision")
    asof = asof.tz_convert("UTC")
    decision = pd.Timestamp(row["Decision UTC"])
    if decision.tzinfo is None or decision.tz_convert("UTC") < asof + pd.Timedelta(hours=4):
        raise ValueError("original decision predates completed signal candle")
    if asof not in bars.index:
        return {**base, "Decision": "SOURCE_SIGNAL_CANDLE_MISSING"}
    future = bars.loc[bars.index > asof].head(hold_bars)
    if len(future) < hold_bars:
        return {**base, "Decision": "CENSORED_FUTURE_HORIZON"}
    expected = pd.date_range(asof + pd.Timedelta(hours=4), periods=hold_bars,
                             freq="4h", tz="UTC")
    if not future.index.equals(expected):
        return {**base, "Decision": "GAPPED_FUTURE_HORIZON"}
    try:
        stop, target, planned = (float(row[k]) for k in
                                 ("Stop proxy", "Target proxy", "Entry proxy"))
        next_open = float(future["open"].iloc[0])
    except (ValueError, TypeError, OverflowError):
        return {**base, "Decision": "ORIGINAL_ENTRY_PLAN_MISSING"}
    if not all(math.isfinite(x) for x in (stop,target,planned,next_open)):
        return {**base, "Decision": "INVALID_ORIGINAL_PLAN"}
    if stop <= 0 or target <= planned or stop >= planned or planned <= 0:
        return {**base, "Decision": "INVALID_ORIGINAL_PLAN"}
    if max_entry_deviation_pct is not None:
        departure = abs(next_open/planned - 1)*100
        if departure > max_entry_deviation_pct:
            return {**base, "Decision": "NEXT_OPEN_DEVIATES_FROM_ORIGINAL_ENTRY"}
    entry = next_open*(1 + cost_pct/200)
    if stop >= entry or target <= entry:
        return {**base, "Decision": "INVALID_NEXT_OPEN_RISK"}
    close = None
    reason = "TIME EXIT"
    elapsed = hold_bars
    for elapsed, (_, candle) in enumerate(future.iterrows(), start=1):
        if candle["open"] <= stop:
            close = float(candle["open"])*(1 - cost_pct/200)
            reason = "STOP GAP"
        elif candle["low"] <= stop:
            close = stop*(1 - cost_pct/200)
            reason = ("STOP FIRST BOTH TOUCHED" if candle["high"] >= target
                      else "STOP")
        elif candle["high"] >= target:
            close = target*(1 - cost_pct/200)
            reason = "TARGET"
        if close is not None:
            break
    if close is None:
        close = float(future["close"].iloc[-1])*(1 - cost_pct/200)
    net_r = (close-entry)/(entry-stop)
    return {**base, "Decision": "MODELED_EXIT", "Signal bar": asof.isoformat(),
            "Original entry proxy": planned, "Next 4h open proxy": next_open,
            "Original stop": stop, "Original target": target,
            "Exit reason": reason, "Bars to exit": elapsed,
            "Net R proxy": round(float(net_r), 6)}


def run_audit(decisions, history, *, costs=COSTS, max_deviation=None):
    required = {"Coin", "Signal open UTC", "Decision UTC", "Research fold",
                "Original reason", "Original technical state", "Research class",
                "Original eligible", "Original timing actionable",
                "Original entry timing", "Proxy liquidity >= $1m",
                "Entry proxy", "Stop proxy", "Target proxy"}
    if not required.issubset(decisions):
        raise ValueError("frozen original historical decisions fields missing")
    if decisions.duplicated(["Coin","Signal open UTC"]).any():
        raise ValueError("duplicate original decision anchor")
    results = []
    for _, row in decisions.iterrows():
        bars = history.get(row["Coin"])
        if bars is None:
            raise ValueError(f"missing 4h source for {row['Coin']}")
        for cost in costs:
            results.append(hypothetical_exit(
                row, bars, cost_pct=cost,
                max_entry_deviation_pct=max_deviation))
    out = pd.DataFrame(results)
    closed = out[out["Decision"] == "MODELED_EXIT"].copy()
    summaries = []
    if not closed.empty:
        for (cohort, fold, cost), group in closed.groupby(
                ["Counterfactual cohort","Research fold","Cost roundtrip %"]):
            count = len(group)
            summaries.append({
                "Cohort":cohort, "Fold":fold,"Cost roundtrip %":cost,
                "Modeled entries": count,
                "Targets":int((group["Exit reason"]=="TARGET").sum()),
                "Stop events":int(group["Exit reason"].str.startswith("STOP").sum()),
                "Mean net R":round(float(group["Net R proxy"].mean()),4),
                "Median net R":round(float(group["Net R proxy"].median()),4),
                "R total":round(float(group["Net R proxy"].sum()),4),
                "Above zero count":int((group["Net R proxy"]>0).sum()),
                "Warning":"Descriptive, no verified fills, correlated assets, no official Kraken Funded risk equity simulation"
            })
    return out,pd.DataFrame(summaries)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--original-results-zip",required=True)
    parser.add_argument("--full-source-zip",required=True)
    parser.add_argument("--out-dir",default="funded-counterfactual-study")
    args=parser.parse_args()
    out=Path(args.out_dir);out.mkdir(parents=True,exist_ok=True)
    with ZipFile(args.original_results_zip) as z:
        original=pd.read_csv(BytesIO(z.read("multi_year_original_asof_decisions.csv")))
    with ZipFile(args.full_source_zip) as z:
        history={coin:load_history(z,coin) for coin in sorted(original["Coin"].unique())}
    base,summary=run_audit(original,history)
    conservative,strict_summary=run_audit(
        original,history,max_deviation=.5)
    base.to_csv(out/"all_original_buy_and_watch_counterfactuals.csv",index=False)
    summary.to_csv(out/"candidate_cohorts_by_fold_and_cost.csv",index=False)
    conservative.to_csv(out/"strict_0p5pct_entry_deviation_counterfactuals.csv",index=False)
    strict_summary.to_csv(out/"strict_0p5pct_cohorts_by_fold_and_cost.csv",index=False)
    assumptions={
        "type":"PURE_HISTORICAL_RESEARCH_NOT_A_LIVE_RULE",
        "historical_rulebook_changed":False,
        "broker_account_or_orders":False,
        "actual_execution_verified":False,
        "frozen_candidate_population":"Original multi-year Kraken 33-coin technical decisions from PR #48",
        "candidate_watch_subset":"Only original WATCH with ORIGINAL eligible and timing actionable, stages READY/RETEST/FRESH BREAKOUT; entire WATCH veto hypothetically bypassed, not single-rule test",
        "original_buy":"Original technical BUY classifications, not funded broker approval",
        "entry":"Next completed 4h bar open; optional ex-ante original plan price distance <=0.5%",
        "exit":"Original frozen stop/target; gap stop first, same-bar both hit STOP FIRST, time exit 20 bars",
        "friction":"0.4/0.8/1.6% roundtrip (half applied entry/exit)",
        "excluded":"Original PASS, WATCH early, no valid plan, <USD 1m rolling 4h liquidity proxy, invalid/gapped forward candles",
        "historical_crypto_universe_warning":"Present-day 33-coin survivor cohort, not past Funded broker instrument universe",
        "sample_warning":"Cross-coin and regime correlation; no statistical causal effect, no official challenge rules",
        "long_only":True,
    }
    (out/"assumptions.json").write_text(json.dumps(assumptions,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({
        "original_decisions":len(original),
        "cohort_selection":base.loc[base["Cost roundtrip %"]==0.8,"Counterfactual cohort"].value_counts().to_dict(),
        "at_0p8pct":base.loc[base["Cost roundtrip %"]==0.8,"Decision"].value_counts().to_dict(),
        "strict_at_0p8pct":conservative.loc[conservative["Cost roundtrip %"]==0.8,"Decision"].value_counts().to_dict(),
        "out_dir":str(out),"live_rules_changed":False,
    },sort_keys=True))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
