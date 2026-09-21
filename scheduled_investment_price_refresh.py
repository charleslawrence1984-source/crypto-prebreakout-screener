from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

import stock_app


def save_manifest(path: Path, manifest: dict) -> None:
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Refresh closing prices and price-dependent Investment decisions."
    )
    parser.add_argument("--input-dir", default="prepared_scans")
    args = parser.parse_args()

    input_dir = Path(args.input_dir)
    manifest_path = input_dir / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise SystemExit(f"Cannot read {manifest_path}: {exc}")

    failures = 0
    london_now = datetime.now(ZoneInfo("Europe/London"))
    refreshed_at = london_now.isoformat(timespec="seconds")

    for kind, label in {
        kind: label for label, kind in stock_app.EXCHANGE_UNIVERSES.items()
    }.items():
        path = input_dir / f"{kind}.csv.gz"
        if not path.exists():
            continue
        try:
            frame = pd.read_csv(path, compression="gzip")
            refreshed, diagnostics = stock_app.refresh_prepared_investment_prices(frame)
            refreshed.to_csv(path, index=False, compression="gzip")

            meta = manifest.setdefault("exchanges", {}).setdefault(kind, {})
            meta["price_refreshed_at"] = refreshed_at
            meta["price_refresh_status"] = (
                "complete"
                if diagnostics.get("missing", 0) == 0
                and diagnostics.get("rate_limit_errors", 0) == 0
                else "partial"
            )
            meta["price_refresh_updated"] = int(diagnostics.get("updated", 0) or 0)
            meta["price_refresh_missing"] = int(diagnostics.get("missing", 0) or 0)
            meta["price_refresh_rate_limit_errors"] = int(
                diagnostics.get("rate_limit_errors", 0) or 0
            )
            print(
                f"{label}: repriced {meta['price_refresh_updated']} rows; "
                f"{meta['price_refresh_missing']} missing",
                flush=True,
            )
        except Exception as exc:
            failures += 1
            meta = manifest.setdefault("exchanges", {}).setdefault(kind, {})
            meta["price_refreshed_at"] = refreshed_at
            meta["price_refresh_status"] = "failed"
            meta["price_refresh_error"] = f"{type(exc).__name__}: {str(exc)[:300]}"
            print(f"{label}: price refresh failed: {exc}", flush=True)

    manifest["price_refreshed_at"] = refreshed_at
    manifest["price_refresh_timezone"] = "Europe/London"
    save_manifest(manifest_path, manifest)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
