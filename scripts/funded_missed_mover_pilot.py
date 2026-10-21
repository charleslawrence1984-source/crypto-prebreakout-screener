"""One-time PR-only frozen technical STOCK backtest / missed-mover pilot.

Reads public Yahoo historical OHLCV without connecting to any Kraken account.
The original rule engines are used at each eligible historical signal session
through funded_stock_backtest.signal_candidates. Real signals are at t; outcomes
begin ONLY from bar t+1. Split and corporate-action biases remain documented.

This pilot is a CURRENT-UNIVERSE case study, never a survivor-free backtest.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path

import pandas as pd

from funded_forward_capture import funded_universe
from funded_stock_data import clean_stock_daily_frame, get_validated_stock_history
from funded_stock_backtest import signal_candidates, simulate_portfolio
from funded_historical_opportunities import opportunity_census, census_summary, gate_outcome_comparison, export_research


def safe_metadata(value):
    """Do not export NaN or Infinity as valid backtest statistics."""
    if isinstance(value, dict):
        return {str(k): safe_metadata(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [safe_metadata(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--out-dir", default="funded-backtest-pilot")
    p.add_argument("--symbols", default="AAPL,MSFT,NVDA,TSLA,PLTR",
                   help="Comma-separated approved stocks, or 'all' for the current 30-stock universe")
    p.add_argument("--min-valid", type=int, default=1,
                   help="Fail closed if fewer distinct fresh symbol histories are usable")
    p.add_argument("--period", default="3y", choices=("3y",))
    args = p.parse_args()
    from time import monotonic
    import yfinance as yf

    universe = funded_universe()["stocks"]
    selected = (list(universe) if args.symbols.strip().lower() == "all" else
                [s.strip().upper() for s in args.symbols.split(",") if s.strip()])
    if len(selected) != len(set(selected)) or not selected or any(s not in universe for s in selected):
        raise ValueError("Symbols must be distinct members of the original 30-stock funded universe")
    now = pd.Timestamp.now(tz="America/New_York")
    captured_at = datetime.now(timezone.utc)
    benchmark_batch = yf.download("^GSPC", period=args.period, interval="1d",
                                  auto_adjust=False, progress=False, threads=False)
    if isinstance(benchmark_batch.columns, pd.MultiIndex):
        benchmark_batch.columns = benchmark_batch.columns.get_level_values(0)
    bench, benchmark_reason = clean_stock_daily_frame(benchmark_batch, now)
    if bench is None:
        benchmark_batch = yf.download("^GSPC", period=args.period, interval="1d",
                                      auto_adjust=False, progress=False, threads=False)
        if isinstance(benchmark_batch.columns, pd.MultiIndex):
            benchmark_batch.columns = benchmark_batch.columns.get_level_values(0)
        bench, benchmark_reason = clean_stock_daily_frame(benchmark_batch, now)
    if bench is None:
        raise RuntimeError("No completed-session-valid benchmark; refusing pilot: " + benchmark_reason)

    try:
        batch = yf.download(selected, period=args.period, interval="1d",
                            group_by="ticker",auto_adjust=False,
                            progress=False,threads=False)
    except Exception:
        batch = pd.DataFrame()
    histories, coverage = {}, []
    for symbol in selected:
        def single(t):
            return yf.download(t, period=args.period, interval="1d",
                               auto_adjust=False, progress=False, threads=False)
        clean, source, reason = get_validated_stock_history(batch, symbol, single, now)
        coverage.append({"Ticker":symbol,"Price origin":source,"Price gate":reason,
                         "Completed bars":0 if clean is None else len(clean),
                         "Last completed bar":"" if clean is None else str(clean.index[-1].date())})
        if clean is not None:
            histories[symbol] = clean
    if len(histories) < args.min_valid:
        raise RuntimeError(
            f"Fresh historical sample {len(histories)}/{len(selected)} below required "
            f"minimum {args.min_valid}; refusing misleading pilot"
        )

    audit_trace = []
    start = monotonic()
    signals, diagnostics = signal_candidates(histories, bench, audit_trace=audit_trace)
    elapsed = round(monotonic() - start, 1)
    audited = set(diagnostics.loc[diagnostics["Status"] == "SCANNED", "Ticker"])
    ledger, ledger_coverage = opportunity_census(
        histories, asset_type="stocks", signals=signals, audit_trace=audit_trace,
        audited_symbols=audited, windows=(5,10,20), thresholds_pct=(5,10,20),
        min_history=252,
    )
    summary = census_summary(ledger)
    veto_comparison = gate_outcome_comparison(ledger)
    result = simulate_portfolio(histories, signals,
                                roundtrip_cost_pct=0.5,max_hold_sessions=20)
    out = Path(args.out_dir)
    metadata = {
        "capture_at_utc": captured_at.isoformat(),
        "mode": "PUBLIC_RESEARCH_ONLY_NO_ORDER_PERMISSIONS",
        "rules_baseline_main_commit": "a1f1a37c3945743261cc4f7dd0ade0648f04cb62",
        "rules_engines": ["trade_rules.evaluate_price_setup",
                          "funded_stock_breakout.evaluate_funded_breakout",
                          "funded_stock_backtest.signal_candidates"],
        "provider": "Yahoo Finance 3y unadjusted stock daily OHLCV and ^GSPC benchmark",
        "approved_current_universe_size": len(universe),
        "pilot_symbols": selected, "valid_prices": sorted(histories),
        "current_survivors_only": True,
        "historical_listing_and_membership_verified": False,
        "historical_earnings_events_verified": False,
        "funded_bid_ask_and_broker_market_access_verified": False,
        "splits_delistings_dividends_adjusted": False,
        "historical_technical_signal_count": len(signals),
        "prefilter_audit_rows": len(audit_trace),
        "run_time_seconds_for_signal_replay": elapsed,
        "original_rules_modified": False,
        "paper_friction_roundtrip_pct": 0.5,
        "entry_timing": "original funded_stock_backtest, next-day open proxy",
        "label_method": "subsequent CLOSE max t+1 through t+h vs CLOSE at t (NOT executable returns)",
        "train_validate_holdout": "per-symbol chronological 60/20/20 with horizon-boundary purge",
        "non_independence": "overlapping decision windows; reported proportions are descriptive",
        "caveats": [
            "Present-day selected stocks do not establish historical survivor-free results.",
            "Audits are restricted to original technical prefilter candidates.",
            "No lookahead used for signal qualification; future prices label outcomes only.",
            "Funded account/earnings and real broker execution gating unavailable.",
            "No strategy profit, candidate precision or challenge pass-rate claim from this pilot.",
        ],
        "portfolio_proxy_metrics": safe_metadata(result.get("metrics", {})),
    }
    export_research(out, ledger, ledger_coverage, summary, metadata,
                    gate_comparison=veto_comparison)
    pd.DataFrame(coverage).to_csv(out/"provider_price_coverage.csv",index=False)
    pd.DataFrame(diagnostics).to_csv(out/"technical_replay_diagnostics.csv",index=False)
    pd.DataFrame(audit_trace).to_csv(out/"original_prefilter_trace.csv",index=False)
    pd.DataFrame(signals).to_csv(out/"qualified_original_signal_candidates.csv",index=False)
    for name in ("trades", "skipped", "equity_curve"):
        report = result.get(name)
        if isinstance(report, pd.DataFrame):
            report.to_csv(out/f"portfolio_{name}.csv",index=False)
    print(json.dumps({
        "status":"TECHNICAL_RESEARCH_ONLY","symbols":selected,
        "valid":sorted(histories),"audited":sorted(audited),
        "qualified_signals":len(signals),
        "prefilter_rows":len(audit_trace),
        "eligible_anchor_rows":len(ledger),
        "mover_summary_rows":len(summary),
        "veto_comparison_rows":len(veto_comparison),
        "research_runtime_s":elapsed,
        "output_dir":str(out),
        "funded_trade_ready_confirmed":0,
    },sort_keys=True))
    if len(audited) == 0:
        raise RuntimeError("No eligible historical engine audit; research must not be reported as complete")


if __name__ == "__main__":
    main()
