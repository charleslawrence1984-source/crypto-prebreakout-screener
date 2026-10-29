"""Read-only prospective 6-venue Yahoo completed-bar Trade SOURCE pilot.

NOT a Trade signal/backtest; does not apply fundamentals, events, FX, liquidity
or broker spreads. Provider outages remain UNKNOWN; CI cannot equate a green
run with live data or a production release.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import yfinance as yf

from trade_completed_bar_guard import validate_completed_trade_frame

SAMPLES = (
    ("AAPL", "nasdaq"), ("MSFT", "nyse"), ("AZN.L", "lse"),
    ("SHOP.TO", "tsx"), ("SAP.DE", "xetra"), ("ASML.AS", "euronext_amsterdam"),
)


def extract_batch(batch: pd.DataFrame, symbol: str) -> pd.DataFrame:
    if batch is None or batch.empty:
        raise ValueError("EMPTY_PROVIDER_RESPONSE")
    if isinstance(batch.columns, pd.MultiIndex):
        for level in (0, 1):
            if symbol in set(map(str, batch.columns.get_level_values(level))):
                return (batch[symbol].copy() if level == 0
                        else batch.xs(symbol, level=1, axis=1).copy())
        raise ValueError("PROVIDER_SYMBOL_ABSENT")
    return batch.copy()


def run_pilot(now=None, fetch=None):
    """Each fresh fetched batch is inspected once at the same observed UTC time."""
    now = datetime.now(timezone.utc) if now is None else now
    if now.tzinfo is None:
        raise ValueError("Timezone-aware now required")
    def downloader(symbol):
        return yf.download(tickers=symbol, period="3y", interval="1d",
                           group_by="ticker", auto_adjust=False, progress=False,
                           threads=False)
    fetch = fetch or downloader
    observed = pd.Timestamp(now)
    examples = []
    for symbol, venue in SAMPLES:
        entry = {"symbol": symbol, "venue": venue}
        try:
            raw = extract_batch(fetch(symbol), symbol)
            entry["provider_raw_bars"] = len(raw)
            frame, verdict = validate_completed_trade_frame(raw, venue, observed)
            entry["session_check"] = verdict
            entry["scorable_completed_bars"] = len(frame) if frame is not None else 0
        except Exception as exc:
            entry["session_check"] = {
                "status": "UNKNOWN", "reason": "PROVIDER_OR_PARSER_" + type(exc).__name__,
                "independently_exchange_verified": False,
            }
            entry["scorable_completed_bars"] = 0
        examples.append(entry)
    return {
        "research_scope": "SIX_PRESENT_DAY_SURVIVORS_YAHOO_PROVENANCE_ONLY",
        "observed_utc": observed.isoformat(),
        "decision": "NO_TRADE_DECISIONS",
        "production_release_readiness": "NOT_VERIFIED",
        "tickers": examples,
        "limitations": [
            "Yahoo public historical candles, not native orderable broker quotes",
            "No immutable vendor capture prior to this run; no source signatures",
            "Only six currently listed ticker examples; not delisted membership",
            "No historical point-in-time fundamentals, news/events, GBP FX or issuer filings",
            "No portfolio slippage, drawdowns, forward paper trades or UI route migration",
            "All Gettex and OTC Trade candidates remain unsupported until exact venue calendars verified",
        ],
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("trade_completed_bar_shadow_pilot.json"))
    args = parser.parse_args()
    result = run_pilot()
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps({
        "readiness": result["production_release_readiness"],
        "source_receipts": [{
            "symbol": x["symbol"], "venue": x["venue"],
            "status": x["session_check"]["status"],
            "reason": x["session_check"]["reason"],
            "last_session": x["session_check"].get("last_completed_session"),
        } for x in result["tickers"]],
    }, indent=2))


if __name__ == "__main__":
    main()
