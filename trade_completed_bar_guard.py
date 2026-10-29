"""Research candidate: source-bar session validation for Stocks TRADE caches.

This is NOT financial/event/FX evidence, does NOT make a BUY and is not
deployed. A last completed exchange session is checked against a real venue
calendar with a conservative vendor-publication grace; ambiguous venues fail
closed. Yahoo-reported data is not independently exchange certified.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

import exchange_calendars as xcals
import pandas as pd

GRACE_MINUTES = 45
MIN_HISTORY_BARS = 252
PRICE_SOURCE = "YAHOO_FINANCE_YFINANCE_UNADJUSTED_DAILY"
# Only explicit supported calendars. A secondary listing's exact trading venue
# must be reconciled independently before allowing its signals.
VENUE_CALENDARS = {
    "nasdaq": "XNAS",
    "nyse": "XNYS",
    "lse": "XLON",
    "lse_aim": "XLON",
    "tsx": "XTSE",
    "xetra": "XETR",
    "euronext_amsterdam": "XAMS",
    "euronext_brussels": "XBRU",
    "euronext_lisbon": "XLIS",
    "euronext_paris": "XPAR",
    "madrid": "XMAD",
    "six": "XSWX",
    "vienna": "XWBO",
    # gettex is not equivalent to XETR or XFRA; OTC has no validated
    # session/currency mapping. NEVER substitute another exchange calendar.
}
REQUIRED = ("Open", "High", "Low", "Close", "Volume")


def _utc_timestamp(now=None) -> pd.Timestamp:
    value = pd.Timestamp.now(tz="UTC") if now is None else pd.Timestamp(now)
    if value.tzinfo is None:
        raise ValueError("Current time must be timezone aware")
    return value.tz_convert("UTC")


def expected_completed_session(venue: str, now=None) -> tuple[str, str]:
    """Return (session ISO date, calendar ID) after actual close + 45m grace."""
    calendar_name = VENUE_CALENDARS.get(venue)
    if not calendar_name:
        raise ValueError("UNSUPPORTED_EXCHANGE_CALENDAR")
    cal = xcals.get_calendar(calendar_name)
    utc_now = _utc_timestamp(now)
    local_now = utc_now.tz_convert(cal.tz)
    sessions = cal.sessions_in_range(
        pd.Timestamp(local_now.date()) - pd.Timedelta(days=32),
        pd.Timestamp(local_now.date()),
    )
    completed = [
        session.date()
        for session in sessions
        if utc_now >= cal.session_close(session) + pd.Timedelta(minutes=GRACE_MINUTES)
    ]
    if not completed:
        raise ValueError("NO_COMPLETED_EXCHANGE_SESSION")
    return completed[-1].isoformat(), calendar_name


def _session_date(index_value, cal) -> pd.Timestamp:
    value = pd.Timestamp(index_value)
    # Yahoo's naive daily indices represent the local calendar day, NOT UTC.
    if value.tzinfo is not None:
        value = value.tz_convert(cal.tz)
    return pd.Timestamp(value.date())


def validate_completed_trade_frame(
    frame: pd.DataFrame | None, venue: str, now=None,
) -> tuple[pd.DataFrame | None, dict]:
    """Return (validated completed OHLCV, diagnostic); never a Trade action.

    Strict: reject stale, non-session and malformed source rows, strip only a
    genuine current session's unfinished candle, and require 252 valid bars.
    A source receipt is a checksum/provenance, NOT independent exchange proof.
    """
    utc_now = _utc_timestamp(now)
    stamp = utc_now.isoformat()
    result = {
        "status": "BLOCKED", "reason": "SOURCE_DATA_UNKNOWN", "venue": venue,
        "price_source": PRICE_SOURCE, "source_observed_at_utc": stamp,
        "exchange_calendar": VENUE_CALENDARS.get(venue) or "UNVERIFIED",
        "expected_completed_session": None, "last_completed_session": None,
        "excluded_incomplete_latest_bar": False,
        "last_completed_ohlcv_sha256": None, "accepted_bars": 0,
        "independently_exchange_verified": False,
    }
    if venue not in VENUE_CALENDARS:
        result["reason"] = "UNSUPPORTED_EXCHANGE_CALENDAR"
        return None, result
    try:
        expected, calendar_name = expected_completed_session(venue, utc_now)
        cal = xcals.get_calendar(calendar_name)
    except (ValueError, KeyError, RuntimeError, OverflowError) as exc:
        result["reason"] = "EXCHANGE_CALENDAR_UNAVAILABLE_" + type(exc).__name__
        return None, result
    result["expected_completed_session"] = expected

    if frame is None or frame.empty or not set(REQUIRED).issubset(frame.columns):
        result["reason"] = "PRICE_DATA_OR_OHLCV_COLUMNS_MISSING"
        return None, result
    try:
        data = frame.copy()
        if not isinstance(data.index, pd.DatetimeIndex):
            raise ValueError("NON_DATETIME_INDEX")
        if data.index.has_duplicates:
            raise ValueError("DUPLICATE_SOURCE_DATES")
        data.index = pd.DatetimeIndex([_session_date(x, cal) for x in data.index])
        if data.index.has_duplicates:
            raise ValueError("DUPLICATE_EXCHANGE_DATES")
        data = data.sort_index()
        for key in REQUIRED:
            data[key] = pd.to_numeric(data[key], errors="coerce")
        data = data.dropna(subset=list(REQUIRED))
        if data.empty:
            raise ValueError("NO_VALID_OHLCV")
        clean = ((data["Open"] > 0) & (data["High"] > 0)
                 & (data["Low"] > 0) & (data["Close"] > 0)
                 & (data["Volume"] >= 0)
                 & (data["High"] >= data[["Open", "Low", "Close"]].max(axis=1))
                 & (data["Low"] <= data[["Open", "High", "Close"]].min(axis=1)))
        if not clean.all():
            raise ValueError("INVALID_OHLCV_PRICE_OR_VOLUME")
        today = utc_now.tz_convert(cal.tz).date()
        last = data.index[-1].date()
        expected_date = pd.Timestamp(expected).date()
        if last > today:
            raise ValueError("FUTURE_SOURCE_BAR")
        if not cal.is_session(pd.Timestamp(last)):
            raise ValueError("NON_EXCHANGE_SESSION_BAR")
        if last > expected_date:
            if last != today:
                raise ValueError("UNEXPECTED_UNCOMPLETED_BAR")
            # Only today's in-progress bar, never an older or invented one.
            data = data.iloc[:-1]
            result["excluded_incomplete_latest_bar"] = True
        if data.empty:
            raise ValueError("NO_COMPLETED_OHLCV")
        last = data.index[-1].date()
        if last != expected_date:
            raise ValueError("STALE_OR_MISSING_COMPLETED_BAR")
        if len(data) < MIN_HISTORY_BARS:
            raise ValueError("INSUFFICIENT_COMPLETED_HISTORY")
        last_row = data.iloc[-1]
        checksum = {
            "session": last.isoformat(),
            "open": float(last_row["Open"]), "high": float(last_row["High"]),
            "low": float(last_row["Low"]), "close": float(last_row["Close"]),
            "volume": float(last_row["Volume"]),
        }
        result.update({
            "status": "PASS", "reason": "COMPLETED_SESSION_SOURCE_BAR_PRESENT",
            "last_completed_session": last.isoformat(), "accepted_bars": len(data),
            "last_completed_ohlcv_sha256": hashlib.sha256(
                json.dumps(checksum, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest(),
        })
        return data, result
    except (TypeError, ValueError, OverflowError, KeyError) as exc:
        result["reason"] = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
        return None, result
