"""Real read-only Nasdaq first-party SOURCE AVAILABILITY research.

Nasdaq Trader Symbol Directory is an exchange-published listing file, NOT
per-security OHLCV, issuer financial approval, historical membership or a
market-data licence. A public Nasdaq historical endpoint response, even 200,
is NOT permission to redistribute or use as a paid trade feed.

No client tokens, login bypasses, retrying access denials, identity spoofing,
broker requests, price/cache mutation or BUY creation.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
from datetime import datetime, timezone
from urllib.parse import urlencode
from pathlib import Path

import requests

NASDAQ_SYMBOL_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
NASDAQ_HISTORICAL_ENDPOINT = "https://api.nasdaq.com/api/quote/AAPL/historical"
EXAMINED = ("AAPL", "MSFT", "NVDA", "GOOGL", "AMZN")
MAX_BYTES = 2_000_000
HEADERS = {"User-Agent": "CL-Signal-source-provenance-research/1.0 (read-only; no automated trading)"}


def listing_receipt(raw: bytes, observed_at: datetime) -> dict:
    """Check official directory text structure, footer and actual sample entries."""
    result = {
        "data_type": "EXCHANGE_LISTING_DIRECTORY_NOT_PRICES",
        "status": "UNVERIFIED_FORMAT",
        "http_source": NASDAQ_SYMBOL_URL,
        "response_sha256": hashlib.sha256(raw).hexdigest(),
        "listing_symbols_observed": [],
        "file_created_at_unverified": None,
        "issuer_prices_authenticated": False,
        "original_historical_membership_verified": False,
        "licence_for_trading_use_verified": False,
        "approved_trade_buy": False,
        "observed_at_utc": observed_at.isoformat(),
    }
    try:
        lines=raw.decode("utf-8-sig").strip().splitlines()
        if not lines or len(lines)<100:
            raise ValueError("NOT_NASDAQ_LISTING_TEXT")
        header=lines[0].split("|")
        expected={"Symbol","Security Name","Market Category","Test Issue","Financial Status"}
        if not expected.issubset(set(header)):
            raise ValueError("MISSING_OFFICIAL_SYMBOL_FIELDS")
        created=[line for line in lines[-3:] if line.startswith("File Creation Time:")]
        if len(created)!=1:
            raise ValueError("MISSING_OR_AMBIGUOUS_CREATION_FOOTER")
        result["file_created_at_unverified"]=created[0].split("|")[0]
        seen={}
        for raw_line in lines[1:]:
            if raw_line.startswith("File Creation Time:"):
                continue
            columns=raw_line.split("|")
            if len(columns)<len(header):
                continue
            item=dict(zip(header,columns))
            symbol=str(item["Symbol"]).upper().strip()
            if symbol in EXAMINED:
                if symbol in seen:
                    raise ValueError("DUPLICATE_LISTING_SYMBOL")
                seen[symbol]={
                    "symbol": symbol,
                    "test_issue": item["Test Issue"],
                    "market_category": item["Market Category"],
                    "financial_status": item["Financial Status"],
                    "exchange_listing_observed": (
                        item["Test Issue"]=="N" and item["Market Category"] in {"Q","G","S"}
                    ),
                }
        result["listing_symbols_observed"]=[seen[key] for key in EXAMINED if key in seen]
        result["status"]=(
            "OFFICIAL_FORMAT_CURRENT_LISTING_EVIDENCE_ONLY"
            if len(seen)==len(EXAMINED)
            and all(z["exchange_listing_observed"] for z in seen.values())
            else "INCOMPLETE_OR_NONSTANDARD_LISTING_EVIDENCE"
        )
    except (UnicodeError,ValueError,KeyError,TypeError) as exc:
        result["status"]="BLOCKED_UNRECOGNISED_DIRECTORY"
        result["reason"]=type(exc).__name__ + ":" + str(exc)[:140]
    return result


def _bounded_get(url: str, *, session=None, params=None) -> tuple[int | None,bytes | None,str]:
    sender=session or requests
    try:
        response=sender.get(url,params=params,headers=HEADERS,timeout=12,
                            allow_redirects=False)
        code=int(response.status_code)
        if code!=200:
            return code,None,"HTTP_NOT_SUCCESS"
        typ=str(response.headers.get("Content-Type","")).lower()
        if not ("text/" in typ or "json" in typ or "octet-stream" in typ):
            return code,None,"UNEXPECTED_CONTENT_TYPE"
        data=response.content
        if len(data)>MAX_BYTES:
            return code,None,"RESPONSE_TOO_LARGE"
        return code,data,"RECEIVED"
    except (requests.RequestException,ValueError,TypeError) as exc:
        return None,None,"TRANSPORT_"+type(exc).__name__


def probe(session=None, now=None) -> dict:
    now=now or datetime.now(timezone.utc)
    result={
        "captured_at_utc":now.isoformat(),
        "scope":"READ_ONLY_EXCHANGE_PRIMARY_LISTING_AND_PUBLIC_PRICE_ACCESS_PROBE",
        "price_feed_independently_authenticated":False,
        "authorised_price_feed_for_product":False,
        "approved_paper_entries":0,"broker_orders":0,
        "listing":{},"nasdaq_public_history":{},
        "next_gate":"Owner-approved licensed first-party/authorised distributor OHLCV, dated ISIN/symbol/venue/currency/adjustments, independently signed source provenance and full original as-of Trade gates",
    }
    code,raw,why=_bounded_get(NASDAQ_SYMBOL_URL,session=session)
    if raw:
        result["listing"]=listing_receipt(raw,now)
        result["listing"]["http_status"]=code
    else:
        result["listing"]={"status":"UNKNOWN_SOURCE_UNAVAILABLE","http_status":code,
                           "reason":why,"issuer_prices_authenticated":False}
    # Only one price-page query with ordinary research identity, no attempts to
    # circumvent access restrictions; a 200 is NOT a licence to trade or redistribute.
    code,raw,why=_bounded_get(NASDAQ_HISTORICAL_ENDPOINT,session=session,
                              params={"assetclass":"stocks","limit":5})
    research={"status":"PRICE_ACCESS_UNKNOWN","http_status":code,"reason":why,
              "official_licence_or_entitlement_confirmed":False,
              "price_feed_authenticated_for_trading":False}
    if raw:
        research["response_sha256"]=hashlib.sha256(raw).hexdigest()
        try:
            body=json.loads(raw)
            rows=((body.get("data") or {}).get("tradesTable") or {}).get("rows")
            if isinstance(rows,list) and rows:
                research["status"]="PUBLIC_JSON_ROWS_OBSERVED_NOT_LICENSED"
                research["rows_observed"]=len(rows)
                research["schema_keys"]=sorted(rows[0])[:20]
            else:
                research["status"]="NO_USABLE_PUBLIC_HISTORICAL_ROWS"
        except (ValueError,TypeError,AttributeError,KeyError):
            research["status"]="PUBLIC_RESPONSE_NOT_USABLE_JSON"
    else:
        research["status"]=(
            "ACCESS_REQUIRES_PERMISSION_OR_UNAVAILABLE" if code in (401,403,429)
            else "PUBLIC_HISTORICAL_ENDPOINT_UNAVAILABLE"
        )
    result["nasdaq_public_history"]=research
    return result


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--output",type=Path,default=Path("exchange_source_access_probe.json"))
    args=p.parse_args()
    report=probe()
    args.output.write_text(json.dumps(report,sort_keys=True,indent=2)+"\n")
    print(json.dumps({"listing":report["listing"].get("status"),
                      "history":report["nasdaq_public_history"].get("status"),
                      "readiness":"PRICE_FEED_NOT_AUTHENTICATED"},indent=2))


if __name__=="__main__":
    main()
