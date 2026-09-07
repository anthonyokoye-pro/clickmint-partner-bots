"""Persistent replay records for authenticated Admin Mini App mutations."""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path


class _ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


class IdempotencyStore:
    def __init__(self, path: str | Path):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS admin_idempotency (key TEXT PRIMARY KEY, status TEXT NOT NULL, payload TEXT NOT NULL, created_at INTEGER NOT NULL)")

    def _connect(self):
        conn = sqlite3.connect(self.path, factory=_ClosingConnection)
        conn.row_factory = sqlite3.Row
        return conn

    def get(self, key: str, *, ttl: int = 3600):
        now = int(time.time())
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM admin_idempotency WHERE key=?", (key,)).fetchone()
            if not row:
                return None
            if now - int(row["created_at"]) >= int(ttl):
                conn.execute("DELETE FROM admin_idempotency WHERE key=?", (key,))
                return None
            return row["status"], json.loads(row["payload"])

    def put(self, key: str, status: str, payload: dict):
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO admin_idempotency(key,status,payload,created_at) VALUES(?,?,?,?)",
                (key, status, json.dumps(payload, separators=(",", ":")), int(time.time())),
            )

    def prune(self, *, ttl: int = 3600, max_entries: int = 10000):
        with self._connect() as conn:
            conn.execute("DELETE FROM admin_idempotency WHERE created_at<?", (int(time.time()) - int(ttl),))
            conn.execute(
                "DELETE FROM admin_idempotency WHERE key IN (SELECT key FROM admin_idempotency ORDER BY created_at ASC LIMIT MAX(0, (SELECT COUNT(*) FROM admin_idempotency)-?))",
                (int(max_entries),),
            )
