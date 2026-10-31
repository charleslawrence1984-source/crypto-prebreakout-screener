"""Offline independent-connection SQLite transactional fencing checks."""
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor

from research.funded_sqlite_lease_poc import SQLiteLeaseStore


class SQLiteLeaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = os.path.join(self.temp.name, "lease.sqlite")

    def store(self):
        return SQLiteLeaseStore(self.path)

    def test_independent_connections_have_one_winner(self):
        self.store()
        with ThreadPoolExecutor(max_workers=8) as pool:
            claims = list(pool.map(lambda n: self.store().claim("bar", str(n), 100, 20), range(32)))
        winners = [x for x in claims if x is not None]
        self.assertEqual(len(winners), 1)
        self.assertTrue(self.store().publish(winners[0], 101))
        self.assertFalse(self.store().publish(winners[0], 102))

    def test_expiry_requires_explicit_recovery_and_fences_old_owner(self):
        a = self.store().claim("bar", "a", 100, 10)
        self.assertIsNone(self.store().claim("bar", "b", 111, 10))
        self.assertFalse(self.store().recover(a, 109))
        self.assertTrue(self.store().recover(a, 110))
        b = self.store().claim("bar", "b", 111, 10)
        self.assertEqual(b.generation, a.generation + 1)
        self.assertFalse(self.store().publish(a, 112))
        self.assertTrue(self.store().publish(b, 112))

    def test_completed_capture_is_not_recoverable(self):
        a = self.store().claim("bar", "a", 100, 10)
        self.assertTrue(self.store().publish(a, 101))
        self.assertFalse(self.store().recover(a, 110))
        self.assertIsNone(self.store().claim("bar", "b", 111, 10))

    def test_invalid_claim_fails_closed(self):
        for args in [("", "a", 100, 10), ("bar", "", 100, 10), ("bar", "a", 100, 0)]:
            with self.assertRaises(ValueError):
                self.store().claim(*args)


if __name__ == "__main__":
    unittest.main()
