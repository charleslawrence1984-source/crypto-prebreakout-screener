"""Gentle missing-only Investment backfill; checkpoint every hourly batch."""
import ast
import json
import subprocess
import sys
import time
from pathlib import Path

MANIFEST = Path("prepared_scans/manifest.json")


def snapshot():
    return json.loads(MANIFEST.read_text())["exchanges"]


def may_continue(before, after, returncode):
    if returncode not in (0, 1):
        return False
    gained = sum(v.get("coverage_symbols", 0) for v in after.values()) > sum(
        v.get("coverage_symbols", 0) for v in before.values())
    if not gained:
        return False
    for kind, meta in after.items():
        if meta == before.get(kind):
            continue
        diagnostics = meta.get("diagnostics", {})
        if meta.get("status") == "failed":
            prefix = "RuntimeError: scan returned no results: "
            error = meta.get("error", "")
            if not error.startswith(prefix):
                return False
            try:
                diagnostics = ast.literal_eval(error[len(prefix):])
            except (ValueError, SyntaxError):
                return False
        if diagnostics.get("rate_limit_errors", 0) or diagnostics.get("other_errors", 0):
            return False
    return any(v.get("remaining_symbols", 0) > 0 for v in after.values())


def is_soft_provider_pause(before, after, returncode):
    """Return True when a non-zero scan was caused only by provider/data gaps.

    These are expected backfill conditions (rate limiting, missing prices/market caps,
    or symbols that no longer meet the market-cap floor). Progress is already
    checkpointed, so they should pause the hourly worker without making GitHub
    Actions report a broken workflow. Unexpected exceptions still fail normally.
    """
    if returncode != 1:
        return False

    prefix = "RuntimeError: scan returned no results: "
    saw_soft_failure = False
    for kind, meta in after.items():
        if meta == before.get(kind) or meta.get("status") != "failed":
            continue

        error = meta.get("error", "")
        if not error.startswith(prefix):
            return False
        try:
            diagnostics = ast.literal_eval(error[len(prefix):])
        except (ValueError, SyntaxError):
            return False

        if int(diagnostics.get("other_errors", 0) or 0):
            return False

        requested = int(diagnostics.get("requested", 0) or 0)
        explained = sum(
            int(diagnostics.get(key, 0) or 0)
            for key in (
                "rate_limit_errors",
                "price_failures",
                "market_cap_failures",
                "below_min_market_cap",
            )
        )
        if requested <= 0 or explained < requested:
            return False
        saw_soft_failure = True

    return saw_soft_failure


def git(*args):
    return subprocess.run(["git", *args], check=True)


def checkpoint():
    git("add", "prepared_scans")
    result = subprocess.run(["git", "diff", "--cached", "--quiet"])
    if result.returncode == 0:
        return
    if result.returncode != 1:
        raise RuntimeError("Cannot inspect staged checkpoint")
    git("commit", "-m", "Checkpoint Investment fundamental batch")
    for attempt in range(3):
        git("pull", "--rebase", "origin", "main")
        if subprocess.run(["git", "push", "origin", "HEAD:main"]).returncode == 0:
            return
        time.sleep(5 * (attempt + 1))
    raise RuntimeError("Cannot publish checkpoint; stopping further provider requests")


def main():
    git("config", "user.name", "github-actions[bot]")
    git("config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com")
    started = time.monotonic()
    last_code = 0
    max_batches = 1
    for batch in range(1, max_batches + 1):
        # Leave time for the next bounded batch and a final checkpoint.
        if time.monotonic() - started > 270 * 60:
            break
        git("pull", "--rebase", "origin", "main")
        before = snapshot()
        print(f"Starting Investment batch {batch}/{max_batches}", flush=True)
        command = [sys.executable, "scheduled_investment_scan.py", "--exchange", "all",
                   "--max-symbols-per-exchange", "75", "--min-market-cap-bn", "0.5",
                   "--workers", "2", "--gap-fill", "--closed-markets-only", "--resume"]
        try:
            last_code = subprocess.run(command, timeout=20 * 60).returncode
        except subprocess.TimeoutExpired:
            last_code = 124
        # Save progress even when individual provider results are unavailable.
        checkpoint()
        after = snapshot()
        if not may_continue(before, after, last_code):
            if is_soft_provider_pause(before, after, last_code):
                print(
                    "Pausing cleanly: provider/data gaps remain; saved progress will retry next run.",
                    flush=True,
                )
                last_code = 0
            else:
                print(
                    "Pausing: no further progress, completion, provider limit, or operational error.",
                    flush=True,
                )
            break
        if batch < max_batches:
            time.sleep(60)

    # If the bounded batch allowance ends while only expected provider/data gaps
    # remain, preserve the checkpoint and report a healthy scheduled run.
    if "before" in locals() and "after" in locals() and is_soft_provider_pause(before, after, last_code):
        print(
            "Batch allowance reached with only provider/data gaps; exiting cleanly for the next hourly retry.",
            flush=True,
        )
        return 0
    return last_code


if __name__ == "__main__":
    raise SystemExit(main())
