"""Managed Pension research workspace — no live trades, transfers or buy approvals.

The quantitative valuation model remains in strategy_scores_v3.py and the
decisions in two_strategy.py. This workspace is intentionally research-only
until independent historical testing and pension-provider comparisons pass.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import streamlit as st

from pension_guardrails import Holding, check_portfolio, check_research_candidate


st.set_page_config(page_title="CL Signal · Managed Pension", page_icon="🏦", layout="wide")

st.page_link("platform_home.py", label="← CL Signal Home", icon="🏠")
st.title("🏦 Managed Pension")
st.caption("CL Signal Investment · Ten years to forever · Long-term company ownership")
st.warning(
    "RESEARCH ONLY — Pension readiness has not been approved. "
    "Do not use this section to initiate a pension transfer or place a trade."
)

tab_research, tab_portfolio, tab_validation = st.tabs(
    ["Investment research", "Portfolio guardrails", "Readiness audit"]
)

with tab_research:
    st.subheader("Long-term company research — existing investment model")
    st.write(
        "This section uses the existing CL Signal long-term investment framework. "
        "Its fundamental scoring and valuation rules are intentionally unchanged. "
        "A saved research signal is not an independently verified pension purchase."
    )
    path = Path(__file__).resolve().parent / "prepared_priority_investments" / "summary.json"
    if not path.exists():
        st.info("No saved priority investment research file is available.")
    else:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            rows = pd.DataFrame(payload.get("rows", []))
        except (ValueError, OSError) as error:
            st.error(f"Cannot load saved investment research: {type(error).__name__}")
            rows = pd.DataFrame()
        if not rows.empty:
            discovered = int((rows.get("Action", pd.Series(dtype=str)) == "BUY CANDIDATE").sum())
            a, b, c = st.columns(3)
            a.metric("Saved research rows", len(rows))
            b.metric("Historical BUY CANDIDATE labels", discovered)
            c.metric("Pension buys authorised", 0)
            st.caption(
                "Saved rows are not sufficient to recommend purchases: full "
                "venue, broker eligibility, manual-quality review, recent quote "
                "and point-in-time investment testing are still required."
            )
            research = rows.loc[rows.get("Action") == "BUY CANDIDATE"].copy()
            cols = [
                key for key in (
                    "Ticker", "Company", "Prepared exchange", "Quality score",
                    "Valuation model version", "Valuation FX status", "Action",
                ) if key in research.columns
            ]
            if cols and not research.empty:
                research["Pension assessment"] = "RESEARCH ONLY — NOT APPROVED"
                st.dataframe(
                    research[cols + ["Pension assessment"]].head(30),
                    hide_index=True, use_container_width=True,
                )

with tab_portfolio:
    st.subheader("Allocation checks — no position data saved to GitHub")
    st.caption(
        "Enter holdings temporarily to check the unchanged CL Signal limits: "
        "no individual holding above 5% and no sector above 20%. "
        "Include cash in the total portfolio value."
    )
    holdings_table = st.data_editor(
        pd.DataFrame(columns=["Ticker", "Sector", "Market value GBP"]),
        num_rows="dynamic", hide_index=True, use_container_width=True,
        key="pension_research_holdings",
    )
    cash = st.number_input("Cash balance (£)", min_value=0.0, value=0.0, step=100.0)
    if st.button("Check proposed portfolio", type="primary"):
        holdings = []
        parse_errors = []
        for index, item in holdings_table.iterrows():
            try:
                amount = float(item["Market value GBP"])
            except (TypeError, ValueError):
                parse_errors.append(f"Row {index + 1}: invalid market value")
                continue
            holdings.append(Holding(
                str(item["Ticker"] or "").strip(),
                str(item["Sector"] or "").strip(),
                amount,
            ))
        errors = parse_errors + check_portfolio(holdings, cash_gbp=float(cash))
        if errors:
            st.error("Portfolio does not pass the allocation checks.")
            for issue in errors:
                st.write("•", issue)
        else:
            st.success("Allocation ceilings pass. This is not an investment recommendation.")

with tab_validation:
    st.subheader("Pension readiness checklist")
    st.markdown(
        """
        - [x] Separate research-only navigation from short-term funded trading
        - [x] Reject stale valuation versions and unverified currency conversions
        - [x] Check individual-stock and sector allocations
        - [ ] Build/verify full point-in-time investment backtest, including delistings
        - [ ] Audit dividends, FX, spread, tax, rebalancing, all exits and cash
        - [ ] Compare matched-period provider fund returns, fees and risk
        - [ ] Obtain written pension transfer rights and protection confirmations
        - [ ] Validate Trading 212 SIPP share eligibility before selecting a portfolio
        - [ ] Review independent forward performance before live pension deployment
        """
    )
    st.caption(
        "Research-only is a deliberate safety state. Neither a scoring model nor "
        "a successful unit test can guarantee future pension returns."
    )
