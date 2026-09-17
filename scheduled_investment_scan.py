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


def save_manifest(path: Path, manifest: dict) -> None:
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare exchange investment scans for the Streamlit app.")
    parser.add_argument("--exchange", default="all", help="Universe key or 'all'.")
    parser.add_argument("--max-symbols", type=int, default=0, help="0 analyses the full exchange universe.")
    parser.add_argument("--chunk-size", type=int, default=0, help="Rolling symbols per exchange; 0 disables chunking.")
    parser.add_argument("--min-market-cap-bn", type=float, default=0.5)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--output-dir", default="prepared_scans")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        manifest = {"schema_version": 1, "exchanges": {}}
    manifest.setdefault("exchanges", {})

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
    for position, kind in enumerate(kinds, start=1):
        previous = manifest["exchanges"].get(kind, {})
        if args.resume and str(previous.get("completed_at", "")).startswith(run_date):
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
            cursor = int(previous.get("next_cursor", 0) or 0) % len(eligible)
            if args.chunk_size > 0:
                scan_symbols = eligible[cursor:cursor + args.chunk_size]
                next_cursor = cursor + len(scan_symbols)
                if next_cursor >= len(eligible):
                    next_cursor = 0
            else:
                scan_symbols = eligible
                next_cursor = 0

            def report(completed, total, returned):
                if completed == total or completed % 25 == 0:
                    print(
                        f"[{position}/{len(kinds)}] {kind}: {completed}/{total} analysed; {returned} returned",
                        flush=True,
                    )

            results = stock_app.fundamental_market_scan(
                tuple(scan_symbols),
                args.max_symbols,
                args.min_market_cap_bn * 1_000_000_000,
                tuple(sorted(market_caps.items())),
                progress_callback=report,
                max_workers=args.workers,
            )
            if results.empty:
                diagnostics = results.attrs.get("scan_diagnostics", {})
                raise RuntimeError(f"scan returned no results: {diagnostics}")
            target = output_dir / f"{kind}.csv.gz"
            if target.exists() and args.chunk_size > 0:
                try:
                    existing = pd.read_csv(target, compression="gzip")
                except Exception:
                    existing = pd.DataFrame()
                if not existing.empty and "Ticker" in existing.columns:
                    existing = existing[~existing["Ticker"].astype(str).isin(scan_symbols)]
                    results = pd.concat([existing, results], ignore_index=True)
            results.to_csv(target, index=False, compression="gzip")
            completed_at = datetime.now(ZoneInfo("Europe/London")).isoformat(timespec="seconds")
            manifest["exchanges"][kind] = {
                "label": labels_by_kind[kind],
                "status": "complete",
                "started_at": started,
                "completed_at": completed_at,
                "universe_symbols": len(universe),
                "eligible_symbols": len(eligible),
                "chunk_start": cursor,
                "chunk_symbols": len(scan_symbols),
                "next_cursor": next_cursor,
                "coverage_symbols": int(results["Ticker"].nunique()),
                "coverage_pct": round(results["Ticker"].nunique() / len(eligible) * 100, 1),
                "analysed_limit": args.max_symbols,
                "result_rows": len(results),
                "minimum_market_cap_bn": args.min_market_cap_bn,
                "diagnostics": results.attrs.get("scan_diagnostics", {}),
            }
            manifest["last_completed_exchange"] = kind
            manifest["updated_at"] = completed_at
            save_manifest(manifest_path, manifest)
            print(f"[{position}/{len(kinds)}] {kind}: saved {len(results)} rows", flush=True)
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
