"""PIT data-contract tests with SYNTHETIC records, not investment results."""
import unittest
from dataclasses import replace
from datetime import datetime, date, timedelta, timezone

from pension_point_in_time import (
    HistoricalDatasetManifest,
    HistoricalUniverseMember,
    HistoricalFundamental,
    HistoricalDecision,
    audit_historical_inputs,
    require_historical_inputs,
)

UTC = timezone.utc
DECISION = datetime(2019, 6, 1, 15, 0, tzinfo=UTC)
HASH = "a" * 64


def sample():
    manifest = HistoricalDatasetManifest(
        start=date(2018, 1, 1),
        end=date(2020, 1, 1),
        source_name="Synthetic fixture, not an independent vendor",
        point_in_time_financials_verified=True,
        includes_delisted_and_failed_companies=True,
        corporate_actions_and_splits_verified=True,
        cash_dividends_complete=True,
        historical_currency_rates_complete=True,
    )
    universe = [
        HistoricalUniverseMember("ABC", date(2010, 1, 1), None, "synthetic listing reference"),
        HistoricalUniverseMember("FAILED", date(2010, 1, 1), date(2018, 7, 2), "synthetic delisting record"),
    ]
    filings = [HistoricalFundamental(
        record_id="ABC-2018-R0", ticker="ABC",
        accounting_period_end=date(2018, 12, 31),
        first_public_at=datetime(2019, 3, 1, tzinfo=UTC),
        effective_revision_at=datetime(2019, 3, 1, tzinfo=UTC),
        source_reference="synthetic issuer filing",
        payload_hash=HASH,
    )]
    decisions = [HistoricalDecision(
        ticker="ABC", action="BUY CANDIDATE",
        decided_at=DECISION, fundamental_record_id="ABC-2018-R0",
        quote_observed_at=DECISION-timedelta(hours=1),
        quote_source="synthetic exchange OHLCV",
    )]
    return manifest, universe, filings, decisions


class DataChronology(unittest.TestCase):
    def assert_issue(self, fragment, manifest, universe, filings, decisions):
        problems = audit_historical_inputs(manifest, universe, filings, decisions)
        self.assertTrue(any(fragment in p for p in problems), problems)

    def test_complete_synthetic_fixture_passes_chronology_only(self):
        a,b,c,d = sample()
        self.assertEqual(audit_historical_inputs(a,b,c,d), [])
        require_historical_inputs(manifest=a,universe=b,fundamentals=c,decisions=d)

    def test_missing_survivors_and_failing_companies_is_rejected(self):
        a,b,c,d = sample()
        self.assert_issue("includes_delisted", replace(a, includes_delisted_and_failed_companies=False),b,c,d)

    def test_missing_dividends_and_fx_and_splits_is_rejected(self):
        a,b,c,d = sample()
        x=replace(a,cash_dividends_complete=False,historical_currency_rates_complete=False,corporate_actions_and_splits_verified=False)
        self.assert_issue("cash_dividends_complete", x,b,c,d)
        self.assert_issue("historical_currency_rates_complete", x,b,c,d)
        self.assert_issue("corporate_actions_and_splits_verified", x,b,c,d)

    def test_future_filing_cannot_create_past_signal(self):
        a,b,c,d = sample()
        c=[replace(c[0],first_public_at=DECISION+timedelta(days=1),effective_revision_at=DECISION+timedelta(days=1))]
        self.assert_issue("filing look-ahead",a,b,c,d)

    def test_future_amendment_cannot_create_past_signal(self):
        a,b,c,d = sample()
        c=[replace(c[0],effective_revision_at=DECISION+timedelta(days=1))]
        self.assert_issue("revision look-ahead",a,b,c,d)

    def test_future_quote_and_stale_quote_cannot_enter_backtest(self):
        a,b,c,d = sample()
        self.assert_issue("quote is from the future",a,b,c,[replace(d[0],quote_observed_at=DECISION+timedelta(minutes=1))])
        self.assert_issue("historical quote is stale",a,b,c,[replace(d[0],quote_observed_at=DECISION-timedelta(days=12))])

    def test_invalid_ticker_and_delisted_universe_cannot_enter(self):
        a,b,c,d = sample()
        self.assert_issue("absent from point-in-time",a,b,c,[replace(d[0],ticker="FAILED")])
        self.assert_issue("absent from point-in-time",a,b,c,[replace(d[0],ticker="SURVIVOR_2026")])

    def test_missing_filing_or_wrong_company_fails(self):
        a,b,c,d=sample()
        self.assert_issue("missing dated fundamental",a,b,c,[replace(d[0],fundamental_record_id="missing")])
        self.assert_issue("different ticker",a,b,[replace(c[0],ticker="XYZ")],d)

    def test_missing_timezone_rejected(self):
        a,b,c,d=sample()
        self.assert_issue("require timezones",a,b,c,[replace(d[0],decided_at=DECISION.replace(tzinfo=None))])

    def test_bad_hash_and_duplicate_events_fail(self):
        a,b,c,d=sample()
        self.assert_issue("source/hash missing",a,b,[replace(c[0],payload_hash="")],d)
        self.assert_issue("duplicate decision",a,b,c,d+d)

    def test_cannot_silently_return_performance_for_empty_input(self):
        a,b,c,d=sample()
        with self.assertRaisesRegex(ValueError,"NOT VALIDATED"):
            require_historical_inputs(manifest=a,universe=b,fundamentals=c,decisions=[])


if __name__ == "__main__":
    unittest.main()
