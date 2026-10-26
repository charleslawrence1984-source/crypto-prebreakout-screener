"""Offline synthetic official full + Q3 source join regression and provenance."""
from hashlib import sha256
import json
from pathlib import Path
import tempfile
import unittest

import pandas as pd

from scripts.funded_kraken_multiyear_replay import joined_source,validated_source,_folds


def make_sources(directory,source,start,days,coin="BTC"):
    directory=Path(directory)
    directory.mkdir(exist_ok=True,parents=True)
    stamp=pd.date_range(start,periods=days,freq="1D",tz="UTC")
    selected={}
    for interval in ("240","1440"):
        dates=(pd.date_range(stamp[0],periods=days*6,freq="4h",tz="UTC")
               if interval=="240" else stamp)
        text="".join(
            f"{int(d.timestamp())},100,102,98,101,100,5\n" for d in dates
        )
        name=f"{source}_{coin}_{interval}.csv"
        (directory/name).write_text(text,encoding="utf-8")
        selected[f"{coin}:{interval}"]={
            "file":name,"sha256":sha256(text.encode("utf-8")).hexdigest()
        }
    (directory/f"{source}_selection_manifest.json").write_text(
        json.dumps({
            "quarter":source,"selected_sources":selected,
            "zip_crc_integrity_verified":True,
            "source_zip_full_sha256_verified":False,
        }),encoding="utf-8")


class OfficialMultiyearTests(unittest.TestCase):
    def test_multi_source_join_preserves_exact_4h_daily_weekly_boundaries(self):
        with tempfile.TemporaryDirectory() as root:
            full=Path(root)/"full";q3=Path(root)/"q3"
            make_sources(full,"FULL","2026-01-01",181)
            make_sources(q3,"2026Q3","2026-07-01",92)
            merged,source=joined_source(full,q3,"BTC")
            self.assertEqual(len(merged["4h"]),273*6)
            self.assertEqual(len(merged["1d"]),273)
            self.assertGreater(len(merged["1w"]),30)
            self.assertEqual(len(source),4)
            self.assertEqual(str(merged["4h"]["timestamp"].iloc[-1]),
                             "2026-09-30 20:00:00+00:00")

    def test_hash_mismatch_fails_before_rule_replay(self):
        with tempfile.TemporaryDirectory() as root:
            full=Path(root)/"full";q3=Path(root)/"q3"
            make_sources(full,"FULL","2026-01-01",181)
            make_sources(q3,"2026Q3","2026-07-01",92)
            (full/"FULL_BTC_240.csv").write_text("FORGED",encoding="utf-8")
            with self.assertRaisesRegex(ValueError,"SHA-256 mismatch"):
                joined_source(full,q3,"BTC")

    def test_repeated_or_discontinuous_raw_input_never_fabricates_week(self):
        with tempfile.TemporaryDirectory() as root:
            full=Path(root)/"full";q3=Path(root)/"q3"
            make_sources(full,"FULL","2026-01-01",181)
            make_sources(q3,"2026Q3","2026-07-02",91)
            histories,_=joined_source(full,q3,"BTC")
            self.assertEqual(len(histories["1d"]),272)
            self.assertLess(len(histories["1w"]),39)

    def test_completed_early_future_ineligible_fold_boundaries_are_separated(self):
        audit=pd.DataFrame([
            {"Coin":"BTC","Signal open UTC":str(d),"Research class":"WATCH"}
            for d in pd.date_range("2024-01-01",periods=10,freq="7D",tz="UTC")
        ])
        outcomes=pd.DataFrame([
            {"Coin":"BTC","Decision bar open":str(d),"Later mover":True}
            for d in pd.date_range("2024-01-01",periods=10,freq="7D",tz="UTC")
        ])
        a,o=_folds(audit,outcomes)
        self.assertEqual(list(a["Research fold"][:6]),["TRAIN"]*6)
        self.assertEqual(list(a["Research fold"][6:8]),["VALIDATION"]*2)
        self.assertEqual(list(a["Research fold"][8:]),["UNTOUCHED HOLDOUT"]*2)
        self.assertEqual(len(o),10)
        self.assertNotIn("UNKNOWN",set(o["Research fold"]))


if __name__=="__main__":
    unittest.main()
