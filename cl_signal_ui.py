import streamlit as st


def apply_cl_signal_styles():
    st.markdown(
        """
        <style>
        .block-container {
            max-width: 1240px;
            padding-top: 3.2rem;
            padding-bottom: 3rem;
        }

        .cl-module-brand {
            display:flex;
            align-items:center;
            gap:14px;
            margin:4px 0 8px 0;
        }

        .cl-module-logo {
            width:58px;
            height:58px;
            flex:0 0 58px;
            position:relative;
            filter:drop-shadow(0 8px 16px rgba(20,70,160,.14));
        }

        .cl-module-c {
            position:absolute;
            left:2px;
            top:5px;
            width:43px;
            height:43px;
            border:8px solid #0b2d72;
            border-right-color:transparent;
            border-radius:50%;
            box-sizing:border-box;
        }

        .cl-module-lv {
            position:absolute;
            left:34px;
            top:5px;
            width:10px;
            height:48px;
            border-radius:6px 6px 4px 4px;
            background:linear-gradient(180deg,#2496ff 0%,#0f55bf 56%,#0a2b70 100%);
        }

        .cl-module-lf {
            position:absolute;
            left:34px;
            top:43px;
            width:25px;
            height:10px;
            border-radius:4px 7px 7px 4px;
            background:linear-gradient(90deg,#0a2b70 0%,#0f55bf 55%,#2496ff 100%);
        }

        .cl-module-bar {
            position:absolute;
            bottom:13px;
            width:5px;
            border-radius:4px 4px 1px 1px;
            background:linear-gradient(180deg,#35a7ff 0%,#1464db 100%);
        }

        .cl-module-bar.one { left:15px; height:10px; }
        .cl-module-bar.two { left:22px; height:17px; }
        .cl-module-bar.three { left:29px; height:25px; }

        .cl-module-wordmark {
            color:#08152f;
            font-size:1.65rem;
            font-weight:900;
            line-height:1;
            letter-spacing:-.035em;
        }

        .cl-module-tagline {
            color:#71839a;
            font-size:.82rem;
            margin-top:5px;
        }

        .cl-module-kicker {
            color:#2f7bf2;
            text-transform:uppercase;
            font-size:.76rem;
            font-weight:850;
            letter-spacing:.10em;
            margin-top:28px;
            margin-bottom:8px;
        }

        .cl-module-title {
            color:#08152f;
            font-size:2.55rem;
            line-height:1.05;
            font-weight:900;
            letter-spacing:-.04em;
            margin-bottom:10px;
        }

        .cl-module-copy {
            color:#5f728b;
            font-size:1.06rem;
            line-height:1.55;
            margin-bottom:22px;
            max-width:820px;
        }

        div[data-testid="stVerticalBlockBorderWrapper"] {
            border-radius:18px;
        }

        div[data-baseweb="tab-list"] {
            gap:6px;
            background:#f6f9fd;
            border:1px solid #e5ebf4;
            border-radius:14px;
            padding:5px;
        }

        button[data-baseweb="tab"] {
            border-radius:10px;
            font-weight:750;
        }

        div[data-testid="stPageLink"] a {
            border-radius:12px !important;
            font-weight:800 !important;
            border:1px solid #dce5f2 !important;
            background:#fff !important;
        }

        div[data-testid="stPageLink"] a:hover {
            border-color:#2f7bf2 !important;
            background:#f7faff !important;
        }

        /* CL Signal action system */
        div[data-testid="stButton"] button[kind="primary"] {
            background:linear-gradient(135deg,#1769e0 0%,#2f8cff 100%) !important;
            border:1px solid #1769e0 !important;
            color:#ffffff !important;
            border-radius:11px !important;
            font-weight:800 !important;
            box-shadow:0 5px 14px rgba(23,105,224,.16) !important;
        }

        div[data-testid="stButton"] button[kind="primary"]:hover {
            background:linear-gradient(135deg,#0f55bf 0%,#247be8 100%) !important;
            border-color:#0f55bf !important;
            box-shadow:0 7px 18px rgba(23,105,224,.22) !important;
        }

        div[data-testid="stButton"] button[kind="primary"]:focus {
            box-shadow:0 0 0 3px rgba(47,123,242,.20) !important;
        }

        div[data-testid="stButton"] button[kind="secondary"] {
            background:#ffffff !important;
            border:1px solid #bfd1eb !important;
            color:#174f9e !important;
            border-radius:11px !important;
            font-weight:750 !important;
        }

        div[data-testid="stButton"] button[kind="secondary"]:hover {
            border-color:#2f7bf2 !important;
            color:#0f55bf !important;
            background:#f7faff !important;
        }

        div[data-testid="stDownloadButton"] button {
            background:#ffffff !important;
            border:1px solid #bfd1eb !important;
            color:#174f9e !important;
            border-radius:11px !important;
            font-weight:750 !important;
        }

        div[data-testid="stDownloadButton"] button:hover {
            border-color:#2f7bf2 !important;
            color:#0f55bf !important;
            background:#f7faff !important;
        }

        /* Destructive actions remain red */
        div[class*="st-key-remove_watch_"] button,
        div[class*="st-key-remove_crypto_watch_"] button,
        div[class*="st-key-remove_meme_watch_"] button {
            background:#fff7f7 !important;
            border:1px solid #e59a9a !important;
            color:#b42318 !important;
            box-shadow:none !important;
        }

        div[class*="st-key-remove_watch_"] button:hover,
        div[class*="st-key-remove_crypto_watch_"] button:hover,
        div[class*="st-key-remove_meme_watch_"] button:hover {
            background:#fff0f0 !important;
            border-color:#d65f5f !important;
            color:#8f1d14 !important;
        }

        section[data-testid="stSidebar"] {
            background:#f8faff;
            border-right:1px solid #e7edf6;
        }

        h2, h3 {
            color:#0a1735;
            letter-spacing:-.02em;
        }

        @media(max-width:800px) {
            .block-container {
                padding-top:2.3rem;
                padding-left:.8rem;
                padding-right:.8rem;
            }

            .cl-module-title {
                font-size:2.05rem;
            }

            .cl-module-copy {
                font-size:.98rem;
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_module_header(module_name: str, icon: str, subtitle: str):
    apply_cl_signal_styles()

    brand_col, back_col = st.columns([4, 1], vertical_alignment="center")

    with brand_col:
        st.markdown(
            """
            <div class="cl-module-brand">
                <div class="cl-module-logo" aria-label="CL Signal logo">
                    <div class="cl-module-c"></div>
                    <div class="cl-module-lv"></div>
                    <div class="cl-module-lf"></div>
                    <div class="cl-module-bar one"></div>
                    <div class="cl-module-bar two"></div>
                    <div class="cl-module-bar three"></div>
                </div>
                <div>
                    <div class="cl-module-wordmark">CL Signal</div>
                    <div class="cl-module-tagline">Market intelligence made simple.</div>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    with back_col:
        st.page_link(
            "platform_home.py",
            label="← Home",
            use_container_width=True,
        )

    st.markdown(
        f"""
        <div class="cl-module-kicker">{icon} &nbsp; CL SIGNAL · {module_name.upper()}</div>
        <div class="cl-module-title">{module_name}</div>
        <div class="cl-module-copy">{subtitle}</div>
        """,
        unsafe_allow_html=True,
    )



def render_signal_decision_card(title: str, action: str, reason: str):
    action_upper = str(action or "UNAVAILABLE").upper()

    if action_upper in {
        "BUY", "BUY CANDIDATE", "ACCUMULATE", "HIGH PRIORITY", "SHORTLIST"
    }:
        border, background, text_colour, icon = "#2e7d32", "#eef8f0", "#1b5e20", "🟢"
    elif action_upper in {"WAIT", "WATCH", "HOLD"}:
        border, background, text_colour, icon = "#d48a00", "#fff8e1", "#7a4d00", "🟠"
    elif action_upper in {"PASS", "AVOID", "SELL", "BLOCKED", "FAIL"}:
        border, background, text_colour, icon = "#c62828", "#fff0f0", "#8e1b1b", "🔴"
    else:
        border, background, text_colour, icon = "#6b7280", "#f5f5f5", "#374151", "⚪"

    st.markdown(
        f"""
        <div style="
            border:2px solid {border};
            background:{background};
            border-radius:16px;
            padding:18px 20px;
            min-height:175px;
            margin:6px 0 14px 0;
        ">
            <div style="
                font-size:.82rem;
                font-weight:800;
                letter-spacing:.07em;
                opacity:.72;
                margin-bottom:5px;
            ">{title}</div>
            <div style="
                font-size:2.35rem;
                line-height:1.05;
                font-weight:900;
                color:{text_colour};
                margin:6px 0 12px 0;
                letter-spacing:-.03em;
            ">{icon} {action_upper}</div>
            <div style="font-size:1rem;line-height:1.5;color:#313131;">
                {reason}
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
