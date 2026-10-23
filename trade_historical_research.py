"""Read-only, technical-only historical pilot of the ORIGINAL Stocks Trade engine.

Important limits: No point-in-time company filings, delisted universe, verified
historical earnings/news, historic FX-to-GBP liquidity or broker fills. Therefore
even a profitable pilot does NOT establish any approved Trade BUY or live readiness.
No rulebook, signal publication or trading endpoint is modified.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from trade_rules import evaluate_price_setup

COLS = ("Open", "High", "Low", "Close", "Volume")
RESEARCH_ONLY = "TECHNICAL_ONLY__FUNDAMENTALS_EVENTS_FX_LIQUIDITY_NOT_PIT_VERIFIED"


def validate_bars(data: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(data, pd.DataFrame) or not set(COLS).issubset(data.columns):
        raise ValueError("OHLCV schema incomplete")
    bars = data.loc[:, COLS].copy()
    if bars.index.has_duplicates or not bars.index.is_monotonic_increasing:
        raise ValueError("Unsorted or duplicate bar dates")
    bars = bars.apply(pd.to_numeric, errors="coerce")
    if bars.isna().any().any() or not bars.index.is_unique:
        raise ValueError("Missing or duplicate price bars: no silent interpolation")
    if (bars.loc[:, ("Open", "High", "Low", "Close")] <= 0).any().any():
        raise ValueError("Nonpositive price")
    if (bars["Volume"] < 0).any():
        raise ValueError("Negative volume")
    tolerance = 1e-6
    if ((bars["High"] + tolerance < bars[["Open", "Close", "Low"]].max(axis=1))
            | (bars["Low"] - tolerance > bars[["Open", "Close", "High"]].min(axis=1))).any():
        raise ValueError("Impossible high/low bar")
    return bars


def entry_gate(plan: dict, next_open: float) -> str:
    """Re-evaluate the published opening-price constraints without next-day H/L."""
    try:
        close = float(plan["price"])
        atr = float(plan["atr20"])
        zone_high = float(plan["zone_high"])
        stop = float(plan["stop"])
        target = float(plan["target"])
        opening = float(next_open)
    except (TypeError, ValueError, KeyError):
        return "MISSING_PLAN"
    if not all(math.isfinite(x) for x in (close, atr, zone_high, stop, target, opening)):
        return "MISSING_PLAN"
    if min(close, atr, opening) <= 0:
        return "INVALID_PRICE"
    if opening <= stop:
        return "STOP_AT_OPEN"
    if abs(opening - close) / atr > 0.5:
        return "OPENING_GAP_OVER_0_5_ATR"
    if opening > zone_high + atr:
        return "EXTENDED_ABOVE_MA_ZONE"
    if (opening - stop) / opening > 0.10:
        return "STOP_WIDER_THAN_10PCT"
    if (target - opening) / opening < 0.10:
        return "UPSIDE_BELOW_10PCT"
    if (target - opening) / (opening - stop) < 2.0:
        return "RR_BELOW_2"
    return "PASS"


def exit_trade(future: pd.DataFrame, entry: float, stop: float, target: float,
               roundtrip_bps: float = 50.0) -> dict:
    """Future includes entry day. Stops beat targets on ambiguous daily bars.

    Overnight adverse gaps fill at the actual open, not the optimistic stop.
    Costs charged on original notional (round trip proxy), no favorable fills.
    """
    if future.empty or roundtrip_bps < 0:
        raise ValueError("Need future bars and nonnegative cost")
    risk = entry - stop
    if not 0 < risk or not target > entry:
        raise ValueError("Untradeable stop/target")
    for timestamp, bar in future.iterrows():
        open_price = float(bar["Open"])
        low, high = float(bar["Low"]), float(bar["High"])
        if open_price <= stop:
            final, why = open_price, "GAP_THROUGH_STOP"
        elif open_price >= target:
            final, why = target, "TARGET_AT_OR_BELOW_OPEN"
        elif low <= stop:
            final, why = stop, "STOP_FIRST_IN_BAR"
        elif high >= target:
            final, why = target, "TARGET"
        else:
            continue
        break
    else:
        timestamp = future.index[-1]
        final, why = float(future.iloc[-1]["Close"]), "TIME_EXIT"
    gross_pct = (final / entry - 1) * 100
    net_pct = gross_pct - roundtrip_bps / 100
    return {
        "exit_date": str(pd.Timestamp(timestamp).date()),
        "exit_price": round(final, 8),
        "exit_reason": why,
        "gross_return_pct": round(gross_pct, 5),
        "net_return_pct": round(net_pct, 5),
        "net_r": round((final - entry - entry * roundtrip_bps / 10000) / risk, 5),
    }


def research_symbol(symbol: str, frame: pd.DataFrame, benchmark: pd.DataFrame | None = None,
                    horizon: int = 252, cooldown: int = 10, roundtrip_bps: float = 50.0,
                    evaluator=evaluate_price_setup) -> dict:
    """Replay ONLY exact completed-close crossover plans, next actual session open.

    Pre-cross WATCH/READY routes are counted but NOT assumed executable. Reject
    incomplete horizon outcomes rather than treating recent winners as complete.
    """
    if horizon < 1 or cooldown < 0 or roundtrip_bps < 0:
        raise ValueError("Invalid research constraints")
    bars = validate_bars(frame)
    benchmark_bars = validate_bars(benchmark) if benchmark is not None else None
    events, counters = [], Counter()
    last_candidate_index = -10**9
    blocked_until = -1
    # The original engine requires >=252 completed bars; next bar is entry.
    for i in range(251, len(bars) - 1):
        counters["evaluated_decision_closes"] += 1
        asof = bars.iloc[:i + 1]
        asof_bench = None
        if benchmark_bars is not None:
            asof_bench = benchmark_bars.loc[benchmark_bars.index <= asof.index[-1]]
        tech = evaluator(asof, asof_bench)
        state = str(tech.get("technical_state") or "BLOCKED")
        counters["state_" + state] += 1
        # Original reason is computed from completed as-of bars, never from
        # the future outcome label. All rejected anchors are retained.
        counters["reason_" + str(tech.get("technical_reason") or "UNAVAILABLE")] += 1
        for label_horizon in (5, 10, 20):
            if i + label_horizon >= len(bars):
                counters[f"mover_{label_horizon}bar_CENSORED"] += 1
                continue
            later_closes = bars["Close"].iloc[i + 1:i + 1 + label_horizon]
            later_max_gain = float(later_closes.max()) / float(asof["Close"].iloc[-1]) - 1
            counters[f"mover_{label_horizon}bar_ANCHORS"] += 1
            for threshold_pct in (5, 10, 20):
                outcome = "MOVER" if later_max_gain >= threshold_pct / 100 else "NONMOVER"
                counters[f"paired_{label_horizon}bar_{threshold_pct}pct_{state}_{outcome}"] += 1
        if state != "AWAITING NEXT OPEN":
            continue
        counters["confirmed_daily_close_candidates"] += 1
        if i - last_candidate_index < cooldown or i <= blocked_until:
            counters["cooldown_or_open_position"] += 1
            continue
        last_candidate_index = i
        opening = float(bars.iloc[i + 1]["Open"])
        gate = entry_gate(tech, opening)
        counters["next_open_" + gate] += 1
        if gate != "PASS":
            continue
        # Complete horizon for conservative, comparable cohort. Censored
        # signals are logged but never included in win or expectancy figures.
        if i + horizon >= len(bars):
            counters["censored_no_full_horizon"] += 1
            continue
        future = bars.iloc[i + 1:i + horizon + 1]
        result = exit_trade(future, opening, float(tech["stop"]),
                            float(tech["target"]), roundtrip_bps)
        end_index = int(bars.index.get_loc(pd.Timestamp(result["exit_date"]).tz_localize(
            bars.index.tz) if bars.index.tz is not None else pd.Timestamp(result["exit_date"])))
        blocked_until = end_index
        counters["completed_research_trades"] += 1
        events.append({
            "symbol": symbol, "signal_close_date": str(asof.index[-1].date()),
            "entry_date": str(bars.index[i + 1].date()),
            "entry_price": round(opening, 8),
            "stop": round(float(tech["stop"]), 8),
            "target": round(float(tech["target"]), 8),
            "original_technical_tier": str(tech.get("technical_tier") or "UNAVAILABLE"),
            "original_technical_score": float(tech.get("technical_score", float("nan"))),
            "research_scope": RESEARCH_ONLY,
            **result,
        })
    return {"symbol": symbol, "bar_count": len(bars), "counters": dict(counters),
            "events": events, "scope": RESEARCH_ONLY}


def manifest_audit(technical_path: Path, fundamentals_path: Path,
                   reference: pd.Timestamp | None = None) -> dict:
    """Monitor coverage/build mismatches; manifest timestamps do NOT certify bars."""
    now = reference if reference is not None else pd.Timestamp.now(tz="UTC")
    if now.tzinfo is None:
        raise ValueError("Audit reference requires timezone")
    tech = json.loads(technical_path.read_text())
    fund = json.loads(fundamentals_path.read_text())
    expected = str(tech.get("rulebook_build") or "")
    venues = {}
    for name in sorted(set(tech.get("exchanges", {})) | set(fund.get("exchanges", {}))):
        t, f = tech.get("exchanges", {}).get(name, {}), fund.get("exchanges", {}).get(name, {})
        flags = []
        for prefix, item in (("TECH", t), ("FUND", f)):
            if not item:
                flags.append(prefix + "_MISSING")
                continue
            if item.get("rulebook_build") != expected:
                flags.append(prefix + "_RULEBOOK_MISMATCH")
            if int(item.get("remaining_symbols") or 0):
                flags.append(prefix + "_INCOMPLETE")
            stamp = item.get("updated_at") or item.get("completed_at")
            try:
                ts = pd.Timestamp(stamp)
                if ts.tzinfo is None or (now - ts.tz_convert("UTC")).total_seconds() > 4 * 86400:
                    flags.append(prefix + "_OLD_MANIFEST")
            except (TypeError, ValueError):
                flags.append(prefix + "_INVALID_TIMESTAMP")
        venues[name] = {
            "technical_eligible": t.get("eligible_symbols"),
            "technical_covered": t.get("coverage_symbols"),
            "fundamental_universe": f.get("universe_symbols"),
            "fundamental_covered": f.get("coverage_symbols"),
            "technical_manifest_at": t.get("updated_at"),
            "fundamental_manifest_at": f.get("completed_at"),
            "flags": flags,
        }
    return {"rulebook_build": expected, "venues": venues,
            "not_verified": ["as-of OHLCV candle freshness", "PIT earnings/financial releases",
                             "PIT FX/liquidity", "primary listing and broker orderability",
                             "missing/delisted historical constituents"]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tickers", nargs="*", default=["AAPL", "MSFT", "NVDA"])
    parser.add_argument("--period", default="5y")
    parser.add_argument("--horizon", type=int, default=252)
    parser.add_argument("--cost-bps", type=float, default=50.0)
    parser.add_argument("--output", type=Path, default=Path("trade_research_report.json"))
    parser.add_argument("--audit-only", action="store_true")
    args = parser.parse_args()
    here = Path(__file__).resolve().parent
    report = {
        "run_utc": datetime.now(timezone.utc).isoformat(),
        "research_only": True,
        "live_trade_readiness": "NOT_VALIDATED",
        "historical_eligible_constituents": "CURRENT_SURVIVORS_ONLY",
        "historical_fundamental_decisions": "UNAVAILABLE",
        "scope": RESEARCH_ONLY,
        "reference": "trade_rules.evaluate_price_setup at each completed close; simulated next-session open",
        "costs_roundtrip_bps": args.cost_bps,
        "max_hold_sessions": args.horizon,
        "manifest_audit": manifest_audit(
            here / "prepared_trade_technicals/manifest.json",
            here / "prepared_trade_fundamentals/manifest.json"),
        "symbols": [],
    }
    if not args.audit_only:
        import yfinance as yf
        for symbol in dict.fromkeys(args.tickers):
            try:
                history = yf.Ticker(symbol).history(
                    period=args.period, interval="1d", auto_adjust=True,
                    actions=False)
                bars = validate_bars(history)
                digest = hashlib.sha256(bars.to_csv().encode()).hexdigest()
                result = research_symbol(symbol, bars, horizon=args.horizon,
                                         roundtrip_bps=args.cost_bps)
                result["source"] = "Yahoo adjusted historical OHLCV (research proxy)"
                result["bars_sha256"] = digest
            except Exception as exc:
                result = {"symbol": symbol, "error": str(exc), "events": []}
            report["symbols"].append(result)
        events = [event for item in report["symbols"] for event in item.get("events", [])]
        report["observed_completed_trades"] = len(events)
        report["net_win_rate"] = (
            sum(event["net_r"] > 0 for event in events) / len(events)
            if events else None)
        report["mean_net_r"] = (
            sum(event["net_r"] for event in events) / len(events)
            if events else None)
        report["conclusion"] = (
            "Descriptive technical-only current-survivor pilot; not a full "
            "historical Trade BUY backtest and not investment advice."
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False, default=str) + "\n")
    print(json.dumps({"readiness": report["live_trade_readiness"],
                      "trade_events": report.get("observed_completed_trades"),
                      "manifest_flags": {k: v["flags"] for k, v
                                         in report["manifest_audit"]["venues"].items()
                                         if v["flags"]}}, indent=2))


if __name__ == "__main__":
    main()
