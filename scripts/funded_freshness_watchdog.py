"""Monitor-only funded research watchdog; never dispatches a workflow.

A successful run is evidence of execution, not proof of artifact JSON validity.
Usage: python scripts/funded_freshness_watchdog.py --repo owner/repo
"""
import argparse
import datetime as dt
import json
import urllib.request

UTC = dt.timezone.utc
CRYPTO_MAX_AGE = dt.timedelta(hours=5, minutes=30)
STOCK_GRACE = dt.timedelta(hours=4)


def parse_time(s):
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


def expected_stock_slot(now):
    """Most recent weekday 22:45 UTC slot, including weekends."""
    day = now.date()
    for _ in range(8):
        if day.weekday() < 5:
            slot = dt.datetime.combine(day, dt.time(22, 45), UTC)
            if slot <= now:
                return slot
        day -= dt.timedelta(days=1)
    raise ValueError("No weekday slot found")


def assess(now, runs, artifacts_by_run):
    """Return diagnostic statuses; deliberately no dispatch/retry side effects.

    Job attribution is required: stock runs can start after midnight and
    cannot be classified from creation hour alone.
    """
    results = {}
    for asset in ("crypto", "stocks"):
        completed = sorted(
            (r for r in runs if r.get("asset") == asset
             and r.get("event") == "schedule"
             and r.get("conclusion") == "success"),
            key=lambda r: parse_time(r["created_at"]), reverse=True)
        if not completed:
            results[asset] = {"status": "missing", "reason": "no_successful_scheduled_run"}
            continue
        latest = completed[0]
        created = parse_time(latest["created_at"])
        artifacts = artifacts_by_run.get(latest["id"], [])
        prefix = "funded-crypto-" if asset == "crypto" else "funded-stocks-"
        present = any(a.get("name", "").startswith(prefix)
                      and not a.get("expired", False) and a.get("size_in_bytes", 0) > 0
                      for a in artifacts)
        if not present:
            status = "artifact_missing"
        elif asset == "crypto" and now - created > CRYPTO_MAX_AGE:
            status = "stale"
        elif asset == "stocks" and (now >= expected_stock_slot(now) + STOCK_GRACE
                                    and created < expected_stock_slot(now)):
            status = "stale"
        else:
            status = "observed_artifact_present_unverified"
        results[asset] = {
            "status": status, "run_id": latest["id"], "created_at": latest["created_at"],
            "age_minutes": round((now - created).total_seconds() / 60),
            "artifact_present": present,
        }
    return results


def github_json(repo, endpoint):
    req = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/{endpoint}",
        headers={"Accept": "application/vnd.github+json",
                 "User-Agent": "cl-signal-funded-monitor-only"})
    with urllib.request.urlopen(req, timeout=20) as response:
        return json.load(response)


def collect(repo):
    runs = []
    for page in range(1, 6):
        batch = github_json(repo, f"actions/workflows/funded-forward-research.yml/runs?per_page=100&page={page}")["workflow_runs"]
        if not batch:
            break
        for r in batch:
            if r.get("event") != "schedule":
                continue
            jobs = github_json(repo, f"actions/runs/{r['id']}/jobs?per_page=100")["jobs"]
            for job in jobs:
                if job.get("conclusion") == "success":
                    name = job.get("name", "")
                    asset = "crypto" if "crypto research" in name.lower() else ("stocks" if "stock research" in name.lower() else None)
                    if asset:
                        runs.append({**r, "asset": asset})
        # Only recent runs needed for freshness; avoid unnecessary API calls.
        if len(runs) >= 12:
            break
    artifacts = {}
    for r in runs[:12]:
        if r["id"] not in artifacts:
            artifacts[r["id"]] = github_json(repo, f"actions/runs/{r['id']}/artifacts?per_page=100")["artifacts"]
    return runs, artifacts


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--repo", required=True)
    p.add_argument("--now", help="UTC ISO timestamp for replay")
    args = p.parse_args()
    now = parse_time(args.now) if args.now else dt.datetime.now(UTC)
    runs, artifacts = collect(args.repo)
    report = {"as_of_utc": now.isoformat(), "mode": "MONITOR_ONLY",
              "note": "Artifact presence is not JSON content/coverage/integrity verification. No missed-slot backfill or trade actions.",
              "assets": assess(now, runs, artifacts)}
    print(json.dumps(report, indent=2))
    # Diagnostic only: no workflow dispatch, writes, or automatic recovery.


if __name__ == "__main__":
    main()
