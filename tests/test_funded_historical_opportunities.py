"""Deterministic no-hindsight missed-mover and non-mover research invariants."""
import unittest

import pandas as pd
import numpy as np

from funded_historical_opportunities import (
    opportunity_census, census_summary, _fold,
)


def prices(n=50, *, spike_index=None, factor=1.0):
    dates = pd.bdate_range("2025-03-03", periods=n)
    close = np.full(n, 100.0)
    if spike_index is not None:
        close[spike_index] *= factor
    return pd.DataFrame({"Close": close}, index=dates)


class OpportunityCensusTests(unittest.TestCase):
    def test_future_outcome_never_changes_preexisting_decision(self):
        raw = prices()
        t = raw.index[8]
        decisions = [{"Ticker": "AAPL", "Signal date": t, "Route": "CORE RSI/MACD"}]
        kwargs = dict(
            asset_type="stocks", signals=decisions, audited_symbols={"AAPL"},
            windows=(5,), thresholds_pct=(10,), min_history=5
        )
        before, _ = opportunity_census({"AAPL": raw}, **kwargs)
        changed = raw.copy()
        changed.loc[changed.index[10], "Close"] = 125.0
        after, _ = opportunity_census({"AAPL": changed}, **kwargs)
        a = before[before["Signal date"] == str(t)].iloc[0]
        b = after[after["Signal date"] == str(t)].iloc[0]
        self.assertEqual(a["Original gate class"], b["Original gate class"])
        self.assertTrue(a["Original technical signal"])
        self.assertTrue(b["Original technical signal"])
        self.assertFalse(a["Mover"])
        self.assertTrue(b["Mover"])

    def test_unreviewed_movers_cannot_be_called_missed_trades(self):
        data = prices(spike_index=12, factor=1.3)
        df, diagnostic = opportunity_census(
            {"BTC": data}, asset_type="crypto",
            windows=(5,), thresholds_pct=(10,), min_history=5,
        )
        self.assertTrue((df["Original gate class"] == "UNKNOWN_NOT_AUDITED").all())
        self.assertTrue(df["Mover"].any())
        self.assertFalse(df["Missed-mover anchor (AUDITED ONLY)"].any())
        self.assertEqual(diagnostic.iloc[0]["status"], "NOT AUDITED")
        self.assertTrue(census_summary(df).empty)

    def test_audited_nonmovers_remain_in_denominator(self):
        data = prices(spike_index=14, factor=1.2)
        df, _ = opportunity_census(
            {"AAPL": data}, asset_type="stocks", audited_symbols={"AAPL"},
            windows=(5,), thresholds_pct=(10,), min_history=5,
        )
        self.assertTrue(df["Mover"].any())
        self.assertTrue((~df["Mover"]).any())
        self.assertTrue(df["Missed-mover anchor (AUDITED ONLY)"].any())
        self.assertTrue((df["Original gate class"] == "NO_PREFILTER_TRIGGER").all())
        report = census_summary(df)
        self.assertEqual(report["Future mover anchors"].sum(),
                         df[df["Fold"].isin(("TRAIN", "VALIDATION", "HOLDOUT"))]["Mover"].sum())
        self.assertEqual(report["Original technical triggers"].sum(), 0)
        self.assertEqual(report["Missed mover anchors"].sum(), report["Future mover anchors"].sum())

    def test_audit_watch_and_rejected_are_not_qualified(self):
        data = prices()
        d1, d2 = data.index[7], data.index[8]
        trace = [
            {"Ticker": "AAPL", "Signal date": d1,
             "Core state": "WATCH", "Core reason": "MACD NOT CROSSED"},
            {"Ticker": "AAPL", "Signal date": d2,
             "Core state": "BLOCKED", "Core reason": "LOW VOLUME",
             "Breakout trigger": True},
        ]
        df, _ = opportunity_census(
            {"AAPL": data}, asset_type="stocks", audit_trace=trace,
            audited_symbols={"AAPL"}, windows=(2,), thresholds_pct=(10,), min_history=5,
        )
        self.assertEqual(df[df["Signal date"] == str(d1)].iloc[0]["Original gate class"], "WATCH_ONLY")
        rec = df[df["Signal date"] == str(d2)].iloc[0]
        self.assertEqual(rec["Original gate class"], "PREFILTER_REJECTED")
        self.assertIn("actual rejection gate not supplied", rec["Original gate reason"])
        self.assertFalse(rec["Original technical signal"])

    def test_folds_purge_overlapping_outcome_windows(self):
        self.assertEqual(_fold(24, 50, 5), "TRAIN")
        self.assertEqual(_fold(26, 50, 5), "PURGED_TRAIN_TO_VALIDATION")
        self.assertEqual(_fold(36, 50, 5), "PURGED_VALIDATION_TO_HOLDOUT")
        self.assertEqual(_fold(40, 50, 5), "HOLDOUT")
        df, _ = opportunity_census(
            {"AAPL": prices()}, asset_type="stocks", audited_symbols={"AAPL"},
            windows=(5,), thresholds_pct=(10,), min_history=5,
        )
        self.assertIn("PURGED_TRAIN_TO_VALIDATION", set(df["Fold"]))
        self.assertIn("PURGED_VALIDATION_TO_HOLDOUT", set(df["Fold"]))
        self.assertFalse(census_summary(df)["Fold"].str.contains("PURGED").any())

    def test_incomplete_horizon_is_censored_not_a_nonmover(self):
        n=20
        df, _ = opportunity_census(
            {"AAPL": prices(n)}, asset_type="stocks", audited_symbols={"AAPL"},
            windows=(5,), thresholds_pct=(10,), min_history=5,
        )
        self.assertEqual(len(df), n - 5 - (5 - 1))
        self.assertTrue((df["Signal date"] < str(prices(n).index[-5])).all())

    def test_qualified_signal_requires_independent_audit(self):
        raw=prices()
        with self.assertRaisesRegex(ValueError, "point-in-time audit"):
            opportunity_census(
                {"AAPL": raw}, asset_type="stocks",
                signals=[{"Ticker":"AAPL","Signal date":raw.index[5]}],
                windows=(5,),min_history=5,
            )

    def test_duplicate_signal_and_nonexistent_date_fail_closed(self):
        raw=prices()
        events=[{"Ticker":"AAPL","Signal date":raw.index[5]}]*2
        with self.assertRaisesRegex(ValueError,"duplicate"):
            opportunity_census({"AAPL":raw},asset_type="stocks",
                signals=events,audited_symbols={"AAPL"},windows=(5,),min_history=5)
        with self.assertRaisesRegex(ValueError,"not in history"):
            opportunity_census({"AAPL":raw},asset_type="stocks",
                signals=[{"Ticker":"AAPL","Signal date":"2020-01-01"}],
                audited_symbols={"AAPL"},windows=(5,),min_history=5)

    def test_invalid_bars_or_duplicate_timestamps_fail_closed(self):
        a=prices()
        a.loc[a.index[3],"Close"]=float("nan")
        with self.assertRaisesRegex(ValueError,"invalid"):
            opportunity_census({"AAPL":a},asset_type="stocks",
                windows=(5,),min_history=5)
        a=prices()
        a=pd.concat([a,a.iloc[-1:]])
        with self.assertRaisesRegex(ValueError,"duplicate"):
            opportunity_census({"AAPL":a},asset_type="stocks",
                windows=(5,),min_history=5)

    def test_crypto_bars_preserve_multiple_same_date_slots(self):
        dates=pd.date_range("2025-01-01",periods=55,freq="4h",tz="UTC")
        a=pd.DataFrame({"Close":[100.]*55},index=dates)
        events=[{"Ticker":"BTC","Signal date":dates[7],"Route":"original crypto BUY"}]
        df,diag=opportunity_census({"BTC":a},asset_type="crypto",
            audited_symbols={"BTC"},signals=events,windows=(5,),
            thresholds_pct=(10,),min_history=5)
        self.assertEqual((df["Original gate class"]=="QUALIFIED_TECHNICAL_SIGNAL").sum(),1)
        self.assertTrue(df["Signal date"].str.contains("04:00:00|08:00:00|12:00:00").any())
        self.assertEqual(diag.iloc[0]["status"],"PREFILTER AUDITED")

    def test_suspect_non_positive_threshold_is_disallowed(self):
        with self.assertRaisesRegex(ValueError,"threshold"):
            opportunity_census({"AAPL":prices()},asset_type="stocks",
                windows=(5,),thresholds_pct=(0,),min_history=5)


if __name__ == "__main__":
    unittest.main()
