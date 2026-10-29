"""Fail-closed reference source evidence tests; all input is synthetic."""
import csv
import hashlib
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from trade_lse_reference_contract import (
    TARGET_SESSION, TARGET_SYMBOLS, geometry_problem, inspect_evidence,
)

HEADERS = ("symbol", "date", "isin", "mic", "currency_unit",
           "open", "high", "low", "close", "volume")


def sample_rows():
    return [{
        "symbol": s, "date": TARGET_SESSION, "isin": "GB0000000000",
        "mic": "XLON", "currency_unit": "GBX",
        "open": "100", "high": "104", "low": "99",
        "close": "102", "volume": "100000",
    } for s in TARGET_SYMBOLS]


class ReferenceContractTests(unittest.TestCase):
    def fixture(self, folder, rows=None, metadata=None):
        root = Path(folder)
        official = root / "synthetic_official_lse_file.dat"
        official.write_bytes(b"SYNTHETIC NON-LSE FIXTURE; NOT OFFICIAL LSE DATA")
        comparison = root / "comparison.csv"
        with comparison.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=HEADERS)
            writer.writeheader()
            writer.writerows(sample_rows() if rows is None else rows)
        meta = {
            "source_product": "LSE_END_OF_DAY_SUMMARY",
            "source_access_right": "SYNTHETIC_TEST_ONLY",
            "official_file_sha256": hashlib.sha256(official.read_bytes()).hexdigest(),
            "source_file_name": official.name,
            "source_session": TARGET_SESSION,
            "reference_mic": "XLON", "reference_quote_unit": "GBX",
            "raw_or_adjusted": "UNADJUSTED",
            "instrument_isin_mapping_reviewed": True,
            "corporate_actions_reviewed": True,
        }
        meta.update(metadata or {})
        provenance = root / "provenance.json"
        provenance.write_text(json.dumps(meta), encoding="utf-8")
        return official, comparison, provenance

    def inspect(self, files):
        return inspect_evidence(*files, now=datetime(
            2026, 10, 29, 11, 0, tzinfo=timezone.utc))

    def test_missing_source_blocks_without_exception(self):
        result = inspect_evidence(None, None, None)
        self.assertEqual(result["status"], "BLOCKED_MISSING_SOURCE")
        self.assertEqual(result["live_trade_readiness"], "NOT_VERIFIED")

    def test_hash_mismatch_does_not_authorize_replacement(self):
        with tempfile.TemporaryDirectory() as folder:
            files = self.fixture(folder, metadata={"official_file_sha256": "0" * 64})
            result = self.inspect(files)
            self.assertIn("SOURCE_FILE_SHA256_MISMATCH", result["issues"])
            self.assertFalse(result["automatic_history_repair_authorised"])

    def test_complete_synthetic_receipt_never_claims_real_authentication(self):
        with tempfile.TemporaryDirectory() as folder:
            result = self.inspect(self.fixture(folder))
            self.assertEqual(result["status"],
                             "STRUCTURALLY_VALID_PENDING_INDEPENDENT_HUMAN_REVIEW")
            self.assertEqual(result["live_trade_readiness"], "NOT_VERIFIED")
            self.assertFalse(result["reference_source_authenticated"])
            self.assertFalse(result["orders_authorised"])
            self.assertEqual(len(result["symbols_observed"]), 6)

    def test_historical_geometry_must_be_valid(self):
        rows = sample_rows()
        rows[0]["high"] = "98"
        with tempfile.TemporaryDirectory() as folder:
            result = self.inspect(self.fixture(folder, rows=rows))
            self.assertIn("INVALID_OHLC_GEOMETRY:AZN.L", result["issues"])

    def test_unreviewed_split_or_dividend_blocks(self):
        with tempfile.TemporaryDirectory() as folder:
            files = self.fixture(folder, metadata={
                "corporate_actions_reviewed": False, "raw_or_adjusted": "UNKNOWN"})
            result = self.inspect(files)
            self.assertIn("CORPORATE_ACTION_REVIEW_OUTSTANDING", result["issues"])
            self.assertIn("UNKNOWN_CORPORATE_ACTION_ADJUSTMENT", result["issues"])

    def test_gbp_versus_gbp_pence_mismatch_blocks(self):
        rows = sample_rows()
        rows[2]["currency_unit"] = "GBP"
        with tempfile.TemporaryDirectory() as folder:
            result = self.inspect(self.fixture(folder, rows=rows))
            self.assertIn("WRONG_NORMALIZED_RECORD_UNIT:ULVR.L", result["issues"])

    def test_wrong_date_and_market_are_not_assumed_equivalent(self):
        rows = sample_rows()
        rows[0]["date"] = "2024-05-02"
        rows[1]["mic"] = "XETR"
        with tempfile.TemporaryDirectory() as folder:
            result = self.inspect(self.fixture(folder, rows=rows))
            self.assertIn("WRONG_NORMALIZED_RECORD_DATE:AZN.L", result["issues"])
            self.assertIn("WRONG_NORMALIZED_RECORD_MIC:SHEL.L", result["issues"])

    def test_missing_or_duplicate_symbol_will_not_pass(self):
        rows = sample_rows()
        rows[1]["symbol"] = "AZN.L"
        with tempfile.TemporaryDirectory() as folder:
            result = self.inspect(self.fixture(folder, rows=rows))
            self.assertIn("DUPLICATE_SYMBOL_RECORD:AZN.L", result["issues"])
            self.assertIn("MISSING_SYMBOL:SHEL.L", result["issues"])

    def test_wrong_product_and_unreviewed_isin_are_blockers(self):
        with tempfile.TemporaryDirectory() as folder:
            result = self.inspect(self.fixture(folder, metadata={
                "source_product": "UNVERIFIED_AGGREGATOR",
                "instrument_isin_mapping_reviewed": False,
            }))
            self.assertIn("UNSUPPORTED_OFFICIAL_PRODUCT_CLAIM", result["issues"])
            self.assertIn("INSTRUMENT_ISIN_MAPPING_NOT_REVIEWED", result["issues"])

    def test_invalid_ohlcv_never_corrected_in_place(self):
        row = {"open": "100", "high": "99", "low": "98",
               "close": "102", "volume": "10000"}
        original = dict(row)
        self.assertEqual(geometry_problem(row), "INVALID_OHLC_GEOMETRY")
        self.assertEqual(row, original)

    def test_zero_or_negative_volume_geometry_not_promoted(self):
        row = {"open": "100", "high": "104", "low": "99",
               "close": "102", "volume": "-5"}
        self.assertEqual(geometry_problem(row), "INVALID_PRICE_OR_VOLUME")


if __name__ == "__main__":
    unittest.main()
