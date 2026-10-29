"""Separate read-only Pension section preview.

Explicitly preserves current browser-local Investment/Trade holdings where
they already live. No migrations, pension provider connections, security
purchases, portfolio data fetching or BUY authority in this preview.
"""
import streamlit as st

st.title("Pension · Investment")
st.caption("10+ year Investment research · read-only preview, not a SIPP account")
st.info(
    "Pension navigation preview. Your existing saved Investment and Trade holdings "
    "have not been moved, overwritten, deleted or imported into this section."
)
st.subheader("Long-term investment strategy")
st.write(
    "This future section will use the existing 10-years-to-forever Investment "
    "quality, valuation, margin-of-safety and publication safeguards; it will "
    "not reuse the Stocks TRADE entry signals or automatically buy ETFs."
)
st.warning(
    "Investment BUY indications are not enabled here. Independent source freshness, "
    "valuation evidence, portfolio-storage migration and an approved pension "
    "account boundary must pass before full release."
)
st.subheader("What will stay separate")
st.markdown(
    "- Stocks TRADE: research setups and positions intended for up to around 12 months.\n"
    "- Pension INVESTMENT: long-term holdings and 10+ year opportunity analysis.\n"
    "- Existing holdings: preserved in their current browser store until a "
    "verified, consented migration with rollback is tested."
)
st.caption(
    "No account access, portfolio balances, holdings, pension transfers, deposits "
    "or securities orders are performed on this page."
)
