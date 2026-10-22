"""One-off GitHub Actions schedule provenance, Kraken Funded research only.

GitHub REST workflow run created_at is a recorded event creation timestamp,
NOT the hidden intended scheduled cron occurrence. We derive ONLY a
heuristic upper bound on lateness against the most recent prior matching
cron, never claim it identifies the exact scheduled event for delayed runs.

Never adds a schedule, mutates main, authorizes orders or changes scan states.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import argparse
import json
from pathlib import Path
import re

from funded_forward_slot_integrity import parse_utc, slot_floor

FLOW = "funded-forward-research.yml"
CRYPTO_HOURS=(0,4,8,12,16,20)
CRYPTO_MINUTE=35
MAX_PAGES=4


def last_possible_crypto_cron_before(created_at):
    clock=parse_utc(created_at)
    for ago in range(2):
        day=(clock-timedelta(days=ago)).date()
        for h in reversed(CRYPTO_HOURS):
            candidate=datetime(day.year,day.month,day.day,h,CRYPTO_MINUTE,tzinfo=timezone.utc)
            if candidate<=clock:
                return candidate
    raise ValueError("No crypto reference cron found")


def infer_crypto_creation_lag(run):
    """Helpful diagnostic, never definitive event-schedule identity."""
    created=parse_utc(run["created_at"])
    possible=last_possible_crypto_cron_before(created)
    delay=round((created-possible).total_seconds()/60,2)
    if not 0<=delay<=240:
        raise ValueError("Invalid nominal 4h cadence reference")
    return {
        "workflow_run":str(run["id"]),
        "recorded_run_created_utc":created.isoformat(),
        "latest_prior_scheduled_crypto_cron_utc":possible.isoformat(),
        "minimum_nominal_crypto_creation_delay_minutes":delay,
        "actual_original_cron_occurrence_proven":False,
        "warning":"Earlier original cron occurrence could have triggered this delayed run",
    }


def schedule_forensics(runs, archived_snapshots, *, as_of):
    clock=parse_utc(as_of)
    captured={}
    for snap in archived_snapshots:
        if snap.get("asset_type")!="crypto":
            continue
        observed=parse_utc(snap["captured_at_utc"])
        if observed>clock:
            raise ValueError("original immutable capture in the future")
        runid=str(snap.get("run_id") or "").split("-",1)[0]
        if not re.fullmatch(r"\d+",runid):
            raise ValueError("non-GitHub original crypto archived source id")
        source_bin=slot_floor(observed)
        if runid in captured and captured[runid]!=source_bin:
            raise ValueError("same original crypto run archive changed source clock")
        captured[runid]=source_bin
    results=[]
    for run in runs:
        if run.get("event")!="schedule":
            continue
        if str(run.get("crypto_job_conclusion"))=="skipped":
            continue
        if str(run.get("crypto_job_name") or "")!="Frozen crypto research observations (33 coins)":
            raise ValueError("Cannot identify source crypto job from GitHub REST job metadata")
        created=parse_utc(run["created_at"])
        if created>clock:
            raise ValueError("workflow run created in the future")
        info=infer_crypto_creation_lag(run)
        observed=captured.get(str(run["id"]))
        info.update({
            "job_conclusion":run.get("crypto_job_conclusion"),
            "matching_original_SHA_verified_capture_present":observed is not None,
            "original_capture_clock_bin_utc":observed.isoformat() if observed else None,
            "missing_original_capture_not_zero_signals":observed is None,
        })
        results.append(info)
    present={str(item["workflow_run"]) for item in results}
    unmatched=sorted(set(captured)-present)
    return {
        "schema":"cl-signal-funded-schedule-creation-provenance-v1",
        "as_of_utc":clock.isoformat(),
        "scheduled_crypto_job_records_examined":len(results),
        "source_verified_crypto_capture_runs_in_period":len(captured),
        "job_with_matching_archive_count":sum(
            x["matching_original_SHA_verified_capture_present"] for x in results),
        "job_without_matching_archive_count":sum(
            not x["matching_original_SHA_verified_capture_present"] for x in results),
        "original_crypto_captures_without_examined_schedule_run":unmatched,
        "heuristic_lag_over_60_minutes_count":sum(
            x["minimum_nominal_crypto_creation_delay_minutes"]>60 for x in results),
        "heuristic_lag_over_120_minutes_count":sum(
            x["minimum_nominal_crypto_creation_delay_minutes"]>120 for x in results),
        "runs":results,
        "confidence":"HEURISTIC: GITHUB REST DOES NOT EXPOSE ORIGINAL INTENDED CRON INSTANCE",
        "no_new_schedule_or_live_change":True,
        "not_kraken_broker_signal_trading":True,
    }


def discover_scheduled_runs(repo, token, *, earliest, clock):
    import requests
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+",repo):
        raise ValueError("unsafe repo identifier")
    if not token:
        raise ValueError("GITHUB_TOKEN required for read-only schedule run history")
    session=requests.Session()
    session.headers.update({
        "Authorization":"Bearer "+token,
        "Accept":"application/vnd.github+json",
        "X-GitHub-Api-Version":"2022-11-28",
    })
    url=f"https://api.github.com/repos/{repo}/actions/workflows/{FLOW}/runs"
    gathered=[];complete=False
    for page in range(1,MAX_PAGES+1):
        r=session.get(url,params={"per_page":100,"page":page,"event":"schedule"},timeout=25)
        r.raise_for_status()
        j=r.json()
        items=j.get("workflow_runs",[])
        if not isinstance(items,list):
            raise ValueError("GitHub returned unexpected workflow runs structure")
        for item in items:
            created=parse_utc(item["created_at"])
            if created<earliest:
                complete=True
                break
            if created>clock:
                raise ValueError("Future GitHub workflow creation event")
            details=session.get(
                f"https://api.github.com/repos/{repo}/actions/runs/{item['id']}/jobs",
                params={"per_page":100},timeout=25)
            details.raise_for_status()
            jobs=details.json().get("jobs",[])
            match=[q for q in jobs
                   if q.get("name")=="Frozen crypto research observations (33 coins)"]
            if len(match)!=1:
                raise ValueError("Original crypto-job name missing or ambiguous")
            state=match[0].get("conclusion")
            if state=="skipped":
                continue
            gathered.append({
                "id":item["id"],"created_at":item["created_at"],
                "event":item.get("event"),
                "crypto_job_name":match[0]["name"],
                "crypto_job_conclusion":state,
            })
        if complete or len(items)<100:
            complete=True
            break
    if not complete:
        raise RuntimeError("GitHub scheduled run pagination truncated; cannot prove complete review")
    return gathered


def main():
    from funded_forward_outcome_runner import restore_github_archives
    import os
    p=argparse.ArgumentParser()
    p.add_argument("--window-days",type=int,default=14)
    p.add_argument("--out-dir",default="funded-schedule-source-provenance")
    a=p.parse_args()
    if not 1<=a.window_days<=21:
        raise ValueError("out-of-bounds prospective schedule research")
    clock=datetime.now(timezone.utc)
    earliest=clock-timedelta(days=a.window_days)
    repo=os.getenv("GITHUB_REPOSITORY","")
    token=os.getenv("GITHUB_TOKEN","")
    sources=restore_github_archives(repository=repo,token=token,
                                    min_created=earliest,max_artifacts=240)
    events=discover_scheduled_runs(repo,token,earliest=earliest,clock=clock)
    report=schedule_forensics(events,sources,as_of=clock.isoformat())
    path=Path(a.out_dir);path.mkdir(exist_ok=True,parents=True)
    (path/"github_actual_schedule_event_vs_original_snapshot_provenance.json").write_text(
        json.dumps(report,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    print(json.dumps({k:v for k,v in report.items() if k not in ("runs",)},sort_keys=True))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
