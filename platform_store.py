"""SQLite-backed delivery audit log and role registry (decision 2026-09-09 #2).

Why: `DeliveryLog` appended to a JSON list and rewrote the whole file on every
delivery, and `RoleRegistry` lived in each bot's private JSON, so the admin bot
had to read two files to list admins and a code generated for "both" scopes had
to be written twice. Both now live in ONE shared SQLite database (WAL), opened
by the reward bot, the partnership bot, the admin bot and the admin server.

Two things live here:

* ``DeliveryAudit`` — one row per (post -> target) delivery event. Same public
  surface as ``core.DeliveryLog`` (``record``, ``all``, ``last``, ``count``,
  ``summary``, ``invalid_records``) plus ``update(row_id, **changes)`` so a
  status change is a targeted UPDATE, not an in-place mutation and rewrite.
* ``SharedKV`` — a tiny mapping facade (``get``/``__setitem__``/``sync``/
  ``__contains__``/``mark_dirty``/``reload``) storing whole JSON values per key,
  used so ``governance.RoleRegistry`` works unchanged on top of SQLite. Roles are
  small dicts; whole-value writes in a transaction are fine for them.

Migration: on first open with a ``legacy`` JsonStore, ``delivery_log`` rows are
appended once per legacy file (tracked in ``meta``) and ``roles_users`` /
``roles_invites`` are merged in without overwriting existing entries. The JSON
copies are renamed ``*_migrated`` so nothing is lost and nothing is read twice.

Sessions and the credit ledger are deliberately NOT here: sessions stay JSON,
the ledger moves via ``MINT_LEDGER_MODE=transactional`` (``mint_ledger.py``).
"""
from __future__ import annotations

import json
import os
import sqlite3
import time

from core import DeliveryLog

_SCHEMA = """
CREATE TABLE IF NOT EXISTS delivery_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    bot TEXT,
    sender TEXT,
    target_channel TEXT,
    mode TEXT,
    status TEXT,
    forward_valid INTEGER NOT NULL DEFAULT 1,
    row TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_delivery_target ON delivery_log(target_channel, status);
CREATE INDEX IF NOT EXISTS ix_delivery_sender ON delivery_log(sender, status);
CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT NOT NULL, updated_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
"""


def _connect(db_path: str) -> sqlite3.Connection:
    directory = os.path.dirname(os.path.abspath(db_path)) or "."
    os.makedirs(directory, exist_ok=True)
    conn = sqlite3.connect(db_path, isolation_level=None, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(_SCHEMA)
    return conn


def _legacy_tag(legacy) -> str:
    return os.path.abspath(getattr(legacy, "path", "") or "") or repr(id(legacy))


# ---------------------------------------------------------------------------
# Delivery audit
# ---------------------------------------------------------------------------
class DeliveryAudit(DeliveryLog):
    """Drop-in for ``core.DeliveryLog`` on SQLite. ``key`` is kept for callers
    that only inspect it (tests); it is not a JSON key any more."""

    key = "delivery_log"

    def __init__(self, db_path: str, legacy=None):   # noqa: D401 - intentional override
        self.path = db_path
        self.legacy = legacy
        self._conn = _connect(db_path)
        self._migrate(legacy)

    # -- migration ---------------------------------------------------------
    def _migrate(self, legacy):
        if legacy is None:
            return
        rows = legacy.get(self.key)
        if not isinstance(rows, list) or not rows:
            return
        tag = f"migrated:delivery_log:{_legacy_tag(legacy)}"
        if self._conn.execute("SELECT 1 FROM meta WHERE k=?", (tag,)).fetchone() is None:
            with self._conn:
                self._conn.execute("BEGIN IMMEDIATE")
                for row in rows:
                    self._insert(dict(row))
                self._conn.execute("INSERT OR REPLACE INTO meta(k, v) VALUES (?, ?)",
                                   (tag, json.dumps({"at": int(time.time()), "rows": len(rows)})))
        legacy[f"{self.key}_migrated"] = rows
        legacy[self.key] = []
        legacy.sync()

    # -- writes --------------------------------------------------------------
    def _insert(self, row: dict) -> int:
        row.setdefault("ts", time.strftime("%Y-%m-%d %H:%M:%S"))
        row.pop("id", None)
        cur = self._conn.execute(
            "INSERT INTO delivery_log(ts, bot, sender, target_channel, mode, status, forward_valid, row)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (str(row.get("ts")), row.get("bot"), _s(row.get("sender")), _s(row.get("target_channel")),
             row.get("mode"), row.get("status"), 1 if row.get("forward_valid", True) else 0, json.dumps(row)))
        return int(cur.lastrowid)

    def record(self, **row) -> dict:
        row_id = self._insert(row)
        row["id"] = row_id
        return row

    def update(self, row_id: int, **changes) -> dict | None:
        cur = self._conn.execute("SELECT row FROM delivery_log WHERE id=?", (int(row_id),)).fetchone()
        if cur is None:
            return None
        row = json.loads(cur[0])
        row.update(changes)
        row.pop("id", None)
        self._conn.execute(
            "UPDATE delivery_log SET status=?, mode=?, forward_valid=?, row=? WHERE id=?",
            (row.get("status"), row.get("mode"), 1 if row.get("forward_valid", True) else 0,
             json.dumps(row), int(row_id)))
        row["id"] = int(row_id)
        return row

    def clear(self):
        self._conn.execute("DELETE FROM delivery_log")

    # -- reads ---------------------------------------------------------------
    @staticmethod
    def _hydrate(rows) -> list:
        out = []
        for row_id, raw in rows:
            d = json.loads(raw)
            d["id"] = int(row_id)
            out.append(d)
        return out

    def all(self) -> list:
        return self._hydrate(self._conn.execute("SELECT id, row FROM delivery_log ORDER BY id").fetchall())

    def last(self, n: int = 20) -> list:
        rows = self._conn.execute("SELECT id, row FROM delivery_log ORDER BY id DESC LIMIT ?", (int(n),)).fetchall()
        return list(reversed(self._hydrate(rows)))

    def count(self) -> int:
        return int(self._conn.execute("SELECT COUNT(*) FROM delivery_log").fetchone()[0])

    def latest_for(self, *, sender, target_channel, status) -> dict | None:
        """Most recent row matching (sender, target, status) — indexed, no full scan."""
        row = self._conn.execute(
            "SELECT id, row FROM delivery_log WHERE sender=? AND target_channel=? AND status=?"
            " ORDER BY id DESC LIMIT 1", (_s(sender), _s(target_channel), status)).fetchone()
        return self._hydrate([row])[0] if row else None

    def invalid_records(self) -> list:
        return self._hydrate(self._conn.execute(
            "SELECT id, row FROM delivery_log WHERE forward_valid=0 ORDER BY id").fetchall())

    def summary(self) -> str:
        counts = dict(self._conn.execute(
            "SELECT status, COUNT(*) FROM delivery_log GROUP BY status").fetchall())
        total = self.count()
        invalid = int(self._conn.execute("SELECT COUNT(*) FROM delivery_log WHERE forward_valid=0").fetchone()[0])
        lines = [
            f"AUDIT SUMMARY — {total} deliveries",
            f"  delivered: {counts.get('delivered', 0)}   agreed: {counts.get('agreed', 0)}"
            f"   skipped: {counts.get('skipped', 0)}",
        ]
        if invalid:
            lines.append(f"  ⚠️ {invalid} delivery(ies) were NOT genuine forwards (copies).")
        return "\n".join(lines)


def _s(v):
    return None if v is None else str(v)


# ---------------------------------------------------------------------------
# Shared key/value facade (roles)
# ---------------------------------------------------------------------------
class SharedKV:
    """Mapping facade over the ``kv`` table for whole-value JSON keys.

    Cross-process freshness: reads check ``PRAGMA data_version`` and drop the
    cache when another connection committed, like ``VerificationStore``."""

    MIGRATE_KEYS = ("roles_users", "roles_invites")

    def __init__(self, db_path: str, legacy=None):
        self.path = db_path
        self.legacy = legacy
        self._conn = _connect(db_path)
        self._cache: dict[str, object] = {}
        self._dirty: set[str] = set()
        self._seen_version = self._data_version()
        self._migrate(legacy)

    def _data_version(self) -> int:
        return int(self._conn.execute("PRAGMA data_version").fetchone()[0])

    def _refresh_if_stale(self):
        v = self._data_version()
        if v != self._seen_version:
            self._seen_version = v
            for k in list(self._cache):
                if k not in self._dirty:
                    self._cache.pop(k, None)

    def _migrate(self, legacy):
        if legacy is None:
            return
        for key in self.MIGRATE_KEYS:
            old = legacy.get(key)
            if not isinstance(old, dict) or not old:
                continue
            current = self._read(key) or {}
            merged = {**old, **current}          # existing SQLite entries win
            self._write(key, merged)
            legacy[f"{key}_migrated"] = old
            legacy[key] = {}
            legacy.sync()

    def _read(self, key):
        row = self._conn.execute("SELECT v FROM kv WHERE k=?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def _write(self, key, value):
        self._conn.execute("INSERT OR REPLACE INTO kv(k, v, updated_at) VALUES (?,?,?)",
                           (key, json.dumps(value), int(time.time())))

    # mapping surface used by RoleRegistry -----------------------------------
    def get(self, key, default=None):
        self._refresh_if_stale()
        if key not in self._cache:
            v = self._read(key)
            if v is None:
                return default
            self._cache[key] = v
        return self._cache[key]

    def __getitem__(self, key):
        v = self.get(key)
        if v is None:
            raise KeyError(key)
        return v

    def __contains__(self, key):
        return self.get(key) is not None

    def __setitem__(self, key, val):
        self._cache[key] = val
        self._dirty.add(key)

    def mark_dirty(self, key):
        self._dirty.add(key)

    def sync(self):
        if not self._dirty:
            return
        with self._conn:
            self._conn.execute("BEGIN IMMEDIATE")
            for k in list(self._dirty):
                self._write(k, self._cache.get(k))
        self._dirty.clear()
        self._seen_version = self._data_version()

    def reload(self):
        self._cache.clear()

    def clear(self, *keys):
        """Reset the role keys to empty (test/reset helper). Keys stay present so
        components that initialised them at construction keep working."""
        for k in keys or self.MIGRATE_KEYS:
            self._write(k, {})
            self._cache.pop(k, None)
        self._dirty.clear()
