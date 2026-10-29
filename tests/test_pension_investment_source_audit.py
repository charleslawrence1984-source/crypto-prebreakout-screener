"""Offline regressions for read-only Pension Investment source audit."""
import csv
import gzip
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "pension_investment_source_audit.py"
spec = importlib.util.spec_from_file_location("pension_source_audit", SCRIPT)
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


def source(**changes):
    value = {"status": "complete", "remaining_symbols": 0,
             "eligible_symbols": 3, "coverage_symbols": 2,
             "coverage_target_symbols": 2, "provider_deferred_symbols": 1}
    value.update(changes)
    return value


class SourceAuditTests(unittest.TestCase):
    def test_verified_deferred_count_contract_permits_dispatch_not_buy(self):
        result = audit.audit_exchange(source(), {"status": "partial", "eligible_symbols": 2,
                                                 "coverage_symbols": 1, "fresh_coverage_symbols": 0}, 2, True)
        self.assertTrue(result["worker_dispatch_allowed"])
        self.assertFalse(result["buy_readiness_assessed"])
        self.assertEqual(result["technical_fresh"], 0)

    def test_recent_partial_fundamentals_block_worker_even_if_csv_exists(self):
        result = audit.audit_exchange(source(status="partial", remaining_symbols=1,
                                                    coverage_symbols=1), {}, 1, True)
        self.assertFalse(result["worker_dispatch_allowed"])
        self.assertIn("source_status_not_complete", result["worker_blockers"])
        self.assertIn("source_remaining_not_zero", result["worker_blockers"])
        self.assertIn("source_count_contract_mismatch", result["worker_blockers"])

    def test_manifest_complete_cannot_hide_missing_or_duplicate_source_rows(self):
        self.assertIn("source_csv_missing", audit.audit_exchange(source(), {}, None, False)["worker_blockers"])
        self.assertIn("source_csv_count_or_ticker_mismatch",
                      audit.audit_exchange(source(), {}, 2, False)["worker_blockers"])
        self.assertIn("source_csv_count_or_ticker_mismatch",
                      audit.audit_exchange(source(), {}, 1, True)["worker_blockers"])

    def test_invalid_counts_are_not_interpreted_as_eligible(self):
        for value in (-1, 2.0, "2", None):
            with self.subTest(value=value):
                result = audit.audit_exchange(source(coverage_target_symbols=value), {}, 2, True)
                self.assertIn("invalid_source_count_contract", result["worker_blockers"])

    def test_real_file_reader_and_report_are_non_mutating(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "prepared_scans").mkdir()
            (root / "prepared_investment_technicals").mkdir()
            source_manifest = root / "prepared_scans" / "manifest.json"
            tech_manifest = root / "prepared_investment_technicals" / "manifest.json"
            source_manifest.write_text(json.dumps({"updated_at": "2026-10-29T01:00:00Z",
                                                   "exchanges": {"lse": source()}}))
            tech_manifest.write_text(json.dumps({"updated_at": "2026-10-29T06:00:00Z",
                                                 "exchanges": {"lse": {"status": "partial", "coverage_symbols": 0}}}))
            with gzip.open(root / "prepared_scans" / "lse.csv.gz", "wt", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=["Ticker"])
                writer.writeheader()
                writer.writerows([{"Ticker": "AAA.L"}, {"Ticker": "BBB.L"}])
            before = {str(p): p.read_bytes() for p in root.rglob("*") if p.is_file()}
            result = audit.audit_root(root, ["lse"])
            after = {str(p): p.read_bytes() for p in root.rglob("*") if p.is_file()}
            self.assertEqual(before, after)
            self.assertTrue(result["exchanges"]["lse"]["worker_dispatch_allowed"])
            self.assertFalse(result["exchanges"]["lse"]["buy_readiness_assessed"])
            self.assertFalse(result["data_modified"])


if __name__ == "__main__":
    unittest.main()
