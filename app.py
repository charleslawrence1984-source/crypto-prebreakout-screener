from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import ccxt.async_support as ccxt
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st


st.set_page_config(page_title="Pre-Breakout Crypto Screener", page_icon="⚡", layout="wide")

STABLE_BASES = {
    "USDT", "USDC", "DAI", "FDUSD", "TUSD", "USDE", "PYUSD", "EURC", "USD1",
    "BUSD", "USDP", "GUSD", "LUSD", "FRAX", "EUR", "GBP",
}
LEVERAGED_SUFFIXES = ("UP", "DOWN", "BULL", "BEAR", "2L", "2S", "3L", "3S", "5L", "5S")

EXCHANGES = {
    "Binance": "binance",
    "Bybit": "bybit",
    "OKX": "okx",
}


@dataclass
class ScreenerConfig:
    exchange_id: str = "okx"
    quote: str = "USDT"
    universe_size: int = 50
    min_quote_volume: float = 5_000_000
    resistance_lookback: int = 30
    near_resistance_min_pct: float = 0.15
    near_resistance_max_pct: float = 5.0
    score_threshold: int = 75
    too_late_pct: float = 2.0
    max_rsi: float = 69.0
    concurrency: int = 5


def _safe_float(x, default=np.nan):
    try:
        v = float(x)
        return v if math.isfinite(v) else default
    except Exception:
        return default


def ohlcv_to_df(rows: list) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
    if df.empty:
        return df
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.dropna().reset_index(drop=True)


def ema(s: pd.Series, span: int) -> pd.Series:
    return s.ewm(span=span, adjust=False).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    out = 100 - (100 / (1 + rs))
    return out.fillna(50)


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def obv(df: pd.DataFrame) -> pd.Series:
    direction = np.sign(df["close"].diff()).fillna(0)
    return (direction * df["volume"]).cumsum()


def lin_slope(values: pd.Series) -> float:
    y = pd.to_numeric(values, errors="coerce").dropna().to_numpy(dtype=float)
    if len(y) < 3:
        return 0.0
    x = np.arange(len(y), dtype=float)
    slope = np.polyfit(x, y, 1)[0]
    denom = np.nanmean(np.abs(y))
    return float(slope / denom) if denom else 0.0


def clamp_score(v: float, lo: float = 0, hi: float = 1) -> float:
    return float(max(lo, min(hi, v)))


def score_setup(df4h: pd.DataFrame, dfd: pd.DataFrame, btc4h: pd.DataFrame, cfg: ScreenerConfig) -> Dict:
    if len(df4h) < max(cfg.resistance_lookback + 25, 70) or len(dfd) < 35 or len(btc4h) < 30:
        return {"eligible": False, "reason": "Not enough history"}

    x = df4h.copy()
    x["rsi"] = rsi(x["close"])
    x["ema20"] = ema(x["close"], 20)
    x["ema50"] = ema(x["close"], 50)
    x["atr"] = atr(x)
    x["obv"] = obv(x)
    macd = ema(x["close"], 12) - ema(x["close"], 26)
    signal = ema(macd, 9)
    x["macd_hist"] = macd - signal

    # Exclude the current candle from resistance discovery so a breakout candle cannot define its own resistance.
    hist = x.iloc[-cfg.resistance_lookback - 1 : -1]
    recent = x.iloc[-12:]
    previous = x.iloc[-24:-12]
    price = float(x["close"].iloc[-1])
    resistance = float(hist["high"].max())
    if not price or not resistance:
        return {"eligible": False, "reason": "Bad price data"}

    distance_pct = (resistance - price) / price * 100
    breakout_pct = (price - resistance) / resistance * 100

    if breakout_pct > cfg.too_late_pct:
        return {"eligible": False, "reason": "Too late / already broken out", "price": price, "resistance": resistance}
    if distance_pct < -0.05:
        return {"eligible": False, "reason": "Already above resistance", "price": price, "resistance": resistance}
    if distance_pct > cfg.near_resistance_max_pct:
        return {"eligible": False, "reason": "Too far below resistance", "price": price, "resistance": resistance}

    # 1) Price structure: higher lows + repeated resistance tests + EMA structure (20 pts)
    lows_slope = lin_slope(recent["low"])
    higher_lows_component = clamp_score((lows_slope + 0.001) / 0.004)
    test_band = resistance * 0.015
    resistance_tests = int(((hist["high"] >= resistance - test_band) & (hist["high"] <= resistance * 1.001)).sum())
    test_component = clamp_score((resistance_tests - 1) / 3)
    ema_component = 1.0 if price > x["ema20"].iloc[-1] > x["ema50"].iloc[-1] else (0.55 if price > x["ema20"].iloc[-1] else 0.15)
    structure_score = 8 * higher_lows_component + 6 * test_component + 6 * ema_component

    # 2) Compression: ATR and range tightening (15 pts)
    atr_now = float(x["atr"].iloc[-5:].mean())
    atr_base = float(x["atr"].iloc[-40:-10].median())
    atr_ratio = atr_now / atr_base if atr_base else 1.0
    atr_component = clamp_score((1.15 - atr_ratio) / 0.45)
    recent_range = (recent["high"].max() - recent["low"].min()) / price
    prev_price = float(previous["close"].iloc[-1]) if len(previous) else price
    prev_range = (previous["high"].max() - previous["low"].min()) / prev_price if len(previous) and prev_price else recent_range
    range_ratio = recent_range / prev_range if prev_range else 1.0
    range_component = clamp_score((1.15 - range_ratio) / 0.55)
    compression_score = 8 * atr_component + 7 * range_component

    # 3) Volume contraction during the coil (15 pts)
    vol5 = float(x["volume"].iloc[-5:].mean())
    vol20 = float(x["volume"].iloc[-20:].mean())
    vol_ratio = vol5 / vol20 if vol20 else 1.0
    vol_contract_component = clamp_score((1.15 - vol_ratio) / 0.5)
    red_mask = x["close"].diff().iloc[-12:] < 0
    green_mask = ~red_mask
    red_vol = float(x["volume"].iloc[-12:][red_mask].mean()) if red_mask.any() else vol20
    green_vol = float(x["volume"].iloc[-12:][green_mask].mean()) if green_mask.any() else vol20
    buy_pressure_component = clamp_score((green_vol / red_vol - 0.8) / 0.8) if red_vol else 0.5
    volume_score = 10 * vol_contract_component + 5 * buy_pressure_component

    # 4) Relative strength vs BTC, measured over 12 and 24 4h bars (15 pts)
    n = min(len(x), len(btc4h))
    coin = x["close"].iloc[-n:].reset_index(drop=True)
    btc = btc4h["close"].iloc[-n:].reset_index(drop=True)
    c12 = coin.iloc[-1] / coin.iloc[-13] - 1 if len(coin) >= 13 else 0
    b12 = btc.iloc[-1] / btc.iloc[-13] - 1 if len(btc) >= 13 else 0
    c24 = coin.iloc[-1] / coin.iloc[-25] - 1 if len(coin) >= 25 else 0
    b24 = btc.iloc[-1] / btc.iloc[-25] - 1 if len(btc) >= 25 else 0
    rs12 = c12 - b12
    rs24 = c24 - b24
    rs_component = clamp_score(((0.65 * rs12 + 0.35 * rs24) + 0.02) / 0.10)
    rs_score = 15 * rs_component

    # 5) Momentum: RSI in the 50-65 zone + improving MACD histogram (10 pts)
    rsi_now = float(x["rsi"].iloc[-1])
    if rsi_now > cfg.max_rsi:
        rsi_component = max(0, 1 - (rsi_now - cfg.max_rsi) / 10)
    elif 52 <= rsi_now <= 64:
        rsi_component = 1.0
    elif 48 <= rsi_now < 52:
        rsi_component = 0.65
    elif 64 < rsi_now <= cfg.max_rsi:
        rsi_component = 0.75
    else:
        rsi_component = 0.25
    mh = x["macd_hist"]
    macd_rising = float(mh.iloc[-1] - mh.iloc[-4])
    macd_scale = max(abs(float(mh.iloc[-10:].std())), price * 1e-5)
    macd_component = clamp_score(0.5 + macd_rising / (3 * macd_scale))
    momentum_score = 6 * rsi_component + 4 * macd_component

    # 6) Accumulation proxy: rising OBV (5 pts)
    obv_slope = lin_slope(x["obv"].iloc[-20:])
    obv_component = clamp_score((obv_slope + 0.005) / 0.02)
    obv_score = 5 * obv_component

    # 7) Daily context: positive but not extended (5 pts)
    d = dfd.copy()
    d["ema20"] = ema(d["close"], 20)
    d["rsi"] = rsi(d["close"])
    daily_price = float(d["close"].iloc[-1])
    daily_ema = float(d["ema20"].iloc[-1])
    daily_rsi = float(d["rsi"].iloc[-1])
    if daily_price >= daily_ema and daily_rsi <= 70:
        daily_component = 1.0
    elif daily_price >= daily_ema:
        daily_component = 0.6
    else:
        daily_component = 0.25
    daily_score = 5 * daily_component

    # 8) Entry quality: near resistance but not touching it, with nearby invalidation (10 pts)
    ideal_mid = (cfg.near_resistance_min_pct + min(cfg.near_resistance_max_pct, 3.5)) / 2
    distance_component = clamp_score(1 - abs(distance_pct - ideal_mid) / max(ideal_mid, 1.0))
    swing_low = float(x["low"].iloc[-12:].min())
    invalidation = swing_low * 0.995
    risk_pct = max((price - invalidation) / price * 100, 0.01)
    # Estimate first objective using range height; cap to avoid fantasy targets.
    base_low = float(hist["low"].min())
    pattern_height_pct = max((resistance - base_low) / resistance * 100, 0)
    projected_target = resistance * (1 + min(pattern_height_pct, 25) / 100)
    reward_pct = max((projected_target - price) / price * 100, 0)
    rr = reward_pct / risk_pct if risk_pct else 0
    rr_component = clamp_score((rr - 1.0) / 2.5)
    entry_score = 5 * distance_component + 5 * rr_component

    total = structure_score + compression_score + volume_score + rs_score + momentum_score + obv_score + daily_score + entry_score
    total = round(float(max(0, min(100, total))), 1)

    # Require the fundamental pre-breakout shape, not just a high aggregate score.
    eligible = (
        cfg.near_resistance_min_pct <= distance_pct <= cfg.near_resistance_max_pct
        and resistance_tests >= 2
        and rsi_now <= cfg.max_rsi + 3
        and lows_slope > -0.0015
    )

    raw_entry_low = max(invalidation * 1.01, price * 0.985)
    raw_entry_high = min(resistance * 0.998, price * 1.01)
    entry_low = min(raw_entry_low, raw_entry_high * 0.999)
    entry_high = max(raw_entry_high, entry_low * 1.001)

    return {
        "eligible": bool(eligible),
        "reason": "Pre-breakout candidate" if eligible else "Shape filter not met",
        "score": total,
        "price": price,
        "resistance": resistance,
        "distance_pct": round(distance_pct, 2),
        "resistance_tests": resistance_tests,
        "rsi": round(rsi_now, 1),
        "atr_ratio": round(atr_ratio, 2),
        "volume_ratio": round(vol_ratio, 2),
        "rs_vs_btc_pct": round(rs12 * 100, 2),
        "risk_reward": round(rr, 2),
        "entry_low": entry_low,
        "entry_high": entry_high,
        "invalidation": invalidation,
        "target_1": resistance * 1.05,
        "target_2": resistance * 1.10,
        "projected_target": projected_target,
        "components": {
            "Structure": round(structure_score, 1),
            "Compression": round(compression_score, 1),
            "Volume": round(volume_score, 1),
            "RS vs BTC": round(rs_score, 1),
            "Momentum": round(momentum_score, 1),
            "OBV": round(obv_score, 1),
            "Daily context": round(daily_score, 1),
            "Entry / R:R": round(entry_score, 1),
        },
    }


async def fetch_market_universe(cfg: ScreenerConfig) -> Tuple[List[Tuple[str, float]], Dict[str, dict], dict]:
    cls = getattr(ccxt, cfg.exchange_id)
    exchange = cls({"enableRateLimit": True, "options": {"defaultType": "spot"}})
    try:
        markets = await exchange.load_markets()
        if not exchange.has.get("fetchTickers"):
            raise RuntimeError(f"{cfg.exchange_id} does not expose batch tickers through CCXT")
        tickers = await exchange.fetch_tickers()

        candidates = []
        for symbol, market in markets.items():
            if not market.get("spot") or market.get("active") is False:
                continue
            if market.get("quote") != cfg.quote:
                continue
            base = str(market.get("base", "")).upper()
            if base in STABLE_BASES or any(base.endswith(sfx) and len(base) > len(sfx) + 2 for sfx in LEVERAGED_SUFFIXES):
                continue
            t = tickers.get(symbol) or {}
            qv = _safe_float(t.get("quoteVolume"), np.nan)
            last = _safe_float(t.get("last"), np.nan)
            bv = _safe_float(t.get("baseVolume"), np.nan)
            if np.isnan(qv) and not np.isnan(last) and not np.isnan(bv):
                qv = last * bv
            if np.isnan(qv) or qv < cfg.min_quote_volume:
                continue
            candidates.append((symbol, qv))
        candidates.sort(key=lambda z: z[1], reverse=True)
        return candidates[: cfg.universe_size], markets, tickers
    finally:
        await exchange.close()


async def scan_exchange(cfg: ScreenerConfig) -> Tuple[pd.DataFrame, Dict[str, Dict[str, pd.DataFrame]], List[str]]:
    universe, _, _ = await fetch_market_universe(cfg)
    cls = getattr(ccxt, cfg.exchange_id)
    exchange = cls({"enableRateLimit": True, "options": {"defaultType": "spot"}})
    errors: List[str] = []
    raw: Dict[str, Dict[str, pd.DataFrame]] = {}
    sem = asyncio.Semaphore(cfg.concurrency)

    try:
        await exchange.load_markets()
        btc_symbol = f"BTC/{cfg.quote}"
        if btc_symbol not in exchange.markets:
            raise RuntimeError(f"{btc_symbol} is not available on {cfg.exchange_id}")
        btc4h = ohlcv_to_df(await exchange.fetch_ohlcv(btc_symbol, timeframe="4h", limit=180))

        async def one(symbol: str, qv: float):
            async with sem:
                try:
                    rows4, rowsd = await asyncio.gather(
                        exchange.fetch_ohlcv(symbol, timeframe="4h", limit=180),
                        exchange.fetch_ohlcv(symbol, timeframe="1d", limit=90),
                    )
                    df4 = ohlcv_to_df(rows4)
                    dfd = ohlcv_to_df(rowsd)
                    result = score_setup(df4, dfd, btc4h, cfg)
                    raw[symbol] = {"4h": df4, "1d": dfd}
                    result["symbol"] = symbol
                    result["quote_volume_24h"] = qv
                    return result
                except Exception as e:
                    errors.append(f"{symbol}: {type(e).__name__}: {e}")
                    return None

        results = await asyncio.gather(*(one(symbol, qv) for symbol, qv in universe))
        rows = [r for r in results if r and r.get("eligible")]
        if not rows:
            return pd.DataFrame(), raw, errors

        rows.sort(key=lambda r: r.get("score", 0), reverse=True)
        display = pd.DataFrame([
            {
                "Coin": r["symbol"].split("/")[0],
                "Symbol": r["symbol"],
                "Score": r["score"],
                "Price": r["price"],
                "To resistance %": r["distance_pct"],
                "Tests": r["resistance_tests"],
                "RSI": r["rsi"],
                "ATR ratio": r["atr_ratio"],
                "Vol ratio": r["volume_ratio"],
                "RS vs BTC %": r["rs_vs_btc_pct"],
                "R:R": r["risk_reward"],
                "Entry low": r["entry_low"],
                "Entry high": r["entry_high"],
                "Breakout": r["resistance"],
                "Invalidation": r["invalidation"],
                "Target +5%": r["target_1"],
                "Target +10%": r["target_2"],
                "24h quote vol": r["quote_volume_24h"],
                "_components": r["components"],
            }
            for r in rows
        ])
        return display, raw, errors
    finally:
        await exchange.close()


def fmt_price(v: float) -> str:
    v = float(v)
    if v >= 1000:
        return f"{v:,.2f}"
    if v >= 1:
        return f"{v:,.4f}"
    if v >= 0.01:
        return f"{v:.5f}"
    return f"{v:.8f}"


def make_chart(df: pd.DataFrame, row: pd.Series) -> go.Figure:
    d = df.tail(70)
    fig = go.Figure()
    fig.add_trace(go.Candlestick(
        x=d["timestamp"], open=d["open"], high=d["high"], low=d["low"], close=d["close"], name="4h"
    ))
    fig.add_hline(y=float(row["Breakout"]), line_dash="dash", annotation_text="Breakout / resistance")
    fig.add_hline(y=float(row["Invalidation"]), line_dash="dot", annotation_text="Invalidation")
    fig.add_hrect(y0=float(row["Entry low"]), y1=float(row["Entry high"]), opacity=0.12, line_width=0, annotation_text="Entry zone")
    fig.update_layout(height=480, margin=dict(l=10, r=10, t=35, b=10), xaxis_rangeslider_visible=False)
    return fig


async def fetch_backtest_data(exchange_id: str, symbol: str, quote: str) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    cls = getattr(ccxt, exchange_id)
    exchange = cls({"enableRateLimit": True, "options": {"defaultType": "spot"}})
    try:
        await exchange.load_markets()
        coin4 = ohlcv_to_df(await exchange.fetch_ohlcv(symbol, timeframe="4h", limit=500))
        coind = ohlcv_to_df(await exchange.fetch_ohlcv(symbol, timeframe="1d", limit=180))
        btc4 = ohlcv_to_df(await exchange.fetch_ohlcv(f"BTC/{quote}", timeframe="4h", limit=500))
        return coin4, coind, btc4
    finally:
        await exchange.close()


def historical_backtest(coin4: pd.DataFrame, coind: pd.DataFrame, btc4: pd.DataFrame, cfg: ScreenerConfig, threshold: int, horizon: int) -> pd.DataFrame:
    records = []
    start = max(cfg.resistance_lookback + 30, 80)
    # Use only completed historical bars. We approximate daily context by data available up to each timestamp.
    for i in range(start, len(coin4) - horizon):
        cut4 = coin4.iloc[: i + 1].copy()
        ts = cut4["timestamp"].iloc[-1]
        cutd = coind[coind["timestamp"] <= ts].copy()
        cutbtc = btc4[btc4["timestamp"] <= ts].copy()
        if len(cutd) < 35 or len(cutbtc) < 30:
            continue
        result = score_setup(cut4, cutd, cutbtc, cfg)
        if not result.get("eligible") or result.get("score", 0) < threshold:
            continue
        entry = result["price"]
        invalidation = result["invalidation"]
        future = coin4.iloc[i + 1 : i + 1 + horizon]
        max_ret = (future["high"].max() / entry - 1) * 100
        min_ret = (future["low"].min() / entry - 1) * 100
        stop_hit = bool((future["low"] <= invalidation).any())
        records.append({
            "Time": ts,
            "Score": result["score"],
            "Entry": entry,
            "Max return %": max_ret,
            "Max adverse %": min_ret,
            "Hit +5%": max_ret >= 5,
            "Hit +10%": max_ret >= 10,
            "Hit +20%": max_ret >= 20,
            "Invalidation touched": stop_hit,
        })
    return pd.DataFrame(records)


# ---------------- UI ----------------
st.title("⚡ Pre-Breakout Crypto Screener")
st.caption("Built to find compression before expansion — and reject coins that have already run.")

with st.sidebar:
    st.header("Scan settings")
    exchange_name = st.selectbox("Exchange", list(EXCHANGES.keys()), index=2)
    universe_size = st.select_slider("Top liquid coins to scan", options=[25, 50, 75, 100, 150], value=50)
    min_vol_m = st.number_input("Minimum 24h quote volume ($m)", min_value=1.0, max_value=500.0, value=5.0, step=1.0)
    threshold = st.slider("Flag score", 60, 95, 80, 1)
    max_distance = st.slider("Maximum distance below resistance (%)", 1.0, 8.0, 5.0, 0.25)
    max_rsi = st.slider("Maximum RSI", 60, 75, 69, 1)
    refresh_minutes = st.selectbox("Auto-refresh", [2, 5, 10, 15, 30], index=1, format_func=lambda x: f"Every {x} min")
    sound_alerts = st.checkbox("Sound alert for new flags", value=False, help="Browser autoplay usually works after you have interacted with the page once.")
    st.divider()
    st.caption("No exchange API key is required. This dashboard only reads public market data.")

cfg = ScreenerConfig(
    exchange_id=EXCHANGES[exchange_name],
    universe_size=universe_size,
    min_quote_volume=min_vol_m * 1_000_000,
    score_threshold=threshold,
    near_resistance_max_pct=max_distance,
    max_rsi=float(max_rsi),
)

if "scan_df" not in st.session_state:
    st.session_state.scan_df = pd.DataFrame()
if "raw_data" not in st.session_state:
    st.session_state.raw_data = {}
if "previous_flags" not in st.session_state:
    st.session_state.previous_flags = set()
if "last_scan" not in st.session_state:
    st.session_state.last_scan = None

manual_col, info_col = st.columns([1, 4])
with manual_col:
    manual_scan = st.button("Run scan now", type="primary", use_container_width=True)
with info_col:
    st.info("A flag means the setup matches the pre-breakout rules. It is not a prediction or a guarantee of a pump.")

run_every = f"{refresh_minutes}m"

@st.fragment(run_every=run_every)
def live_scan():
    should_scan = manual_scan or st.session_state.scan_df.empty
    # Fragment auto-reruns should scan every time; on initial full run it also scans.
    should_scan = True
    if should_scan:
        status = st.status(f"Scanning top {cfg.universe_size} liquid {cfg.quote} spot markets on {exchange_name}…", expanded=False)
        try:
            df, raw, errors = asyncio.run(scan_exchange(cfg))
            st.session_state.scan_df = df
            st.session_state.raw_data = raw
            st.session_state.last_scan = datetime.now(timezone.utc)
            status.update(label=f"Scan complete — {len(df)} pre-breakout candidates found", state="complete")
            if errors:
                with st.expander(f"{len(errors)} market-data warnings"):
                    st.code("\n".join(errors[:25]))
        except Exception as e:
            status.update(label="Scan failed", state="error")
            st.error(f"{type(e).__name__}: {e}")
            return

    df = st.session_state.scan_df.copy()
    if st.session_state.last_scan:
        st.caption("Last scan: " + st.session_state.last_scan.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z"))

    if df.empty:
        st.warning("No coins currently meet the pre-breakout shape filter. That is a valid result — don't force a trade.")
        return

    flagged = df[df["Score"] >= cfg.score_threshold].copy()
    current_flags = set(flagged["Symbol"].tolist())
    new_flags = current_flags - st.session_state.previous_flags
    if new_flags:
        st.toast("New high-score setup: " + ", ".join(sorted(s.split('/')[0] for s in new_flags)))
        if sound_alerts:
            sr = 16000
            t = np.linspace(0, 0.28, int(sr * 0.28), endpoint=False)
            tone = (0.20 * np.sin(2 * np.pi * 880 * t)).astype(np.float32)
            st.audio(tone, sample_rate=sr, autoplay=True)
    st.session_state.previous_flags = current_flags

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Candidates", len(df))
    c2.metric(f"Flags ≥ {cfg.score_threshold}", len(flagged))
    c3.metric("Best score", f"{df['Score'].max():.1f}/100")
    c4.metric("Best setup", df.iloc[0]["Coin"])

    shown = df[df["Score"] >= cfg.score_threshold].copy()
    if shown.empty:
        st.warning(f"Candidates exist, but none score {cfg.score_threshold}+ right now.")
        shown = df.head(10)

    display_cols = [
        "Coin", "Score", "Price", "To resistance %", "Tests", "RSI", "ATR ratio",
        "Vol ratio", "RS vs BTC %", "R:R", "Entry low", "Entry high", "Breakout", "Invalidation"
    ]
    st.dataframe(
        shown[display_cols],
        use_container_width=True,
        hide_index=True,
        column_config={
            "Score": st.column_config.ProgressColumn("Score", min_value=0, max_value=100, format="%.1f"),
            "To resistance %": st.column_config.NumberColumn(format="%.2f%%"),
            "RS vs BTC %": st.column_config.NumberColumn(format="%.2f%%"),
            "R:R": st.column_config.NumberColumn(format="%.2f"),
            "Price": st.column_config.NumberColumn(format="%.8g"),
            "Entry low": st.column_config.NumberColumn(format="%.8g"),
            "Entry high": st.column_config.NumberColumn(format="%.8g"),
            "Breakout": st.column_config.NumberColumn(format="%.8g"),
            "Invalidation": st.column_config.NumberColumn(format="%.8g"),
        },
    )

live_scan()

st.divider()
st.subheader("Inspect a setup")
scan_df = st.session_state.scan_df
if not scan_df.empty:
    symbols = scan_df["Symbol"].tolist()
    selected = st.selectbox("Candidate", symbols, format_func=lambda s: f"{s.split('/')[0]} — {float(scan_df.loc[scan_df['Symbol']==s, 'Score'].iloc[0]):.1f}/100")
    row = scan_df.loc[scan_df["Symbol"] == selected].iloc[0]
    raw = st.session_state.raw_data.get(selected, {})
    if "4h" in raw:
        st.plotly_chart(make_chart(raw["4h"], row), use_container_width=True)

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Entry zone", f"{fmt_price(row['Entry low'])} – {fmt_price(row['Entry high'])}")
    m2.metric("Breakout level", fmt_price(row["Breakout"]))
    m3.metric("Invalidation", fmt_price(row["Invalidation"]))
    m4.metric("Risk / reward", f"{row['R:R']:.2f}:1")

    comps = row["_components"]
    comp_df = pd.DataFrame({"Factor": list(comps.keys()), "Points": list(comps.values())})
    st.bar_chart(comp_df.set_index("Factor"), horizontal=True)
else:
    st.caption("Run a scan to inspect individual setups.")

st.divider()
st.subheader("Historical sanity check")
st.caption("This is a simple event study, not a full execution simulator. It checks what happened after past pre-breakout flags.")

if not scan_df.empty:
    bc1, bc2, bc3 = st.columns(3)
    with bc1:
        bt_symbol = st.selectbox("Coin to backtest", scan_df["Symbol"].tolist(), key="bt_symbol")
    with bc2:
        bt_threshold = st.slider("Historical score threshold", 65, 95, cfg.score_threshold, 1)
    with bc3:
        horizon = st.selectbox("Forward window", [6, 12, 18, 24, 36], index=3, format_func=lambda x: f"{x} × 4h bars ({x*4}h)")

    if st.button("Run backtest"):
        with st.spinner("Fetching historical candles and replaying the setup rules…"):
            try:
                coin4, coind, btc4 = asyncio.run(fetch_backtest_data(cfg.exchange_id, bt_symbol, cfg.quote))
                bt = historical_backtest(coin4, coind, btc4, cfg, bt_threshold, horizon)
                if bt.empty:
                    st.warning("No historical signals met those settings in the available candle history.")
                else:
                    b1, b2, b3, b4, b5 = st.columns(5)
                    b1.metric("Signals", len(bt))
                    b2.metric("Hit +5%", f"{bt['Hit +5%'].mean()*100:.1f}%")
                    b3.metric("Hit +10%", f"{bt['Hit +10%'].mean()*100:.1f}%")
                    b4.metric("Hit +20%", f"{bt['Hit +20%'].mean()*100:.1f}%")
                    b5.metric("Invalidation touched", f"{bt['Invalidation touched'].mean()*100:.1f}%")
                    st.dataframe(bt.sort_values("Time", ascending=False), use_container_width=True, hide_index=True)
            except Exception as e:
                st.error(f"Backtest failed: {type(e).__name__}: {e}")

with st.expander("How the 100-point score works"):
    st.markdown(
        """
- **20 pts — Structure:** higher lows, repeated resistance tests, 4h EMA structure.
- **15 pts — Compression:** ATR contraction and a tightening trading range.
- **15 pts — Volume:** volume dries up during the coil, with preference for stronger volume on up-bars.
- **15 pts — Relative strength:** coin return versus BTC over recent 4h windows.
- **10 pts — Momentum:** RSI in a constructive zone plus improving MACD histogram.
- **5 pts — OBV:** accumulation proxy via rising on-balance volume.
- **5 pts — Daily context:** daily trend constructive without being extremely stretched.
- **10 pts — Entry / R:R:** distance to resistance and projected reward versus invalidation risk.

The screener also applies a **hard shape filter**: it must still be below resistance, close enough to matter, have at least two resistance tests, and not be materially overbought. Coins already above resistance are rejected rather than rewarded.
        """
    )

st.caption("Trading tool only — not financial advice. Crypto can gap through technical levels; always size risk independently of the score.")