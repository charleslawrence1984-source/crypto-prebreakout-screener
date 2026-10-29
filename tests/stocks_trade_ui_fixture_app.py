"""Rendered AppTest harness importing the real Stocks card and decision paths.

Not a browser E2E or production release. Source fixtures intentionally lack
independent official Trade receipts; a confident LEGACY score cannot show BUY.
"""
from __future__ import annotations

import streamlit as st

from stock_app import render_decision_card, quick_trade_decision
from trade_action_contract import REQUIRED_RECEIPTS, guarded_trade_publication
from two_strategy import trade_decision

LEGACY = {
    "price": 100.0, "swing_target": 125.0, "invalidation": 90.0,
    "trade_score": 90.0, "one_year_hold_score": 80.0,
    "hold_score": 80.0, "valuation_score": 20.0,
    "rr": 2.5, "upside_pct": 25.0, "in_preferred_zone": True,
    "classification": "Swing only",
}
scenario = st.radio(
    "Trade publication evidence",
    ["Legacy high score only", "Claimed source status, missing receipt",
     "All claimed receipts (still manual review)", "Approved engine BLOCKED"],
)
record = dict(LEGACY)
if scenario == "Claimed source status, missing receipt":
    record["approved_trade_status"] = "PAPER CANDIDATE"
elif scenario == "All claimed receipts (still manual review)":
    record["approved_trade_status"] = "PAPER CANDIDATE"
    record.update({key: True for key in REQUIRED_RECEIPTS})
elif scenario == "Approved engine BLOCKED":
    record["approved_trade_status"] = "BLOCKED"
    record.update({key: True for key in REQUIRED_RECEIPTS})

quick = quick_trade_decision(record)
direct = trade_decision(record)
ranked = guarded_trade_publication(record)
render_decision_card("Quick Analyse TRADE", quick["action"], quick["reason"])
render_decision_card("Direct ticker TRADE", direct["action"],
                     "; ".join(direct["reasons"]) or "No reasons")
render_decision_card("Ranked TRADE", ranked["action"], ranked["reason"])
st.caption("Fixture-only display; no exchange source, no live signal, no order.")
