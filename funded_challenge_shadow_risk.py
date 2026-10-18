"""Research-only risk accounting for Kraken Funded prospective shadow outcomes.

No execution access, no real funded account balance, no margin/financing/
floating-P&L data and NO challenge-passed assertion. The original 12%/3%
challenge overlay is tested only on independently frozen PUBLIC-market proxies.
Model repeated observations of the same open asset as ONE hypothetical position,
not multiple profitable independent trades.
"""
from __future__ import annotations

from datetime import datetime, timezone
import math

from funded_forward_health import validate_snapshot

START_EQUITY = 1000.0
TARGET_EQUITY = 1120.0
FAIL_EQUITY = 970.0
MAX_SINGLE_RISK_USD = 5.0
DEFAULT_REDUCED_RISK_USD = 3.0
MAX_OPEN_PLANNED_RISK_USD = 10.0
MAX_POSITION_EQUITY_FRACTION = .35
MAX_COMBINED_NOTIONAL_EQUITY_FRACTION = 1.0
DEFAULT_COST_PCT = .5
EXIT_STATUSES = frozenset({
    "SHADOW STOP", "SHADOW STOP (GAP)", "SHADOW TARGET", "SHADOW TIMEOUT",
})
ADMISSIBLE = EXIT_STATUSES | {"SHADOW OPEN"}


def utc(value):
    instant = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise ValueError("Paper event timestamp must contain a timezone")
    return instant.astimezone(timezone.utc)


def finite(value):
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def extract_cases(snapshots, reports):
    """Bind frozen originals to independently evaluated, checksum-referenced cases."""
    by_key = {}
    for original in snapshots:
        validate_snapshot(original)
        key = (original["run_id"], original["payload_sha256"])
        if key in by_key:
            raise ValueError("Duplicate frozen source snapshot")
        by_key[key] = original
    if len(reports) != len(by_key):
        raise ValueError("Incomplete prospective report pairing")
    cases = []
    seen = set()
    for report in reports:
        if (report.get("research_only") is not True
                or report.get("broker_trades") != 0
                or report.get("funded_account_connected") is not False):
            raise ValueError("Outcome report is not broker-disconnected research")
        key = (report.get("source_run_id"), report.get("source_sha256"))
        if key not in by_key or key in seen:
            raise ValueError("Outcome does not reference one unique frozen source")
        seen.add(key)
        snapshot = by_key[key]
        originals = snapshot["rows"]
        outcomes = report.get("rows")
        if report.get("asset_type") != snapshot["asset_type"] or not isinstance(outcomes, list) or len(outcomes) != len(originals):
            raise ValueError("Outcome rows do not match original funded universe")
        clock = utc(report["evaluated_at_utc"])
        if clock < utc(snapshot["captured_at_utc"]):
            raise ValueError("Outcome evaluated before original signal")
        for original, observed in zip(originals, outcomes):
            if observed.get("asset") != original.get("asset"):
                raise ValueError("Outcome and original asset order diverged")
            if observed.get("research_only") is not True or observed.get("broker_trade") is not False:
                raise ValueError("Outcome falsely asserts a real broker trade")
            if observed.get("original_engine_status") != original.get("status"):
                raise ValueError("Outcome original engine state differs from frozen source")
            status = observed.get("shadow_status")
            if status not in ADMISSIBLE:
                continue
            entry = finite(observed.get("hypothetical_entry"))
            stop = finite(original.get("stop"))
            exit_price = finite(observed.get("hypothetical_exit"))
            if entry is None or stop is None or not 0 < stop < entry:
                raise ValueError("Paper position entry/stop not internally consistent")
            start = utc(observed["entry_at"])
            if start < utc(snapshot["captured_at_utc"]):
                raise ValueError("Paper entry is before original frozen observation")
            ended = None
            if status in EXIT_STATUSES:
                ended = utc(observed["exit_at"])
                if ended < start or ended > clock or exit_price is None or exit_price <= 0:
                    raise ValueError("Closed paper outcome is temporally/price invalid")
            elif observed.get("exit_at") is not None:
                raise ValueError("Open paper outcome has a fabricated closing time")
            cost = finite(observed.get("scenario_roundtrip_cost_pct"))
            if cost is None:
                cost = DEFAULT_COST_PCT
            if not 0 <= cost <= 5:
                raise ValueError("Invalid forward-only cost assumption")
            cases.append({
                "kind": snapshot["asset_type"], "asset": original["asset"],
                "source_run_id": snapshot["run_id"], "opened": start,
                "ended": ended, "entry": entry, "stop": stop,
                "exit": exit_price, "cost_pct": cost, "status": status,
            })
    return sorted(cases, key=lambda c: (c["opened"], c["asset"], c["kind"], c["source_run_id"]))


def simulate_risk(snapshots, reports, *, evaluated_at, risk_usd=DEFAULT_REDUCED_RISK_USD):
    """Model DISTINCT shadow positions and booked-only equity, fail closed.

    Never calls this a pass or authentic funded risk compliance: intrabar
    mark-to-market losses, real funded spreads/financing and 24/7 equity
    product rules are unobserved.
    """
    clock = utc(evaluated_at)
    budget = finite(risk_usd)
    if budget is None or not 0 < budget <= MAX_SINGLE_RISK_USD:
        raise ValueError("Paper risk budget exceeds original funded per-position cap")
    cases = extract_cases(snapshots, reports)
    equity = START_EQUITY
    high = START_EQUITY
    realized_peak_to_trough = 0.0
    active = []
    accepted = []
    vetoed = []
    closed = []
    peak_open_risk = 0.0
    breach = False
    target = False

    def complete_before(time):
        nonlocal equity, high, realized_peak_to_trough, breach, target
        due = sorted((item for item in active
                      if item["ended"] is not None and item["ended"] < time),
                     key=lambda item: (item["ended"], item["asset"]))
        for item in due:
            active.remove(item)
            realized = item["units"] * (item["exit"] - item["entry"]
                        - item["entry"] * item["cost_pct"]/100)
            equity = round(equity + realized, 8)
            high = max(high, equity)
            realized_peak_to_trough = max(realized_peak_to_trough, high - equity)
            breach = breach or equity <= FAIL_EQUITY
            target = target or equity >= TARGET_EQUITY
            closed.append({
                "asset": item["asset"], "kind": item["kind"],
                "source_run_id": item["source_run_id"],
                "paper_pnl_usd": round(realized, 6),
                "modeled_realized_equity_after_usd": round(equity, 6),
                "exit": item["ended"].isoformat(),
                "status": item["status"],
            })

    for case in cases:
        if case["opened"] > clock:
            raise ValueError("Paper entry after audit time")
        complete_before(case["opened"])
        name = case["kind"] + ":" + case["asset"]
        if breach or target:
            vetoed.append({"asset": name, "reason": "MODELLED FAIL/TARGET THRESHOLD REACHED"})
            continue
        if any(v["kind"] + ":" + v["asset"] == name for v in active):
            vetoed.append({"asset": name, "reason": "REPEATED SIGNAL ON ALREADY-OPEN ASSET"})
            continue
        risk_fraction = (case["entry"] - case["stop"])/case["entry"] + case["cost_pct"]/100
        if not math.isfinite(risk_fraction) or risk_fraction <= 0:
            vetoed.append({"asset": name, "reason": "INVALID FROZEN RISK"})
            continue
        open_risk = sum(item["planned_risk"] for item in active)
        notional_open = sum(item["notional"] for item in active)
        available_risk = MAX_OPEN_PLANNED_RISK_USD - open_risk
        available_notional = MAX_COMBINED_NOTIONAL_EQUITY_FRACTION * equity - notional_open
        notional = min(budget/risk_fraction,
                       MAX_POSITION_EQUITY_FRACTION * equity,
                       available_risk/risk_fraction, available_notional)
        if notional <= 0.01:
            vetoed.append({"asset": name, "reason": "OPEN RISK OR NOTIONAL CAP EXHAUSTED"})
            continue
        units = notional/case["entry"]
        risk = notional*risk_fraction
        item = dict(case, units=units, notional=notional, planned_risk=risk)
        active.append(item)
        peak_open_risk = max(peak_open_risk, sum(v["planned_risk"] for v in active))
        accepted.append({
            "asset": name, "source_run_id": case["source_run_id"],
            "entry_at": case["opened"].isoformat(),
            "hypothetical_position_notional_usd": round(notional, 6),
            "planned_risk_including_cost_usd": round(risk, 6),
            "paper_only": True,
        })
    complete_before(clock.replace(year=clock.year + 1) if clock.year < 9999 else clock)
    # Open states always retain their reserved risk. Any unresolved closed
    # outcome whose end has not yet elapsed must be rejected, not credited.
    if any(v["ended"] is not None for v in active):
        raise ValueError("Paper exit is later than evaluation time")
    return {
        "schema": "cl-signal-funded-paper-challenge-risk-v1",
        "as_of_utc": clock.isoformat(),
        "starting_equity_proxy_usd": START_EQUITY,
        "modeled_realized_equity_proxy_usd": round(equity, 6),
        "target_reference_usd": TARGET_EQUITY,
        "failure_reference_usd": FAIL_EQUITY,
        "risk_budget_per_paper_position_usd": budget,
        "max_per_position_risk_usd": MAX_SINGLE_RISK_USD,
        "max_total_open_risk_usd": MAX_OPEN_PLANNED_RISK_USD,
        "max_position_notional_pct": 35,
        "max_total_notional_pct": 100,
        "admitted_unique_paper_cases": len(accepted),
        "closed_unique_paper_cases": len(closed),
        "open_unique_paper_cases": len(active),
        "duplicate_or_risk_vetoed_cases": len(vetoed),
        "peak_planned_open_risk_usd": round(peak_open_risk, 6),
        "max_realized_only_peak_to_trough_drawdown_usd": round(realized_peak_to_trough, 6),
        "modeled_realized_threshold_breach": breach,
        "modeled_realized_target_threshold_seen": target,
        "unrealized_equity_and_floating_drawdown_unknown": True,
        "kraken_funded_challenge_pass_confirmed": False,
        "kraken_broker_account_connected": False,
        "research_only": True,
        "model_limits_cannot_prove_real_funded_rule_compliance": True,
        "admissions": accepted,
        "vetoes": vetoed,
        "closed_paper_cases": closed,
        "open_assets": [v["kind"] + ":" + v["asset"] for v in active],
        "note": ("Public-market proxy only. Equity accounts for hypothetical closed "
                 "cases and costs, NOT floating loss, funding, spreads, slippage, "
                 "fees, margin, leverage, execution or actual challenge equity."),
    }
