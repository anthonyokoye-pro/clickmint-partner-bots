"""Unified delivery worker primitives for Reward and Partnership queues.

The worker owns operational policy (rate limiting, recovery, metrics and
exception classification); bot modules provide only scope-specific claim and
send callbacks.  SQLite keeps the limiter and metrics shared across restarts
and future worker processes without introducing Redis/Celery.
"""
from __future__ import annotations

import asyncio
import sqlite3
import time
from pathlib import Path
from dataclasses import dataclass


_SCHEMA = """
CREATE TABLE IF NOT EXISTS worker_buckets (
    bucket TEXT PRIMARY KEY,
    tokens REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS worker_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scope TEXT NOT NULL,
    outcome TEXT NOT NULL,
    duration_ms REAL NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_worker_metrics_time ON worker_metrics(created_at, scope);
"""


class WorkerStore:
    def __init__(self, path: str | Path):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, timeout=30, isolation_level=None,
                                     check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=30000")
        self._conn.executescript(_SCHEMA)

    def acquire(self, bucket: str, *, rate_per_second: float, burst: int = 1) -> bool:
        """Atomically consume one token from a persistent token bucket."""
        rate = max(0.001, float(rate_per_second)); capacity = max(1, int(burst))
        # Wall clock is persisted; monotonic values are invalid after restart.
        now = time.time()
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            row = self._conn.execute("SELECT tokens,updated_at FROM worker_buckets WHERE bucket=?",
                                     (bucket,)).fetchone()
            if row is None:
                tokens, updated = float(capacity), now
            else:
                tokens = min(float(capacity), float(row["tokens"]) + max(0.0, now - float(row["updated_at"])) * rate)
                updated = now
            allowed = tokens >= 1.0
            if allowed:
                tokens -= 1.0
            self._conn.execute(
                "INSERT INTO worker_buckets(bucket,tokens,updated_at) VALUES(?,?,?) "
                "ON CONFLICT(bucket) DO UPDATE SET tokens=excluded.tokens,updated_at=excluded.updated_at",
                (bucket, tokens, updated),
            )
            self._conn.execute("COMMIT")
            return allowed
        except Exception:
            self._conn.execute("ROLLBACK")
            raise

    def record(self, scope: str, outcome: str, duration_ms: float = 0) -> None:
        self._conn.execute("INSERT INTO worker_metrics(scope,outcome,duration_ms,created_at) VALUES(?,?,?,?)",
                           (scope, outcome, float(duration_ms), int(time.time())))

    def summary(self, *, since: int = 0) -> dict:
        rows = self._conn.execute(
            "SELECT scope,outcome,COUNT(*) count,AVG(duration_ms) avg_ms "
            "FROM worker_metrics WHERE created_at>=? GROUP BY scope,outcome", (int(since),)).fetchall()
        result: dict[str, dict] = {}
        for row in rows:
            result.setdefault(row["scope"], {})[row["outcome"]] = {
                "count": int(row["count"]), "average_ms": round(float(row["avg_ms"]), 2)}
        return result

    def close(self):
        self._conn.close()


@dataclass
class WorkerScope:
    name: str
    claim: object
    deliver: object
    complete: object
    fail: object
    rate_per_second: float = 1.0
    burst: int = 1


def admit_delivery(store: WorkerStore, scope: str, item: dict, *, rate_per_second: float, burst: int) -> bool:
    """Centralized admission helper for existing bot loops during extraction."""
    bucket = f"{scope}:{item.get('recipient_id', 'global')}"
    allowed = store.acquire(bucket, rate_per_second=rate_per_second, burst=burst)
    if not allowed:
        store.record(scope, "rate_limited")
    return allowed


class UnifiedDeliveryWorker:
    """Run all registered delivery scopes with one operational policy."""
    def __init__(self, store: WorkerStore, *, batch_size: int = 20):
        self.store = store
        self.batch_size = max(1, int(batch_size))
        self.scopes: list[WorkerScope] = []

    def register(self, scope: WorkerScope) -> None:
        if any(existing.name == scope.name for existing in self.scopes):
            raise ValueError(f"duplicate worker scope: {scope.name}")
        self.scopes.append(scope)

    async def run_once(self) -> dict:
        totals = {"claimed": 0, "delivered": 0, "failed": 0, "rate_limited": 0}
        for scope in self.scopes:
            claimed = scope.claim(limit=self.batch_size)
            for item in claimed:
                totals["claimed"] += 1
                bucket = f"{scope.name}:{item.get('recipient_id', 'global')}"
                if not self.store.acquire(bucket, rate_per_second=scope.rate_per_second,
                                          burst=scope.burst):
                    totals["rate_limited"] += 1
                    self.store.record(scope.name, "rate_limited")
                    scope.fail(item, "central delivery rate limit", retry_seconds=1)
                    continue
                started = time.perf_counter()
                try:
                    result = scope.deliver(item)
                    if asyncio.iscoroutine(result):
                        result = await result
                    scope.complete(item, result)
                    totals["delivered"] += 1
                    self.store.record(scope.name, "delivered", (time.perf_counter() - started) * 1000)
                except Exception as exc:
                    totals["failed"] += 1
                    scope.fail(item, str(exc)[:500], retry_seconds=60)
                    self.store.record(scope.name, "failed", (time.perf_counter() - started) * 1000)
        return totals
