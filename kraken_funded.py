from __future__ import annotations

import io
import json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import pandas as pd
import requests
import streamlit as st

from cl_signal_ui import render_module_header


st.set_page_config(page_title="CL Signal · Kraken Funded", page_icon="💼", layout="wide")

ROOT = Path(__file__).resolve().parent
TRADE_TECHNICAL_DIR = ROOT / "prepared_trade_technicals"
PREPARED_CRYPTO_DIR = ROOT / "prepared_crypto"

# Kraken Funded challenge overlay. The underlying CL Signal rulebooks are unchanged.
FUNDED_START_BALANCE = 1_000.0
FUNDED_TARGET_BALANCE = 1_120.0
FUNDED_FAIL_BALANCE = 970.0
FUNDED_STANDARD_RISK_USD = 4.0
FUNDED_MAX_RISK_USD = 5.0
FUNDED_MAX_TOTAL_OPEN_RISK_USD = 10.0
FUNDED_MIN_RR = 2.5
FUNDED_MAX_POSITION_PCT = 35.0

KRAKEN_FUNDED_STOCKS = [
    "NVDA", "NFLX", "SPCX", "GOOGL", "CRCL", "AAPL", "TSLA", "MSTR", "HOOD",
    "SNDK", "META", "AMZN", "BABA", "AMD", "ARM", "AVGO", "BB", "CBRS",
    "COIN", "CRWV", "HIMS", "INTC", "LITE", "MRVL", "MSFT", "MU", "ORCL",
    "PLTR", "RKLB", "TSM",
]

KRAKEN_FUNDED_CRYPTO = [
    "BTC", "ETH", "LINK", "BCH", "DOGE", "SHIB", "ARB", "BONK", "AAVE", "AVAX",
    "BNB", "CRV", "DOT", "FIL", "GRASS", "ADA", "HBAR", "INJ", "KAITO", "LDO",
    "LTC", "MOODENG", "NEAR", "PNUT", "SOL", "S", "TRUMP", "UNI", "WLD", "XRP",
    "ALGO", "APT", "ATOM",
]


def safe_number(value):
    try:
        number = float(value)
        return number if np.isfinite(number) else np.nan
    except Exception:
        return np.nan


def first_existing(frame: pd.DataFrame, names: list[str]) -> str | None:
    for name in names:
        if name in frame.columns:
            return name
    return None


def funded_position_plan(entry, stop, risk_usd=FUNDED_STANDARD_RISK_USD):
    entry = safe_number(entry)
    stop = safe_number(stop)
    if not np.isfinite(entry) or not np.isfinite(stop) or entry <= stop or entry <= 0:
        return np.nan, np.nan, np.nan

    per_unit_risk = entry - stop
    risk_units = risk_usd / per_unit_risk
    max_notional = FUNDED_START_BALANCE * FUNDED_MAX_POSITION_PCT / 100
    notional = min(risk_units * entry, max_notional)
    units = notional / entry
    actual_risk = units * per_unit_risk
    return units, notional, actual_risk


def funded_stock_status(row: pd.Series) -> str:
    state = str(row.get("Technical state") or "")
    if state == "WATCH":
        return "🟡 FUNDED WATCH"
    if state not in {"ENTRY READY", "AWAITING NEXT OPEN"}:
        return "⚪ NO CURRENT SETUP"

    rr = safe_number(row.get("R:R"))
    liquidity = safe_number(row.get("Median traded value GBPm"))
    entry = safe_number(row.get("Entry"))
    stop = safe_number(row.get("Stop"))

    if not np.isfinite(rr) or rr < FUNDED_MIN_RR:
        return "🟡 FUNDED WATCH · R:R"
    if not np.isfinite(liquidity) or liquidity < 0.5:
        return "🔴 FUNDED PASS · LIQUIDITY"
    if not np.isfinite(entry) or not np.isfinite(stop) or entry <= stop:
        return "🟡 FUNDED WATCH · RISK PLAN"
    return "🟢 FUNDED READY"


@st.cache_data(ttl=300, show_spinner=False)
def load_funded_stock_rows() -> pd.DataFrame:
    frames = []
    if TRADE_TECHNICAL_DIR.exists():
        for path in TRADE_TECHNICAL_DIR.glob("*.csv.gz"):
            try:
                frame = pd.read_csv(path, compression="gzip")
            except Exception:
                continue
            if frame.empty or "Ticker" not in frame.columns:
                continue
            frame = frame[frame["Ticker"].astype(str).str.upper().isin(KRAKEN_FUNDED_STOCKS)].copy()
            if not frame.empty:
                frame["Source market"] = path.stem.replace(".csv", "")
                frames.append(frame)

    if frames:
        technical = pd.concat(frames, ignore_index=True)
        if "Median traded value GBPm" in technical.columns:
            technical["_liq"] = pd.to_numeric(technical["Median traded value GBPm"], errors="coerce")
            technical = technical.sort_values("_liq", ascending=False, na_position="last")
        technical = technical.drop_duplicates("Ticker", keep="first")
    else:
        technical = pd.DataFrame(columns=["Ticker"])

    universe = pd.DataFrame({"Ticker": KRAKEN_FUNDED_STOCKS})
    output = universe.merge(technical, on="Ticker", how="left")

    state = output.get("Technical state", pd.Series("", index=output.index)).fillna("").astype(str)
    output["CL Signal"] = np.select(
        [
            state.isin(["ENTRY READY", "AWAITING NEXT OPEN"]),
            state.eq("WATCH"),
        ],
        [
            "🟢 READY",
            "🟡 WATCH",
        ],
        default="⚪ NO CURRENT SETUP",
    )

    macd = output.get("MACD progress", pd.Series("", index=output.index)).fillna("").astype(str).str.upper()
    output["MACD"] = np.select(
        [
            macd.eq("CONFIRMED"),
            macd.eq("NEAR CROSSOVER"),
            macd.eq("IMPROVING"),
            macd.eq("WEAK"),
        ],
        [
            "✅ Confirmed",
            "🟡 Near crossover",
            "🟠 Improving",
            "❌ Weak",
        ],
        default="—",
    )

    liquidity = pd.to_numeric(
        output.get("Median traded value GBPm", pd.Series(np.nan, index=output.index)),
        errors="coerce",
    )
    output["Liquidity"] = liquidity.apply(
        lambda value: (
            "—" if not np.isfinite(value)
            else "❌ <£0.01m" if value < 0.01
            else f"{'✅' if value >= 0.5 else '❌'} £{value:.2f}m"
        )
    )

    output["RSI"] = pd.to_numeric(output.get("RSI"), errors="coerce").round(1)
    output["Entry"] = pd.to_numeric(output.get("Entry"), errors="coerce")
    output["Stop"] = pd.to_numeric(output.get("Stop"), errors="coerce")
    output["Target"] = pd.to_numeric(output.get("Target"), errors="coerce")
    output["R:R"] = pd.to_numeric(output.get("R:R"), errors="coerce").round(2)

    output["Funded status"] = output.apply(funded_stock_status, axis=1)
    stock_plans = output.apply(
        lambda row: funded_position_plan(row.get("Entry"), row.get("Stop")),
        axis=1,
    )
    output["Risk $"] = [round(plan[2], 2) if np.isfinite(plan[2]) else np.nan for plan in stock_plans]
    output["Position $"] = [round(plan[1], 2) if np.isfinite(plan[1]) else np.nan for plan in stock_plans]

    return output


@st.cache_data(ttl=60, show_spinner=False)
def load_crypto_pipeline() -> dict[str, pd.DataFrame]:
    raw_base = (
        "https://raw.githubusercontent.com/"
        "charleslawrence1984-source/crypto-prebreakout-screener/"
        "crypto-data/prepared_crypto/"
    )
    names = {
        "scores": "deep_scores.csv.gz",
        "swing": "swing_opportunities.csv.gz",
        "active": "active_monitor.csv.gz",
    }

    def fetch(filename: str):
        try:
            response = requests.get(raw_base + filename, timeout=4)
            response.raise_for_status()
            return response.content
        except Exception:
            return None

    remote = {}
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {executor.submit(fetch, filename): key for key, filename in names.items()}
        for future in as_completed(futures):
            remote[futures[future]] = future.result()

    result = {}
    for key, filename in names.items():
        try:
            payload = remote.get(key)
            if payload:
                result[key] = pd.read_csv(io.BytesIO(payload), compression="gzip")
                continue
        except Exception:
            pass
        try:
            result[key] = pd.read_csv(PREPARED_CRYPTO_DIR / filename, compression="gzip")
        except Exception:
            result[key] = pd.DataFrame()
    return result


def normalise_crypto_base(frame: pd.DataFrame) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame()
    output = frame.copy()
    base_col = first_existing(output, ["Coin", "Base", "Symbol", "Ticker"])
    if not base_col:
        return pd.DataFrame()
    output["_Base"] = (
        output[base_col].astype(str).str.upper()
        .str.replace("/USDT", "", regex=False)
        .str.replace("/USD", "", regex=False)
        .str.replace("-USD", "", regex=False)
    )
    return output


@st.cache_data(ttl=60, show_spinner=False)
def load_funded_crypto_rows() -> pd.DataFrame:
    pipeline = load_crypto_pipeline()
    frames = []

    for priority, key in enumerate(["swing", "scores", "active"]):
        frame = normalise_crypto_base(pipeline.get(key, pd.DataFrame()))
        if frame.empty:
            continue
        frame = frame[frame["_Base"].isin(KRAKEN_FUNDED_CRYPTO)].copy()
        if frame.empty:
            continue
        frame["_priority"] = priority
        frames.append(frame)

    if frames:
        combined = pd.concat(frames, ignore_index=True, sort=False)
        combined = combined.sort_values("_priority").drop_duplicates("_Base", keep="first")
    else:
        combined = pd.DataFrame(columns=["_Base"])

    universe = pd.DataFrame({"Coin": KRAKEN_FUNDED_CRYPTO})
    output = universe.merge(combined, left_on="Coin", right_on="_Base", how="left")

    status_col = first_existing(
        output,
        ["Swing status", "Decision", "Trade decision", "Monitor state", "Entry mode"],
    )
    raw_status = (
        output[status_col].fillna("").astype(str).str.upper()
        if status_col else pd.Series("", index=output.index)
    )

    output["CL Signal"] = np.select(
        [
            raw_status.str.contains("BUY|READY|ENTRY", regex=True),
            raw_status.str.contains("WATCH|EARLY|MONITOR|DEVELOP", regex=True),
            raw_status.str.contains("PASS|AVOID|BLOCK|EXTENDED", regex=True),
        ],
        [
            "🟢 READY / BUY",
            "🟡 WATCH",
            "🔴 PASS",
        ],
        default="⚪ NOT DEEP-SCORED",
    )

    score_col = first_existing(output, ["Swing score", "Score", "Technical score"])
    output["Score"] = (
        pd.to_numeric(output[score_col], errors="coerce").round(1)
        if score_col else np.nan
    )

    timing_col = first_existing(
        output,
        ["Entry mode", "Entry timing", "Entry timing verdict", "discovery_entry_timing"],
    )
    output["Entry timing"] = output[timing_col].fillna("—").astype(str) if timing_col else "—"

    rsi_col = first_existing(output, ["RSI", "RSI 4h", "rsi"])
    output["RSI"] = (
        pd.to_numeric(output[rsi_col], errors="coerce").round(1)
        if rsi_col else np.nan
    )

    distance_col = first_existing(
        output,
        ["Distance to resistance %", "Live distance to resistance %", "distance_to_resistance_pct"],
    )
    output["To resistance %"] = (
        pd.to_numeric(output[distance_col], errors="coerce").round(1)
        if distance_col else np.nan
    )

    rr_col = first_existing(output, ["R:R", "Reward/Risk", "Reward risk"])
    output["R:R"] = (
        pd.to_numeric(output[rr_col], errors="coerce").round(2)
        if rr_col else np.nan
    )

    execution_col = first_existing(
        output,
        ["Execution available", "Execution liquidity pass", "Execution gate"],
    )
    if execution_col:
        raw_execution = output[execution_col]
        output["Execution"] = raw_execution.apply(
            lambda value: "✅" if str(value).strip().lower() in {"true", "1", "yes", "pass"} else "❌"
        )
    else:
        output["Execution"] = "—"

    entry_col = first_existing(output, ["Active entry", "Planned entry", "Entry"])
    stop_col = first_existing(output, ["Invalidation", "Stop"])
    target_col = first_existing(output, ["First technical target", "Target"])
    rr_funded_col = first_existing(output, ["Current R:R", "R:R", "Reward/Risk", "Reward risk"])

    output["Funded entry"] = pd.to_numeric(output[entry_col], errors="coerce") if entry_col else np.nan
    output["Funded stop"] = pd.to_numeric(output[stop_col], errors="coerce") if stop_col else np.nan
    output["Funded target"] = pd.to_numeric(output[target_col], errors="coerce") if target_col else np.nan
    output["Funded R:R"] = (
        pd.to_numeric(output[rr_funded_col], errors="coerce").round(2)
        if rr_funded_col else output["R:R"]
    )

    funded_status = []
    positions = []
    risks = []
    for _, row in output.iterrows():
        core = str(row.get("CL Signal") or "")
        timing = str(row.get("Entry timing") or "").upper()
        execution = str(row.get("Execution") or "")
        rr = safe_number(row.get("Funded R:R"))
        entry = safe_number(row.get("Funded entry"))
        stop = safe_number(row.get("Funded stop"))

        if core == "🔴 PASS":
            status = "🔴 FUNDED PASS"
        elif core == "⚪ NOT DEEP-SCORED":
            status = "⚪ NOT DEEP-SCORED"
        elif core == "🟡 WATCH":
            status = "🟡 FUNDED WATCH"
        elif "EXTENDED" in timing or "TOO LATE" in timing:
            status = "🔴 FUNDED PASS · LATE"
        elif execution == "❌":
            status = "🔴 FUNDED PASS · EXECUTION"
        elif not np.isfinite(rr) or rr < FUNDED_MIN_RR:
            status = "🟡 FUNDED WATCH · R:R"
        elif not np.isfinite(entry) or not np.isfinite(stop) or entry <= stop:
            status = "🟡 FUNDED WATCH · RISK PLAN"
        else:
            status = "🟢 FUNDED READY"

        plan = funded_position_plan(entry, stop)
        funded_status.append(status)
        positions.append(round(plan[1], 2) if np.isfinite(plan[1]) else np.nan)
        risks.append(round(plan[2], 2) if np.isfinite(plan[2]) else np.nan)

    output["Funded status"] = funded_status
    output["Position $"] = positions
    output["Risk $"] = risks

    return output


render_module_header(
    "Kraken Funded",
    "💼",
    "Apply the existing CL Signal Trade rules only to assets available in your Kraken Funded universe.",
)

st.info(
    "This is a restricted universe, not a new strategy. The normal Stock Trade and Crypto Swing rulebooks still decide "
    "whether a setup is valid. The Funded overlay then asks whether that valid setup is suitable for a challenge with "
    "very little drawdown room."
)

m1, m2, m3, m4 = st.columns(4)
m1.metric("Starting balance", "$1,000")
m2.metric("Pass target", "$1,120", "+12%")
m3.metric("Failure level", "$970", "-3%")
m4.metric("Standard risk / trade", "$4", "0.4%")

with st.expander("Kraken Funded overlay rules", expanded=False):
    st.markdown(
        """
        **Core CL Signal rules stay unchanged.** The Funded layer is deliberately more selective:

        - Normal CL Signal **READY / BUY** is required before a Funded READY is possible.
        - Minimum Funded reward/risk: **2.5:1**. A normal 2.0–2.49R setup remains WATCH for the challenge.
        - Standard planned loss: **$4 per trade**; never intentionally exceed **$5**.
        - Maximum combined planned open risk: **$10** so most of the $30 failure buffer remains unused.
        - Position size is calculated from **entry → invalidation/stop**, capped at **35% of the $1,000 account**.
        - Crypto marked **EXTENDED / TOO LATE** is not eligible for Funded READY.
        - There is **no need to force trades** because the challenge has no time limit.
        """
    )

stock_tab, crypto_tab = st.tabs(["📈 Stocks", "⚡ Crypto"])

with stock_tab:
    stocks = load_funded_stock_rows()

    ready_count = int(stocks["CL Signal"].eq("🟢 READY").sum())
    watch_count = int(stocks["CL Signal"].eq("🟡 WATCH").sum())
    no_setup_count = int(stocks["CL Signal"].eq("⚪ NO CURRENT SETUP").sum())

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Funded stocks", len(KRAKEN_FUNDED_STOCKS))
    c2.metric("Ready", ready_count)
    c3.metric("Watch", watch_count)
    c4.metric("No current setup", no_setup_count)

    stock_filter = st.segmented_control(
        "Show",
        ["All", "Ready", "Watch", "No current setup"],
        default="All",
        key="funded_stock_filter",
    )
    shown = stocks.copy()
    if stock_filter == "Ready":
        shown = shown[shown["CL Signal"] == "🟢 READY"]
    elif stock_filter == "Watch":
        shown = shown[shown["CL Signal"] == "🟡 WATCH"]
    elif stock_filter == "No current setup":
        shown = shown[shown["CL Signal"] == "⚪ NO CURRENT SETUP"]

    stock_cols = [
        "CL Signal", "Ticker", "MACD", "RSI", "Liquidity",
        "Entry", "Stop", "Target", "R:R",
    ]
    st.dataframe(
        shown[[column for column in stock_cols if column in shown.columns]],
        hide_index=True,
        use_container_width=True,
    )
    st.caption(
        "READY still requires the same confirmed Stock Trade setup. WATCH means the setup is developing. "
        "No current setup means the asset is allowed by Kraken Funded but is not currently present in the approved Trade shortlist."
    )

with crypto_tab:
    crypto = load_funded_crypto_rows()

    ready_count = int(crypto["CL Signal"].eq("🟢 READY / BUY").sum())
    watch_count = int(crypto["CL Signal"].eq("🟡 WATCH").sum())
    pass_count = int(crypto["CL Signal"].eq("🔴 PASS").sum())

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Funded coins", len(KRAKEN_FUNDED_CRYPTO))
    c2.metric("Ready / Buy", ready_count)
    c3.metric("Watch", watch_count)
    c4.metric("Pass", pass_count)

    crypto_filter = st.segmented_control(
        "Show",
        ["All", "Ready / Buy", "Watch", "Pass"],
        default="All",
        key="funded_crypto_filter",
    )
    shown = crypto.copy()
    if crypto_filter == "Ready / Buy":
        shown = shown[shown["CL Signal"] == "🟢 READY / BUY"]
    elif crypto_filter == "Watch":
        shown = shown[shown["CL Signal"] == "🟡 WATCH"]
    elif crypto_filter == "Pass":
        shown = shown[shown["CL Signal"] == "🔴 PASS"]

    crypto_cols = [
        "CL Signal", "Coin", "Score", "Entry timing", "RSI",
        "To resistance %", "R:R", "Execution",
    ]
    st.dataframe(
        shown[[column for column in crypto_cols if column in shown.columns]],
        hide_index=True,
        use_container_width=True,
    )
    st.caption(
        "The funded universe does not override the normal crypto Swing gates: pre-breakout structure, timing, "
        "execution liquidity, invalidation and risk/reward still have to pass."
    )
