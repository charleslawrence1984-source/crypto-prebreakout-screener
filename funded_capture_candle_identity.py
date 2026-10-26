"""Research-only immutable original Kraken 4h candle/capture consistency.

Never manufactures a BUY for a skipped schedule; never authorizes a funded
account order. Uses actual source SHA-validated main-branch frozen snapshots.
Detect repeated, contradictory, stale, incomplete and non-aligned signal bars.
For 4h observations, GitHub cron time is NOT an exact expected event identity.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

from funded_forward_health import validate_snapshot, BAD_DATA

UTC=timezone.utc
FOUR_HOURS=timedelta(hours=4)
ONE_MINUTE=timedelta(minutes=1)
OTHER_REJECTIONS={"PASS","WATCH","DATA UNAVAILABLE","SCAN ERROR"}


def timestamp(s):
    try:
        dt=datetime.fromisoformat(str(s).replace("Z","+00:00"))
    except (ValueError,TypeError) as exc:
        raise ValueError("malformed original UTC time") from exc
    if dt.tzinfo is None or dt.utcoffset()!=timedelta(0):
        raise ValueError("original source clock is not UTC-aware")
    return dt.astimezone(UTC)


def bucket(value):
    dt=timestamp(value)
    return dt.replace(hour=dt.hour//4*4,minute=0,second=0,microsecond=0)


def last_complete_four_hour_open(captured_at):
    # A 4h candle ending now is not final until 1 minute later.
    return bucket(timestamp(captured_at)-ONE_MINUTE)-FOUR_HOURS


def classify_4h_row(row, captured):
    state=str(row.get("status") or "").strip().upper()
    if not state:raise ValueError("Original engine state missing")
    if state in BAD_DATA:return "PROVIDER_UNAVAILABLE_OR_ERROR"
    raw=str(row.get("bar_timestamp") or "")
    if not raw:return "MISSING_SOURCE_CANDLE"
    try:bar=timestamp(raw)
    except ValueError:return "UNVERIFIED_SOURCE_CANDLE_TIME"
    if bar!=bucket(bar):return "MISALIGNED_SOURCE_CANDLE"
    latest=last_complete_four_hour_open(captured)
    if bar>latest:return "FUTURE_OR_UNFINISHED_SOURCE_CANDLE"
    if bar<latest:return "STILL_OLDER_THAN_COMPLETED_4H_CANDLE"
    return "COMPLETED_SOURCE_CANDLE_AT_CAPTURE"


def audit(snapshots, *, as_of, validator=validate_snapshot,
          max_window_buckets=600):
    now=timestamp(as_of)
    accepted_by_run={}
    for source in snapshots:
        validator(source)
        key=str(source.get("run_id") or "")
        if not key:raise ValueError("source run missing identity")
        digest=source["payload_sha256"]
        if key in accepted_by_run and accepted_by_run[key]["payload_sha256"]!=digest:
            raise ValueError("source-run identity collision with different SHA")
        accepted_by_run[key]=source
    by_kind={"crypto":[],"stocks":[]}
    for source in accepted_by_run.values():
        clock=timestamp(source["captured_at_utc"])
        if clock>now:raise ValueError("future source capture cannot be included")
        by_kind[source["asset_type"]].append((clock,source))
    captured=sorted(by_kind["crypto"],key=lambda x:(x[0],x[1]["run_id"]))
    if not captured:raise RuntimeError("no real original crypto scans; research coverage UNKNOWN")
    stock_count=len(by_kind["stocks"])
    start=bucket(captured[0][0]);end=bucket(captured[-1][0])
    expected=int((end-start)/FOUR_HOURS)+1
    if expected>max_window_buckets or expected<1:
        raise ValueError("observed research time range beyond bounded window")
    slots=defaultdict(list)
    for clock,s in captured:slots[bucket(clock)].append((clock,s))
    gap=[(start+FOUR_HOURS*i).isoformat() for i in range(expected)
         if start+FOUR_HOURS*i not in slots]
    source_quality=Counter();original_states=Counter()
    episode_observations=defaultdict(list)
    all_rows=0
    for clock,snap in captured:
        for row in snap["rows"]:
            all_rows+=1
            state=str(row["status"]).strip().upper()
            original_states[state]+=1
            kind=classify_4h_row(row,clock)
            source_quality[kind]+=1
            # Don't use provider-unavailable or misaligned rows to construct
            # positive research triggers, even if a bad source says BUY.
            if state!="BUY" and state not in OTHER_REJECTIONS:
                # States remain whatever original engine actually recorded.
                pass
            symbol=str(row["asset"])
            if kind=="COMPLETED_SOURCE_CANDLE_AT_CAPTURE":
                bar=timestamp(row["bar_timestamp"]).isoformat()
                pair=str((row.get("meta") or {}).get("pair") or "")
                episode_observations[(symbol,bar)].append({
                    "state":state,"run_id":snap["run_id"],
                    "captured_at_utc":clock.isoformat(),
                    "spot_pair":pair,
                    "source_state":kind,
                })
            elif state=="BUY":
                # Never count this as one of the qualified original signals.
                episode_observations[(symbol,"SOURCE_INVALID_"+snap["run_id"])].append({
                    "state":state,"run_id":snap["run_id"],
                    "captured_at_utc":clock.isoformat(),"spot_pair":"",
                    "source_state":kind,
                })
    episodes=[]
    for (symbol,bar),observations in sorted(episode_observations.items()):
        has_buy=any(r["state"]=="BUY" for r in observations)
        if not has_buy:continue
        state_set={r["state"] for r in observations}
        pair_set={r["spot_pair"] for r in observations}
        fully_consistent=(
            not bar.startswith("SOURCE_INVALID_") and
            state_set=={"BUY"} and len(pair_set)==1 and "" not in pair_set
        )
        episodes.append({
            "asset":symbol,"bar_open_utc_or_quarantine":bar,
            "original_buy_observations":sum(r["state"]=="BUY" for r in observations),
            "all_same_candle_observations":len(observations),
            "original_engine_states":sorted(state_set),
            "source_pairs":sorted(pair_set),
            "eligibility":"CONSISTENT_ORIGINAL_TECHNICAL_BUY_RESEARCH_ONLY"
                         if fully_consistent else "QUARANTINE_SOURCE_OR_STATE_CONFLICT",
            "funded_market_or_actual_trade_verified":False,
            "observations":observations,
        })
    invalid=sum(e["eligibility"].startswith("QUARANTINE") for e in episodes)
    buy_observations=original_states["BUY"]
    consistent=sum(e["eligibility"].startswith("CONSISTENT") for e in episodes)
    # Missing individual slot, NOT broker NO SIGNAL; no retroactive backfill.
    return {
        "schema":"cl-signal-funded-original-candle-idempotence-v1",
        "audited_at_utc":now.isoformat(),
        "actual_crypto_snapshots":len(captured),
        "actual_stock_snapshots":stock_count,
        "crypto_source_rows":all_rows,
        "first_actual_crypto_4h_bin_utc":start.isoformat(),
        "last_actual_crypto_4h_bin_utc":end.isoformat(),
        "4h_bins_between_first_and_last_actual_capture":expected,
        "4h_bins_observed":len(slots),
        "4h_bins_unobserved_UNKNOWN":len(gap),
        "4h_bins_unobserved_utc":gap,
        "actual_capture_4h_bin_coverage_pct":round(len(slots)*100/expected,2),
        "multiple_crypto_captures_in_same_bin":sum(len(v)-1 for v in slots.values()),
        "source_candle_quality":dict(source_quality),
        "original_engine_states":dict(original_states),
        "original_technical_BUY_observations":buy_observations,
        "distinct_source_asset_and_bar_BUY_or_quarantine_episodes":len(episodes),
        "consistent_unique_original_technical_BUY_episodes":consistent,
        "quarantined_BUY_episodes":invalid,
        "episodes":episodes,
        "unknown_slots_are_not_no_signals":True,
        "actual_scheduled_trigger_time_exposed":False,
        "no_synthetic_signals_generated":True,
        "broker_funded_quote_or_account_verified":False,
        "live_strategy_or_schedule_changed":False,
    }


def main():
    import argparse
    from funded_forward_outcome_runner import load_local_snapshots, restore_github_archives
    import os
    parser=argparse.ArgumentParser()
    parser.add_argument("--from-github",action="store_true")
    parser.add_argument("--window-days",type=int,default=14)
    parser.add_argument("--out-dir",default="funded-original-candle-audit")
    parser.add_argument("--snapshots-dir",default="funded-forward-artifacts")
    args=parser.parse_args()
    if not 1<=args.window_days<=30:raise ValueError("research history window unbounded")
    clock=datetime.now(UTC)
    source=(restore_github_archives(
        repository=os.environ.get("GITHUB_REPOSITORY",""),
        token=os.environ.get("GITHUB_TOKEN",""),
        min_created=clock-timedelta(days=args.window_days),
        max_artifacts=250) if args.from_github
        else load_local_snapshots(Path(args.snapshots_dir)))
    result=audit(source,as_of=clock.isoformat())
    directory=Path(args.out_dir);directory.mkdir(parents=True,exist_ok=True)
    (directory/"latest_original_candle_and_signal_identity.json").write_text(
        json.dumps(result,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    print(json.dumps({key:value for key,value in result.items()
                      if key not in ("episodes","4h_bins_unobserved_utc")},
                     sort_keys=True))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
