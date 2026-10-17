"""Smoke-test the CL Signal homepage and segregated pension page."""
import unittest
from pathlib import Path

from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]


class ManagedPensionNavigation(unittest.TestCase):
    def test_home_and_pension_page_render_without_exceptions(self):
        app = AppTest.from_file(str(ROOT / "stock_app_v2.py"), default_timeout=30).run()
        self.assertEqual(len(app.exception), 0, str(app.exception))
        app.switch_page("managed_pension.py").run(timeout=30)
        self.assertEqual(len(app.exception), 0, str(app.exception))
        self.assertTrue(any("Managed Pension" in t.value for t in app.title))
        self.assertTrue(any("RESEARCH ONLY" in w.value for w in app.warning))
        self.assertFalse(any("Trade now" in b.label for b in app.button))


if __name__ == "__main__":
    unittest.main()
