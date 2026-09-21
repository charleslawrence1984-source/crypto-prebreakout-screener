from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd


DATASETS = {
    "trade_fundamentals": Path("prepared_trade_fundamentals"),
    "trade_technicals": Path("prepared_trade_technicals"),
    "investment_fundamentals": Path("prepared_scans"),
    "investment_technicals": Path("prepared_investment_technicals"),
}


def parse_time(value):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None


def manifest_for(path: Path) -> dict:
    target = path / "manifest.json"
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except Exception:
        return {}


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit CL Signal stock-data coverage and freshness.")
    parser.add_argument("--deep", action="store_true")
    parser.add_argument("--output", default="prepared_health/stock_freshness.json")
    args = parser.parse_args()

    now = datetime.now(timezone.utc)
    report = {
        "generated_at": now.isoformat(),
        "mode": "deep" if args.deep else "weekly",
        "datasets": {},
        "issues": [],
    }

    for name, directory in DATASETS.items():
        manifest = manifest_for(directory)
        exchanges = manifest.get("exchanges", {})
        dataset = {
            "manifest_updated_at": manifest.get("updated_at"),
            "exchange_count": len(exchanges),
            "coverage_symbols": 0,
            "eligible_symbols": 0,
            "complete_exchanges": 0,
            "partial_or_failed_exchanges": 0,
            "files_checked": 0,
            "duplicate_tickers": 0,
        }

        for kind, meta in exchanges.items():
            dataset["coverage_symbols"] += int(meta.get("coverage_symbols", 0) or 0)
            dataset["eligible_symbols"] += int(meta.get("eligible_symbols", 0) or 0)
            if str(meta.get("status", "")).lower() == "complete":
                dataset["complete_exchanges"] += 1
            else:
                dataset["partial_or_failed_exchanges"] += 1

            if args.deep:
                file_path = directory / f"{kind}.csv.gz"
                if file_path.exists():
                    dataset["files_checked"] += 1
                    try:
                        frame = pd.read_csv(file_path, compression="gzip")
                        if "Ticker" in frame.columns:
                            duplicates = int(frame["Ticker"].duplicated().sum())
                            dataset["duplicate_tickers"] += duplicates
                            if duplicates:
                                report["issues"].append(
                                    f"{name}/{kind}: {duplicates} duplicate ticker rows"
                                )
                    except Exception as exc:
                        report["issues"].append(
                            f"{name}/{kind}: unreadable data file ({type(exc).__name__})"
                        )

        updated = parse_time(manifest.get("updated_at"))
        if updated is not None:
            age_hours = (now - updated).total_seconds() / 3600
            dataset["manifest_age_hours"] = round(age_hours, 1)
        else:
            dataset["manifest_age_hours"] = None
            report["issues"].append(f"{name}: manifest timestamp unavailable")

        report["datasets"][name] = dataset

    change_file = DATASETS["trade_fundamentals"] / "fundamental_changes.json"
    try:
        changes = json.loads(change_file.read_text(encoding="utf-8"))
        change_time = parse_time(changes.get("generated_at"))
        report["fundamental_change_detector"] = {
            "generated_at": changes.get("generated_at"),
            "age_hours": (
                round((now - change_time).total_seconds() / 3600, 1)
                if change_time is not None else None
            ),
            "changed_companies": sum(
                int(meta.get("changed_count", 0) or 0)
                for meta in changes.get("exchanges", {}).values()
            ),
            "new_companies": sum(
                int(meta.get("new_count", 0) or 0)
                for meta in changes.get("exchanges", {}).values()
            ),
        }
    except Exception:
        report["fundamental_change_detector"] = {"status": "unavailable"}
        report["issues"].append("fundamental change detector file unavailable")

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print(json.dumps(report, indent=2, sort_keys=True))
    return 1 if args.deep and report["issues"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
