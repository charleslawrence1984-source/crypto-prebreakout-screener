"""Regression tests for Kraken Funded safety decisions, without importing Streamlit."""
import ast
from datetime import datetime, timezone, timedelta
import math
from pathlib import Path
from types import SimpleNamespace
import unittest


class FakePandas:
    class Timestamp:
        @staticmethod
        def now(tz=None):
            return datetime.now(timezone.utc)

    @staticmethod
    def to_datetime(value, errors=None, utc=None):
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def isna(value):
        return value is None


def load_rules():
    """Compile the actual production functions, isolating them from its UI side effects."""
    source = Path(__file__).resolve().parents[1] / "kraken_funded.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    names = {"safe_number", "_as_bool", "_account_guard",
             "funded_crypto_status", "funded_stock_status", "funded_position_plan"}
    functions = [node for node in tree.body
                 if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                 and node.name in names]
    assert {node.name for node in functions} == names
    fake_st = SimpleNamespace(session_state={
        "funded_risk_verified": True, "funded_account_equity": 1000.0,
        "funded_other_open_risk": 0.0, "funded_verified_earnings": ["NVDA"],
    })
    ns = {
        "np": SimpleNamespace(isfinite=math.isfinite, bool_=bool, nan=math.nan),
        "pd": FakePandas, "st": fake_st,
        "FUNDED_START_BALANCE": 1000.0, "FUNDED_FAIL_BALANCE": 970.0,
        "FUNDED_MAX_TOTAL_OPEN_RISK_USD": 10.0,
        "FUNDED_MAX_POSITION_PCT": 35.0,
        "FUNDED_STANDARD_RISK_USD": 4.0,
        "FUNDED_REDUCED_RISK_USD": 3.0,
        "FUNDED_CRYPTO_READY_SCORE": 80.0,
        "FUNDED_READY_MIN_RR": 2.0,
        "FUNDED_A_PLUS_MIN_RR": 2.5,
    }
    module = ast.Module(
        body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0)] + functions,
        type_ignores=[],
    )
    exec(compile(ast.fix_missing_locations(module), str(source), "exec"), ns)
    return ns


class FundedRulesTests(unittest.TestCase):
    def setUp(self):
        self.ns = load_rules()

    def crypto(self, **changes):
        row = {
            "Coin": "ETH", "CL Signal": "🟢 READY / BUY",
            "Entry timing": "RETEST", "Entry timing actionable": True,
            "Swing eligible": True, "Execution available": True,
            "Execution liquidity pass": True, "RS vs BTC %": 1.5,
            "Score": 90, "Funded entry": 100, "Funded stop": 95,
            "Funded target": 115, "Funded R:R": 3,
            "deep_scored_at": datetime.now(timezone.utc).isoformat(),
        }
        row.update(changes)
        return self.ns["funded_crypto_status"](row)

    def stock(self, **changes):
        row = {
            "Ticker": "NVDA", "Technical state": "ENTRY READY",
            "Entry": 100, "Stop": 95, "Target": 115, "R:R": 3,
            "MACD progress": "CONFIRMED", "RSI": 55,
            "Median traded value GBPm": 5,
            "Earnings check": "OUTSIDE 5 SESSIONS (UNVERIFIED)",
            "Scan source": "LIVE",
            "scanned_at": datetime.now(timezone.utc).isoformat(),
        }
        row.update(changes)
        return self.ns["funded_stock_status"](row)

    def test_complete_crypto_signal_is_ready(self):
        self.assertEqual(self.crypto(), "🟢 A+ FUNDED READY")

    def test_crypto_watch_never_upgrades_on_score_alone(self):
        self.assertEqual(self.crypto(**{"CL Signal": "🟡 WATCH"}),
                         "🟡 FUNDED WATCH · CORE CONFIRMATION")

    def test_crypto_requires_original_eligibility(self):
        self.assertEqual(self.crypto(**{"Swing eligible": False}),
                         "🟡 FUNDED WATCH · SETUP NOT ELIGIBLE")

    def test_crypto_rejects_weak_execution(self):
        self.assertEqual(self.crypto(**{"Execution liquidity pass": False}),
                         "🔴 FUNDED PASS · EXECUTION")

    def test_crypto_rejects_string_false_execution(self):
        self.assertEqual(self.crypto(**{"Execution available": "False"}),
                         "🔴 FUNDED PASS · EXECUTION")

    def test_crypto_rejects_stale_data(self):
        age = datetime.now(timezone.utc) - timedelta(hours=12)
        self.assertEqual(self.crypto(deep_scored_at=age.isoformat()),
                         "🟡 FUNDED WATCH · REFRESH SIGNAL")

    def test_crypto_rejects_no_relative_strength(self):
        self.assertEqual(self.crypto(**{"RS vs BTC %": -1}),
                         "🟡 FUNDED WATCH · RELATIVE STRENGTH")

    def test_crypto_requires_consistent_target(self):
        self.assertEqual(self.crypto(**{"Funded target": 101}),
                         "🟡 FUNDED WATCH · R:R")

    def test_pre_cross_is_visible_not_trade_ready(self):
        self.assertEqual(self.stock(**{"Technical state": "PRE-CROSS READY"}),
                         "🟡 FUNDED WATCH · PRE-CROSS")

    def test_compression_discovery_is_watch_only(self):
        self.assertEqual(self.stock(**{"Technical state": "FUNDED TREND WATCH"}),
                         "🟡 FUNDED WATCH · COMPRESSION")

    def test_daily_stock_earnings_window_veto(self):
        self.assertEqual(self.stock(**{"Earnings check": "WITHIN 5 SESSIONS"}),
                         "🔴 FUNDED PASS · EARNINGS WINDOW")

    def test_stock_requires_official_earnings_confirmation(self):
        self.ns["st"].session_state["funded_verified_earnings"] = []
        self.assertEqual(self.stock(), "🟡 FUNDED WATCH · VERIFY EARNINGS")

    def test_stock_requires_live_fresh_scan(self):
        self.assertEqual(self.stock(**{"Scan source": "PREPARED"}),
                         "🟡 FUNDED WATCH · RUN LIVE SCAN")

    def test_no_automatic_ready_without_account_confirmation(self):
        self.ns["st"].session_state["funded_risk_verified"] = False
        self.assertEqual(self.crypto(), "🟡 FUNDED WATCH · VERIFY ACCOUNT")
        self.assertEqual(self.stock(), "🟡 FUNDED WATCH · VERIFY ACCOUNT")

    def test_open_risk_is_enforced(self):
        self.ns["st"].session_state["funded_other_open_risk"] = 8.0
        self.assertEqual(self.crypto(), "🔴 FUNDED PASS · ACCOUNT RISK LIMIT")
        self.assertTrue(math.isnan(self.ns["funded_position_plan"](100, 95, 4)[1]))

    def test_drawdown_buffer_is_enforced(self):
        self.ns["st"].session_state["funded_account_equity"] = 981.0
        self.assertEqual(self.stock(), "🔴 FUNDED PASS · ACCOUNT RISK LIMIT")

    def test_position_notional_is_capped(self):
        _, notional, risk = self.ns["funded_position_plan"](100, 99.5, 4)
        self.assertLessEqual(notional, 350)
        self.assertLessEqual(risk, 4)


if __name__ == "__main__":
    unittest.main()
