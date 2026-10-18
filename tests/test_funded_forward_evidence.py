"""Funded evidence never fabricates a win rate from zero genuine paper closes."""
import unittest

from funded_forward_evidence import summarize_forward_evidence


def journal(*, triggers=0, accepted=None, closed=None, statuses=None):
    accepted = accepted if accepted is not None else []
    closed = closed if closed is not None else []
    return {
        "research_only": True,
        "broker_trades_executed": 0,
        "funded_challenge_pass_claimed": False,
        "source_snapshot_count": 3,
        "original_technical_trigger_observations": triggers,
        "shadow_status_counts": statuses or {"NO RESEARCH ENTRY": 99},
        "provisional_challenge_risk": {
            "kraken_funded_challenge_pass_confirmed": False,
            "kraken_broker_account_connected": False,
            "research_only": True,
            "admissions": accepted,
            "closed_paper_cases": closed,
            "admitted_unique_paper_cases": len(accepted),
            "closed_unique_paper_cases": len(closed),
            "open_unique_paper_cases": len(accepted)-len(closed),
            "max_realized_only_peak_to_trough_drawdown_usd": 12,
        },
    }


class GenuineEvidenceTests(unittest.TestCase):
    def test_99_observations_zero_buy_has_unknown_not_zero_win_rate(self):
        result = summarize_forward_evidence(journal())
        self.assertEqual(result["original_technical_trigger_observation_count"], 0)
        self.assertEqual(result["distinct_closed_paper_positions"], 0)
        self.assertIsNone(result["paper_closed_case_win_rate_pct"])
        self.assertIsNone(result["paper_average_closed_net_pnl_usd"])
        self.assertIsNone(result["paper_realized_only_max_drawdown_usd"])
        self.assertFalse(result["strategy_profitable_or_viable_proven"])
        self.assertIsNone(result["broker_verified_profit_usd"])
        self.assertIsNone(result["kraken_funded_challenge_success_rate"])

    def test_qualifying_signal_without_future_close_has_unknown_statistics(self):
        result = summarize_forward_evidence(journal(
            triggers=1, accepted=[{"asset":"BTC","paper_only":True}]))
        self.assertEqual(result["distinct_admitted_paper_positions"], 1)
        self.assertIsNone(result["paper_closed_case_win_rate_pct"])
        self.assertIn("NO CLOSED", result["outcome_sample_classification"])

    def test_two_closed_synthetic_cases_are_descriptive_not_strategy_proof(self):
        closed = [{"paper_pnl_usd": 6.0}, {"paper_pnl_usd": -3.5}]
        accepted = [{"asset":"BTC"}, {"asset":"ETH"}]
        result = summarize_forward_evidence(journal(triggers=2, accepted=accepted, closed=closed))
        self.assertEqual(result["paper_closed_case_win_rate_pct"], 50)
        self.assertEqual(result["paper_average_closed_net_pnl_usd"], 1.25)
        self.assertEqual(result["paper_sum_closed_net_pnl_usd"], 2.5)
        self.assertEqual(result["paper_realized_only_max_drawdown_usd"], 12)
        self.assertFalse(result["descriptive_sample_band_reached"])
        self.assertFalse(result["strategy_profitable_or_viable_proven"])
        self.assertIsNone(result["broker_verified_win_rate_pct"])

    def test_data_missing_is_reported_not_mistaken_for_profitable_sample(self):
        summary = summarize_forward_evidence(journal(statuses={
            "NO RESEARCH ENTRY": 84, "DATA UNVERIFIED": 8,
            "AWAITING ENTRY": 5, "FROZEN PLAN VETO": 2,
        }))
        self.assertEqual(summary["unresolved_or_quality_vetoed_observations"], 15)
        self.assertEqual(summary["distinct_closed_paper_positions"], 0)

    def test_rejects_fake_broker_results_or_case_count(self):
        source = journal()
        source["broker_trades_executed"] = 1
        with self.assertRaisesRegex(ValueError, "disconnected"):
            summarize_forward_evidence(source)
        source = journal(triggers=0, accepted=[{"asset":"BTC"}])
        with self.assertRaisesRegex(ValueError, "Distinct"):
            summarize_forward_evidence(source)

    def test_cannot_relabel_broker_challenge_pass_as_shadow(self):
        source = journal()
        source["provisional_challenge_risk"]["kraken_funded_challenge_pass_confirmed"] = True
        with self.assertRaisesRegex(ValueError,"disconnected"):
            summarize_forward_evidence(source)


if __name__ == "__main__":
    unittest.main()
