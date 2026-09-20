import streamlit as st

st.markdown(
    """
    <style>
    .block-container {
        padding-top: 1.35rem;
        padding-bottom: 2.4rem;
        max-width: 1320px;
    }

    .cl-brand {
        display:flex;
        align-items:center;
        gap:14px;
        margin: 2px 0 26px 0;
    }

    .cl-logo {
        width:58px;
        height:58px;
        border-radius:17px;
        background:linear-gradient(145deg,#091630,#16499d);
        position:relative;
        box-shadow:0 12px 28px rgba(24,73,157,.20);
        flex:0 0 58px;
    }

    .cl-logo:before {
        content:"";
        position:absolute;
        left:10px;
        top:10px;
        width:34px;
        height:34px;
        border:6px solid white;
        border-right-color:transparent;
        border-radius:50%;
    }

    .cl-bar {
        position:absolute;
        bottom:11px;
        width:5px;
        border-radius:10px;
        background:#3b88ff;
        z-index:2;
    }
    .cl-bar.one {left:28px;height:13px;}
    .cl-bar.two {left:36px;height:22px;}
    .cl-bar.three {left:44px;height:30px;}

    .cl-name {
        color:#091630;
        font-size:2rem;
        font-weight:850;
        line-height:1;
        letter-spacing:-.02em;
    }

    .cl-tagline {
        color:#657893;
        margin-top:6px;
        font-size:.98rem;
    }

    .hero {
        padding:18px 0 20px 0;
    }

    .eyebrow {
        color:#347cff;
        text-transform:uppercase;
        letter-spacing:.10em;
        font-size:.80rem;
        font-weight:800;
        margin-bottom:14px;
    }

    .hero-title {
        color:#08142f;
        font-size:3.55rem;
        line-height:1.04;
        font-weight:900;
        letter-spacing:-.045em;
        margin:0 0 20px 0;
    }

    .hero-copy {
        color:#526681;
        font-size:1.22rem;
        line-height:1.56;
        max-width:720px;
        margin-bottom:22px;
    }

    .pill-row {
        display:flex;
        flex-wrap:wrap;
        gap:10px;
        margin-top:20px;
    }

    .pill {
        padding:9px 13px;
        border-radius:999px;
        border:1px solid #e3eaf6;
        background:#f8faff;
        color:#536983;
        font-size:.91rem;
        font-weight:650;
    }

    .dashboard {
        background:linear-gradient(180deg,#ffffff 0%,#f7faff 100%);
        border:1px solid #e3ebf8;
        border-radius:24px;
        padding:22px;
        box-shadow:0 20px 46px rgba(11,37,83,.09);
    }

    .dash-top {
        display:flex;
        justify-content:space-between;
        align-items:center;
        margin-bottom:18px;
    }

    .dash-title {
        color:#0b1736;
        font-size:1.35rem;
        font-weight:850;
    }

    .dash-live {
        color:#16935f;
        background:#ecf8f2;
        border:1px solid #d2efe2;
        border-radius:999px;
        padding:6px 9px;
        font-size:.76rem;
        font-weight:800;
    }

    .dash-grid {
        display:grid;
        grid-template-columns:repeat(2,minmax(0,1fr));
        gap:12px;
        margin-bottom:14px;
    }

    .dash-tile {
        background:#fff;
        border:1px solid #e6edf7;
        border-radius:15px;
        padding:14px;
    }

    .dash-label {
        color:#71819a;
        font-size:.78rem;
        margin-bottom:5px;
    }

    .dash-value {
        color:#0c1735;
        font-size:1.07rem;
        font-weight:850;
    }

    .dash-sub {
        color:#15885a;
        margin-top:4px;
        font-size:.76rem;
        font-weight:700;
    }

    .chart-card {
        background:#fff;
        border:1px solid #e6edf7;
        border-radius:17px;
        padding:15px;
    }

    .chart-title {
        color:#334b68;
        font-size:.82rem;
        font-weight:750;
        margin-bottom:10px;
    }

    .chart-area {
        height:145px;
        border-radius:12px;
        position:relative;
        overflow:hidden;
        background:
          linear-gradient(180deg,rgba(49,124,255,.09),rgba(49,124,255,.01)),
          linear-gradient(90deg,#edf1f7 1px,transparent 1px),
          linear-gradient(#edf1f7 1px,transparent 1px);
        background-size:100% 100%,45px 45px,45px 45px;
    }

    .section-title {
        color:#0a1735;
        font-size:2rem;
        font-weight:850;
        letter-spacing:-.025em;
        margin-top:34px;
        margin-bottom:6px;
    }

    .section-copy {
        color:#687b94;
        font-size:1rem;
        margin-bottom:20px;
    }

    .market-card {
        height:100%;
        min-height:315px;
        background:#fff;
        border:1px solid #e4ebf6;
        border-radius:22px;
        padding:23px;
        box-shadow:0 9px 30px rgba(16,40,92,.05);
    }

    .market-icon {
        width:54px;
        height:54px;
        border-radius:17px;
        display:flex;
        align-items:center;
        justify-content:center;
        font-size:1.45rem;
        margin-bottom:16px;
    }

    .blue {background:#ebf3ff;}
    .green {background:#edf9f3;}
    .purple {background:#f4efff;}

    .market-name {
        color:#0a1735;
        font-size:1.65rem;
        font-weight:850;
        margin-bottom:7px;
    }

    .market-subtitle {
        color:#405674;
        font-size:1rem;
        font-weight:700;
        line-height:1.4;
        margin-bottom:13px;
    }

    .market-copy {
        color:#657893;
        line-height:1.55;
        font-size:.95rem;
        margin-bottom:14px;
    }

    .tag-row {
        display:flex;
        flex-wrap:wrap;
        gap:7px;
    }

    .tag {
        background:#f7f9fc;
        border:1px solid #e5ebf4;
        border-radius:999px;
        padding:6px 9px;
        color:#536983;
        font-size:.71rem;
        font-weight:800;
        text-transform:uppercase;
    }

    .feature-strip {
        margin-top:26px;
        background:#fff;
        border:1px solid #e4ebf6;
        border-radius:20px;
        padding:15px 10px;
        box-shadow:0 8px 24px rgba(16,40,92,.04);
    }

    .step-card {
        background:#fff;
        border:1px solid #e4ebf6;
        border-radius:20px;
        padding:20px;
        min-height:185px;
        box-shadow:0 8px 24px rgba(16,40,92,.04);
    }

    .step-number {
        width:38px;
        height:38px;
        border-radius:50%;
        display:flex;
        align-items:center;
        justify-content:center;
        background:#2f80ff;
        color:#fff;
        font-weight:850;
        margin-bottom:13px;
    }

    .step-title {
        color:#0a1735;
        font-size:1.12rem;
        font-weight:850;
        margin-bottom:8px;
    }

    .step-copy {
        color:#687b94;
        line-height:1.5;
        font-size:.92rem;
    }

    div[data-testid="stPageLink"] a {
        border-radius:12px !important;
        font-weight:750 !important;
        border:1px solid #d7e2f3 !important;
        background:white !important;
    }

    div[data-testid="stPageLink"] a:hover {
        border-color:#2f80ff !important;
        color:#1e66e5 !important;
        background:#f7faff !important;
    }

    @media (max-width: 800px) {
        .hero-title {font-size:2.55rem;}
        .market-card {min-height:auto;}
    }
    </style>
    """,
    unsafe_allow_html=True,
)

st.markdown(
    """
    <div class="cl-brand">
        <div class="cl-logo">
            <div class="cl-bar one"></div>
            <div class="cl-bar two"></div>
            <div class="cl-bar three"></div>
        </div>
        <div>
            <div class="cl-name">CL Signal</div>
            <div class="cl-tagline">Market intelligence made simple.</div>
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)

hero_left, hero_right = st.columns([1.14, 0.86], gap="large", vertical_alignment="center")

with hero_left:
    st.markdown(
        """
        <div class="hero">
            <div class="eyebrow">Smarter insights. Brighter opportunities.</div>
            <div class="hero-title">Find opportunities across stocks, crypto and meme coins.</div>
            <div class="hero-copy">
                CL Signal helps you find, understand and track potential opportunities
                without digging through endless charts, indicators and raw market data yourself.
                The analysis can be detailed underneath, but the experience stays simple.
            </div>
            <div class="pill-row">
                <div class="pill">✓ Data-driven analysis</div>
                <div class="pill">⚡ Decision-first UX</div>
                <div class="pill">◎ Watch what matters</div>
                <div class="pill">▥ Three market engines</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    cta1, cta2 = st.columns(2)
    with cta1:
        st.page_link(
            "stocks_page.py",
            label="Explore Stocks",
            icon="📈",
            use_container_width=True,
        )
    with cta2:
        st.page_link(
            "app.py",
            label="Explore Crypto",
            icon="⚡",
            use_container_width=True,
        )

with hero_right:
    st.markdown(
        """
        <div class="dashboard">
            <div class="dash-top">
                <div class="dash-title">CL Signal overview</div>
                <div class="dash-live">● LIVE PLATFORM</div>
            </div>

            <div class="dash-grid">
                <div class="dash-tile">
                    <div class="dash-label">STOCKS</div>
                    <div class="dash-value">Trade + Invest</div>
                    <div class="dash-sub">Technical & fundamental</div>
                </div>
                <div class="dash-tile">
                    <div class="dash-label">CRYPTO</div>
                    <div class="dash-value">Pre-breakout</div>
                    <div class="dash-sub">Swing + accumulation</div>
                </div>
                <div class="dash-tile">
                    <div class="dash-label">MEME COINS</div>
                    <div class="dash-value">Early discovery</div>
                    <div class="dash-sub">Stricter risk filters</div>
                </div>
                <div class="dash-tile">
                    <div class="dash-label">WORKFLOW</div>
                    <div class="dash-value">BUY · WAIT · PASS</div>
                    <div class="dash-sub">Decision first</div>
                </div>
            </div>

            <div class="chart-card">
                <div class="chart-title">From market noise to clearer decisions</div>
                <div class="chart-area">
                    <svg viewBox="0 0 600 145" preserveAspectRatio="none" style="position:absolute;inset:0;width:100%;height:100%;">
                        <polyline
                            fill="none"
                            stroke="#2f80ff"
                            stroke-width="4"
                            points="8,123 55,119 95,106 140,111 182,93 226,99 266,81 314,84 360,67 406,72 454,53 500,58 548,34 593,24"/>
                    </svg>
                </div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

st.markdown('<div class="section-title">Choose your market</div>', unsafe_allow_html=True)
st.markdown(
    '<div class="section-copy">Three specialised engines. One consistent CL Signal experience.</div>',
    unsafe_allow_html=True,
)

stocks_col, crypto_col, memes_col = st.columns(3, gap="medium")

with stocks_col:
    st.markdown(
        """
        <div class="market-card">
            <div class="market-icon blue">📈</div>
            <div class="market-name">Stocks</div>
            <div class="market-subtitle">Swing trades and long-term investment opportunities.</div>
            <div class="market-copy">
                Find technical trade setups alongside long-term company analysis using
                fundamentals, valuation, margin of safety and disciplined entry criteria.
            </div>
            <div class="tag-row">
                <div class="tag">Swing Trades</div>
                <div class="tag">Long-Term</div>
                <div class="tag">Valuation</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.page_link("stocks_page.py", label="Open Stocks", icon="📈", use_container_width=True)

with crypto_col:
    st.markdown(
        """
        <div class="market-card">
            <div class="market-icon green">⚡</div>
            <div class="market-name">Crypto</div>
            <div class="market-subtitle">Pre-breakout swing setups and accumulation opportunities.</div>
            <div class="market-copy">
                Look for compression before expansion while checking relative strength,
                tokenomics, exchange breadth, market structure and macro liquidity.
            </div>
            <div class="tag-row">
                <div class="tag">Pre-Breakout</div>
                <div class="tag">Accumulation</div>
                <div class="tag">RS vs BTC</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.page_link("app.py", label="Open Crypto", icon="⚡", use_container_width=True)

with memes_col:
    st.markdown(
        """
        <div class="market-card">
            <div class="market-icon purple">🐸</div>
            <div class="market-name">Meme Coins</div>
            <div class="market-subtitle">Early-stage discovery with stricter filters.</div>
            <div class="market-copy">
                Cut through the noise using liquidity, real trading activity, community,
                narrative, tokenomics and anti-chase rules designed for much higher risk.
            </div>
            <div class="tag-row">
                <div class="tag">Early Stage</div>
                <div class="tag">High Risk</div>
                <div class="tag">Community</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.page_link("meme_app.py", label="Open Meme Coins", icon="🐸", use_container_width=True)

st.markdown('<div class="feature-strip">', unsafe_allow_html=True)
f1, f2, f3, f4 = st.columns(4)
with f1:
    st.metric("Market engines", "3", help="Stocks, Crypto and Meme Coins")
with f2:
    st.metric("Decision style", "BUY · WAIT · PASS")
with f3:
    st.metric("Watchlists", "Built in")
with f4:
    st.metric("Advanced detail", "On demand")
st.markdown("</div>", unsafe_allow_html=True)

st.markdown('<div class="section-title">Three steps to smarter decisions</div>', unsafe_allow_html=True)
st.markdown(
    '<div class="section-copy">The heavy analysis stays underneath. The user journey stays simple.</div>',
    unsafe_allow_html=True,
)

step1, step2, step3 = st.columns(3, gap="medium")

with step1:
    st.markdown(
        """
        <div class="step-card">
            <div class="step-number">1</div>
            <div class="step-title">Search or discover</div>
            <div class="step-copy">
                Search a company or coin directly, or browse opportunities the relevant
                market engine has already surfaced.
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

with step2:
    st.markdown(
        """
        <div class="step-card">
            <div class="step-number">2</div>
            <div class="step-title">See the decision first</div>
            <div class="step-copy">
                CL Signal puts the decision and the reason up front. Deeper technical
                and fundamental evidence is there when you want it.
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

with step3:
    st.markdown(
        """
        <div class="step-card">
            <div class="step-number">3</div>
            <div class="step-title">Track what matters</div>
            <div class="step-copy">
                Add opportunities to a watchlist and focus on what needs to change rather
                than repeatedly starting your research again.
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

st.caption(
    "CL Signal is a screening and research tool. It does not guarantee outcomes or remove market risk."
)
