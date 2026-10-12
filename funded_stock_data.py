"""Validated daily OHLCV retrieval for the Kraken Funded stocks tab only.

Batch downloads can omit otherwise valid tickers. Retry the affected symbol
individually, then fail closed if price candles remain missing or stale.
"""
from __future__ import annotations

import pandas as pd

REQUIRED = ("Open", "High", "Low", "Close", "Volume")
MIN_BARS = 252
MAX_LAST_BAR_AGE_DAYS = 5


def _extract_stock_frame(batch: pd.DataFrame, symbol: str) -> pd.DataFrame | None:
    if batch is None or batch.empty:
        return None
    if isinstance(batch.columns, pd.MultiIndex):
        level0 = set(str(v) for v in batch.columns.get_level_values(0))
        level1 = set(str(v) for v in batch.columns.get_level_values(1))
        if symbol in level0:
            frame = batch[symbol].copy()
        elif symbol in level1:
            frame = batch.xs(symbol, level=1, axis=1).copy()
        else:
            return None
    else:
        frame = batch.copy()
    frame = frame.rename(columns={str(column): str(column).title() for column in frame.columns})
    return frame if set(REQUIRED).issubset(frame.columns) else None


def clean_stock_daily_frame(frame: pd.DataFrame | None, now: pd.Timestamp) -> tuple[pd.DataFrame | None, str]:
    if frame is None or frame.empty:
        return None, "PRICE DATA MISSING"
    data = frame.copy()
    for field in REQUIRED:
        if field not in data.columns:
            return None, "OHLCV COLUMNS MISSING"
        data[field] = pd.to_numeric(data[field], errors="coerce")
    data = data.dropna(subset=list(REQUIRED)).sort_index()
    data = data.loc[~data.index.duplicated(keep="last")]
    data = data.loc[
        (data["Open"] > 0) & (data["High"] > 0)
        & (data["Low"] > 0) & (data["Close"] > 0)
        & (data["Volume"] >= 0)
        & (data["High"] >= data[["Open", "Close", "Low"]].max(axis=1))
        & (data["Low"] <= data[["Open", "Close", "High"]].min(axis=1))
    ]
    if data.empty:
        return None, "NO VALID OHLCV ROWS"
    # Do not let a developing US regular-session candle become a confirmed breakout.
    ny_now = pd.Timestamp(now)
    if ny_now.tzinfo is None:
        ny_now = ny_now.tz_localize("America/New_York")
    else:
        ny_now = ny_now.tz_convert("America/New_York")
    last_date = pd.Timestamp(data.index[-1]).date()
    if last_date == ny_now.date() and (ny_now.hour, ny_now.minute) < (16, 15):
        data = data.iloc[:-1]
    if data.empty:
        return None, "NO COMPLETED PRICE BARS"
    if len(data) < MIN_BARS:
        return None, f"PRICE HISTORY {len(data)}/{MIN_BARS} COMPLETED BARS"
    last_date = pd.Timestamp(data.index[-1]).date()
    age_days = (ny_now.date() - last_date).days
    if age_days < 0:
        return None, f"FUTURE-DATED PRICE BAR {last_date}"
    if age_days > MAX_LAST_BAR_AGE_DAYS:
        return None, f"STALE PRICE DATA — LAST COMPLETED {last_date}"
    return data, "PASS"


def get_validated_stock_history(
    batch: pd.DataFrame | None, symbol: str, downloader, now: pd.Timestamp
) -> tuple[pd.DataFrame | None, str, str]:
    """Return (clean_frame, source, reason), always checking the latest full bar.

    downloader(symbol) should request independent unadjusted 3-year daily data.
    """
    batch_frame = _extract_stock_frame(batch, symbol)
    clean, reason = clean_stock_daily_frame(batch_frame, now)
    if clean is not None:
        return clean, "BATCH", "PASS"
    original_reason = reason
    try:
        direct = downloader(symbol)
        direct_frame = _extract_stock_frame(direct, symbol)
        clean, reason = clean_stock_daily_frame(direct_frame, now)
        if clean is not None:
            return clean, "SINGLE-TICKER RETRY", "PASS"
    except Exception as exc:
        reason = f"RETRY ERROR: {type(exc).__name__}"
    return None, "NO VALID PRICE DATA", f"BATCH: {original_reason}; RETRY: {reason}"
