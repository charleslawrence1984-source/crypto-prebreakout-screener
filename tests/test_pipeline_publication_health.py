"""Pipeline publication health tests — synthetic timestamps and manifests."""
import json
import unittest
from datetime import datetime,timedelta,timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from pipeline_publication_health import (
    FEEDS,PublishedFeed,inspect_feed,audit,timestamp,
)

NOW=datetime(2026,10,17,16,30,tzinfo=timezone.utc)
CRYPTO=PublishedFeed("crypto","prepared_crypto/manifest.json",90)
MEME=PublishedFeed("meme","prepared_meme/manifest.json",120)
TRADE=PublishedFeed("trade_fundamentals","prepared_trade_fundamentals/manifest.json",5760,True)
INV=PublishedFeed("investment_priority","prepared_priority_investments/summary.json",5760)


def at(minutes=0):
    return (NOW-timedelta(minutes=minutes)).isoformat()


class PublishHealth(unittest.TestCase):
    def test_fresh_complete_crypto_is_current(self):
        info=inspect_feed(CRYPTO,{"updated_at":at(30),"status":"CURRENT",
            "deep_scores_current":60,"unique_eligible_coins":160},now=NOW)
        self.assertEqual(info["state"],"CURRENT")
        self.assertEqual(info["severity"],"OK")

    def test_successful_github_job_cannot_hide_stale_crypto(self):
        info=inspect_feed(CRYPTO,{"updated_at":at(240),"status":"CURRENT",
            "deep_scores_current":60,"unique_eligible_coins":160},now=NOW)
        self.assertEqual(info["state"],"STALE")
        self.assertEqual(info["severity"],"CRITICAL")

    def test_partial_meme_429_is_degraded_not_success(self):
        info=inspect_feed(MEME,{"updated_at":at(10),"status":"PARTIAL",
            "universe_tokens":2000,"errors":["Gecko API: HTTP 429"]},now=NOW)
        self.assertEqual(info["state"],"DEGRADED")
        self.assertTrue(any("provider" in issue for issue in info["issues"]))

    def test_missing_manifest_never_assumed_healthy(self):
        info=inspect_feed(CRYPTO,None,now=NOW)
        self.assertEqual(info["state"],"MISSING")

    def test_future_and_naive_dates_rejected(self):
        result=inspect_feed(CRYPTO,{"updated_at":at(-50),"status":"CURRENT"},now=NOW)
        self.assertEqual(result["state"],"FUTURE DATED")
        self.assertIsNone(timestamp("2026-10-17T10:00:00"))
        self.assertEqual(inspect_feed(MEME,{"updated_at":"not a date"},now=NOW)["state"],"INVALID TIMESTAMP")

    def test_missing_exchange_coverage_and_partial_market(self):
        doc={"updated_at":at(25),"exchanges":{"lse":{"status":"complete"},
             "tsx":{"status":"partial","coverage_pct":87.2}}}
        result=inspect_feed(TRADE,doc,now=NOW)
        self.assertEqual(result["state"],"DEGRADED")
        self.assertIn("tsx:partial"," | ".join(result["issues"]))
        self.assertEqual(inspect_feed(TRADE,{"updated_at":at(12)},now=NOW)["severity"],"WARNING")

    def test_priority_summary_uses_true_price_refreshed_timestamp(self):
        payload={"refreshed_at":at(23),"total":1,"rows":[{"Ticker":"ABC"}]}
        self.assertEqual(inspect_feed(INV,payload,now=NOW)["state"],"CURRENT")
        wrong={"refreshed_at":at(23),"total":500,"rows":[{"Ticker":"ABC"}]}
        self.assertEqual(inspect_feed(INV,wrong,now=NOW)["state"],"DEGRADED")

    def test_filesystem_audit_aggregates_and_fails_closed(self):
        with TemporaryDirectory() as root:
            directory=Path(root)/"prepared_crypto"
            directory.mkdir()
            (directory/"manifest.json").write_text(json.dumps({
                "updated_at":at(50),"status":"CURRENT","deep_scores_current":40,
                "unique_eligible_coins":120}),encoding="utf-8")
            report=audit(Path(root),now=NOW,definitions=[CRYPTO,MEME])
            self.assertEqual(report["overall"],"CRITICAL")
            self.assertEqual([r["state"] for r in report["datasets"]],["CURRENT","MISSING"])

    def test_naive_audit_clock_is_not_silently_converted(self):
        with self.assertRaisesRegex(ValueError,"timezone"):
            inspect_feed(MEME,{},now=NOW.replace(tzinfo=None))


if __name__=="__main__":
    unittest.main()
