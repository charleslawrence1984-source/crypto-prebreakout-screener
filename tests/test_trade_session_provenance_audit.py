"""Source-provenance research fixture tests, no network or trading."""
import csv
import gzip
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from trade_session_provenance_audit import audit, read_venue


class TradeSessionProvenanceTests(unittest.TestCase):
    def make_gzip(self, directory, name, rows, fields):
        target = Path(directory) / (name + ".csv.gz")
        with gzip.open(target, "wt", encoding="utf-8", newline="") as fp:
            writer = csv.DictWriter(fp, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
        return target

    def metadata(self, coverage=1, status="complete", build="2026.10.02.03"):
        return {"eligible_symbols": coverage, "coverage_symbols": coverage,
                "rulebook_build": build, "status": status, "remaining_symbols": 0}

    def at(self):
        return datetime(2026, 10, 29, 10, tzinfo=timezone.utc)

    def test_calculation_time_does_not_count_as_exchange_last_bar(self):
        with tempfile.TemporaryDirectory() as root:
            p = self.make_gzip(root, "nasdaq",
                               [{"Ticker": "AAPL", "Technical calculated at": "2026-10-29T01:00:00Z"}],
                               ["Ticker", "Technical calculated at"])
            data = read_venue(p, self.metadata(), "2026.10.02.03", self.at())
            self.assertEqual(data["rows_without_last_bar"], 1)
            self.assertIn("LAST_COMPLETED_BAR_MISSING", data["flags"])
            self.assertFalse(data["last_completed_exchange_session_independently_verified"])

    def test_even_reported_bar_never_counts_as_independently_verified(self):
        with tempfile.TemporaryDirectory() as root:
            p = self.make_gzip(root, "nasdaq",
                               [{"Ticker": "AAPL", "Last completed bar": "2026-10-28"}],
                               ["Ticker", "Last completed bar"])
            data = read_venue(p, self.metadata(), "2026.10.02.03", self.at())
            self.assertEqual(data["rows_without_last_bar"], 0)
            self.assertFalse(data["last_completed_exchange_session_independently_verified"])

    def test_future_claim_and_duplicate_ticker_fail_diagnostic(self):
        with tempfile.TemporaryDirectory() as root:
            p = self.make_gzip(root, "nyse",
                               [{"Ticker": "MSFT", "Last completed bar": "2026-11-01"},
                                {"Ticker": "MSFT", "Last completed bar": "2026-11-01"}],
                               ["Ticker", "Last completed bar"])
            d = read_venue(p, self.metadata(2), "2026.10.02.03", self.at())
            self.assertEqual(d["reported_future_last_bar"], 2)
            self.assertEqual(d["duplicate_ticker_rows"], 1)
            self.assertIn("FUTURE_REPORTED_BAR", d["flags"])
            self.assertIn("DUPLICATE_TICKER", d["flags"])

    def test_manifest_old_build_partial_and_cache_rows_mismatch(self):
        with tempfile.TemporaryDirectory() as root:
            p = self.make_gzip(root, "gettex",
                               [{"Ticker": "TEST", "Rulebook build": "2026.09.29.13"}],
                               ["Ticker", "Rulebook build"])
            meta = self.metadata(2, status="partial", build="2026.09.29.13")
            meta["remaining_symbols"] = 1
            d = read_venue(p, meta, "2026.10.02.03", self.at())
            for flag in ("MANIFEST_OLD_RULEBOOK", "MANIFEST_INCOMPLETE",
                         "MANIFEST_COVERAGE_ROW_COUNT_MISMATCH", "ROW_OLD_RULEBOOK"):
                self.assertIn(flag, d["flags"])

    def test_missing_file_has_explicit_failure(self):
        with tempfile.TemporaryDirectory() as root:
            d = read_venue(Path(root) / "missing.csv.gz", self.metadata(),
                           "2026.10.02.03", self.at())
            self.assertIn("MISSING_TECHNICAL_GZIP", d["flags"])

    def test_entire_report_stays_not_verified(self):
        with tempfile.TemporaryDirectory() as root:
            p = Path(root) / "prepared_trade_technicals"
            p.mkdir()
            self.make_gzip(p, "nasdaq", [{"Ticker": "AAPL"}], ["Ticker"])
            (p / "manifest.json").write_text(json.dumps({
                "rulebook_build": "2026.10.02.03",
                "exchanges": {"nasdaq": self.metadata()},
            }))
            report = audit(Path(root), self.at())
            self.assertEqual(report["live_trade_readiness"], "NOT_VERIFIED")
            self.assertEqual(report["summary"]["rows_without_explicit_last_completed_bar"], 1)
            self.assertIn("nasdaq", report["summary"]["venues_with_missing_session_provenance"])


if __name__ == "__main__":
    unittest.main()
