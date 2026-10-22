"""Prospective Kraken Funded 4h capture-slot integrity from ORIGINAL frozen SHA archives.

No new observations, historical backfill, rescore, scheduling or broker orders.
An unobserved 4h interval is UNKNOWN and never a PASS/WATCH/NO SIGNAL.
UTC capture bins are an observational estimate: delayed GitHub cron may shift
an intended capture into a later bucket, so these aren't confirmed failed jobs.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import json

from funded_forward_health import BAD_DATA, validate_snapshot

UTC=timezone.utc
STEP=timedelta(hours=4)
CRYPTO_STATES={"BUY","WATCH","PASS"}


def parse_utc(value):
    try:
        raw=datetime.fromisoformat(str(value).replace("Z","+00:00"))
    except (TypeError,ValueError) as exc:
        raise ValueError("Invalid UTC event timestamp") from exc
    if raw.tzinfo is None or raw.utcoffset()!=timedelta(0):
        raise ValueError("Historical research timestamp not UTC-aware")
    return raw.astimezone(UTC)


def slot_floor(value):
    moment=parse_utc(value)
    return moment.replace(hour=moment.hour-(moment.hour%4),
                          minute=0,second=0,microsecond=0)


def source_candle_clock(row, observed):
    """At a 00:35 scan, last completed 4h candle opened 20:00 previous day.

    Report first unexpected stale/future source, preserving missing/zero-trade
    upstream candles rather than manufacturing an observation.
    """
    observed=parse_utc(observed)
    if str(row.get("status") or "").upper() in BAD_DATA:
        return "SOURCE_UNAVAILABLE"
    raw=row.get("bar_timestamp")
    if not raw:
        return "BAR_TIMESTAMP_UNAVAILABLE"
    try:
        candle=parse_utc(raw)
    except ValueError:
        return "BAR_TIMESTAMP_MALFORMED"
    if (candle.minute,candle.second,candle.microsecond)!=(0,0,0) or candle.hour%4:
        return "NON_4H_CANDLE_ALIGNMENT"
    # One-minute conservative candle finality; hourly schedule minute ~35.
    latest_open=slot_floor(observed-timedelta(minutes=1))-STEP
    if candle>latest_open:
        return "FUTURE_OR_UNCOMPLETED_4H_CANDLE"
    if candle<latest_open:
        return "STALE_COMPLETED_4H_CANDLE"
    return "LATEST_COMPLETED_4H_CANDLE"


def audit_snapshots(snapshots, *, as_of, validate=validate_snapshot,
                    max_buckets=600):
    if not 1<=max_buckets<=1000:
        raise ValueError("research bin cap is invalid")
    now=parse_utc(as_of)
    original={}
    for snap in snapshots:
        validate(snap)
        key=str(snap.get("run_id") or "")
        digest=str(snap.get("payload_sha256") or "")
        if not key or not digest:
            raise ValueError("SHA validated archived original missing identity")
        if key in original:
            if original[key]!=digest:
                raise ValueError("A captured research run ID was revised")
            continue
        original[key]=digest
    seen=set()
    crypto=[]
    stocks=[]
    for snap in sorted(snapshots,
                       key=lambda x:(parse_utc(x["captured_at_utc"]),
                                     str(x["run_id"]))):
        key=str(snap["run_id"])
        if key in seen:
            continue
        seen.add(key)
        when=parse_utc(snap["captured_at_utc"])
        if when>now:
            raise ValueError("Observed capture is later than research audit clock")
        kind=snap["asset_type"]
        if kind=="crypto":
            crypto.append((when,snap))
        elif kind=="stocks":
            stocks.append((when,snap))
        else:
            raise ValueError("Unknown immutable original asset class")
    if not crypto:
        raise RuntimeError("NO FROZEN CRYPTO SCANS: CANNOT ASSERT SIGNAL COVERAGE")
    bins=defaultdict(list)
    for when,snap in crypto:
        bins[slot_floor(when)].append((when,snap))
    start=slot_floor(crypto[0][0])
    end=slot_floor(crypto[-1][0])
    expected=int((end-start)/STEP)+1
    if expected>max_buckets:
        raise RuntimeError("Too many research buckets; fail closed on window")
    states=Counter()
    source_health=Counter()
    snapshots_by_bucket=[]
    missing=[]
    watch_transitions=[]
    state_before={}
    research_trigger=[]
    distinct_signal_candles=set()
    source_bars=set()
    for k in range(expected):
        bucket=start+k*STEP
        snapshots_this=bins.get(bucket,[])
        if not snapshots_this:
            missing.append(bucket.isoformat())
            snapshots_by_bucket.append({
                "UTC 4h bin":bucket.isoformat(),
                "State":"UNOBSERVED_CAPTURE_BUCKET_NOT_NO_SIGNAL",
                "Captures":0,"Distinct technical trigger captures":0,
            })
            continue
        bucket_triggers=0
        for when,snap in snapshots_this:
            for row in snap["rows"]:
                name=str(row["asset"])
                state=str(row.get("status") or "").strip().upper()
                states[state]+=1
                bar_evidence=source_candle_clock(row,when)
                source_health[bar_evidence]+=1
                original_bar=str(row.get("bar_timestamp") or "")
                if original_bar and bar_evidence=="LATEST_COMPLETED_4H_CANDLE":
                    source_bars.add((name,original_bar))
                if state=="BUY":
                    bucket_triggers+=1
                    distinct_signal_candles.add((name,original_bar))
                    research_trigger.append({
                        "coin":name,"original_state":"BUY",
                        "capture_utc":when.isoformat(),
                        "completed_bar_open":original_bar,
                        "source_run_id":snap["run_id"],
                        "latest_source_bar_quality":bar_evidence,
                        "funded_broker_trade_confirmed":False,
                    })
                if state=="BUY" and state_before.get(name)=="WATCH":
                    watch_transitions.append({
                        "coin":name,"original_status_before":"WATCH",
                        "original_status_now":"BUY",
                        "captured_at_utc":when.isoformat(),
                        "broker_fill_verified":False,
                    })
                # A PASS or data error resets continuity; do not invent it
                # across unobserved buckets.
                state_before[name]=state
        snapshots_by_bucket.append({
            "UTC 4h bin":bucket.isoformat(),
            "State":"FROZEN_CAPTURE_PRESENT",
            "Captures":len(snapshots_this),
            "Distinct technical trigger captures":bucket_triggers,
        })
        if k+1<expected and start+(k+1)*STEP not in bins:
            state_before.clear()
    duplicates=sum(max(0,len(v)-1) for v in bins.values())
    return {
        "schema":"cl-signal-funded-prospective-4h-coverage-v1",
        "audited_at_utc":now.isoformat(),
        "first_observed_crypto_4h_bin_utc":start.isoformat(),
        "last_observed_crypto_4h_bin_utc":end.isoformat(),
        "source_crypto_scans":len(crypto),
        "source_stock_scans":len(stocks),
        "capture_4h_bins_in_observed_range":expected,
        "observed_crypto_4h_bins":len(bins),
        "unobserved_crypto_4h_bins":len(missing),
        "unobserved_crypto_4h_bin_starts_utc":missing,
        "multiple_crypto_captures_in_same_4h_bin":duplicates,
        "4h_capture_coverage_pct":round(100*len(bins)/expected,2),
        "latest_completed_coin_candle_quality":dict(sorted(source_health.items())),
        "original_technical_status_observations":dict(sorted(states.items())),
        "original_BUY_capture_observations":len(research_trigger),
        "original_BUY_unique_asset_plus_completed_bar":len(distinct_signal_candles),
        "watch_to_original_BUY_transitions_after_observed_continuity":watch_transitions,
        "genuine_archived_crypo_source_bars_by_asset_and_bar":len(source_bars),
        "research_trigger_evidence":research_trigger,
        "per_4h_bin":snapshots_by_bucket,
        "bucket_time_is_actual_capture_not_scheduled_intent":True,
        "missing_bin_means_NO_KNOWN_OUTCOME":True,
        "scan_states_not_independent_broker_trades":True,
        "funded_market_quotes_or_execution_verified":False,
        "live_rule_or_schedule_changed":False,
        "limitations":[
            "Only between first and last actual captured crypto bins: outside that range unquantified",
            "GitHub scheduled start delays can move the original intended slot into a later bin",
            "Multiple snapshots or coins in the same market episode do not imply independent trades",
            "Archived public Kraken OHLCV not Kraken Funded account market or executable fill",
            "No observations are interpolated or generated for unobserved periods",
        ],
    }


def main():
    import argparse
    from pathlib import Path
    import os
    from funded_forward_outcome_runner import load_local_snapshots, restore_github_archives
    p=argparse.ArgumentParser()
    p.add_argument("--from-github",action="store_true")
    p.add_argument("--window-days",type=int,default=14)
    p.add_argument("--max-artifacts",type=int,default=250)
    p.add_argument("--snapshot-dir",default="funded-forward-artifacts")
    p.add_argument("--out-dir",default="funded-slot-evidence")
    args=p.parse_args()
    if not 1<=args.window_days<=30 or not 1<=args.max_artifacts<=250:
        raise ValueError("research archive query exceeds capped scope")
    clock=datetime.now(UTC)
    if args.from_github:
        source=restore_github_archives(
            repository=os.getenv("GITHUB_REPOSITORY",""),
            token=os.getenv("GITHUB_TOKEN",""),
            min_created=clock-timedelta(days=args.window_days),
            max_artifacts=args.max_artifacts)
    else:
        source=load_local_snapshots(Path(args.snapshot_dir))
    result=audit_snapshots(source,as_of=clock.isoformat())
    out=Path(args.out_dir);out.mkdir(exist_ok=True,parents=True)
    (out/"slot_integrity_and_trigger_episodes.json").write_text(
        json.dumps(result,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    # Show concise counts; full evidence retains all source run hashes indirectly
    print(json.dumps({key:result[key] for key in (
        "source_crypto_scans","source_stock_scans","capture_4h_bins_in_observed_range",
        "observed_crypto_4h_bins","unobserved_crypto_4h_bins",
        "multiple_crypto_captures_in_same_4h_bin","4h_capture_coverage_pct",
        "latest_completed_coin_candle_quality","original_BUY_capture_observations",
        "original_BUY_unique_asset_plus_completed_bar")},sort_keys=True))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
