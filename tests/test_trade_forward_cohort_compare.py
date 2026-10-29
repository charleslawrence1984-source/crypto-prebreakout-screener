"""Immutable prospective cohort comparison: no backfill, false P&L or survivor reselection."""
from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from trade_forward_cohort_compare import compare_cohorts,compare_files


def sample(t="2026-10-29T15:00:00+00:00",day="2026-10-28",close=100.0):
    rows=[{"symbol":s,"venue":"nasdaq","observation_id":s+"-sourcehash",
           "first_captured_utc":t,"source_session":day,
           "signal_close_price":close,"technical_state":"BLOCKED",
           "approved_paper_entry":False} for s in ("AAPL","MSFT")]
    return {"capture_created_utc":t,"approved_trade_entries":0,
            "simulated_trade_entries":0,"observations":rows}


class FrozenCohortTests(unittest.TestCase):
    def later(self,prior=None):
        now=copy.deepcopy(prior or sample())
        now["capture_created_utc"]="2026-10-30T22:00:00+00:00"
        for row in now["observations"]:
            row["first_captured_utc"]=now["capture_created_utc"]
            row["source_session"]="2026-10-30"
            row["signal_close_price"]=103.0
            row["observation_id"]=row["symbol"]+"-newsourcehash"
        return now

    def test_future_captured_close_is_not_approved_return(self):
        old=sample()
        out=compare_cohorts(old,self.later(old))
        self.assertEqual(len(out["comparisons"]),2)
        self.assertFalse(out["realized_strategy_returns_available"])
        self.assertEqual(out["approved_paper_trades"],0)
        self.assertEqual(out["comparisons"][0]["status"],
                         "DESCRIPTIVE_FORWARD_PRICE_CHANGE_NOT_TRADE_RETURN")

    def test_same_run_is_never_forward_comparison(self):
        old=sample()
        with self.assertRaises(ValueError):
            compare_cohorts(old,old)

    def test_late_capture_but_same_completed_price_bar_not_faked(self):
        old=sample()
        future=self.later(old)
        for row in future["observations"]:
            row["source_session"]="2026-10-28"
        output=compare_cohorts(old,future)
        self.assertTrue(all(r["status"]=="NO_LATER_VERIFIED_COMPLETED_PRICE_BAR"
                            for r in output["comparisons"]))

    def test_cannot_drop_loser_or_add_winner_after_observation(self):
        old=sample()
        future=self.later(old)
        future["observations"].pop()
        with self.assertRaisesRegex(ValueError,"SURVIVORSHIP"):
            compare_cohorts(old,future)

    def test_cannot_relabel_venue_at_followup(self):
        future=self.later()
        future["observations"][0]["venue"]="gettex"
        with self.assertRaisesRegex(ValueError,"EXCHANGE_VENUE_CHANGED"):
            compare_cohorts(sample(),future)

    def test_backdated_row_timestamp_blocks_even_with_later_batch_time(self):
        future=self.later()
        future["observations"][0]["first_captured_utc"]="2026-10-29T12:00:00+00:00"
        with self.assertRaisesRegex(ValueError,"FOLLOWUP_ROW_TIMESTAMP_MISMATCH"):
            compare_cohorts(sample(),future)

    def test_source_only_approved_entry_claim_must_fail(self):
        future=self.later()
        future["approved_trade_entries"]=1
        with self.assertRaisesRegex(ValueError,"CANNOT_BE_APPROVED"):
            compare_cohorts(sample(),future)

    def test_missing_prior_snapshot_is_blocked(self):
        report=compare_files(None,None)
        self.assertEqual(report["status"],"BLOCKED_MISSING_REQUIRED_TWO_SNAPSHOTS")
        self.assertEqual(report["readiness"],"NOT_VERIFIED")

    def test_explicit_original_sha_required_and_mismatch_fail(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/"prior.json"
            f=Path(d)/"followup.json"
            p.write_text(json.dumps(sample()))
            f.write_text(json.dumps(self.later()))
            with self.assertRaisesRegex(ValueError,"EXPECTED_HASH_REQUIRED"):
                compare_files(p,f)
            with self.assertRaisesRegex(ValueError,"SHA256_MISMATCH"):
                compare_files(p,f,"0"*64)
            digest=hashlib.sha256(p.read_bytes()).hexdigest()
            report=compare_files(p,f,digest)
            self.assertEqual(report["prior_file_sha256"],digest)
            self.assertEqual(report["approved_paper_trades"],0)

    def test_duplicate_captured_symbol_in_source_cohort_is_rejected(self):
        old=sample()
        old["observations"].append(copy.deepcopy(old["observations"][0]))
        with self.assertRaisesRegex(ValueError,"DUPLICATE"):
            compare_cohorts(old,self.later())

if __name__=="__main__":
    unittest.main()
