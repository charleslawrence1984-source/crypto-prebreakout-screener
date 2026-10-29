"""Read-only Investment fundamental/technical dispatch diagnostic for Pension #60.

Reports source-contract and cache state, NOT a BUY readiness verdict. No provider
requests, Streamlit import, workflow dispatch, or writes to prepared data.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import json
from pathlib import Path


def source_rows(path: Path):
    """Return (count, unique_valid_tickers), or (None, False) if absent."""
    if not path.is_file():
        return None, False
    with gzip.open(path, "rt", newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames or "Ticker" not in reader.fieldnames:
            return 0, False
        symbols = [row.get("Ticker", "") for row in reader]
    return len(symbols), all(bool(symbol) for symbol in symbols) and len(set(symbols)) == len(symbols)


def audit_exchange(source: dict, technical: dict, row_count, unique_tickers: bool) -> dict:
    """Mirror the worker's conservative complete_exchange admission contract."""
    reasons = []
    if source.get("status") != "complete":
        reasons.append("source_status_not_complete")
    if source.get("remaining_symbols") != 0:
        reasons.append("source_remaining_not_zero")
    total = source.get("eligible_symbols")
    covered = source.get("coverage_symbols")
    deferred = source.get("provider_deferred_symbols", 0)
    target = source.get("coverage_target_symbols", total)
    if any(type(v) is not int or v < 0 for v in (total, covered, deferred, target)):
        reasons.append("invalid_source_count_contract")
    elif target + deferred != total or covered != target:
        reasons.append("source_count_contract_mismatch")
    if row_count is None:
        reasons.append("source_csv_missing")
    elif row_count != covered or not unique_tickers:
        reasons.append("source_csv_count_or_ticker_mismatch")
    eligible = technical.get("eligible_symbols")
    coverage = technical.get("coverage_symbols")
    fresh = technical.get("fresh_coverage_symbols")
    remaining = technical.get("remaining_symbols")
    return {
        "source_status": source.get("status"),
        "source_completed_at": source.get("completed_at"),
        "source_eligible": total,
        "source_covered": covered,
        "source_target": target,
        "source_provider_deferred": deferred,
        "source_remaining": source.get("remaining_symbols"),
        "source_csv_rows": row_count,
        "worker_dispatch_allowed": not reasons,
        "worker_blockers": reasons,
        "technical_status": technical.get("status"),
        "technical_eligible": eligible,
        "technical_covered": coverage,
        "technical_fresh": fresh,
        "technical_remaining": remaining,
        "technical_requested_last_run": technical.get("requested_this_run"),
        "technical_manifest_source_at": technical.get("source_fundamentals_updated_at"),
        "buy_readiness_assessed": False,
    }


def audit_root(root: Path, exchanges: list[str]) -> dict:
    source = json.loads((root / "prepared_scans" / "manifest.json").read_text())
    tech = json.loads((root / "prepared_investment_technicals" / "manifest.json").read_text())
    output = {
        "scope": "Pension Investment source dispatch diagnostic only; NOT BUY verification",
        "fundamental_manifest_at": source.get("updated_at"),
        "technical_manifest_at": tech.get("updated_at"),
        "exchanges": {},
        "data_modified": False,
    }
    for exchange in exchanges:
        count, unique = source_rows(root / "prepared_scans" / f"{exchange}.csv.gz")
        output["exchanges"][exchange] = audit_exchange(
            source.get("exchanges", {}).get(exchange, {}),
            tech.get("exchanges", {}).get(exchange, {}), count, unique,
        )
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--exchanges", default="nasdaq,nyse,lse",
                        help="Explicit comma-separated exchange keys (default: NASDAQ, NYSE, LSE)")
    args = parser.parse_args()
    exchanges = [item.strip().lower() for item in args.exchanges.split(",") if item.strip()]
    if not exchanges:
        parser.error("At least one exchange is required")
    print(json.dumps(audit_root(args.root, exchanges), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
