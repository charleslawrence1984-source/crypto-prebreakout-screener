"""Release audit uses real cache contents and cannot mistake green CI for live approval."""
import csv
import gzip
import tempfile
import unittest
from pathlib import Path

from trade_integrated_release_audit import audit_release,cached_price_provenance


class ReleaseAuditTests(unittest.TestCase):
    def test_no_price_cache_is_not_exchange_authenticated(self):
        with tempfile.TemporaryDirectory() as path:
            summary=cached_price_provenance(Path(path))
            self.assertEqual(summary["cached_rows"],0)
            self.assertFalse(summary["cached_dataset_independent_exchange_verified"])

    def test_real_gzip_rows_require_actual_source_session_fields(self):
        with tempfile.TemporaryDirectory() as path:
            folder=Path(path)/"prepared_trade_technicals"
            folder.mkdir()
            with gzip.open(folder/"lse.csv.gz","wt",newline="",encoding="utf-8") as f:
                writer=csv.DictWriter(f,fieldnames=("Ticker","Technical score"))
                writer.writeheader()
                writer.writerow({"Ticker":"AZN.L","Technical score":"95"})
            summary=cached_price_provenance(Path(path))
            self.assertEqual(summary["cached_files_inspected"],1)
            self.assertEqual(summary["cached_rows"],1)
            self.assertEqual(summary["rows_without_completed_session"],1)
            self.assertEqual(summary["rows_without_pass_status"],1)

    def test_self_declared_pass_does_not_prove_independent_exchange(self):
        with tempfile.TemporaryDirectory() as path:
            folder=Path(path)/"prepared_trade_technicals"
            folder.mkdir()
            with gzip.open(folder/"nasdaq.csv.gz","wt",newline="",encoding="utf-8") as f:
                writer=csv.DictWriter(f,fieldnames=(
                    "Ticker","Last completed exchange session","Price session status"))
                writer.writeheader()
                writer.writerow({"Ticker":"AAPL","Last completed exchange session":"2026-10-28",
                                 "Price session status":"PASS"})
            result=cached_price_provenance(Path(path))
            self.assertEqual(result["rows_without_completed_session"],0)
            self.assertFalse(result["cached_dataset_independent_exchange_verified"])

    def test_real_repo_release_is_blocked_and_source_gates_stay_missing(self):
        root=Path(__file__).resolve().parents[1]
        result=audit_release(root)
        self.assertEqual(result["decision"],"BLOCKED_NOT_LIVE_READY")
        self.assertFalse(result["live_broker_order_permission"])
        self.assertFalse(result["owner_release_approved"])
        self.assertFalse(result["production_deployed_by_this_workflow"])
        self.assertEqual(result["lse_official_reference_status"],"BLOCKED_MISSING_SOURCE")
        self.assertEqual(result["historical_pit_manifest_status"],
                         "NO_AUTHENTIC_POINT_IN_TIME_SOURCE_RECEIPT_MANIFEST")
        self.assertIn("STOCKS_PENSION_MIXED_LEGACY_ROUTE",result["blocked_gates"])

    def test_malformed_cache_header_is_bad_not_zero_confident(self):
        with tempfile.TemporaryDirectory() as path:
            folder=Path(path)/"prepared_trade_technicals"
            folder.mkdir()
            with gzip.open(folder/"nyse.csv.gz","wt",encoding="utf-8") as file:
                file.write("Something,Else\n1,2\n")
            a=cached_price_provenance(Path(path))
            self.assertEqual(len(a["bad_files"]),1)

if __name__=="__main__":
    unittest.main()
