"""Portfolio replay tests use SYNTHETIC prices, not live investment evidence."""
import unittest
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

from pension_point_in_time import (
    HistoricalDatasetManifest, HistoricalUniverseMember,
    HistoricalFundamental, HistoricalDecision,
)
from pension_portfolio_replay import HistoricalPriceBar, replay_point_in_time_decisions

UTC=timezone.utc
D0=date(2019,4,1)
D1=date(2019,4,2)
D2=date(2019,4,3)
D3=date(2019,4,4)


def fake_inputs():
    manifest=HistoricalDatasetManifest(
        start=date(2019,1,1),end=date(2020,1,1),
        source_name="SYNTHETIC ONLY",
        point_in_time_financials_verified=True,
        includes_delisted_and_failed_companies=True,
        corporate_actions_and_splits_verified=True,
        cash_dividends_complete=True,
        historical_currency_rates_complete=True,
    )
    universe=[HistoricalUniverseMember("ABC",date(2010,1,1),None,"SYNTHETIC"),HistoricalUniverseMember("FAILED",date(2010,1,1),date(2018,7,1),"SYNTHETIC")]
    filing=[HistoricalFundamental(
        record_id="abc-fixture",ticker="ABC",accounting_period_end=date(2018,12,31),
        first_public_at=datetime(2019,3,1,tzinfo=UTC),effective_revision_at=datetime(2019,3,1,tzinfo=UTC),
        source_reference="SYNTHETIC",payload_hash="b"*64,
    )]
    decision=[HistoricalDecision(
        ticker="ABC",action="BUY CANDIDATE",
        decided_at=datetime(2019,4,1,14,0,tzinfo=UTC),
        fundamental_record_id="abc-fixture",
        quote_observed_at=datetime(2019,4,1,13,55,tzinfo=UTC),quote_source="SYNTHETIC"
    )]
    bars=[
        HistoricalPriceBar(D0,"ABC","Technology",10,10),
        HistoricalPriceBar(D1,"ABC","Technology",10,10),
        # £1 ex-dividend; next ex-date price falls £1; no artificial loss.
        HistoricalPriceBar(D2,"ABC","Technology",9,9,1),
        HistoricalPriceBar(D3,"ABC","Technology",9,9),
    ]
    return manifest,universe,filing,decision,bars


def run(*,inputset=None,cost=0):
    a,b,c,d,e=inputset or fake_inputs()
    return replay_point_in_time_decisions(a,b,c,d,e,initial_cash_gbp=1000,cost_pct_per_side=cost)


class PensionReplayContractTests(unittest.TestCase):
    def test_first_trade_is_next_session_not_signal_day(self):
        report=run()
        self.assertEqual(report.fills[0].day,D1)
        self.assertEqual(report.fills[0].shares,5)
        self.assertEqual(report.nav[0].closing_nav_gbp,1000)
        self.assertEqual(report.nav[1].closing_nav_gbp,1000)

    def test_ex_dividend_does_not_create_a_fake_loss_or_profit(self):
        report=run()
        self.assertEqual(report.nav[2].closing_nav_gbp,1000)
        self.assertEqual(report.end_cash_gbp,955)

    def test_spread_or_dealing_cost_reduces_eligible_position_size(self):
        report=run(cost=1)
        self.assertEqual(report.fills[0].shares,4)
        self.assertLess(report.nav[1].closing_nav_gbp,1000)
        self.assertGreater(report.end_cash_gbp,0)

    def test_sell_is_next_day_and_dividend_entitlement_preserved(self):
        a,b,c,d,e=fake_inputs()
        d.append(replace(
            d[0],action="SELL",decided_at=datetime(2019,4,3,15,tzinfo=UTC),
            quote_observed_at=datetime(2019,4,3,14,tzinfo=UTC)
        ))
        report=run(inputset=(a,b,c,d,e))
        self.assertEqual([x.action for x in report.fills],["BUY CANDIDATE","SELL"])
        self.assertEqual(report.fills[1].day,D3)
        self.assertEqual(report.end_share_counts,{})
        self.assertEqual(report.end_cash_gbp,1000)

    def test_buy_more_does_not_create_a_new_position(self):
        a,b,c,d,e=fake_inputs()
        d=[replace(d[0],action="BUY MORE")]
        report=run(inputset=(a,b,c,d,e))
        self.assertEqual(len(report.fills),0)
        self.assertEqual(report.unfilled_eligible_actions,1)

    def test_missing_held_stock_price_prevents_report(self):
        a,b,c,d,e=fake_inputs()
        e=[bar for bar in e if bar.day!=D2]
        with self.assertRaisesRegex(ValueError,"missing a price mark"):
            run(inputset=(a,b,c,d,e))

    def test_no_execution_without_following_session(self):
        a,b,c,d,e=fake_inputs()
        d=[replace(d[0],decided_at=datetime(2019,4,4,14,tzinfo=UTC),quote_observed_at=datetime(2019,4,4,13,50,tzinfo=UTC))]
        with self.assertRaisesRegex(ValueError,"unexecuted historical actions"):
            run(inputset=(a,b,c,d,e))

    def test_conflicting_same_ticker_actions_not_cherry_picked(self):
        a,b,c,d,e=fake_inputs()
        d.append(replace(d[0],action="SELL",decided_at=datetime(2019,4,1,15,tzinfo=UTC),quote_observed_at=datetime(2019,4,1,14,tzinfo=UTC)))
        with self.assertRaisesRegex(ValueError,"conflicting"):
            run(inputset=(a,b,c,d,e))

    def test_missing_point_in_time_data_prevents_any_backtest(self):
        a,b,c,d,e=fake_inputs()
        a=replace(a,includes_delisted_and_failed_companies=False)
        with self.assertRaisesRegex(ValueError,"HISTORICAL BACKTEST NOT VALIDATED"):
            run(inputset=(a,b,c,d,e))


if __name__=="__main__":
    unittest.main()
