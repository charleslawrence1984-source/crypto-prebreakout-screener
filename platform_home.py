import streamlit as st

st.title("📊 Market Opportunity Platform")
st.caption("Working title — final product name to be decided.")

st.markdown(
    """
    ### One place to find better opportunities across markets

    This platform is built to help you **find, understand and track potential opportunities**
    without having to dig through endless charts, indicators and raw market data yourself.

    The analysis underneath can be detailed, but the experience stays simple:
    **see the decision first, understand why, then watch what needs to happen next.**
    """
)

st.markdown("---")

stocks_col, crypto_col, memes_col = st.columns(3)

with stocks_col:
    with st.container(border=True):
        st.markdown("## 📈 Stocks")
        st.write(
            "Find swing-trade setups and long-term investment opportunities using "
            "technical analysis, fundamental quality, valuation and margin of safety."
        )
        st.markdown("**Best for:** established companies, swing trades and long-term investing.")
        st.page_link(
            "stocks_page.py",
            label="Open Stocks",
            icon="📈",
            use_container_width=True,
        )

with crypto_col:
    with st.container(border=True):
        st.markdown("## ⚡ Crypto")
        st.write(
            "Find pre-breakout crypto setups before momentum becomes obvious, while checking "
            "relative strength, tokenomics, liquidity, market structure and macro conditions."
        )
        st.markdown("**Best for:** liquid crypto swing trades and accumulation setups.")
        st.page_link(
            "app.py",
            label="Open Crypto",
            icon="⚡",
            use_container_width=True,
        )

with memes_col:
    with st.container(border=True):
        st.markdown("## 🐸 Meme Coins")
        st.write(
            "Discover emerging meme coins using liquidity, real activity, community, narrative, "
            "tokenomics and anti-chase filters designed for much higher-risk markets."
        )
        st.markdown("**Best for:** early-stage speculative opportunities with strict risk filters.")
        st.page_link(
            "meme_app.py",
            label="Open Meme Coins",
            icon="🐸",
            use_container_width=True,
        )

st.markdown("---")

st.markdown("### How the platform works")
h1, h2, h3 = st.columns(3)
with h1:
    st.markdown("**1 · Choose a market**")
    st.caption("Stocks, Crypto or Meme Coins.")
with h2:
    st.markdown("**2 · See the decision first**")
    st.caption("The platform simplifies the analysis into clear actions such as BUY, WAIT or PASS.")
with h3:
    st.markdown("**3 · Track what matters**")
    st.caption("Use watchlists and opportunity screens instead of repeatedly searching the market from scratch.")

st.info(
    "The three areas use different rules because stocks, crypto and meme coins behave differently — "
    "but they are designed to feel like one consistent product."
)

st.caption("Screening and research tool only. It does not guarantee outcomes or remove market risk.")
