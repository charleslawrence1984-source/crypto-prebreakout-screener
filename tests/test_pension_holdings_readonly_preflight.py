"""Dry-run-only historical browser holdings partition; no data migration."""
import copy
import json
import unittest

from pension_holdings_readonly_preflight import readonly_partition_preflight


class HoldingsPartitionSafetyTests(unittest.TestCase):
    def test_two_strategies_can_hold_same_ticker_without_merging(self):
        original=[
            {"Ticker":"AAPL","Position type":"TRADE","Shares":2,"Average cost":110},
            {"Ticker":"AAPL","Position type":"INVESTMENT","Shares":3,"Average cost":180},
        ]
        saved=copy.deepcopy(original)
        out=readonly_partition_preflight(original)
        self.assertEqual(out["status"],"PREVIEW_PARTITION_COMPLETE_NOT_APPLIED")
        self.assertEqual(len(out["trade_rows"]),1)
        self.assertEqual(len(out["investment_rows"]),1)
        self.assertEqual(out["raw_rows"],2)
        self.assertEqual(original,saved)
        self.assertEqual(out["trade_rows"][0]["original"],original[0])
        self.assertEqual(out["investment_rows"][0]["original"],original[1])
        self.assertFalse(out["migration_performed"])

    def test_duplicate_same_strategy_rows_never_silently_consolidated(self):
        rows=[{"Ticker":"AAPL","Position type":"INVESTMENT","Shares":1},
              {"Ticker":"AAPL","Position type":"INVESTMENT","Shares":4}]
        out=readonly_partition_preflight(rows)
        self.assertEqual(len(out["investment_rows"]),2)
        self.assertEqual([x["original_row_number"] for x in out["investment_rows"]],[0,1])

    def test_missing_legacy_type_defaults_investment_but_blocks_autorelease(self):
        rows=[{"Ticker":"AZN.L","Shares":1,"Average cost":100}]
        out=readonly_partition_preflight(rows)
        self.assertEqual(len(out["investment_rows"]),1)
        self.assertEqual(out["status"],"BLOCKED_MANUAL_REVIEW_REQUIRED")
        self.assertTrue(any("MISSING_POSITION_TYPE" in x for x in out["review_flags"]))

    def test_unknown_position_type_is_blocked_not_reassigned(self):
        result=readonly_partition_preflight([{"Ticker":"AAPL","Position type":"CRYPTO"}])
        self.assertEqual(result["status"],"BLOCKED_UNASSIGNED_LEGACY_POSITION")
        self.assertEqual(len(result["investment_rows"]),0)

    def test_unreadable_or_structurally_wrong_json_never_migrates(self):
        for payload in ("{invalid",{"Ticker":"AAPL"},[None]):
            with self.subTest(payload=payload):
                result=readonly_partition_preflight(payload)
                self.assertTrue(result["status"].startswith("BLOCKED"))
                self.assertFalse(result["migration_performed"])

    def test_ordinal_preserved_and_deepcopy_is_independent(self):
        rows=[{"Ticker":"MSFT","Position type":"TRADE","Shares":2,"Average cost":99,
               "Custom note":"do not drop"}]
        result=readonly_partition_preflight(json.dumps(rows))
        result["trade_rows"][0]["original"]["Shares"]=999
        self.assertEqual(rows[0]["Shares"],2)
        self.assertIn("Custom note",result["trade_rows"][0]["original"])
        self.assertEqual(len(result["original_sha256"]),64)

    def test_blank_ticker_with_quantity_blocks_automatic_migration(self):
        report=readonly_partition_preflight([
            {"Ticker":"","Position type":"INVESTMENT","Shares":1}
        ])
        self.assertEqual(report["status"],"BLOCKED_MANUAL_REVIEW_REQUIRED")
        self.assertIn("POSITION_WITHOUT_TICKER_REVIEW:0",report["review_flags"])

if __name__=="__main__":
    unittest.main()
