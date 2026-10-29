"""Read-only staged Pension route and no-loss migration boundary tests."""
from __future__ import annotations
import ast
import unittest
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

ROOT=Path(__file__).resolve().parents[1]


class PensionNavigationPreviewTests(unittest.TestCase):
    def test_separate_named_pension_route_and_stocks_identity(self):
        src=(ROOT/"stock_app_v2.py").read_text(encoding="utf-8")
        self.assertIn('"pension_preview_page.py"',src)
        self.assertIn('url_path="pension"',src)
        self.assertIn('url_path="stocks"',src)
        self.assertIn("[home, stocks, pension, crypto, memes, kraken_funded]",src)

    def test_home_explicitly_calls_preview_not_claiming_migration(self):
        src=(ROOT/"platform_home.py").read_text(encoding="utf-8")
        self.assertIn('st.page_link(\n        "pension_preview_page.py"',src)
        self.assertIn("Pension Investment (Read-only Preview)",src)
        self.assertIn("Explore Stocks (current mixed page)",src)

    def test_pension_preview_never_imports_or_writes_trade_legacy_storage(self):
        src=(ROOT/"pension_preview_page.py").read_text(encoding="utf-8")
        tree=ast.parse(src)
        modules=[v.module for v in ast.walk(tree) if isinstance(v,ast.ImportFrom)]
        self.assertFalse(set(modules) & {
            "stock_app","stock_app_v2","stocks_page","two_strategy",
            "trade_rules","investment_live_readiness",
        })
        for token in ("LocalStorage(", "CookieController(", "transfer_pension(",
                      "st.session_state[", "place_order(", "open_broker(", "BUY CANDIDATE"):
            self.assertNotIn(token,src)
        self.assertIn("have not been moved",src)

    def test_real_readonly_pension_streamlit_render(self):
        page=AppTest.from_file(str(ROOT/"pension_preview_page.py"),default_timeout=30).run()
        self.assertFalse(page.exception,[str(v.message) for v in page.exception])
        self.assertTrue(any("Pension" in str(v.value) for v in page.title))
        self.assertGreaterEqual(len(page.info),1)
        self.assertIn("not been moved", (ROOT/"pension_preview_page.py").read_text(encoding="utf-8"))
        self.assertEqual(len(page.button),0)

if __name__=="__main__":
    unittest.main()
