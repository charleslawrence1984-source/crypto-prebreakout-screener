"""Deterministic research-only counterfactual outcome invariants."""
import io
import json
from hashlib import sha256
import tempfile
from pathlib import Path
from zipfile import ZipFile
import unittest

import pandas as pd

from funded_counterfactual_risk import (
    classify, hypothetical_exit, run_audit, load_history,
)


def series(n=35, start="2026-06-01T00:00:00Z"):
    dt = pd.date_range(start, periods=n, freq="4h", tz="UTC")
    f = pd.DataFrame({
        "timestamp":dt, "open":[100.] * n, "high":[102.] * n,
        "low":[99.] * n, "close":[101.] * n, "volume":[2000.] * n,
    })
    return f.set_index("timestamp", drop=False)


def record(**changes):
    f=series()
    row={
        "Coin":"ETH", "Research class":"PROXY_BUY_UNVERIFIED",
        "Signal open UTC":f.index[0].isoformat(),
        "Decision UTC":(f.index[0]+pd.Timedelta(hours=4,minutes=1)).isoformat(),
        "Research fold":"TRAIN","Original reason":"baseline",
        "Original technical state":"BUY",
        "Original eligible":True, "Original timing actionable":True,
        "Original entry timing":"READY","Proxy liquidity >= $1m":True,
        "Entry proxy":100.,"Stop proxy":98.,"Target proxy":110.,
    }
    return {**row,**changes}


class FrozenCounterfactualTests(unittest.TestCase):
    def test_watch_ready_has_distinct_bypass_from_true_original_buy(self):
        original=record()
        watch=record(**{"Research class":"WATCH",
                        "Original technical state":"WATCH"})
        self.assertEqual(classify(original),"ORIGINAL_BUY")
        self.assertEqual(classify(watch),"WATCH_READY_BYPASS")
        self.assertEqual(classify(record(**{"Research class":"REJECTED_PASS"})),
                         "PASS_REJECTED_NO_PLAN")
        self.assertEqual(classify(record(**{"Research class":"WATCH",
                         "Original entry timing":"EARLY"})), "WATCH_EARLY_OR_NONENTRY")
        self.assertEqual(classify(record(**{"Research class":"WATCH",
                         "Original eligible":False})), "WATCH_NOT_ELIGIBLE")

    def test_same_candle_touch_target_and_stop_resolves_conservatively(self):
        f=series()
        f.loc[f.index[1],["high","low"]]=[111.,97.]
        result=hypothetical_exit(record(),f,cost_pct=.8)
        self.assertEqual(result["Exit reason"],"STOP FIRST BOTH TOUCHED")
        self.assertLess(result["Net R proxy"],0)
        self.assertFalse(result["Historic Kraken Funded fill verified"])

    def test_both_future_gap_and_incomplete_horizon_fail_closed(self):
        f=series()
        missing=f.drop(f.index[3])
        self.assertEqual(
            hypothetical_exit(record(),missing)["Decision"],"GAPPED_FUTURE_HORIZON")
        self.assertEqual(
            hypothetical_exit(record(),f.iloc[:10])["Decision"],
            "CENSORED_FUTURE_HORIZON")

    def test_adverse_stop_gap_uses_actual_next_open(self):
        f=series()
        f.loc[f.index[1],["open","low","high","close"]]=[94.,93.,97.,95.]
        res=hypothetical_exit(record(),f)
        self.assertEqual(res["Exit reason"],"STOP GAP")
        # An open through stop has negative realised R, not filled at stop.
        self.assertLess(res["Net R proxy"],-1)

    def test_next_open_stale_plan_is_excluded_in_strict_sensitivity(self):
        f=series()
        f.loc[f.index[1],["open","high","low","close"]]=[104.,110.,99.,105.]
        strict=hypothetical_exit(record(),f,max_entry_deviation_pct=.5)
        self.assertEqual(strict["Decision"],"NEXT_OPEN_DEVIATES_FROM_ORIGINAL_ENTRY")
        broad=hypothetical_exit(record(),f)
        self.assertEqual(broad["Decision"],"MODELED_EXIT")

    def test_no_promoted_low_liquidity_and_no_original_plan(self):
        f=series()
        self.assertEqual(
            hypothetical_exit(record(**{"Proxy liquidity >= $1m":False}),f)["Decision"],
            "LIQUIDITY_PROXY_BELOW_1M")
        self.assertEqual(
            hypothetical_exit(record(**{"Stop proxy":None}),f)["Decision"],
            "ORIGINAL_ENTRY_PLAN_MISSING")

    def test_cost_sensitivity_reduces_r_without_reselecting_cohort(self):
        f=series()
        a=hypothetical_exit(record(),f,cost_pct=.4)
        b=hypothetical_exit(record(),f,cost_pct=1.6)
        self.assertEqual(a["Decision"],"MODELED_EXIT")
        self.assertEqual(b["Decision"],"MODELED_EXIT")
        self.assertGreater(a["Net R proxy"],b["Net R proxy"])
        result,summary=run_audit(
            pd.DataFrame([record(),record(**{"Coin":"BTC",
            "Signal open UTC":f.index[1].isoformat(),
            "Decision UTC":(f.index[1]+pd.Timedelta(hours=4,minutes=1)).isoformat(),
            "Research class":"WATCH","Original technical state":"WATCH"})]),
            {"ETH":f,"BTC":f},
        )
        self.assertEqual(len(result),6)
        self.assertEqual(set(result["Cost roundtrip %"]),{.4,.8,1.6})

    def test_source_csv_sha_binds_frozen_archive_to_report(self):
        data=("1790000000,100,110,90,105,15,3\n"
              "1790014400,100,110,90,105,15,3\n").encode()
        manifest={"source_zip_full_sha256_verified":False,
                  "zip_crc_integrity_verified":True,
                  "selected_sources":{"BTC:240":{"file":"FULL_BTC_240.csv",
                                                 "sha256":sha256(data).hexdigest()}}}
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/"source.zip"
            with ZipFile(path,"w") as z:
                z.writestr("FULL_selection_manifest.json",json.dumps(manifest))
                z.writestr("FULL_BTC_240.csv",data)
            with ZipFile(path) as z:
                self.assertEqual(len(load_history(z,"BTC")),2)
            with ZipFile(path,"a") as z:
                z.writestr("FULL_BTC_240.csv",data.replace(b"105",b"106"))
            with ZipFile(path) as z:
                with self.assertRaisesRegex(ValueError,"SHA mismatch"):
                    load_history(z,"BTC")


if __name__=="__main__":
    unittest.main()
