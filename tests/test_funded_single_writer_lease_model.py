"""Offline model checks only: not proof of cross-run atomic lease backend."""
import unittest
from concurrent.futures import ThreadPoolExecutor

from research.funded_single_writer_lease_model import InMemoryLeaseStore


class LeaseModelTests(unittest.TestCase):
    def test_concurrent_claims_have_exactly_one_winner(self):
        store = InMemoryLeaseStore()
        with ThreadPoolExecutor(max_workers=12) as pool:
            results = list(pool.map(
                lambda n: store.claim("crypto/4h/2026-10-31T00:00Z", str(n), 100, 90),
                range(100)))
        self.assertEqual(sum(x is not None for x in results), 1)

    def test_same_owner_cannot_reclaim_existing_key(self):
        store = InMemoryLeaseStore()
        self.assertIsNotNone(store.claim("k", "a", 10, 5))
        self.assertIsNone(store.claim("k", "a", 11, 5))

    def test_expiry_is_not_automatic_takeover(self):
        store = InMemoryLeaseStore()
        old = store.claim("k", "a", 10, 5)
        self.assertIsNone(store.claim("k", "b", 20, 5))
        self.assertFalse(store.publish(old, 20))

    def test_recovery_fences_old_generation(self):
        store = InMemoryLeaseStore()
        old = store.claim("k", "a", 10, 5)
        self.assertFalse(store.recover_expired(old, 14))
        self.assertTrue(store.recover_expired(old, 15))
        new = store.claim("k", "b", 15, 5)
        self.assertEqual(new.generation, old.generation + 1)
        self.assertFalse(store.publish(old, 16))
        self.assertTrue(store.publish(new, 16))
        self.assertFalse(store.retire(old))

    def test_expired_lease_cannot_publish(self):
        store = InMemoryLeaseStore()
        lease = store.claim("k", "a", 10, 5)
        self.assertTrue(store.publish(lease, 14))
        self.assertFalse(store.publish(lease, 15))

    def test_invalid_claim_fails_closed(self):
        store = InMemoryLeaseStore()
        for args in [("", "a", 10, 5), ("k", "", 10, 5), ("k", "a", 10, 0)]:
            with self.assertRaises(ValueError):
                store.claim(*args)


if __name__ == "__main__":
    unittest.main()
