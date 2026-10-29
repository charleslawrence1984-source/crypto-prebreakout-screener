"""Prospective, research-only original Stocks TRADE source observation ledger.

Never fabricates a paper entry: original Trade technical states are collected
at the ACTUAL run timestamp, while absent dated fundamental/event/FX/broker
proof locks every observation as NOT_ELIGIBLE_FOR_APPROVED_PAPER_TRADE.
No orders, accounts, persistence to main or backdated scans. Future returns
are only descriptive *after* a genuinely later run with a prior captured file.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import yfinance as yf

from trade_completed_bar_guard import validate_completed_trade_frame
from trade_rules import evaluate_price_setup

WATCHLIST = (("AAPL", "nasdaq"), ("MSFT", "nasdaq"), ("NVDA", "nasdaq"),
             ("GOOGL", "nasdaq"), ("AMZN", "nasdaq"), ("AZN.L", "lse"))
EVIDENCE_MISSING = (
    "Unreconstructed as-of issuer finance and officially dated earnings/news",
    "Historical source membership/delisted lineage",
    "Independent broker orderability, GBP FX and spread/liquidity on the signal date",
    "Actual open-gap validation, fees and risk sizing",
)


def _utc(value):
    stamp = pd.Timestamp(value)
    if stamp.tzinfo is None:
        raise ValueError("UTC_OFFSET_REQUIRED")
    return stamp.tz_convert("UTC")


def _frame(raw: pd.DataFrame, symbol: str) -> pd.DataFrame:
    if raw is None or raw.empty:
        raise ValueError("PROVIDER_EMPTY")
    if isinstance(raw.columns, pd.MultiIndex):
        if symbol in raw.columns.get_level_values(0):
            raw = raw[symbol].copy()
        elif symbol in raw.columns.get_level_values(1):
            raw = raw.xs(symbol, level=1, axis=1).copy()
        else:
            raise ValueError("PROVIDER_SYMBOL_NOT_FOUND")
    return raw.rename(columns={str(c):str(c).title() for c in raw.columns})


def freeze_one(symbol: str, venue: str, data: pd.DataFrame | None,
               benchmark: pd.DataFrame | None, captured_at) -> dict:
    moment = _utc(captured_at)
    row = {
        "symbol": symbol, "venue": venue,
        "first_captured_utc": moment.isoformat(),
        "paper_mode": "RESEARCH_OBSERVATION_NO_EXECUTION",
        "approved_paper_entry": False,
        "live_trade_ready": False,
        "missing_release_evidence": EVIDENCE_MISSING,
        "technical_state": "UNAVAILABLE",
        "technical_reason": "NO_VALID_COMPLETED_PRICE_HISTORY",
        "source_session": None,
        "source_ohlcv_sha256": None, "source_validation": "UNKNOWN",
        "signal_close_price": None, "original_rulebook_technical_score": None,
    }
    valid, receipt = validate_completed_trade_frame(data, venue, moment)
    row["source_validation"] = receipt["status"]
    row["price_guard_reason"] = receipt["reason"]
    if valid is None:
        row["observation_id"] = _id(row)
        return row
    row["source_session"] = receipt["last_completed_session"]
    row["source_ohlcv_sha256"] = receipt["last_completed_ohlcv_sha256"]
    # Reference benchmark must itself be a confirmed closed-session history;
    # absent a clean benchmark, do not score the candidate.
    if benchmark is None:
        row["technical_reason"] = "BENCHMARK_EVIDENCE_UNAVAILABLE"
        row["observation_id"] = _id(row)
        return row
    benchmark_valid, benchmark_receipt = validate_completed_trade_frame(
        benchmark, "nyse" if venue in {"nyse", "nasdaq"} else venue, moment
    )
    if benchmark_valid is None:
        row["technical_reason"] = "BENCHMARK_" + benchmark_receipt["reason"]
        row["observation_id"] = _id(row)
        return row
    result = evaluate_price_setup(valid, benchmark_valid)
    row["technical_state"] = str(result.get("technical_state") or "BLOCKED")
    row["technical_reason"] = str(result.get("technical_reason") or "UNAVAILABLE")
    row["original_rulebook_technical_score"] = (
        float(result["technical_score"])
        if pd.notna(result.get("technical_score")) else None
    )
    row["signal_close_price"] = float(valid.iloc[-1]["Close"])
    row["observation_id"] = _id(row)
    return row


def _id(row: dict) -> str:
    material = {key: row.get(key) for key in (
        "symbol", "venue", "first_captured_utc", "source_session",
        "source_ohlcv_sha256", "technical_state", "technical_reason")}
    return hashlib.sha256(json.dumps(material,sort_keys=True).encode()).hexdigest()


def later_price_observation(prior: dict, new: dict) -> dict:
    """Follow-up only on an existing frozen earlier observation, no backfill.

    Even price changes are not simulated trade P&L because no approved opening
    entry, fills, funded cash, spreads, exits, FX or brokerage are recorded.
    """
    if str(prior.get("symbol")) != str(new.get("symbol")):
        raise ValueError("SYMBOL_MISMATCH")
    earlier = _utc(prior["first_captured_utc"])
    later = _utc(new["first_captured_utc"])
    if later <= earlier:
        raise ValueError("NOT_A_PROSPECTIVE_LATER_OBSERVATION")
    old_session = prior.get("source_session")
    new_session = new.get("source_session")
    if not old_session or not new_session or new_session <= old_session:
        return {"status":"NO_LATER_VERIFIED_COMPLETED_PRICE_BAR",
                "approved_paper_trade":False}
    old_close,new_close=prior.get("signal_close_price"),new.get("signal_close_price")
    if not (isinstance(old_close,(float,int)) and old_close > 0
            and isinstance(new_close,(float,int)) and new_close > 0):
        return {"status":"INCOMPLETE_PRICE_CONTEXT","approved_paper_trade":False}
    return {
        "status":"DESCRIPTIVE_FORWARD_PRICE_CHANGE_NOT_TRADE_RETURN",
        "prior_captured_utc":earlier.isoformat(),
        "followup_captured_utc":later.isoformat(),
        "prior_session":old_session,"followup_session":new_session,
        "public_close_change_pct":round((new_close/old_close-1)*100,5),
        "approved_paper_trade":False,
        "realized_strategy_return":None,
    }


def capture(now=None, fetch=None) -> dict:
    now=_utc(now or datetime.now(timezone.utc))
    def downloader(symbol):
        return yf.download(
            tickers=symbol,period="3y",interval="1d",
            auto_adjust=False,threads=False,progress=False,
            group_by="ticker",
        )
    fetch=fetch or downloader
    observations=[]
    benchmark_cache={}
    for symbol,venue in WATCHLIST:
        try:
            price=_frame(fetch(symbol),symbol)
            bench_sym="SPY" if venue in ("nasdaq","nyse") else "ISF.L"
            if bench_sym not in benchmark_cache:
                try:
                    benchmark_cache[bench_sym]=_frame(fetch(bench_sym),bench_sym)
                except Exception:
                    benchmark_cache[bench_sym]=None
            record=freeze_one(symbol,venue,price,benchmark_cache[bench_sym],now)
        except Exception as exc:
            record=freeze_one(symbol,venue,None,None,now)
            record["provider_issue"]=type(exc).__name__
        observations.append(record)
    return {
        "capture_created_utc":now.isoformat(),
        "scope":"PROSPECTIVE_SOURCE_OBSERVATIONS_NOT_FULL_ASOF_TRADE_CANDIDATES",
        "watchlist_selection":"FIXED_IN_CODE_BEFORE_CAPTURE; PRESENT_DAY_SURVIVORS_ONLY",
        "approved_trade_entries":0,
        "simulated_trade_entries":0,
        "live_trade_readiness":"NOT_VERIFIED",
        "paper_readiness":"NOT_READY_NO_FULL_ASOF_EVIDENCE",
        "observations":observations,
        "limits":[
            "Snapshots captured prospectively at actual run time; prior historical backfill cannot count as paper",
            "No original-dated financial/event/FX/liquidity/broker evidence, so no approved entries/fills",
            "Original Trade technical setup states use a provider-captured last completed bar only",
            "Six preselected current symbols are not representative or a survivorship-free backtest",
            "Elapsed forward follow-ups require a separate later signed prior snapshot",
        ],
    }


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--output",type=Path,default=Path("stocks_trade_forward_observation.json"))
    args=parser.parse_args()
    report=capture()
    args.output.write_text(json.dumps(report,indent=2,sort_keys=True)+"\n")
    print(json.dumps({
        "captured":report["capture_created_utc"],
        "approved_entries":report["approved_trade_entries"],
        "states":[{"symbol":x["symbol"],"session":x["source_session"],
                   "technical_state":x["technical_state"],
                   "technical_reason":x["technical_reason"],
                   "source":x["source_validation"]}
                  for x in report["observations"]],
    },indent=2))


if __name__=="__main__":
    main()
