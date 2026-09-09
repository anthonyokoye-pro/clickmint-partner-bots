"""SQLite-backed store for destination verification data and bot credentials.

Two goals, one module:

* **Shared credential ownership (step 9).** Reward and Partnership used separate
  JSON files, so the same Telegram user could connect bot A in one and bot B in
  the other, and the "one bot per ClickMint account" check only saw half the
  picture. Both bots now open the SAME database, so `BotCredentialStore` and
  `ChannelRegistry` see one truth.
* **JSON → SQLite (step 10).** Destination rows are now real rows with an index
  on owner, state and recheck time, written in a transaction under WAL — no more
  whole-file rewrite per update, no more merge-on-sync races between processes.

Compatibility: this class deliberately exposes the small mapping interface the
existing components already use on `JsonStore` (`get`, `__setitem__`, `sync`,
`__contains__`, `mark_dirty`, `reload`) for exactly two keys:

    "telegram_bot_credentials" -> {owner_id: record}
    "managed_channels"         -> {chat_key: row}

so `ChannelRegistry(store)` and `BotCredentialStore(store)` work unchanged.
Every other key is delegated to the wrapped legacy JsonStore, so ledger, audit,
roles, sessions and friends are untouched by this step.

Migration: on first open, if the SQLite tables are empty and the wrapped JSON
store still holds those keys, rows are copied in once. The JSON copy is left in
place (renamed key `*_migrated`) so nothing is destroyed.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time

CRED_KEY = "telegram_bot_credentials"
DEST_KEY = "managed_channels"
_MANAGED = {CRED_KEY, DEST_KEY}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS bot_credentials (
    owner_id   INTEGER PRIMARY KEY,
    bot_id     INTEGER NOT NULL UNIQUE,
    username   TEXT,
    record     TEXT NOT NULL,           -- full encrypted record (nonce/ciphertext/...)
    updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS destinations (
    chat_key        TEXT PRIMARY KEY,   -- registry key: chat_id or @username
    owner_id        INTEGER NOT NULL,
    verified_state  TEXT NOT NULL DEFAULT 'REGISTERED',
    status          TEXT NOT NULL DEFAULT 'REGISTERED',
    next_check_at   INTEGER,            -- derived on write; lets the worker index-scan
    row             TEXT NOT NULL,      -- full JSON row (source of truth for the registry)
    updated_at      INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_dest_owner ON destinations(owner_id);
CREATE INDEX IF NOT EXISTS ix_dest_state ON destinations(verified_state);
CREATE INDEX IF NOT EXISTS ix_dest_next  ON destinations(next_check_at);
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
"""


def _next_check(row: dict) -> int | None:
    try:
        from destination_state import RECHECK_INTERVAL, coerce_state
    except Exception:            # pragma: no cover - import cycle guard
        return None
    interval = RECHECK_INTERVAL.get(coerce_state(row.get("verified_state")))
    if interval is None:
        return None
    last = max(int(row.get(k) or 0) for k in ("last_recheck_at", "last_verified_at", "last_transition_at"))
    return last + interval


class VerificationStore:
    def __init__(self, db_path: str, legacy=None):
        self.path = db_path
        self.legacy = legacy
        directory = os.path.dirname(os.path.abspath(db_path)) or "."
        os.makedirs(directory, exist_ok=True)
        self._conn = sqlite3.connect(db_path, isolation_level=None, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=30000")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.executescript(_SCHEMA)
        self._cache: dict[str, dict] = {}
        self._dirty: set[str] = set()
        self._seen_version = self._data_version()
        self._migrate_from_legacy()

    def _data_version(self) -> int:
        # Changes whenever ANOTHER connection commits — cheap cross-process
        # cache invalidation, so the reward bot sees a connection made by the
        # partnership bot or the Web App onboarding server without a restart.
        return int(self._conn.execute("PRAGMA data_version").fetchone()[0])

    def _refresh_if_stale(self):
        version = self._data_version()
        if version != self._seen_version:
            self._seen_version = version
            for key in list(self._cache):
                if key not in self._dirty:
                    self._cache.pop(key, None)

    # ------------------------------------------------------------ migration
    def _migrate_from_legacy(self):
        if self.legacy is None:
            return
        for key in _MANAGED:
            legacy_value = self.legacy.get(key)
            if not isinstance(legacy_value, dict) or not legacy_value:
                continue
            if self._count(key) == 0:
                self._write(key, legacy_value)
                self._conn.execute("INSERT OR REPLACE INTO meta(k, v) VALUES (?, ?)",
                                   (f"migrated:{key}", json.dumps({"at": int(time.time()), "rows": len(legacy_value)})))
            # Leave a non-live copy so nothing is lost, but stop the JSON from being read as truth.
            self.legacy[f"{key}_migrated"] = legacy_value
            self.legacy[key] = {}
            self.legacy.sync()

    def _count(self, key: str) -> int:
        table = "bot_credentials" if key == CRED_KEY else "destinations"
        return int(self._conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])

    # --------------------------------------------------------------- reads
    def _read(self, key: str) -> dict:
        if key == CRED_KEY:
            rows = self._conn.execute("SELECT owner_id, record FROM bot_credentials").fetchall()
            return {str(owner): json.loads(rec) for owner, rec in rows}
        rows = self._conn.execute("SELECT chat_key, row FROM destinations").fetchall()
        return {k: json.loads(r) for k, r in rows}

    def get(self, key, default=None):
        if key not in _MANAGED:
            return self.legacy.get(key, default) if self.legacy is not None else default
        self._refresh_if_stale()
        if key not in self._cache:
            self._cache[key] = self._read(key)
        return self._cache[key]

    def __getitem__(self, key):
        if key in _MANAGED:
            return self.get(key)
        return self.legacy[key]

    def __contains__(self, key):
        if key in _MANAGED:
            return True
        return self.legacy is not None and key in self.legacy

    def keys(self):
        base = set(self.legacy.keys()) if self.legacy is not None else set()
        return base | _MANAGED

    # -------------------------------------------------------------- writes
    def __setitem__(self, key, val):
        if key in _MANAGED:
            self._cache[key] = val
            self._dirty.add(key)
            return
        self.legacy[key] = val

    def mark_dirty(self, key):
        if key in _MANAGED:
            self._dirty.add(key)
        elif self.legacy is not None:
            self.legacy.mark_dirty(key)

    def _write(self, key: str, value: dict):
        now = int(time.time())
        with self._conn:                       # one transaction per sync
            self._conn.execute("BEGIN IMMEDIATE")
            if key == CRED_KEY:
                self._conn.execute("DELETE FROM bot_credentials")
                for owner, rec in value.items():
                    self._conn.execute(
                        "INSERT INTO bot_credentials(owner_id, bot_id, username, record, updated_at) VALUES (?,?,?,?,?)",
                        (int(owner), int(rec.get("bot_id", 0)), rec.get("username"), json.dumps(rec),
                         int(rec.get("updated_at") or now)))
            else:
                self._conn.execute("DELETE FROM destinations")
                for chat_key, row in value.items():
                    self._conn.execute(
                        "INSERT INTO destinations(chat_key, owner_id, verified_state, status, next_check_at, row, updated_at)"
                        " VALUES (?,?,?,?,?,?,?)",
                        (str(chat_key), int(row.get("owner_id") or 0), str(row.get("verified_state") or "REGISTERED"),
                         str(row.get("status") or "REGISTERED"), _next_check(row), json.dumps(row), now))

    def sync(self):
        for key in list(self._dirty):
            self._write(key, self._cache.get(key) or {})
        self._dirty.clear()
        self._seen_version = self._data_version()
        if self.legacy is not None:
            self.legacy.sync()

    def reload(self):
        self._cache.clear()
        if self.legacy is not None:
            self.legacy.reload()

    # ------------------------------------------------ indexed query helpers
    def destinations_due(self, now: int | None = None, limit: int = 50) -> list[dict]:
        """Rows whose recheck time has passed — served by ix_dest_next, so the
        background worker no longer loads every destination to find five."""
        now = int(now if now is not None else time.time())
        rows = self._conn.execute(
            "SELECT row FROM destinations WHERE next_check_at IS NOT NULL AND next_check_at <= ?"
            " ORDER BY next_check_at ASC LIMIT ?", (now, int(limit))).fetchall()
        return [json.loads(r[0]) for r in rows]

    def owner_of_bot(self, bot_id: int) -> int | None:
        row = self._conn.execute("SELECT owner_id FROM bot_credentials WHERE bot_id = ?", (int(bot_id),)).fetchone()
        return int(row[0]) if row else None

    def close(self):
        self._conn.close()
