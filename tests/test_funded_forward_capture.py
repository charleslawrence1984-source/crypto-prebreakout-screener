"""Prospective archive contract tests — synthetic only, no web/broker calls."""
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest

from funded_forward_capture import (
    RESEARCH_ONLY, create_snapshot, funded_universe, write_snapshot_once,
)

NOW=datetime(2026,10,17,16,35,tzinfo=timezone.utc)
ASSETS={"stocks":("NVDA","AAPL"),"crypto":("BTC","ETH")}


class ProspectiveCapture(unittest.TestCase):
    def test_exact_original_universe_is_present_and_distinct(self):
        mapping=funded_universe()
        self.assertEqual(len(mapping["stocks"]),30)
        self.assertEqual(len(mapping["crypto"]),33)
        self.assertIn("NVDA",mapping["stocks"])
        self.assertIn("BTC",mapping["crypto"])

    def test_both_asset_classes_share_no_broker_order_fields(self):
        for kind,asset in (("stocks","NVDA"),("crypto","BTC")):
            result=create_snapshot(kind,[
                {"asset":asset,"status":"WATCH","reason":"Potential setup",
                 "source":"synthetic historical proxy","score":81.0,
                 "entry":100.0,"stop":95.0,"target":120.0},
            ],observed_at=NOW,run_id="trial-1",universe=ASSETS)
            self.assertEqual(result["expected_universe"],2)
            self.assertEqual(result["missing_rows"],1)
            self.assertEqual(result["rows"][1]["status"],"DATA UNAVAILABLE")
            self.assertTrue(result["research_only"])
            self.assertFalse(result["broker_account_connected"])
            self.assertTrue(all(x["trade_authorization"]==RESEARCH_ONLY for x in result["rows"]))
            self.assertEqual(len(result["payload_sha256"]),64)

    def test_provider_failure_cannot_be_reported_as_no_trade_signal(self):
        snap=create_snapshot("stocks",[
            {"asset":"NVDA","status":"SCAN ERROR","reason":"connection failed"},
        ],observed_at=NOW,run_id="trial-1",universe=ASSETS)
        self.assertEqual(snap["rows"][0]["status"],"SCAN ERROR")
        self.assertEqual(snap["rows"][1]["status"],"DATA UNAVAILABLE")

    def test_no_backdating_naive_or_non_utc_timestamp(self):
        with self.assertRaisesRegex(ValueError,"timezone"):
            create_snapshot("crypto",[],observed_at=NOW.replace(tzinfo=None),
                            run_id="trial",universe=ASSETS)
        from datetime import timedelta
        offset=timezone(timedelta(hours=1))
        with self.assertRaisesRegex(ValueError,"UTC"):
            create_snapshot("crypto",[],observed_at=NOW.astimezone(offset),
                            run_id="trial",universe=ASSETS)

    def test_unknown_and_duplicate_tickers_fail_closed(self):
        with self.assertRaisesRegex(ValueError,"unapproved"):
            create_snapshot("stocks",[{"asset":"UNLISTED","status":"WATCH"}],
                            observed_at=NOW,run_id="trial",universe=ASSETS)
        with self.assertRaisesRegex(ValueError,"duplicate"):
            create_snapshot("stocks",[{"asset":"AAPL","status":"WATCH"}]*2,
                            observed_at=NOW,run_id="trial",universe=ASSETS)

    def test_forbidden_broker_state_never_saved_to_automatic_research(self):
        with self.assertRaisesRegex(ValueError,"forbidden broker"):
            create_snapshot("crypto",[
                {"asset":"BTC","status":"READY","broker_order":"BUY BTC"},
            ],observed_at=NOW,run_id="trial",universe=ASSETS)

    def test_null_nonfinite_numeric_research_values_are_not_fake_zero(self):
        result=create_snapshot("stocks",[
            {"asset":"NVDA","status":"WATCH","score":float("nan"),
             "entry":float("inf"),"meta":{"x":float("nan")}},
        ],observed_at=NOW,run_id="trial",universe=ASSETS)
        self.assertIsNone(result["rows"][0]["entry"])
        self.assertIsNone(result["rows"][0]["score"])
        self.assertIsNone(result["rows"][0]["meta"]["x"])
        json.dumps(result,allow_nan=False)

    def test_exclusive_creation_prevents_rewriting_history(self):
        result=create_snapshot("crypto",[],observed_at=NOW,run_id="trial",universe=ASSETS)
        with TemporaryDirectory() as tmp:
            dest=Path(tmp)/"observations.json"
            write_snapshot_once(dest,result)
            first=dest.read_bytes()
            with self.assertRaises(FileExistsError):
                write_snapshot_once(dest,result)
            self.assertEqual(dest.read_bytes(),first)


if __name__=="__main__":
    unittest.main()
