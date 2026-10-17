"""Strict forward research outcome tests; NO actual Kraken executions."""
from dataclasses import replace
from datetime import datetime,timedelta,timezone
import unittest

from funded_forward_capture import create_snapshot
from funded_forward_outcomes import CompletedBar,assess_frozen_outcome

T=datetime(2026,10,17,12,30,tzinfo=timezone.utc)
UNIVERSE={"stocks":("NVDA",),"crypto":("BTC",)}


def observation(kind,state):
    asset="NVDA" if kind=="stocks" else "BTC"
    snap=create_snapshot(kind,[{
        "asset":asset,"status":state,"entry":100.0,"stop":95.0,"target":110.0,
        "source":"mocked completed bar","bar_timestamp":"2026-10-17",
    }],observed_at=T,run_id="prospective-only",universe=UNIVERSE)
    return snap,snap["rows"][0]


def candles(*sets):
    result=[]
    for i,values in enumerate(sets):
        o,h,l,c=values
        result.append(CompletedBar(T+timedelta(hours=4*(i+1)),o,h,l,c))
    return result


class HypotheticalOutcomeTests(unittest.TestCase):
    def test_non_candidate_watches_never_turned_into_wins(self):
        for kind in ("crypto","stocks"):
            snap,row=observation(kind,"WATCH")
            out=assess_frozen_outcome(snap,row,candles((100,120,90,115)))
            self.assertEqual(out.assessment,"NOT A RESEARCH ENTRY")
            self.assertIsNone(out.realized_r_proxy)

    def test_conservative_stop_wins_collision(self):
        snap,row=observation("crypto","BUY")
        out=assess_frozen_outcome(snap,row,candles((100,113,93,106)))
        self.assertEqual(out.exit_reason,"STOP (SAME-BAR STOP FIRST)")
        self.assertLess(out.realized_r_proxy,0)
        self.assertIn("UNEXECUTED",out.caveat)

    def test_target_then_time_exit(self):
        snap,row=observation("stocks","ENTRY READY")
        hit=assess_frozen_outcome(snap,row,candles((100,111,99,110)))
        self.assertEqual(hit.exit_reason,"TARGET")
        self.assertGreater(hit.realized_r_proxy,0)
        time=assess_frozen_outcome(snap,row,candles((100,104,97,102),(102,105,98,103)),holding_bars=2)
        self.assertEqual(time.exit_reason,"TIME EXIT")

    def test_insufficient_bars_censored_not_claimed_win(self):
        snap,row=observation("crypto","BUY")
        out=assess_frozen_outcome(snap,row,candles((100,105,97,104)),holding_bars=3)
        self.assertEqual(out.assessment,"CENSORED — MORE BARS NEEDED")
        self.assertIsNone(out.realized_r_proxy)

    def test_gap_below_stop_is_never_an_entry(self):
        snap,row=observation("crypto","BUY")
        out=assess_frozen_outcome(snap,row,candles((93,97,90,94)))
        self.assertEqual(out.assessment,"NEXT OPEN OUTSIDE PLAN")

    def test_large_next_open_gap_invalidates_proxy(self):
        snap,row=observation("stocks","FUNDED BREAKOUT PENDING OPEN")
        out=assess_frozen_outcome(snap,row,candles((103,107,101,105)))
        self.assertEqual(out.assessment,"NEXT OPEN OUTSIDE PREDECLARED GAP")

    def test_following_bar_gap_through_stop_uses_open(self):
        snap,row=observation("stocks","ENTRY READY")
        out=assess_frozen_outcome(
            snap,row,candles((100,104,97,100),(90,95,89,91)),holding_bars=2,
        )
        self.assertEqual(out.exit_reason,"STOP GAP (OPEN)")
        self.assertLess(out.realized_r_proxy,-1)

    def test_cannot_backdate_or_reorder_observation_prices(self):
        snap,row=observation("crypto","BUY")
        with self.assertRaisesRegex(ValueError,"strictly later"):
            assess_frozen_outcome(snap,row,[CompletedBar(T,100,101,99,100)])
        with self.assertRaisesRegex(ValueError,"strictly later"):
            assess_frozen_outcome(snap,row,candles((100,102,99,101),(101,103,100,102))[::-1])

    def test_cannot_accept_invalid_ohlc_candle(self):
        snap,row=observation("stocks","ENTRY READY")
        with self.assertRaisesRegex(ValueError,"invalid future completed OHLC"):
            assess_frozen_outcome(snap,row,candles((100,98,99,100)))

    def test_snapshot_tampering_is_rejected(self):
        snap,row=observation("stocks","ENTRY READY")
        snap["rows"][0]["target"]=120
        with self.assertRaisesRegex(ValueError,"hash mismatch"):
            assess_frozen_outcome(snap,row,candles((100,111,99,110)))

    def test_cannot_inject_other_idea_into_saved_snapshot(self):
        snap,row=observation("crypto","BUY")
        altered=dict(row)
        altered["entry"]=99.0
        with self.assertRaisesRegex(ValueError,"not part of the frozen snapshot"):
            assess_frozen_outcome(snap,altered,candles((100,111,99,110)))

    def test_costs_are_not_silently_zero(self):
        snap,row=observation("crypto","BUY")
        result=assess_frozen_outcome(snap,row,candles((100,111,99,110)))
        self.assertLess(result.realized_r_proxy,2.0)


if __name__=="__main__":
    unittest.main()
