"""Honest forward-evidence sufficiency for Kraken Funded paper research only.

There is no valid profitability rate for zero closed prospective positions.
Neither sufficient sample size nor a successful proxy result proves
real Kraken Funded risk compliance or real brokerage execution.
"""
from __future__ import annotations

import math

MIN_DESCRIPTIVE_CLOSED_CASES = 30


def summarize_forward_evidence(journal):
    if (journal.get("research_only") is not True
            or journal.get("broker_trades_executed") != 0
            or journal.get("funded_challenge_pass_claimed") is not False):
        raise ValueError("Only disconnected Funded original-signal research can be summarised")
    n_snapshots = journal.get("source_snapshot_count")
    n_triggers = journal.get("original_technical_trigger_observations")
    if (not isinstance(n_snapshots, int) or n_snapshots < 1
            or not isinstance(n_triggers, int) or n_triggers < 0):
        raise ValueError("Original frozen research coverage must be verified first")
    model = journal.get("provisional_challenge_risk")
    if not isinstance(model, dict) or model.get("kraken_funded_challenge_pass_confirmed") is not False:
        raise ValueError("No verified, disconnected Kraken Funded risk proxy")
    if (model.get("research_only") is not True
            or model.get("kraken_broker_account_connected") is not False):
        raise ValueError("Broker-linked results cannot be paper evidence")
    accepted = model.get("admissions")
    closed = model.get("closed_paper_cases")
    if (not isinstance(accepted, list) or not isinstance(closed, list)
            or len(accepted) != model.get("admitted_unique_paper_cases")
            or len(closed) != model.get("closed_unique_paper_cases")
            or len(closed) > len(accepted)
            or len(accepted) > n_triggers):
        raise ValueError("Distinct paper cases disagree with frozen original source")
    statuses = journal.get("shadow_status_counts") or {}
    if not isinstance(statuses, dict):
        raise ValueError("Original forward-result status data missing")
    missing = sum(
        value for name, value in statuses.items()
        if isinstance(value, int) and (
            name == "DATA UNVERIFIED" or name.startswith("AWAITING ")
            or name == "FROZEN PLAN VETO"
        )
    )
    amounts = []
    for item in closed:
        pnl = item.get("paper_pnl_usd")
        if not isinstance(pnl, (int, float)) or not math.isfinite(float(pnl)):
            raise ValueError("A paper closed case has invalid net hypothetical dollar P&L")
        amounts.append(float(pnl))
    n = len(amounts)
    wins = sum(x > 0 for x in amounts)
    losses = sum(x < 0 for x in amounts)
    flat = n - wins - losses
    if n_triggers == 0:
        classification = "NO ORIGINAL TECHNICAL ENTRY OBSERVATIONS"
    elif not n:
        classification = "ORIGINAL TRIGGERS BUT NO CLOSED DISTINCT PAPER CASES"
    elif n < MIN_DESCRIPTIVE_CLOSED_CASES:
        classification = "PRELIMINARY SMALL PAPER-CASE SAMPLE"
    else:
        classification = "DESCRIPTIVE PAPER SAMPLE: STILL NOT LIVE-ACCOUNT VALIDATION"
    drawdown = model.get("max_realized_only_peak_to_trough_drawdown_usd")
    if n and (not isinstance(drawdown, (int, float)) or not math.isfinite(float(drawdown))):
        raise ValueError("Modeled realized-only drawdown missing or invalid")
    return {
        "schema": "cl-signal-funded-evidence-sufficiency-v1",
        "genuine_original_snapshot_count": n_snapshots,
        "original_technical_trigger_observation_count": n_triggers,
        "distinct_admitted_paper_positions": len(accepted),
        "distinct_closed_paper_positions": n,
        "distinct_open_paper_positions": model.get("open_unique_paper_cases"),
        "unresolved_or_quality_vetoed_observations": missing,
        "outcome_sample_classification": classification,
        "closed_cases_needed_for_descriptive_band": MIN_DESCRIPTIVE_CLOSED_CASES,
        "descriptive_sample_band_reached": n >= MIN_DESCRIPTIVE_CLOSED_CASES,
        "paper_winning_closed_cases": wins,
        "paper_losing_closed_cases": losses,
        "paper_flat_closed_cases": flat,
        "paper_closed_case_win_rate_pct": round(100 * wins/n, 3) if n else None,
        "paper_average_closed_net_pnl_usd": round(sum(amounts)/n, 6) if n else None,
        "paper_sum_closed_net_pnl_usd": round(sum(amounts), 6) if n else None,
        "paper_realized_only_max_drawdown_usd": round(float(drawdown), 6) if n else None,
        "broker_verified_win_rate_pct": None,
        "broker_verified_profit_usd": None,
        "kraken_funded_challenge_success_rate": None,
        "genuine_account_intrabar_drawdown_verified": False,
        "strategy_profitable_or_viable_proven": False,
        "research_only": True,
        "note": ("No observed paper positions means profitability and paper "
                 "drawdown statistics are UNKNOWN, never 0%. For nonzero "
                 "samples metrics are purely descriptive and may include "
                 "public-proxy pricing and selective censoring. The 30-case "
                 "threshold is a display convention, not statistical validation, "
                 "independent market regimes or actual Funded fills."),
    }
