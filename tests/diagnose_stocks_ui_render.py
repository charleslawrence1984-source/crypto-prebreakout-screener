"""Assert the complete Stocks Streamlit script renders in offline AppTest.

Real widget tree is exercised (all page/tab blocks). Navigation, JS browser
storage and Yahoo network are mocked ONLY within this CI harness. Passing
is evidence of scripted rendering, not a real-browser interaction/persistence
test. Production source, strategy and published research remain unchanged.
"""
from __future__ import annotations

import faulthandler
import sys
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import yfinance as yf
from streamlit.testing.v1 import AppTest


class BrowserStorageStub:
    """The AppTest harness has no browser JS; preserve session-state logic."""

    def __init__(self, *args, **kwargs):
        pass

    def getItem(self, key):
        return None

    def setItem(self, key, value):
        return None

    def get(self, key):
        return None

    def set(self, key, value, **kwargs):
        return None


def main():
    faulthandler.enable()
    faulthandler.dump_traceback_later(11, repeat=False, file=sys.stderr)
    try:
        with (
            patch("streamlit.page_link", lambda *args, **kwargs: None),
            patch("streamlit_local_storage.LocalStorage", BrowserStorageStub),
            patch("streamlit_cookies_controller.CookieController", BrowserStorageStub),
            patch.object(yf, "download", return_value=pd.DataFrame()),
            patch.object(yf, "Ticker", side_effect=RuntimeError("Offline diagnostic")),
        ):
            ui = AppTest.from_file(str(Path("stock_app.py").resolve()), default_timeout=26).run()
        exceptions = [str(exc.message) for exc in ui.exception]
        print({"status": "STOCKS_UI_RENDERED", "tabs": len(ui.tabs),
               "exceptions": exceptions[:8]}, flush=True)
        assert len(ui.tabs) >= 7, "Stocks script did not render its 7 main navigation tabs"
        assert not exceptions, "Stocks UI emitted uncaught Streamlit rendering exceptions"
    finally:
        faulthandler.cancel_dump_traceback_later()


if __name__ == "__main__":
    main()
