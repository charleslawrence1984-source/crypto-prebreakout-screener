"""Offline research-only single-writer lease model. NOT a production lease backend.

Demonstrates the safety properties required of a future atomic remote store.
Never imports a broker, touches GitHub Actions, or changes scheduling.
"""
from dataclasses import dataclass
from threading import Lock


@dataclass(frozen=True)
class Lease:
    key: str
    owner: str
    generation: int
    expires_at: int


class InMemoryLeaseStore:
    """Thread-safe local model; process-local, NOT cross-run coordination."""

    def __init__(self):
        self._lock = Lock()
        self._leases = {}
        self._generations = {}

    def claim(self, key: str, owner: str, now: int, ttl: int):
        if not key or not owner or ttl <= 0:
            raise ValueError("valid key, owner and positive TTL required")
        with self._lock:
            previous = self._leases.get(key)
            # Expiration does NOT silently authorize takeover; recovery
            # must explicitly fence the prior writer.
            if previous is not None:
                return None
            generation = self._generations.get(key, 0) + 1
            self._generations[key] = generation
            lease = Lease(key, owner, generation, now + ttl)
            self._leases[key] = lease
            return lease

    def publish(self, lease: Lease, now: int) -> bool:
        with self._lock:
            active = self._leases.get(lease.key)
            return active == lease and now < lease.expires_at

    def retire(self, lease: Lease) -> bool:
        with self._lock:
            if self._leases.get(lease.key) != lease:
                return False
            del self._leases[lease.key]
            return True

    def recover_expired(self, lease: Lease, now: int) -> bool:
        """Explicit recovery transition; old fencing generation invalidated."""
        with self._lock:
            if self._leases.get(lease.key) != lease or now < lease.expires_at:
                return False
            del self._leases[lease.key]
            return True
