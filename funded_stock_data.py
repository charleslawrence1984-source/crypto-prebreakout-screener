"""Fail-closed daily OHLCV for the Kraken Funded stock research and UI.

Validate against the LAST COMPLETED NYSE SESSION (with a conservative 45-minute
vendor-publication grace), not a five-calendar-day threshold. A clean but
previous-session batch triggers a direct per-symbol retry. Public Yahoo prices
do not establish Kraken Funded bid/ask or order eligibility.
"""
from __future__ import annotations

from functools import lru_cache

import exchange_calendars as xcals
import pandas as pd

REQUIRED = ("Open", "High", "Low", "Close", "Volume")
MIN_BARS = 252
PRICE_PUBLICATION_GRACE_MINUTES = 45


@lru_cache(maxsize=1)
def _nyse():
    """Exchange calendar handles weekends, holidays, DST and early closes."""
    return xcals.get_calendar("XNYS")


def _new_york_now(now: pd.Timestamp) -> pd.Timestamp:
    value = pd.Timestamp(now)
    return (value.tz_localize("America/New_York") if value.tzinfo is None
            else value.tz_convert("America/New_York"))


def expected_completed_nyse_session(now: pd.Timestamp):
    """Return the latest NYSE session whose actual close + grace has passed.

    Fail closed if the calendar is unavailable or a completed session cannot
    be established; no calendar-day substitute that might bless stale bars.
    """
    ny_now = _new_york_now(now)
    utc_now = ny_now.tz_convert("UTC")
    cal = _nyse()
    start = pd.Timestamp(ny_now.date()) - pd.Timedelta(days=21)
    end = pd.Timestamp(ny_now.date())
    sessions = cal.sessions_in_range(start, end)
    completed = [session.date() for session in sessions
                 if utc_now >= cal.session_close(session)
                 + pd.Timedelta(minutes=PRICE_PUBLICATION_GRACE_MINUTES)]
    if not completed:
        raise ValueError("No verified completed NYSE session in calendar window")
    return completed[-1]


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

    ny_now = _new_york_now(now)
    try:
        expected = expected_completed_nyse_session(ny_now)
    except (ValueError, KeyError, RuntimeError, OverflowError) as exc:
        return None, f"NYSE SESSION CALENDAR UNAVAILABLE — {type(exc).__name__}"

    last_date = pd.Timestamp(data.index[-1]).date()
    if last_date > ny_now.date():
        return None, f"FUTURE-DATED PRICE BAR {last_date}"

    if last_date > expected:
        # A genuine today's NYSE candle cannot be used until close+grace.
        # Never accept made-up nontrading-day candles.
        if last_date != ny_now.date() or not _nyse().is_session(pd.Timestamp(last_date)):
            return None, f"NON-COMPLETED OR NON-SESSION PRICE BAR {last_date}"
        data = data.iloc[:-1]
    if data.empty:
        return None, "NO COMPLETED PRICE BARS"
    if len(data) < MIN_BARS:
        return None, f"PRICE HISTORY {len(data)}/{MIN_BARS} COMPLETED BARS"

    last_date = pd.Timestamp(data.index[-1]).date()
    if last_date < expected:
        return None, (f"STALE PRICE DATA — LAST COMPLETED {last_date}; "
                      f"EXPECTED COMPLETED NYSE SESSION {expected}")
    if last_date > expected:
        return None, f"NON-COMPLETED PRICE BAR {last_date}; EXPECTED {expected}"
    return data, "PASS"


def get_validated_stock_history(
    batch: pd.DataFrame | None, symbol: str, downloader, now: pd.Timestamp
) -> tuple[pd.DataFrame | None, str, str]:
    """Return (clean_frame, source, reason); retry a stale batch independently.

    The independent downloader must obtain the SAME unadjusted three-year
    daily-history series, not interpolate a missing completed session.
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
