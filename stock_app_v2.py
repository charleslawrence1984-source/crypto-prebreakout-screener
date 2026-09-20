import streamlit as st

st.set_page_config(
    page_title="Market Opportunity Platform",
    page_icon="📊",
    layout="wide",
)

stocks = st.Page(
    "stocks_page.py",
    title="Stocks",
    icon="📈",
    url_path="stocks",
    default=True,
)
crypto = st.Page(
    "app.py",
    title="Crypto",
    icon="⚡",
    url_path="crypto",
)
memes = st.Page(
    "meme_app.py",
    title="Meme Coins",
    icon="🐸",
    url_path="memes",
)

page = st.navigation(
    [stocks, crypto, memes],
    position="top",
)
page.run()
