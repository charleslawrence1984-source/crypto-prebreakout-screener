"""Forward stock paper journal contract tests: no backfills and no orders."""
import unittest

import numpy as np
import pandas as pd

from funded_stock_paper import (
    COLUMNS, COSTS_PCT, empty_journal, capture_new_breakouts,
    import_journal_csv, update_paper_outcomes, summarize_paper_journal,
    explain_capture_eligibility,
)


class FundedStockPaperTests(unittest.TestCase):
    NOW = pd.Timestamp("2026-10-15T20:30:00Z")

    def scan(self, **changes):
        row = {
            "Ticker": "MU",
            "Scan source": "LIVE",
            "scanned_at": (self.NOW - pd.Timedelta(minutes=2)).isoformat(),
            "Technical state": "FUNDED BREAKOUT REVIEW",
            "Gate": "PROJECTED R:R BELOW 2; STRUCTURAL STOP OUTSIDE 0.4–8%",
            "Technical score": 90.0,
            "Breakout date": "2026-10-15",
            "Last completed bar": "2026-10-15",
            "Reference entry": 100.0,
            "Reference stop": 88.0,
            "Research retest stop": 98.0,
            "Reference target": 120.0,
            "Resistance": 99.6,
        }
        row.update(changes)
        return pd.DataFrame([row])

    def journal(self, **changes):
        out, n = capture_new_breakouts(self.scan(**changes), empty_journal(), self.NOW)
        self.assertEqual(n, 4)
        return out

    def future_prices(self, second=None, first=None, length=5):
        dates = pd.bdate_range("2026-10-15", periods=length+1)
        data = pd.DataFrame({
            "Open": [100.0] * (length+1),
            "High": [101.0] * (length+1),
            "Low": [99.0] * (length+1),
            "Close": [100.0] * (length+1),
        }, index=dates)
        if first:
            data.loc[dates[1], ["Open", "High", "Low", "Close"]] = first
        if second:
            data.loc[dates[2], ["Open", "High", "Low", "Close"]] = second
        return {"MU": data}

    def test_zero_capture_diagnostics_report_no_scan_and_no_breakout(self):
        empty = explain_capture_eligibility(pd.DataFrame(), empty_journal(), self.NOW)
        self.assertEqual(len(empty), 1)
        self.assertIn("NO FRESH STOCK SCAN", empty.iloc[0]["Paper journal assessment"])
        blocked = explain_capture_eligibility(
            self.scan(**{
                "Technical state": "BLOCKED",
                "Breakout date": np.nan,
            }),
            empty_journal(), self.NOW,
        )
        self.assertEqual(
            blocked.iloc[0]["Paper journal assessment"],
            "NO BREAKOUT ON THE LAST COMPLETED US SESSION",
        )

    def test_explanations_are_consistent_with_capture_decision(self):
        cases = [
            ({"scanned_at": (self.NOW-pd.Timedelta(minutes=30)).isoformat()},
             "STOCK SCAN EXPIRED"),
            ({"Technical state": "FUNDED BREAKOUT REVIEW",
              "Gate": "NO CONSTRUCTIVE BASE"},
             "OTHER TECHNICAL"),
            ({"Technical score": 72},
             "BREAKOUT QUALITY"),
            ({"Reference target": np.nan},
             "INCOMPLETE OR INVALID"),
        ]
        for changes, expected in cases:
            with self.subTest(case=changes):
                scan = self.scan(**changes)
                explain = explain_capture_eligibility(
                    scan, empty_journal(), self.NOW,
                )
                self.assertIn(expected, explain.iloc[0]["Paper journal assessment"])
                _, n = capture_new_breakouts(scan, empty_journal(), self.NOW)
                self.assertEqual(n, 0)
        ready = explain_capture_eligibility(self.scan(), empty_journal(), self.NOW)
        self.assertEqual(
            ready.iloc[0]["Paper journal assessment"],
            "ELIGIBLE — NEW PROSPECTIVE PAPER SNAPSHOT",
        )
        book = self.journal()
        recorded = explain_capture_eligibility(self.scan(), book, self.NOW)
        self.assertEqual(
            recorded.iloc[0]["Paper journal assessment"],
            "ALREADY CAPTURED — NO DUPLICATE",
        )

    def test_provider_outage_never_erases_a_completed_paper_result(self):
        book = self.journal()
        prices = self.future_prices(second=[100, 121, 99, 120])
        good = update_paper_outcomes(
            book, prices, self.NOW+pd.Timedelta(days=10),
        )
        self.assertEqual(int(good["Status"].eq("COMPLETED").sum()), 2)
        missing = update_paper_outcomes(
            good, {}, self.NOW+pd.Timedelta(days=11),
        )
        pd.testing.assert_frame_equal(good, missing)
        bad = update_paper_outcomes(
            good, {"MU": pd.DataFrame()}, self.NOW+pd.Timedelta(days=11),
        )
        pd.testing.assert_frame_equal(good, bad)

    def test_missing_original_entry_candle_preserves_completed_paper_result(self):
        book = self.journal()
        good = update_paper_outcomes(
            book, self.future_prices(second=[100, 121, 99, 120]),
            self.NOW+pd.Timedelta(days=10),
        )
        trimmed = self.future_prices(length=15)
        trimmed["MU"] = trimmed["MU"].iloc[-3:]
        subsequent = update_paper_outcomes(
            good, trimmed, self.NOW+pd.Timedelta(days=50),
        )
        # Previously captured final outcomes cannot disappear simply
        # because the source truncated the old price window.
        final_before = good[good["Status"].eq("COMPLETED")]
        final_after = subsequent[subsequent["Status"].eq("COMPLETED")]
        pd.testing.assert_frame_equal(final_before, final_after)

    def test_live_signal_capture_creates_four_frozen_methods_costs(self):
        saved = self.journal()
        self.assertEqual(len(saved), 4)
        self.assertEqual(list(saved.columns), COLUMNS)
        self.assertEqual(set(saved["Roundtrip cost %"]), set(COSTS_PCT))
        self.assertEqual(saved["Status"].unique().tolist(),
                         ["AWAITING NEXT COMPLETE US SESSION"])
        self.assertEqual(saved.loc[
            saved["Stop method"] == "RESISTANCE RETEST (research)",
            "Frozen stop",
        ].tolist(), [98.0, 98.0])
        self.assertEqual(saved["Signal date"].unique().tolist(), ["2026-10-15"])
        again, n = capture_new_breakouts(self.scan(), saved, self.NOW)
        self.assertEqual(n, 0)
        pd.testing.assert_frame_equal(saved, again)

    def test_never_backfill_stale_invalid_or_non_breakout_watch(self):
        variations = [
            {"Scan source": "PREPARED"},
            {"scanned_at": (self.NOW-pd.Timedelta(minutes=21)).isoformat()},
            {"Gate": "PROJECTED R:R BELOW 2; NO CONSTRUCTIVE BASE"},
            {"Technical state": "WATCH"},
            {"Technical score": 65},
            {"Technical score": np.nan},
            {"Breakout date": "2026-10-13"},
            {"Research retest stop": np.nan},
            {"Reference target": np.nan},
            {"Last completed bar": "2026-10-14"},
        ]
        for changed in variations:
            with self.subTest(changed=changed):
                out, n = capture_new_breakouts(self.scan(**changed), empty_journal(), self.NOW)
                self.assertEqual(n, 0)
                self.assertTrue(out.empty)

    def test_cannot_capture_after_next_regular_session_open(self):
        next_day = pd.Timestamp("2026-10-16T14:05:00Z")
        scan = self.scan(scanned_at=(next_day-pd.Timedelta(minutes=2)).isoformat())
        snap, n = capture_new_breakouts(scan, empty_journal(), next_day)
        self.assertEqual(n, 0)
        self.assertTrue(snap.empty)

    def test_csv_roundtrip_and_tampered_stop_rejected(self):
        book = self.journal()
        parsed = import_journal_csv(
            book.to_csv(index=False).encode("utf-8"), {"MU"},
            now=self.NOW + pd.Timedelta(minutes=1),
        )
        self.assertEqual(len(parsed), 4)
        modified = parsed.copy()
        modified.loc[0, "Frozen stop"] = 105
        with self.assertRaises(ValueError):
            import_journal_csv(
                modified.to_csv(index=False).encode(), {"MU"},
                now=self.NOW + pd.Timedelta(minutes=1),
            )
        modified = parsed.copy()
        modified.loc[0, "Paper ID"] = parsed.loc[1, "Paper ID"]
        with self.assertRaises(ValueError):
            import_journal_csv(
                modified.to_csv(index=False).encode(), {"MU"},
                now=self.NOW + pd.Timedelta(minutes=1),
            )

    def test_no_outcomes_until_next_completed_daily_bar(self):
        book = self.journal()
        only_signal = self.future_prices(length=0)
        updated = update_paper_outcomes(book, only_signal, self.NOW)
        self.assertTrue(updated["Status"].eq(
            "AWAITING NEXT COMPLETE US SESSION"
        ).all())
        self.assertTrue(updated["Realized R"].isna().all())

    def test_next_open_requalifies_and_future_target_is_observed(self):
        book = self.journal()
        prices = self.future_prices(second=[100, 121, 99, 120])
        updated = update_paper_outcomes(
            book, prices, self.NOW + pd.Timedelta(days=10),
        )
        control = updated[updated["Stop method"] == "APPROVED STRUCTURAL (control)"]
        self.assertTrue(control["Status"].eq("REJECTED — STOP OUTSIDE 0.4–8%").all())
        retest = updated[updated["Stop method"] == "RESISTANCE RETEST (research)"]
        self.assertTrue(retest["Status"].eq("COMPLETED").all())
        self.assertTrue(retest["Exit reason"].eq("TARGET").all())
        self.assertTrue((retest["Realized R"] > 2).all())
        self.assertEqual(retest["Next entry date"].unique().tolist(), ["2026-10-16"])
        self.assertEqual(retest["Exit date"].unique().tolist(), ["2026-10-19"])
        summary = summarize_paper_journal(updated)
        self.assertEqual(summary["Completed"].sum(), 2)
        self.assertEqual(summary["Wins"].sum(), 2)
        self.assertEqual(summary["Next-open accepted"].sum(), 2)

    def test_stop_wins_ambiguous_stop_target_bar(self):
        book = self.journal()
        prices = self.future_prices(second=[100, 121, 97, 101])
        updated = update_paper_outcomes(book, prices, self.NOW+pd.Timedelta(days=10))
        retest = updated[updated["Stop method"] == "RESISTANCE RETEST (research)"]
        self.assertTrue(retest["Exit reason"].eq("STOP (TARGET ALSO CROSSED)").all())
        self.assertTrue(retest["Realized R"].eq(-1).all())

    def test_next_open_gap_above_one_percent_is_rejected(self):
        book = self.journal()
        future = self.future_prices(first=[104.0, 105, 103, 104])
        updated = update_paper_outcomes(book, future, self.NOW+pd.Timedelta(days=10))
        self.assertTrue(updated["Status"].eq(
            "REJECTED — GAP ABOVE SIGNAL CLOSE"
        ).all())

    def test_stress_cost_can_fail_two_R_when_baseline_passes(self):
        book = self.journal(**{"Reference target": 106.5})
        updated = update_paper_outcomes(
            book, self.future_prices(), self.NOW+pd.Timedelta(days=10)
        )
        method = updated[updated["Stop method"] == "RESISTANCE RETEST (research)"]
        baseline = method[method["Roundtrip cost %"] == .5].iloc[0]
        stress = method[method["Roundtrip cost %"] == 1.].iloc[0]
        self.assertEqual(baseline["Status"], "OPEN PAPER PROXY")
        self.assertEqual(stress["Status"], "REJECTED — NET R:R BELOW 2")

    def test_20th_completed_session_forces_time_exit(self):
        book = self.journal()
        updated = update_paper_outcomes(
            book, self.future_prices(length=20), self.NOW+pd.Timedelta(days=40)
        )
        retest = updated[updated["Stop method"] == "RESISTANCE RETEST (research)"]
        self.assertTrue(retest["Status"].eq("COMPLETED").all())
        self.assertTrue(retest["Exit reason"].eq("TIME EXIT").all())
        self.assertTrue(retest["Realized R"].lt(0).all())

    def test_incomplete_next_open_data_does_not_shift_entry_to_later_price(self):
        book = self.journal()
        late = self.future_prices(length=7)
        late["MU"] = late["MU"].iloc[-1:]
        updated = update_paper_outcomes(book, late, self.NOW+pd.Timedelta(days=10))
        self.assertTrue(updated["Status"].eq("INCOMPLETE ENTRY PRICE HISTORY").all())


if __name__ == "__main__":
    unittest.main()
