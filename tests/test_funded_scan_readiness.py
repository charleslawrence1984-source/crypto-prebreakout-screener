"""Kraken funded scanner health is independent of hypothetical trading returns."""
from datetime import datetime, timedelta, timezone
import unittest

import pandas as pd

from funded_scan_readiness import audit_funded_stock_scan

NOW = datetime(2026,10,17,15,0,tzinfo=timezone.utc)
UNIVERSE = ("A","B","C")


def rows(*states, source="LIVE", age_minutes=3):
    result=[]
    for symbol,state in zip(UNIVERSE,states):
        result.append({
            "Ticker":symbol, "Scan source":source,
            "scanned_at":(NOW-timedelta(minutes=age_minutes)).isoformat(),
            "Technical state":state,
            "Funded status":(
                "⚪ DATA UNAVAILABLE" if state=="DATA UNAVAILABLE" else
                "🟡 FUNDED WATCH · VERIFY LIVE ENTRY" if state=="FUNDED BREAKOUT PENDING OPEN" else
                "⚪ NO CURRENT SETUP"
            ),
            "Gate":"NO VALID BREAKOUT" if state=="BLOCKED" else state,
        })
    return pd.DataFrame(result)


class StockScanReadinessTests(unittest.TestCase):
    def check(self,df):
        return audit_funded_stock_scan(df,UNIVERSE,now=NOW)

    def test_no_scan_is_not_no_opportunities(self):
        audit=self.check(None)
        self.assertEqual(audit.status,"NO LIVE EVIDENCE")
        self.assertEqual(audit.fresh_live_rows,0)

    def test_prepared_fallback_is_not_live_evidence(self):
        audit=self.check(rows("BLOCKED","BLOCKED","BLOCKED",source="PREPARED"))
        self.assertEqual(audit.status,"DATA INCOMPLETE / STALE")
        self.assertEqual(audit.fresh_live_rows,0)

    def test_completed_live_scan_with_no_setups_is_honest(self):
        audit=self.check(rows("BLOCKED","BLOCKED","BLOCKED"))
        self.assertEqual(audit.status,"NO QUALIFIED LIVE SETUP")
        self.assertEqual(audit.fresh_live_rows,3)
        self.assertEqual(audit.distinct_rejection_reasons,(("NO VALID BREAKOUT",3),))

    def test_missing_price_data_blocks_claim_of_clean_scan(self):
        audit=self.check(rows("BLOCKED","DATA UNAVAILABLE","BLOCKED"))
        self.assertEqual(audit.status,"DATA INCOMPLETE / STALE")
        self.assertEqual(audit.data_unavailable,1)

    def test_manual_quote_pending_is_not_a_trade(self):
        audit=self.check(rows("FUNDED BREAKOUT PENDING OPEN","BLOCKED","BLOCKED"))
        self.assertEqual(audit.status,"MANUAL EXECUTION VERIFICATION")
        self.assertEqual(audit.pending_live_quotes,1)
        self.assertEqual(audit.manual_review_ready_labels,0)

    def test_future_and_stale_scans_never_get_credit(self):
        self.assertEqual(self.check(rows("BLOCKED","BLOCKED","BLOCKED",age_minutes=25)).fresh_live_rows,0)
        self.assertEqual(self.check(rows("BLOCKED","BLOCKED","BLOCKED",age_minutes=-10)).fresh_live_rows,0)

    def test_missing_and_duplicate_tickers_fail_data_coverage(self):
        self.assertEqual(self.check(rows("BLOCKED","BLOCKED")).status,"DATA INCOMPLETE / STALE")
        frame=rows("BLOCKED","BLOCKED","BLOCKED")
        duplicate=pd.concat([frame,frame.iloc[[0]]],ignore_index=True)
        audit=self.check(duplicate)
        self.assertEqual(audit.status,"DATA INCOMPLETE / STALE")
        self.assertTrue(any("Duplicate" in x for x in audit.blockers))

    def test_ready_label_not_counted_if_not_fresh(self):
        frame=rows("FUNDED CORE ENTRY CHECK","BLOCKED","BLOCKED",source="PREPARED")
        frame.loc[0,"Funded status"]="🟢 FUNDED READY"
        audit=self.check(frame)
        self.assertEqual(audit.manual_review_ready_labels,0)
        self.assertTrue(any("READY label" in x for x in audit.blockers))

    def test_invalid_universe_or_timezone_fails_closed(self):
        with self.assertRaises(ValueError):
            audit_funded_stock_scan(pd.DataFrame(),("A","A"),now=NOW)
        with self.assertRaises(ValueError):
            audit_funded_stock_scan(pd.DataFrame(),UNIVERSE,now=NOW.replace(tzinfo=None))


if __name__=="__main__":
    unittest.main()
