"""Prospective Kraken Funded public-spot capture admission (research only).

Candidate for independently approved *redundant* 4h Github scheduling.
No retroactive BUY, broker action, order, market-provider switch, or
automatic resubmission. Never interprets an UNKNOWN/missing slot as PASS.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
import json
import os
import re
import urllib.request
import zipfile

from funded_forward_health import validate_snapshot
from funded_forward_capture import funded_universe

UTC = timezone.utc
BAR_PERIOD = timedelta(hours=4)
MAX_FIRST_CAPTURE_LAG = timedelta(minutes=90)  # operational cutoff; NOT trade rule
RECENT_HISTORY = timedelta(hours=8)
MAX_ARCHIVES = 1000
MAX_BYTES = 3_000_000
ARTIFACT_RE = re.compile(r"funded-crypto-[0-9]+-[0-9]+$")
JSON_RE = re.compile(r"funded-crypto-[0-9]{8}T[0-9]{6}Z-[0-9]+-[0-9]+\.json$")
REPO_RE = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


def utc_datetime(value: str) -> datetime:
    value = str(value)
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None or dt.utcoffset() != timedelta():
        raise ValueError("timestamp must be explicitly UTC")
    return dt.astimezone(UTC)


def expected_source_bar(now: datetime) -> tuple[datetime, datetime, float]:
    """The last FULL four-hour bar at actual dispatch, never nominal cron."""
    if now.tzinfo is None or now.utcoffset() != timedelta():
        raise ValueError("actual collection clock must be UTC")
    now = now.astimezone(UTC)
    boundary = now.replace(hour=(now.hour // 4) * 4, minute=0,
                           second=0, microsecond=0)
    # The just-ended candle cannot be used until 1 min after its close.
    if now - boundary < timedelta(minutes=1):
        boundary -= BAR_PERIOD
    open_at = boundary - BAR_PERIOD
    return open_at, boundary, (now - boundary).total_seconds() / 60


def already_captured(snapshots, source_open: datetime, *,
                     universe=None) -> bool:
    """Only a full, validated, already archived source-bar snapshot is final."""
    names = tuple((universe or funded_universe())["crypto"])
    for snap in snapshots:
        validate_snapshot(snap, universe=universe)
        if snap["asset_type"] != "crypto":
            continue
        # All original public Kraken spot rows must have the EXPECTED source
        # candle, not older history, missing timestamps or provider errors.
        if not all(
            row["status"] not in {"DATA UNAVAILABLE", "SCAN ERROR"}
            and row["asset"] == asset
            and row.get("bar_timestamp")
            and utc_datetime(row["bar_timestamp"]) == source_open
            and (row.get("meta") or {}).get("pair")
            for row, asset in zip(snap["rows"], names)
        ):
            continue
        captured = utc_datetime(snap["captured_at_utc"])
        # A later artifact cannot be counted as proof of an earlier gate.
        if captured >= source_open + BAR_PERIOD + timedelta(minutes=1):
            return True
    return False


def decide(now: datetime, snapshots, *, universe=None,
           max_lag=MAX_FIRST_CAPTURE_LAG) -> dict:
    """Fail closed on past-candle requests; retries only while still timely."""
    opening, close, lag = expected_source_bar(now)
    state = "ELIGIBLE_FRESH_4H_CANDLE"
    should_capture = True
    if now < close + timedelta(minutes=1):
        state = "CANDLE_FINALITY_PENDING"
        should_capture = False
    elif now - close > max_lag:
        state = "LATE_START_UNKNOWN_NO_BACKFILL"
        should_capture = False
    elif already_captured(snapshots, opening, universe=universe):
        state = "PRIOR_COMPLETE_SHA_VALIDATED_CAPTURE"
        should_capture = False
    return {
        "schema": "cl-signal-funded-prospective-admission-v1",
        "mode": "PUBLIC_CRYPTO_RESEARCH_ONLY",
        "actual_checked_at_utc": now.isoformat(),
        "last_complete_source_bar_open_utc": opening.isoformat(),
        "source_bar_close_utc": close.isoformat(),
        "minutes_after_bar_close": round(lag, 3),
        "capture_new_snapshot": should_capture,
        "decision": state,
        "unobserved_bars_are_UNKNOWN": True,
        "no_backdated_signal_or_broker_order": True,
        "historical_schedule_identity_unverified": True,
    }


def _github_request(url: str, *, token: str) -> bytes:
    """Drop private auth on GitHub's cross-host signed ZIP redirect."""
    import requests
    if not url.startswith("https://api.github.com/repos/"):
        raise ValueError("Original artifact download URL is not GitHub API")
    with requests.Session() as session:
        # requests strips bearer Authorization when GitHub redirects to
        # signed external artifact storage; urllib default leaked it there.
        with session.get(url, headers={
            "Authorization": "Bearer " + token,
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "cl-signal-funded-admission-read-only",
        }, timeout=35, allow_redirects=True, stream=True) as response:
            response.raise_for_status()
            data = bytearray()
            for piece in response.iter_content(chunk_size=65536):
                if not piece:
                    continue
                data.extend(piece)
                if len(data) > MAX_BYTES:
                    raise RuntimeError("GitHub artifact/source response over size budget")
    return bytes(data)


def read_recent_original_sources(repository: str, token: str, now: datetime):
    """Read exact prior MAIN snapshots, fail closed if API/history incomplete.

    Never queries orders or uses mutating GitHub methods.
    """
    if not REPO_RE.fullmatch(repository) or not token:
        raise ValueError("read-only repo and Actions token required")
    endpoint = f"https://api.github.com/repos/{repository}"
    earliest = now - RECENT_HISTORY
    matches = []
    exhausted = False
    count = 0
    for page in range(1, 11):
        content = json.loads(_github_request(
            f"{endpoint}/actions/artifacts?per_page=100&page={page}", token=token))
        items = content.get("artifacts")
        if not isinstance(items, list):
            raise RuntimeError("malformed GitHub artifact listing")
        count += len(items)
        for item in items:
            created = utc_datetime(item["created_at"])
            if created < earliest:
                exhausted = True
                break
            if (ARTIFACT_RE.fullmatch(str(item.get("name") or ""))
                    and not item.get("expired")
                    and (item.get("workflow_run") or {}).get("head_branch") == "main"):
                matches.append(item)
        if len(items) < 100:
            exhausted = True
        if exhausted:
            break
    if not exhausted or count > MAX_ARCHIVES:
        raise RuntimeError("Cannot prove complete recent GitHub artifact listing")
    archives = []
    for item in matches:
        zipped = _github_request(item["archive_download_url"], token=token)
        with zipfile.ZipFile(BytesIO(zipped)) as bundle:
            names = [n for n in bundle.namelist() if JSON_RE.fullmatch(Path(n).name)]
            if len(names) != 1:
                raise RuntimeError("Expected a single authentic frozen crypto JSON source")
            if bundle.getinfo(names[0]).file_size > MAX_BYTES:
                raise RuntimeError("Frozen JSON exceeds accepted size")
            source = json.loads(bundle.read(names[0]).decode("utf-8"))
        validate_snapshot(source)
        archives.append(source)
    return archives


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", default="funded-schedule-admission-evidence")
    parser.add_argument("--offline-fixture", default=None, help="Research tests only; no live API")
    args = parser.parse_args()
    now = datetime.now(UTC)
    output = Path(args.out_dir)
    output.mkdir(parents=True, exist_ok=True)
    try:
        if args.offline_fixture:
            sources = json.loads(Path(args.offline_fixture).read_text(encoding="utf-8"))
        else:
            sources = read_recent_original_sources(
                os.environ.get("GITHUB_REPOSITORY", ""),
                os.environ.get("GITHUB_TOKEN", ""), now)
        result = decide(now, sources)
    except Exception as exc:
        result = {
            "schema": "cl-signal-funded-prospective-admission-v1",
            "mode": "PUBLIC_CRYPTO_RESEARCH_ONLY",
            "capture_new_snapshot": False,
            "decision": "API_OR_SOURCE_UNVERIFIED_FAIL_CLOSED",
            "error_type": type(exc).__name__,
            "unobserved_bars_are_UNKNOWN": True,
            "no_backdated_signal_or_broker_order": True,
        }
        failure = True
    else:
        failure = result["decision"] in {
            "CANDLE_FINALITY_PENDING", "LATE_START_UNKNOWN_NO_BACKFILL",
        }
    (output / "scheduled_research_admission.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, sort_keys=True))
    step_file = os.environ.get("GITHUB_OUTPUT")
    if step_file:
        with open(step_file, "a", encoding="utf-8") as file:
            file.write("capture=" + ("true" if result["capture_new_snapshot"] else "false") + "\n")
    summary_file = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_file:
        with open(summary_file, "a", encoding="utf-8") as file:
            file.write("### Funded original crypto four-hour capture admission\n"
                       f"- Result: **{result['decision']}**\n"
                       "- SHA checked prior main-branch evidence; research only\n"
                       "- No broker/stop/position/order validation\n")
    # If the scheduled event is too late OR GitHub source evidence is
    # unavailable, fail the scheduled job rather than silently green it.
    return 2 if failure else 0


if __name__ == "__main__":
    raise SystemExit(main())
