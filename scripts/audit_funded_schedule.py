"""Read-only, conservative audit of Kraken Funded scheduled research captures.

GitHub run.created_at is NOT a trustworthy record of which cron expression
triggered a delayed event. Attribute by actual asset job and a bounded
preceding-slot window, but always label the slot mapping INFERRED.
Never repair or dispatch workflows, fabricate market observations, or trade.
"""
import argparse
import datetime as dt
import json
import os
import urllib.request

UTC = dt.timezone.utc
CRYPTO_HOURS = (0, 4, 8, 12, 16, 20)
CRYPTO_GRACE = dt.timedelta(minutes=90)
STOCK_GRACE = dt.timedelta(hours=4)
WINDOW = dt.timedelta(days=3)
WORKFLOW = "funded-forward-research.yml"


def parse_time(value):
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("UTC offset required")
    return parsed.astimezone(UTC)


def expected_slots(now, start):
    day = start.date()
    while day <= now.date():
        for hour in CRYPTO_HOURS:
            yield "crypto", dt.datetime.combine(day, dt.time(hour, 35), UTC)
        if day.weekday() < 5:
            yield "stocks", dt.datetime.combine(day, dt.time(22, 45), UTC)
        day += dt.timedelta(days=1)


def following_slot(asset, slot):
    if asset == "crypto":
        return slot + dt.timedelta(hours=4)
    day = slot.date() + dt.timedelta(days=1)
    while day.weekday() >= 5:
        day += dt.timedelta(days=1)
    return dt.datetime.combine(day, dt.time(22, 45), UTC)


def job_asset(job):
    name = job.get("name", "").lower()
    if "frozen crypto research observations" in name:
        return "crypto"
    if "frozen stock research observations" in name:
        return "stocks"
    return None


def classify(runs, now, start, jobs_by_run=None, history_complete=True):
    """Diagnose potential scheduled gaps, never assert exact cron attribution.

    jobs_by_run: {run_id: [GitHub job dictionaries]}; absent/failed fetch
    yields UNKNOWN, not an inferred successful capture.
    """
    jobs_by_run = jobs_by_run or {}
    scheduled = [
        r for r in runs
        if r.get("event") == "schedule"
        and r.get("path", "").endswith(WORKFLOW)
        and r.get("created_at")
    ]
    observations = []
    for asset, slot in expected_slots(now, start):
        grace = CRYPTO_GRACE if asset == "crypto" else STOCK_GRACE
        if slot < start or slot + grace > now:
            continue
        next_slot = following_slot(asset, slot)
        candidates = [r for r in scheduled if
                      slot <= parse_time(r["created_at"]) < next_slot]
        attributed = []
        unattributed_ids = []
        for run in candidates:
            jobs = jobs_by_run.get(run["id"])
            if jobs is None:
                unattributed_ids.append(run["id"])
                continue
            matching = [j for j in jobs if job_asset(j) == asset
                        and j.get("conclusion") != "skipped"]
            if matching:
                attributed.append((run, matching))
        if any(j.get("conclusion") == "success"
               for _, jobs in attributed for j in jobs):
            status = "observed_success_artifact_unverified_slot_inferred"
        elif any(j.get("status") != "completed"
                 for _, jobs in attributed for j in jobs):
            status = "asset_job_in_progress"
        elif attributed:
            status = "asset_job_failed"
        elif unattributed_ids:
            status = "job_metadata_unavailable"
        else:
            status = "no_matching_run_observed" if history_complete else "history_incomplete"
        observations.append({
            "asset": asset, "slot_utc": slot.isoformat(),
            "status": status,
            "attributed_run_ids": [r["id"] for r, _ in attributed],
            "unattributed_run_ids": unattributed_ids,
            "run_creation_delay_minutes": [
                round((parse_time(r["created_at"]) - slot).total_seconds() / 60, 1)
                for r, _ in attributed
            ],
            "slot_mapping": "inferred_not_proven",
        })
    return observations


def github_json(repo, endpoint):
    headers = {"Accept": "application/vnd.github+json",
               "User-Agent": "cl-signal-read-only-schedule-audit"}
    if os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = "Bearer " + os.environ["GITHUB_TOKEN"]
    req = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/{endpoint}", headers=headers)
    with urllib.request.urlopen(req, timeout=20) as response:
        return json.load(response)


def collect(repo, start, max_pages=10):
    """Get workflow-scoped run history, with an explicit pagination bound."""
    runs = []
    complete = False
    for page in range(1, max_pages + 1):
        batch = github_json(
            repo, f"actions/workflows/{WORKFLOW}/runs?per_page=100&page={page}"
        )["workflow_runs"]
        runs.extend(batch)
        if not batch or len(batch) < 100 or parse_time(batch[-1]["created_at"]) < start:
            complete = True
            break
    recent = [r for r in runs if r.get("event") == "schedule"
              and parse_time(r["created_at"]) >= start]
    jobs_by_run = {}
    errors = []
    for run in recent:
        try:
            jobs = []
            for page in range(1, 5):
                response = github_json(repo, f"actions/runs/{run['id']}/jobs?per_page=100&page={page}")
                jobs.extend(response["jobs"])
                if len(response["jobs"]) < 100:
                    break
            jobs_by_run[run["id"]] = jobs
        except Exception as exc:
            errors.append({"run_id": run["id"], "error": type(exc).__name__})
    return recent, jobs_by_run, complete, errors


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--repo", required=True, help="owner/repo")
    p.add_argument("--now", help="ISO offset-aware timestamp, for reproducibility")
    args = p.parse_args()
    now = parse_time(args.now) if args.now else dt.datetime.now(UTC)
    start = now - WINDOW
    runs, jobs_by_run, complete, errors = collect(args.repo, start)
    slots = classify(runs, now, start, jobs_by_run, complete)
    report = {
        "as_of_utc": now.isoformat(),
        "mode": "READ_ONLY",
        "history_complete": complete,
        "job_metadata_errors": errors,
        "scope": "workflow_runs_and_asset_jobs_only",
        "limitations": (
            "Slot attribution is inferred from created_at and asset job; GitHub "
            "does not identify the exact cron occurrence. Late runs might be "
            "assigned to a later slot. No JSON integrity, candle data, historical "
            "missing-capture reconstruction, execution or funded signal proof."
        ),
        "slots": slots,
        "crypto_slots_without_matching_run": sum(
            s["status"] == "no_matching_run_observed"
            for s in slots if s["asset"] == "crypto"),
    }
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
