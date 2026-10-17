"""Regression checks for the EXISTING Kraken Funded account-safety contract.

These tests intentionally extract existing functions/values from the source
without importing the live Streamlit app or querying Kraken. A passing test
does not authorize real-money trades or prove fill/slippage safety.
"""
import ast
import math
import pathlib
import types
import unittest

SOURCE = pathlib.Path(__file__).resolve().parents[1] / "kraken_funded.py"


class FakeNP:
    nan = float("nan")
    isfinite = staticmethod(math.isfinite)


def load_contract():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    keys = {
        "FUNDED_START_BALANCE", "FUNDED_TARGET_BALANCE", "FUNDED_FAIL_BALANCE",
        "FUNDED_STANDARD_RISK_USD", "FUNDED_REDUCED_RISK_USD",
        "FUNDED_MAX_TOTAL_OPEN_RISK_USD",
    }
    values = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in keys:
                    values[target.id] = ast.literal_eval(node.value)
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id in keys:
            values[node.target.id] = ast.literal_eval(node.value)
    funcs = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in {"safe_number", "_account_guard"}]
    assert len(funcs) == 2, "Kraken funded contract functions changed; audit required"
    return values, funcs


class FundedRiskGate(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        vals, funcs = load_contract()
        cls.values = vals
        cls.functions = funcs

    def guard(self, equity=1000.0, other=0.0, risk=4.0, verified=True):
        context = dict(self.values)
        context["np"] = FakeNP
        context["st"] = types.SimpleNamespace(session_state={
            "funded_risk_verified": verified,
            "funded_account_equity": equity,
            "funded_other_open_risk": other,
        })
        compiled = compile(ast.Module(body=self.functions, type_ignores=[]), str(SOURCE), "exec")
        exec(compiled, context)
        return context["_account_guard"](risk)

    def test_1000_tier_has_fixed_12_percent_goal_and_3_percent_floor(self):
        self.assertEqual(self.values["FUNDED_START_BALANCE"], 1000)
        self.assertEqual(self.values["FUNDED_TARGET_BALANCE"], 1120)
        self.assertEqual(self.values["FUNDED_FAIL_BALANCE"], 970)

    def test_account_verification_is_mandatory(self):
        self.assertIn("VERIFY ACCOUNT", self.guard(verified=False))

    def test_normal_verified_small_risk_is_not_blocked(self):
        self.assertEqual(self.guard(), "")

    def test_unverified_or_insufficient_safety_buffer_blocks(self):
        self.assertIn("ACCOUNT RISK LIMIT", self.guard(equity=975.0))
        self.assertIn("ACCOUNT RISK LIMIT", self.guard(equity=980.0))
        self.assertIn("ACCOUNT RISK LIMIT", self.guard(equity=float("nan")))

    def test_existing_open_exposure_counts_toward_limit(self):
        self.assertIn("ACCOUNT RISK LIMIT", self.guard(other=8, risk=4))
        self.assertEqual(self.guard(other=6, risk=4), "")

    def test_missing_or_invalid_open_risk_blocks(self):
        self.assertIn("ACCOUNT RISK LIMIT", self.guard(other=-1))
        self.assertIn("ACCOUNT RISK LIMIT", self.guard(other=float("nan")))


if __name__ == "__main__":
    unittest.main()
