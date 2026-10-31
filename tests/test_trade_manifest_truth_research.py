"""Synthetic guardrails for the independent read-only Trade cache manifest audit."""
import copy
import unittest
from datetime import datetime, timezone, timedelta

from trade_manifest_truth_research import RELEASE_VERDICT, audit

NOW = datetime(2026, 10, 31, 10, 0, tzinfo=timezone.utc)


def specimen():
    f = {"updated_at": "2026-10-31T05:33:56+00:00", "exchanges": {
        "nasdaq": {"coverage_symbols": 3326, "fresh_symbols": 3326, "status": "complete"},
        "lse": {"coverage_symbols": 459, "fresh_symbols": 0, "status": "partial"},
    }}
    t = {"updated_at": "2026-10-31T06:55:05+00:00", "exchanges": {
        "nasdaq": {"coverage_symbols": 233, "eligible_symbols": 233,
                   "requested_this_run": 0, "returned_this_run": 0,
                   "refresh_mode": "missing_only", "updated_at": "2026-10-31T06:54:58+00:00"},
        "lse": {"coverage_symbols": 64, "eligible_symbols": 64,
                "requested_this_run": 0, "returned_this_run": 0,
                "refresh_mode": "missing_only", "updated_at": "2026-10-31T06:55:01+00:00"},
    }}
    return f, t


class ManifestTruthTests(unittest.TestCase):
    def test_coverage_is_not_price_or_pit_proof(self):
        f, t = specimen()
        r = audit(f, t, observed_at=NOW)
        self.assertEqual(r["technical_coverage_total"], 297)
        self.assertEqual(r["technical_eligible_total"], 297)
        self.assertEqual(r["venues_zero_technical_requests"], ["lse", "nasdaq"])
        self.assertFalse(r["completed_bar_source_session_verified"])
        self.assertFalse(r["issuer_publication_asof_verified"])
        self.assertEqual(r["release_verdict"], RELEASE_VERDICT)

    def test_venue_updated_today_but_no_rows_requested_not_marked_fresh(self):
        f, t = specimen()
        r = audit(f, t, observed_at=NOW)
        self.assertTrue(all(row["technical_no_rows_requested"] for row in r["rows"]))
        self.assertTrue(all(row["completed_session_price_provenance_from_manifest"] == "NOT_PROVIDED" for row in r["rows"]))

    def test_older_manifest_is_observed_not_assumed_bad_price(self):
        f, t = specimen()
        t["exchanges"]["lse"]["updated_at"] = "2026-09-30T23:05:42+01:00"
        r = audit(f, t, observed_at=NOW)
        self.assertEqual(r["venues_technical_manifest_older_than_7d"], ["lse"])
        self.assertEqual(r["release_verdict"], RELEASE_VERDICT)

    def test_full_refresh_still_does_not_prove_completed_session(self):
        f, t = specimen()
        t["exchanges"]["nasdaq"].update(requested_this_run=233, returned_this_run=233, refresh_mode="all_eligible")
        r = audit(f, t, observed_at=NOW)
        self.assertFalse(r["per_symbol_source_hash_verified"])
        self.assertFalse(r["broker_paper_entries_or_fills_verified"])

    def test_fundamental_zero_fresh_not_equivalent_to_missing_all_coverage(self):
        f, t = specimen()
        r = audit(f, t, observed_at=NOW)
        self.assertEqual(r["fundamental_venues_reporting_zero_fresh"], ["lse"])
        self.assertEqual(r["rows"][0]["fundamental_coverage_symbols"], 459)

    def test_reject_mismatched_venues(self):
        f, t = specimen()
        del t["exchanges"]["lse"]
        with self.assertRaises(ValueError):
            audit(f, t, observed_at=NOW)

    def test_reject_future_timestamp(self):
        f, t = specimen()
        t["exchanges"]["nasdaq"]["updated_at"] = "2026-11-01T00:00:00+00:00"
        with self.assertRaises(ValueError):
            audit(f, t, observed_at=NOW)

    def test_reject_missing_timezone(self):
        f, t = specimen()
        t["exchanges"]["nasdaq"]["updated_at"] = "2026-10-31T06:54:58"
        with self.assertRaises(ValueError):
            audit(f, t, observed_at=NOW)

    def test_reject_invalid_counts(self):
        f, t = specimen()
        t["exchanges"]["lse"]["requested_this_run"] = "0"
        with self.assertRaises(ValueError):
            audit(f, t, observed_at=NOW)

    def test_reject_coverage_greater_than_eligible(self):
        f, t = specimen()
        t["exchanges"]["lse"]["coverage_symbols"] = 65
        with self.assertRaises(ValueError):
            audit(f, t, observed_at=NOW)

    def test_reject_bool_as_count(self):
        f, t = specimen()
        t["exchanges"]["lse"]["coverage_symbols"] = True
        with self.assertRaises(ValueError):
            audit(f, t, observed_at=NOW)

    def test_no_trade_signal_or_order_claim_in_audit(self):
        f, t = specimen()
        r = audit(f, t, observed_at=NOW)
        self.assertFalse(r["trade_rules_modified"])
        self.assertEqual(r["trading_orders"], 0)
        self.assertFalse(r["historical_membership_verified"])
        self.assertFalse(r["event_fx_liquidity_verified"])

if __name__ == "__main__":
    unittest.main()
