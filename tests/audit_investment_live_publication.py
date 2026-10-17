"""Read-only publication audit; evidence, not a BUY recommendation.

Useful in PR CI to show actual prepared technical coverage and how many
valuation-only BUY rows would be permitted/withheld by the new UI guard.
Never writes prepared files, orders or pension state.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from investment_live_readiness import InvestmentPublication


def main():
    root = Path(__file__).resolve().parents[1]
    health = InvestmentPublication(root)
    fund = health.fundamental.get("exchanges", {})
    tech = health.technical.get("exchanges", {})
    priority = health.summary.get("rows", [])
    eligible = sum(int(x.get("eligible_symbols", 0) or 0) for x in tech.values())
    covered = sum(int(x.get("coverage_symbols", 0) or 0) for x in tech.values())
    pending = sum(int(x.get("remaining_symbols", 0) or 0) for x in tech.values())
    source_pending = sum(int(x.get("remaining_symbols", 0) or 0) for x in fund.values())
    source_deferred = sum(int(x.get("provider_deferred_symbols", 0) or 0) for x in fund.values())
    reasons = Counter()
    buy_rows = ready = 0
    per_exchange = Counter()
    for item in priority:
        if item.get("Action") != "BUY CANDIDATE":
            continue
        kind = str(item.get("Prepared exchange") or "")
        ticker = str(item.get("Ticker") or "")
        buy_rows += 1
        reason = health.issue(ticker, kind=kind)
        if reason:
            reasons[reason] += 1
        else:
            ready += 1
            per_exchange[kind] += 1
    report = {
        "source": "read-only draft branch prepared source files",
        "technical_manifest_timestamp": health.technical.get("updated_at"),
        "fundamental_manifest_timestamp": health.fundamental.get("updated_at"),
        "priority_price_timestamp": health.priority.get("price_refreshed_at"),
        "eligible": eligible, "technical_covered": covered,
        "technical_pending": pending,
        "upstream_retryable_missing": source_pending,
        "upstream_provider_deferred": source_deferred,
        "priority_valution_buy_rows_before_ui_guard": buy_rows,
        "ready_buy_rows_after_guard": ready,
        "withheld_buy_rows": buy_rows - ready,
        "withheld_reasons": dict(reasons.most_common(12)),
        "ready_by_exchange": dict(per_exchange),
    }
    print(json.dumps(report, indent=2, sort_keys=True), flush=True)
    assert ready + sum(reasons.values()) == buy_rows
    assert covered <= eligible
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
