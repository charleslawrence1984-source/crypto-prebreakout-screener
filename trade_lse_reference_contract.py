"""Fail-closed intake for separately licensed LSE reference-price evidence.

This validates integrity of *supplied* bytes and normalized comparison rows.
It does NOT authenticate an official LSE file, infer a provider schema,
authorize corporate-action repairs, or emit a Trade signal. No files are
transferred or committed; real licensed source records stay outside Git.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path

TARGET_SESSION = "2024-05-01"
TARGET_SYMBOLS = ("AZN.L", "SHEL.L", "ULVR.L", "BP.L", "HSBA.L", "VOD.L")
PRICE_FIELDS = ("open", "high", "low", "close", "volume")
REQUIRED_METADATA = (
    "source_product", "source_access_right", "official_file_sha256",
    "source_file_name", "source_session", "reference_mic",
    "reference_quote_unit", "raw_or_adjusted", "instrument_isin_mapping_reviewed",
    "corporate_actions_reviewed",
)
ALLOWED_SOURCE_PRODUCTS = frozenset((
    "LSE_END_OF_DAY_SUMMARY", "LSE_DAILY_OFFICIAL_LIST", "LSE_TICK_DATA",
))
EXPECTED_COLUMNS = {
    "symbol", "date", "isin", "mic", "currency_unit",
    "open", "high", "low", "close", "volume",
}


def sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def geometry_problem(row: dict) -> str | None:
    try:
        numbers = [float(row[k]) for k in PRICE_FIELDS]
    except (ValueError, TypeError, KeyError):
        return "NONNUMERIC_OHLCV"
    if any(not math.isfinite(value) for value in numbers):
        return "NONFINITE_OHLCV"
    op, hi, lo, cl, vol = numbers
    if min(op, hi, lo, cl) <= 0 or vol < 0:
        return "INVALID_PRICE_OR_VOLUME"
    if hi < max(op, lo, cl) or lo > min(op, hi, cl):
        return "INVALID_OHLC_GEOMETRY"
    return None


def inspect_evidence(source_file: Path | None,
                     comparison_csv: Path | None,
                     provenance_json: Path | None,
                     now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    report = {
        "observed_at_utc": now.astimezone(timezone.utc).isoformat(),
        "scope": "PRIVATE_AUTHORITATIVE_SOURCE_INTAKE_RESEARCH",
        "live_trade_readiness": "NOT_VERIFIED",
        "reference_source_authenticated": False,
        "automatic_history_repair_authorised": False,
        "orders_authorised": False,
        "status": "BLOCKED_MISSING_SOURCE",
        "source_sha256": None,
        "comparison_sha256": None,
        "target_session": TARGET_SESSION,
        "symbols_required": list(TARGET_SYMBOLS),
        "symbols_observed": [],
        "issues": [],
        "manual_review_required": True,
    }
    if not all(p is not None and p.is_file() for p in (
        source_file, comparison_csv, provenance_json
    )):
        report["issues"].append("REQUIRED_SOURCE_AND_MAPPING_FILES_UNAVAILABLE")
        return report
    report["source_sha256"] = sha256(source_file)
    report["comparison_sha256"] = sha256(comparison_csv)
    try:
        meta = json.loads(provenance_json.read_text(encoding="utf-8"))
        missing = [k for k in REQUIRED_METADATA if not meta.get(k)]
        if missing:
            report["issues"].append("MISSING_SOURCE_METADATA:" + ",".join(missing))
        if str(meta.get("official_file_sha256") or "").lower() != report["source_sha256"]:
            report["issues"].append("SOURCE_FILE_SHA256_MISMATCH")
        if meta.get("source_product") not in ALLOWED_SOURCE_PRODUCTS:
            report["issues"].append("UNSUPPORTED_OFFICIAL_PRODUCT_CLAIM")
        if meta.get("source_session") != TARGET_SESSION:
            report["issues"].append("WRONG_REFERENCE_SESSION")
        if meta.get("reference_mic") != "XLON":
            report["issues"].append("UNVERIFIED_EXCHANGE_MIC")
        if meta.get("reference_quote_unit") != "GBX":
            report["issues"].append("UNVERIFIED_GBP_PENCE_UNITS")
        if meta.get("raw_or_adjusted") != "UNADJUSTED":
            report["issues"].append("UNKNOWN_CORPORATE_ACTION_ADJUSTMENT")
        if meta.get("instrument_isin_mapping_reviewed") is not True:
            report["issues"].append("INSTRUMENT_ISIN_MAPPING_NOT_REVIEWED")
        if meta.get("corporate_actions_reviewed") is not True:
            report["issues"].append("CORPORATE_ACTION_REVIEW_OUTSTANDING")
        with comparison_csv.open("r", encoding="utf-8-sig", newline="") as handle:
            rows = csv.DictReader(handle)
            if not EXPECTED_COLUMNS.issubset(set(rows.fieldnames or [])):
                report["issues"].append("NORMALIZED_PRICE_COLUMN_SET_INCOMPLETE")
                data = []
            else:
                data = list(rows)
        by_symbol = {}
        for record in data:
            symbol = str(record.get("symbol") or "").strip().upper()
            if record.get("date") != TARGET_SESSION:
                report["issues"].append("WRONG_NORMALIZED_RECORD_DATE:" + symbol)
            if record.get("mic") != "XLON":
                report["issues"].append("WRONG_NORMALIZED_RECORD_MIC:" + symbol)
            if record.get("currency_unit") != "GBX":
                report["issues"].append("WRONG_NORMALIZED_RECORD_UNIT:" + symbol)
            isin = str(record.get("isin") or "").strip().upper()
            if len(isin) != 12 or not isin[:2].isalpha() or not isin[2:].isalnum():
                report["issues"].append("INVALID_INSTRUMENT_ISIN:" + symbol)
            if symbol in by_symbol:
                report["issues"].append("DUPLICATE_SYMBOL_RECORD:" + symbol)
            if symbol not in TARGET_SYMBOLS:
                report["issues"].append("UNEXPECTED_SYMBOL:" + symbol)
            problem = geometry_problem(record)
            if problem:
                report["issues"].append(problem + ":" + symbol)
            by_symbol[symbol] = record
        for symbol in TARGET_SYMBOLS:
            if symbol not in by_symbol:
                report["issues"].append("MISSING_SYMBOL:" + symbol)
        report["symbols_observed"] = sorted(by_symbol)
        if not report["issues"]:
            report["status"] = "STRUCTURALLY_VALID_PENDING_INDEPENDENT_HUMAN_REVIEW"
            # A normalized CSV and a matching hash prove only local
            # consistency. They cannot authenticate publisher identity,
            # issuance time, market venue, licenses or price transformations.
        else:
            report["status"] = "BLOCKED_SOURCE_QUALITY"
    except (ValueError, OSError, UnicodeError, csv.Error, TypeError) as exc:
        report["status"] = "BLOCKED_SOURCE_PARSE_ERROR"
        report["issues"].append("PARSE_ERROR:" + type(exc).__name__)
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--official-file", type=Path)
    parser.add_argument("--normalized-prices", type=Path)
    parser.add_argument("--provenance", type=Path)
    parser.add_argument("--output", type=Path, default=Path("lse_reference_contract_report.json"))
    args = parser.parse_args()
    result = inspect_evidence(args.official_file, args.normalized_prices, args.provenance)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps({
        "scope": result["scope"], "status": result["status"],
        "issues": result["issues"], "readiness": result["live_trade_readiness"],
    }, indent=2))


if __name__ == "__main__":
    main()
