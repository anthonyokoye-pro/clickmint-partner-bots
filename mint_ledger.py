"""Transactional Mint ledger and first-touch referral domain services.

This module is deliberately independent of Telegram and the legacy JSON store.
It provides the transactional foundation required before Mint rewards, referrals,
and paid posting can be trusted. SQLite is used as the zero-dependency local
backend; the schema and repository boundaries are designed for a later
PostgreSQL migration.

Rules implemented here:
- Mint is an internal unit, never a cryptocurrency.
- Ledger entries are append-only; corrections are compensating entries.
- Credits and debits are idempotent.
- Referral attribution is first-touch and single-level.
- Referral rewards are pending until qualification and risk checks pass.
- Posting orders reserve Mint atomically and can be refunded idempotently.
"""
from __future__ import annotations

import hashlib
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path


SCHEMA = """
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;
PRAGMA synchronous = FULL;

CREATE TABLE IF NOT EXISTS mint_accounts (
    user_id TEXT PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'active',
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS mint_ledger_entries (
    entry_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES mint_accounts(user_id),
    amount INTEGER NOT NULL CHECK (amount > 0),
    direction TEXT NOT NULL CHECK (direction IN ('credit', 'debit')),
    state TEXT NOT NULL CHECK (state IN ('pending', 'confirmed', 'reversed', 'void')),
    entry_type TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    reference_type TEXT,
    reference_id TEXT,
    metadata TEXT NOT NULL DEFAULT '{}',
    created_at INTEGER NOT NULL,
    confirmed_at INTEGER,
    reversed_entry_id TEXT REFERENCES mint_ledger_entries(entry_id)
);
CREATE INDEX IF NOT EXISTS idx_mint_entries_user ON mint_ledger_entries(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_mint_entries_reference ON mint_ledger_entries(reference_type, reference_id);

CREATE TABLE IF NOT EXISTS referral_codes (
    code TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL REFERENCES mint_accounts(user_id),
    campaign_id TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    max_uses INTEGER,
    uses INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL,
    expires_at INTEGER
);

CREATE TABLE IF NOT EXISTS referrals (
    referred_id TEXT PRIMARY KEY REFERENCES mint_accounts(user_id),
    referrer_id TEXT NOT NULL REFERENCES mint_accounts(user_id),
    code TEXT NOT NULL REFERENCES referral_codes(code),
    campaign_id TEXT,
    status TEXT NOT NULL CHECK (status IN ('attributed', 'pending', 'qualified', 'rejected', 'reversed')),
    first_touch_at INTEGER NOT NULL,
    qualified_at INTEGER,
    reward_entry_id TEXT REFERENCES mint_ledger_entries(entry_id)
);
CREATE INDEX IF NOT EXISTS idx_referrals_referrer ON referrals(referrer_id, status);

CREATE TABLE IF NOT EXISTS reward_events (
    event_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES mint_accounts(user_id),
    event_type TEXT NOT NULL,
    source_id TEXT NOT NULL,
    amount INTEGER NOT NULL CHECK (amount > 0),
    status TEXT NOT NULL CHECK (status IN ('pending', 'confirmed', 'rejected', 'reversed')),
    idempotency_key TEXT NOT NULL UNIQUE,
    policy_version TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    confirmed_at INTEGER,
    metadata TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_reward_events_user ON reward_events(user_id, created_at);

CREATE TABLE IF NOT EXISTS fraud_flags (
    flag_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES mint_accounts(user_id),
    flag_type TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('open', 'reviewed', 'dismissed')),
    reason TEXT NOT NULL,
    source_id TEXT,
    created_at INTEGER NOT NULL,
    reviewed_at INTEGER
);
CREATE INDEX IF NOT EXISTS idx_fraud_flags_user ON fraud_flags(user_id, status);

CREATE TABLE IF NOT EXISTS post_orders (
    order_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES mint_accounts(user_id),
    amount INTEGER NOT NULL CHECK (amount > 0),
    status TEXT NOT NULL CHECK (status IN ('reserved', 'completed', 'failed', 'refunded')),
    idempotency_key TEXT NOT NULL UNIQUE,
    source_id TEXT NOT NULL,
    debit_entry_id TEXT NOT NULL REFERENCES mint_ledger_entries(entry_id),
    created_at INTEGER NOT NULL,
    completed_at INTEGER
);

CREATE TABLE IF NOT EXISTS audit_events (
    audit_id TEXT PRIMARY KEY,
    actor_type TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    action TEXT NOT NULL,
    object_type TEXT NOT NULL,
    object_id TEXT NOT NULL,
    reason TEXT NOT NULL,
    created_at INTEGER NOT NULL
);
"""


class InsufficientMint(Exception):
    """Raised when an atomic Mint debit cannot be funded."""


class DuplicateOperation(Exception):
    """Raised when an idempotency key is reused for a different operation."""


class TransactionalMintLedger:
    """SQLite-backed transactional Mint repository.

    A new connection is opened per operation so the service is safe to use from
    several bot processes. SQLite WAL plus IMMEDIATE transactions serialize
    balance-changing operations. Production deployment should move this schema
    to PostgreSQL before materially scaling the system.
    """

    def __init__(self, path: str | Path):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    def _connect(self):
        conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 30000")
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
        return f"{prefix}_{secrets.token_hex(12)}"

    def ensure_account(self, user_id: int | str, *, status: str = "active") -> None:
        uid = str(user_id)
        stamp = self._now()
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO mint_accounts(user_id,status,created_at,updated_at) VALUES(?,?,?,?) "
                "ON CONFLICT(user_id) DO UPDATE SET status=excluded.status, updated_at=excluded.updated_at",
                (uid, status, stamp, stamp),
            )

    def _ensure_account_tx(self, conn, user_id):
        uid = str(user_id)
        stamp = self._now()
        conn.execute(
            "INSERT INTO mint_accounts(user_id,created_at,updated_at) VALUES(?,?,?) ON CONFLICT(user_id) DO NOTHING",
            (uid, stamp, stamp),
        )
        return uid

    def _entry_by_key(self, conn, key: str):
        return conn.execute("SELECT * FROM mint_ledger_entries WHERE idempotency_key=?", (key,)).fetchone()

    def _balance_tx(self, conn, uid: str, *, include_pending: bool = False) -> int:
        states = ("confirmed", "pending") if include_pending else ("confirmed",)
        marks = ",".join("?" for _ in states)
        row = conn.execute(
            f"SELECT COALESCE(SUM(CASE WHEN direction='credit' THEN amount ELSE -amount END),0) AS balance "
            f"FROM mint_ledger_entries WHERE user_id=? AND state IN ({marks})",
            (uid, *states),
        ).fetchone()
        return int(row["balance"])

    def balance(self, user_id: int | str, *, include_pending: bool = False) -> int:
        uid = str(user_id)
        with self._connect() as conn:
            return self._balance_tx(conn, uid, include_pending=include_pending)

    def entries(self, user_id: int | str, limit: int = 100) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM mint_ledger_entries WHERE user_id=? ORDER BY created_at DESC LIMIT ?",
                (str(user_id), int(limit)),
            ).fetchall()
            return [dict(row) for row in rows]

    def _add_entry_tx(self, conn, *, uid, amount, direction, state, entry_type,
                      idempotency_key, reference_type=None, reference_id=None,
                      metadata="{}") -> str:
        existing = self._entry_by_key(conn, idempotency_key)
        if existing:
            if (existing["user_id"], existing["amount"], existing["direction"], existing["entry_type"]) != (str(uid), int(amount), direction, entry_type):
                raise DuplicateOperation(idempotency_key)
            return existing["entry_id"]
        if direction == "debit" and state in ("pending", "confirmed"):
            if self._balance_tx(conn, str(uid)) < int(amount):
                raise InsufficientMint(f"user {uid} has insufficient confirmed Mint")
        entry_id = self._id("mint")
        now = self._now()
        conn.execute(
            "INSERT INTO mint_ledger_entries(entry_id,user_id,amount,direction,state,entry_type,idempotency_key,reference_type,reference_id,metadata,created_at,confirmed_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (entry_id, str(uid), int(amount), direction, state, entry_type, idempotency_key,
             reference_type, reference_id, metadata, now, now if state == "confirmed" else None),
        )
        return entry_id

    def credit(self, user_id, amount: int, *, entry_type: str, idempotency_key: str,
               reference_type=None, reference_id=None, pending: bool = False,
               metadata: str = "{}") -> str:
        if int(amount) <= 0:
            raise ValueError("Mint credit must be positive")
        with self._tx() as conn:
            uid = self._ensure_account_tx(conn, user_id)
            return self._add_entry_tx(conn, uid=uid, amount=amount, direction="credit",
                                      state="pending" if pending else "confirmed",
                                      entry_type=entry_type, idempotency_key=idempotency_key,
                                      reference_type=reference_type, reference_id=reference_id,
                                      metadata=metadata)

    def debit(self, user_id, amount: int, *, entry_type: str, idempotency_key: str,
              reference_type=None, reference_id=None, metadata: str = "{}") -> str:
        if int(amount) <= 0:
            raise ValueError("Mint debit must be positive")
        with self._tx() as conn:
            uid = self._ensure_account_tx(conn, user_id)
            return self._add_entry_tx(conn, uid=uid, amount=amount, direction="debit",
                                      state="confirmed", entry_type=entry_type,
                                      idempotency_key=idempotency_key, reference_type=reference_type,
                                      reference_id=reference_id, metadata=metadata)

    def confirm_entry(self, entry_id: str) -> bool:
        with self._tx() as conn:
            row = conn.execute("SELECT * FROM mint_ledger_entries WHERE entry_id=?", (entry_id,)).fetchone()
            if not row:
                raise KeyError(entry_id)
            if row["state"] != "pending":
                return row["state"] == "confirmed"
            if row["direction"] == "debit" and self._balance_tx(conn, row["user_id"]) < row["amount"]:
                raise InsufficientMint("pending debit cannot be confirmed")
            conn.execute("UPDATE mint_ledger_entries SET state='confirmed', confirmed_at=? WHERE entry_id=?",
                         (self._now(), entry_id))
            return True

    def reverse(self, entry_id: str, *, idempotency_key: str, reason: str) -> str:
        with self._tx() as conn:
            row = conn.execute("SELECT * FROM mint_ledger_entries WHERE entry_id=?", (entry_id,)).fetchone()
            if not row:
                raise KeyError(entry_id)
            if row["state"] == "reversed":
                existing = conn.execute("SELECT entry_id FROM mint_ledger_entries WHERE reversed_entry_id=?", (entry_id,)).fetchone()
                return existing["entry_id"] if existing else ""
            reverse_direction = "debit" if row["direction"] == "credit" else "credit"
            reverse_id = self._add_entry_tx(
                conn, uid=row["user_id"], amount=row["amount"], direction=reverse_direction,
                state="confirmed", entry_type="REVERSAL", idempotency_key=idempotency_key,
                reference_type="mint_entry", reference_id=entry_id,
                metadata='{"reason": ' + repr(reason).replace("'", '"') + '}',
            )
            conn.execute("UPDATE mint_ledger_entries SET state='reversed', reversed_entry_id=? WHERE entry_id=?",
                         (reverse_id, entry_id))
            return reverse_id

    def create_referral_code(self, owner_id, *, campaign_id=None, max_uses=None,
                             expires_at=None) -> str:
        with self._tx() as conn:
            owner = self._ensure_account_tx(conn, owner_id)
            for _ in range(5):
                code = "CM_" + secrets.token_urlsafe(8).replace("-", "_")
                try:
                    conn.execute(
                        "INSERT INTO referral_codes(code,owner_id,campaign_id,max_uses,created_at,expires_at) VALUES(?,?,?,?,?,?)",
                        (code, owner, campaign_id, max_uses, self._now(), expires_at),
                    )
                    return code
                except sqlite3.IntegrityError:
                    continue
            raise RuntimeError("could not create unique referral code")

    def attach_referral(self, referred_id, code: str) -> bool:
        with self._tx() as conn:
            referred = self._ensure_account_tx(conn, referred_id)
            row = conn.execute("SELECT * FROM referral_codes WHERE code=? AND active=1", (code,)).fetchone()
            if not row or row["owner_id"] == referred:
                return False
            if row["expires_at"] is not None and row["expires_at"] < self._now():
                return False
            if row["max_uses"] is not None and row["uses"] >= row["max_uses"]:
                return False
            try:
                conn.execute(
                    "INSERT INTO referrals(referred_id,referrer_id,code,campaign_id,status,first_touch_at) VALUES(?,?,?,?,?,?)",
                    (referred, row["owner_id"], code, row["campaign_id"], "attributed", self._now()),
                )
            except sqlite3.IntegrityError:
                return False
            conn.execute("UPDATE referral_codes SET uses=uses+1 WHERE code=?", (code,))
            return True

    def qualify_referral(self, referred_id, *, activity_id: str, reward_amount: int = 1,
                         policy_version: str = "referral_v1") -> bool:
        """Create one pending referral reward after a qualifying activity.

        A unique activity-derived idempotency key prevents repeated callbacks or
        retries from issuing multiple Mint rewards. Open fraud flags reject new
        rewards until an administrator reviews them.
        """
        with self._tx() as conn:
            referred = str(referred_id)
            row = conn.execute("SELECT * FROM referrals WHERE referred_id=?", (referred,)).fetchone()
            if not row or row["status"] in ("qualified", "rejected", "reversed"):
                return False
            open_flag = conn.execute(
                "SELECT 1 FROM fraud_flags WHERE user_id IN (?,?) AND status='open' LIMIT 1",
                (referred, row["referrer_id"]),
            ).fetchone()
            if open_flag:
                conn.execute("UPDATE referrals SET status='pending' WHERE referred_id=?", (referred,))
                return False
            event_key = f"referral:{referred}:{activity_id}"
            existing = conn.execute("SELECT event_id FROM reward_events WHERE idempotency_key=?", (event_key,)).fetchone()
            if existing:
                return False
            event_id = self._id("reward")
            conn.execute(
                "INSERT INTO reward_events(event_id,user_id,event_type,source_id,amount,status,idempotency_key,policy_version,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (event_id, row["referrer_id"], "REFERRAL_REWARD", activity_id, int(reward_amount), "pending", event_key, policy_version, self._now()),
            )
            conn.execute("UPDATE referrals SET status='pending', qualified_at=? WHERE referred_id=?", (self._now(), referred))
            return True

    def confirm_referral_reward(self, referred_id, *, activity_id: str) -> str | None:
        with self._tx() as conn:
            row = conn.execute("SELECT * FROM referrals WHERE referred_id=?", (str(referred_id),)).fetchone()
            if not row:
                return None
            event = conn.execute(
                "SELECT * FROM reward_events WHERE event_type='REFERRAL_REWARD' AND user_id=? AND source_id=? AND status='pending'",
                (row["referrer_id"], activity_id),
            ).fetchone()
            if not event:
                return None
            entry_id = self._add_entry_tx(
                conn, uid=row["referrer_id"], amount=event["amount"], direction="credit",
                state="confirmed", entry_type="REFERRAL_REWARD",
                idempotency_key=f"mint:{event['event_id']}", reference_type="reward_event",
                reference_id=event["event_id"],
            )
            conn.execute("UPDATE reward_events SET status='confirmed', confirmed_at=? WHERE event_id=?", (self._now(), event["event_id"]))
            conn.execute("UPDATE referrals SET status='qualified', reward_entry_id=? WHERE referred_id=?", (entry_id, str(referred_id)))
            return entry_id

    def flag_fraud(self, user_id, *, flag_type: str, reason: str, source_id=None) -> str:
        with self._tx() as conn:
            uid = self._ensure_account_tx(conn, user_id)
            flag_id = self._id("fraud")
            conn.execute(
                "INSERT INTO fraud_flags(flag_id,user_id,flag_type,status,reason,source_id,created_at) VALUES(?,?,?,?,?,?,?)",
                (flag_id, uid, flag_type, "open", reason, source_id, self._now()),
            )
            return flag_id

    def create_post_order(self, user_id, *, amount: int, source_id: str,
                          idempotency_key: str) -> str:
        with self._tx() as conn:
            uid = self._ensure_account_tx(conn, user_id)
            existing = conn.execute("SELECT order_id FROM post_orders WHERE idempotency_key=?", (idempotency_key,)).fetchone()
            if existing:
                return existing["order_id"]
            order_id = self._id("post")
            entry_id = self._add_entry_tx(
                conn, uid=uid, amount=amount, direction="debit", state="confirmed",
                entry_type="POSTING_SPEND", idempotency_key=f"post-debit:{idempotency_key}",
                reference_type="post_order", reference_id=order_id,
            )
            conn.execute(
                "INSERT INTO post_orders(order_id,user_id,amount,status,idempotency_key,source_id,debit_entry_id,created_at) VALUES(?,?,?,?,?,?,?,?)",
                (order_id, uid, int(amount), "reserved", idempotency_key, source_id, entry_id, self._now()),
            )
            return order_id

    def complete_post_order(self, order_id: str) -> bool:
        with self._tx() as conn:
            cur = conn.execute("UPDATE post_orders SET status='completed', completed_at=? WHERE order_id=? AND status='reserved'", (self._now(), order_id))
            return cur.rowcount == 1

    def refund_post_order(self, order_id: str, *, reason: str) -> str | None:
        with self._tx() as conn:
            row = conn.execute("SELECT * FROM post_orders WHERE order_id=?", (order_id,)).fetchone()
            if not row or row["status"] == "refunded":
                return None
            entry_id = self._add_entry_tx(
                conn, uid=row["user_id"], amount=row["amount"], direction="credit", state="confirmed",
                entry_type="POSTING_REFUND", idempotency_key=f"post-refund:{order_id}",
                reference_type="post_order", reference_id=order_id,
                metadata='{"reason": "' + reason.replace('"', '') + '"}',
            )
            conn.execute("UPDATE post_orders SET status='refunded', completed_at=? WHERE order_id=?", (self._now(), order_id))
            return entry_id

    def add_audit_event(self, *, actor_type: str, actor_id: str, action: str,
                        object_type: str, object_id: str, reason: str) -> str:
        with self._tx() as conn:
            audit_id = self._id("audit")
            conn.execute(
                "INSERT INTO audit_events(audit_id,actor_type,actor_id,action,object_type,object_id,reason,created_at) VALUES(?,?,?,?,?,?,?,?)",
                (audit_id, actor_type, str(actor_id), action, object_type, str(object_id), reason, self._now()),
            )
            return audit_id
