"""No lookahead labels or fabricated signals from paired historical cohorts."""
import unittest
from trade_missed_mover_diagnostics import summarize_paired_cohorts


def counters():
    return {
        "mover_5bar_ANCHORS":5,
        "mover_5bar_CENSORED":2,
        "paired_5bar_5pct_BLOCKED_MOVER":1,
        "paired_5bar_5pct_BLOCKED_NONMOVER":3,
        "paired_5bar_5pct_WATCH_NONMOVER":1,
        "paired_5bar_10pct_BLOCKED_NONMOVER":4,
        "paired_5bar_10pct_WATCH_NONMOVER":1,
        "paired_5bar_20pct_BLOCKED_NONMOVER":4,
        "paired_5bar_20pct_WATCH_NONMOVER":1,
        "mover_10bar_ANCHORS":0,
        "mover_20bar_ANCHORS":0,
    }


class PairedCohortTests(unittest.TestCase):
    def test_mover_and_nonmover_pair_counts_and_state_provenance(self):
        r=summarize_paired_cohorts({"symbols":[{"symbol":"AAPL","counters":counters()}]})
        self.assertEqual(r["readiness"],"NOT_VERIFIED")
        self.assertFalse(r["historical_constituents_reconstructed"])
        a=next(z for z in r["rows"] if z["forward_sessions"]==5
               and z["rise_threshold_pct"]==5
               and z["original_asof_technical_state"]=="BLOCKED")
        self.assertEqual(a["future_mover"],1)
        self.assertEqual(a["future_nonmover"],3)
        self.assertEqual(a["descriptive_mover_share_pct"],25)
        self.assertEqual(a["interpretation"],"EX_POST_LABEL_ONLY_NOT_MISSED_APPROVED_BUY")
        self.assertEqual(r["censored"][0]["censored"],2)

    def test_bad_denominator_blocks_report(self):
        c=counters()
        c["paired_5bar_5pct_BLOCKED_NONMOVER"]=2
        with self.assertRaisesRegex(ValueError,"PAIR_COUNT_MISMATCH"):
            summarize_paired_cohorts({"symbols":[{"symbol":"AAPL","counters":c}]})

    def test_censoring_is_never_a_future_nonmover(self):
        c=counters()
        c["mover_5bar_CENSORED"]=100
        r=summarize_paired_cohorts({"symbols":[{"symbol":"AAPL","counters":c}]})
        observed=sum(x["total_completed_future_labels"] for x in r["rows"]
                     if x["forward_sessions"]==5 and x["rise_threshold_pct"]==5)
        self.assertEqual(observed,5)
        self.assertEqual(r["censored"][0]["censored"],100)

    def test_no_reported_approved_trades_on_technical_pilot(self):
        r=summarize_paired_cohorts({"symbols":[{"symbol":"AAPL","counters":counters()}]})
        self.assertEqual(r["approved_historical_trade_entries"],0)
        self.assertFalse(r["asof_fundamentals_and_events_verified"])

    def test_missing_provider_symbol_not_assumed_no_movers(self):
        r=summarize_paired_cohorts({"symbols":[{"symbol":"XYZ","error":"source unavailable",
                                               "events":[]}]})
        self.assertEqual(r["rows"],[])
        self.assertEqual(r["censored"][0]["reason"],"SOURCE_FAILURE")

if __name__=="__main__":
    unittest.main()
