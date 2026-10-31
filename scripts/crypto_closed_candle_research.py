"""Offline research-only OHLCV candle-identity audit for GENERAL CL Signal Crypto.

No exchange calls, BUY decisions, strategy thresholds, account operations or writes to input.
Input is JSON [open_ms, open, high, low, close, volume] CCXT rows.
The caller supplies the as-of clock and timeframe; not a live-feed validator.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path

TIMEFRAMES_MS = {"4h": 4 * 3600_000, "1d": 24 * 3600_000, "1w": 7 * 24 * 3600_000}


def parse_utc(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError("as-of must have explicit timezone")
    return dt.astimezone(timezone.utc)


def audit_candles(rows: list, timeframe: str, asof_utc: str) -> dict:
    """Audit open-time identity, gaps and closed-bar selection without repairing input."""
    if timeframe not in TIMEFRAMES_MS:
        raise ValueError("unsupported timeframe")
    clock_ms = int(parse_utc(asof_utc).timestamp() * 1000)
    interval = TIMEFRAMES_MS[timeframe]
    opens = []
    invalid_rows = 0
    for row in rows:
        try:
            if not isinstance(row, (list, tuple)) or len(row) < 6:
                raise ValueError("bad row shape")
            stamp = row[0]
            if isinstance(stamp, bool) or not isinstance(stamp, int) or stamp < 0:
                raise ValueError("bad open timestamp")
            values = [float(v) for v in row[1:6]]
            if not all(math.isfinite(v) for v in values):
                raise ValueError("nonfinite OHLCV")
            o, h, l, c, v = values
            if l > min(o, c) or h < max(o, c) or l > h or v < 0:
                raise ValueError("invalid OHLCV geometry")
            opens.append(stamp)
        except (TypeError, ValueError, OverflowError):
            invalid_rows += 1
    unique = sorted(set(opens))
    duplicates = len(opens) - len(unique)
    out_of_order = opens != sorted(opens)
    missing_bins = sum(
        max(0, (b - a) // interval - 1)
        for a, b in zip(unique, unique[1:])
        if (b - a) % interval == 0
    )
    irregular_steps = sum(
        1 for a, b in zip(unique, unique[1:]) if (b - a) % interval != 0
    )
    completed = [t for t in unique if t + interval <= clock_ms]
    latest = opens[-1] if opens else None
    reasons = []
    if not opens:
        reasons.append("NO_VALID_BARS")
    if invalid_rows:
        reasons.append("INVALID_ROWS")
    if duplicates:
        reasons.append("DUPLICATE_OPEN_TIMES")
    if out_of_order:
        reasons.append("OUT_OF_ORDER")
    if missing_bins:
        reasons.append("MISSING_BINS")
    if irregular_steps:
        reasons.append("IRREGULAR_STEP")
    if not completed:
        reasons.append("NO_COMPLETED_BAR")
    if latest is not None and latest + interval > clock_ms:
        reasons.append("SUPPLIED_LAST_BAR_UNCLOSED")
    if latest is not None and latest > clock_ms:
        reasons.append("FUTURE_OPEN")
    return {
        "timeframe": timeframe,
        "asof_utc": parse_utc(asof_utc).isoformat(),
        "supplied_rows": len(rows),
        "valid_rows": len(opens),
        "duplicate_open_times": duplicates,
        "missing_bins": missing_bins,
        "irregular_steps": irregular_steps,
        "latest_supplied_open_ms": latest,
        "latest_completed_open_ms": completed[-1] if completed else None,
        "scorer_last_bar_differs_from_completed": bool(completed and latest != completed[-1]),
        "status": "RESEARCH_BLOCKED" if reasons else "STRUCTURALLY_COMPLETE_ONLY",
        "reasons": reasons,
        "not_proven": [
            "source_authenticity", "scan_coverage", "point_in_time_universe",
            "fees_slippage", "forward_shadow_outcomes"
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows-json", required=True, type=Path)
    parser.add_argument("--timeframe", required=True, choices=sorted(TIMEFRAMES_MS))
    parser.add_argument("--asof-utc", required=True)
    args = parser.parse_args()
    raw = args.rows_json.read_bytes()
    rows = json.loads(raw)
    if not isinstance(rows, list):
        parser.error("input must be a JSON array of CCXT OHLCV rows")
    result = audit_candles(rows, args.timeframe, args.asof_utc)
    result["source_sha256"] = hashlib.sha256(raw).hexdigest()
    print(json.dumps(result, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
