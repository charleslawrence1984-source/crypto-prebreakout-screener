import unittest
from continuous_investment_backfill import may_continue


class ContinueTests(unittest.TestCase):
    def test_progress_and_provider_gaps(self):
        before = {"gettex": {"coverage_symbols": 10}}
        after = {"gettex": {"coverage_symbols": 20, "remaining_symbols": 30},
                 "madrid": {"status": "failed", "error": "RuntimeError: scan returned no results: {'price_failures': 1}"}}
        self.assertTrue(may_continue(before, after, 1))
        self.assertFalse(may_continue(after, after, 1))
        after["madrid"]["error"] = "RuntimeError: scan returned no results: {'rate_limit_errors': 1}"
        self.assertFalse(may_continue(before, after, 1))
        after["madrid"]["error"] = "ConnectionResetError: network interrupted"
        self.assertFalse(may_continue(before, after, 1))

    def test_completion_and_timeout(self):
        before = {"x": {"coverage_symbols": 10}}
        after = {"x": {"coverage_symbols": 20, "remaining_symbols": 0}}
        self.assertFalse(may_continue(before, after, 0))
        after["x"]["remaining_symbols"] = 2
        self.assertFalse(may_continue(before, after, 124))
        after["x"]["diagnostics"] = {"other_errors": 2}
        self.assertFalse(may_continue(before, after, 1))


if __name__ == "__main__":
    unittest.main()
