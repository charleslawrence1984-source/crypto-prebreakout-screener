"""Research-only audit of actual frozen prepared Trade technical CSV rows.

A cache-complete manifest and 'Technical calculated at' do NOT establish that
the underlying OHLCV data included the last completed exchange session.
This script reads all checked-in gzip CSVs without contacting an exchange,
modifying data, scheduling scans, or making any trading decision.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

LAST_BAR_FIELDS = (
    "Last completed exchange session",
    "Last completed OHLCV session",
    "Last completed bar",
    "Last historical bar",
    "Last bar date",
)


def read_venue(path: Path, manifest_row: dict, required_build: str,
               asof_utc: datetime) -> dict:
    result = {
        "path": path.name, "reported_eligible": manifest_row.get("eligible_symbols"),
        "reported_covered": manifest_row.get("coverage_symbols"),
        "reported_status": manifest_row.get("status"),
        "reported_rulebook": manifest_row.get("rulebook_build"),
        "file_exists": path.is_file(), "cached_rows": 0, "distinct_tickers": 0,
        "duplicate_ticker_rows": 0, "explicit_last_bar_field": None,
        "rows_without_last_bar": 0, "reported_future_last_bar": 0,
        "row_rulebook_mismatch": 0, "sha256": None, "flags": [],
        "last_completed_exchange_session_independently_verified": False,
    }
    if not path.is_file():
        result["flags"].append("MISSING_TECHNICAL_GZIP")
        return result
    result["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    try:
        with gzip.open(path, "rt", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            fields = reader.fieldnames or []
            provenance = next((field for field in LAST_BAR_FIELDS if field in fields), None)
            result["explicit_last_bar_field"] = provenance
            tickers = set()
            for row in reader:
                result["cached_rows"] += 1
                ticker = str(row.get("Ticker") or "").strip().upper()
                if ticker and ticker in tickers:
                    result["duplicate_ticker_rows"] += 1
                if ticker:
                    tickers.add(ticker)
                if row.get("Rulebook build") and row["Rulebook build"] != required_build:
                    result["row_rulebook_mismatch"] += 1
                last_bar = str(row.get(provenance) or "").strip() if provenance else ""
                if not last_bar:
                    result["rows_without_last_bar"] += 1
                else:
                    # A provider-reported date is not independently verified
                    # against holiday calendars, bars and source publication.
                    try:
                        stamp = datetime.fromisoformat(last_bar.replace("Z", "+00:00"))
                        if stamp.tzinfo is not None:
                            stamp = stamp.astimezone(timezone.utc).replace(tzinfo=None)
                        if stamp.date() > asof_utc.date():
                            result["reported_future_last_bar"] += 1
                    except ValueError:
                        result["rows_without_last_bar"] += 1
            result["distinct_tickers"] = len(tickers)
    except (OSError, csv.Error, UnicodeError) as exc:
        result["flags"].append("CORRUPT_OR_UNREADABLE_GZIP_" + type(exc).__name__)
        return result
    if result["cached_rows"] != manifest_row.get("coverage_symbols"):
        result["flags"].append("MANIFEST_COVERAGE_ROW_COUNT_MISMATCH")
    if manifest_row.get("rulebook_build") != required_build:
        result["flags"].append("MANIFEST_OLD_RULEBOOK")
    if manifest_row.get("status") != "complete" or int(manifest_row.get("remaining_symbols") or 0):
        result["flags"].append("MANIFEST_INCOMPLETE")
    if result["rows_without_last_bar"]:
        result["flags"].append("LAST_COMPLETED_BAR_MISSING")
    if result["duplicate_ticker_rows"]:
        result["flags"].append("DUPLICATE_TICKER")
    if result["row_rulebook_mismatch"]:
        result["flags"].append("ROW_OLD_RULEBOOK")
    if result["reported_future_last_bar"]:
        result["flags"].append("FUTURE_REPORTED_BAR")
    return result


def audit(root: Path, when: datetime | None = None) -> dict:
    when = when or datetime.now(timezone.utc)
    if when.tzinfo is None:
        raise ValueError("Timezone-aware reference required")
    manifest_path = root / "prepared_trade_technicals" / "manifest.json"
    manifest_raw = manifest_path.read_bytes()
    manifest = json.loads(manifest_raw)
    build = manifest["rulebook_build"]
    venues = {}
    for venue, metadata in sorted(manifest["exchanges"].items()):
        venues[venue] = read_venue(
            root / "prepared_trade_technicals" / (venue + ".csv.gz"),
            metadata, build, when,
        )
    return {
        "audited_at_utc": when.astimezone(timezone.utc).isoformat(),
        "scope": "READ_ONLY_CHECKED_IN_TRADE_TECHNICAL_CSV_PROVENANCE",
        "live_trade_readiness": "NOT_VERIFIED",
        "manifest_sha256": hashlib.sha256(manifest_raw).hexdigest(),
        "required_rulebook": build,
        "venues": venues,
        "summary": {
            "total_cached_rows": sum(v["cached_rows"] for v in venues.values()),
            "rows_without_explicit_last_completed_bar": sum(
                v["rows_without_last_bar"] for v in venues.values()),
            "venues_with_missing_session_provenance": [
                k for k, v in venues.items() if v["rows_without_last_bar"]],
            "venues_with_any_flags": [k for k, v in venues.items() if v["flags"]],
        },
        "limits": [
            "No exchange-calendar holiday or partial-day comparison",
            "No immutable provider OHLCV bars or exact last-session timestamps checked",
            "Even a present last-bar field is provider-claimed, not an independently verified source",
            "Current surviving constituent cache is not historical membership",
            "No point-in-time financial/event releases, FX, liquidity or broker paper fills",
        ],
        "next_gate": "Persist actual completed bar session + provider/source identity for every Trade symbol, verify against venue-local completed exchange calendar and independent raw OHLCV, then fail closed on stale/unknown before any publication.",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--output", type=Path, default=Path("trade_session_provenance_audit.json"))
    args = parser.parse_args()
    result = audit(args.root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result["summary"], indent=2))


if __name__ == "__main__":
    main()
