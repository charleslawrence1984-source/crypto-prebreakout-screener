import importlib.util
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

import pandas as pd

with patch.dict(sys.modules, {"stock_app": types.ModuleType("stock_app")}):
    spec = importlib.util.spec_from_file_location("investment_technicals", Path(__file__).with_name("scheduled_investment_technicals.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)


class TechnicalTests(unittest.TestCase):
    def test_gate_resume_and_invalidation(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, dst = Path(tmp) / "source", Path(tmp) / "technical"
            src.mkdir()
            rows = pd.DataFrame([
                {"Ticker": "A", "Action": "WAIT", "Hard gates": "PASS", "Price": 10},
                {"Ticker": "B", "Action": "BUY CANDIDATE", "Hard gates": "PASS", "Price": 20},
                {"Ticker": "C", "Action": "PASS", "Hard gates": "PASS", "Price": 30},
                {"Ticker": "D", "Action": "WAIT", "Hard gates": "FAIL", "Price": 40},
            ])
            meta = {"status": "partial", "eligible_symbols": 4, "coverage_symbols": 4, "remaining_symbols": 0}
            def save():
                rows.to_csv(src / "test.csv.gz", index=False)
                (src / "manifest.json").write_text(json.dumps({"updated_at": "2026-09-19", "exchanges": {"test": meta}}))
            calls = []
            def analyse(symbol):
                calls.append(symbol)
                return {"RSI": 50}
            save()
            module.prepare(src, dst, 1, analyse, "v1")
            self.assertEqual(calls, [])
            meta["status"] = "complete"
            save()
            module.prepare(src, dst, 1, analyse, "v1")
            self.assertEqual(calls, ["A"])
            module.prepare(src, dst, 1, analyse, "v1")
            self.assertEqual(calls, ["A", "B"])
            rows.loc[0, "Hard gates"] = "FAIL"
            rows.loc[1, "Price"] = 22
            rows.loc[2, "Action"] = "WAIT"
            save()
            module.prepare(src, dst, 0, analyse, "v1")
            self.assertFalse((dst / "test.csv.gz").exists())
            module.prepare(src, dst, 10, analyse, "v1")
            self.assertEqual(set(pd.read_csv(dst / "test.csv.gz").Ticker), {"B", "C"})
            rows["Hard gates"] = "FAIL"
            save()
            module.prepare(src, dst, 10, analyse, "v1")
            self.assertFalse((dst / "test.csv.gz").exists())

    def test_matching_fingerprint_reconciles_source_timestamp_without_faking_calculation_freshness(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, dst = Path(tmp) / "source", Path(tmp) / "technical"
            src.mkdir()
            data = pd.DataFrame([
                {"Ticker": "AAA", "Action": "BUY CANDIDATE", "Hard gates": "PASS", "Price": 10}
            ])
            data.to_csv(src / "nyse.csv.gz", index=False)
            previous = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
            latest = datetime.now(timezone.utc).isoformat()
            meta = {"status": "complete", "eligible_symbols": 1, "coverage_symbols": 1,
                    "coverage_target_symbols": 1, "provider_deferred_symbols": 0,
                    "remaining_symbols": 0, "completed_at": previous}
            source_manifest = src / "manifest.json"
            source_manifest.write_text(json.dumps({"exchanges": {"nyse": meta}}))
            calls = []
            def analyse(symbol):
                calls.append(symbol)
                return {"rsi": 48.0, "trade_score": 72.0}
            self.assertEqual(module.prepare(src, dst, 1, analyse, "v1"), 0)
            initial = pd.read_csv(dst / "nyse.csv.gz").iloc[0]
            old_fingerprint = initial["Fundamental fingerprint"]
            calculated = initial["Technical calculated at"]
            self.assertEqual(calls, ["AAA"])
            meta["completed_at"] = latest
            source_manifest.write_text(json.dumps({"exchanges": {"nyse": meta}}))
            self.assertEqual(module.prepare(src, dst, 0, analyse, "v1"), 0)
            refreshed = pd.read_csv(dst / "nyse.csv.gz").iloc[0]
            self.assertEqual(calls, ["AAA"], "No new technical analysis for same source fingerprint")
            self.assertEqual(refreshed["Fundamental fingerprint"], old_fingerprint)
            self.assertEqual(refreshed["Fundamental source updated at"], latest)
            self.assertEqual(refreshed["Technical calculated at"], calculated,
                             "Reconciliation must not fake a technical recalculation time")
            status = json.loads((dst / "manifest.json").read_text())["exchanges"]["nyse"]
            self.assertEqual(status["fresh_coverage_symbols"], 1)
            self.assertEqual(status["stale_coverage_symbols"], 0)

    def test_expired_technical_row_is_refreshed_or_stays_nonready_after_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, dst = Path(tmp) / "source", Path(tmp) / "technical"
            src.mkdir()
            pd.DataFrame([{"Ticker": "AAA", "Action": "WAIT", "Hard gates": "PASS",
                           "Price": 10}]).to_csv(src / "nyse.csv.gz", index=False)
            meta = {"status": "complete", "eligible_symbols": 1,
                    "coverage_symbols": 1, "remaining_symbols": 0,
                    "completed_at": datetime.now(timezone.utc).isoformat()}
            (src / "manifest.json").write_text(json.dumps({"exchanges": {"nyse": meta}}))
            calls = []
            def analyse(symbol):
                calls.append(symbol)
                return {"rsi": 48.0, "trade_score": 72.0}
            self.assertEqual(module.prepare(src, dst, 1, analyse, "v1"), 0)
            original = pd.read_csv(dst / "nyse.csv.gz")
            expired = (datetime.now(timezone.utc) - timedelta(hours=130)).isoformat()
            original.loc[0, "Technical calculated at"] = expired
            original.to_csv(dst / "nyse.csv.gz", index=False, compression="gzip")
            self.assertEqual(module.prepare(src, dst, 0, analyse, "v1"), 0)
            self.assertEqual(calls, ["AAA"], "Reconciliation-only must not download candles")
            manifest = json.loads((dst / "manifest.json").read_text())["exchanges"]["nyse"]
            self.assertEqual(manifest["stale_coverage_symbols"], 1)
            self.assertEqual(manifest["status"], "partial")
            # The next bounded batch retries the stale symbol. A provider error
            # must not stamp it as fresh or erase the original evidence.
            def provider_down(symbol):
                raise ValueError("provider temporarily down")
            self.assertEqual(module.prepare(src, dst, 1, provider_down, "v1"), 0)
            unchanged = pd.read_csv(dst / "nyse.csv.gz")
            self.assertEqual(len(unchanged), 1)
            self.assertEqual(unchanged.iloc[0]["Technical calculated at"], expired)
            manifest = json.loads((dst / "manifest.json").read_text())["exchanges"]["nyse"]
            self.assertEqual(manifest["stale_coverage_symbols"], 1)
            self.assertEqual(manifest["status"], "partial")
            self.assertEqual(module.prepare(src, dst, 1, analyse, "v1"), 0)
            recovered = pd.read_csv(dst / "nyse.csv.gz")
            self.assertEqual(len(recovered), 1, "Refreshed symbol must replace its old row")
            self.assertNotEqual(recovered.iloc[0]["Technical calculated at"], expired)
            manifest = json.loads((dst / "manifest.json").read_text())["exchanges"]["nyse"]
            self.assertEqual(manifest["stale_coverage_symbols"], 0)
            self.assertEqual(manifest["fresh_coverage_symbols"], 1)
            self.assertEqual(manifest["status"], "complete")

    def test_missing_history_stays_partial(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, dst = Path(tmp) / "source", Path(tmp) / "technical"
            src.mkdir()
            pd.DataFrame([{"Ticker": "A", "Action": "WAIT", "Hard gates": "PASS", "Price": 10}]).to_csv(src / "test.csv.gz", index=False)
            (src / "manifest.json").write_text(json.dumps({"exchanges": {"test": {"status": "complete", "eligible_symbols": 1, "coverage_symbols": 1, "remaining_symbols": 0}}}))
            module.prepare(src, dst, 10, lambda symbol: None, "v1")
            result = json.loads((dst / "manifest.json").read_text())["exchanges"]["test"]
            self.assertEqual(result["status"], "partial")
            self.assertEqual(result["remaining_symbols"], 1)


    def test_provider_deferred_fundamentals_allow_verified_subset_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, dst = Path(tmp) / "source", Path(tmp) / "technical"
            src.mkdir()
            pd.DataFrame([
                {"Ticker": "A", "Action": "WAIT", "Hard gates": "PASS", "Price": 10},
                {"Ticker": "B", "Action": "BUY CANDIDATE", "Hard gates": "PASS", "Price": 20},
            ]).to_csv(src / "test.csv.gz", index=False)
            meta = {
                "status": "complete", "eligible_symbols": 3,
                "coverage_target_symbols": 2, "coverage_symbols": 2,
                "provider_deferred_symbols": 1, "remaining_symbols": 0,
                "provider_backoff": {"C": {"until": "2026-10-24T00:00:00+00:00"}},
            }
            (src / "manifest.json").write_text(json.dumps({"exchanges": {"test": meta}}))
            calls = []
            def analyse(symbol):
                calls.append(symbol)
                return {"RSI": 51}
            self.assertEqual(module.prepare(src, dst, 10, analyse, "v1"), 0)
            self.assertEqual(calls, ["A", "B"])
            saved = pd.read_csv(dst / "test.csv.gz")
            self.assertEqual(set(saved["Ticker"]), {"A", "B"})
            result = json.loads((dst / "manifest.json").read_text())["exchanges"]["test"]
            self.assertEqual(result["status"], "partial")
            self.assertEqual(result["eligible_symbols"], 2)
            self.assertEqual(result["coverage_symbols"], 2)
            self.assertEqual(result["remaining_symbols"], 0)
            self.assertEqual(result["source_provider_deferred_symbols"], 1)
            self.assertEqual(result["source_total_eligible_symbols"], 3)

    def test_inconsistent_fundamentals_or_retryable_gaps_never_scan(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, dst = Path(tmp) / "source", Path(tmp) / "technical"
            src.mkdir()
            pd.DataFrame([
                {"Ticker": "A", "Action": "WAIT", "Hard gates": "PASS", "Price": 10},
                {"Ticker": "B", "Action": "WAIT", "Hard gates": "PASS", "Price": 20},
            ]).to_csv(src / "test.csv.gz", index=False)
            meta = {
                "status": "complete", "eligible_symbols": 3,
                "coverage_target_symbols": 2, "coverage_symbols": 1,
                "provider_deferred_symbols": 1, "remaining_symbols": 0,
            }
            manifest = src / "manifest.json"
            calls = []
            def analyse(symbol):
                calls.append(symbol)
                return {"RSI": 51}

            # An inaccurate claimed source count cannot be used as a backdoor.
            manifest.write_text(json.dumps({"exchanges": {"test": meta}}))
            self.assertEqual(module.prepare(src, dst, 10, analyse, "v1"), 0)
            self.assertEqual(calls, [])
            self.assertEqual(json.loads((dst / "manifest.json").read_text())["exchanges"]["test"]["status"], "deferred_fundamentals")

            # Correct counts do not override an unfinished source pass.
            meta.update(coverage_symbols=2, remaining_symbols=1, status="partial")
            manifest.write_text(json.dumps({"exchanges": {"test": meta}}))
            self.assertEqual(module.prepare(src, dst, 10, analyse, "v1"), 0)
            self.assertEqual(calls, [])
            self.assertEqual(json.loads((dst / "manifest.json").read_text())["exchanges"]["test"]["status"], "deferred_fundamentals")

            # Even if source claims 'complete', unaccounted missing symbols block it.
            meta.update(status="complete", remaining_symbols=0, provider_deferred_symbols=0)
            manifest.write_text(json.dumps({"exchanges": {"test": meta}}))
            self.assertEqual(module.prepare(src, dst, 10, analyse, "v1"), 0)
            self.assertEqual(calls, [])
            self.assertEqual(json.loads((dst / "manifest.json").read_text())["exchanges"]["test"]["status"], "deferred_fundamentals")


if __name__ == "__main__":
    unittest.main()
