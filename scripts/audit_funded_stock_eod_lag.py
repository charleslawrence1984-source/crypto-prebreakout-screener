#!/usr/bin/env python3
"""Read-only NYSE EOD availability audit. No network, scheduling or trading actions.

Input CSV: captured_at_utc,symbol,expected_session,last_bar_session,source,archive_sha256_ok,provider_error
One row per frozen stock observation; expected_session must come from the
existing NYSE completed-session gate, not naive calendar-day subtraction.
"""
import argparse
import csv
import json
from collections import defaultdict
from datetime import datetime, timezone


def audit(rows):
    grouped = defaultdict(list)
    for row in rows:
        when = datetime.fromisoformat(row["captured_at_utc"].replace("Z", "+00:00"))
        if when.tzinfo is None:
            raise ValueError("capture timestamp must have timezone")
        if not row["expected_session"]:
            raise ValueError("missing expected NYSE session")
        expected = row["expected_session"]
        last = row["last_bar_session"]
        if last and last > expected:
            raise ValueError("future session is not a completed NYSE session")
        integrity = row["archive_sha256_ok"].strip().lower() == "true"
        status = ("UNVERIFIED_ARCHIVE" if not integrity else
                  "PROVIDER_ERROR" if row["provider_error"] else
                  "FRESH" if last == expected else
                  "MISSING_BAR" if not last else "STALE")
        grouped[(when.astimezone(timezone.utc).isoformat(), expected)].append(
            {"symbol": row["symbol"], "source": row["source"],
             "last_bar_session": last, "status": status,
             "provider_error": row["provider_error"]})
    results = []
    for (captured, expected), entries in sorted(grouped.items()):
        counts = {k: sum(x["status"] == k for x in entries)
                  for k in ("FRESH", "STALE", "MISSING_BAR", "PROVIDER_ERROR", "UNVERIFIED_ARCHIVE")}
        results.append({"captured_at_utc": captured, "expected_session": expected,
                        "observed_rows": len(entries), "counts": counts,
                        "all_rows_fresh": bool(entries) and counts["FRESH"] == len(entries),
                        "rows": entries})
    return results


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("csv_path", help="existing frozen-evidence metadata CSV; no downloads")
    p.add_argument("--out", help="optional JSON report file")
    args = p.parse_args()
    with open(args.csv_path, newline="", encoding="utf-8") as fh:
        results = audit(csv.DictReader(fh))
    report = {"research_only": True, "no_schedule_recommendation_without_multiple_sessions": True,
              "observations": results}
    output = json.dumps(report, indent=2)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(output + "\n")
    else:
        print(output)


if __name__ == "__main__":
    main()
