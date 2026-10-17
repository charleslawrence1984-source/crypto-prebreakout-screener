"""Regression tests: source daily bars, not refresh run-times, control BUY freshness."""
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pandas as pd

import stock_app


def investment_row(ticker="AAA"):
    return {
        "Ticker": ticker,
        "Action": "BUY CANDIDATE",
        "Price": 15.0,
        "Valuation model version": stock_app.VALUATION_MODEL_VERSION,
        "Valuation FX status": "PASS",
        "Base intrinsic value": 40.0,
        "Bear intrinsic value": 30.0,
        "Required margin of safety %": 30.0,
        "Hard gates": "PASS",
        "Sector model": "Generic",
        "Quality score": 85.0,
    }


class SourceDailyCandleTests(unittest.TestCase):
    def test_last_valid_close_retains_its_true_date_for_each_listing(self):
        index = pd.to_datetime(["2026-10-14", "2026-10-16"])
        prices = pd.DataFrame(
            [[14.5, 30.0], [15.5, float("nan")]],
            index=index,
            columns=pd.MultiIndex.from_product([["Close"], ["AAA", "BBB"]]),
        )
        with patch.object(stock_app.yf, "download", return_value=prices):
            dated, errors = stock_app._batch_latest_prices(
                ["AAA", "BBB"], with_candle_dates=True,
            )
        self.assertEqual(errors, 0)
        self.assertEqual(dated["AAA"], {"price": 15.5, "candle_date": "2026-10-16"})
        self.assertEqual(dated["BBB"], {"price": 30.0, "candle_date": "2026-10-14"})
        # Prior fundamental scanner users must retain the unchanged float API.
        with patch.object(stock_app.yf, "download", return_value=prices):
            legacy, _ = stock_app._batch_latest_prices(["AAA", "BBB"])
        self.assertEqual(legacy, {"AAA": 15.5, "BBB": 30.0})

    def test_missing_invalid_or_old_candle_cannot_reissue_buy(self):
        row = investment_row()
        row["Price candle date"] = datetime.now(timezone.utc).date().isoformat()
        old = (datetime.now(timezone.utc) - timedelta(days=8)).date().isoformat()
        for quote in (None, {"price": 11.0, "candle_date": old},
                      {"price": 11.0, "candle_date": "invalid"}):
            with self.subTest(quote=quote):
                source = pd.DataFrame([row])
                with patch.object(stock_app, "_batch_latest_prices",
                                  return_value=({"AAA": quote} if quote else {}, 0)):
                    result, diag = stock_app.refresh_prepared_investment_prices(source)
                self.assertEqual(result.iloc[0]["Action"], "REVALUE")
                self.assertNotEqual(result.iloc[0]["Valuation gate"], "PASS")
                if quote is None:
                    self.assertEqual(result.iloc[0]["Price candle date"], "")
                    self.assertEqual(diag["missing"], 1)
                else:
                    self.assertEqual(diag["stale_candles"], 1)

    def test_fresh_bar_reprices_but_old_bar_for_another_ticker_does_not(self):
        today = datetime.now(timezone.utc).date().isoformat()
        old = (datetime.now(timezone.utc) - timedelta(days=12)).date().isoformat()
        batch = {"AAA": {"price": 12.0, "candle_date": today},
                 "BBB": {"price": 12.0, "candle_date": old}}
        with patch.object(stock_app, "_batch_latest_prices", return_value=(batch, 0)):
            output, diag = stock_app.refresh_prepared_investment_prices(
                pd.DataFrame([investment_row("AAA"), investment_row("BBB")]))
        self.assertEqual(list(output.set_index("Ticker").loc[["AAA", "BBB"], "Action"]),
                         ["BUY CANDIDATE", "REVALUE"])
        self.assertEqual(output.set_index("Ticker").loc["AAA", "Price candle date"], today)
        self.assertEqual(output.set_index("Ticker").loc["BBB", "Price candle date"], old)
        self.assertEqual(diag["updated"], 1)
        self.assertEqual(diag["stale_candles"], 1)

    def test_priority_summary_preserves_exact_per_listing_bar_date(self):
        workflow = (Path(__file__).resolve().parents[1] /
                    ".github/workflows/investment-priority-price-refresh.yml").read_text()
        self.assertIn('"Price candle date"', workflow)
        refresh = (Path(__file__).resolve().parents[1] /
                   "scheduled_investment_price_refresh.py").read_text()
        self.assertIn("price_refresh_stale_candles", refresh)


if __name__ == "__main__":
    unittest.main()
