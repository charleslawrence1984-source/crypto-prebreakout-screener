"""Frozen slot report and independent updated forward journal reconciliation tests."""
import unittest

from funded_forward_prospective_reconcile import reconcile_coverage_and_paper


def samples():
    coverage={
        "schema":"cl-signal-funded-prospective-4h-coverage-v1",
        "audited_at_utc":"2026-10-22T09:15:00+00:00",
        "source_crypto_scans":18,"source_stock_scans":3,
        "unobserved_crypto_4h_bins":12,
        "4h_capture_coverage_pct":60,
        "multiple_crypto_captures_in_same_4h_bin":1,
        "original_BUY_capture_observations":2,
        "original_BUY_unique_asset_plus_completed_bar":1,
        "funded_market_quotes_or_execution_verified":False,
    }
    paper={
        "schema":"cl-signal-funded-forward-daily-journal-v1",
        "evaluated_at_utc":"2026-10-22T09:25:00+00:00",
        "research_only":True,"broker_trades_executed":0,
        "crypto_snapshots":18,"stock_snapshots":3,
        "original_technical_trigger_observations":2,
        "performance_evidence":{"distinct_closed_paper_positions":0},
        "shadow_status_counts":{"AWAITING FUTURE CANDLES":2},
    }
    return coverage,paper


class ReconcileTests(unittest.TestCase):
    def test_unknown_gaps_and_zero_closed_never_look_like_no_signals(self):
        a,b=samples()
        result=reconcile_coverage_and_paper(a,b)
        self.assertEqual(result["unobserved_4h_capture_bins"],12)
        self.assertEqual(result["original_crypto_trigger_observations"],2)
        self.assertEqual(result["original_coin_and_bar_unique_triggers"],1)
        self.assertIn("NO_REAL_CLOSED_FORWARD_PAPER_OUTCOME_YET",result["warnings"])
        self.assertFalse(result["evidence_of_positive_forward_edge"])
        self.assertFalse(result["broker_equity_or_orders_accessed"])

    def test_journal_must_follow_capture_report(self):
        a,b=samples()
        b["evaluated_at_utc"]="2026-10-22T09:00:00Z"
        with self.assertRaisesRegex(ValueError,"preceded"):
            reconcile_coverage_and_paper(a,b)

    def test_nonzero_broker_trades_never_tolerated(self):
        a,b=samples()
        b["broker_trades_executed"]=1
        with self.assertRaisesRegex(ValueError,"broker orders"):
            reconcile_coverage_and_paper(a,b)

    def test_run_source_drift_is_flagged_not_falsely_aggregated(self):
        a,b=samples()
        b["crypto_snapshots"]=17
        b["original_technical_trigger_observations"]=0
        x=reconcile_coverage_and_paper(a,b)
        self.assertIn("CAPTURE_SOURCE_UNIVERSES_CHANGED_BETWEEN_TWO_READ_ONLY_RUNS",x["warnings"])
        self.assertIn("PAPER_JOURNAL_LACKS_LATEST_CRYPTO_TRIGGER_COHORT",x["warnings"])

    def test_closed_paper_is_never_broker_confirmation(self):
        a,b=samples()
        b["performance_evidence"]["distinct_closed_paper_positions"]=1
        result=reconcile_coverage_and_paper(a,b)
        self.assertEqual(result["historical_distinct_closed_paper_positions"],1)
        self.assertFalse(result["evidence_of_positive_forward_edge"])
        self.assertFalse(result["fully_verified_Funded_broker_market_eligibility"])

    def test_no_missing_distinct_closed_count_permitted(self):
        a,b=samples()
        b["performance_evidence"]={}
        with self.assertRaisesRegex(ValueError,"count absent"):
            reconcile_coverage_and_paper(a,b)


if __name__=="__main__":
    unittest.main()
