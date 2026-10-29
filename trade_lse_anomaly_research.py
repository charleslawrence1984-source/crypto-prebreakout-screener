"""Independent-provider *attempt* to diagnose 2024-05-01 LSE daily OHLCV anomalies.

Research only. No modifications to historical records, existing scores,
publication status, trading signals, portfolios or source caches.

Stooq UK historical download may require a CAPTCHA-issued API key. This
script never bypasses quotas or fabricates secondary candles. Missing data
remains UNKNOWN. Stooq prices may be corporate-action-adjusted and are not
directly comparable to Yahoo's unadjusted GBp OHLCV prices.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests
import yfinance as yf

ANOMALY_DAY = "2024-05-01"
SAMPLES = ("AZN.L", "SHEL.L", "ULVR.L", "BP.L", "HSBA.L", "VOD.L")
SECONDARY = {"AZN.L": "azn.uk", "SHEL.L": "shel.uk"}
WINDOW_START = pd.Timestamp("2024-04-25")
WINDOW_END = pd.Timestamp("2024-05-08")
COLS = ("Open", "High", "Low", "Close", "Volume")


def invalid_ohlcv_reason(row: dict) -> str | None:
    try:
        open_, high, low, close, volume = (
            float(row[k]) for k in COLS
        )
    except (ValueError, TypeError, KeyError):
        return "MISSING_OR_NONNUMERIC"
    values = (open_, high, low, close, volume)
    if not all(pd.notna(x) and abs(x) < float("inf") for x in values):
        return "NONFINITE"
    if min(open_, high, low, close) <= 0 or volume < 0:
        return "NONPOSITIVE_OR_NEGATIVE_VOLUME"
    if high + 1e-8 < max(open_, low, close):
        return "HIGH_BELOW_OPEN_LOW_OR_CLOSE"
    if low - 1e-8 > min(open_, high, close):
        return "LOW_ABOVE_OPEN_HIGH_OR_CLOSE"
    return None


def rows_in_window(frame: pd.DataFrame) -> list[dict]:
    rows = []
    if frame is None or frame.empty:
        return rows
    if not isinstance(frame.index, pd.DatetimeIndex):
        return rows
    for ts, values in frame.sort_index().iterrows():
        value_date = pd.Timestamp(ts).date().isoformat()
        if value_date < WINDOW_START.date().isoformat() or value_date > WINDOW_END.date().isoformat():
            continue
        row = {"date": value_date}
        for key in COLS:
            v = pd.to_numeric(values.get(key), errors="coerce")
            row[key] = float(v) if pd.notna(v) else None
        row["geometry_issue"] = invalid_ohlcv_reason(row)
        rows.append(row)
    return rows


def yahoo_result(symbol: str, fetcher) -> dict:
    result = {
        "ticker": symbol, "provider": "YAHOO_VIA_YFINANCE",
        "requested_period": "3y", "requested_auto_adjust": False,
        "status": "UNKNOWN", "raw_bars": 0,
        "anomaly_day_ohlcv": None, "anomaly_day_issue": None,
        "window": [], "source_response_sha256": None,
    }
    try:
        frame = fetcher(symbol)
        if frame is None or frame.empty:
            raise ValueError("EMPTY_RESPONSE")
        if isinstance(frame.columns, pd.MultiIndex):
            if symbol in frame.columns.get_level_values(0):
                frame = frame[symbol]
            elif symbol in frame.columns.get_level_values(1):
                frame = frame.xs(symbol, level=1, axis=1)
            else:
                raise ValueError("SYMBOL_NOT_PRESENT_IN_BATCH")
        frame = frame.rename(columns={k: str(k).title() for k in frame.columns})
        if not set(COLS).issubset(frame.columns):
            raise ValueError("MISSING_OHLCV")
        if not isinstance(frame.index, pd.DatetimeIndex):
            raise ValueError("MISSING_TIME_INDEX")
        result["raw_bars"] = int(len(frame))
        rows = rows_in_window(frame)
        result["window"] = rows
        raw = json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()
        result["source_response_sha256"] = hashlib.sha256(raw).hexdigest()
        day = next((r for r in rows if r["date"] == ANOMALY_DAY), None)
        result["anomaly_day_ohlcv"] = day
        result["anomaly_day_issue"] = day["geometry_issue"] if day else "NO_BAR_FOR_DATE"
        result["status"] = "SOURCE_ROWS_OBSERVED" if rows else "NO_ROWS_IN_WINDOW"
    except Exception as exc:
        result["status"] = "UNKNOWN"
        result["error"] = type(exc).__name__
    return result


def stooq_result(ticker: str, fetcher) -> dict:
    result = {
        "ticker": ticker, "provider": "STOOQ_UK_DAILY_CSV",
        "window_start": WINDOW_START.date().isoformat(),
        "window_end": WINDOW_END.date().isoformat(),
        "status": "UNKNOWN", "anomaly_day_ohlcv": None,
        "anomaly_day_issue": None, "source_response_sha256": None,
        "price_comparability_to_yahoo": "UNVERIFIED_ADJUSTMENT_AND_GBP_PENCE_UNITS",
    }
    try:
        response = fetcher(ticker)
        if isinstance(response, bytes):
            text = response.decode("utf-8-sig")
        elif isinstance(response, str):
            text = response
        else:
            raise ValueError("NON_TEXT_PROVIDER_RESPONSE")
        result["source_response_sha256"] = hashlib.sha256(text.encode()).hexdigest()
        if not text.lstrip().startswith("Date,Open,High,Low,Close,Volume"):
            raise ValueError("NO_CSV_OR_PROVIDER_KEY_OR_RATE_LIMIT")
        rows = list(csv.DictReader(io.StringIO(text)))
        if not rows:
            raise ValueError("EMPTY_CSV")
        dates = []
        for row in rows:
            parsed = {"date": row.get("Date")}
            for key in COLS:
                value = pd.to_numeric(row.get(key), errors="coerce")
                parsed[key] = float(value) if pd.notna(value) else None
            parsed["geometry_issue"] = invalid_ohlcv_reason(parsed)
            dates.append(parsed)
        matching = next((row for row in dates if row["date"] == ANOMALY_DAY), None)
        result["anomaly_day_ohlcv"] = matching
        result["anomaly_day_issue"] = (
            matching["geometry_issue"] if matching else "NO_BAR_FOR_DATE"
        )
        result["rows_returned"] = len(dates)
        result["status"] = (
            "INDEPENDENT_PROVIDER_DAILY_CANDLE_OBSERVED"
            if matching else "INDEPENDENT_PROVIDER_DATE_ABSENT"
        )
    except Exception as exc:
        result["error"] = type(exc).__name__
        result["status"] = "UNKNOWN"
    return result


def summarize(a: dict, b: dict) -> dict:
    status = "UNRESOLVED"
    if (a["status"] == "SOURCE_ROWS_OBSERVED"
            and b["status"] == "INDEPENDENT_PROVIDER_DAILY_CANDLE_OBSERVED"):
        status = "TWO_PROVIDER_CANDLES_OBSERVED_NOT_NORMALIZED"
    return {
        "ticker": a["ticker"], "status": status,
        "yahoo_anomaly": a.get("anomaly_day_issue"),
        "secondary_anomaly": b.get("anomaly_day_issue"),
        "independent_exchange_verified": False,
        "automatic_replacement_or_repair_authorized": False,
        "next_required_evidence": "Reconcile adjusted vs unadjusted GBp prices, timestamp, official venue, corporate actions and independent raw exchange data",
    }


def run_audit(now=None, yahoo_fetch=None, stooq_fetch=None) -> dict:
    now = now or datetime.now(timezone.utc)
    def yf_fetch(symbol):
        return yf.download(
            tickers=symbol, period="3y", interval="1d", auto_adjust=False,
            threads=False, progress=False, group_by="ticker",
        )
    def stooq_download(ticker):
        resp = requests.get(
            "https://stooq.com/q/d/l/",
            params={"s": ticker, "d1": "20240425", "d2": "20240508", "i": "d"},
            headers={"User-Agent": "CL-Signal-Research-Only/1.0"},
            timeout=15,
        )
        resp.raise_for_status()
        return resp.text
    yahoo_fetch = yahoo_fetch or yf_fetch
    stooq_fetch = stooq_fetch or stooq_download
    source = [yahoo_result(symbol, yahoo_fetch) for symbol in SAMPLES]
    secondary = {symbol: stooq_result(ticker, stooq_fetch)
                 for symbol, ticker in SECONDARY.items()}
    return {
        "observed_utc": now.isoformat(), "source": source,
        "secondary": secondary,
        "comparison": [
            summarize(next(x for x in source if x["ticker"] == symbol),
                      secondary[symbol]) for symbol in SECONDARY
        ],
        "research_decision": "NO_TRADE_SIGNAL_OR_HISTORY_REPAIR",
        "source_venue": "LONDON_STOCK_EXCHANGE_YAHOO_TICKER_SUFFIX_UNCONFIRMED_EXACT_MARKET",
        "trade_production_readiness": "NOT_VERIFIED",
        "limitations": [
            "Yahoo public series is current retrospective data, not contemporaneous point-in-time as-of source",
            "Stooq might require CAPTCHA API key or reject automated downloads; unavailable is UNKNOWN",
            "Stooq may use adjusted prices and different units; cannot overwrite Yahoo bars based on one comparison",
            "Neither provider is an official independently signed exchange feed",
            "A historical source anomaly is not proof that the latest completed bar is stale",
            "No historical company membership/financial/event/news/FX/liquidity reconstruction or broker fills",
            "No live UI, orderbook or prospective paper trading evaluated",
        ],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", type=Path, default=Path("lse_history_anomaly_research.json"))
    args = ap.parse_args()
    result = run_audit()
    args.output.write_text(json.dumps(result, sort_keys=True, indent=2)+"\n")
    print(json.dumps({
        "trade_production_readiness": result["trade_production_readiness"],
        "yahoo": [{"ticker": r["ticker"], "status": r["status"],
                   "issue": r["anomaly_day_issue"], "raw_bars": r["raw_bars"]}
                  for r in result["source"]],
        "secondary": [{"ticker": r["ticker"], "status": r["status"],
                       "issue": r["anomaly_day_issue"], "error": r.get("error")}
                      for r in result["secondary"].values()],
        "comparison": result["comparison"],
    }, indent=2))


if __name__ == "__main__":
    main()
