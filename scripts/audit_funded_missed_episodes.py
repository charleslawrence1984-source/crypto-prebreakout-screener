#!/usr/bin/env python3
"""Offline descriptive, ex-ante source-bar episode census; never places orders.

CSV: asset,bar_utc,source_bar_utc,original_label,outcome,horizon_bars,
     fold,eligible,reason
Future outcomes are attached AFTER episode membership is decided. UNKNOWN
means missing/censored and is never treated as a rejected trade. An episode
is anchored at the first eligible observation; the next episode starts only
after the prior horizon's fixed 4-hour interval. This is a descriptive
cooldown, not a trade simulator or an independent causal estimator.
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
        raise ValueError("timestamps must be UTC")
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
        horizon = int(row["horizon_bars"])
        if horizon <= 0:
            raise ValueError("positive horizon required")
        stamp, source = utc(row["bar_utc"]), utc(row["source_bar_utc"])
        if source > stamp:
            raise ValueError("source bar later than observation")
        # Deliberately exclude both original classification and future outcome
        # from the grouping key: neither can create additional episodes.
        grouped[(row["asset"], row["fold"], horizon)].append((stamp, source, row))
    result = []
    for (asset, fold, horizon), anchors in sorted(grouped.items()):
        previous = None
        seen = set()
        for stamp, source, row in sorted(anchors, key=lambda x: (x[0], x[1])):
            if source in seen:
                continue
            seen.add(source)
            if previous is not None and stamp < previous + timedelta(hours=4*horizon):
                continue
            previous = stamp
            result.append({
                "asset": asset, "fold": fold, "horizon_bars": horizon,
                "first_anchor_utc": stamp.isoformat(), "source_bar_utc": source.isoformat(),
                "original_label": row["original_label"], "outcome": row["outcome"],
                "outcome_scope": "FIRST_ANCHOR_ONLY",
            })
    matrix = Counter((r["fold"], r["horizon_bars"], r["original_label"], r["outcome"])
                     for r in result)
    return {
        "research_only": True,
        "episode_definition": "first eligible source-bar observation; fixed 4h horizon cooldown by asset/fold/horizon, before outcome",
        "episodes": result,
        "matrix": [{"fold": k[0], "horizon_bars": k[1], "original_label": k[2],
                    "outcome": k[3], "count": v} for k, v in sorted(matrix.items())],
        "excluded": dict(excluded),
        "warning": "Only first-anchor future outcome is attached; mixed outcomes inside a window do not create new episodes. No execution, profit or funded readiness inference.",
    }


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
