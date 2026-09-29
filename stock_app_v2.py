import streamlit as st

st.set_page_config(
    page_title="CL Signal",
    page_icon="📊",
    layout="wide",
)

home = st.Page(
    "platform_home.py",
    title="Home",
    icon="🏠",
    url_path="home",
    default=True,
)
stocks = st.Page(
    "stocks_page.py",
    title="Stocks",
    icon="📈",
    url_path="stocks",
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
kraken_funded = st.Page(
    "kraken_funded.py",
    title="Kraken Funded",
    icon="💼",
    url_path="kraken-funded",
)

page = st.navigation(
    [home, stocks, crypto, memes, kraken_funded],
    position="hidden",
)
page.run()
