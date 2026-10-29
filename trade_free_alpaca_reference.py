"""No-cost research intake for Alpaca FREE historical US SIP daily bars.

Only read historical bar data that is >=15 minutes old, explicitly request
feed=sip and adjustment=raw. The Basic tier and paper-only accounts are
free according to provider docs; availability still depends on account
entitlements and individual noncommercial permissions.

No trading endpoint, card, subscription upgrade, credential persistence,
pricing/licence assumptions, market-data publishing or BUY/paper fill.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime,timezone
from pathlib import Path

import pandas as pd
import requests

from trade_completed_bar_guard import validate_completed_trade_frame

ALPACA_BARS = "https://data.alpaca.markets/v2/stocks/{symbol}/bars"
SYMBOLS = ("AAPL", "MSFT", "NVDA", "GOOGL", "AMZN")
MAX_PAGES = 4
MIN_DELAY_MINUTES = 30  # conservative buffer beyond the Basic 15m restriction
START_DAYS = 1450  # ~3.97y, enough for technical lookback under holiday/short years
QUOTE_CURRENCY = "USD"


def _utc(value):
    t=pd.Timestamp(value)
    if t.tzinfo is None:
        raise ValueError("CAPTURE_TIMESTAMP_WITH_OFFSET_REQUIRED")
    return t.tz_convert("UTC")


def _safe_json_digest(payload):
    return hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(",",":")).encode()).hexdigest()


def normalize_daily_bars(bars:list[dict]) -> pd.DataFrame:
    values=[]
    for bar in bars:
        if not isinstance(bar,dict):
            raise ValueError("UNRECOGNISED_ALPACA_BAR_SCHEMA")
        try:
            t=_utc(bar["t"])
            o,h,l,c,v=(float(bar[k]) for k in ("o","h","l","c","v"))
        except (ValueError,TypeError,KeyError,OverflowError) as exc:
            raise ValueError("INVALID_ALPACA_OHLCV_BAR") from exc
        values.append((t,o,h,l,c,v))
    if not values:
        raise ValueError("NO_ALPACA_BARS")
    out=pd.DataFrame(values,columns=("timestamp","Open","High","Low","Close","Volume"))
    if out["timestamp"].duplicated().any():
        raise ValueError("DUPLICATE_ALPACA_DAILY_BAR_TIMESTAMP")
    # RFC3339 timestamp retained; venue day conversion happens in the
    # approved XLON/XNAS calendar guard, not by blindly slicing UTC strings.
    return out.set_index("timestamp").sort_index()


def read_one_symbol(symbol:str, *, key_id:str,secret:str, at, session=None)->dict:
    """Provider-authenticated read only; authorization/rights still not asserted."""
    now=_utc(at)
    result={
        "ticker":symbol,"venue":"nasdaq","source":"ALPACA_HISTORICAL_SIP_DELAYED",
        "feed_requested":"sip","adjustment_requested":"raw",
        "quoted_currency":QUOTE_CURRENCY,
        "captured_utc":now.isoformat(),
        "status":"BLOCKED",
        "completed_session":None,"source_ohlcv_sha256":None,
        "provider_response_sha256":None,
        "authorized_price_research_authenticated":False,
        "licence_for_public_display_or_redistribution":False,
        "live_trade_buy_authorized":False,"paper_order_authorized":False,
        "attempted_trading_order":False,
        "bars_seen":0,"pages":0,
    }
    if symbol not in SYMBOLS:
        result["status"]="UNAPPROVED_SAMPLE_SYMBOL"
        return result
    if not key_id or not secret:
        result["status"]="BLOCKED_NO_FREE_ALPACA_API_CREDENTIALS"
        return result
    sender=session or requests
    end=now-pd.Timedelta(minutes=MIN_DELAY_MINUTES)
    start=now-pd.Timedelta(days=START_DAYS)
    params={
        "timeframe":"1Day","feed":"sip","adjustment":"raw",
        "start":start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "end":end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "limit":1000,
    }
    page_token=None
    allbars=[]
    hashes=[]
    for page_num in range(MAX_PAGES):
        if page_token:
            params["page_token"]=page_token
        try:
            response=sender.get(
                ALPACA_BARS.format(symbol=symbol),params=params,
                headers={"APCA-API-KEY-ID":key_id,"APCA-API-SECRET-KEY":secret,
                         "Accept":"application/json"},
                timeout=20,allow_redirects=False)
            code=int(response.status_code)
            result["last_http_status"]=code
            if code!=200:
                result["status"]=(
                    "BLOCKED_PROVIDER_NOT_ENTITLED"
                    if code in (401,403,422) else
                    "BLOCKED_RATE_LIMIT" if code==429 else
                    "BLOCKED_PROVIDER_HTTP_UNAVAILABLE")
                return result
            doc=response.json()
        except (requests.RequestException,ValueError,TypeError,AttributeError):
            result["status"]="BLOCKED_PROVIDER_TRANSPORT_OR_JSON"
            return result
        if not isinstance(doc,dict) or not isinstance(doc.get("bars"),list):
            result["status"]="BLOCKED_ALPACA_SCHEMA_MISMATCH"
            return result
        hashes.append(_safe_json_digest(doc))
        allbars.extend(doc["bars"])
        result["pages"]=page_num+1
        next_page=doc.get("next_page_token")
        if not next_page:
            break
        if next_page==page_token or not isinstance(next_page,str):
            result["status"]="BLOCKED_PAGINATION_INTEGRITY"
            return result
        page_token=next_page
    else:
        result["status"]="BLOCKED_INCOMPLETE_PAGINATION"
        return result
    result["bars_seen"]=len(allbars)
    result["provider_response_sha256"]=_safe_json_digest(hashes)
    try:
        bars=normalize_daily_bars(allbars)
    except ValueError:
        result["status"]="BLOCKED_SOURCE_BAR_SCHEMA_OR_GEOMETRY"
        return result
    complete,receipt=validate_completed_trade_frame(bars,"nasdaq",now)
    result["calendar_check"]=receipt
    if complete is None:
        result["status"]="BLOCKED_"+receipt["reason"]
        return result
    result["completed_session"]=receipt["last_completed_session"]
    result["source_ohlcv_sha256"]=receipt["last_completed_ohlcv_sha256"]
    result["validated_completed_bars"]=len(complete)
    # Positive means API credentials fetched a plausible SIP dataset. It does
    # NOT mean exchange-certified NOCP, commercial licence or a Trade BUY.
    result["status"]="PROVIDER_SIP_SESSION_STRUCTURALLY_VALID_PENDING_COMPARISON"
    result["authorized_price_research_authenticated"]=True
    return result


def cross_check_yahoo(alpaca_raw: pd.DataFrame, yahoo_raw:pd.DataFrame,
                      *, max_close_diff_bps:float=50.0) ->dict:
    """Exact session/field paired comparison, no self-authorizing price patch."""
    def index_days(df):
        out=df.copy()
        if not isinstance(out.index,pd.DatetimeIndex):
            raise ValueError("DATED_INDEX_REQUIRED")
        idx=pd.DatetimeIndex([pd.Timestamp(i).tz_localize(None).date() for i in out.index])
        out.index=idx
        if out.index.has_duplicates:
            raise ValueError("DUPLICATE_DATE_INDEX")
        return out
    a=index_days(alpaca_raw)
    b=index_days(yahoo_raw)
    dates=sorted(set(a.index)&set(b.index))[-252:]
    result={
        "status":"BLOCKED_UNPAIRED_SOURCE_HISTORY",
        "same_venue_session_prices_proven":False,
        "licensed_for_publication":False,
        "cross_source_comparison_sessions":len(dates),
        "max_abs_close_difference_bps":None,
        "mismatched_sessions":[],
    }
    if len(dates)<252:
        return result
    largest=0.0
    discrepant=[]
    for date in dates:
        try:
            left=float(a.loc[date,"Close"])
            right=float(b.loc[date,"Close"])
            if left<=0 or right<=0:
                raise ValueError("NON_POSITIVE_PRICE")
            gap=abs(left/right-1)*10000
        except (ValueError,TypeError,KeyError,OverflowError):
            result["status"]="BLOCKED_PRICE_SCHEMA"
            return result
        largest=max(largest,gap)
        if gap>max_close_diff_bps:
            discrepant.append(pd.Timestamp(date).date().isoformat())
    result["max_abs_close_difference_bps"]=round(largest,4)
    result["mismatched_sessions"]=discrepant[:30]
    result["status"]=(
        "SOURCE_MISMATCH_REQUIRES_HUMAN_REVIEW" if discrepant
        else "PAIRWISE_CLOSES_WITHIN_TOLERANCE_NOT_EXCHANGE_CERTIFICATION")
    return result


def run():
    now=datetime.now(timezone.utc)
    key_id=os.environ.get("ALPACA_KEY_ID","")
    secret=os.environ.get("ALPACA_SECRET_KEY","")
    enabled=os.environ.get("ALPACA_PERSONAL_RESEARCH_RIGHTS_CONFIRMED","")=="1"
    if enabled and key_id and secret:
        rows=[read_one_symbol(symbol,key_id=key_id,secret=secret,at=now)
              for symbol in SYMBOLS]
    else:
        rows=[read_one_symbol(symbol,key_id="",secret="",at=now) for symbol in SYMBOLS]
    return {
        "captured_utc":now.isoformat(),
        "source":"ALPACA_FREE_BASIC_DELAYED_HISTORICAL_SIP_PERSONAL_RESEARCH_ONLY",
        "credential_present":bool(key_id and secret),
        "permissions_confirmed_by_owner":enabled,
        "paid_subscription_initiated":False,
        "broker_order_or_paper_fill_initiated":False,
        "license_public_website":False,
        "independent_price_session_authenticated":sum(
            x["status"]=="PROVIDER_SIP_SESSION_STRUCTURALLY_VALID_PENDING_COMPARISON"
            for x in rows),
        "paper_entry_approved":0,
        "source_rows":rows,
        "next_step":"Only if credentials and rights are supplied, compare original SIP bars to Yahoo; complete issuer/event/FX and original Trade paper gate before any paper order",
    }


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--output",type=Path,default=Path("trade_free_alpaca_source_receipt.json"))
    args=ap.parse_args()
    report=run()
    args.output.write_text(json.dumps(report,sort_keys=True,indent=2)+"\n")
    print(json.dumps({
        "source":report["source"],
        "authenticated_price_sessions":report["independent_price_session_authenticated"],
        "source_states":[x["status"] for x in report["source_rows"]],
        "paper_orders":0,
    },indent=2))


if __name__=="__main__":
    main()
