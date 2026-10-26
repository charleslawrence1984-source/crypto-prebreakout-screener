#!/usr/bin/env python3
"""Offline missed-mover episode census. Research only; never creates BUY or orders.

CSV columns: asset,bar_utc,source_bar_utc,original_label,outcome,
             horizon_bars,fold,eligible,reason
outcome: MOVER, NONMOVER or UNKNOWN. Input labels must already be frozen as-of
and outcome horizons fully completed; this module never downloads future data.
An episode is the first eligible anchor of the same asset/fold/horizon/outcome
within horizon_bars * 4 hours of the previous retained anchor. This is a
descriptive non-overlap convention, not an execution simulation.
"""
import argparse
import csv
import json
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone


LABELS = {"BUY", "WATCH", "PASS"}
OUTCOMES = {"MOVER", "NONMOVER", "UNKNOWN"}


def utc(value):
    d = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if d.tzinfo is None or d.utcoffset() != timedelta(0):
        raise ValueError("bar timestamps must be UTC")
    return d.astimezone(timezone.utc)


def episodes(rows):
    grouped = defaultdict(list)
    excluded = Counter()
    for row in rows:
        if row["original_label"] not in LABELS or row["outcome"] not in OUTCOMES:
            raise ValueError("invalid original label or outcome")
        if row["eligible"].lower() != "true":
            excluded[row.get("reason") or "INELIGIBLE"] += 1
            continue
        h = int(row["horizon_bars"])
        if h <= 0:
            raise ValueError("positive horizon required")
        stamp = utc(row["bar_utc"])
        source = utc(row["source_bar_utc"])
        if source > stamp:
            raise ValueError("source bar later than observation")
        grouped[(row["asset"], row["fold"], h, row["outcome"])].append((stamp, source, row))
    result = []
    for (asset, fold, horizon, outcome), anchors in sorted(grouped.items()):
        previous = None
        seen = set()
        for stamp, source, row in sorted(anchors, key=lambda item: (item[0], item[1])):
            if source in seen:
                continue
            seen.add(source)
            if previous is not None and stamp < previous + timedelta(hours=4*horizon):
                continue
            previous = stamp
            result.append({"asset": asset, "fold": fold, "horizon_bars": horizon,
                           "outcome": outcome, "first_anchor_utc": stamp.isoformat(),
                           "source_bar_utc": source.isoformat(),
                           "original_label": row["original_label"]})
    matrix = Counter((r["fold"], r["horizon_bars"], r["original_label"], r["outcome"])
                     for r in result)
    return {"research_only": True, "episode_definition": "first eligible anchor, 4h horizon cooldown per asset/fold/horizon/outcome",
            "episodes": result, "matrix": [
                {"fold": k[0], "horizon_bars": k[1], "original_label": k[2],
                 "outcome": k[3], "count": v} for k, v in sorted(matrix.items())],
            "excluded": dict(excluded),
            "warning": "MOVER is future price movement, not a filled or profitable trade; separate MOVER/NONMOVER episode groups are descriptive, not independent trade controls."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv_path")
    parser.add_argument("--out")
    args = parser.parse_args()
    with open(args.csv_path, encoding="utf-8", newline="") as f:
        report = episodes(csv.DictReader(f))
    payload = json.dumps(report, indent=2)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(payload + "\n")
    else:
        print(payload)


if __name__ == "__main__":
    main()
