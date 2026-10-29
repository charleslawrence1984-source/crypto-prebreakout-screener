"""Automated actual Streamlit AppTest decision-card and page smoke tests.

Synthetic fixture is not a real broker/browser scenario. Offline source stubs
are never used to claim real TradingView/Yahoo/PIT financial freshness.
"""
from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import yfinance as yf
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]


class StorageStub:
    def __init__(self, *args, **kwargs):
        pass
    def getItem(self, *args, **kwargs):
        return None
    def setItem(self, *args, **kwargs):
        return None
    def get(self, *args, **kwargs):
        return None
    def set(self, *args, **kwargs):
        return None


class RenderedTradeActionTests(unittest.TestCase):
    def cards(self, app):
        return [m.value for m in app.markdown
                if "Quick Analyse TRADE" in m.value
                or "Direct ticker TRADE" in m.value
                or "Ranked TRADE" in m.value]

    def assert_actions(self, app, desired):
        self.assertFalse(app.exception, [str(e.message) for e in app.exception])
        cards = self.cards(app)
        self.assertEqual(len(cards), 3, "Expected the real three decision-card renderings")
        for html in cards:
            self.assertIn(desired, html)
            self.assertNotIn("🟢 BUY", html)
            self.assertNotIn(">BUY<", html)

    def test_three_actual_rendered_trade_cards_never_emit_legacy_buy(self):
        # The imported stock_app executes its real page when loaded.
        # Mock its unrelated navigation/browser APIs, not action rendering.
        with (
            patch("streamlit.page_link", lambda *args, **kwargs: None),
            patch("streamlit_local_storage.LocalStorage", StorageStub),
            patch("streamlit_cookies_controller.CookieController", StorageStub),
            patch.object(yf, "download", return_value=pd.DataFrame()),
            patch.object(yf, "Ticker", side_effect=RuntimeError("offline source fixture")),
        ):
            app = AppTest.from_file(
                str(ROOT / "tests/stocks_trade_ui_fixture_app.py"), default_timeout=45,
            ).run()
            self.assert_actions(app, "WAIT")
            cases = (
                ("Claimed source status, missing receipt", "WAIT"),
                ("All claimed receipts (still manual review)", "READY TO VERIFY"),
                ("Approved engine BLOCKED", "WAIT"),
            )
            for label, action in cases:
                with self.subTest(label=label):
                    app.radio[0].set_value(label).run()
                    self.assert_actions(app, action)
        self.assertIn("Fixture-only display", " ".join(m.value for m in app.caption))

    def test_offline_real_stocks_base_script_renders_without_ui_exceptions(self):
        with (
            patch("streamlit.page_link", lambda *args, **kwargs: None),
            patch("streamlit_local_storage.LocalStorage", StorageStub),
            patch("streamlit_cookies_controller.CookieController", StorageStub),
            patch.object(yf, "download", return_value=pd.DataFrame()),
            patch.object(yf, "Ticker", side_effect=RuntimeError("offline UI smoke fixture")),
        ):
            app = AppTest.from_file(str(ROOT / "stock_app.py"), default_timeout=45).run()
        self.assertFalse(app.exception, [str(e.message) for e in app.exception])
        self.assertGreaterEqual(len(app.tabs), 7)


if __name__ == "__main__":
    unittest.main()
