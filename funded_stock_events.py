"""Kraken Funded STOCKS ONLY: earnings estimates and manual event-risk checks.

Yahoo data is UNVERIFIED and can be wrong or unavailable. Corporate-event
sources must be checked independently. This module does not make official claims.
"""
from __future__ import annotations

from datetime import date, datetime
from urllib.parse import urlparse
import pandas as pd


def event_date(value):
    """Coerce scalar timestamps, including provider timezone-aware values."""
    try:
        parsed = pd.to_datetime(value, errors="coerce")
        if pd.isna(parsed):
            return None
        if isinstance(parsed, pd.Timestamp):
            if parsed.tzinfo is not None:
                parsed = parsed.tz_convert("America/New_York")
            return parsed.date()
        if isinstance(parsed, datetime):
            return parsed.date()
        if isinstance(parsed, date):
            return parsed
    except (ValueError, TypeError, OverflowError):
        return None
    return None


def next_future_date(values, today):
    dates = [event_date(v) for v in values]
    future = [d for d in dates if d is not None and d >= today]
    return min(future) if future else None


def earnings_window(value, today, warning_sessions=5):
    """Approximate weekday count; exchange holidays are not modeled."""
    upcoming = event_date(value)
    if upcoming is None:
        return "DATE UNKNOWN"
    if upcoming < today:
        return "DATE EXPIRED"
    trading_days = len(pd.bdate_range(today, upcoming)) - 1
    if trading_days <= warning_sessions:
        return "WITHIN 5 SESSIONS"
    return "OUTSIDE 5 SESSIONS (UNVERIFIED)"


def lookup_estimated_earnings(ticker, today):
    """Return estimated next event and source; never imply official verification.

    Accept either modern yfinance Ticker.calendar dictionaries or the historical
    get_earnings_dates() data-frame index. Older reported dates are not future dates.
    """
    errors = []
    dates = []
    try:
        calendar = ticker.calendar
        if isinstance(calendar, dict):
            for key in ("Earnings Date", "earningsDate", "earnings_date"):
                value = calendar.get(key)
                if value is None:
                    continue
                dates.extend(value if isinstance(value, (list, tuple)) else [value])
            found = next_future_date(dates, today)
            if found is not None:
                return {"estimated_date": found.isoformat(),
                        "source": "Yahoo calendar (UNVERIFIED)",
                        "quality": "ESTIMATE AVAILABLE"}
    except Exception as exc:
        errors.append("CALENDAR " + type(exc).__name__)
    try:
        earnings = ticker.get_earnings_dates(limit=12)
        if isinstance(earnings, pd.DataFrame) and not earnings.empty:
            found = next_future_date(earnings.index, today)
            if found is not None:
                return {"estimated_date": found.isoformat(),
                        "source": "Yahoo earnings schedule (UNVERIFIED)",
                        "quality": "ESTIMATE AVAILABLE"}
    except Exception as exc:
        errors.append("SCHEDULE " + type(exc).__name__)
    return {"estimated_date": "", "source": "Yahoo (NO CURRENT ESTIMATE)",
            "quality": "; ".join(errors) if errors else "NO UPCOMING DATE FOUND"}



def fetch_fmp_earnings_calendar(api_key, symbols, today, http_get=None):
    """Optional independent *estimated* earnings calendar, one HTTP request.

    The provider may require a paid entitlement. Failure is reported, never
    turned into an official-event confirmation or an invented date.
    """
    if not api_key or not str(api_key).strip():
        return {}, "FMP API KEY NOT CONFIGURED"
    if http_get is None:
        from requests import get as http_get
    try:
        end = today + pd.Timedelta(days=89)
        response = http_get(
            "https://financialmodelingprep.com/stable/earnings-calendar",
            params={
                "from": today.isoformat(), "to": end.isoformat(),
                "apikey": str(api_key).strip(),
            },
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
        rows = (payload.get("data") or payload.get("results"))
        if isinstance(payload, dict) else payload
        if not isinstance(rows, list):
            return {}, "FMP RESPONSE NOT A CALENDAR LIST"
        universe = {str(symbol).upper() for symbol in symbols}
        found = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            symbol = str(row.get("symbol") or "").upper()
            when = event_date(row.get("date"))
            if symbol not in universe or when is None or when < today:
                continue
            existing = found.get(symbol)
            if existing is None or when.isoformat() < existing["estimated_date"]:
                found[symbol] = {
                    "estimated_date": when.isoformat(),
                    "source": "FMP earnings calendar (UNVERIFIED)",
                    "quality": "ESTIMATE AVAILABLE",
                }
        return found, "FMP CALENDAR RETURNED"
    except Exception as exc:
        # No request URL, response body or API key in diagnostic messages.
        code = getattr(getattr(exc, "response", None), "status_code", None)
        if code:
            return {}, f"FMP HTTP {code}"
        return {}, f"FMP FETCH FAILED: {type(exc).__name__}"


def validate_official_event_verification(record, ticker, today, now):
    """Checks record completeness/freshness, not the truth of a web page."""
    if not isinstance(record, dict) or str(record.get("ticker", "")).upper() != str(ticker).upper():
        return None, "VERIFY EARNINGS"
    if record.get("user_confirmed") is not True:
        return None, "VERIFY EARNINGS"
    source = str(record.get("source_url") or "").strip()
    parsed = urlparse(source)
    if parsed.scheme != "https" or not parsed.hostname or "." not in parsed.hostname:
        return None, "OFFICIAL SOURCE URL REQUIRED"
    if parsed.hostname.lower().removeprefix("www.") in {
        "finance.yahoo.com", "google.com", "www.google.com",
        "marketwatch.com", "nasdaq.com", "investing.com",
    }:
        return None, "COMPANY INVESTOR-RELATIONS SOURCE REQUIRED"
    verified = event_date(record.get("next_date"))
    if verified is None:
        return None, "CONFIRMED EVENT DATE REQUIRED"
    checked_at = pd.to_datetime(record.get("checked_at"), errors="coerce", utc=True)
    if pd.isna(checked_at):
        return None, "STALE EARNINGS VERIFICATION"
    now_utc = pd.Timestamp(now)
    now_utc = now_utc.tz_localize("UTC") if now_utc.tzinfo is None else now_utc.tz_convert("UTC")
    age_seconds = (now_utc - checked_at).total_seconds()
    if not 0 <= age_seconds <= 24 * 3600:
        return None, "STALE EARNINGS VERIFICATION"
    if checked_at.tz_convert("Europe/London").date() != today:
        return None, "STALE EARNINGS VERIFICATION"
    return verified, "MANUAL SOURCE CHECKED"


def earnings_trade_gate(row, verified_records, today, now):
    """Conservative: no green trading status on unknown or near-term events."""
    ticker = str(row.get("Ticker") or "").upper()
    estimate = event_date(row.get("Next earnings estimate"))
    if estimate is not None and earnings_window(estimate, today) == "WITHIN 5 SESSIONS":
        return "🔴 FUNDED PASS · EARNINGS WINDOW"
    verified, error = validate_official_event_verification(
        (verified_records or {}).get(ticker), ticker, today, now,
    )
    if verified is None:
        return "🟡 FUNDED WATCH · " + error
    if earnings_window(verified, today) == "WITHIN 5 SESSIONS":
        return "🔴 FUNDED PASS · EARNINGS WINDOW"
    if verified < today:
        return "🟡 FUNDED WATCH · VERIFY UPCOMING EARNINGS"
    # A near-term provider estimate which disagrees sharply with the manually
    # confirmed corporate date needs review, not blind promotion to READY.
    if estimate is not None:
        weekdays_to_estimate = len(pd.bdate_range(today, estimate)) - 1 if estimate >= today else 999
        if weekdays_to_estimate <= 15 and abs((verified - estimate).days) >= 7:
            return "🟡 FUNDED WATCH · EARNINGS DATE CONFLICT"
    return ""
