"""Standalone, no-cost Alpaca PAPER-only source connection receipt.

Separate from the unfinished Stocks TRADE release branches and original
trading engine. This program uses ONLY the market-data endpoint, performs
one 30+ minute delayed historical daily AAPL SIP read and never submits
paper or real trades. Provider-authenticated access is NOT exchange-price
certification, approved trading research, or commercial redistribution rights.
No keys or raw prices are written to an artifact or logged.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

HOST = "https://data.alpaca.markets"
PAPER_ACCOUNT_URL = "https://paper-api.alpaca.markets/v2/account"
PATH = "/v2/stocks/AAPL/bars"
MAX_BODY_BYTES = 1_000_000


class NoCrossHostRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Disallow ALL redirects, which could leak paper API headers to a
        # different destination. HTTPError is handled without logging body.
        return None


def _now_utc(moment=None):
    if moment is None:
        return datetime.now(timezone.utc)
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError("UTC_OFFSET_REQUIRED")
    return moment.astimezone(timezone.utc)


def check_free_paper_source(now=None, environ=None, opener=None):
    moment = _now_utc(now)
    settings = os.environ if environ is None else environ
    key = settings.get("ALPACA_KEY_ID", "")
    secret = settings.get("ALPACA_SECRET_KEY", "")
    rights = settings.get("ALPACA_PERSONAL_RESEARCH_RIGHTS_CONFIRMED") == "1"
    report = {
        "observed_utc": moment.isoformat(),
        "source": "ALPACA_PAPER_KEY_HISTORICAL_SIP_RESEARCH_ONLY",
        "status": "NOT_CONNECTED",
        "free_plan_assumed_not_billed": True,
        "credentials_present": bool(key and secret),
        "personal_research_rights_confirmed_by_owner": rights,
        "historical_feed_requested": "sip",
        "exchange_prices_certified": False,
        "commercial_publication_rights_verified": False,
        "approved_trade_signal": False,
        "paper_order_placed": False,
        "live_order_placed": False,
        "price_rows_exported": 0,
    }
    if not (key and secret):
        report["status"] = "BLOCKED_MISSING_PAPER_API_CREDENTIALS"
        return report
    if not rights:
        report["status"] = "BLOCKED_OWNER_RESEARCH_PERMISSION_NOT_CONFIRMED"
        return report

    start = moment - timedelta(days=50)
    # Basic plan SIP historical access excludes the latest 15 minutes.
    # A 45-minute buffer prevents a near-live paid entitlement request.
    end = moment - timedelta(minutes=45)
    params = urllib.parse.urlencode({
        "timeframe": "1Day", "feed": "sip", "adjustment": "raw",
        "start": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "end": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "limit": 100,
    })
    url = HOST + PATH + "?" + params
    req = urllib.request.Request(
        url,
        headers={
            "APCA-API-KEY-ID": key,
            "APCA-API-SECRET-KEY": secret,
            "Accept": "application/json",
        },
        method="GET",
    )
    # Verify the keys belong to the PAPER environment first. The account
    # endpoint is GET-only; no account identity, balances or buying power
    # are retained in logs, reports or artifacts.
    open_request = opener or urllib.request.build_opener(NoCrossHostRedirect()).open
    paper_req = urllib.request.Request(
        PAPER_ACCOUNT_URL,
        headers={
            "APCA-API-KEY-ID": key,
            "APCA-API-SECRET-KEY": secret,
            "Accept": "application/json",
        },
        method="GET",
    )
    try:
        with open_request(paper_req, timeout=15) as account_response:
            if account_response.status != 200:
                report["status"] = "BLOCKED_NOT_VERIFIED_AS_PAPER_KEYS"
                return report
            account_data = account_response.read(100_000)
        account = json.loads(account_data)
        if not isinstance(account, dict) or not account.get("id"):
            report["status"] = "BLOCKED_UNRECOGNISED_PAPER_ACCOUNT_RESPONSE"
            return report
        report["paper_account_verified"] = True
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError,
            OSError, ValueError, TypeError):
        report["status"] = "BLOCKED_PAPER_ACCOUNT_CREDENTIALS_UNVERIFIED"
        return report

    # Only the historical market-data GET after paper verification.
    # NO POST, live API trading URL, balances or order endpoint exists.
    try:
        with open_request(req, timeout=15) as response:
            status = response.status
            content = response.read(MAX_BODY_BYTES + 1)
    except urllib.error.HTTPError as exc:
        report["status"] = (
            "BLOCKED_NO_FREE_HISTORICAL_ENTITLEMENT"
            if exc.code in (401, 403, 422) else
            "BLOCKED_RATE_LIMIT" if exc.code == 429 else
            "BLOCKED_SOURCE_HTTP_OR_REDIRECT"
        )
        report["http_status"] = exc.code
        return report
    except (urllib.error.URLError, TimeoutError, OSError):
        report["status"] = "BLOCKED_SOURCE_NETWORK_FAILURE"
        return report
    report["http_status"] = status
    if status != 200:
        report["status"] = "BLOCKED_SOURCE_HTTP_OR_REDIRECT"
        return report
    if len(content) > MAX_BODY_BYTES:
        report["status"] = "BLOCKED_SOURCE_RESPONSE_OVERSIZE"
        return report
    try:
        payload = json.loads(content)
        bars = payload.get("bars", [])
        if not isinstance(bars, list) or not bars or len(bars) > 100:
            raise ValueError("BARS_EMPTY_OR_BAD_SCHEMA")
        dates = []
        for bar in bars:
            timestamp = datetime.fromisoformat(bar["t"].replace("Z", "+00:00"))
            if timestamp.tzinfo is None or timestamp > end:
                raise ValueError("BAR_OUTSIDE_CLOSED_SOURCE_WINDOW")
            o, h, l, c, v = (float(bar[k]) for k in ("o", "h", "l", "c", "v"))
            if min(o, h, l, c) <= 0 or v < 0 or h < max(o, l, c) or l > min(o, h, c):
                raise ValueError("INVALID_OHLCV_GEOMETRY")
            dates.append(timestamp)
        if len(set(dates)) != len(dates):
            raise ValueError("DUPLICATE_SOURCE_BAR_TIMESTAMP")
    except (ValueError, TypeError, KeyError, AttributeError):
        report["status"] = "BLOCKED_INVALID_PROVIDER_BAR_SCHEMA"
        return report
    report.update({
        "status": "PAPER_CREDENTIALS_AUTHENTICATED_SOURCE_ONLY",
        "source_response_sha256": hashlib.sha256(content).hexdigest(),
        "historical_bars_observed": len(bars),
        "first_bar_utc": min(dates).isoformat(),
        "last_bar_utc": max(dates).isoformat(),
        "end_requested_utc": end.isoformat(),
        "price_rows_exported": 0,
    })
    return report


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--output", type=Path, default=Path("alpaca_free_connection_receipt.json"))
    args = p.parse_args()
    report = check_free_paper_source()
    args.output.write_text(json.dumps(report, sort_keys=True, indent=2) + "\n")
    print(json.dumps({
        "status": report["status"],
        "authenticated_source": report["status"] == "PAPER_CREDENTIALS_AUTHENTICATED_SOURCE_ONLY",
        "historical_bars_observed": report.get("historical_bars_observed", 0),
        "paper_orders": 0,
        "live_orders": 0,
    }, sort_keys=True))


if __name__ == "__main__":
    main()
