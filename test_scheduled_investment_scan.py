from __future__ import annotations

import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

# The scheduled job only needs these stock_app entry points.  A tiny module stub
# keeps this unit test independent of Streamlit/Plotly runtime dependencies.
stock_app_stub = types.ModuleType("stock_app")
stock_app_stub.EXCHANGE_UNIVERSES = {"Test Exchange": "test"}
stock_app_stub.get_universe = lambda _kind: []
stock_app_stub.get_universe_market_caps = lambda _kind: {}
stock_app_stub.fundamental_market_scan = lambda *_args, **_kwargs: pd.DataFrame()
sys.modules.setdefault("stock_app", stock_app_stub)

import scheduled_investment_scan as job


class InvestmentBackfillTests(unittest.TestCase):
    def run_job(self, output_dir: Path, returned_symbols: list[str]) -> tuple[int, tuple[str, ...]]:
        requested: tuple[str, ...] = ()

        def fake_scan(symbols, *_args, **_kwargs):
            nonlocal requested
            requested = symbols
            frame = pd.DataFrame(
                [{"Ticker": symbol, "Action": "WAIT"} for symbol in returned_symbols]
            )
            frame.attrs["scan_diagnostics"] = {
                "requested": len(symbols),
                "returned": len(frame),
                "rate_limit_errors": 0,
            }
            return frame

        argv = [
            "scheduled_investment_scan.py",
            "--exchange",
            "test",
            "--output-dir",
            str(output_dir),
            "--max-symbols-per-exchange",
            "2",
            "--gap-fill",
            "--resume",
        ]
        with (
            patch.object(sys, "argv", argv),
            patch.object(job.stock_app, "EXCHANGE_UNIVERSES", {"Test Exchange": "test"}),
            patch.object(job.stock_app, "get_universe", return_value=["AAA", "BBB", "CCC"]),
            patch.object(
                job.stock_app,
                "get_universe_market_caps",
                return_value={"AAA": 1_000_000_000, "BBB": 1_000_000_000, "CCC": 1_000_000_000},
            ),
            patch.object(job.stock_app, "fundamental_market_scan", side_effect=fake_scan),
        ):
            return job.main(), requested

    def test_gap_fill_requests_only_missing_symbols_and_merges_without_duplicates(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            pd.DataFrame([{"Ticker": "AAA", "Action": "WAIT"}]).to_csv(
                output_dir / "test.csv.gz", index=False, compression="gzip"
            )

            exit_code, requested = self.run_job(output_dir, ["BBB", "CCC"])

            self.assertEqual(exit_code, 0)
            self.assertEqual(requested, ("BBB", "CCC"))
            saved = pd.read_csv(output_dir / "test.csv.gz", compression="gzip")
            self.assertEqual(set(saved["Ticker"]), {"AAA", "BBB", "CCC"})
            self.assertEqual(saved["Ticker"].nunique(), 3)
            manifest = json.loads((output_dir / "manifest.json").read_text())
            exchange = manifest["exchanges"]["test"]
            self.assertEqual(exchange["status"], "complete")
            self.assertEqual(exchange["remaining_symbols"], 0)

    def test_provider_miss_stays_partial_and_is_not_falsely_marked_complete(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            pd.DataFrame([{"Ticker": "AAA", "Action": "WAIT"}]).to_csv(
                output_dir / "test.csv.gz", index=False, compression="gzip"
            )

            exit_code, requested = self.run_job(output_dir, ["BBB"])

            self.assertEqual(exit_code, 0)
            self.assertEqual(requested, ("BBB", "CCC"))
            manifest = json.loads((output_dir / "manifest.json").read_text())
            exchange = manifest["exchanges"]["test"]
            self.assertEqual(exchange["status"], "partial")
            self.assertEqual(exchange["coverage_symbols"], 2)
            self.assertEqual(exchange["remaining_symbols"], 1)


if __name__ == "__main__":
    unittest.main()
