"""Unit tests for pension research fail-closed decisions. No market/API calls."""
import unittest
from datetime import datetime, timedelta, timezone

from pension_guardrails import Holding, check_portfolio, check_research_candidate, MODEL_VERSION


def valid_candidate():
    return {
        "Action": "BUY CANDIDATE",
        "Valuation model version": MODEL_VERSION,
        "Valuation FX status": "PASS",
        "Hard gates": "PASS",
        "Investability": "✅ INVESTABLE",
    }


NOW = datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc)


class PortfolioLimits(unittest.TestCase):
    def test_twenty_stocks_five_sectors_at_limits_are_valid(self):
        holdings = [
            Holding(f"S{i}", f"Sector {i // 4}", 100.0)
            for i in range(20)
        ]
        self.assertEqual(check_portfolio(holdings), [])

    def test_concentration_does_not_silently_pass(self):
        holdings = [Holding("A", "Technology", 70), Holding("B", "Technology", 30)]
        problems = check_portfolio(holdings)
        self.assertTrue(any("stock weight" in p for p in problems))
        self.assertTrue(any("sector weight" in p for p in problems))

    def test_cash_is_included_in_portfolio_denominator(self):
        holdings = [Holding("A", "Technology", 50)]
        self.assertEqual(check_portfolio(holdings, cash_gbp=950), [])

    def test_sector_unknown_and_duplicates_are_rejected(self):
        holdings = [Holding("A", "Unknown", 40), Holding("A", "Unknown", 60)]
        problems = check_portfolio(holdings)
        self.assertTrue(any("duplicate" in p for p in problems))
        self.assertTrue(any("unverified" in p for p in problems))

    def test_invalid_and_negative_values_fail_closed(self):
        self.assertTrue(check_portfolio([Holding("A", "Tech", -100)]))
        self.assertTrue(check_portfolio([], cash_gbp=float("nan")))
        self.assertTrue(check_portfolio([], cash_gbp=0))


class PensionResearchGate(unittest.TestCase):
    def check(self, candidate=None, when=NOW, approved=True, broker=True):
        return check_research_candidate(
            candidate or valid_candidate(),
            quote_observed_at=when,
            asof=NOW,
            manual_research_approved=approved,
            broker_instrument_verified=broker,
        )

    def test_verified_fresh_candidate_can_pass_screening_only(self):
        self.assertEqual(self.check(), [])

    def test_no_manual_approval_is_not_a_pension_buy(self):
        self.assertTrue(any("not signed off" in p for p in self.check(approved=False)))

    def test_broker_instrument_must_be_confirmed(self):
        self.assertTrue(any("SIPP instrument" in p for p in self.check(broker=False)))

    def test_stale_or_future_quote_cannot_pass(self):
        self.assertTrue(any("older" in p for p in self.check(when=NOW-timedelta(days=6))))
        self.assertTrue(any("future" in p for p in self.check(when=NOW+timedelta(minutes=1))))

    def test_unverified_model_fx_gates_and_listing_all_block(self):
        row = valid_candidate()
        row.update({
            "Valuation model version": "old",
            "Valuation FX status": "MISSING CURRENCY",
            "Hard gates": "UNKNOWN",
            "Investability": "SECONDARY",
        })
        self.assertGreaterEqual(len(self.check(candidate=row)), 4)

    def test_no_timestamp_fails_closed(self):
        self.assertIn(
            "quote timestamp unavailable",
            check_research_candidate(valid_candidate(), asof=NOW, manual_research_approved=True, broker_instrument_verified=True),
        )

    def test_timezone_required_for_asof(self):
        with self.assertRaises(ValueError):
            check_research_candidate(valid_candidate(), asof=NOW.replace(tzinfo=None))


if __name__ == "__main__":
    unittest.main()
