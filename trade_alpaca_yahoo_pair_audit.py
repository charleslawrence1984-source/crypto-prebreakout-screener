"""Private research: compare 252 paired delayed Alpaca SIP and Yahoo raw daily bars.

No raw prices, keys, account details, BUY labels, or orders are published.
PR CI is always secretless; manually trigger on main only after separate approval.
"""
import hashlib
import json
import math
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

SYMBOLS = ("AAPL", "MSFT", "NVDA", "GOOGL", "AMZN")
NY = ZoneInfo("America/New_York")
DAYS = 850
REQUIRED = 252

class Blocked(Exception):
    pass

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

def get_json(url, headers=None, opener=None):
    request = urllib.request.Request(url, headers=headers or {}, method="GET")
    op = opener or urllib.request.build_opener(NoRedirect()).open
    try:
        with op(request, timeout=20) as response:
            if response.status != 200:
                raise Blocked("PROVIDER_HTTP_FAILURE")
            raw = response.read(3000001)
    except urllib.error.HTTPError as e:
        if e.code in (401, 403, 422):
            raise Blocked("PROVIDER_AUTH_OR_ENTITLEMENT")
        if e.code == 429:
            raise Blocked("PROVIDER_RATE_LIMIT")
        raise Blocked("HTTP_OR_REDIRECT_REJECTED")
    except (urllib.error.URLError, TimeoutError, OSError):
        raise Blocked("SOURCE_NETWORK_FAILURE")
    if len(raw) > 3000000:
        raise Blocked("SOURCE_OVERSIZED")
    try:
        return json.loads(raw)
    except (ValueError, UnicodeError):
        raise Blocked("INVALID_SOURCE_JSON")

def value_bar(o, h, l, c, v):
    try:
        vals = {k:float(x) for k,x in zip(("o","h","l","c","v"), (o,h,l,c,v))}
        if not all(math.isfinite(x) for x in vals.values()):
            raise ValueError()
        if min(vals[k] for k in ("o","h","l","c")) <= 0 or vals["v"] < 0:
            raise ValueError()
        if vals["h"] < max(vals[k] for k in ("o","l","c")) or vals["l"] > min(vals[k] for k in ("o","h","c")):
            raise ValueError()
        return vals
    except (ValueError, TypeError, OverflowError):
        raise Blocked("INVALID_OHLCV")

def alpaca_normalize(bars, today):
    if not isinstance(bars, list):
        raise Blocked("INVALID_ALPACA_SCHEMA")
    out = {}
    for b in bars:
        try:
            dt = datetime.fromisoformat(b["t"].replace("Z","+00:00"))
            if dt.tzinfo is None:
                raise ValueError()
            day = dt.astimezone(NY).date().isoformat()
            if day >= today: # never use a possibly incomplete session
                continue
            row = value_bar(*(b[k] for k in ("o","h","l","c","v")))
        except (KeyError, TypeError, AttributeError, ValueError):
            raise Blocked("INVALID_ALPACA_SCHEMA")
        if day in out:
            raise Blocked("DUPLICATE_ALPACA_DAY")
        out[day] = row
    return out

def yahoo_normalize(doc, today):
    try:
        d = doc["chart"]["result"][0]
        ts = d["timestamp"]
        q = d["indicators"]["quote"][0]
        if not ts or any(len(q[k]) != len(ts) for k in ("open","high","low","close","volume")):
            raise ValueError()
        out = {}
        for i, t in enumerate(ts):
            day = datetime.fromtimestamp(int(t), timezone.utc).astimezone(NY).date().isoformat()
            if day >= today:
                continue
            row = value_bar(*(q[k][i] for k in ("open","high","low","close","volume")))
            if day in out:
                raise Blocked("DUPLICATE_YAHOO_DAY")
            out[day] = row
        action_dates = set()
        for typ in ("splits","dividends"):
            for event in (d.get("events") or {}).get(typ, {}).values():
                if "date" in event:
                    action_dates.add(datetime.fromtimestamp(int(event["date"]),timezone.utc).astimezone(NY).date().isoformat())
        return out, sorted(action_dates)
    except Blocked:
        raise
    except (ValueError, IndexError, KeyError, TypeError, AttributeError, OverflowError):
        raise Blocked("YAHOO_SCHEMA_OR_PRICE_ERROR")

def verify_paper(key, secret, opener=None):
    account = get_json("https://paper-api.alpaca.markets/v2/account", {
        "APCA-API-KEY-ID":key,"APCA-API-SECRET-KEY":secret,"Accept":"application/json"}, opener)
    if not isinstance(account,dict) or not account.get("id"):
        raise Blocked("PAPER_IDENTITY_NOT_VERIFIED")

def fetch_alpaca(symbol, now, key, secret, opener=None):
    params = {
        "timeframe":"1Day","feed":"sip","adjustment":"raw",
        "start":(now-timedelta(days=DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "end":(now-timedelta(minutes=45)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "limit":"1000"}
    rows, tokens = [], set()
    for _ in range(4):
        url = "https://data.alpaca.markets/v2/stocks/"+symbol+"/bars?"+urllib.parse.urlencode(params)
        doc = get_json(url, {"APCA-API-KEY-ID":key,"APCA-API-SECRET-KEY":secret,
                              "Accept":"application/json"}, opener)
        if not isinstance(doc,dict) or not isinstance(doc.get("bars"),list):
            raise Blocked("ALPACA_SCHEMA_ERROR")
        rows.extend(doc["bars"])
        token = doc.get("next_page_token")
        if not token:
            return alpaca_normalize(rows, now.astimezone(NY).date().isoformat())
        if not isinstance(token,str) or token in tokens:
            raise Blocked("ALPACA_PAGINATION_INTEGRITY")
        tokens.add(token)
        params["page_token"] = token
    raise Blocked("ALPACA_PAGINATION_INCOMPLETE")

def fetch_yahoo(symbol, now, opener=None):
    params = urllib.parse.urlencode({
        "period1":int((now-timedelta(days=DAYS)).timestamp()),
        "period2":int((now-timedelta(minutes=45)).timestamp()),
        "interval":"1d","events":"div,splits","includeAdjustedClose":"true"})
    doc = get_json("https://query1.finance.yahoo.com/v8/finance/chart/"+symbol+"?"+params,
                   {"Accept":"application/json","User-Agent":"Mozilla/5.0 PersonalResearch/1.0"}, opener)
    # Quote Close is non-dividend-adjusted; NEVER use indicators.adjclose.
    return yahoo_normalize(doc, now.astimezone(NY).date().isoformat())

def compare(alpaca, yahoo, corporate_days=(), required=REQUIRED):
    out = {
        "status":"BLOCKED_INSUFFICIENT_252_ALPACA_SESSIONS",
        "matched_sessions":0,"required_sessions":required,
        "missing_yahoo_dates":[],"extra_yahoo_dates":[],
        "max_price_gap_bps":None,"max_volume_gap_pct":None,
        "field_mismatch_counts":{k:0 for k in ("o","h","l","c","v")},
        "mismatch_examples":[],"corporate_action_dates":[],
        "technical_condition_disagreements":{"prior_20d_high_break":0,"above_50d_mean":0},
        "technical_disagreement_dates":[],
        "exchange_certification":False,"public_redistribution_rights":False,
        "approved_trade_signal":False,"paper_or_live_order":False}
    days = sorted(alpaca)[-required:]
    if len(days) < required:
        out["alpaca_sessions_found"] = len(days)
        return out
    missing = sorted(set(days)-set(yahoo))
    extra = sorted(d for d in yahoo if days[0] <= d <= days[-1] and d not in alpaca)
    out["matched_sessions"] = len(days)-len(missing)
    out["missing_yahoo_dates"], out["extra_yahoo_dates"] = missing[:20], extra[:20]
    if missing or extra:
        out["status"] = "BLOCKED_UNPAIRED_PROVIDER_SESSIONS"
        return out
    max_p, max_v = 0.0, 0.0
    mismatches, technical = [], []
    for day in days:
        fields = []
        for k in ("o","h","l","c"):
            gap=abs(alpaca[day][k]/yahoo[day][k]-1)*10000
            max_p=max(max_p,gap)
            if gap>5.0: # five basis points, investigate rather than auto-patch
                out["field_mismatch_counts"][k]+=1
                fields.append(k)
        av,yv=alpaca[day]["v"],yahoo[day]["v"]
        gapv=abs(av/yv-1)*100 if yv else (0 if av==0 else float("inf"))
        max_v=max(max_v,gapv)
        if gapv>2:
            out["field_mismatch_counts"]["v"]+=1
            fields.append("v")
        if fields:
            mismatches.append({"date":day,"fields":fields})
    # Sensitivity only: NOT original TRADE strategy/backtest or approved signals.
    for n in range(50,len(days)):
        date=days[n]
        flags=[]
        for src in (alpaca,yahoo):
            flags.append((src[date]["c"] > max(src[x]["h"] for x in days[n-20:n]),
                          src[date]["c"] > sum(src[x]["c"] for x in days[n-49:n+1])/50))
        diverged=[]
        for label, k in (("prior_20d_high_break",0),("above_50d_mean",1)):
            if flags[0][k]!=flags[1][k]:
                out["technical_condition_disagreements"][label]+=1
                diverged.append(label)
        if diverged:
            technical.append({"date":date,"conditions":diverged})
    out["max_price_gap_bps"]=round(max_p,3)
    out["max_volume_gap_pct"]=round(max_v,3) if math.isfinite(max_v) else None
    out["mismatch_examples"]=mismatches[:20]
    out["corporate_action_dates"]=[x for x in corporate_days if days[0]<=x<=days[-1]][:20]
    out["technical_disagreement_dates"]=technical[:20]
    out["status"]="REVIEW_PRICE_VOLUME_OR_TRIGGER_SENSITIVITY" if mismatches or technical else "RESEARCH_PAIRED_WITHIN_TOLERANCE"
    return out

def audit(now=None, environ=None, opener=None):
    now=now or datetime.now(timezone.utc)
    if not now.tzinfo or now.utcoffset() is None:
        raise ValueError("UTC_OFFSET_REQUIRED")
    now=now.astimezone(timezone.utc)
    env=environ if environ is not None else os.environ
    key,secret=env.get("ALPACA_KEY_ID",""),env.get("ALPACA_SECRET_KEY","")
    rights=env.get("ALPACA_PERSONAL_RESEARCH_RIGHTS_CONFIRMED")=="1"
    report={
        "observed_utc":now.isoformat(),"source":["ALPACA_SIP_HISTORICAL_RAW","YAHOO_UNADJUSTED_DAILY"],
        "purpose":"PRIVATE_PERSONAL_RESEARCH_ONLY","symbols":list(SYMBOLS),"rows":[],
        "credentials_present":bool(key and secret),"rights_acknowledged":rights,
        "paper_account_verified":False,"overall_status":"BLOCKED_NOT_READY",
        "subscriptions_started":False,"public_data_licence_verified":False,
        "production_signals_modified":False,"paper_or_live_orders":False}
    if not (key and secret and rights):
        report["overall_status"]="BLOCKED_MISSING_PAPER_KEYS_OR_PERMISSION"
        return report
    try:
        verify_paper(key,secret,opener)
    except Blocked as e:
        report["overall_status"]="BLOCKED_"+str(e)
        return report
    report["paper_account_verified"]=True
    for symbol in SYMBOLS:
        row={"symbol":symbol,"status":"BLOCKED","matched_sessions":0}
        try:
            a=fetch_alpaca(symbol,now,key,secret,opener)
            b,corporate=fetch_yahoo(symbol,now,opener)
            row.update(compare(a,b,corporate))
            row["alpaca_sessions_observed"]=len(a)
            row["yahoo_sessions_observed"]=len(b)
        except Blocked as e:
            row["status"]="BLOCKED_"+str(e)
        report["rows"].append(row)
    report["matched_session_pairs"]=sum(row["matched_sessions"] for row in report["rows"])
    report["overall_status"]=("ALL_FIVE_RESEARCH_COHORT_PAIRED" if all(
        r["status"]=="RESEARCH_PAIRED_WITHIN_TOLERANCE" for r in report["rows"])
        else "REVIEW_OR_BLOCKED_COHORT")
    report["report_sha256"]=hashlib.sha256(json.dumps(report["rows"],sort_keys=True).encode()).hexdigest()
    return report

def main():
    import argparse
    from pathlib import Path
    p=argparse.ArgumentParser()
    p.add_argument("--output",type=Path,default=Path("alpaca_yahoo_252_metadata.json"))
    args=p.parse_args()
    r=audit()
    args.output.write_text(json.dumps(r,sort_keys=True,indent=2,allow_nan=False)+"\n")
    print(json.dumps({"status":r["overall_status"],"paper_verified":r["paper_account_verified"],
          "matched_pairs":r.get("matched_session_pairs",0),"symbols":[
          (x["symbol"],x["status"],x["matched_sessions"]) for x in r["rows"]],
          "orders":0},sort_keys=True))

if __name__=="__main__":
    main()
