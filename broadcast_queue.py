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
    created_at INTEGER NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    deleted_at INTEGER,
    deleted_by TEXT
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
            columns = {row[1] for row in conn.execute("PRAGMA table_info(broadcast_campaigns)")}
            if "title" not in columns:
                conn.execute("ALTER TABLE broadcast_campaigns ADD COLUMN title TEXT NOT NULL DEFAULT ''")
            if "deleted_at" not in columns:
                conn.execute("ALTER TABLE broadcast_campaigns ADD COLUMN deleted_at INTEGER")
            if "deleted_by" not in columns:
                conn.execute("ALTER TABLE broadcast_campaigns ADD COLUMN deleted_by TEXT")

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
                        scheduled_at: int | None = None, title: str = "") -> str:
        if bot_scope not in {"reward", "partnership"}:
            raise ValueError("invalid bot scope")
        campaign_id = self._id("broadcast")
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO broadcast_campaigns(campaign_id,bot_scope,created_by,payload,status,scheduled_at,created_at,title) VALUES(?,?,?,?,?,?,?,?)",
                (campaign_id, bot_scope, str(created_by), json.dumps(payload),
                 "draft", scheduled_at, int(time.time()), title.strip()[:160]),
            )
        return campaign_id

    @staticmethod
    def _campaign_row(row):
        if not row:
            return None
        result = dict(row)
        result["payload"] = json.loads(result["payload"] or "{}")
        if result.get("deleted_at") is not None:
            result["status"] = "deleted"
        return result

    def recent_campaigns(self, *, bot_scope: str = "reward", limit: int = 20) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM broadcast_campaigns WHERE bot_scope=? ORDER BY created_at DESC LIMIT ?",
                (bot_scope, max(1, int(limit))),
            ).fetchall()
        return [self._campaign_row(row) for row in rows]

    def get_campaign(self, campaign_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM broadcast_campaigns WHERE campaign_id=?", (campaign_id,)).fetchone()
        return self._campaign_row(row)

    def queue_campaign(self, campaign_id: str, recipient_ids: list[int | str]) -> int:
        now = int(time.time())
        with self._tx() as conn:
            campaign = conn.execute("SELECT status, deleted_at FROM broadcast_campaigns WHERE campaign_id=?", (campaign_id,)).fetchone()
            if not campaign:
                raise KeyError(campaign_id)
            if campaign["deleted_at"] is not None or campaign["status"] not in {"draft", "paused"}:
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

    def release(self, delivery_id: str, *, retry_seconds: int = 300) -> bool:
        """Return a claimed delivery to pending WITHOUT counting the attempt.

        Used when the worker itself decides not to send (e.g. safe mode) —
        that is not a delivery failure and must not burn the retry budget."""
        with self._tx() as conn:
            cur = conn.execute(
                "UPDATE broadcast_recipients SET status='pending',attempts=MAX(attempts-1,0),next_attempt_at=? "
                "WHERE delivery_id=? AND status='processing'",
                (int(time.time()) + max(1, retry_seconds), delivery_id),
            )
            return cur.rowcount == 1

    def fail(self, delivery_id: str, error: str, *, blocked: bool = False,
             retry_seconds: int = 60, max_attempts: int = 8) -> bool:
        """Record a delivery failure with a bounded retry budget.

        Permanent blocks remain terminal. Transient failures become terminal
        ``failed`` after ``max_attempts`` so one broken destination cannot create
        an infinite retry loop or hide operational debt.
        """
        if int(max_attempts) < 1:
            raise ValueError("max_attempts must be positive")
        with self._tx() as conn:
            row = conn.execute(
                "SELECT attempts FROM broadcast_recipients WHERE delivery_id=? AND status='processing'",
                (delivery_id,),
            ).fetchone()
            if not row:
                return False
            attempts = int(row["attempts"])
            terminal = blocked or attempts >= int(max_attempts)
            status = "blocked" if blocked else ("failed" if terminal else "pending")
            cur = conn.execute(
                "UPDATE broadcast_recipients SET status=?,last_error=?,next_attempt_at=? WHERE delivery_id=? AND status='processing'",
                (status, str(error)[:500],
                 int(time.time()) + (0 if terminal else max(1, retry_seconds)), delivery_id),
            )
            return cur.rowcount == 1

    def recover_stale_processing(self, *, older_than_seconds: int = 900,
                                 retry_seconds: int = 60, max_attempts: int = 8) -> dict:
        """Recover deliveries abandoned by a crashed worker.

        ``claim`` has no external lease column, so this uses the attempt timestamp
        as the recovery boundary. Processing rows older than the threshold are
        returned to the queue or made terminal when their retry budget is spent.
        """
        if int(older_than_seconds) < 1 or int(max_attempts) < 1:
            raise ValueError("recovery thresholds must be positive")
        cutoff = int(time.time()) - int(older_than_seconds)
        with self._tx() as conn:
            rows = conn.execute(
                "SELECT delivery_id,attempts FROM broadcast_recipients "
                "WHERE status='processing' AND next_attempt_at<=?",
                (cutoff,),
            ).fetchall()
            recovered = terminal = 0
            for row in rows:
                is_terminal = int(row["attempts"]) >= int(max_attempts)
                conn.execute(
                    "UPDATE broadcast_recipients SET status=?,last_error=?,next_attempt_at=? "
                    "WHERE delivery_id=? AND status='processing'",
                    ("failed" if is_terminal else "pending",
                     "worker lease expired; delivery recovered",
                     int(time.time()) + (0 if is_terminal else max(1, int(retry_seconds))),
                     row["delivery_id"]),
                )
                if is_terminal:
                    terminal += 1
                else:
                    recovered += 1
            return {"recovered": recovered, "terminal": terminal}

    def campaign_summary(self, campaign_id: str) -> dict | None:
        with self._connect() as conn:
            campaign = conn.execute("SELECT * FROM broadcast_campaigns WHERE campaign_id=?", (campaign_id,)).fetchone()
            if not campaign:
                return None
            counts = conn.execute(
                "SELECT status, COUNT(*) AS count FROM broadcast_recipients WHERE campaign_id=? GROUP BY status",
                (campaign_id,),
            ).fetchall()
        result = self._campaign_row(campaign)
        result["deliveries"] = {row["status"]: int(row["count"]) for row in counts}
        return result

    def update_draft(self, campaign_id: str, *, title: str, payload: dict, scheduled_at: int | None = None) -> bool:
        with self._tx() as conn:
            cur = conn.execute(
                "UPDATE broadcast_campaigns SET title=?,payload=?,scheduled_at=? "
                "WHERE campaign_id=? AND status='draft' AND deleted_at IS NULL",
                (title.strip()[:160], json.dumps(payload), scheduled_at, campaign_id),
            )
            return cur.rowcount == 1

    def delete_draft(self, campaign_id: str, *, deleted_by=None) -> bool:
        """Tombstone a draft so history and audit relationships remain queryable."""
        with self._tx() as conn:
            cur = conn.execute(
                "UPDATE broadcast_campaigns SET deleted_at=?, deleted_by=? "
                "WHERE campaign_id=? AND status='draft' AND deleted_at IS NULL",
                (int(time.time()), str(deleted_by) if deleted_by is not None else None, campaign_id),
            )
            return cur.rowcount == 1

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
