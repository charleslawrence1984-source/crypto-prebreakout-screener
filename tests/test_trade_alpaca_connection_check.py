"""Standalone Alpaca free PAPER account connection test: no real credentials."""
from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone

from trade_alpaca_connection_check import check_free_paper_source

NOW = datetime(2026, 10, 29, 16, 5, tzinfo=timezone.utc)
ENV = {
    "ALPACA_KEY_ID": "FAKE-PAPER-ID",
    "ALPACA_SECRET_KEY": "FAKE-PAPER-SECRET",
    "ALPACA_PERSONAL_RESEARCH_RIGHTS_CONFIRMED": "1",
}


class FakeReply:
    def __init__(self, status, body):
        self.status = status
        self.body = json.dumps(body).encode()
    def __enter__(self):
        return self
    def __exit__(self, *unused):
        return False
    def read(self, length=None):
        return self.body if length is None else self.body[:length]


class FakeOpener:
    def __init__(self, account_status=200, bars_status=200, bars=None):
        self.calls = []
        self.account_status = account_status
        self.bars_status = bars_status
        self.bars = bars if bars is not None else [{
            "t": f"2026-10-{day:02d}T04:00:00Z",
            "o": 100.0, "h": 102.0, "l": 99.0,
            "c": 100.5, "v": 10000,
        } for day in (21, 22, 23, 26, 27, 28)]
    def __call__(self, req, timeout):
        self.calls.append((req, timeout))
        if "paper-api.alpaca.markets" in req.full_url:
            return FakeReply(self.account_status, {"id": "PAPER-FAKE-ID"})
        return FakeReply(self.bars_status, {"bars": self.bars, "next_page_token": None})


class FreeConnectionTests(unittest.TestCase):
    def test_no_credentials_never_calls_an_endpoint(self):
        fn = FakeOpener()
        result = check_free_paper_source(NOW, environ={}, opener=fn)
        self.assertEqual(result["status"], "BLOCKED_MISSING_PAPER_API_CREDENTIALS")
        self.assertEqual(fn.calls, [])

    def test_no_owner_permission_never_uses_paper_keys(self):
        fn = FakeOpener()
        env = dict(ENV, ALPACA_PERSONAL_RESEARCH_RIGHTS_CONFIRMED="0")
        result = check_free_paper_source(NOW, environ=env, opener=fn)
        self.assertEqual(result["status"], "BLOCKED_OWNER_RESEARCH_PERMISSION_NOT_CONFIRMED")
        self.assertEqual(fn.calls, [])

    def test_verified_paper_account_then_delayed_sip_bar_metadata_only(self):
        fn = FakeOpener()
        result = check_free_paper_source(NOW, environ=ENV, opener=fn)
        self.assertEqual(result["status"], "PAPER_CREDENTIALS_AUTHENTICATED_SOURCE_ONLY")
        self.assertTrue(result["paper_account_verified"])
        self.assertEqual(result["historical_bars_observed"], 6)
        self.assertEqual(len(fn.calls), 2)
        self.assertEqual(fn.calls[0][0].get_method(), "GET")
        self.assertEqual(fn.calls[1][0].get_method(), "GET")
        self.assertIn("https://paper-api.alpaca.markets/v2/account", fn.calls[0][0].full_url)
        self.assertIn("feed=sip", fn.calls[1][0].full_url)
        self.assertIn("adjustment=raw", fn.calls[1][0].full_url)
        self.assertEqual(result["price_rows_exported"], 0)
        self.assertFalse(result["exchange_prices_certified"])
        self.assertFalse(result["approved_trade_signal"])
        self.assertFalse(result["live_order_placed"])
        self.assertFalse(result["paper_order_placed"])
        self.assertNotIn("FAKE-PAPER-SECRET", str(result))
        self.assertNotIn("buying_power", str(result))

    def test_live_api_key_must_not_continue_if_paper_account_fails(self):
        fn = FakeOpener(account_status=403)
        result = check_free_paper_source(NOW, environ=ENV, opener=fn)
        self.assertEqual(result["status"], "BLOCKED_NOT_VERIFIED_AS_PAPER_KEYS")
        self.assertEqual(len(fn.calls), 1)
        self.assertFalse(result["live_order_placed"])

    def test_invalid_bar_geometry_never_looks_authenticated(self):
        fn = FakeOpener(bars=[{
            "t": "2026-10-28T04:00:00Z",
            "o": 100.0, "h": 90.0, "l": 95.0, "c": 101.0, "v": 10,
        }])
        result = check_free_paper_source(NOW, environ=ENV, opener=fn)
        self.assertEqual(result["status"], "BLOCKED_INVALID_PROVIDER_BAR_SCHEMA")
        self.assertFalse(result["approved_trade_signal"])

    def test_near_live_daily_bar_is_rejected(self):
        fn = FakeOpener(bars=[{
            "t": "2026-10-29T16:02:00Z",
            "o": 100.0, "h": 101.0, "l": 99.0, "c": 100.5, "v": 10,
        }])
        result = check_free_paper_source(NOW, environ=ENV, opener=fn)
        self.assertEqual(result["status"], "BLOCKED_INVALID_PROVIDER_BAR_SCHEMA")


if __name__ == "__main__":
    unittest.main()
