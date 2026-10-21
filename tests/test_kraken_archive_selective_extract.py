"""Offline tests of exact Kraken pair CSV extraction with checksum provenance."""
from hashlib import sha256
import io
import tempfile
from pathlib import Path
import unittest
from zipfile import ZipFile

from scripts.kraken_archive_selective_extract import extract_quarter, _members_for_coin
from scripts.kraken_archive_range_probe import ArchiveUnavailable
from tests.test_kraken_archive_range_probe import make_provider


def sample_zip(*, missing=False, conflicting=False):
    f=io.BytesIO()
    with ZipFile(f,"w") as z:
        z.writestr("XBTUSD_240.csv","1790000000,1,2,0.5,1.5,5,2\n")
        z.writestr("XBTUSD_1440.csv","1790000000,1,2,0.5,1.5,5,2\n")
        z.writestr("NEARUSD_240.csv","1790000000,2,3,1.5,2.5,7,4\n")
        if not missing:
            z.writestr("NEARUSD_1440.csv","1790000000,2,3,1.5,2.5,7,4\n")
        if conflicting:
            z.writestr("XXBTZUSD_240.csv","1790000000,1,2,0.5,1.5,5,2\n")
    return f.getvalue()


class SelectiveKrakenArchiveTests(unittest.TestCase):
    def test_exact_pair_alias_extract_preserves_source_sha_and_crc(self):
        blob=sample_zip()
        with tempfile.TemporaryDirectory() as tmp:
            info=extract_quarter("2026Q3",["BTC","NEAR"],tmp,
                                  opener=make_provider(blob),min_pairs=2)
            self.assertEqual(info["captured_paired_coins"],2)
            self.assertFalse(info["source_zip_full_sha256_verified"])
            self.assertTrue(info["zip_crc_integrity_verified"])
            saved=Path(tmp)/"2026Q3_BTC_240.csv"
            self.assertTrue(saved.exists())
            self.assertEqual(info["selected_sources"]["BTC:240"]["sha256"],
                             sha256(saved.read_bytes()).hexdigest())
            self.assertEqual(info["selected_sources"]["BTC:240"]["archive_member"],
                             "XBTUSD_240.csv")
            self.assertIn("source_quarter",info["selected_sources"]["NEAR:1440"])

    def test_missing_interval_explicit_and_never_mistaken_as_complete(self):
        blob=sample_zip(missing=True)
        with tempfile.TemporaryDirectory() as tmp:
            info=extract_quarter("2026Q3",["BTC","NEAR"],tmp,
                                  opener=make_provider(blob),min_pairs=1)
            self.assertEqual(info["captured_paired_coins"],1)
            missing=[x for x in info["coverage"] if x["coin"]=="NEAR"][0]
            self.assertEqual(missing["status"],"MISSING_PAIRED_INTERVALS")
            self.assertFalse((Path(tmp)/"2026Q3_NEAR_240.csv").exists())

    def test_ambiguous_alias_is_rejected(self):
        blob=sample_zip(conflicting=True)
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError,"ambiguous"):
                extract_quarter("2026Q3",["BTC"],tmp,
                                opener=make_provider(blob))

    def test_enforce_required_coins_to_prevent_false_green(self):
        blob=sample_zip(missing=True)
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ArchiveUnavailable,"required exact paired"):
                extract_quarter("2026Q3",["BTC","NEAR"],tmp,
                                opener=make_provider(blob),min_pairs=2)


if __name__=="__main__":
    unittest.main()
