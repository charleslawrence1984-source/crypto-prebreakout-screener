from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional

import numpy as np
import pandas as pd


@dataclass
class ScreenerConfig:
    exchange_id: str = "okx"
    quote: str = "USDT"
    universe_size: int = 50
    min_quote_volume: float = 5_000_000
    resistance_lookback: int = 30
    near_resistance_min_pct: float = 0.15
    near_resistance_max_pct: float = 5.0
    score_threshold: int = 80
    too_late_pct: float = 2.0
    max_rsi: float = 69.0
    min_gross_profit_pct: float = 30.0
    concurrency: int = 5


def _safe_float(x, default=np.nan):
    try:
        v = float(x)
        return v if math.isfinite(v) else default
    except Exception:
        return default


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



def classify_trend(df4h: pd.DataFrame, dfd: pd.DataFrame) -> Dict:
    """
    Classify trend as UPTREND / SIDEWAYS / DOWNTREND.

    Daily structure sets the primary direction. The 4h structure is used as
    confirmation so a short-term bounce or pullback does not redefine the
    broader trend on its own.
    """
    if dfd is None or len(dfd) < 60 or df4h is None or len(df4h) < 50:
        return {
            "trend": "UNAVAILABLE",
            "daily": "UNAVAILABLE",
            "four_hour": "UNAVAILABLE",
            "detail": "Not enough history",
        }

    d = dfd.copy()
    h = df4h.copy()

    d["ema20"] = ema(d["close"], 20)
    d["ema50"] = ema(d["close"], 50)
    d["ema200"] = ema(d["close"], 200)
    h["ema20"] = ema(h["close"], 20)
    h["ema50"] = ema(h["close"], 50)

    d_price = float(d["close"].iloc[-1])
    d20 = float(d["ema20"].iloc[-1])
    d50 = float(d["ema50"].iloc[-1])
    d200 = float(d["ema200"].iloc[-1]) if len(d) >= 200 else np.nan
    d50_slope = lin_slope(d["ema50"].iloc[-12:])
    daily_spread_pct = abs(d20 - d50) / d_price * 100 if d_price else np.nan

    h_price = float(h["close"].iloc[-1])
    h20 = float(h["ema20"].iloc[-1])
    h50 = float(h["ema50"].iloc[-1])
    h50_slope = lin_slope(h["ema50"].iloc[-12:])
    fourh_spread_pct = abs(h20 - h50) / h_price * 100 if h_price else np.nan

    daily_up = d_price > d20 > d50 and d50_slope > 0
    daily_down = d_price < d20 < d50 and d50_slope < 0
    daily_flat = (
        (math.isfinite(daily_spread_pct) and daily_spread_pct <= 2.0)
        or abs(d50_slope) < 0.0006
    )

    fourh_up = h_price > h20 > h50 and h50_slope > 0
    fourh_down = h_price < h20 < h50 and h50_slope < 0
    fourh_flat = (
        (math.isfinite(fourh_spread_pct) and fourh_spread_pct <= 1.5)
        or abs(h50_slope) < 0.0008
    )

    daily_label = (
        "UPTREND" if daily_up
        else "DOWNTREND" if daily_down
        else "SIDEWAYS"
    )
    fourh_label = (
        "UPTREND" if fourh_up
        else "DOWNTREND" if fourh_down
        else "SIDEWAYS"
    )

    if daily_up and not fourh_down:
        trend = "UPTREND"
    elif daily_down and not fourh_up:
        trend = "DOWNTREND"
    elif daily_up and fourh_down:
        trend = "SIDEWAYS"
    elif daily_down and fourh_up:
        trend = "SIDEWAYS"
    elif daily_flat or fourh_flat:
        trend = "SIDEWAYS"
    else:
        trend = "SIDEWAYS"

    ema200_context = ""
    if math.isfinite(d200):
        ema200_context = (
            "above 200D EMA" if d_price >= d200 else "below 200D EMA"
        )

    detail = (
        f"Daily {daily_label}; 4h {fourh_label}; "
        f"daily EMA50 slope {d50_slope:+.4f}"
    )
    if ema200_context:
        detail += f"; {ema200_context}"

    return {
        "trend": trend,
        "daily": daily_label,
        "four_hour": fourh_label,
        "detail": detail,
    }



def period_return_pct(df: pd.DataFrame, days: int) -> float:
    if df is None or df.empty or "close" not in df.columns:
        return np.nan
    s = pd.to_numeric(df["close"], errors="coerce").dropna()
    if len(s) < 2:
        return np.nan
    lookback = min(days, len(s) - 1)
    old = float(s.iloc[-1 - lookback])
    new = float(s.iloc[-1])
    if old <= 0:
        return np.nan
    return (new / old - 1) * 100



def project_freshness(dfd: pd.DataFrame, dfw: Optional[pd.DataFrame] = None) -> Dict:
    """
    Practical freshness proxy based on available spot-price history on the selected
    exchange. This is not the project's true launch age, but it is useful for
    distinguishing newer listings from long-established assets without one extra
    API request per coin.
    """
    source = dfw if dfw is not None and not dfw.empty else dfd
    if source is None or source.empty or "timestamp" not in source.columns:
        return {
            "freshness": "UNKNOWN",
            "history_days": np.nan,
            "freshness_score": np.nan,
            "freshness_basis": "No exchange-history data",
        }

    ts = pd.to_datetime(source["timestamp"], errors="coerce", utc=True).dropna()
    if len(ts) < 2:
        return {
            "freshness": "UNKNOWN",
            "history_days": np.nan,
            "freshness_score": np.nan,
            "freshness_basis": "Insufficient exchange-history data",
        }

    history_days = max(0.0, (ts.iloc[-1] - ts.iloc[0]).total_seconds() / 86400)
    if history_days < 180:
        label, score = "NEW", 100.0
    elif history_days < 540:
        label, score = "RECENT", 80.0
    elif history_days < 1095:
        label, score = "MATURE", 55.0
    else:
        label, score = "LEGACY", 35.0

    return {
        "freshness": label,
        "history_days": round(history_days, 0),
        "freshness_score": score,
        "freshness_basis": "Available spot history on selected exchange",
    }



def trend_channel(df: pd.DataFrame, window: int = 80, high_col: str = "high", low_col: str = "low", close_col: str = "close") -> Dict:
    if df is None or len(df) < 30:
        return {"direction":"UNAVAILABLE","position":np.nan,"support":np.nan,"resistance":np.nan,"width_pct":np.nan,"rr":np.nan,"touches":0,"quality":"LOW","state":"NONE","slope_pct":np.nan}
    d = df.tail(min(window, len(df))).copy()
    for col in (high_col, low_col, close_col):
        d[col] = pd.to_numeric(d[col], errors="coerce")
    d = d.dropna(subset=[high_col, low_col, close_col])
    if len(d) < 30:
        return {"direction":"UNAVAILABLE","position":np.nan,"support":np.nan,"resistance":np.nan,"width_pct":np.nan,"rr":np.nan,"touches":0,"quality":"LOW","state":"NONE","slope_pct":np.nan}
    x = np.arange(len(d), dtype=float)
    y = d[close_col].to_numpy(dtype=float)
    slope, intercept = np.polyfit(x, y, 1)
    centre = intercept + slope * x
    upper = centre + float(np.quantile(d[high_col].to_numpy(dtype=float) - centre, 0.90))
    lower = centre + float(np.quantile(d[low_col].to_numpy(dtype=float) - centre, 0.10))
    support, resistance, price = float(lower[-1]), float(upper[-1]), float(y[-1])
    width = resistance - support
    if width <= 0 or price <= 0:
        return {"direction":"UNAVAILABLE","position":np.nan,"support":support,"resistance":resistance,"width_pct":np.nan,"rr":np.nan,"touches":0,"quality":"LOW","state":"NONE","slope_pct":np.nan}
    slope_pct = slope * max(len(d)-1,1) / max(float(centre[0]),1e-12) * 100
    direction = "RISING" if slope_pct >= 3 else "FALLING" if slope_pct <= -3 else "SIDEWAYS"
    position = (price - support) / width * 100
    tol = max(width * 0.08, price * 0.005)
    touches = int((np.abs(d[low_col].to_numpy(dtype=float)-lower) <= tol).sum() + (np.abs(d[high_col].to_numpy(dtype=float)-upper) <= tol).sum())
    ss_res = float(np.sum((y-centre)**2)); ss_tot = float(np.sum((y-np.mean(y))**2))
    r2 = max(0.0, 1-ss_res/ss_tot) if ss_tot > 0 else 0.0
    if direction == "SIDEWAYS":
        quality = "HIGH" if touches >= 6 else "MEDIUM" if touches >= 4 else "LOW"
    else:
        quality = "HIGH" if touches >= 6 and r2 >= 0.45 else "MEDIUM" if touches >= 4 and r2 >= 0.20 else "LOW"
    state = "ABOVE CHANNEL" if price > resistance + tol else "BELOW CHANNEL" if price < support - tol else "INSIDE"
    downside = max(price-support, price*0.001); upside = max(resistance-price,0.0)
    return {"direction":direction,"position":round(position,1),"support":support,"resistance":resistance,"width_pct":round(width/price*100,2),"rr":round(upside/downside,2),"touches":touches,"quality":quality,"state":state,"slope_pct":round(slope_pct,2),"lower_series":lower.tolist(),"upper_series":upper.tolist(),"start":len(df)-len(d)}



def latest_completed_4h_candle_signal(df4h: pd.DataFrame) -> Dict:
    """
    Detect a bearish red shooting star on the latest completed 4h candle.
    A live/incomplete candle is ignored so a temporary wick cannot create a false warning.
    """
    if df4h is None or df4h.empty or len(df4h) < 2:
        return {
            "candle_pattern": "UNAVAILABLE",
            "candle_caution": False,
            "candle_detail": "Not enough 4h candle history",
        }

    x = df4h.copy()
    timestamps = pd.to_datetime(x["timestamp"], errors="coerce", utc=True)
    now = pd.Timestamp.now(tz="UTC")
    completed_mask = timestamps + pd.Timedelta(hours=4) <= now
    completed = x.loc[completed_mask]

    if completed.empty:
        candle = x.iloc[-2]
    else:
        candle = completed.iloc[-1]

    o = float(candle["open"])
    h = float(candle["high"])
    l = float(candle["low"])
    close = float(candle["close"])
    candle_range = max(h - l, 0.0)

    if candle_range <= 0:
        return {
            "candle_pattern": "OTHER",
            "candle_caution": False,
            "candle_detail": "Flat completed 4h candle",
        }

    body = abs(close - o)
    upper_wick = h - max(o, close)
    lower_wick = min(o, close) - l
    red = close < o

    # Shooting-star geometry: small body near the low, long upper rejection wick,
    # little lower wick. Require the candle to close red for the caution rule.
    shooting_star = (
        red
        and body / candle_range <= 0.35
        and upper_wick >= max(body * 2.0, candle_range * 0.45)
        and lower_wick <= candle_range * 0.20
    )

    if shooting_star:
        return {
            "candle_pattern": "RED SHOOTING STAR",
            "candle_caution": True,
            "candle_detail": (
                f"Latest completed 4h candle rejected higher prices: "
                f"upper wick {upper_wick / candle_range * 100:.0f}% of range; red close."
            ),
        }

    return {
        "candle_pattern": "OTHER",
        "candle_caution": False,
        "candle_detail": "No red shooting-star warning on latest completed 4h candle",
    }



def bollinger_context(df: pd.DataFrame, close_col: str = "close", window: int = 20) -> Dict:
    if df is None or len(df) < max(window + 5, 30):
        return {
            "bb_mid": np.nan, "bb_upper": np.nan, "bb_lower": np.nan,
            "bb_width_pct": np.nan, "bb_position_pct": np.nan,
            "bb_width_percentile": np.nan, "bb_regime": "UNAVAILABLE",
        }
    d = df.copy()
    close = pd.to_numeric(d[close_col], errors="coerce")
    mid = close.rolling(window).mean()
    sd = close.rolling(window).std()
    upper = mid + 2 * sd
    lower = mid - 2 * sd
    width_pct_series = (upper - lower) / mid.replace(0, np.nan) * 100

    price = _safe_float(close.iloc[-1], np.nan)
    mid_now = _safe_float(mid.iloc[-1], np.nan)
    upper_now = _safe_float(upper.iloc[-1], np.nan)
    lower_now = _safe_float(lower.iloc[-1], np.nan)
    width_now = _safe_float(width_pct_series.iloc[-1], np.nan)

    if not all(math.isfinite(v) for v in [price, mid_now, upper_now, lower_now, width_now]):
        return {
            "bb_mid": mid_now, "bb_upper": upper_now, "bb_lower": lower_now,
            "bb_width_pct": width_now, "bb_position_pct": np.nan,
            "bb_width_percentile": np.nan, "bb_regime": "UNAVAILABLE",
        }

    band_range = upper_now - lower_now
    position = (price - lower_now) / band_range * 100 if band_range > 0 else np.nan
    hist_width = width_pct_series.dropna().tail(120)
    percentile = (
        float((hist_width <= width_now).mean() * 100)
        if len(hist_width) >= 20 else np.nan
    )

    recent = width_pct_series.dropna().tail(6)
    expanding = len(recent) >= 4 and recent.iloc[-1] > recent.iloc[0] * 1.12
    if math.isfinite(percentile) and percentile <= 20:
        regime = "SQUEEZE"
    elif expanding:
        regime = "EXPANDING"
    else:
        regime = "NORMAL"

    return {
        "bb_mid": mid_now,
        "bb_upper": upper_now,
        "bb_lower": lower_now,
        "bb_width_pct": round(width_now, 2),
        "bb_position_pct": round(float(position), 1) if math.isfinite(position) else np.nan,
        "bb_width_percentile": round(float(percentile), 1) if math.isfinite(percentile) else np.nan,
        "bb_regime": regime,
    }



def ascending_triangle_pattern(df4h: pd.DataFrame, window: int = 60) -> Dict:
    if df4h is None or len(df4h) < 36:
        return {
            "triangle_label": "NO TRIANGLE",
            "triangle_score": 0.0,
            "triangle_resistance": np.nan,
            "triangle_support_now": np.nan,
            "triangle_touches": 0,
            "triangle_flatness_pct": np.nan,
            "triangle_compression_pct": np.nan,
            "triangle_measured_target": np.nan,
            "triangle_detail": "Insufficient 4h history",
        }

    d = df4h.tail(min(window, len(df4h))).copy()
    for col in ("high", "low", "close", "volume"):
        d[col] = pd.to_numeric(d[col], errors="coerce")
    d = d.dropna(subset=["high", "low", "close", "volume"])
    if len(d) < 36:
        return {
            "triangle_label": "NO TRIANGLE",
            "triangle_score": 0.0,
            "triangle_resistance": np.nan,
            "triangle_support_now": np.nan,
            "triangle_touches": 0,
            "triangle_flatness_pct": np.nan,
            "triangle_compression_pct": np.nan,
            "triangle_measured_target": np.nan,
            "triangle_detail": "Insufficient clean 4h history",
        }

    x = np.arange(len(d), dtype=float)
    highs = d["high"].to_numpy(dtype=float)
    lows = d["low"].to_numpy(dtype=float)
    closes = d["close"].to_numpy(dtype=float)
    price = float(closes[-1])

    resistance = float(np.quantile(highs, 0.93))
    top_mask = highs >= resistance * 0.985
    top_x = x[top_mask]
    top_y = highs[top_mask]
    # Count separate resistance-test clusters rather than every adjacent candle.
    prior_hit = np.concatenate(([False], top_mask[:-1]))
    touches = int(np.sum(top_mask & ~prior_hit))

    if touches >= 2:
        top_slope, top_intercept = np.polyfit(top_x, top_y, 1)
        top_start = top_intercept
        top_end = top_intercept + top_slope * (len(d) - 1)
        flatness_pct = abs(top_end - top_start) / max(resistance, 1e-12) * 100
    else:
        top_slope = 0.0
        flatness_pct = 99.0

    # Rising support is based on lower quantile points so one wick does not define the triangle.
    low_cutoff = float(np.quantile(lows, 0.35))
    low_mask = lows <= low_cutoff
    low_x = x[low_mask]
    low_y = lows[low_mask]
    if len(low_x) >= 4:
        support_slope, support_intercept = np.polyfit(low_x, low_y, 1)
    else:
        support_slope, support_intercept = np.polyfit(x, lows, 1)

    support_start = float(support_intercept)
    support_now = float(support_intercept + support_slope * (len(d) - 1))
    start_gap = max(resistance - support_start, price * 0.001)
    end_gap = max(resistance - support_now, price * 0.001)
    compression_pct = (1 - end_gap / start_gap) * 100

    support_rise_pct = (
        (support_now / support_start - 1) * 100
        if support_start > 0 else -99.0
    )
    distance_to_resistance_pct = (resistance - price) / price * 100

    vol_recent = float(d["volume"].iloc[-10:].mean())
    vol_early = float(d["volume"].iloc[: max(10, len(d)//3)].mean())
    volume_ratio = vol_recent / vol_early if vol_early > 0 else 1.0

    flat_component = clamp_score((2.5 - flatness_pct) / 2.5)
    touch_component = clamp_score((touches - 1) / 3)
    rising_low_component = clamp_score((support_rise_pct + 1.0) / 8.0)
    compression_component = clamp_score(compression_pct / 45.0)
    volume_component = clamp_score((1.20 - volume_ratio) / 0.55)
    location_component = (
        1.0 if 0.20 <= distance_to_resistance_pct <= 4.0
        else 0.65 if 0 <= distance_to_resistance_pct <= 6.0
        else 0.20
    )

    score = round(
        25 * flat_component
        + 20 * touch_component
        + 20 * rising_low_component
        + 15 * compression_component
        + 10 * volume_component
        + 10 * location_component,
        1,
    )

    structural_ok = (
        touches >= 2
        and flatness_pct <= 3.0
        and support_slope > 0
        and compression_pct > 5
        and price <= resistance * 1.01
    )
    if structural_ok and score >= 75:
        label = "ASCENDING TRIANGLE — STRONG"
    elif structural_ok and score >= 60:
        label = "ASCENDING TRIANGLE — DEVELOPING"
    elif score >= 45 and touches >= 2 and support_slope > 0:
        label = "POSSIBLE ASCENDING TRIANGLE"
    else:
        label = "NO TRIANGLE"

    base_low = float(np.quantile(lows[: max(12, len(lows)//3)], 0.20))
    height = max(resistance - base_low, 0.0)
    measured_target = resistance + height if structural_ok and height > 0 else np.nan

    return {
        "triangle_label": label,
        "triangle_score": score,
        "triangle_resistance": resistance,
        "triangle_support_now": support_now,
        "triangle_support_start": support_start,
        "triangle_window_bars": len(d),
        "triangle_touches": touches,
        "triangle_flatness_pct": round(flatness_pct, 2),
        "triangle_compression_pct": round(compression_pct, 1),
        "triangle_volume_ratio": round(volume_ratio, 2),
        "triangle_measured_target": measured_target,
        "triangle_detail": (
            f"{touches} resistance touches · resistance flatness {flatness_pct:.2f}% · "
            f"rising support {support_rise_pct:.1f}% · compression {compression_pct:.1f}% · "
            f"recent/early volume {volume_ratio:.2f}x"
        ),
    }



def score_setup(
    df4h: pd.DataFrame,
    dfd: pd.DataFrame,
    btc4h: pd.DataFrame,
    cfg: ScreenerConfig,
    dfw: Optional[pd.DataFrame] = None,
    btcd: Optional[pd.DataFrame] = None,
) -> Dict:
    if len(df4h) < max(cfg.resistance_lookback + 25, 70) or len(dfd) < 35 or len(btc4h) < 30:
        return {"eligible": False, "reason": "Not enough history"}

    coin_trend_info = classify_trend(df4h, dfd)
    market_trend_info = (
        classify_trend(btc4h, btcd)
        if btcd is not None and not btcd.empty
        else {
            "trend": "UNAVAILABLE",
            "daily": "UNAVAILABLE",
            "four_hour": "UNAVAILABLE",
            "detail": "BTC daily history unavailable",
        }
    )
    freshness_info = project_freshness(dfd, dfw)
    candle_signal = latest_completed_4h_candle_signal(df4h)
    triangle = ascending_triangle_pattern(df4h, 60)
    bb_4h = bollinger_context(df4h)
    bb_daily = bollinger_context(dfd)
    channel_4h = trend_channel(df4h, 80)
    channel_daily = trend_channel(dfd, 90)

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

    # Keep scoring even when the strict pre-breakout shape fails. The broad scan
    # still excludes these coins, while Quick analyse can explain the full setup.
    shape_rejection = None
    if breakout_pct > cfg.too_late_pct:
        shape_rejection = "Too late / already broken out"
    elif distance_pct < -0.05:
        shape_rejection = "Already above resistance"
    elif distance_pct > cfg.near_resistance_max_pct:
        shape_rejection = "Too far below resistance"

    # 1) Price structure: higher lows + resistance interaction + EMA structure (20 pts)
    # Resistance-test count is evidence, not a hard gate. One clean compression can
    # be valid; repeated tests add confidence only if the rest of the structure agrees.
    lows_slope = lin_slope(recent["low"])
    higher_lows_component = clamp_score((lows_slope + 0.001) / 0.004)
    test_band = resistance * 0.015
    resistance_tests = int(((hist["high"] >= resistance - test_band) & (hist["high"] <= resistance * 1.001)).sum())
    test_component = clamp_score(resistance_tests / 4)
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

    # 5) Momentum: RSI is measured as a broad momentum/extension feature rather
    # than assuming a narrow 52-64 "ideal" band. MACD improvement remains useful.
    rsi_now = float(x["rsi"].iloc[-1])
    if 45 <= rsi_now <= 68:
        rsi_component = 1.0
    elif 38 <= rsi_now < 45 or 68 < rsi_now <= 74:
        rsi_component = 0.70
    elif 32 <= rsi_now < 38 or 74 < rsi_now <= 80:
        rsi_component = 0.40
    else:
        rsi_component = 0.15
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
    d["sma50"] = d["close"].rolling(50).mean()
    d["sma200"] = d["close"].rolling(200).mean()
    d["rsi"] = rsi(d["close"])
    d["atr"] = atr(d)
    d["obv"] = obv(d)
    daily_price = float(d["close"].iloc[-1])
    daily_ema = float(d["ema20"].iloc[-1])
    daily_sma50 = _safe_float(d["sma50"].iloc[-1], np.nan)
    daily_sma200 = _safe_float(d["sma200"].iloc[-1], np.nan)
    daily_rsi = float(d["rsi"].iloc[-1])

    if math.isfinite(daily_sma50) and math.isfinite(daily_sma200):
        if daily_price > daily_sma50 > daily_sma200:
            sma_regime = "BULLISH STACK"
        elif daily_sma50 > daily_sma200 and daily_price <= daily_sma50:
            sma_regime = "GOLDEN CROSS — PULLBACK"
        elif daily_price > daily_sma50 and daily_sma50 <= daily_sma200:
            sma_regime = "EARLY RECOVERY"
        elif daily_price > daily_sma200:
            sma_regime = "ABOVE 200D"
        else:
            sma_regime = "BELOW 200D"
    elif math.isfinite(daily_sma50):
        sma_regime = "ABOVE 50D" if daily_price > daily_sma50 else "BELOW 50D"
    else:
        sma_regime = "UNAVAILABLE"
    if daily_price >= daily_ema and daily_rsi <= 70:
        daily_component = 1.0
    elif daily_price >= daily_ema:
        daily_component = 0.6
    else:
        daily_component = 0.25
    daily_score = 5 * daily_component

    coin_ret_30 = period_return_pct(dfd, 30)
    coin_ret_90 = period_return_pct(dfd, 90)
    coin_ret_180 = period_return_pct(dfd, 180)
    btc_ret_30 = period_return_pct(btcd, 30) if btcd is not None else np.nan
    btc_ret_90 = period_return_pct(btcd, 90) if btcd is not None else np.nan
    btc_ret_180 = period_return_pct(btcd, 180) if btcd is not None else np.nan
    rs_btc_30 = (
        coin_ret_30 - btc_ret_30
        if math.isfinite(coin_ret_30) and math.isfinite(btc_ret_30)
        else np.nan
    )
    rs_btc_90 = (
        coin_ret_90 - btc_ret_90
        if math.isfinite(coin_ret_90) and math.isfinite(btc_ret_90)
        else np.nan
    )
    rs_btc_180 = (
        coin_ret_180 - btc_ret_180
        if math.isfinite(coin_ret_180) and math.isfinite(btc_ret_180)
        else np.nan
    )

    # Separate potential-base signal for disciplined accumulation entries.
    # This does not reward averaging into an unconfirmed downtrend.
    base_window = d.iloc[-60:]
    base_low = float(base_window["low"].min())
    base_distance_pct = (daily_price / base_low - 1) * 100 if base_low else 100.0
    base_location_component = clamp_score((25 - base_distance_pct) / 20)

    recent_daily_low = float(d["low"].iloc[-10:].min())
    prior_daily_low = float(d["low"].iloc[-30:-10].min())
    higher_base_component = clamp_score(
        0.5 + ((recent_daily_low / prior_daily_low - 1) / 0.08)
    ) if prior_daily_low else 0.0

    daily_ema_slope = lin_slope(d["ema20"].iloc[-10:])
    flattening_component = clamp_score((daily_ema_slope + 0.004) / 0.010)

    daily_rsi_change = daily_rsi - float(d["rsi"].iloc[-6])
    rsi_recovery_component = (
        clamp_score(0.55 + daily_rsi_change / 16)
        if 32 <= daily_rsi <= 62
        else 0.2
    )

    daily_obv_slope = lin_slope(d["obv"].iloc[-20:])
    daily_obv_component = clamp_score((daily_obv_slope + 0.006) / 0.024)

    bottom_components_raw = {
        "Base proximity": 30 * base_location_component,
        "Higher lows": 25 * higher_base_component,
        "Trend flattening": 20 * flattening_component,
        "RSI recovery": 15 * rsi_recovery_component,
        "Daily OBV": 10 * daily_obv_component,
    }
    bottom_score = round(float(sum(bottom_components_raw.values())), 1)
    bottom_components = {
        label: round(float(points), 1)
        for label, points in bottom_components_raw.items()
    }

    recent_support = float(d["low"].iloc[-20:].min())
    daily_atr = float(d["atr"].iloc[-5:].mean())
    accumulation_low = recent_support
    accumulation_high = min(
        recent_support + 1.5 * daily_atr,
        recent_support * 1.12,
    )
    in_accumulation_zone = accumulation_low <= daily_price <= accumulation_high

    if bottom_score >= 70:
        bottom_status = "Strong potential base"
    elif bottom_score >= 50:
        bottom_status = "Base developing"
    else:
        bottom_status = "Bottom not confirmed"

    # 8) Entry quality: near resistance but not touching it, with nearby invalidation (10 pts)
    ideal_mid = (cfg.near_resistance_min_pct + min(cfg.near_resistance_max_pct, 3.5)) / 2
    distance_component = clamp_score(1 - abs(distance_pct - ideal_mid) / max(ideal_mid, 1.0))
    # Use the nearest meaningful support across the 4h structure and daily trend
    # for entry timing, rather than anchoring solely to the current price.
    swing_low = float(x["low"].iloc[-24:].min())
    support_candidates = {
        "4h EMA20": float(x["ema20"].iloc[-1]),
        "4h EMA50": float(x["ema50"].iloc[-1]),
        "Daily EMA20": daily_ema,
        "Daily SMA50": daily_sma50,
        "Daily SMA200": daily_sma200,
        "20-day support cluster": float(d["low"].iloc[-20:].quantile(0.35)),
    }
    if channel_4h.get("quality") in ("HIGH", "MEDIUM") and channel_4h.get("direction") != "FALLING":
        support_candidates["4h channel support"] = _safe_float(channel_4h.get("support"))
    if channel_daily.get("quality") in ("HIGH", "MEDIUM") and channel_daily.get("direction") != "FALLING":
        support_candidates["Daily channel support"] = _safe_float(channel_daily.get("support"))
    valid_supports = {
        label: level for label, level in support_candidates.items()
        if 0 < level <= price
    }
    if valid_supports:
        entry_basis, entry_anchor = max(valid_supports.items(), key=lambda item: item[1])
    else:
        entry_basis, entry_anchor = "Current price fallback", price
    entry_atr = float(x["atr"].iloc[-5:].mean())
    invalidation = min(swing_low, entry_anchor - 1.25 * entry_atr) * 0.995
    risk_pct = max((price - invalidation) / price * 100, 0.01)

    # Short-term measured move remains useful, while long-range weekly resistance
    # uses up to ~4 years of available history as a technical reference window.
    # This is not treated as evidence of a fixed four-year crypto cycle.
    pattern_base_low = float(hist["low"].min())
    pattern_height_pct = max((resistance - pattern_base_low) / resistance * 100, 0)
    measured_target = resistance * (1 + min(pattern_height_pct, 35) / 100)

    weekly_primary = np.nan
    weekly_stretch = np.nan
    weekly_target_levels: List[float] = []
    cycle_high = np.nan
    cycle_position_pct = np.nan
    cycle_accumulation_low = np.nan
    cycle_accumulation_high = np.nan
    in_cycle_accumulation_zone = False
    cycle_accumulation_basis = "Weekly history unavailable"
    target_basis = "4h measured move"

    if dfw is not None and len(dfw) >= 26:
        w = dfw.copy().tail(209)
        completed_w = w.iloc[:-1] if len(w) > 1 else w
        weekly_highs = completed_w["high"]
        weekly_lows = completed_w["low"]
        cycle_high = float(weekly_highs.max())
        cycle_low = float(weekly_lows.min())
        if cycle_high > cycle_low:
            cycle_position_pct = (price - cycle_low) / (cycle_high - cycle_low) * 100

        local_lows = weekly_lows[
            weekly_lows == weekly_lows.rolling(5, center=True, min_periods=3).min()
        ]
        weekly_supports = sorted(
            float(level)
            for level in local_lows.dropna()
            if 0 < float(level) <= price
        )
        if weekly_supports:
            weekly_support = weekly_supports[-1]
            weekly_atr = float(atr(completed_w).iloc[-5:].mean())
            cycle_accumulation_low = max(
                weekly_support - 0.25 * weekly_atr,
                weekly_support * 0.92,
            )
            cycle_accumulation_high = min(
                weekly_support + 0.25 * weekly_atr,
                weekly_support * 1.08,
            )
            in_cycle_accumulation_zone = (
                cycle_accumulation_low <= price <= cycle_accumulation_high
            )
            cycle_accumulation_basis = "Nearest confirmed weekly swing-low support"

        local_peaks = weekly_highs[
            weekly_highs == weekly_highs.rolling(5, center=True, min_periods=3).max()
        ]
        overhead = sorted(
            {
                float(level)
                for level in local_peaks.dropna()
                if float(level) >= max(price * 1.08, resistance * 1.03)
            }
        )
        if overhead:
            weekly_target_levels = [float(level) * 0.985 for level in overhead]
            weekly_primary = weekly_target_levels[0]
            for candidate in weekly_target_levels[1:]:
                if candidate >= weekly_primary * 1.08:
                    weekly_stretch = candidate
                    break
            target_basis = "Nearest major weekly resistance"

    first_take_profit = (
        float(weekly_primary)
        if math.isfinite(weekly_primary)
        else max(measured_target, resistance * 1.03)
    )
    first_take_profit_basis = (
        "Nearest major weekly resistance"
        if math.isfinite(weekly_primary)
        else "4h measured move"
    )

    entry_half_width = max(entry_atr * 0.40, price * 0.004)
    raw_entry_low = max(invalidation * 1.01, entry_anchor - entry_half_width)
    raw_entry_high = min(resistance * 0.998, entry_anchor + entry_half_width)
    entry_low = min(raw_entry_low, raw_entry_high * 0.999)
    entry_high = max(raw_entry_high, entry_low * 1.001)
    planned_entry = (entry_low + entry_high) / 2

    credible_targets = list(weekly_target_levels)
    if measured_target > price:
        credible_targets.append(float(measured_target))
    triangle_target = _safe_float(triangle.get("triangle_measured_target"), np.nan)
    if (
        triangle.get("triangle_label") in (
            "ASCENDING TRIANGLE — STRONG",
            "ASCENDING TRIANGLE — DEVELOPING",
        )
        and math.isfinite(triangle_target)
        and triangle_target > price
    ):
        credible_targets.append(float(triangle_target))
    credible_targets = sorted(set(credible_targets))

    minimum_trade_target = planned_entry * (1 + cfg.min_gross_profit_pct / 100)
    qualifying_targets = [
        level for level in credible_targets
        if level >= minimum_trade_target
    ]
    projected_target = qualifying_targets[0] if qualifying_targets else np.nan
    stretch_target = qualifying_targets[1] if len(qualifying_targets) > 1 else np.nan

    if math.isfinite(projected_target):
        target_basis = (
            "Major weekly resistance meeting the 30% rule"
            if any(abs(projected_target - level) < max(level * 1e-8, 1e-12) for level in weekly_target_levels)
            else "Ascending-triangle measured move meeting the 30% rule"
            if math.isfinite(triangle_target)
            and abs(projected_target - triangle_target) < max(triangle_target * 1e-8, 1e-12)
            else "4h measured move meeting the 30% rule"
        )
        target_upside_pct = (projected_target - planned_entry) / planned_entry * 100
    else:
        target_basis = "No credible target meets the 30% gross-profit rule"
        target_upside_pct = np.nan

    downside_to_invalidation_pct = max(
        (planned_entry - invalidation) / planned_entry * 100,
        0.01,
    )
    reward_pct = max(float(target_upside_pct), 0) if math.isfinite(target_upside_pct) else 0.0
    rr = reward_pct / downside_to_invalidation_pct
    rr_component = clamp_score((rr - 1.0) / 2.5)
    entry_score = 5 * distance_component + 5 * rr_component

    # The trade model's published component weights total 95 points. Normalize
    # the raw result so the displayed score genuinely uses a 0–100 scale.
    raw_total = (
        structure_score + compression_score + volume_score + rs_score
        + momentum_score + obv_score + daily_score + entry_score
    )
    total = round(float(max(0, min(100, raw_total / 95 * 100))), 1)

    shape_eligible = (
        cfg.near_resistance_min_pct <= distance_pct <= cfg.near_resistance_max_pct
        and lows_slope > -0.0015
        and rsi_now <= 80
    )
    trade_target_eligible = math.isfinite(projected_target)
    eligible = shape_eligible and trade_target_eligible

    if eligible:
        result_reason = "Pre-breakout candidate with at least 30% gross target upside"
    elif not shape_eligible:
        result_reason = shape_rejection or "Shape filter not met"
    else:
        result_reason = "Pre-breakout shape found, but no credible 30% gross-profit target"

    # Accumulation is now driven by the actual daily base structure. Long-range
    # weekly support and the four-year range are reference context only and do
    # not trigger an accumulation decision.
    if bottom_score >= 70 and in_accumulation_zone:
        accumulation_verdict = "ACCUMULATION READY"
    elif bottom_score >= 50 or in_accumulation_zone:
        accumulation_verdict = "WATCH FOR BASE CONFIRMATION"
    else:
        accumulation_verdict = "NOT READY TO ACCUMULATE"

    previous_cycle_high_reference = (
        cycle_high * 0.985
        if math.isfinite(cycle_high) and cycle_high > planned_entry
        else np.nan
    )

    return {
        "eligible": bool(eligible),
        "shape_eligible": bool(shape_eligible),
        "trade_target_eligible": bool(trade_target_eligible),
        "reason": result_reason,
        "score": total,
        "price": price,
        "resistance": resistance,
        "distance_pct": round(distance_pct, 2),
        "resistance_tests": resistance_tests,
        "rsi": round(rsi_now, 1),
        "atr_ratio": round(atr_ratio, 2),
        "volume_ratio": round(vol_ratio, 2),
        "rs_vs_btc_pct": round(rs12 * 100, 2),
        "rs_vs_btc_96h_pct": round(rs24 * 100, 2),
        "return_30d_pct": round(float(coin_ret_30), 2) if math.isfinite(coin_ret_30) else np.nan,
        "return_90d_pct": round(float(coin_ret_90), 2) if math.isfinite(coin_ret_90) else np.nan,
        "return_180d_pct": round(float(coin_ret_180), 2) if math.isfinite(coin_ret_180) else np.nan,
        "rs_vs_btc_30d_pct": round(float(rs_btc_30), 2) if math.isfinite(rs_btc_30) else np.nan,
        "rs_vs_btc_90d_pct": round(float(rs_btc_90), 2) if math.isfinite(rs_btc_90) else np.nan,
        "rs_vs_btc_180d_pct": round(float(rs_btc_180), 2) if math.isfinite(rs_btc_180) else np.nan,
        "risk_reward": round(rr, 2),
        "planned_entry": planned_entry,
        "downside_to_invalidation_pct": round(downside_to_invalidation_pct, 2),
        "entry_low": entry_low,
        "entry_high": entry_high,
        "invalidation": invalidation,
        "target_1": resistance * 1.05,
        "target_2": resistance * 1.10,
        "first_take_profit": first_take_profit,
        "first_take_profit_basis": first_take_profit_basis,
        "projected_target": projected_target,
        "stretch_target": stretch_target,
        "target_upside_pct": round(float(target_upside_pct), 2) if math.isfinite(target_upside_pct) else np.nan,
        "target_basis": target_basis,
        "minimum_gross_profit_pct": cfg.min_gross_profit_pct,
        "entry_basis": entry_basis,
        "coin_trend": coin_trend_info["trend"],
        "coin_trend_daily": coin_trend_info["daily"],
        "coin_trend_4h": coin_trend_info["four_hour"],
        "coin_trend_detail": coin_trend_info["detail"],
        "market_trend": market_trend_info["trend"],
        "market_trend_daily": market_trend_info["daily"],
        "market_trend_4h": market_trend_info["four_hour"],
        "market_trend_detail": market_trend_info["detail"],
        "project_freshness": freshness_info["freshness"],
        "history_days": freshness_info["history_days"],
        "freshness_score": freshness_info["freshness_score"],
        "freshness_basis": freshness_info["freshness_basis"],
        "candle_pattern": candle_signal["candle_pattern"],
        "candle_caution": bool(candle_signal["candle_caution"]),
        "candle_detail": candle_signal["candle_detail"],
        "triangle_label": triangle.get("triangle_label", "NO TRIANGLE"),
        "triangle_score": triangle.get("triangle_score", 0.0),
        "triangle_resistance": triangle.get("triangle_resistance", np.nan),
        "triangle_support_now": triangle.get("triangle_support_now", np.nan),
        "triangle_touches": triangle.get("triangle_touches", 0),
        "triangle_flatness_pct": triangle.get("triangle_flatness_pct", np.nan),
        "triangle_compression_pct": triangle.get("triangle_compression_pct", np.nan),
        "triangle_measured_target": triangle.get("triangle_measured_target", np.nan),
        "triangle_detail": triangle.get("triangle_detail", ""),
        "sma50": daily_sma50,
        "sma200": daily_sma200,
        "sma_regime": sma_regime,
        "bb_4h_regime": bb_4h.get("bb_regime", "UNAVAILABLE"),
        "bb_4h_mid": bb_4h.get("bb_mid", np.nan),
        "bb_4h_upper": bb_4h.get("bb_upper", np.nan),
        "bb_4h_lower": bb_4h.get("bb_lower", np.nan),
        "bb_4h_width_pct": bb_4h.get("bb_width_pct", np.nan),
        "bb_4h_position_pct": bb_4h.get("bb_position_pct", np.nan),
        "bb_4h_width_percentile": bb_4h.get("bb_width_percentile", np.nan),
        "bb_daily_regime": bb_daily.get("bb_regime", "UNAVAILABLE"),
        "bb_daily_width_pct": bb_daily.get("bb_width_pct", np.nan),
        "bb_daily_position_pct": bb_daily.get("bb_position_pct", np.nan),
        "price_vs_sma50_pct": (
            round((daily_price / daily_sma50 - 1) * 100, 2)
            if math.isfinite(daily_sma50) and daily_sma50 > 0 else np.nan
        ),
        "price_vs_sma200_pct": (
            round((daily_price / daily_sma200 - 1) * 100, 2)
            if math.isfinite(daily_sma200) and daily_sma200 > 0 else np.nan
        ),
        "channel_4h_direction": channel_4h.get("direction", "UNAVAILABLE"),
        "channel_4h_position": channel_4h.get("position", np.nan),
        "channel_4h_support": channel_4h.get("support", np.nan),
        "channel_4h_resistance": channel_4h.get("resistance", np.nan),
        "channel_4h_width_pct": channel_4h.get("width_pct", np.nan),
        "channel_4h_rr": channel_4h.get("rr", np.nan),
        "channel_4h_touches": channel_4h.get("touches", 0),
        "channel_4h_quality": channel_4h.get("quality", "LOW"),
        "channel_4h_state": channel_4h.get("state", "NONE"),
        "channel_daily_direction": channel_daily.get("direction", "UNAVAILABLE"),
        "channel_daily_position": channel_daily.get("position", np.nan),
        "channel_daily_support": channel_daily.get("support", np.nan),
        "channel_daily_resistance": channel_daily.get("resistance", np.nan),
        "channel_daily_quality": channel_daily.get("quality", "LOW"),
        "channel_daily_state": channel_daily.get("state", "NONE"),
        "cycle_position_pct": round(float(cycle_position_pct), 1) if math.isfinite(cycle_position_pct) else np.nan,
        "cycle_accumulation_low": cycle_accumulation_low,
        "cycle_accumulation_high": cycle_accumulation_high,
        "in_cycle_accumulation_zone": bool(in_cycle_accumulation_zone),
        "cycle_accumulation_basis": cycle_accumulation_basis,
        "accumulation_verdict": accumulation_verdict,
        "previous_cycle_high_reference": previous_cycle_high_reference,
        "bottom_score": bottom_score,
        "bottom_components": bottom_components,
        "bottom_status": bottom_status,
        "accumulation_low": accumulation_low,
        "accumulation_high": accumulation_high,
        "in_accumulation_zone": bool(in_accumulation_zone),
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

