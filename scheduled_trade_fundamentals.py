from __future__ import annotations

import argparse
import json
import logging
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

# Keep Streamlit bare-mode context warnings out of scheduled-job logs.
logging.getLogger("streamlit").setLevel(logging.CRITICAL)
logging.getLogger("streamlit.runtime").setLevel(logging.CRITICAL)

import stock_app


def save_manifest(path: Path, manifest: dict) -> None:
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Prepare nightly Trade fundamental snapshots for the Streamlit app."
    )
    parser.add_argument("--exchange", default="all", help="Universe key or 'all'.")
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--output-dir", default="prepared_trade_fundamentals")
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

    labels_by_kind = {
        kind: label for label, kind in stock_app.EXCHANGE_UNIVERSES.items()
    }
    if args.exchange == "all":
        kinds = list(labels_by_kind)
    elif args.exchange in labels_by_kind:
        kinds = [args.exchange]
    else:
        raise SystemExit(f"Unknown exchange key: {args.exchange}")

    london_now = datetime.now(ZoneInfo("Europe/London"))
    run_date = london_now.date().isoformat()
    hard_failures = 0

    for position, kind in enumerate(kinds, start=1):
        previous = manifest["exchanges"].get(kind, {})
        if args.resume and str(previous.get("completed_at", "")).startswith(run_date):
            print(
                f"[{position}/{len(kinds)}] {kind}: already completed today; skipping",
                flush=True,
            )
            continue

        started = datetime.now(ZoneInfo("Europe/London")).isoformat(timespec="seconds")
        print(
            f"[{position}/{len(kinds)}] {labels_by_kind[kind]}: loading universe",
            flush=True,
        )

        try:
            universe = stock_app.get_universe(kind)
            if not universe:
                raise RuntimeError("exchange universe returned no symbols")

            refreshed = {}
            batch_errors = []
            batch_size = max(1, int(args.batch_size))

            for start in range(0, len(universe), batch_size):
                chunk = universe[start : start + batch_size]
                try:
                    batch = stock_app.trade_tradingview_snapshots(
                        tuple(chunk),
                        kind,
                        stock_app.TRADE_RULEBOOK_BUILD,
                    )
                    refreshed.update(batch)
                except Exception as exc:
                    batch_errors.append(
                        f"{start}-{start + len(chunk) - 1}: "
                        f"{exc.__class__.__name__}: {str(exc)[:200]}"
                    )

                completed = min(start + len(chunk), len(universe))
                print(
                    f"[{position}/{len(kinds)}] {kind}: "
                    f"{completed}/{len(universe)} requested; "
                    f"{len(refreshed)} fundamentals refreshed",
                    flush=True,
                )

            target = output_dir / f"{kind}.csv.gz"
            fresh_frame = pd.DataFrame(
                [
                    stock_app.trade_snapshot_to_record(snapshot)
                    for snapshot in refreshed.values()
                ]
            )

            # Preserve the last known snapshot for any symbol missed by a temporary
            # provider failure instead of deleting good background data.
            existing = pd.DataFrame()
            if target.exists():
                try:
                    existing = pd.read_csv(target, compression="gzip")
                except Exception:
                    existing = pd.DataFrame()

            if not existing.empty and "Ticker" in existing.columns:
                existing = existing[
                    ~existing["Ticker"].astype(str).isin(refreshed.keys())
                ]
                combined = pd.concat([existing, fresh_frame], ignore_index=True)
            else:
                combined = fresh_frame

            if combined.empty:
                raise RuntimeError(
                    "nightly Trade fundamental refresh returned no usable snapshots"
                )

            if "Ticker" in combined.columns:
                current_symbols = set(universe)
                combined = combined[
                    combined["Ticker"].astype(str).isin(current_symbols)
                ].drop_duplicates(subset=["Ticker"], keep="last")

            combined.to_csv(target, index=False, compression="gzip")
            completed_at = datetime.now(
                ZoneInfo("Europe/London")
            ).isoformat(timespec="seconds")

            fresh_count = len(refreshed)
            coverage_count = (
                int(combined["Ticker"].nunique())
                if "Ticker" in combined.columns
                else 0
            )
            manifest["exchanges"][kind] = {
                "label": labels_by_kind[kind],
                "status": "partial" if batch_errors else "complete",
                "started_at": started,
                "completed_at": completed_at,
                "universe_symbols": len(universe),
                "fresh_symbols": fresh_count,
                "fresh_coverage_pct": round(
                    fresh_count / len(universe) * 100, 1
                ),
                "coverage_symbols": coverage_count,
                "coverage_pct": round(
                    coverage_count / len(universe) * 100, 1
                ),
                "batch_size": batch_size,
                "rulebook_build": stock_app.TRADE_RULEBOOK_BUILD,
                "provider": "TradingView batch fundamentals",
                "batch_errors": batch_errors[:10],
            }
            manifest["last_completed_exchange"] = kind
            manifest["updated_at"] = completed_at
            manifest["rulebook_build"] = stock_app.TRADE_RULEBOOK_BUILD
            save_manifest(manifest_path, manifest)

            status_note = "PARTIAL" if batch_errors else "complete"
            print(
                f"[{position}/{len(kinds)}] {kind}: {status_note}; "
                f"fresh {fresh_count}/{len(universe)}, stored {coverage_count}",
                flush=True,
            )
        except Exception as exc:
            hard_failures += 1
            manifest["exchanges"][kind] = {
                **previous,
                "label": labels_by_kind[kind],
                "status": "failed",
                "started_at": started,
                "failed_at": datetime.now(
                    ZoneInfo("Europe/London")
                ).isoformat(timespec="seconds"),
                "error": f"{exc.__class__.__name__}: {str(exc)[:500]}",
                "rulebook_build": stock_app.TRADE_RULEBOOK_BUILD,
            }
            manifest["updated_at"] = datetime.now(
                ZoneInfo("Europe/London")
            ).isoformat(timespec="seconds")
            save_manifest(manifest_path, manifest)
            print(
                f"[{position}/{len(kinds)}] {kind}: FAILED: {exc}",
                flush=True,
            )

    return 1 if hard_failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
