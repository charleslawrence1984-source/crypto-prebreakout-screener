"""Background Crypto BUY/READY UI must withhold stale and partial source labels."""
import unittest
from datetime import datetime, timedelta, timezone

import pandas as pd

from crypto_prepared_publication_guard import (
    mask_prepared_crypto_signals, verified_crypto_publisher_health,
)

NOW = datetime(2026, 10, 17, 20, 0, tzinfo=timezone.utc)


def ts(mins):
    return (NOW - timedelta(minutes=mins)).isoformat()


def frames():
    swing = pd.DataFrame([
        {"Base": "BTC", "Swing status": "BUY", "Opportunity stage": "READY",
         "Entry timing": "READY", "Entry mode": "ACTIVE", "Active entry": 100.,
         "Active entry low": 95., "Active entry high": 102., "deep_scored_at": ts(10)},
        {"Base": "ETH", "Swing status": "BUY", "Opportunity stage": "READY",
         "Entry timing": "READY", "Entry mode": "ACTIVE", "Active entry": 50.,
         "Active entry low": 48., "Active entry high": 53., "deep_scored_at": ts(200)},
        {"Base": "SOL", "Swing status": "BUY", "Opportunity stage": "READY",
         "Entry timing": "READY", "Entry mode": "ACTIVE", "Active entry": 20.,
         "Active entry low": 18., "Active entry high": 21., "deep_scored_at": ts(-30)},
        {"Base": "OLD", "Swing status": "WATCH", "Opportunity stage": "EARLY WATCH",
         "deep_scored_at": None},
    ])
    acc = pd.DataFrame([
        {"Base": "BTC", "Accumulation status": "ACCUMULATE",
         "Accumulation verdict": "ACCUMULATE", "Opportunity stage": "READY",
         "deep_scored_at": ts(10)},
        {"Base": "ETH", "Accumulation status": "ACCUMULATE",
         "Accumulation verdict": "ACCUMULATE", "Opportunity stage": "READY",
         "deep_scored_at": ts(1500)},
    ])
    return swing, acc


class PublishedCryptoSafety(unittest.TestCase):
    def test_current_pipeline_still_rejects_old_future_and_missing_coin_scores(self):
        swing,acc=frames()
        out,aa=mask_prepared_crypto_signals(swing,acc,pipeline_health="CURRENT",now=NOW)
        self.assertEqual(out["Swing status"].tolist(),["BUY","RESEARCH ONLY","RESEARCH ONLY","RESEARCH ONLY"])
        self.assertEqual(aa["Accumulation status"].tolist(),["ACCUMULATE","RESEARCH ONLY"])
        self.assertTrue(pd.isna(out.loc[1,"Active entry"]))
        self.assertEqual(out.loc[2,"Entry timing"],"UNVERIFIED")
        self.assertIn("stale",out.loc[3,"Publication evidence"])
        self.assertEqual(swing.loc[1,"Swing status"],"BUY", "Saved source must not be mutated")

    def test_stale_partial_missing_pipeline_never_shows_actionable_prepared_rows(self):
        for health in ("STALE","PARTIAL","INVALID CLOCK","NOT READY","",None):
            with self.subTest(health=health):
                sw,ac=frames()
                out,aa=mask_prepared_crypto_signals(sw,ac,pipeline_health=health,now=NOW)
                self.assertEqual(int(out["Swing status"].eq("BUY").sum()),0)
                self.assertEqual(int(aa["Accumulation status"].eq("ACCUMULATE").sum()),0)
                self.assertTrue(out["Active entry"].isna().all())
                self.assertTrue(out["Publication evidence"].str.contains("RESEARCH ONLY").all())

    def test_empty_frames_and_missing_score_timestamp_fail_closed(self):
        sw,ac=mask_prepared_crypto_signals(
            pd.DataFrame([{"Swing status":"BUY","Base":"UNKNOWN"}]),
            pd.DataFrame(),pipeline_health="CURRENT",now=NOW)
        self.assertEqual(sw.iloc[0]["Swing status"],"RESEARCH ONLY")
        self.assertTrue(ac.empty)

    def test_reject_naive_clock(self):
        with self.assertRaises(ValueError):
            mask_prepared_crypto_signals(*frames(),pipeline_health="CURRENT",now=NOW.replace(tzinfo=None))

    def test_actual_exchange_audit_overrides_untruthful_current_flag(self):
        time=ts(5)
        manifest={"status":"CURRENT","updated_at":time}
        rows=[{"exchange_id":name,"status":"CURRENT"}
              for name in ("binance","okx","bybit","kraken","cryptocom")]
        sidecar={"generated_at":time,"exchange_counts":rows,"errors":[]}
        self.assertEqual(verified_crypto_publisher_health("CURRENT",manifest,sidecar),"CURRENT")
        rows[2]["status"]="VENUE UNAVAILABLE"
        self.assertEqual(verified_crypto_publisher_health("CURRENT",manifest,sidecar),"PARTIAL")
        self.assertEqual(verified_crypto_publisher_health("STALE",manifest,sidecar),"STALE")

    def test_missing_venue_and_mismatched_audit_never_count_as_current(self):
        time=ts(5)
        manifest={"status":"CURRENT","updated_at":time}
        base=[{"exchange_id":name,"status":"CURRENT"}
              for name in ("binance","okx","bybit","kraken","cryptocom")]
        cases=[
            {},
            {"generated_at":time,"exchange_counts":base[:-1]},
            {"generated_at":ts(120),"exchange_counts":base},
            {"generated_at":time,"exchange_counts":base+[base[0]]},
        ]
        for audit in cases:
            with self.subTest(audit=audit):
                self.assertEqual(verified_crypto_publisher_health("CURRENT",manifest,audit),"UNVERIFIED")


if __name__=="__main__":
    unittest.main()
