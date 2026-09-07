"""Small SQLite broadcast queue for Reward and Partnership scopes.

This is delivery infrastructure only. Audience selection and message authorization
remain in the respective bot services. It is intentionally compatible with a later
PostgreSQL/worker migration.
"""
from __future__ import annotations

import json
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS broadcast_campaigns (
    campaign_id TEXT PRIMARY KEY,
    bot_scope TEXT NOT NULL CHECK (bot_scope IN ('reward','partnership')),
    created_by TEXT NOT NULL,
    payload TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('draft','queued','running','paused','cancelled','completed')),
    scheduled_at INTEGER,
    created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS broadcast_recipients (
    delivery_id TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL REFERENCES broadcast_campaigns(campaign_id),
    recipient_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending','processing','sent','failed','blocked','cancelled')),
    attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt_at INTEGER NOT NULL,
    last_error TEXT,
    telegram_message_id INTEGER,
    sent_at INTEGER,
    UNIQUE(campaign_id, recipient_id)
);
CREATE INDEX IF NOT EXISTS idx_broadcast_ready
ON broadcast_recipients(status, next_attempt_at);
"""


class _ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


class BroadcastQueue:
    def __init__(self, path: str | Path):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    def _connect(self):
        conn = sqlite3.connect(self.path, timeout=30, isolation_level=None, factory=_ClosingConnection)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    @contextmanager
    def _tx(self):
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    @staticmethod
    def _id(prefix):
        return f"{prefix}_{secrets.token_hex(10)}"

    def create_campaign(self, *, bot_scope: str, created_by, payload: dict,
                        scheduled_at: int | None = None) -> str:
        if bot_scope not in {"reward", "partnership"}:
            raise ValueError("invalid bot scope")
        campaign_id = self._id("broadcast")
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO broadcast_campaigns VALUES(?,?,?,?,?,?,?)",
                (campaign_id, bot_scope, str(created_by), json.dumps(payload),
                 "draft", scheduled_at, int(time.time())),
            )
        return campaign_id

    def recent_campaigns(self, *, bot_scope: str = "reward", limit: int = 20) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM broadcast_campaigns WHERE bot_scope=? ORDER BY created_at DESC LIMIT ?",
                (bot_scope, max(1, int(limit))),
            ).fetchall()
        results = []
        for row in rows:
            item = dict(row)
            item["payload"] = json.loads(item["payload"] or "{}")
            results.append(item)
        return results

    def get_campaign(self, campaign_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM broadcast_campaigns WHERE campaign_id=?", (campaign_id,)).fetchone()
        if not row:
            return None
        result = dict(row)
        result["payload"] = json.loads(result["payload"] or "{}")
        return result

    def queue_campaign(self, campaign_id: str, recipient_ids: list[int | str]) -> int:
        now = int(time.time())
        with self._tx() as conn:
            campaign = conn.execute("SELECT status FROM broadcast_campaigns WHERE campaign_id=?", (campaign_id,)).fetchone()
            if not campaign:
                raise KeyError(campaign_id)
            if campaign["status"] not in {"draft", "paused"}:
                raise ValueError("campaign is not queueable")
            inserted = 0
            for recipient_id in dict.fromkeys(str(value) for value in recipient_ids):
                delivery_id = self._id("delivery")
                cur = conn.execute(
                    "INSERT OR IGNORE INTO broadcast_recipients(delivery_id,campaign_id,recipient_id,status,attempts,next_attempt_at) VALUES(?,?,?,?,?,?)",
                    (delivery_id, campaign_id, recipient_id, "pending", 0, now),
                )
                inserted += cur.rowcount
            conn.execute("UPDATE broadcast_campaigns SET status='queued' WHERE campaign_id=?", (campaign_id,))
            return inserted

    def claim(self, *, limit: int = 20, bot_scope: str | None = None) -> list[dict]:
        now = int(time.time())
        with self._tx() as conn:
            scope_sql = ""
            params: list[object] = [now, now]
            if bot_scope:
                scope_sql = " AND c.bot_scope=?"
                params.append(bot_scope)
            params.append(max(1, int(limit)))
            rows = conn.execute(
                "SELECT r.delivery_id FROM broadcast_recipients r "
                "JOIN broadcast_campaigns c ON c.campaign_id=r.campaign_id "
                "WHERE r.status='pending' AND r.next_attempt_at<=? "
                "AND c.status IN ('queued','running') "
                "AND (c.scheduled_at IS NULL OR c.scheduled_at<=?)" + scope_sql +
                " ORDER BY r.delivery_id LIMIT ?",
                params,
            ).fetchall()
            result = []
            for row in rows:
                conn.execute(
                    "UPDATE broadcast_recipients SET status='processing', attempts=attempts+1 WHERE delivery_id=? AND status='pending'",
                    (row["delivery_id"],),
                )
                item = conn.execute("SELECT * FROM broadcast_recipients WHERE delivery_id=?", (row["delivery_id"],)).fetchone()
                result.append(dict(item))
            return result

    def complete(self, delivery_id: str, telegram_message_id: int) -> bool:
        with self._tx() as conn:
            cur = conn.execute(
                "UPDATE broadcast_recipients SET status='sent',telegram_message_id=?,sent_at=? WHERE delivery_id=? AND status='processing'",
                (int(telegram_message_id), int(time.time()), delivery_id),
            )
            return cur.rowcount == 1

    def fail(self, delivery_id: str, error: str, *, blocked: bool = False,
             retry_seconds: int = 60) -> bool:
        with self._tx() as conn:
            cur = conn.execute(
                "UPDATE broadcast_recipients SET status=?,last_error=?,next_attempt_at=? WHERE delivery_id=? AND status='processing'",
                ("blocked" if blocked else "pending", str(error)[:500],
                 int(time.time()) + (0 if blocked else max(1, retry_seconds)), delivery_id),
            )
            return cur.rowcount == 1

    def campaign_summary(self, campaign_id: str) -> dict | None:
        with self._connect() as conn:
            campaign = conn.execute("SELECT * FROM broadcast_campaigns WHERE campaign_id=?", (campaign_id,)).fetchone()
            if not campaign:
                return None
            counts = conn.execute(
                "SELECT status, COUNT(*) AS count FROM broadcast_recipients WHERE campaign_id=? GROUP BY status",
                (campaign_id,),
            ).fetchall()
        result = dict(campaign)
        result["payload"] = json.loads(result["payload"] or "{}")
        result["deliveries"] = {row["status"]: int(row["count"]) for row in counts}
        return result

    def pause(self, campaign_id: str) -> bool:
        with self._tx() as conn:
            cur = conn.execute(
                "UPDATE broadcast_campaigns SET status='paused' WHERE campaign_id=? AND status IN ('queued','running')",
                (campaign_id,),
            )
            return cur.rowcount == 1

    def resume(self, campaign_id: str) -> bool:
        with self._tx() as conn:
            cur = conn.execute(
                "UPDATE broadcast_campaigns SET status='queued' WHERE campaign_id=? AND status='paused'",
                (campaign_id,),
            )
            return cur.rowcount == 1

    def cancel(self, campaign_id: str) -> int:
        with self._tx() as conn:
            cur = conn.execute(
                "UPDATE broadcast_recipients SET status='cancelled' WHERE campaign_id=? AND status IN ('pending','processing')",
                (campaign_id,),
            )
            conn.execute("UPDATE broadcast_campaigns SET status='cancelled' WHERE campaign_id=?", (campaign_id,))
            return cur.rowcount
