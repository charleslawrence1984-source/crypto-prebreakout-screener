"""Read-only audit of funded research scheduled GitHub Actions observations.

Fetches GitHub run metadata; never dispatches workflows or places orders.
Usage: python scripts/audit_funded_schedule.py --repo owner/repo
"""
import argparse
import datetime as dt
import json
import urllib.request

UTC = dt.timezone.utc
CRYPTO_HOURS = (0, 4, 8, 12, 16, 20)
GRACE = dt.timedelta(minutes=90)
WINDOW = dt.timedelta(days=3)


def parse_time(value):
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def expected_slots(now, start):
    day = start.date()
    while day <= now.date():
        for hour in CRYPTO_HOURS:
            yield "crypto", dt.datetime.combine(day, dt.time(hour, 35), UTC)
        if day.weekday() < 5:
            yield "stocks", dt.datetime.combine(day, dt.time(22, 45), UTC)
        day += dt.timedelta(days=1)


def classify(runs, now, start, grace=GRACE):
    """Only evaluate slots older than grace. Never count an unverified run as healthy."""
    scheduled = [r for r in runs if r.get("event") == "schedule"
                 and r.get("path", "").endswith("funded-forward-research.yml")]
    observations = []
    for asset, slot in expected_slots(now, start):
        if slot < start or slot + grace > now:
            continue
        # A scheduled event is associated with the nearest *preceding* cron slot;
        # bound by the next slot for that asset to avoid double counting.
        next_slot = slot + (dt.timedelta(hours=4) if asset == "crypto" else dt.timedelta(days=1))
        matches = []
        for run in scheduled:
            created = parse_time(run["created_at"])
            if slot <= created < next_slot and (
                asset == "crypto" and slot.hour in CRYPTO_HOURS
                or asset == "stocks" and slot.hour == 22
            ):
                matches.append(run)
        # Important: stock runs delayed past midnight require run/job-level
        # attribution; do not falsely mark them healthy by timestamp alone.
        if asset == "stocks":
            observations.append({"asset": asset, "slot_utc": slot.isoformat(),
                                 "status": "needs_job_attribution", "candidate_run_ids": [r["id"] for r in matches]})
            continue
        if not matches:
            status = "missing"
        elif any(r.get("conclusion") == "success" for r in matches):
            status = "run_success_artifact_unverified"
        elif any(r.get("status") != "completed" for r in matches):
            status = "running"
        else:
            status = "failed"
        observations.append({"asset": asset, "slot_utc": slot.isoformat(),
                             "status": status, "candidate_run_ids": [r["id"] for r in matches]})
    return observations


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--repo", required=True)
    p.add_argument("--now", help="ISO UTC override for reproducible audit")
    args = p.parse_args()
    now = parse_time(args.now) if args.now else dt.datetime.now(UTC)
    start = now - WINDOW
    url = f"https://api.github.com/repos/{args.repo}/actions/runs?per_page=100"
    runs = []
    for page in range(1, 11):
        req = urllib.request.Request(url + f"&page={page}", headers={"Accept": "application/vnd.github+json", "User-Agent": "funded-research-audit"})
        with urllib.request.urlopen(req, timeout=20) as response:
            batch = json.load(response)["workflow_runs"]
        runs.extend(batch)
        if not batch or parse_time(batch[-1]["created_at"]) < start:
            break
    slots = classify(runs, now, start)
    report = {"as_of_utc": now.isoformat(), "scope": "read_only_schedule_metadata",
              "limitations": "Artifact contents, coverage and job-level asset attribution NOT verified; no inferred trades.",
              "slots": slots, "missing_crypto": sum(x["status"] == "missing" for x in slots if x["asset"] == "crypto")}
    print(json.dumps(report, indent=2))
    # Report only; do not fail CI or mutate any repository state.


if __name__ == "__main__":
    main()
