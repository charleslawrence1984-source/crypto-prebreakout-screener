from __future__ import annotations

import argparse
import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

logging.getLogger("streamlit").setLevel(logging.CRITICAL)
import stock_app

BUILD = "investment-technicals-2026.09.19.1"


def eligible_rows(frame):
    required = {"Ticker", "Action", "Hard gates", "Price"}
    if not required.issubset(frame.columns):
        if frame.empty:
            return frame.iloc[0:0]
        raise ValueError("Fundamental file lacks required eligibility columns")
    return frame[
        frame["Hard gates"].eq("PASS")
        & frame["Action"].isin(["BUY CANDIDATE", "WAIT"])
        & pd.to_numeric(frame["Price"], errors="coerce").gt(0)
        & frame["Ticker"].notna()
    ].drop_duplicates("Ticker", keep="last")


def complete_exchange(meta, frame):
    total = meta.get("eligible_symbols")
    return (
        meta.get("status") == "complete"
        and total is not None
        and meta.get("remaining_symbols") == 0
        and meta.get("coverage_symbols") == total
        and "Ticker" in frame
        and frame["Ticker"].nunique() == total
    )


def fingerprint(row):
    return hashlib.sha256(row.to_json().encode()).hexdigest()


def reconcile(frame, fingerprints, build):
    required = {"Ticker", "Fundamental fingerprint", "Technical build"}
    if not required.issubset(frame.columns):
        return pd.DataFrame()
    return frame[
        frame["Ticker"].map(fingerprints).eq(frame["Fundamental fingerprint"])
        & frame["Ticker"].isin(fingerprints)
        & frame["Technical build"].eq(build)
    ].drop_duplicates("Ticker", keep="last")


def prepare(source_dir, output_dir, limit, analyse, build):
    source = json.loads((source_dir / "manifest.json").read_text())
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {"exchanges": {}}
    failures = 0
    kinds = set(source["exchanges"]) | set(manifest["exchanges"])
    for kind in sorted(kinds):
        meta = source["exchanges"].get(kind, {})
        target = output_dir / f"{kind}.csv.gz"
        src = source_dir / f"{kind}.csv.gz"
        try:
            frame = pd.read_csv(src) if src.exists() else pd.DataFrame()
            eligible = eligible_rows(frame)
            fingerprints = {str(row["Ticker"]): fingerprint(row) for _, row in eligible.iterrows()}
            cached = pd.read_csv(target) if target.exists() else pd.DataFrame()
            cached = reconcile(cached, fingerprints, build)
            # Persist invalidation even when zero companies remain eligible.
            if cached.empty:
                target.unlink(missing_ok=True)
            else:
                cached.to_csv(target, index=False, compression="gzip")
            ready = complete_exchange(meta, frame)
            timestamp = meta.get("completed_at") or source.get("updated_at")
            covered = set(cached["Ticker"]) if not cached.empty else set()
            attempts = manifest["exchanges"].get(kind, {}).get("dispatch_counts", {})
            missing = sorted(set(fingerprints) - covered, key=lambda s: (attempts.get(s, 0), s))
            selected = missing[:limit] if ready else []
            rows, errors = [], []
            for symbol in selected:
                attempts[symbol] = attempts.get(symbol, 0) + 1
                try:
                    technical = analyse(symbol)
                    if not technical:
                        raise ValueError("No usable technical price history")
                    rows.append({
                        **technical, "Ticker": symbol,
                        "Technical build": build,
                        "Fundamental fingerprint": fingerprints[symbol],
                        "Fundamental source updated at": timestamp,
                        "Technical calculated at": datetime.now(timezone.utc).isoformat(),
                    })
                except Exception as exc:
                    errors.append({"symbol": symbol, "error": str(exc)[:200]})
            combined = pd.concat([cached, pd.DataFrame(rows)], ignore_index=True)
            if not combined.empty:
                combined.to_csv(target, index=False, compression="gzip")
            coverage = len(combined)
            remaining = len(fingerprints) - coverage
            manifest["exchanges"][kind] = {
                "status": ("complete" if remaining == 0 else "partial") if ready else "deferred_fundamentals",
                "eligible_symbols": len(fingerprints),
                "coverage_symbols": coverage,
                "remaining_symbols": remaining,
                "requested_this_run": len(selected),
                "returned_this_run": len(rows),
                "errors": errors,
                "source_fundamentals_updated_at": timestamp,
                "technical_build": build,
                "dispatch_counts": {s: attempts.get(s, 0) for s in missing if s not in {r["Ticker"] for r in rows}},
            }
            print(f"{kind}: {manifest['exchanges'][kind]['status']} {coverage}/{len(fingerprints)}", flush=True)
        except Exception as exc:
            # Unreadable fundamentals cannot support a usable technical cache.
            target.unlink(missing_ok=True)
            manifest["exchanges"][kind] = {"status": "failed", "error": str(exc)[:500]}
            failures += 1
        manifest["updated_at"] = datetime.now(timezone.utc).isoformat()
        manifest["technical_build"] = build
        manifest["fundamental_coverage_mode"] = "completed-exchanges-only"
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return int(failures > 0)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--fundamentals-dir", default="prepared_scans")
    parser.add_argument("--output-dir", default="prepared_investment_technicals")
    parser.add_argument("--max-symbols-per-exchange", type=int, default=50)
    parser.add_argument("--reconcile-only", action="store_true")
    args = parser.parse_args()
    if args.max_symbols_per_exchange < 1:
        parser.error("batch size must be positive")
    # Bind results to the exact calculation code, as well as the cache schema.
    app_hash = hashlib.sha256(Path(stock_app.__file__).read_bytes()).hexdigest()[:16]
    return prepare(Path(args.fundamentals_dir), Path(args.output_dir),
                   0 if args.reconcile_only else args.max_symbols_per_exchange,
                   stock_app.technical_analysis, BUILD + "-" + app_hash)


if __name__ == "__main__":
    raise SystemExit(main())
