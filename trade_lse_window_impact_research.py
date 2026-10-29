"""Read-only LSE source contamination report; NO scoring or BUY labels.

Distinguishes a corrupted 2024 source row outside the current 252-observation
window from an actual completed-session validation of the latest source.
An old anomaly being outside the window NEVER authorizes ignoring it for
full historical replays. Technical EMA warmups can use older observations.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import yfinance as yf

from trade_lse_reference_contract import geometry_problem

SAMPLES = ("AZN.L", "SHEL.L", "ULVR.L", "BP.L", "HSBA.L", "VOD.L")
REQUIRED = ("Open", "High", "Low", "Close", "Volume")
RECENT_WINDOW_ROWS = 252


def inspect_frame(frame: pd.DataFrame | None, symbol: str) -> dict:
    result = {
        "symbol": symbol, "source": "YAHOO_YFINANCE_PUBLIC_RETRIEVAL",
        "status": "PROVIDER_UNKNOWN", "total_rows": 0,
        "recent_window_rows": 0, "recent_window_first": None,
        "recent_window_last": None, "all_bad_sessions": [],
        "recent_bad_sessions": [],
        "recent_window_clean_not_live_verified": False,
        "historical_asof_backtest_allowed": False,
        "live_trade_readiness": "NOT_VERIFIED",
    }
    if frame is None or frame.empty or not isinstance(frame.index, pd.DatetimeIndex):
        return result
    frame = frame.copy()
    if isinstance(frame.columns, pd.MultiIndex):
        if symbol in frame.columns.get_level_values(0):
            frame = frame[symbol]
        elif symbol in frame.columns.get_level_values(1):
            frame = frame.xs(symbol, level=1, axis=1)
        else:
            return result
    frame = frame.rename(columns={str(k):str(k).title() for k in frame.columns})
    if not set(REQUIRED).issubset(frame.columns) or frame.index.has_duplicates:
        result["status"] = "MISSING_COLUMNS_OR_DUPLICATE_DATES"
        return result
    frame = frame.sort_index()
    result["total_rows"] = len(frame)
    tail = frame.tail(RECENT_WINDOW_ROWS)
    result["recent_window_rows"] = len(tail)
    if len(tail):
        result["recent_window_first"] = tail.index[0].date().isoformat()
        result["recent_window_last"] = tail.index[-1].date().isoformat()
    for date, row in frame.iterrows():
        geometry = geometry_problem({key.lower(): row[key] for key in REQUIRED})
        if geometry:
            result["all_bad_sessions"].append({
                "date": pd.Timestamp(date).date().isoformat(),
                "issue": geometry,
            })
    for date, row in tail.iterrows():
        geometry = geometry_problem({key.lower(): row[key] for key in REQUIRED})
        if geometry:
            result["recent_bad_sessions"].append({
                "date": pd.Timestamp(date).date().isoformat(),
                "issue": geometry,
            })
    result["recent_window_clean_not_live_verified"] = (
        len(tail) == RECENT_WINDOW_ROWS and not result["recent_bad_sessions"]
    )
    result["status"] = (
        "HISTORICAL_DEFECT_OUTSIDE_LAST_252_NOT_CLEARED"
        if result["all_bad_sessions"] and result["recent_window_clean_not_live_verified"]
        else "DEFECT_IN_RECENT_WINDOW"
        if result["recent_bad_sessions"] else "NO_GEOMETRY_DEFECT_OBSERVED_NOT_CERTIFIED"
        if result["recent_window_clean_not_live_verified"]
        else "INSUFFICIENT_RECENT_SOURCE_BARS"
    )
    return result


def run(now=None, fetch=None) -> dict:
    now = now or datetime.now(timezone.utc)
    def download(symbol):
        return yf.download(tickers=symbol, period="3y", interval="1d",
                           group_by="ticker", auto_adjust=False,
                           progress=False, threads=False)
    fetch = fetch or download
    rows = []
    for ticker in SAMPLES:
        try:
            rows.append(inspect_frame(fetch(ticker), ticker))
        except Exception as exc:
            rows.append({
                "symbol": ticker, "status": "PROVIDER_UNKNOWN", "error": type(exc).__name__,
                "live_trade_readiness": "NOT_VERIFIED",
                "historical_asof_backtest_allowed": False,
            })
    return {
        "as_of_utc": now.isoformat(),
        "scope": "LSE_SOURCE_WINDOW_CONTAMINATION_RESEARCH_NO_SIGNAL_SCORING",
        "readiness": "NOT_VERIFIED",
        "reports": rows,
        "limits": [
            "Valid geometry on 252 recent vendor candles is NOT freshness, publication, orderability or independent verification",
            "Older EMA warmup / historical scoring can depend on earlier bars, including the corrupt 2024 date",
            "Unrepaired malformed bars remain prohibited for full historical as-of replay",
            "No safe corporate-action or adjusted GBp price reconciliation performed",
            "Current-survivor six-symbol pilot is not historical membership, PIT fundamentals, execution or paper evidence",
        ],
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--output", type=Path, default=Path("lse_window_impact_research.json"))
    args = p.parse_args()
    result = run()
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True)+"\n")
    print(json.dumps({
        "readiness": result["readiness"],
        "samples": [{
            "symbol": r["symbol"], "status": r["status"],
            "all_bad": r.get("all_bad_sessions"), "recent_bad": r.get("recent_bad_sessions"),
            "recent_start": r.get("recent_window_first"), "recent_end": r.get("recent_window_last"),
        } for r in result["reports"]],
    }, indent=2))


if __name__ == "__main__":
    main()
