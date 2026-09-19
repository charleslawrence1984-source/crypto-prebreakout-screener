import unittest

import pandas as pd

from scheduled_trade_technicals import is_fundamentally_eligible, reconcile_cached


class ScheduledTradeTechnicalsTests(unittest.TestCase):
    def test_only_clean_score_65_plus_is_eligible(self):
        self.assertTrue(is_fundamentally_eligible({"fundamental_score": 65, "fundamental_failures": []}))
        self.assertFalse(is_fundamentally_eligible({"fundamental_score": 64.99, "fundamental_failures": []}))
        self.assertFalse(is_fundamentally_eligible({"fundamental_score": 90, "fundamental_failures": ["FCF HARD GATE FAILED"]}))

    def test_reconcile_removes_companies_that_later_become_ineligible(self):
        frame = pd.DataFrame([{"Ticker": "KEEP", "Technical score": 80}, {"Ticker": "DROP", "Technical score": 90}])
        result = reconcile_cached(frame, {"KEEP"})
        self.assertEqual(result["Ticker"].tolist(), ["KEEP"])


if __name__ == "__main__":
    unittest.main()
