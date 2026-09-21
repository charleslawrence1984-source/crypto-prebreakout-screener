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


FUNDAMENTAL_CHANGE_COLUMNS = [
    "Currency", "ROIC", "ROE", "Operating margin", "FCF margin",
    "Annual FCF", "Annual net income", "Net debt / FCF",
    "Interest coverage", "No interest expense", "Current ratio",
    "Revenue growth", "Earnings growth", "Operating growth",
    "Growth source", "Share change", "Distribution ratio",
    "Missing hard inputs", "Trailing FCF",
]


def _normalise_change_value(value):
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass
    if isinstance(value, float):
        return round(value, 8)
    return value


def _fundamental_fingerprint(row: dict) -> str:
    payload = {
        column: _normalise_change_value(row.get(column))
        for column in FUNDAMENTAL_CHANGE_COLUMNS
    }
    return json.dumps(payload, sort_keys=True, default=str)


def save_manifest(path: Path, manifest: dict) -> None:
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Prepare nightly Trade fundamental snapshots for the Streamlit app."
    )
    parser.add_argument("--exchange", default="all", help="Universe key or 'all'.")
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument(
        "--max-symbols-per-exchange",
        type=int,
        default=0,
        help="0 refreshes the full exchange; otherwise fill at most this many missing symbols per exchange.",
    )
    parser.add_argument(
        "--closed-markets-only",
        action="store_true",
        help="During Friday evening backfill, defer North America until its regular session has closed.",
    )
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

    changes_path = output_dir / "fundamental_changes.json"
    changes = {
        "generated_at": datetime.now(ZoneInfo("Europe/London")).isoformat(timespec="seconds"),
        "rulebook_build": stock_app.TRADE_RULEBOOK_BUILD,
        "exchanges": {},
    }

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

    north_america = {"nasdaq", "nyse", "otc", "tsx"}

    for position, kind in enumerate(kinds, start=1):
        previous = manifest["exchanges"].get(kind, {})

        if args.closed_markets_only and kind in north_america:
            # US/Canada cash sessions are still open during Friday evening in the UK.
            # On weekends everything is closed, so North America becomes eligible.
            if london_now.weekday() < 5 and (
                london_now.hour < 21
                or (london_now.hour == 21 and london_now.minute < 15)
            ):
                print(
                    f"[{position}/{len(kinds)}] {kind}: market still open; deferred",
                    flush=True,
                )
                continue

        if (
            args.resume
            and str(previous.get("completed_at", "")).startswith(run_date)
            and str(previous.get("rulebook_build", "")) == stock_app.TRADE_RULEBOOK_BUILD
            and not args.closed_markets_only
        ):
            print(
                f"[{position}/{len(kinds)}] {kind}: already refreshed today; skipping",
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

            target = output_dir / f"{kind}.csv.gz"
            existing = pd.DataFrame()
            if target.exists():
                try:
                    existing = pd.read_csv(target, compression="gzip")
                except Exception:
                    existing = pd.DataFrame()

            existing_symbols = set()
            previous_fingerprints = {}
            if not existing.empty and "Ticker" in existing.columns:
                existing_symbols = set(existing["Ticker"].dropna().astype(str))
                previous_fingerprints = {
                    str(row.get("Ticker")): _fundamental_fingerprint(row)
                    for row in existing.to_dict(orient="records")
                    if row.get("Ticker")
                }

            missing_symbols = [
                symbol for symbol in universe if symbol not in existing_symbols
            ]
            if args.max_symbols_per_exchange > 0:
                scan_symbols = missing_symbols[: args.max_symbols_per_exchange]
            else:
                # The normal nightly updater refreshes the full exchange, while the
                # weekend backfill works only on gaps.
                scan_symbols = (
                    missing_symbols
                    if args.closed_markets_only
                    else list(universe)
                )

            if not scan_symbols:
                completed_at = datetime.now(
                    ZoneInfo("Europe/London")
                ).isoformat(timespec="seconds")
                coverage_count = len(existing_symbols.intersection(set(universe)))
                manifest["exchanges"][kind] = {
                    **previous,
                    "label": labels_by_kind[kind],
                    "status": "complete",
                    "completed_at": completed_at,
                    "universe_symbols": len(universe),
                    "coverage_symbols": coverage_count,
                    "coverage_pct": round(
                        coverage_count / len(universe) * 100, 1
                    ),
                    "remaining_symbols": 0,
                    "rulebook_build": stock_app.TRADE_RULEBOOK_BUILD,
                    "provider": "TradingView batch fundamentals",
                }
                manifest["updated_at"] = completed_at
                save_manifest(manifest_path, manifest)
                print(
                    f"[{position}/{len(kinds)}] {kind}: no gaps remain; complete",
                    flush=True,
                )
                continue

            refreshed = {}
            batch_errors = []
            batch_size = max(1, int(args.batch_size))

            for start in range(0, len(scan_symbols), batch_size):
                chunk = scan_symbols[start : start + batch_size]
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

                completed = min(start + len(chunk), len(scan_symbols))
                print(
                    f"[{position}/{len(kinds)}] {kind}: "
                    f"{completed}/{len(scan_symbols)} requested this run; "
                    f"{len(refreshed)} fundamentals returned",
                    flush=True,
                )

            fresh_frame = pd.DataFrame(
                [
                    stock_app.trade_snapshot_to_record(snapshot)
                    for snapshot in refreshed.values()
                ]
            )

            changed_symbols = []
            new_symbols = []
            if not fresh_frame.empty:
                for row in fresh_frame.to_dict(orient="records"):
                    symbol = str(row.get("Ticker") or "")
                    if not symbol:
                        continue
                    old_fp = previous_fingerprints.get(symbol)
                    new_fp = _fundamental_fingerprint(row)
                    if old_fp is None:
                        new_symbols.append(symbol)
                    elif old_fp != new_fp:
                        changed_symbols.append(symbol)

            changes["exchanges"][kind] = {
                "label": labels_by_kind[kind],
                "changed_symbols": sorted(changed_symbols),
                "new_symbols": sorted(new_symbols),
                "changed_count": len(changed_symbols),
                "new_count": len(new_symbols),
            }
            changes["generated_at"] = datetime.now(
                ZoneInfo("Europe/London")
            ).isoformat(timespec="seconds")
            save_manifest(changes_path, changes)

            # Preserve the last known snapshot for any symbol missed by a temporary
            # provider failure instead of deleting good background data.
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
            remaining_symbols = max(len(universe) - coverage_count, 0)
            status = "complete" if remaining_symbols == 0 else "partial"
            manifest["exchanges"][kind] = {
                "label": labels_by_kind[kind],
                "status": status,
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
                "remaining_symbols": remaining_symbols,
                "requested_this_run": len(scan_symbols),
                "batch_size": batch_size,
                "rulebook_build": stock_app.TRADE_RULEBOOK_BUILD,
                "provider": "TradingView batch fundamentals",
                "batch_errors": batch_errors[:10],
            }
            manifest["last_completed_exchange"] = kind
            manifest["updated_at"] = completed_at
            manifest["rulebook_build"] = stock_app.TRADE_RULEBOOK_BUILD
            save_manifest(manifest_path, manifest)

            status_note = status.upper()
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

    changes["generated_at"] = datetime.now(
        ZoneInfo("Europe/London")
    ).isoformat(timespec="seconds")
    save_manifest(changes_path, changes)
    return 1 if hard_failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
