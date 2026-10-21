"""Safe offline official Kraken OHLCVT archive and weekly rebuild tests."""
import hashlib
from pathlib import Path
import tempfile
import unittest
from zipfile import ZipFile

import pandas as pd

from funded_kraken_official_archive import (
    read_ohlcvt_member, verify_archive_checksum,
    merge_historic_segments, weekly_from_kraken_daily, weekly_overlap_parity,
)


def make_daily(n=14, start="2026-09-03"):
    times=pd.date_range(start,periods=n,freq="1D",tz="UTC")
    return pd.DataFrame({
        "timestamp":times, "open":[100.]*n,"high":[105.]*n,
        "low":[98.]*n,"close":[102.]*n,"volume":[1000.]*n,
    })


class OfficialKrakenArchiveTests(unittest.TestCase):
    def test_csv_is_headerless_and_requires_exact_pair_filename(self):
        with tempfile.TemporaryDirectory() as tmp:
            file=Path(tmp)/"test.zip"
            date=pd.Timestamp("2026-06-01T00:00:00Z").timestamp()
            content=f"{int(date)},100,110,90,105,150.2,34\n".encode()
            with ZipFile(file,"w") as z:
                z.writestr("BTCUSD_240.csv",content)
            obj=read_ohlcvt_member(file,"BTCUSD_240.csv")
            self.assertEqual(len(obj),1)
            self.assertEqual(obj.iloc[0]["close"],105)
            self.assertEqual(str(obj.iloc[0]["timestamp"]), "2026-06-01 00:00:00+00:00")
            with self.assertRaisesRegex(ValueError,"absent"):
                read_ohlcvt_member(file,"BTCUSDT_240.csv")
            self.assertTrue(verify_archive_checksum(
                file,hashlib.sha256(file.read_bytes()).hexdigest()))
            with self.assertRaisesRegex(ValueError,"SHA-256 mismatch"):
                verify_archive_checksum(file,"0"*64)

    def test_full_week_thursday_anchor_only(self):
        d=make_daily()
        w=weekly_from_kraken_daily(d)
        self.assertEqual(len(w),2)
        self.assertEqual(str(w.iloc[0]["timestamp"]),"2026-09-03 00:00:00+00:00")
        self.assertEqual(w.iloc[0]["volume"],7000)
        self.assertEqual(weekly_overlap_parity(w,w.copy()),2)
        partial=d.iloc[:-1].copy()
        self.assertEqual(len(weekly_from_kraken_daily(partial)),1)

    def test_gap_must_not_look_like_continuous_completed_week(self):
        d=make_daily()
        d=d.drop(index=4).reset_index(drop=True)
        w=weekly_from_kraken_daily(d)
        self.assertEqual(len(w),1)
        self.assertEqual(str(w.iloc[0]["timestamp"]),"2026-09-10 00:00:00+00:00")

    def test_native_kraken_weekly_parity_rejects_different_price(self):
        w=weekly_from_kraken_daily(make_daily())
        bad=w.copy()
        bad.loc[0,"close"]=101
        with self.assertRaisesRegex(ValueError,"disagrees"):
            weekly_overlap_parity(w,bad)

    def test_duplicate_archive_segments_must_be_identical(self):
        x=make_daily(3)
        merged=merge_historic_segments(x,x.copy())
        self.assertEqual(len(merged),3)
        y=x.copy()
        y.loc[0,"close"]=101
        with self.assertRaisesRegex(ValueError,"conflicting"):
            merge_historic_segments(x,y)

    def test_malformatted_unix_seconds_do_not_silently_parse(self):
        with tempfile.TemporaryDirectory() as tmp:
            file=Path(tmp)/"test.zip"
            content=b"9999999999999,100,110,90,105,150.2,34\n"
            with ZipFile(file,"w") as z:
                z.writestr("BTCUSD_240.csv",content)
            with self.assertRaisesRegex(ValueError,"SECONDS"):
                read_ohlcvt_member(file,"BTCUSD_240.csv")


if __name__=="__main__":
    unittest.main()
