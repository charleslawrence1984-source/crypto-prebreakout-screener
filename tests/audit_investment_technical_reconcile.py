"""Safely dry-run source-stamp reconciliation against the ACTUAL published caches.

Only a temporary copy of technical rows is written. Never hits Yahoo or
modifies prepared files, brokerage orders, or any saved portfolio.
"""
from __future__ import annotations

import json
import shutil
import tempfile
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scheduled_investment_technicals import (
    BUILD, complete_exchange, eligible_rows, fingerprint, prepare,
)


def main():
    root = Path(__file__).resolve().parents[1]
    source = root / "prepared_scans"
    cached_root = root / "prepared_investment_technicals"
    fundamentals = json.loads((source / "manifest.json").read_text())
    before = json.loads((cached_root / "manifest.json").read_text())
    repairs = 0
    invalidated = 0
    confirmed = 0
    comparisons = {}
    with tempfile.TemporaryDirectory() as scratch:
        output = Path(scratch) / "technicals"
        shutil.copytree(cached_root, output)

        def never_download(symbol):
            raise AssertionError("Reconciliation-only audit must never request " + symbol)

        assert prepare(source, output, 0, never_download, BUILD) == 0
        after = json.loads((output / "manifest.json").read_text())
        for kind, meta in fundamentals["exchanges"].items():
            src = source / f"{kind}.csv.gz"
            if not src.exists():
                continue
            frame = pd.read_csv(src)
            if not complete_exchange(meta, frame):
                continue
            eligible = eligible_rows(frame)
            fp = {str(row["Ticker"]): fingerprint(row) for _, row in eligible.iterrows()}
            p = cached_root / f"{kind}.csv.gz"
            q = output / f"{kind}.csv.gz"
            old = pd.read_csv(p) if p.exists() else pd.DataFrame()
            new = pd.read_csv(q) if q.exists() else pd.DataFrame()
            old_rows = {str(row["Ticker"]): row for _, row in old.iterrows()}
            new_rows = {str(row["Ticker"]): row for _, row in new.iterrows()}
            count = {"retained": 0, "source_stamp_reconciled": 0, "invalidated": 0}
            for ticker, existing in old_rows.items():
                updated = new_rows.get(ticker)
                if updated is None:
                    count["invalidated"] += 1
                    invalidated += 1
                    continue
                assert ticker in fp
                assert str(updated["Fundamental fingerprint"]) == fp[ticker]
                assert str(existing["Fundamental fingerprint"]) == fp[ticker]
                assert str(updated["Fundamental source updated at"]) == str(meta["completed_at"])
                assert str(updated["Technical calculated at"]) == str(existing["Technical calculated at"]), (
                    "Dry-run stamped a false new technical calculation date for " + ticker
                )
                count["retained"] += 1
                confirmed += 1
                if str(existing["Fundamental source updated at"]) != str(updated["Fundamental source updated at"]):
                    count["source_stamp_reconciled"] += 1
                    repairs += 1
            assert after["exchanges"][kind]["coverage_symbols"] == len(new)
            if count["retained"] or count["invalidated"]:
                comparisons[kind] = count
    print(json.dumps({
        "source": "offline copy of actual GitHub prepared caches",
        "old_manifest": before.get("updated_at"),
        "reconciled_source_stamps": repairs,
        "retained_matching_fingerprint_rows": confirmed,
        "invalidated_changed_fingerprint_rows": invalidated,
        "per_exchange": comparisons,
        "production_files_modified": False,
        "live_downloads": 0,
    }, indent=2, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
