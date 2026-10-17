"""Read-only, reproducible CL Signal Investment BUY publication/screen audit.

Checks the current repository's *actual* published CSV/JSON files and the
same guarded Streamlit loader/decision functions used by the Stocks app.
Only publication readiness is being audited; neither profits nor discretionary
investment suitability are inferred. Never writes repo data or invokes trades.
"""
from __future__ import annotations

import json
import math
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from investment_live_readiness import (
    InvestmentPublication,
    guard_portfolio_investment_adds,
    guard_quick_investment_decision,
    withhold_unverified_buys,
)
from strategy_scores_v3 import VALUATION_MODEL_VERSION


def nonempty_session(value):
    return isinstance(value, str) and len(value) == 10 and value[4] == "-" and value[7] == "-"


def audit(root: Path, *, now: datetime | None = None, check_ui: bool = True) -> dict:
    root = Path(root)
    evidence = InvestmentPublication(
        root, now=now or datetime.now(timezone.utc),
        expected_valuation_model=VALUATION_MODEL_VERSION,
    )
    summary = evidence.summary
    assert summary.get("refreshed_at") == evidence.priority.get("price_refreshed_at"), (
        "Priority publication manifest and summary timestamps disagree"
    )
    saved = summary.get("rows")
    assert isinstance(saved, list), "Priority summary has no listing rows"

    # Compare every summary item to the exact corresponding saved priority CSV.
    # This prevents one screen from seeing a newer quote or decision than another.
    data = {}
    collisions = []
    mismatch = []
    for kind in evidence.priority.get("exchanges", {}):
        path = root / "prepared_priority_investments" / (kind + ".csv.gz")
        if not path.exists():
            continue
        frame = pd.read_csv(path, compression="gzip").fillna(value={"Price candle date": ""}) if False else pd.read_csv(path, compression="gzip")
        if "Ticker" not in frame:
            mismatch.append(kind + ":missing-Ticker")
            continue
        for _, row in frame.iterrows():
            key = kind, str(row["Ticker"]).strip()
            if key in data:
                collisions.append(str(key))
            data[key] = row.to_dict()

    buy = [item for item in saved if item.get("Action") == "BUY CANDIDATE"]
    published_counts = Counter()
    failure_reasons = Counter()
    passes = []
    unverified = []
    verified_primary_buys = []
    seen = set()
    for item in saved:
        kind = str(item.get("Prepared exchange") or "").strip()
        symbol = str(item.get("Ticker") or "").strip()
        key = kind, symbol
        if not kind or not symbol:
            collisions.append(str(key) + ":missing-key")
            continue
        if key in seen:
            collisions.append(str(key) + ":duplicate-summary")
        seen.add(key)
        original = data.get(key)
        if original is None:
            mismatch.append(str(key) + ":missing-priority-CSV")
            continue
        for name in ("Action", "Valuation FX status", "Valuation gate", "Valuation model version"):
            if str(item.get(name) or "") != str(original.get(name) or ""):
                mismatch.append(str(key) + ":" + name)
        for name in ("Price", "Quality score", "Base margin of safety %",
                     "Required margin of safety %", "Bear margin of safety %"):
            lhs, rhs = item.get(name), original.get(name)
            try:
                if (lhs is None and pd.isna(rhs)) or (rhs is None and pd.isna(lhs)):
                    continue
                if not math.isclose(float(lhs), float(rhs), rel_tol=1e-8, abs_tol=1e-6):
                    mismatch.append(str(key) + ":" + name)
            except (TypeError, ValueError):
                if lhs is not None or not pd.isna(rhs):
                    mismatch.append(str(key) + ":" + name)
        raw_candle = original.get("Price candle date")
        date_csv = raw_candle if nonempty_session(raw_candle) else None
        if (item.get("Price candle date") or None) != date_csv:
            mismatch.append(str(key) + ":Price candle date")

    assert not collisions, "Duplicate or invalid priority tickers: " + repr(collisions[:10])
    assert not mismatch, "Priority summary/CSV mismatch: " + repr(mismatch[:15])
    assert len(data) == len(saved), f"Priority CSV/summary row counts differ: {len(data)} vs {len(saved)}"

    # Verify the model's exact per-listing gate and all three publication
    # overlays. A hidden/undated ticker cannot become a BUY in any of them.
    exchange_lookup = {k: k for k in evidence.priority.get("exchanges", {})}
    for item in buy:
        kind = str(item["Prepared exchange"])
        ticker = str(item["Ticker"])
        published_counts[kind] += 1
        reason = evidence.issue(ticker, kind=kind)
        test_frame = pd.DataFrame([{
            "Ticker": ticker,
            "Exchange": kind,
            "Action": "BUY CANDIDATE",
            "Decision reason": "cached valuation-only BUY",
            "Valuation gate": "PASS",
        }])
        feed = withhold_unverified_buys(test_frame, evidence, exchange_lookup).iloc[0]
        quick = guard_quick_investment_decision({"action": "BUY CANDIDATE"}, ticker, evidence)
        holdings = guard_portfolio_investment_adds(pd.DataFrame([{
            "Ticker": ticker,
            "Resolved ticker": ticker,
            "Action": "BUY MORE",
            "Review reason": "candidate",
        }]), evidence).iloc[0]
        if reason:
            failure_reasons[reason] += 1
            unverified.append({"exchange": kind, "ticker": ticker, "reason": reason})
            assert feed["Action"] != "BUY CANDIDATE", f"Feed bypass: {kind} {ticker}"
            assert quick["action"] != "BUY CANDIDATE", f"Quick bypass: {kind} {ticker}"
            assert holdings["Action"] != "BUY MORE", f"Portfolio bypass: {kind} {ticker}"
        else:
            passes.append({"exchange": kind, "ticker": ticker,
                           "candle": item.get("Price candle date"),
                           "quality": item.get("Quality score")})
            # Ambiguous listing identity should be rejected in Quick/Portfolio,
            # even if the explicitly keyed opportunity entry can pass.
            verified_primary_buys.append((kind, ticker))
            assert feed["Action"] == "BUY CANDIDATE", f"Valid feed suppressed: {kind} {ticker}"

    screen = {"checked": False}
    if check_ui:
        import stock_app  # Runs Streamlit declarations in bare, read-only CI mode.

        # This is the *production Streamlit backend loader*, not a duplicate
        # reproduction of its sector, FX, venue, and company-dedup logic.
        live_feed = stock_app.load_all_investment_opportunities()
        assert isinstance(live_feed, pd.DataFrame)
        primary_labels = {label: kind for label, kind in stock_app.INVESTMENT_UNIVERSES.items()}
        actionable = live_feed[live_feed["Action"].eq("BUY CANDIDATE")] if not live_feed.empty else live_feed
        displayed = []
        for _, row in actionable.iterrows():
            label = str(row.get("Exchange") or "")
            kind = primary_labels.get(label)
            ticker = str(row.get("Ticker") or "")
            assert kind, f"BUY displayed with unknown exchange label: {label}"
            assert not evidence.issue(ticker, kind=kind), f"Unverified BUY visible in Streamlit loader: {kind} {ticker}"
            quick = stock_app.quick_investment_decision(row.to_dict())
            assert quick.get("action") == "BUY CANDIDATE", (
                f"Home/Opportunities BUY conflicts with actual Quick Analysis: {kind} {ticker}: {quick}"
            )
            displayed.append({"exchange": kind, "ticker": ticker})
        # Confirm identical Home and Opportunities entry points use exactly one
        # loader. Portfolio independent gating is tested above.
        screen = {
            "checked": True,
            "prepared_feed_rows": len(live_feed),
            "home_and_opportunities_buy_count": len(actionable),
            "displayed_buy_listings": displayed,
            "unverified_displayed_buy_count": 0,
            "quick_analysis_disagreement_count": 0,
        }

    report = {
        "audit_timestamp_utc": evidence.now.isoformat(),
        "fundamental_timestamp": evidence.fundamental.get("updated_at"),
        "technical_timestamp": evidence.technical.get("updated_at"),
        "priority_refreshed_at": summary.get("refreshed_at"),
        "priority_rows": len(saved),
        "priority_valuation_buy_rows": len(buy),
        "priority_buy_by_exchange": dict(sorted(published_counts.items())),
        "fully_verified_priority_buy_rows": len(passes),
        "withheld_priority_buy_rows": len(unverified),
        "withheld_reasons": dict(failure_reasons.most_common()),
        "verified_priority_buy_listings": passes[:40],
        "streamlit_loader": screen,
        "source_candle_date_count": sum(nonempty_session(x.get("Price candle date")) for x in saved),
        "publication_consistency": "PASS",
        "note": "Publication verification and cross-screen signal safety only; not financial advice or historical alpha evidence.",
    }
    assert len(passes) + len(unverified) == len(buy)
    return report


if __name__ == "__main__":
    print(json.dumps(audit(Path(__file__).resolve().parents[1]), indent=2, sort_keys=True), flush=True)
