from __future__ import annotations

import argparse
import json
import logging
import math
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import yfinance as yf

# Keep Streamlit bare-mode context warnings out of scheduled-job logs.
logging.getLogger("streamlit").setLevel(logging.CRITICAL)
logging.getLogger("streamlit.runtime").setLevel(logging.CRITICAL)

import stock_app


def save_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def fundamental_result(snapshot, peers, gbp_rate: float) -> dict:
    """Apply the long-lived Trade gates while deliberately deferring event-only gates."""
    return stock_app.score_fundamental_snapshot(
        snapshot,
        peers,
        gbp_rate,
        earnings_sessions=None,
        official_event_verified=False,
        apply_event_gate=False,
    )


def is_fundamentally_eligible(result: dict) -> bool:
    failures = [str(value) for value in result.get("fundamental_failures", [])]
    return not failures and float(result.get("fundamental_score", 0) or 0) >= 65


def reconcile_cached(frame: pd.DataFrame, eligible_symbols: set[str]) -> pd.DataFrame:
    """Remove cached technicals immediately when refreshed fundamentals make them ineligible."""
    if frame.empty or "Ticker" not in frame.columns:
        return pd.DataFrame()
    return frame[
        frame["Ticker"].astype(str).isin(eligible_symbols)
    ].drop_duplicates(subset=["Ticker"], keep="last")


def technical_row(symbol: str, technical: dict, turnover_gbp: float, source_timestamp: str) -> dict:
    return {
        "Ticker": symbol,
        "Technical state": technical.get("technical_state", "BLOCKED"),
        "Technical reason": technical.get("technical_reason", "UNAVAILABLE"),
        "Signal date": technical.get("signal_date"),
        "Entry date": technical.get("entry_date"),
        "Price": technical.get("price"),
        "ATR20": technical.get("atr20"),
        "MA zone low": technical.get("zone_low"),
        "MA zone high": technical.get("zone_high"),
        "Entry": technical.get("entry"),
        "Stop": technical.get("stop"),
        "Target": technical.get("target"),
        "Target basis": technical.get("target_source"),
        "Stop distance %": technical.get("stop_distance_pct"),
        "Upside %": technical.get("upside_pct"),
        "R:R": technical.get("reward_risk"),
        "RSI": technical.get("rsi"),
        "Median traded value GBPm": turnover_gbp / 1_000_000 if math.isfinite(turnover_gbp) else np.nan,
        "Liquidity gate": "PASS" if math.isfinite(turnover_gbp) and turnover_gbp >= 5_000_000 else "FAIL",
        "Technical score": technical.get("technical_score"),
        "Tier": technical.get("technical_tier"),
        "Market regime": technical.get("market_state"),
        "RS recovery %": technical.get("relative_strength_pct"),
        "Volume ratio": technical.get("volume_ratio"),
        "Rulebook build": stock_app.TRADE_RULEBOOK_BUILD,
        "Fundamental source updated at": source_timestamp,
        "Technical calculated at": datetime.now(ZoneInfo("Europe/London")).isoformat(timespec="seconds"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill Trade technicals for fundamental hard-gate passers.")
    parser.add_argument("--exchange", default="all")
    parser.add_argument("--max-symbols-per-exchange", type=int, default=100)
    parser.add_argument("--output-dir", default="prepared_trade_technicals")
    parser.add_argument("--fundamentals-dir", default="prepared_trade_fundamentals")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--refresh-existing", action="store_true", help="Recalculate every currently eligible technical row, not only missing rows.")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    fundamentals_dir = Path(args.fundamentals_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    fundamentals_manifest_path = fundamentals_dir / "manifest.json"
    fundamentals_manifest = json.loads(fundamentals_manifest_path.read_text(encoding="utf-8"))
    source_timestamp = str(fundamentals_manifest.get("updated_at") or "UNAVAILABLE")
    labels = {kind: label for label, kind in stock_app.EXCHANGE_UNIVERSES.items()}
    kinds = list(labels) if args.exchange == "all" else [args.exchange]
    if any(kind not in labels for kind in kinds):
        raise SystemExit(f"Unknown exchange key: {args.exchange}")

    manifest_path = output_dir / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        manifest = {"schema_version": 1, "exchanges": {}}
    manifest.setdefault("exchanges", {})

    fx_rates = stock_app.trade_fx_to_gbp()
    unresolved_by_exchange: dict[str, dict] = {}
    hard_failures = 0

    for position, kind in enumerate(kinds, start=1):
        print(f"[{position}/{len(kinds)}] {labels[kind]}: loading prepared fundamentals", flush=True)
        fundamental_path = fundamentals_dir / f"{kind}.csv.gz"
        if not fundamental_path.exists():
            print(f"[{position}/{len(kinds)}] {kind}: no prepared fundamental file", flush=True)
            hard_failures += 1
            continue

        try:
            fundamental_frame = pd.read_csv(fundamental_path, compression="gzip")
            snapshots = [
                stock_app.trade_snapshot_from_record(row)
                for row in fundamental_frame.to_dict(orient="records")
            ]
            snapshots = [snapshot for snapshot in snapshots if snapshot.symbol]
            universe = stock_app.get_universe(kind)
            if not universe:
                raise RuntimeError("exchange universe returned no symbols")
        except Exception as exc:
            print(f"[{position}/{len(kinds)}] {kind}: setup failed: {type(exc).__name__}: {exc}", flush=True)
            hard_failures += 1
            continue

        prepared_symbols = {snapshot.symbol for snapshot in snapshots}
        unresolved = [symbol for symbol in universe if symbol not in prepared_symbols]
        unresolved_by_exchange[kind] = {
            "label": labels[kind],
            "count": len(unresolved),
            "symbols": unresolved,
        }

        required_currencies = {
            snapshot.currency for snapshot in snapshots if snapshot.currency
        }
        missing_fx = sorted(
            currency for currency in required_currencies
            if not math.isfinite(float(fx_rates.get(currency, np.nan)))
        )
        if missing_fx:
            print(
                f"[{position}/{len(kinds)}] {kind}: missing GBP FX rates for "
                f"{', '.join(missing_fx)}; deferring without marking complete",
                flush=True,
            )
            hard_failures += 1
            continue

        eligible: list[str] = []
        fundamental_score_by_symbol: dict[str, float] = {}
        for snapshot in snapshots:
            rate = fx_rates.get(snapshot.currency, np.nan)
            result = fundamental_result(snapshot, snapshots, rate)
            if is_fundamentally_eligible(result):
                eligible.append(snapshot.symbol)
                fundamental_score_by_symbol[snapshot.symbol] = float(result["fundamental_score"])

        eligible_set = set(eligible)
        target = output_dir / f"{kind}.csv.gz"
        try:
            existing = pd.read_csv(target, compression="gzip") if target.exists() else pd.DataFrame()
        except Exception:
            existing = pd.DataFrame()
        existing = reconcile_cached(existing, eligible_set)
        existing_symbols = set(existing["Ticker"].astype(str)) if not existing.empty else set()
        missing = [symbol for symbol in eligible if symbol not in existing_symbols]
        candidates = eligible if args.refresh_existing else missing
        scan_symbols = (
            candidates[: args.max_symbols_per_exchange]
            if args.max_symbols_per_exchange > 0
            else candidates
        )

        benchmark = stock_app.trade_benchmark_frame(stock_app.TRADE_MARKET_CONTEXT[kind]["benchmark"])
        context = stock_app.TRADE_MARKET_CONTEXT[kind]
        quote_to_gbp = fx_rates.get(context["currency"], np.nan)
        rows: list[dict] = []

        for start in range(0, len(scan_symbols), 60):
            chunk = scan_symbols[start : start + 60]
            try:
                data = yf.download(
                    tickers=chunk,
                    period="3y",
                    interval="1d",
                    group_by="ticker",
                    auto_adjust=False,
                    threads=True,
                    progress=False,
                )
            except Exception as exc:
                print(f"[{position}/{len(kinds)}] {kind}: price batch failed: {type(exc).__name__}", flush=True)
                continue
            for symbol in chunk:
                try:
                    frame = stock_app.extract_ticker_frame(data, symbol)
                    if frame is None:
                        continue
                    technical = stock_app.evaluate_price_setup(frame, benchmark)
                    clean = frame.dropna(subset=["Close", "Volume"])
                    raw_turnover = (clean["Close"] * clean["Volume"]).tail(20).median()
                    turnover_gbp = raw_turnover * context["price_scale"] * quote_to_gbp
                    row = technical_row(symbol, technical, turnover_gbp, source_timestamp)
                    row["Fundamental score"] = fundamental_score_by_symbol[symbol]
                    rows.append(row)
                except Exception:
                    continue

        if args.refresh_existing and not existing.empty:
            # A full refresh must never leave yesterday's technical state behind
            # when today's price-history request fails. Remove every attempted row
            # first, then add back only successfully recalculated rows.
            existing = existing[
                ~existing["Ticker"].astype(str).isin(set(scan_symbols))
            ]

        if rows:
            refreshed = {str(row["Ticker"]) for row in rows}
            if not existing.empty and not args.refresh_existing:
                existing = existing[
                    ~existing["Ticker"].astype(str).isin(refreshed)
                ]
            combined = pd.concat([existing, pd.DataFrame(rows)], ignore_index=True)
        else:
            combined = existing
        if not combined.empty:
            combined["Fundamental source updated at"] = source_timestamp
            combined = reconcile_cached(combined, eligible_set)
            combined.to_csv(target, index=False, compression="gzip")

        covered = int(combined["Ticker"].nunique()) if not combined.empty else 0
        remaining = max(len(eligible) - covered, 0)
        now = datetime.now(ZoneInfo("Europe/London")).isoformat(timespec="seconds")
        manifest["exchanges"][kind] = {
            "label": labels[kind],
            "status": "complete" if remaining == 0 else "partial",
            "eligible_symbols": len(eligible),
            "coverage_symbols": covered,
            "coverage_pct": round(covered / len(eligible) * 100, 1) if eligible else 100.0,
            "remaining_symbols": remaining,
            "requested_this_run": len(scan_symbols),
            "refresh_mode": "all_eligible" if args.refresh_existing else "missing_only",
            "returned_this_run": len(rows),
            "unresolved_fundamental_symbols": len(unresolved),
            "rulebook_build": stock_app.TRADE_RULEBOOK_BUILD,
            "source_fundamentals_updated_at": source_timestamp,
            "updated_at": now,
        }
        manifest["updated_at"] = now
        manifest["rulebook_build"] = stock_app.TRADE_RULEBOOK_BUILD
        manifest["source_fundamentals_updated_at"] = source_timestamp
        manifest["fundamental_coverage_mode"] = "practical-complete-with-explicit-provider-gaps"
        save_json(manifest_path, manifest)
        print(f"[{position}/{len(kinds)}] {kind}: technicals {covered}/{len(eligible)}; {remaining} remain", flush=True)

    save_json(
        output_dir / "unresolved_fundamentals.json",
        {
            "recorded_at": datetime.now(ZoneInfo("Europe/London")).isoformat(timespec="seconds"),
            "source_fundamentals_updated_at": source_timestamp,
            "rulebook_build": stock_app.TRADE_RULEBOOK_BUILD,
            "total_unresolved": sum(value["count"] for value in unresolved_by_exchange.values()),
            "exchanges": unresolved_by_exchange,
        },
    )
    return 1 if hard_failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
