"""Automated Investment health probes must fail closed on stopped pipelines."""
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from audit_investment_buy_surface_consistency import publication_feed_health


class InvestmentPublicationHealthTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 17, 19, 0, tzinfo=timezone.utc)
        self.fund = {"updated_at": (self.now - timedelta(days=2)).isoformat()}
        self.tech = {"updated_at": (self.now - timedelta(hours=2)).isoformat()}
        self.price = {"price_refreshed_at": (self.now - timedelta(hours=1)).isoformat()}

    def test_recent_feeds_pass_even_without_any_assumed_buy_count(self):
        result = publication_feed_health(self.fund, self.tech, self.price, self.now)
        self.assertEqual(set(result), {"fundamentals", "technicals", "prices"})
        self.assertEqual(result["technicals"]["max_age_hours"], 96)

    def test_stopped_technical_backfill_reports_failure(self):
        self.tech["updated_at"] = (self.now - timedelta(hours=97)).isoformat()
        with self.assertRaisesRegex(AssertionError, "technicals"):
            publication_feed_health(self.fund, self.tech, self.price, self.now)

    def test_stopped_price_refresh_reports_failure(self):
        self.price["price_refreshed_at"] = (self.now - timedelta(hours=97)).isoformat()
        with self.assertRaisesRegex(AssertionError, "prices"):
            publication_feed_health(self.fund, self.tech, self.price, self.now)

    def test_stopped_fundamental_scan_reports_failure(self):
        self.fund["updated_at"] = (self.now - timedelta(days=8)).isoformat()
        with self.assertRaisesRegex(AssertionError, "fundamentals"):
            publication_feed_health(self.fund, self.tech, self.price, self.now)

    def test_missing_and_future_timestamp_also_fail(self):
        self.price["price_refreshed_at"] = ""
        with self.assertRaisesRegex(AssertionError, "prices"):
            publication_feed_health(self.fund, self.tech, self.price, self.now)
        self.price["price_refreshed_at"] = (self.now + timedelta(days=2)).isoformat()
        with self.assertRaisesRegex(AssertionError, "prices"):
            publication_feed_health(self.fund, self.tech, self.price, self.now)

    def test_workflow_has_no_runtime_dependency_on_unmerged_branch(self):
        workflow = (Path(__file__).resolve().parents[1] /
                    ".github/workflows/investment-buy-surface-consistency.yml").read_text()
        self.assertIn('workflows: ["Investment technical backfill", "Investment priority price refresh"]', workflow)
        self.assertIn("github.event.workflow_run.conclusion", workflow)
        self.assertIn('ref: main', workflow)
        self.assertIn("actions/upload-artifact@v4", workflow)
        self.assertIn("cancel-in-progress: false", workflow)
        self.assertNotIn("audit/investment-buy-surface-consistency-20261017", workflow)


if __name__ == "__main__":
    unittest.main()
