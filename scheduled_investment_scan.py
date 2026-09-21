from __future__ import annotations

import argparse
import json
import logging
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

# Keep Streamlit's bare-mode context warnings out of scheduled-job logs.
logging.getLogger("streamlit").setLevel(logging.CRITICAL)
logging.getLogger("streamlit.runtime").setLevel(logging.CRITICAL)
import stock_app


def scan_with_outcomes(*args, **kwargs):
    """Keep per-company provider outcomes without changing calculation code."""
    original = stock_app._investment_company_result
    outcomes = {}
    def capture(*company_args, **company_kwargs):
        result = original(*company_args, **company_kwargs)
        outcomes[result["symbol"]] = {k: v for k, v in result.items() if k not in ("row", "symbol")}
        return result
    stock_app._investment_company_result = capture
    try:
        results = stock_app.fundamental_market_scan(*args, **kwargs)
        results.attrs.setdefault("scan_diagnostics", {})["symbol_outcomes"] = outcomes
        return results
    finally:
        stock_app._investment_company_result = original


def save_manifest(path: Path, manifest: dict) -> None:
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare exchange investment scans for the Streamlit app.")
    parser.add_argument("--exchange", default="all", help="Universe key or 'all'.")
    parser.add_argument("--max-symbols", type=int, default=0, help="0 analyses the full exchange universe.")
    parser.add_argument("--chunk-size", type=int, default=0, help="Rolling symbols per exchange; 0 disables chunking.")
    parser.add_argument(
        "--max-symbols-per-exchange",
        type=int,
        default=0,
        help="In gap-fill mode, analyse at most this many missing symbols per exchange; 0 means all gaps.",
    )
    parser.add_argument(
        "--gap-fill",
        action="store_true",
        help="Analyse only symbols that are missing from the prepared exchange file.",
    )
    parser.add_argument(
        "--closed-markets-only",
        action="store_true",
        help="Defer North America until its regular cash session has closed.",
    )
    parser.add_argument("--min-market-cap-bn", type=float, default=0.5)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--output-dir", default="prepared_scans")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--symbols-json", default="", help="Optional JSON file containing per-exchange changed_symbols/new_symbols lists.")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        manifest = {"schema_version": 1, "exchanges": {}}
    manifest.setdefault("exchanges", {})

    selected_symbols_by_kind = None
    if args.symbols_json:
        try:
            payload = json.loads(Path(args.symbols_json).read_text(encoding="utf-8"))
            selected_symbols_by_kind = {
                kind: sorted(set(
                    list(meta.get("changed_symbols", []))
                    + list(meta.get("new_symbols", []))
                ))
                for kind, meta in payload.get("exchanges", {}).items()
            }
        except Exception as exc:
            raise SystemExit(f"Cannot read --symbols-json {args.symbols_json}: {exc}")

    labels_by_kind = {kind: label for label, kind in stock_app.EXCHANGE_UNIVERSES.items()}
    if args.exchange == "all":
        kinds = list(labels_by_kind)
    elif args.exchange in labels_by_kind:
        kinds = [args.exchange]
    else:
        raise SystemExit(f"Unknown exchange key: {args.exchange}")

    london_now = datetime.now(ZoneInfo("Europe/London"))
    run_date = london_now.date().isoformat()
    failures = 0
    north_america = {"nasdaq", "nyse", "otc", "tsx"}
    for position, kind in enumerate(kinds, start=1):
        previous = manifest["exchanges"].get(kind, {})
        if args.closed_markets_only and kind in north_america:
            if london_now.weekday() < 5 and (
                london_now.hour < 21
                or (london_now.hour == 21 and london_now.minute < 15)
            ):
                print(f"[{position}/{len(kinds)}] {kind}: market still open; deferred", flush=True)
                continue
        if (
            args.resume
            and not args.gap_fill
            and selected_symbols_by_kind is None
            and str(previous.get("completed_at", "")).startswith(run_date)
        ):
            print(f"[{position}/{len(kinds)}] {kind}: already completed today; skipping", flush=True)
            continue
        started = datetime.now(ZoneInfo("Europe/London")).isoformat(timespec="seconds")
        print(f"[{position}/{len(kinds)}] {labels_by_kind[kind]}: loading universe", flush=True)
        try:
            universe = stock_app.get_universe(kind)
            market_caps = stock_app.get_universe_market_caps(kind)
            if not universe:
                raise RuntimeError("exchange universe returned no symbols")
            eligible = [
                symbol for symbol in universe
                if market_caps.get(symbol, 0) >= args.min_market_cap_bn * 1_000_000_000
            ]
            if not eligible:
                raise RuntimeError("no symbols passed the exchange market-cap prefilter")
            target = output_dir / f"{kind}.csv.gz"
            existing = pd.DataFrame()
            if target.exists():
                try:
                    existing = pd.read_csv(target, compression="gzip")
                except Exception:
                    existing = pd.DataFrame()
            existing_symbols = set()
            eligible_set = set(eligible)
            if not existing.empty and "Ticker" in existing.columns:
                # Remove companies that have left the current eligible universe so a
                # completed manifest and its prepared file describe the same snapshot.
                existing = existing[existing["Ticker"].astype(str).isin(eligible_set)]
                existing_symbols = set(existing["Ticker"].dropna().astype(str))

            cursor = int(previous.get("next_cursor", 0) or 0) % len(eligible)
            if selected_symbols_by_kind is not None:
                requested = set(selected_symbols_by_kind.get(kind, []))
                scan_symbols = [symbol for symbol in eligible if symbol in requested]
                next_cursor = cursor
            elif args.gap_fill:
                missing_symbols = [symbol for symbol in eligible if symbol not in existing_symbols]
                # Persist dispatch counts separately from healthy completed retries.
                # Untouched companies go first; failed requests cannot monopolise
                # the front of the universe. Never infer provider unavailability
                # or technical eligibility from these scheduling counts.
                attempts = {
                    symbol: previous.get("gap_fill_dispatch_counts", {}).get(symbol, 0)
                    for symbol in missing_symbols
                }
                missing_symbols.sort(key=lambda symbol: attempts[symbol])
                if args.max_symbols_per_exchange > 0:
                    scan_symbols = missing_symbols[: args.max_symbols_per_exchange]
                else:
                    scan_symbols = missing_symbols
                next_cursor = 0
            elif args.chunk_size > 0:
                scan_symbols = eligible[cursor:cursor + args.chunk_size]
                next_cursor = cursor + len(scan_symbols)
                if next_cursor >= len(eligible):
                    next_cursor = 0
            else:
                scan_symbols = eligible
                next_cursor = 0

            if not scan_symbols and selected_symbols_by_kind is not None:
                print(
                    f"[{position}/{len(kinds)}] {kind}: no changed fundamentals to refresh",
                    flush=True,
                )
                continue

            if not scan_symbols:
                # Persist the trimmed snapshot even when there is no provider work.
                if not existing.empty:
                    existing.to_csv(target, index=False, compression="gzip")
                completed_at = datetime.now(ZoneInfo("Europe/London")).isoformat(timespec="seconds")
                coverage_count = len(existing_symbols)
                manifest["exchanges"][kind] = {
                    **previous,
                    "label": labels_by_kind[kind],
                    "status": "complete",
                    "completed_at": completed_at,
                    "universe_symbols": len(universe),
                    "eligible_symbols": len(eligible),
                    "coverage_symbols": coverage_count,
                    "coverage_pct": round(coverage_count / len(eligible) * 100, 1),
                    "remaining_symbols": 0,
                    "minimum_market_cap_bn": args.min_market_cap_bn,
                }
                manifest["updated_at"] = completed_at
                save_manifest(manifest_path, manifest)
                print(f"[{position}/{len(kinds)}] {kind}: no gaps remain; complete", flush=True)
                continue

            def report(completed, total, returned):
                if completed == total or completed % 25 == 0:
                    print(
                        f"[{position}/{len(kinds)}] {kind}: {completed}/{total} analysed; {returned} returned",
                        flush=True,
                    )

            if args.gap_fill:
                for symbol in scan_symbols:
                    attempts[symbol] += 1
                previous = {
                    **previous,
                    "gap_fill_dispatch_counts": attempts,
                    "gap_fill_last_dispatched_at": started,
                }
                manifest["exchanges"][kind] = previous
                save_manifest(manifest_path, manifest)

            results = scan_with_outcomes(
                tuple(scan_symbols),
                args.max_symbols,
                args.min_market_cap_bn * 1_000_000_000,
                tuple(sorted(market_caps.items())),
                progress_callback=report,
                max_workers=args.workers,
            )
            diagnostics = results.attrs.get("scan_diagnostics", {})
            # Preserve structured evidence even when a batch returned zero rows.
            previous = {**previous, "diagnostics": diagnostics,
                        "requested_this_run": len(scan_symbols),
                        "returned_this_run": int(diagnostics.get("returned", 0) or 0)}
            manifest["exchanges"][kind] = previous
            save_manifest(manifest_path, manifest)
            if results.empty:
                summary = {k: v for k, v in diagnostics.items() if k != "symbol_outcomes"}
                raise RuntimeError(f"scan returned no results: {summary}")
            if not existing.empty and "Ticker" in existing.columns:
                refreshed_symbols = set(results["Ticker"].dropna().astype(str))
                existing = existing[~existing["Ticker"].astype(str).isin(refreshed_symbols)]
                results = pd.concat([existing, results], ignore_index=True)
            if "Ticker" in results.columns:
                eligible_set = set(eligible)
                results = results[
                    results["Ticker"].astype(str).isin(eligible_set)
                ].drop_duplicates(subset=["Ticker"], keep="last")
            results.to_csv(target, index=False, compression="gzip")
            completed_at = datetime.now(ZoneInfo("Europe/London")).isoformat(timespec="seconds")
            coverage_count = int(results["Ticker"].nunique())
            remaining_symbols = max(len(eligible) - coverage_count, 0)
            status = "complete" if remaining_symbols == 0 else "partial"
            stored_symbols = set(results["Ticker"].astype(str))
            manifest["exchanges"][kind] = {
                "gap_fill_dispatch_counts": {
                    symbol: count
                    for symbol, count in previous.get("gap_fill_dispatch_counts", {}).items()
                    if symbol in eligible and symbol not in stored_symbols
                },
                "gap_fill_last_dispatched_at": previous.get("gap_fill_last_dispatched_at"),
                "label": labels_by_kind[kind],
                "status": status,
                "started_at": started,
                "completed_at": completed_at,
                "universe_symbols": len(universe),
                "eligible_symbols": len(eligible),
                "chunk_start": cursor,
                "chunk_symbols": len(scan_symbols),
                "next_cursor": next_cursor,
                "coverage_symbols": coverage_count,
                "coverage_pct": round(coverage_count / len(eligible) * 100, 1),
                "remaining_symbols": remaining_symbols,
                "requested_this_run": len(scan_symbols),
                "returned_this_run": int(diagnostics.get("returned", 0) or 0),
                "analysed_limit": args.max_symbols,
                "result_rows": len(results),
                "minimum_market_cap_bn": args.min_market_cap_bn,
                "diagnostics": diagnostics,
                "refresh_mode": "changed_fundamentals" if selected_symbols_by_kind is not None else (
                    "gap_fill" if args.gap_fill else "rolling_or_full"
                ),
            }
            manifest["last_completed_exchange"] = kind
            manifest["updated_at"] = completed_at
            save_manifest(manifest_path, manifest)
            print(
                f"[{position}/{len(kinds)}] {kind}: {status.upper()}; "
                f"stored {coverage_count}/{len(eligible)}, {remaining_symbols} gaps remain",
                flush=True,
            )
        except Exception as exc:
            failures += 1
            manifest["exchanges"][kind] = {
                **previous,
                "label": labels_by_kind[kind],
                "status": "failed",
                "started_at": started,
                "failed_at": datetime.now(ZoneInfo("Europe/London")).isoformat(timespec="seconds"),
                "error": f"{exc.__class__.__name__}: {str(exc)[:500]}",
            }
            manifest["updated_at"] = datetime.now(ZoneInfo("Europe/London")).isoformat(timespec="seconds")
            save_manifest(manifest_path, manifest)
            print(f"[{position}/{len(kinds)}] {kind}: FAILED: {exc}", flush=True)

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
