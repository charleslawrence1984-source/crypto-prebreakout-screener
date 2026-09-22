from __future__ import annotations

import math
from typing import Dict, Iterable, List, Tuple

import numpy as np
import pandas as pd


FEATURE_COLUMNS = [
    "rs_btc_7d_pct",
    "rs_btc_14d_pct",
    "rs_btc_30d_pct",
    "return_3d_pct",
    "return_7d_pct",
    "return_14d_pct",
    "rsi14",
    "atr_pct",
    "atr_compression",
    "bb_width_pct",
    "bb_width_percentile_60d",
    "volume_ratio_5_20",
    "range_compression_10_20",
    "distance_to_resistance_pct",
    "resistance_tests_30d",
    "higher_low_pct",
    "distance_ema20_pct",
    "distance_sma50_pct",
    "obv_slope_norm",
    "close_position_30d_pct",
]


def _safe_float(value, default=np.nan) -> float:
    try:
        x = float(value)
        return x if math.isfinite(x) else default
    except Exception:
        return default


def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50)


def _atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    prev = df["close"].shift(1)
    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev).abs(),
            (df["low"] - prev).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def _obv(df: pd.DataFrame) -> pd.Series:
    direction = np.sign(df["close"].diff()).fillna(0)
    return (direction * df["volume"]).cumsum()


def _rolling_percentile(series: pd.Series, window: int = 60) -> pd.Series:
    def rank_last(values: np.ndarray) -> float:
        clean = values[np.isfinite(values)]
        if len(clean) < 10:
            return np.nan
        last = clean[-1]
        return float((clean <= last).mean() * 100)

    return series.rolling(window, min_periods=max(10, window // 3)).apply(rank_last, raw=True)


def build_feature_frame(coin_daily: pd.DataFrame, btc_daily: pd.DataFrame) -> pd.DataFrame:
    """
    Build point-in-time daily features. All resistance/structure windows are shifted
    where needed so the current candle cannot define its own historical resistance.
    """
    c = coin_daily.copy().sort_values("timestamp").drop_duplicates("timestamp")
    b = btc_daily.copy().sort_values("timestamp").drop_duplicates("timestamp")
    required = {"timestamp", "open", "high", "low", "close", "volume"}
    if not required.issubset(c.columns) or not required.issubset(b.columns):
        raise ValueError("Daily data must include timestamp/open/high/low/close/volume.")

    for frame in (c, b):
        for col in ("open", "high", "low", "close", "volume"):
            frame[col] = pd.to_numeric(frame[col], errors="coerce")
    c = c.dropna(subset=["close", "high", "low", "volume"]).reset_index(drop=True)
    b = b.dropna(subset=["close"]).reset_index(drop=True)

    # Align BTC close to each coin daily candle without looking forward.
    aligned = pd.merge_asof(
        c[["timestamp"]].sort_values("timestamp"),
        b[["timestamp", "close"]].rename(columns={"close": "btc_close"}).sort_values("timestamp"),
        on="timestamp",
        direction="backward",
    )
    c["btc_close"] = aligned["btc_close"].to_numpy()

    close = c["close"]
    c["return_3d_pct"] = close.pct_change(3) * 100
    c["return_7d_pct"] = close.pct_change(7) * 100
    c["return_14d_pct"] = close.pct_change(14) * 100
    c["return_30d_pct"] = close.pct_change(30) * 100

    btc = c["btc_close"]
    c["rs_btc_7d_pct"] = (close.pct_change(7) - btc.pct_change(7)) * 100
    c["rs_btc_14d_pct"] = (close.pct_change(14) - btc.pct_change(14)) * 100
    c["rs_btc_30d_pct"] = (close.pct_change(30) - btc.pct_change(30)) * 100

    c["rsi14"] = _rsi(close)
    c["atr14"] = _atr(c)
    c["atr_pct"] = c["atr14"] / close * 100
    atr_recent = c["atr_pct"].rolling(5, min_periods=3).mean()
    atr_base = c["atr_pct"].shift(5).rolling(25, min_periods=10).median()
    c["atr_compression"] = atr_recent / atr_base.replace(0, np.nan)

    bb_mid = close.rolling(20).mean()
    bb_sd = close.rolling(20).std()
    bb_upper = bb_mid + 2 * bb_sd
    bb_lower = bb_mid - 2 * bb_sd
    c["bb_width_pct"] = (bb_upper - bb_lower) / bb_mid.replace(0, np.nan) * 100
    c["bb_width_percentile_60d"] = _rolling_percentile(c["bb_width_pct"], 60)

    c["volume_ratio_5_20"] = (
        c["volume"].rolling(5).mean()
        / c["volume"].rolling(20).mean().replace(0, np.nan)
    )

    range10 = (
        c["high"].rolling(10).max() - c["low"].rolling(10).min()
    ) / close.replace(0, np.nan)
    prior20 = (
        c["high"].shift(10).rolling(20).max() - c["low"].shift(10).rolling(20).min()
    ) / close.shift(10).replace(0, np.nan)
    c["range_compression_10_20"] = range10 / prior20.replace(0, np.nan)

    historical_resistance = c["high"].shift(1).rolling(30).max()
    c["distance_to_resistance_pct"] = (historical_resistance - close) / close * 100

    # Count how many prior highs touched the current historical resistance band.
    tests = []
    for i in range(len(c)):
        resistance = _safe_float(historical_resistance.iloc[i])
        if i < 30 or not math.isfinite(resistance) or resistance <= 0:
            tests.append(np.nan)
            continue
        highs = c["high"].iloc[max(0, i - 30):i]
        band_low = resistance * 0.985
        tests.append(float(((highs >= band_low) & (highs <= resistance * 1.001)).sum()))
    c["resistance_tests_30d"] = tests

    recent_low = c["low"].rolling(10).min()
    prior_low = c["low"].shift(10).rolling(20).min()
    c["higher_low_pct"] = (recent_low / prior_low.replace(0, np.nan) - 1) * 100

    c["ema20"] = close.ewm(span=20, adjust=False).mean()
    c["sma50"] = close.rolling(50).mean()
    c["sma200"] = close.rolling(200).mean()
    c["distance_ema20_pct"] = (close / c["ema20"] - 1) * 100
    c["distance_sma50_pct"] = (close / c["sma50"] - 1) * 100

    c["obv"] = _obv(c)
    obv_delta = c["obv"].diff(10)
    avg_vol = c["volume"].rolling(20).mean().replace(0, np.nan)
    c["obv_slope_norm"] = obv_delta / (avg_vol * 10)

    low30 = c["low"].rolling(30).min()
    high30 = c["high"].rolling(30).max()
    c["close_position_30d_pct"] = (
        (close - low30) / (high30 - low30).replace(0, np.nan) * 100
    )

    c["trend_regime"] = np.select(
        [
            (close > c["sma50"]) & (c["sma50"] > c["sma200"]),
            (close > c["sma50"]) & (c["sma50"] <= c["sma200"]),
            close < c["sma50"],
        ],
        ["BULLISH STACK", "EARLY RECOVERY", "BELOW 50D"],
        default="UNAVAILABLE",
    )

    return c


def label_forward_outcomes(
    features: pd.DataFrame,
    target_pct: float = 30.0,
    horizon_days: int = 30,
    adverse_limit_pct: float = 15.0,
    min_history: int = 90,
) -> pd.DataFrame:
    """
    Label every eligible daily anchor. A success must hit the upside target before
    breaching the adverse threshold. If both occur in the same daily candle, the
    result is treated conservatively as ambiguous/failure because intraday order is unknown.
    """
    rows: List[Dict] = []
    if horizon_days < 1:
        raise ValueError("horizon_days must be positive")

    last_anchor = len(features) - horizon_days - 1
    for i in range(max(min_history, 1), max(last_anchor + 1, 0)):
        row = features.iloc[i]
        entry = _safe_float(row["close"])
        if not math.isfinite(entry) or entry <= 0:
            continue
        future = features.iloc[i + 1:i + 1 + horizon_days]
        if future.empty:
            continue

        target_price = entry * (1 + target_pct / 100)
        adverse_price = entry * (1 - adverse_limit_pct / 100)

        hit_target = future["high"] >= target_price
        hit_adverse = future["low"] <= adverse_price
        target_idx = int(np.argmax(hit_target.to_numpy())) if hit_target.any() else None
        adverse_idx = int(np.argmax(hit_adverse.to_numpy())) if hit_adverse.any() else None

        ambiguous_same_day = (
            target_idx is not None and adverse_idx is not None and target_idx == adverse_idx
        )
        success = (
            target_idx is not None
            and not ambiguous_same_day
            and (adverse_idx is None or target_idx < adverse_idx)
        )
        time_to_target = target_idx + 1 if success else np.nan
        mfe = (future["high"].max() / entry - 1) * 100
        mae = (future["low"].min() / entry - 1) * 100

        out = {
            "date": row["timestamp"],
            "entry": entry,
            "success": bool(success),
            "ambiguous_same_day": bool(ambiguous_same_day),
            "target_pct": float(target_pct),
            "horizon_days": int(horizon_days),
            "adverse_limit_pct": float(adverse_limit_pct),
            "time_to_target_days": time_to_target,
            "mfe_pct": float(mfe),
            "mae_pct": float(mae),
            "trend_regime": row.get("trend_regime", "UNAVAILABLE"),
        }
        for col in FEATURE_COLUMNS:
            out[col] = _safe_float(row.get(col))
        rows.append(out)

    return pd.DataFrame(rows)


def feature_comparison(events: pd.DataFrame) -> pd.DataFrame:
    if events.empty or "success" not in events.columns:
        return pd.DataFrame()
    winners = events[events["success"]]
    failures = events[~events["success"]]
    if winners.empty or failures.empty:
        return pd.DataFrame()

    rows = []
    for feature in FEATURE_COLUMNS:
        all_values = pd.to_numeric(events[feature], errors="coerce").dropna()
        win_values = pd.to_numeric(winners[feature], errors="coerce").dropna()
        fail_values = pd.to_numeric(failures[feature], errors="coerce").dropna()
        if len(all_values) < 20 or len(win_values) < 3 or len(fail_values) < 3:
            continue
        wmed = float(win_values.median())
        fmed = float(fail_values.median())
        scale = float(all_values.std(ddof=0))
        separation = (wmed - fmed) / scale if scale > 1e-12 else 0.0
        rows.append(
            {
                "Feature": feature,
                "Winner median": round(wmed, 3),
                "Failure median": round(fmed, 3),
                "Difference": round(wmed - fmed, 3),
                "Standardised separation": round(separation, 3),
                "Observed direction": "Higher in winners" if separation > 0 else "Lower in winners" if separation < 0 else "No separation",
                "Winner samples": int(len(win_values)),
                "Failure samples": int(len(fail_values)),
            }
        )

    result = pd.DataFrame(rows)
    if result.empty:
        return result
    result["Abs separation"] = result["Standardised separation"].abs()
    result = result.sort_values("Abs separation", ascending=False).drop(columns=["Abs separation"])
    return result.reset_index(drop=True)


def snapshot_table(
    features: pd.DataFrame,
    events: pd.DataFrame,
    mode: str = "latest",
    offsets: Iterable[int] = (30, 14, 7, 3, 1, 0),
) -> Tuple[pd.DataFrame, Dict]:
    winners = events[events["success"]].copy() if not events.empty else pd.DataFrame()
    if winners.empty:
        return pd.DataFrame(), {}

    if mode == "strongest":
        chosen = winners.sort_values(["mfe_pct", "date"], ascending=[False, False]).iloc[0]
    else:
        chosen = winners.sort_values("date", ascending=False).iloc[0]

    anchor_date = pd.Timestamp(chosen["date"])
    feature_dates = pd.to_datetime(features["timestamp"])
    anchor_matches = np.where(feature_dates == anchor_date)[0]
    if len(anchor_matches) == 0:
        return pd.DataFrame(), {}
    anchor_idx = int(anchor_matches[-1])

    rows = []
    for offset in offsets:
        idx = anchor_idx - int(offset)
        if idx < 0:
            continue
        r = features.iloc[idx]
        label = "T0 setup" if offset == 0 else f"T-{offset}"
        item = {
            "Snapshot": label,
            "Date": r["timestamp"],
            "Close": _safe_float(r["close"]),
            "RS vs BTC 7d %": _safe_float(r.get("rs_btc_7d_pct")),
            "RS vs BTC 30d %": _safe_float(r.get("rs_btc_30d_pct")),
            "RSI14": _safe_float(r.get("rsi14")),
            "ATR compression": _safe_float(r.get("atr_compression")),
            "BB width pctile": _safe_float(r.get("bb_width_percentile_60d")),
            "Volume ratio 5/20": _safe_float(r.get("volume_ratio_5_20")),
            "Range compression": _safe_float(r.get("range_compression_10_20")),
            "Distance to resistance %": _safe_float(r.get("distance_to_resistance_pct")),
            "Resistance tests": _safe_float(r.get("resistance_tests_30d")),
            "Higher low %": _safe_float(r.get("higher_low_pct")),
            "30d range position %": _safe_float(r.get("close_position_30d_pct")),
            "Trend": r.get("trend_regime", "UNAVAILABLE"),
        }
        rows.append(item)

    meta = {
        "anchor_date": anchor_date,
        "entry": float(chosen["entry"]),
        "mfe_pct": float(chosen["mfe_pct"]),
        "mae_pct": float(chosen["mae_pct"]),
        "time_to_target_days": _safe_float(chosen["time_to_target_days"]),
    }
    return pd.DataFrame(rows), meta


def research_summary(events: pd.DataFrame) -> Dict:
    if events.empty:
        return {
            "samples": 0,
            "winners": 0,
            "hit_rate_pct": np.nan,
            "median_mfe_pct": np.nan,
            "median_mae_pct": np.nan,
            "median_time_to_target_days": np.nan,
        }
    winners = events[events["success"]]
    return {
        "samples": int(len(events)),
        "winners": int(len(winners)),
        "hit_rate_pct": float(events["success"].mean() * 100),
        "median_mfe_pct": float(events["mfe_pct"].median()),
        "median_mae_pct": float(events["mae_pct"].median()),
        "median_time_to_target_days": (
            float(winners["time_to_target_days"].median()) if not winners.empty else np.nan
        ),
    }
