"""Diagnostic ONLY: locate full Stocks Streamlit AppTest slow path safely.

A passing exit code indicates diagnostic completion, NOT successful full UI
rendering. The production pages, investment strategy and source files are
untouched. This is bounded to 26 seconds and does not hit Yahoo providers.
"""
from __future__ import annotations

import faulthandler
import sys
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import yfinance as yf
from streamlit.testing.v1 import AppTest


def main():
    faulthandler.enable()
    faulthandler.dump_traceback_later(11, repeat=False, file=sys.stderr)
    try:
        with (
            patch("streamlit.page_link", lambda *args, **kwargs: None),
            patch.object(yf, "download", return_value=pd.DataFrame()),
            patch.object(yf, "Ticker", side_effect=RuntimeError("Offline diagnostic")),
        ):
            ui = AppTest.from_file(str(Path("stock_app.py").resolve()), default_timeout=26).run()
        exceptions = [str(exc.message) for exc in ui.exception]
        print({"status": "APPTEST_RETURNED", "tabs": len(ui.tabs),
               "exceptions": exceptions[:8]}, flush=True)
        # Any discovered exceptions should inform a separate UI repair.
    except Exception as exc:
        print({"status": "APPTEST_DID_NOT_FINISH", "error_type": type(exc).__name__,
               "details": str(exc)[:250]}, flush=True)
    finally:
        faulthandler.cancel_dump_traceback_later()


if __name__ == "__main__":
    main()
