"""Read-only recovery observation for a VERIFIED prior failed Funded stock scan.

Never edits original statuses, scans technical BUY, changes publication-grace
thresholds, backfills historic entry signals, or alters the stock data source.
"""
from __future__ import annotations

from datetime import datetime, timezone
from io import BytesIO
import json
from pathlib import Path
from zipfile import ZipFile

import pandas as pd

from funded_forward_health import validate_snapshot
from funded_stock_data import (
    _extract_stock_frame, clean_stock_daily_frame, expected_completed_nyse_session,
)

EXPECTED_RUN=40048440026


def label_original(reason):
    source=str(reason or "").upper()
    if "STALE PRICE DATA" in source:
        return "HISTORIC_BATCH_AND_RETRY_STALE"
    if "PRICE HISTORY" in source and "252 COMPLETED BARS" in source:
        return "INSUFFICIENT_252_BAR_HISTORY"
    if "PRICE HISTORY" in source:
        return "INSUFFICIENT_252_BAR_HISTORY"
    return "OTHER_UPSTREAM_DATA_FAILURE"


def read_prior_snapshot(z):
    names=[x for x in z.namelist() if x.startswith("funded-stocks-") and x.endswith(".json")]
    if len(names)!=1:raise ValueError("expected exactly one archived original 30-stock source")
    record=json.loads(z.read(names[0]))
    validate_snapshot(record)
    if record["asset_type"]!="stocks" or not str(record["run_id"]).startswith(str(EXPECTED_RUN)):
        raise ValueError("research source is NOT the specified original failed stock capture")
    if any(row["status"]!="DATA UNAVAILABLE" for row in record["rows"]):
        raise ValueError("this source comparison is restricted to frozen failed stock rows")
    return record


def audit_recovery(frozen, current_batch, *, as_of):
    check_time=pd.Timestamp(as_of)
    if check_time.tzinfo is None:raise ValueError("provider recheck must be UTC aware")
    expected=expected_completed_nyse_session(check_time)
    rows=[]
    for previous in frozen["rows"]:
        symbol=previous["asset"]
        frame=_extract_stock_frame(current_batch,symbol)
        clean,reason=clean_stock_daily_frame(frame,check_time)
        label="SAME_PUBLICATION_GUARD_PASS_CURRENTLY" if clean is not None else "STILL_UNAVAILABLE_FOR_CURRENT_COMPLETED_SESSION"
        rows.append({
            "Symbol":symbol,
            "Original 24 Oct archive state":"DATA UNAVAILABLE",
            "Original failure category":label_original(previous["reason"]),
            "Observed at frozen UTC":frozen["captured_at_utc"],
            "Observed at independent recheck UTC":check_time.isoformat(),
            "Current expected completed NYSE session":str(expected),
            "Current independent batch check":label,
            "Current unmodified calendar/252-bar validation result":reason,
            "Current cleaned final completed session":str(clean.index[-1].date()) if clean is not None else None,
            "Original historical signal re-scored":False,
            "Funded broker trade confirmed":False,
        })
    tab=pd.DataFrame(rows)
    summary={
        "frozen_original_stock_capture":frozen["run_id"],
        "initial_stale_cases":int((tab["Original failure category"]=="HISTORIC_BATCH_AND_RETRY_STALE").sum()),
        "initial_insufficient_history_cases":int((tab["Original failure category"]=="INSUFFICIENT_252_BAR_HISTORY").sum()),
        "current_recheck_clean":int((tab["Current independent batch check"]=="SAME_PUBLICATION_GUARD_PASS_CURRENTLY").sum()),
        "current_recheck_unavailable":int((tab["Current independent batch check"]!="SAME_PUBLICATION_GUARD_PASS_CURRENTLY").sum()),
        "as_of":check_time.isoformat(),
        "earliest_available_candle_publication_time_unknown":True,
        "cannot_reconstruct_missed_stock_signals":True,
        "future_vendor_source_switch_authorized":False,
        "live_scan_rules_unchanged":True,
    }
    return tab,summary


def main():
    import argparse
    import yfinance as yf
    p=argparse.ArgumentParser()
    p.add_argument("--frozen-stock-zip",required=True)
    p.add_argument("--out-dir",default="funded-yahoo-republication-study")
    args=p.parse_args()
    with ZipFile(args.frozen_stock_zip) as z:
        original=read_prior_snapshot(z)
    codes=[r["asset"] for r in original["rows"]]
    now=pd.Timestamp.now(tz="UTC")
    try:
        batch=yf.download(
            tickers=" ".join(codes),period="3y",interval="1d",
            auto_adjust=False,progress=False,threads=False)
    except Exception as exc:
        print(f"::warning::Research Yahoo provider batch unavailable: {type(exc).__name__}")
        batch=pd.DataFrame()
    table,summary=audit_recovery(original,batch,as_of=now)
    path=Path(args.out_dir);path.mkdir(parents=True,exist_ok=True)
    table.to_csv(path/"after_original_failed_close_source_recheck.csv",index=False)
    (path/"data_publication_status.json").write_text(
        json.dumps(summary,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    print(json.dumps(summary,sort_keys=True))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
