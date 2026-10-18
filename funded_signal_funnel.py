"""Read-only Kraken Funded ORIGINAL-signal funnel and prospective scan coverage.

A BUY/entry requires the original engine's actual status; merely passing
individual diagnostic gates never promotes WATCH/PASS to an entry. These
frozen research observations do not represent executable funded quotes.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
import json
import math
import os
from pathlib import Path

from funded_forward_health import (
    BAD_DATA, RESEARCH_CRYPTO_TRIGGER, RESEARCH_STOCK_TRIGGERS, validate_snapshot,
)

WATCH_STATES = frozenset({"WATCH", "FUNDED TREND WATCH", "PRE-CROSS READY"})
CRYPTO_MIN_SCORE = 80.0
CRYPTO_MIN_SPOT_VOLUME_USD = 1_000_000
MIN_PROJECTED_RR = 2.0
# This is a descriptive lag alert, NOT a guaranteed GitHub scheduling SLA.
CRYPTO_GAP_ALERT_HOURS = 5.5


def number(raw):
    try:
        value = float(raw)
        return value if math.isfinite(value) else None
    except (ValueError, TypeError):
        return None


def utc(raw):
    value = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("Actual capture timestamp must be timezone-aware UTC")
    return value.astimezone(timezone.utc)


def original_trigger(kind, state):
    return (state == RESEARCH_CRYPTO_TRIGGER if kind == "crypto"
            else state in RESEARCH_STOCK_TRIGGERS)


def summarize_snapshot(snap):
    """Count independent, possibly overlapping evidence gaps, not fake BUYs."""
    health = validate_snapshot(snap)
    kind = snap["asset_type"]
    states, blockers, watches, unavailable = Counter(), Counter(), [], []
    original_entry = []
    for row in snap["rows"]:
        name = row["asset"]
        state = str(row["status"]).strip().upper()
        states[state] += 1
        reason = str(row.get("reason") or "").upper()
        meta = row.get("meta") or {}
        if state in BAD_DATA:
            unavailable.append(name)
            continue
        if original_trigger(kind, state):
            original_entry.append(name)
        if state in WATCH_STATES:
            watches.append({
                "asset": name, "original_engine_state": state,
                "score": number(row.get("score")),
                "projected_rr": number(row.get("rr")),
                "reason": row.get("reason") or "",
            })
        if kind == "crypto":
            score = number(row.get("score"))
            rr = number(row.get("rr"))
            volume = number(meta.get("volume_usd_public_spot_proxy"))
            if score is None:
                blockers["score_unavailable"] += 1
            elif score < CRYPTO_MIN_SCORE:
                blockers["score_below_80"] += 1
            if rr is None:
                blockers["projected_rr_unavailable"] += 1
            elif rr < MIN_PROJECTED_RR:
                blockers["projected_rr_below_2"] += 1
            if volume is None:
                blockers["kraken_spot_volume_unverified"] += 1
            elif volume < CRYPTO_MIN_SPOT_VOLUME_USD:
                blockers["kraken_spot_volume_below_1m"] += 1
            if meta.get("entry_timing_actionable") is not True:
                blockers["timing_not_verified_actionable"] += 1
            if meta.get("swing_eligible") is not True:
                blockers["swing_shape_not_eligible"] += 1
            if name != "BTC":
                rs = number(meta.get("relative_strength_vs_btc_pct"))
                if rs is None or rs <= 0:
                    blockers["nonpositive_or_unknown_rs_vs_btc"] += 1
            if "TOO FAR BELOW RESISTANCE" in reason:
                blockers["distance_from_resistance"] += 1
            if "SHAPE FILTER NOT MET" in reason:
                blockers["original_shape_filter"] += 1
            if state == "PASS":
                blockers["original_crypto_rejection"] += 1
        else:
            if "NO ACTIVE RSI EXHAUSTION OR CURRENT CROSSOVER" in reason:
                blockers["rsi_macd_no_current_setup"] += 1
            if "SMA180 AND SMA200 ARE NOT BOTH RISING" in reason:
                blockers["slow_ma_trend_not_rising"] += 1
            if "NO PRICE CONTACT WITH THE MA SUPPORT ZONE" in reason:
                blockers["ma_support_contact_missing"] += 1
            if "PULLBACK" in reason:
                blockers["pullback_developing"] += 1
            if "COMPRESSION" in reason:
                blockers["compression_developing"] += 1
    return {
        "schema": "cl-signal-funded-observation-funnel-v1",
        "source_run_id": snap["run_id"],
        "source_sha256": snap["payload_sha256"],
        "captured_at_utc": snap["captured_at_utc"],
        "asset_type": kind,
        "approved_universe": health["approved_universe"],
        "usable_price_histories": health["usable_price_histories"],
        "coverage": health["coverage"],
        "original_states": dict(sorted(states.items())),
        "original_research_entry_triggers": original_entry,
        "watch_assets": watches,
        "unavailable_assets": unavailable,
        "overlapping_evidence_gaps": dict(sorted(blockers.items())),
        "diagnostic_counts_are_nonexclusive": True,
        "funded_broker_buy_confirmed": 0,
        "broker_quote_and_stop_execution_verified": False,
        "sample_is_not_strategy_pnl": True,
        "research_only": True,
        "note": ("Crypto PASS is a rejected setup. Score/volume/timing/reason counts "
                 "overlap. Only the original technical BUY/stock entry-status "
                 "is a research trigger; WATCH and diagnostics cannot create a trade."),
    }


def summarize_history(snapshots, *, as_of=None):
    """Build evidence of transitions and scheduling gaps from authentic archives."""
    if not snapshots:
        raise RuntimeError("No original funded research snapshots; cannot claim healthy coverage")
    seen = {}
    for snap in snapshots:
        validate_snapshot(snap)
        run = str(snap["run_id"])
        if run in seen:
            if seen[run] != snap["payload_sha256"]:
                raise ValueError("Conflicting frozen archives share the same original run ID")
            continue
        seen[run] = snap["payload_sha256"]
    unique = []
    processed = set()
    for snap in sorted(snapshots, key=lambda s: (utc(s["captured_at_utc"]), str(s["run_id"]))):
        if snap["run_id"] not in processed:
            unique.append(snap)
            processed.add(snap["run_id"])
    by_type = defaultdict(list)
    recurring_watch = set()
    watched = set()
    promoted = set()
    watch_to_trigger = []
    trigger_observations = []
    for snap in unique:
        when = utc(snap["captured_at_utc"])
        kind = snap["asset_type"]
        by_type[kind].append((when, snap))
        for row in snap["rows"]:
            key = (kind, row["asset"])
            state = str(row["status"]).strip().upper()
            if state in WATCH_STATES:
                if key in watched:
                    recurring_watch.add(key)
                watched.add(key)
            if original_trigger(kind, state):
                trigger_observations.append({"asset_type": kind, "asset": row["asset"],
                                             "captured_at_utc": when.isoformat(),
                                             "source_run_id": snap["run_id"]})
                if key in watched and key not in promoted:
                    promoted.add(key)
                    watch_to_trigger.append({"asset_type": kind, "asset": row["asset"],
                                             "captured_at_utc": when.isoformat()})
    crypto_gaps = []
    for (before, _), (after, _) in zip(by_type["crypto"], by_type["crypto"][1:]):
        hours = (after-before).total_seconds() / 3600
        if hours > CRYPTO_GAP_ALERT_HOURS:
            crypto_gaps.append({
                "last_completed_capture_utc": before.isoformat(),
                "next_completed_capture_utc": after.isoformat(),
                "gap_hours": round(hours, 2),
                "caution": "Observed scan gap; cannot assume uninterrupted 4h coverage",
            })
    latest = {}
    for kind in ("crypto", "stocks"):
        rows = by_type[kind]
        latest[kind] = rows[-1][0].isoformat() if rows else None
    captured_at = utc(as_of) if as_of is not None else datetime.now(timezone.utc)
    latest_crypto = by_type["crypto"][-1][0] if by_type["crypto"] else None
    age_hours = ((captured_at - latest_crypto).total_seconds() / 3600
                 if latest_crypto is not None else None)
    if age_hours is not None and age_hours < 0:
        raise ValueError("Audit time predates the latest original funded capture")
    return {
        "schema": "cl-signal-funded-funnel-history-v1",
        "audited_at_utc": captured_at.isoformat(),
        "total_original_snapshots": len(unique),
        "stocks_snapshots": len(by_type["stocks"]),
        "crypto_snapshots": len(by_type["crypto"]),
        "last_capture_utc": latest,
        "crypto_last_capture_age_hours": round(age_hours, 2) if age_hours is not None else None,
        "crypto_capture_age_warn_over_8h": age_hours is None or age_hours > 8,
        "observed_crypto_gaps_over_5_5h": crypto_gaps,
        "research_entry_trigger_observations": trigger_observations,
        "prior_watch_to_original_trigger_transitions": watch_to_trigger,
        "assets_watched_more_than_once": [
            {"asset_type": kind, "asset": asset} for kind, asset in sorted(recurring_watch)
        ],
        "broker_trades_verified": 0,
        "funded_challenge_pass_confirmed": False,
        "research_only": True,
        "data_limits": ("GitHub cron is best effort. These are only ACTUALLY "
                        "archived observations; gaps may hide setups. Repeated "
                        "observations of an asset are not independent trades."),
        "latest_snapshot_summaries": {
            kind: summarize_snapshot(by_type[kind][-1][1]) if by_type[kind] else None
            for kind in ("crypto", "stocks")
        },
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--from-github", action="store_true")
    parser.add_argument("--snapshot-dir", default="funded-forward-artifacts")
    parser.add_argument("--out-dir", default="funded-funnel-artifacts")
    parser.add_argument("--window-days", type=int, default=30)
    args = parser.parse_args()
    from funded_forward_outcome_runner import (
        load_local_snapshots, restore_github_archives,
    )
    clock = datetime.now(timezone.utc)
    if not 1 <= args.window_days <= 90:
        raise ValueError("Window must be 1–90 days")
    if args.from_github:
        records = restore_github_archives(
            repository=os.environ.get("GITHUB_REPOSITORY", ""),
            token=os.environ.get("GITHUB_TOKEN", ""),
            min_created=clock - timedelta(days=args.window_days),
            max_artifacts=250,
        )
    else:
        records = load_local_snapshots(Path(args.snapshot_dir))
    report = summarize_history(records, as_of=clock.isoformat())
    output = Path(args.out_dir)
    output.mkdir(parents=True, exist_ok=True)
    run = os.environ.get("GITHUB_RUN_ID", "manual")
    attempt = os.environ.get("GITHUB_RUN_ATTEMPT", "1")
    path = output / f"funded-funnel-{clock:%Y%m%dT%H%M%SZ}-{run}-{attempt}.json"
    with path.open("x", encoding="utf-8") as out:
        json.dump(report, out, indent=2, sort_keys=True, allow_nan=False)
        out.write("\n")
    print(json.dumps({
        "file": str(path),
        "original_snapshots": report["total_original_snapshots"],
        "crypto_scans": report["crypto_snapshots"],
        "stock_scans": report["stocks_snapshots"],
        "research_entry_triggers": len(report["research_entry_trigger_observations"]),
        "repeat_watch": report["assets_watched_more_than_once"],
        "crypto_gaps": report["observed_crypto_gaps_over_5_5h"],
        "funded_broker_trades": 0,
    }, sort_keys=True))
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as out:
            out.write(
                "### Kraken Funded: prospective ORIGINAL-signal funnel (not orders)\n"
                f"- Real archived Crypto scans: **{report['crypto_snapshots']}**; "
                f"Stocks scans: **{report['stocks_snapshots']}**\n"
                f"- Original research entry triggers: **{len(report['research_entry_trigger_observations'])}**\n"
                f"- Observed Crypto scan gaps >5.5h: "
                f"**{len(report['observed_crypto_gaps_over_5_5h'])}**\n"
                "- Research-only, no broker verification or automatic-stop protection; "
                "a WATCH is NOT a BUY.\n"
            )


if __name__ == "__main__":
    main()
