"""Fail-closed RESEARCH-ONLY timeliness assessment of original funded crypto BUYs.

Consumes the already independently validated candle-identity JSON produced by
funded_capture_candle_identity.py. Never scores an indicator, reconstructs a
missed signal, creates a funded READY label or calls a trading/broker API.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta
import json
from pathlib import Path

from funded_capture_candle_identity import FOUR_HOURS, bucket, timestamp

IDENTITY_SCHEMA = "cl-signal-funded-original-candle-idempotence-v1"
SAFETY_SCHEMA = "cl-signal-funded-forward-signal-safety-v1"
CONSISTENT = "CONSISTENT_ORIGINAL_TECHNICAL_BUY_RESEARCH_ONLY"
QUARANTINE = "QUARANTINE_SOURCE_OR_STATE_CONFLICT"


def assess(report: dict, *, max_research_lag_minutes: int = 60) -> dict:
    """Record source-close-to-first-observation lag; never authorize execution.

    60m is an *operational research data-quality window*, not an optimized
    strategy gate, a broker quote-age guarantee or an authorized live rule.
    """
    if not isinstance(report, dict) or report.get("schema") != IDENTITY_SCHEMA:
        raise ValueError("missing authenticated candle identity report")
    if not isinstance(max_research_lag_minutes, int) or not 1 <= max_research_lag_minutes <= 240:
        raise ValueError("research lag bound must be 1..240 minutes")
    if (report.get("unknown_slots_are_not_no_signals") is not True
            or report.get("no_synthetic_signals_generated") is not True
            or report.get("broker_funded_quote_or_account_verified") is not False
            or report.get("live_strategy_or_schedule_changed") is not False
            or report.get("actual_scheduled_trigger_time_exposed") is not False):
        raise ValueError("identity report did not retain fail-closed broker/schedule invariants")
    n_scans = report.get("actual_crypto_snapshots")
    if not isinstance(n_scans, int) or n_scans < 1:
        raise ValueError("no authentic crypto observations")
    observed = report.get("4h_bins_observed")
    unknown = report.get("4h_bins_unobserved_UNKNOWN")
    total = report.get("4h_bins_between_first_and_last_actual_capture")
    if (not all(type(n) is int and n >= 0 for n in (observed, unknown, total))
            or observed < 1 or total != observed + unknown or n_scans < observed):
        raise ValueError("coverage accounting inconsistent")
    until = timestamp(report["audited_at_utc"])
    episodes = report.get("episodes")
    if not isinstance(episodes, list):
        raise ValueError("missing immutable episode ledger")
    if report.get("distinct_source_asset_and_bar_BUY_or_quarantine_episodes") != len(episodes):
        raise ValueError("episode-count mismatch")

    output = []
    total_original_buys = 0
    unique_consistent = 0
    quarantined = 0
    lagged_duplicate_observations = 0
    seen = set()
    limit = timedelta(minutes=max_research_lag_minutes)
    for episode in episodes:
        asset = episode.get("asset")
        bar_id = episode.get("bar_open_utc_or_quarantine")
        if not isinstance(asset, str) or not asset or not isinstance(bar_id, str):
            raise ValueError("malformed original episode identity")
        key = (asset, bar_id)
        if key in seen:
            raise ValueError("duplicate episode identity")
        seen.add(key)
        obs = episode.get("observations")
        if not isinstance(obs, list) or not obs:
            raise ValueError("missing real episode observations")
        buys = sum(x.get("state") == "BUY" for x in obs)
        if buys < 1 or buys != episode.get("original_buy_observations"):
            raise ValueError("original BUY observation count mismatch")
        total_original_buys += buys
        label = episode.get("eligibility")
        if label not in {CONSISTENT, QUARANTINE}:
            raise ValueError("unexpected original episode decision")
        records = []
        for item in obs:
            if not item.get("run_id") or not item.get("captured_at_utc"):
                raise ValueError("missing original capture source identity")
            clock = timestamp(item["captured_at_utc"])
            if clock > until:
                raise ValueError("future original capture is not evidence")
            records.append((clock, item))
        records.sort(key=lambda x: x[0])
        first_at = records[0][0]
        result = {
            "asset": asset,
            "source_bar_open_utc_or_quarantine": bar_id,
            "original_buy_observations": buys,
            "first_observed_run_id": records[0][1]["run_id"],
            "first_observed_at_utc": first_at.isoformat(),
            "distinct_source_episode_only": True,
            "funded_order_permission": False,
            "funded_instrument_quote_and_equity_unverified": True,
        }
        if label == QUARANTINE:
            quarantined += 1
            result["timeliness_classification"] = "QUARANTINE_INCONSISTENT_SOURCE"
            result["first_observation_lag_seconds"] = None
            result["research_timeliness_qualified"] = False
        else:
            unique_consistent += 1
            try:
                opened = timestamp(bar_id)
            except ValueError as exc:
                raise ValueError("valid episode missing canonical candle time") from exc
            if opened != bucket(opened):
                raise ValueError("valid episode has misaligned candle")
            closed = opened + FOUR_HOURS
            if first_at < closed + timedelta(minutes=1):
                raise ValueError("signal predates immutable completed-candle finality")
            for at, item in records:
                if (item.get("source_state") != "COMPLETED_SOURCE_CANDLE_AT_CAPTURE"
                        or item.get("state") != "BUY" or not item.get("spot_pair")
                        or at < closed + timedelta(minutes=1)):
                    raise ValueError("consistent episode contains invalid source observation")
            lagged_duplicate_observations += sum(
                at - closed > limit for at, _ in records[1:]
            )
            delay = first_at - closed
            timely = delay <= limit
            result.update({
                "source_bar_close_utc": closed.isoformat(),
                "first_observation_lag_seconds": int(delay.total_seconds()),
                "research_timeliness_qualified": timely,
                "timeliness_classification": (
                    "PROMPT_COMPLETED_CANDLE_RESEARCH_ONLY"
                    if timely else "LATE_FIRST_OBSERVATION_RESEARCH_ONLY"
                ),
                "later_duplicate_observations": len(records) - 1,
            })
        output.append(result)
    if (report.get("original_technical_BUY_observations") != total_original_buys
            or report.get("consistent_unique_original_technical_BUY_episodes") != unique_consistent
            or report.get("quarantined_BUY_episodes") != quarantined):
        raise ValueError("aggregate authenticated BUY counts inconsistent")
    prompt = sum(x["timeliness_classification"] == "PROMPT_COMPLETED_CANDLE_RESEARCH_ONLY"
                 for x in output)
    return {
        "schema": SAFETY_SCHEMA,
        "candle_identity_audited_at_utc": until.isoformat(),
        "research_lag_threshold_minutes_NOT_trading_rule": max_research_lag_minutes,
        "actual_crypto_snapshots": n_scans,
        "observed_4h_bins": observed,
        "unobserved_4h_bins_UNKNOWN": unknown,
        "4h_bin_coverage_pct": round(100 * observed / total, 2),
        "distinct_original_buy_episodes": len(output),
        "original_buy_observations": total_original_buys,
        "prompt_observed_unique_research_buy_episodes": prompt,
        "late_first_observation_episodes": unique_consistent - prompt,
        "quarantined_source_episodes": quarantined,
        "late_duplicate_observations_NOT_extra_trades": lagged_duplicate_observations,
        "episodes": output,
        "funded_ready_signals": 0,
        "verified_funded_orders": 0,
        "missing_capture_bins_not_backfilled": True,
        "broker_quotes_instruments_manual_stop_and_equity_unverified": True,
        "live_signals_rules_or_schedules_changed": False,
        "no_broker_orders_or_execution_authorized": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--identity-report", required=True)
    parser.add_argument("--out-file", required=True)
    parser.add_argument("--max-research-lag-minutes", type=int, default=60)
    args = parser.parse_args()
    report = json.loads(Path(args.identity_report).read_text(encoding="utf-8"))
    result = assess(report, max_research_lag_minutes=args.max_research_lag_minutes)
    destination = Path(args.out_file)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n",
                           encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "episodes"},
                     sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
