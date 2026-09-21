import streamlit as st

st.markdown(
    """
    <style>
    .block-container {
        max-width: 1240px;
        padding-top: 3.6rem;
        padding-bottom: 3rem;
    }
    .cl-wordmark {
        display:flex;
        align-items:center;
        gap:20px;
        margin:16px 0 40px 0;
    }
    .cl-logo-css {
        width:110px;
        height:110px;
        flex:0 0 110px;
        position:relative;
        filter:drop-shadow(0 12px 22px rgba(20,70,160,.18));
    }
    .cl-c-ring {
        position:absolute;
        left:3px;
        top:8px;
        width:78px;
        height:78px;
        border:14px solid #0b2d72;
        border-right-color:transparent;
        border-radius:50%;
        transform:rotate(-4deg);
        box-sizing:border-box;
        background:transparent;
    }
    .cl-l-vert {
        position:absolute;
        left:61px;
        top:8px;
        width:18px;
        height:86px;
        border-radius:9px 9px 6px 6px;
        background:linear-gradient(180deg,#2496ff 0%,#0f55bf 56%,#0a2b70 100%);
    }
    .cl-l-foot {
        position:absolute;
        left:61px;
        top:76px;
        width:47px;
        height:18px;
        border-radius:5px 11px 11px 5px;
        background:linear-gradient(90deg,#0a2b70 0%,#0f55bf 55%,#2496ff 100%);
    }
    .cl-signal-bar {
        position:absolute;
        bottom:25px;
        width:10px;
        border-radius:6px 6px 2px 2px;
        background:linear-gradient(180deg,#35a7ff 0%,#1464db 100%);
        box-shadow:0 1px 3px rgba(15,85,190,.18);
    }
    .cl-signal-bar.one { left:27px; height:20px; }
    .cl-signal-bar.two { left:41px; height:33px; }
    .cl-signal-bar.three { left:55px; height:48px; }
    .cl-title {
        color:#08152f;
        font-size:3rem;
        font-weight:900;
        line-height:.98;
        letter-spacing:-.045em;
    }
    .cl-subtitle {
        color:#6b7f99;
        font-size:1.08rem;
        margin-top:9px;
    }
    .eyebrow {
        color:#2f7bf2;
        text-transform:uppercase;
        font-size:.78rem;
        font-weight:800;
        letter-spacing:.11em;
        margin-bottom:12px;
    }
    .hero-title {
        color:#08152f;
        font-size:3.35rem;
        line-height:1.05;
        font-weight:900;
        letter-spacing:-.045em;
        margin-bottom:18px;
    }
    .hero-copy {
        color:#596d88;
        font-size:1.18rem;
        line-height:1.6;
        margin-bottom:14px;
    }
    .hero-panel {
        border:1px solid #e4ebf5;
        border-radius:22px;
        padding:24px;
        background:linear-gradient(145deg,#fbfdff,#f5f9ff);
        box-shadow:0 16px 40px rgba(16,45,92,.07);
    }
    .panel-kicker {
        color:#2f7bf2;
        font-size:.78rem;
        font-weight:800;
        letter-spacing:.08em;
        text-transform:uppercase;
        margin-bottom:10px;
    }
    .panel-title {
        color:#0a1735;
        font-size:1.6rem;
        font-weight:900;
        margin-bottom:10px;
    }
    .panel-copy {
        color:#61758f;
        line-height:1.55;
        margin-bottom:18px;
    }
    .mini-row {
        display:grid;
        grid-template-columns:1fr 1fr;
        gap:10px;
    }
    .mini-card {
        background:white;
        border:1px solid #e5ebf4;
        border-radius:14px;
        padding:13px 14px;
    }
    .mini-label {
        color:#7b8ba0;
        font-size:.72rem;
        font-weight:750;
        text-transform:uppercase;
        letter-spacing:.06em;
    }
    .mini-value {
        color:#0b1735;
        font-weight:850;
        margin-top:5px;
    }
    .section-title {
        color:#0a1735;
        font-size:2rem;
        font-weight:900;
        letter-spacing:-.03em;
        margin-top:34px;
        margin-bottom:6px;
    }
    .section-copy {
        color:#6b7d94;
        margin-bottom:18px;
    }
    .market-heading {
        color:#0a1735;
        font-size:1.55rem;
        font-weight:900;
        margin-bottom:5px;
    }
    .market-sub {
        color:#405876;
        font-weight:700;
        line-height:1.4;
        min-height:44px;
    }
    .market-copy {
        color:#687c96;
        line-height:1.55;
        min-height:118px;
        margin-top:12px;
    }
    .tags {
        display:flex;
        flex-wrap:wrap;
        gap:7px;
        margin-top:12px;
        margin-bottom:6px;
    }
    .tag {
        border-radius:999px;
        background:#f3f7fc;
        border:1px solid #e2e9f3;
        padding:5px 9px;
        color:#526983;
        font-size:.70rem;
        font-weight:800;
        text-transform:uppercase;
    }
    .step-num {
        width:38px;
        height:38px;
        display:flex;
        align-items:center;
        justify-content:center;
        border-radius:50%;
        background:#2f7bf2;
        color:#fff;
        font-weight:900;
        margin-bottom:12px;
    }
    .step-title {
        color:#0a1735;
        font-weight:900;
        font-size:1.12rem;
        margin-bottom:7px;
    }
    .step-copy {
        color:#687c96;
        line-height:1.5;
    }
    div[data-testid="stPageLink"] a {
        border-radius:12px !important;
        font-weight:800 !important;
        border:1px solid #bfd1eb !important;
        color:#174f9e !important;
        background:#ffffff !important;
    }
    div[data-testid="stPageLink"] a:hover {
        border-color:#2f7bf2 !important;
        color:#0f55bf !important;
        background:#f7faff !important;
    }
    @media(max-width:800px) {
        .hero-title { font-size:2.35rem; }
        .market-copy,.market-sub { min-height:0; }
    }
    </style>
    """,
    unsafe_allow_html=True,
)

st.markdown(
    """
    <div class="cl-wordmark">
        <div class="cl-logo-css" aria-label="CL Signal logo">
            <div class="cl-c-ring"></div>
            <div class="cl-l-vert"></div>
            <div class="cl-l-foot"></div>
            <div class="cl-signal-bar one"></div>
            <div class="cl-signal-bar two"></div>
            <div class="cl-signal-bar three"></div>
        </div>
        <div>
            <div class="cl-title">CL Signal</div>
            <div class="cl-subtitle">Market intelligence made simple.</div>
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)

left, right = st.columns([1.18, .82], gap="large", vertical_alignment="center")

with left:
    st.markdown(
        """
        <div class="eyebrow">Smarter insights. Brighter opportunities.</div>
        <div class="hero-title">Find opportunities across stocks, crypto and meme coins.</div>
        <div class="hero-copy">
            CL Signal helps you find, understand and track potential opportunities without
            digging through endless charts, indicators and raw market data yourself.
        </div>
        """,
        unsafe_allow_html=True,
    )
    b1, b2 = st.columns(2)
    with b1:
        st.page_link("stocks_page.py", label="Explore Stocks", icon="📈", use_container_width=True)
    with b2:
        st.page_link("app.py", label="Explore Crypto", icon="⚡", use_container_width=True)
    st.caption("Data-driven analysis · Decision-first experience · Watchlists · Advanced evidence when you want it")

with right:
    st.markdown(
        """
        <div class="hero-panel">
            <div class="panel-kicker">One platform · Three specialist engines</div>
            <div class="panel-title">Complex analysis underneath. Clear decisions on top.</div>
            <div class="panel-copy">
                Stocks, crypto and meme coins need different rules. CL Signal keeps those
                specialist models separate while giving you one consistent way to use them.
            </div>
            <div class="mini-row">
                <div class="mini-card">
                    <div class="mini-label">Stocks</div>
                    <div class="mini-value">Trade + Invest</div>
                </div>
                <div class="mini-card">
                    <div class="mini-label">Crypto</div>
                    <div class="mini-value">Swing + Accumulate</div>
                </div>
                <div class="mini-card">
                    <div class="mini-label">Meme Coins</div>
                    <div class="mini-value">Early Discovery</div>
                </div>
                <div class="mini-card">
                    <div class="mini-label">Workflow</div>
                    <div class="mini-value">BUY · WAIT · PASS</div>
                </div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

st.markdown('<div class="section-title">Choose your market</div>', unsafe_allow_html=True)
st.markdown(
    '<div class="section-copy">Start where you want to research. Every section uses the same simple CL Signal journey.</div>',
    unsafe_allow_html=True,
)

c1, c2, c3 = st.columns(3, gap="medium")

with c1:
    with st.container(border=True):
        st.markdown("### 📈 Stocks")
        st.markdown(
            '<div class="market-sub">Swing trades and long-term investment opportunities.</div>',
            unsafe_allow_html=True,
        )
        st.markdown(
            '<div class="market-copy">Find technical trade setups alongside long-term company analysis using fundamentals, valuation, margin of safety and disciplined entry criteria.</div>',
            unsafe_allow_html=True,
        )
        st.markdown(
            '<div class="tags"><span class="tag">Swing trades</span><span class="tag">Long-term</span><span class="tag">Valuation</span></div>',
            unsafe_allow_html=True,
        )
        st.page_link("stocks_page.py", label="Open Stocks", icon="📈", use_container_width=True)

with c2:
    with st.container(border=True):
        st.markdown("### ⚡ Crypto")
        st.markdown(
            '<div class="market-sub">Pre-breakout swing setups and accumulation opportunities.</div>',
            unsafe_allow_html=True,
        )
        st.markdown(
            '<div class="market-copy">Look for compression before expansion while checking relative strength, tokenomics, exchange breadth, market structure and macro liquidity.</div>',
            unsafe_allow_html=True,
        )
        st.markdown(
            '<div class="tags"><span class="tag">Pre-breakout</span><span class="tag">Accumulation</span><span class="tag">RS vs BTC</span></div>',
            unsafe_allow_html=True,
        )
        st.page_link("app.py", label="Open Crypto", icon="⚡", use_container_width=True)

with c3:
    with st.container(border=True):
        st.markdown("### 🐸 Meme Coins")
        st.markdown(
            '<div class="market-sub">Early-stage discovery with stricter filters.</div>',
            unsafe_allow_html=True,
        )
        st.markdown(
            '<div class="market-copy">Cut through the noise using liquidity, activity, community, narrative, tokenomics and anti-chase rules designed for a much higher-risk market.</div>',
            unsafe_allow_html=True,
        )
        st.markdown(
            '<div class="tags"><span class="tag">Early stage</span><span class="tag">High risk</span><span class="tag">Community</span></div>',
            unsafe_allow_html=True,
        )
        st.page_link("meme_app.py", label="Open Meme Coins", icon="🐸", use_container_width=True)

st.markdown('<div class="section-title">How CL Signal works</div>', unsafe_allow_html=True)
st.markdown(
    '<div class="section-copy">You should not need to understand every indicator to understand what the platform is telling you.</div>',
    unsafe_allow_html=True,
)

s1, s2, s3 = st.columns(3, gap="medium")

with s1:
    with st.container(border=True):
        st.markdown('<div class="step-num">1</div>', unsafe_allow_html=True)
        st.markdown('<div class="step-title">Search or discover</div>', unsafe_allow_html=True)
        st.markdown(
            '<div class="step-copy">Search a company or coin directly, or browse opportunities the relevant market engine has already found.</div>',
            unsafe_allow_html=True,
        )

with s2:
    with st.container(border=True):
        st.markdown('<div class="step-num">2</div>', unsafe_allow_html=True)
        st.markdown('<div class="step-title">See the decision first</div>', unsafe_allow_html=True)
        st.markdown(
            '<div class="step-copy">Start with BUY, WAIT or PASS and the reason. Open the deeper evidence only when you want to understand more.</div>',
            unsafe_allow_html=True,
        )

with s3:
    with st.container(border=True):
        st.markdown('<div class="step-num">3</div>', unsafe_allow_html=True)
        st.markdown('<div class="step-title">Track what matters</div>', unsafe_allow_html=True)
        st.markdown(
            '<div class="step-copy">Add interesting opportunities to a watchlist so you can focus on what needs to change instead of starting again.</div>',
            unsafe_allow_html=True,
        )

st.caption("CL Signal is a screening and research tool. It does not guarantee outcomes or remove market risk.")
