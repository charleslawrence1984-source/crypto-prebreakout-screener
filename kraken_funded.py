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

    return output


render_module_header(
    "Kraken Funded",
    "💼",
    "Apply the existing CL Signal Trade rules only to assets available in your Kraken Funded universe.",
)

st.info(
    "This is a restricted universe, not a new strategy. Stocks use the existing CL Signal Stock Trade rules; "
    "crypto uses the existing CL Signal Swing rules. A funded asset only becomes actionable when the same rules pass."
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
