import unittest
from dataclasses import replace
from datetime import date

from pension_fund_comparison import FundPerformance, rank_comparable_returns


def observation(provider, value):
    return FundPerformance(
        provider=provider,
        fund=f"{provider} example fund",
        period_start=date(2025, 9, 30),
        period_end=date(2026, 9, 30),
        cumulative_total_return_pct=value,
        reporting_currency="GBP",
        includes_distributions=True,
        adjusted_for_all_member_fees=True,
    )


class ComparablePensionFundReturns(unittest.TestCase):
    def setUp(self):
        self.examples = [
            observation("Standard Life", 11.0),
            observation("Smart Pension", 8.0),
            observation("Aviva", 13.0),
        ]

    def test_identical_net_return_periods_can_be_ranked(self):
        result = rank_comparable_returns(self.examples)
        self.assertEqual([x.provider for x in result], ["Aviva", "Standard Life", "Smart Pension"])

    def test_missing_third_provider_is_not_comparable(self):
        with self.assertRaisesRegex(ValueError, "missing comparable"):
            rank_comparable_returns(self.examples[:2])

    def test_unequal_dates_are_not_comparable(self):
        rows = self.examples.copy()
        rows[1] = replace(rows[1], period_end=date(2026, 10, 15))
        with self.assertRaisesRegex(ValueError, "different"):
            rank_comparable_returns(rows)

    def test_member_flat_fee_must_be_accounted_for(self):
        rows = self.examples.copy()
        rows[1] = replace(rows[1], adjusted_for_all_member_fees=False)
        with self.assertRaisesRegex(ValueError, "member-level costs"):
            rank_comparable_returns(rows)

    def test_non_gbp_and_missing_dividends_cannot_pass(self):
        rows = self.examples.copy()
        rows[1] = replace(rows[1], reporting_currency="USD")
        with self.assertRaisesRegex(ValueError, "GBP"):
            rank_comparable_returns(rows)
        rows[1] = replace(rows[1], reporting_currency="GBP", includes_distributions=False)
        with self.assertRaisesRegex(ValueError, "distributions"):
            rank_comparable_returns(rows)

    def test_duplicate_provider_rejected(self):
        rows = self.examples + [observation("Aviva", 99)]
        with self.assertRaisesRegex(ValueError, "duplicate"):
            rank_comparable_returns(rows)


if __name__ == "__main__":
    unittest.main()
