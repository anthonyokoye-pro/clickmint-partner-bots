"""Transactional Task Marketplace repository.

This is the first marketplace foundation.  It replaces the old global
AvailablePostQueue concept with tasks that have shared capacity and per-user
claims.  Eligibility/recommendation policy remains outside this repository so
Telegram handlers, analytics, and future workers can share it.
"""
from __future__ import annotations

import json
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path


SCHEMA = """
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;
PRAGMA synchronous = FULL;

CREATE TABLE IF NOT EXISTS tasks (
    task_id TEXT PRIMARY KEY,
    creator_user_id TEXT NOT NULL,
    category TEXT NOT NULL,
    title TEXT NOT NULL,
    payload TEXT NOT NULL DEFAULT '{}',
    required_performers INTEGER NOT NULL CHECK (required_performers > 0),
    status TEXT NOT NULL CHECK (status IN ('draft','published','full','expired','cancelled')),
    created_at INTEGER NOT NULL,
    published_at INTEGER,
    expires_at INTEGER,
    reward_amount INTEGER NOT NULL DEFAULT 1 CHECK (reward_amount > 0),
    minimum_tier TEXT NOT NULL DEFAULT 'PROVISIONAL',
    allowed_categories TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS idx_tasks_marketplace
    ON tasks(status, expires_at, created_at);

CREATE TABLE IF NOT EXISTS task_claims (
    claim_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(task_id),
    user_id TEXT NOT NULL,
    destination_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('claimed','completed','released','expired')),
    claimed_at INTEGER NOT NULL,
    lease_expires_at INTEGER NOT NULL,
    completed_at INTEGER,
    UNIQUE(task_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_task_claims_task ON task_claims(task_id, status);
CREATE INDEX IF NOT EXISTS idx_task_claims_user ON task_claims(user_id, status);

CREATE TABLE IF NOT EXISTS task_completions (
    completion_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(task_id),
    claim_id TEXT NOT NULL UNIQUE REFERENCES task_claims(claim_id),
    user_id TEXT NOT NULL,
    destination_id TEXT NOT NULL,
    telegram_chat_id TEXT NOT NULL,
    telegram_message_id INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('verified','reversed')),
    completed_at INTEGER NOT NULL,
    reward_event_id TEXT UNIQUE
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_task_completion_user
    ON task_completions(task_id, user_id);
CREATE TABLE IF NOT EXISTS task_outbox (
    event_id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    task_id TEXT NOT NULL,
    payload TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending','processing','sent','failed')),
    attempts INTEGER NOT NULL DEFAULT 0,
    available_at INTEGER NOT NULL,
    last_error TEXT,
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_task_outbox_ready ON task_outbox(status, available_at);
"""


class TaskError(Exception):
    pass


class TaskNotFound(TaskError):
    pass


class TaskUnavailable(TaskError):
    pass


class DuplicateTaskCompletion(TaskError):
    pass


class _ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


class TaskMarketplace:
    def __init__(self, path: str | Path):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(SCHEMA)
            self._ensure_columns(conn)

    def _ensure_columns(self, conn):
        """Additive compatibility migration for task databases created earlier."""
        columns = {row[1] for row in conn.execute("PRAGMA table_info(tasks)").fetchall()}
        additions = {
            "reward_amount": "INTEGER NOT NULL DEFAULT 1",
            "minimum_tier": "TEXT NOT NULL DEFAULT 'PROVISIONAL'",
            "allowed_categories": "TEXT NOT NULL DEFAULT '[]'",
        }
        for name, definition in additions.items():
            if name not in columns:
                conn.execute(f"ALTER TABLE tasks ADD COLUMN {name} {definition}")

    def _connect(self):
        conn = sqlite3.connect(self.path, timeout=30, isolation_level=None, factory=_ClosingConnection)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
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
    def _now() -> int:
        return int(time.time())

    @staticmethod
    def _id(prefix: str) -> str:
        return f"{prefix}_{secrets.token_hex(10)}"

    def _emit(self, conn, event_type: str, task_id: str, payload: dict) -> None:
        stamp = self._now()
        conn.execute(
            "INSERT INTO task_outbox(event_id,event_type,task_id,payload,status,available_at,created_at) VALUES(?,?,?,?,?,?,?)",
            (self._id("taskevt"), event_type, task_id, json.dumps(payload, separators=(",", ":")), "pending", stamp, stamp),
        )

    def create_task(self, *, creator_user_id, category: str, title: str,
                    required_performers: int, payload: dict | None = None,
                    expires_at: int | None = None, reward_amount: int = 1,
                    minimum_tier: str = "PROVISIONAL",
                    allowed_categories: list[str] | None = None) -> str:
        if not category.strip() or not title.strip():
            raise ValueError("category and title are required")
        if int(required_performers) < 1:
            raise ValueError("required_performers must be positive")
        if int(reward_amount) < 1:
            raise ValueError("reward_amount must be positive")
        valid_tiers = {"PROVISIONAL", "EMERGING", "ESTABLISHED", "PROVEN", "PREMIER"}
        if minimum_tier not in valid_tiers:
            raise ValueError("invalid minimum credibility tier")
        task_id = self._id("task")
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO tasks(task_id,creator_user_id,category,title,payload,required_performers,status,created_at,expires_at,reward_amount,minimum_tier,allowed_categories) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (task_id, str(creator_user_id), category.strip(), title.strip(),
                 json.dumps(payload or {}, separators=(",", ":")),
                 int(required_performers), "draft", self._now(), expires_at,
                 int(reward_amount), minimum_tier,
                 json.dumps(list(allowed_categories or [category.strip()]))) ,
            )
            self._emit(conn, "TASK_CREATED", task_id, {"creator_user_id": str(creator_user_id)})
        return task_id

    def get_task(self, task_id: str) -> dict | None:
        with self._connect() as conn:
            return self._row(conn.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone())

    def publish(self, task_id: str) -> bool:
        with self._tx() as conn:
            cur = conn.execute(
                "UPDATE tasks SET status='published', published_at=? "
                "WHERE task_id=? AND status='draft'",
                (self._now(), task_id),
            )
            if cur.rowcount:
                self._emit(conn, "TASK_PUBLISHED", task_id, {})
            return cur.rowcount == 1

    def cancel(self, task_id: str) -> bool:
        """Cancel a draft or published task and release active claims."""
        with self._tx() as conn:
            cur = conn.execute(
                "UPDATE tasks SET status='cancelled' WHERE task_id=? AND status IN ('draft','published','full')",
                (task_id,),
            )
            if cur.rowcount:
                conn.execute("UPDATE task_claims SET status='released' WHERE task_id=? AND status='claimed'", (task_id,))
                self._emit(conn, "TASK_CANCELLED", task_id, {})
            return cur.rowcount == 1

    def _expire_tasks_tx(self, conn, now: int):
        tasks = conn.execute(
            "UPDATE tasks SET status='expired' WHERE status IN ('published','full') "
            "AND expires_at IS NOT NULL AND expires_at<=?",
            (now,),
        ).rowcount
        claims = conn.execute(
            "UPDATE task_claims SET status='expired' WHERE status='claimed' "
            "AND lease_expires_at<=?",
            (now,),
        ).rowcount
        # A claim lease can expire before the task itself. Re-open a previously
        # full task when capacity is available again.
        reopened = conn.execute(
            "UPDATE tasks SET status='published' WHERE status='full' "
            "AND (expires_at IS NULL OR expires_at>?) "
            "AND (SELECT COUNT(*) FROM task_claims c WHERE c.task_id=tasks.task_id "
            "AND c.status IN ('claimed','completed')) < required_performers",
            (now,),
        ).rowcount
        return tasks, claims, reopened

    def recover_expired(self) -> dict:
        """Release stale claims and expire/reopen tasks in one transaction."""
        with self._tx() as conn:
            tasks, claims, reopened = self._expire_tasks_tx(conn, self._now())
            return {"expired_tasks": tasks, "expired_claims": claims,
                    "reopened_tasks": reopened}

    def available_for(self, user_id, *, categories: list[str] | None = None,
                      limit: int = 20) -> list[dict]:
        """Return marketplace candidates, excluding tasks this user completed/claimed.

        Band/status/channel eligibility is deliberately evaluated by the caller's
        policy service; this query only enforces durable marketplace state.
        """
        now = self._now()
        with self._tx() as conn:
            self._expire_tasks_tx(conn, now)
            params: list[object] = [str(user_id)]
            category_sql = ""
            if categories:
                marks = ",".join("?" for _ in categories)
                category_sql = f" AND t.category IN ({marks})"
                params.extend(categories)
            params.append(max(1, int(limit)))
            rows = conn.execute(
                "SELECT t.*, "
                "(SELECT COUNT(*) FROM task_claims c WHERE c.task_id=t.task_id "
                "AND c.status IN ('claimed','completed')) AS occupied_slots "
                "FROM tasks t WHERE t.status='published' "
                "AND (t.expires_at IS NULL OR t.expires_at>?) "
                "AND NOT EXISTS (SELECT 1 FROM task_claims mine "
                "WHERE mine.task_id=t.task_id AND mine.user_id=? "
                "AND mine.status IN ('claimed','completed')) "
                + category_sql +
                " AND (SELECT COUNT(*) FROM task_claims c2 WHERE c2.task_id=t.task_id "
                "AND c2.status IN ('claimed','completed')) < t.required_performers "
                "ORDER BY t.created_at ASC LIMIT ?",
                [now, *params],
            ).fetchall()
            return [self._row(row) for row in rows]

    def claim(self, task_id: str, *, user_id, destination_id,
              lease_seconds: int = 3600) -> dict:
        if lease_seconds < 1:
            raise ValueError("lease_seconds must be positive")
        now = self._now()
        with self._tx() as conn:
            self._expire_tasks_tx(conn, now)
            task = conn.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
            if not task:
                raise TaskNotFound(task_id)
            if task["status"] != "published":
                raise TaskUnavailable("task is not published")
            if task["expires_at"] is not None and task["expires_at"] <= now:
                raise TaskUnavailable("task has expired")
            existing = conn.execute(
                "SELECT * FROM task_claims WHERE task_id=? AND user_id=? "
                "AND status IN ('claimed','completed')",
                (task_id, str(user_id)),
            ).fetchone()
            if existing:
                raise TaskUnavailable("user already has this task")
            occupied = conn.execute(
                "SELECT COUNT(*) FROM task_claims WHERE task_id=? AND status IN ('claimed','completed')",
                (task_id,),
            ).fetchone()[0]
            if occupied >= task["required_performers"]:
                conn.execute("UPDATE tasks SET status='full' WHERE task_id=?", (task_id,))
                raise TaskUnavailable("no task slots remain")
            claim_id = self._id("claim")
            conn.execute(
                "INSERT INTO task_claims(claim_id,task_id,user_id,destination_id,status,claimed_at,lease_expires_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (claim_id, task_id, str(user_id), str(destination_id), "claimed",
                 now, now + int(lease_seconds)),
            )
            self._emit(conn, "TASK_CLAIMED", task_id, {"claim_id": claim_id, "user_id": str(user_id), "destination_id": str(destination_id)})
            return self._row(conn.execute("SELECT * FROM task_claims WHERE claim_id=?", (claim_id,)).fetchone())

    def complete(self, claim_id: str, *, telegram_chat_id,
                 telegram_message_id: int, reward_event_id: str | None = None) -> dict:
        if int(telegram_message_id) < 1:
            raise ValueError("telegram_message_id must be positive")
        now = self._now()
        with self._tx() as conn:
            claim = conn.execute("SELECT * FROM task_claims WHERE claim_id=?", (claim_id,)).fetchone()
            if not claim:
                raise TaskNotFound(claim_id)
            if claim["status"] != "claimed":
                if claim["status"] == "completed":
                    raise DuplicateTaskCompletion(claim_id)
                raise TaskUnavailable("claim is no longer active")
            task = conn.execute("SELECT * FROM tasks WHERE task_id=?", (claim["task_id"],)).fetchone()
            if not task or task["status"] not in ("published", "full"):
                raise TaskUnavailable("task is not completable")
            if claim["lease_expires_at"] <= now:
                conn.execute("UPDATE task_claims SET status='expired' WHERE claim_id=?", (claim_id,))
                raise TaskUnavailable("claim lease expired")
            completion_id = self._id("completion")
            try:
                conn.execute(
                    "INSERT INTO task_completions(completion_id,task_id,claim_id,user_id,destination_id,telegram_chat_id,telegram_message_id,status,completed_at,reward_event_id) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (completion_id, claim["task_id"], claim_id, claim["user_id"],
                     claim["destination_id"], str(telegram_chat_id), int(telegram_message_id),
                     "verified", now, reward_event_id),
                )
            except sqlite3.IntegrityError as exc:
                raise DuplicateTaskCompletion(claim_id) from exc
            conn.execute("UPDATE task_claims SET status='completed', completed_at=? WHERE claim_id=?", (now, claim_id))
            occupied = conn.execute(
                "SELECT COUNT(*) FROM task_claims WHERE task_id=? AND status IN ('claimed','completed')",
                (claim["task_id"],),
            ).fetchone()[0]
            if occupied >= task["required_performers"]:
                conn.execute("UPDATE tasks SET status='full' WHERE task_id=?", (claim["task_id"],))
            self._emit(conn, "TASK_COMPLETED", claim["task_id"], {"completion_id": completion_id, "claim_id": claim_id, "user_id": claim["user_id"], "reward_event_id": reward_event_id})
            row = conn.execute("SELECT * FROM task_completions WHERE completion_id=?", (completion_id,)).fetchone()
            return self._row(row)

    def release_claim(self, claim_id: str, *, reason: str = "") -> bool:
        with self._tx() as conn:
            cur = conn.execute(
                "UPDATE task_claims SET status='released' WHERE claim_id=? AND status='claimed'",
                (claim_id,),
            )
            if cur.rowcount == 1:
                # Re-open a task whose last slot was held by this claim. This is
                # essential after a delivery failure: released capacity must not
                # strand a task in ``full`` forever.
                conn.execute(
                    "UPDATE tasks SET status='published' WHERE task_id=(SELECT task_id FROM task_claims WHERE claim_id=?) "
                    "AND status='full' AND (expires_at IS NULL OR expires_at>?) "
                    "AND (SELECT COUNT(*) FROM task_claims c WHERE c.task_id=tasks.task_id "
                    "AND c.status IN ('claimed','completed')) < required_performers",
                    (claim_id, self._now()),
                )
            return cur.rowcount == 1

    def user_claim_count_since(self, user_id, since: int, destination_id=None) -> int:
        with self._connect() as conn:
            if destination_id is None:
                return int(conn.execute(
                    "SELECT COUNT(*) FROM task_claims WHERE user_id=? AND claimed_at>=? "
                    "AND status IN ('claimed','completed')",
                    (str(user_id), int(since)),
                ).fetchone()[0])
            return int(conn.execute(
                "SELECT COUNT(*) FROM task_claims WHERE user_id=? AND destination_id=? "
                "AND claimed_at>=? AND status IN ('claimed','completed')",
                (str(user_id), str(destination_id), int(since)),
            ).fetchone()[0])

    def reconcile(self) -> dict:
        """Repair derived task states and expire stale claims atomically.

        Claims/completions are the durable facts. ``tasks.status`` is a derived
        index used for discovery, so this method makes it safe to run after a
        crash or manual database restore.
        """
        now = self._now()
        with self._tx() as conn:
            expired_tasks, expired_claims, reopened = self._expire_tasks_tx(conn, now)
            full = conn.execute(
                "UPDATE tasks SET status='full' WHERE status='published' "
                "AND (SELECT COUNT(*) FROM task_claims c WHERE c.task_id=tasks.task_id "
                "AND c.status IN ('claimed','completed')) >= required_performers"
            ).rowcount
            reopened += conn.execute(
                "UPDATE tasks SET status='published' WHERE status='full' "
                "AND (expires_at IS NULL OR expires_at>?) "
                "AND (SELECT COUNT(*) FROM task_claims c WHERE c.task_id=tasks.task_id "
                "AND c.status IN ('claimed','completed')) < required_performers",
                (now,),
            ).rowcount
            return {"expired_tasks": expired_tasks, "expired_claims": expired_claims,
                    "marked_full": full, "reopened_tasks": reopened}

    def reconcile_rewards(self, mint_ledger) -> dict:
        """Idempotently reconcile verified completions into canonical Mint.

        The task database remains the source of delivery evidence; Mint remains
        the source of balance authority. The deterministic key prevents a retry
        or restart from issuing a second reward.
        """
        with self._connect() as conn:
            rows = conn.execute("SELECT c.*, t.reward_amount FROM task_completions c JOIN tasks t ON t.task_id=c.task_id WHERE c.status='verified'").fetchall()
        credited = skipped = 0
        for row in rows:
            key = row["reward_event_id"] or f"task:{row['completion_id']}"
            existing = {entry.get("idempotency_key") for entry in mint_ledger.entries(row["user_id"], limit=100000)}
            if key in existing:
                skipped += 1
            else:
                mint_ledger.credit(row["user_id"], int(row["reward_amount"]), entry_type="TASK_REWARD", idempotency_key=key, reference_type="task_completion", reference_id=row["completion_id"])
                credited += 1
            with self._tx() as conn:
                conn.execute("UPDATE task_completions SET reward_event_id=? WHERE completion_id=?", (key, row["completion_id"]))
        return {"checked": len(rows), "credited": credited, "already_reconciled": skipped}

    def claim_outbox(self, limit: int = 20):
        now = self._now()
        with self._tx() as conn:
            rows = conn.execute("SELECT event_id FROM task_outbox WHERE status IN ('pending','failed') AND available_at<=? ORDER BY created_at LIMIT ?", (now, max(1, int(limit)))).fetchall()
            result = []
            for row in rows:
                conn.execute("UPDATE task_outbox SET status='processing', attempts=attempts+1 WHERE event_id=?", (row["event_id"],))
                item = conn.execute("SELECT * FROM task_outbox WHERE event_id=?", (row["event_id"],)).fetchone()
                data = dict(item); data["payload"] = json.loads(data["payload"] or "{}"); result.append(data)
            return result

    def complete_outbox(self, event_id: str) -> bool:
        with self._tx() as conn:
            return conn.execute("UPDATE task_outbox SET status='sent' WHERE event_id=? AND status='processing'", (event_id,)).rowcount == 1

    def fail_outbox(self, event_id: str, error: str, retry_delay: int = 60) -> bool:
        with self._tx() as conn:
            return conn.execute("UPDATE task_outbox SET status='failed', last_error=?, available_at=? WHERE event_id=? AND status='processing'", (str(error)[:500], self._now()+max(1, int(retry_delay)), event_id)).rowcount == 1

    def progress(self, task_id: str) -> dict:
        with self._connect() as conn:
            task = conn.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
            if not task:
                raise TaskNotFound(task_id)
            completed = conn.execute(
                "SELECT COUNT(*) FROM task_completions WHERE task_id=? AND status='verified'",
                (task_id,),
            ).fetchone()[0]
            claimed = conn.execute(
                "SELECT COUNT(*) FROM task_claims WHERE task_id=? AND status='claimed'",
                (task_id,),
            ).fetchone()[0]
            return {
                "task_id": task_id,
                "completed": int(completed),
                "claimed": int(claimed),
                "required": int(task["required_performers"]),
                "remaining": max(0, int(task["required_performers"]) - completed - claimed),
                "status": task["status"],
            }

    @staticmethod
    def _row(row):
        if row is None:
            return None
        result = dict(row)
        if "payload" in result:
            result["payload"] = json.loads(result["payload"] or "{}")
        if "allowed_categories" in result:
            result["allowed_categories"] = json.loads(result["allowed_categories"] or "[]")
        return result
