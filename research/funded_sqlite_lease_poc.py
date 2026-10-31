"""Research-only durable SQLite lease and fencing proof of concept.

Not wired to any collector, GitHub workflow, account or order system.
SQLite file is only shared if runners share the SAME durable filesystem.
GitHub-hosted independent runners do not; this is NOT a production backend.
"""
import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True)
class Claim:
    key: str
    owner: str
    generation: int
    expires_at: int


class SQLiteLeaseStore:
    def __init__(self, path: str):
        self.path = path
        with self._connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS leases (key TEXT PRIMARY KEY, owner TEXT NOT NULL, generation INTEGER NOT NULL, expires_at INTEGER NOT NULL, completed INTEGER NOT NULL DEFAULT 0)")
            db.execute("CREATE TABLE IF NOT EXISTS generations (key TEXT PRIMARY KEY, generation INTEGER NOT NULL)")

    def _connect(self):
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.execute("PRAGMA busy_timeout=10000")
        return db

    def claim(self, key: str, owner: str, now: int, ttl: int):
        if not key or not owner or ttl <= 0:
            raise ValueError("invalid lease arguments")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM leases WHERE key=?", (key,)).fetchone():
                db.rollback()
                return None
            previous = db.execute("SELECT generation FROM generations WHERE key=?", (key,)).fetchone()
            generation = (previous[0] if previous else 0) + 1
            db.execute("INSERT INTO generations(key,generation) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET generation=excluded.generation", (key,generation))
            db.execute("INSERT INTO leases(key,owner,generation,expires_at) VALUES (?,?,?,?)", (key,owner,generation,now+ttl))
            db.commit()
            return Claim(key,owner,generation,now+ttl)

    def publish(self, claim: Claim, now: int):
        # Atomic fencing: stale owner cannot commit even if still running.
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            count = db.execute("UPDATE leases SET completed=1 WHERE key=? AND owner=? AND generation=? AND expires_at>? AND completed=0", (claim.key,claim.owner,claim.generation,now)).rowcount
            db.commit()
            return count == 1

    def recover(self, claim: Claim, now: int):
        # Explicit operator-controlled research transition, not auto-steal.
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            count = db.execute("DELETE FROM leases WHERE key=? AND owner=? AND generation=? AND expires_at<=? AND completed=0", (claim.key,claim.owner,claim.generation,now)).rowcount
            db.commit()
            return count == 1
