"""Research-only GitHub schedule resilience shadow audit.

No orders, workflow dispatch, mutation, recovery or alerts. Run metadata does
not identify the exact original cron occurrence; report only inferred windows.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import urllib.error
import urllib.request

UTC = dt.timezone.utc
CRYPTO_HOURS = (0, 4, 8, 12, 16, 20)
CRYPTO_GRACE = dt.timedelta(minutes=90)
STOCK_GRACE = dt.timedelta(hours=4)
WORKFLOW_FILE = "funded-forward-research.yml"
CRYPTO_JOB = "Frozen crypto research observations"
STOCK_JOB = "Frozen stock research observations"


def when(s: str) -> dt.datetime:
    result = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("UTC offset required")
    return result.astimezone(UTC)


def intended_slots(start: dt.datetime, end: dt.datetime):
    day = start.date()
    while day <= end.date():
        for hour in CRYPTO_HOURS:
            slot = dt.datetime.combine(day, dt.time(hour, 35), UTC)
            if start <= slot <= end:
                yield ("crypto", slot)
        if day.weekday() < 5:
            slot = dt.datetime.combine(day, dt.time(22, 45), UTC)
            if start <= slot <= end:
                yield ("stocks", slot)
        day += dt.timedelta(days=1)


def next_slot(asset: str, slot: dt.datetime) -> dt.datetime:
    if asset == "crypto":
        return slot + dt.timedelta(hours=4)
    d = slot.date() + dt.timedelta(days=1)
    while d.weekday() >= 5:
        d += dt.timedelta(days=1)
    return dt.datetime.combine(d, dt.time(22, 45), UTC)


def asset_jobs(jobs, asset):
    prefix = CRYPTO_JOB if asset == "crypto" else STOCK_JOB
    return [j for j in jobs if str(j.get("name", "")).startswith(prefix)
            and j.get("conclusion") != "skipped"]


def artifact_present(artifacts, asset):
    prefix = "funded-crypto-" if asset == "crypto" else "funded-stocks-"
    return any(a.get("name", "").startswith(prefix)
               and int(a.get("size_in_bytes", 0)) > 0
               and not a.get("expired", False) for a in artifacts)


def audit(now, start, runs, jobs_by_id, artifacts_by_id, *, complete=True):
    """Classify only eligible mature windows. Never infer fills or missing bars."""
    scheduled = [r for r in runs if r.get("event") == "schedule"
                 and r.get("path", "").endswith(WORKFLOW_FILE)
                 and r.get("created_at")]
    result = []
    for asset, slot in intended_slots(start, now):
        grace = CRYPTO_GRACE if asset == "crypto" else STOCK_GRACE
        if now < slot + grace:
            continue
        candidates = [r for r in scheduled if slot <= when(r["created_at"]) < next_slot(asset, slot)]
        associated = []
        uncertain = []
        for r in candidates:
            rid = r["id"]
            if rid not in jobs_by_id:
                uncertain.append(rid)
                continue
            jobs = asset_jobs(jobs_by_id[rid], asset)
            if jobs:
                associated.append((r, jobs))
        successful = [(r, jobs) for r, jobs in associated
                      if any(j.get("status") == "completed" and
                             j.get("conclusion") == "success" for j in jobs)]
        good_artifact = any(artifact_present(artifacts_by_id.get(r["id"], []), asset)
                            for r, _ in successful)
        if good_artifact:
            status = "artifact_observed_content_unverified"
        elif successful:
            status = "successful_asset_job_artifact_unverified_or_missing"
        elif any(any(j.get("status") != "completed" for j in jobs) for _, jobs in associated):
            status = "asset_job_in_progress"
        elif associated:
            status = "asset_job_non_success"
        elif uncertain:
            status = "job_metadata_unavailable"
        elif complete:
            status = "no_matching_asset_job_observed"
        else:
            status = "history_incomplete"
        result.append({
            "asset": asset, "nominal_slot_utc": slot.isoformat(),
            "status": status, "attributed_run_ids": [r["id"] for r, _ in associated],
            "unknown_job_run_ids": uncertain,
            "observed_start_delay_minutes": [round((when(r["created_at"]) - slot).total_seconds()/60, 1)
                                             for r, _ in associated],
            "original_cron_occurrence": "not_exposed_by_github_api",
            "no_lookahead_backfill": True,
        })
    return result


def github_json(repo, path):
    headers = {"Accept": "application/vnd.github+json",
               "User-Agent": "cl-signal-kraken-read-only-shadow"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(f"https://api.github.com/repos/{repo}/{path}",
                                     headers=headers)
    with urllib.request.urlopen(request, timeout=25) as response:
        return json.load(response)


def collect(repo, start):
    runs = []
    complete = False
    for page in range(1, 11):
        batch = github_json(repo, f"actions/workflows/{WORKFLOW_FILE}/runs?per_page=100&page={page}")["workflow_runs"]
        runs.extend(batch)
        if len(batch) < 100 or (batch and when(batch[-1]["created_at"]) < start):
            complete = True
            break
        if not batch:
            complete = True
            break
    ids = {r["id"] for r in runs if r.get("event") == "schedule" and when(r["created_at"]) >= start}
    jobs = {}
    artifacts = {}
    failures = []
    for rid in sorted(ids):
        try:
            jobs[rid] = github_json(repo, f"actions/runs/{rid}/jobs?per_page=100")["jobs"]
            artifacts[rid] = github_json(repo, f"actions/runs/{rid}/artifacts?per_page=100")["artifacts"]
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, KeyError) as err:
            failures.append({"run_id": rid, "kind": type(err).__name__})
    return runs, jobs, artifacts, complete, failures


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--as-of", help="UTC timestamp; use actual current time by default")
    parser.add_argument("--days", type=int, default=3)
    args = parser.parse_args()
    if not 1 <= args.days <= 14:
        parser.error("--days must be between 1 and 14")
    now = when(args.as_of) if args.as_of else dt.datetime.now(UTC)
    start = now - dt.timedelta(days=args.days)
    runs, jobs, artifacts, complete, failures = collect(args.repo, start)
    slots = audit(now, start, runs, jobs, artifacts, complete=complete)
    report = {
        "as_of_utc": now.isoformat(), "mode": "READ_ONLY_NO_DISPATCH",
        "pages_complete_to_start": complete, "api_metadata_failures": failures,
        "scope": "scheduled workflow, successful asset jobs, nonempty artifact metadata",
        "limitations": (
            "GitHub workflow run created_at is not the original cron occurrence; "
            "a late event can cross the next nominal window. An unmatched window "
            "does not prove a dropped cron trigger. Artifact contents, provider "
            "freshness and funded broker executions are NOT verified. "
            "No silent missing-price reconstruction, trading or recovery."),
        "slots": slots,
    }
    print(json.dumps(report, indent=2))
    # A data access failure must not be reported as a healthy observation.
    return 0 if complete and not failures else 2


if __name__ == "__main__":
    raise SystemExit(main())
