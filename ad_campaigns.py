"""Consent-aware Ad Campaign model — deliberately SEPARATE from broadcasts.

docs/COMPLIANCE_AND_ENFORCEMENT.md: "Keep advertising separate from announcements
and task distribution. Consent, versioned terms, scoped Ads Manager roles, and
safety review are required before advertising is enabled."

This module makes each of those a hard, testable precondition:

  * VERSIONED TERMS   — every consent records the exact terms version accepted.
                        Publishing new terms invalidates older consents for new
                        campaigns (they are not retro-actively "re-agreed").
  * CONSENT           — per destination (channel/group), explicit, revocable.
                        Only a destination that has opted in under the CURRENT
                        terms can ever receive an ad. Owner destinations are NOT
                        exempt: consent is about the audience, not the owner.
  * SCOPED ROLE       — creating/editing ads needs the "ads" admin scope (or the
                        owner). Approving a safety review needs the OWNER.
  * SAFETY REVIEW     — a campaign moves draft -> in_review -> approved by a
                        human with a reason; only approved campaigns queue.
  * KILL SWITCH       — ADS_ENABLED (config) defaults OFF. Disabled means: no
                        queueing, no delivery, consents can still be recorded.

Payment / billing is intentionally absent (financial features stay disabled).
"""
from __future__ import annotations

import json
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

CAMPAIGN_STATES = {"draft", "in_review", "approved", "rejected", "queued", "running", "paused", "completed", "cancelled"}
AD_SCOPE = "ads"

SCHEMA = """
CREATE TABLE IF NOT EXISTS ad_terms (
 version INTEGER PRIMARY KEY, text TEXT NOT NULL, published_by TEXT NOT NULL,
 published_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS ad_consents (
 destination_id TEXT NOT NULL, owner_id TEXT NOT NULL, terms_version INTEGER NOT NULL,
 granted_at INTEGER NOT NULL, revoked_at INTEGER, categories_json TEXT NOT NULL DEFAULT '[]',
 PRIMARY KEY(destination_id, terms_version)
);
CREATE TABLE IF NOT EXISTS ad_campaigns (
 campaign_id TEXT PRIMARY KEY, title TEXT NOT NULL, advertiser_label TEXT NOT NULL,
 category TEXT NOT NULL, payload TEXT NOT NULL, status TEXT NOT NULL,
 created_by TEXT NOT NULL, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
 terms_version INTEGER NOT NULL, scheduled_at INTEGER,
 reviewed_by TEXT, review_reason TEXT NOT NULL DEFAULT '', reviewed_at INTEGER
);
CREATE TABLE IF NOT EXISTS ad_deliveries (
 delivery_id TEXT PRIMARY KEY, campaign_id TEXT NOT NULL, destination_id TEXT NOT NULL,
 consent_terms_version INTEGER NOT NULL, status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
 next_attempt_at INTEGER NOT NULL, telegram_message_id INTEGER, sent_at INTEGER,
 last_error TEXT, UNIQUE(campaign_id, destination_id)
);
CREATE TABLE IF NOT EXISTS ad_events (
 event_id TEXT PRIMARY KEY, campaign_id TEXT, destination_id TEXT, action TEXT NOT NULL,
 actor_id TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '', created_at INTEGER NOT NULL
);
"""


class _ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


class AdPolicyError(ValueError):
    """A compliance precondition (consent / terms / review / role) was not met."""


class AdCampaignStore:
    def __init__(self, path: str | Path, *, enabled: bool = False):
        self.path = str(path)
        self.enabled = bool(enabled)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    # -- plumbing ----------------------------------------------------------
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

    @staticmethod
    def _now():
        return int(time.time())

    def _event(self, conn, action, actor_id, *, campaign_id=None, destination_id=None, reason=""):
        conn.execute("INSERT INTO ad_events(event_id,campaign_id,destination_id,action,actor_id,reason,created_at) VALUES(?,?,?,?,?,?,?)",
                     (self._id("adev"), campaign_id, destination_id, action, str(actor_id), reason, self._now()))

    # -- terms -------------------------------------------------------------
    def publish_terms(self, text: str, *, published_by) -> dict:
        if not text.strip():
            raise AdPolicyError("terms text is required")
        with self._tx() as conn:
            row = conn.execute("SELECT COALESCE(MAX(version),0)+1 AS v FROM ad_terms").fetchone()
            version = int(row["v"])
            conn.execute("INSERT INTO ad_terms(version,text,published_by,published_at) VALUES(?,?,?,?)",
                         (version, text.strip(), str(published_by), self._now()))
            self._event(conn, "TERMS_PUBLISHED", published_by, reason=f"v{version}")
        return self.current_terms()

    def current_terms(self) -> dict | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM ad_terms ORDER BY version DESC LIMIT 1").fetchone()
        return dict(row) if row else None

    # -- consent -----------------------------------------------------------
    def grant_consent(self, destination_id, *, owner_id, terms_version: int, categories=()) -> dict:
        terms = self.current_terms()
        if terms is None:
            raise AdPolicyError("no advertising terms have been published")
        if int(terms_version) != int(terms["version"]):
            raise AdPolicyError(f"consent must reference the current terms (v{terms['version']})")
        now = self._now()
        with self._tx() as conn:
            conn.execute("INSERT OR REPLACE INTO ad_consents(destination_id,owner_id,terms_version,granted_at,revoked_at,categories_json) VALUES(?,?,?,?,NULL,?)",
                         (str(destination_id), str(owner_id), int(terms_version), now, json.dumps(sorted(set(categories)))))
            self._event(conn, "CONSENT_GRANTED", owner_id, destination_id=str(destination_id), reason=f"v{terms_version}")
        return self.consent(destination_id)

    def revoke_consent(self, destination_id, *, owner_id, reason="") -> bool:
        with self._tx() as conn:
            cur = conn.execute("UPDATE ad_consents SET revoked_at=? WHERE destination_id=? AND revoked_at IS NULL",
                               (self._now(), str(destination_id)))
            if cur.rowcount:
                self._event(conn, "CONSENT_REVOKED", owner_id, destination_id=str(destination_id), reason=reason)
                # Nothing may still be waiting to go out to a destination that just said no.
                conn.execute("UPDATE ad_deliveries SET status='cancelled',last_error='consent revoked' "
                             "WHERE destination_id=? AND status IN ('pending','processing')", (str(destination_id),))
            return cur.rowcount > 0

    def consent(self, destination_id) -> dict | None:
        """Active consent under the CURRENT terms, or None."""
        terms = self.current_terms()
        if terms is None:
            return None
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM ad_consents WHERE destination_id=? AND terms_version=? AND revoked_at IS NULL",
                               (str(destination_id), int(terms["version"]))).fetchone()
        if not row:
            return None
        item = dict(row)
        item["categories"] = json.loads(item.pop("categories_json") or "[]")
        return item

    def consenting_destinations(self, category: str | None = None) -> list[dict]:
        terms = self.current_terms()
        if terms is None:
            return []
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM ad_consents WHERE terms_version=? AND revoked_at IS NULL ORDER BY granted_at",
                                (int(terms["version"]),)).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            item["categories"] = json.loads(item.pop("categories_json") or "[]")
            if category and item["categories"] and category not in item["categories"]:
                continue
            out.append(item)
        return out

    # -- campaigns ---------------------------------------------------------
    def create_campaign(self, *, title: str, advertiser_label: str, category: str, text: str,
                        created_by, scheduled_at: int | None = None) -> str:
        terms = self.current_terms()
        if terms is None:
            raise AdPolicyError("publish advertising terms before creating campaigns")
        if not title.strip() or not advertiser_label.strip() or not category.strip():
            raise AdPolicyError("title, advertiser label and category are required")
        if not text.strip() or len(text) > 4000:
            raise AdPolicyError("ad text is required and must be at most 4000 characters")
        campaign_id = self._id("ad")
        now = self._now()
        with self._tx() as conn:
            conn.execute("INSERT INTO ad_campaigns(campaign_id,title,advertiser_label,category,payload,status,created_by,created_at,updated_at,terms_version,scheduled_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                         (campaign_id, title.strip()[:160], advertiser_label.strip()[:120], category.strip(),
                          json.dumps({"text": text}), "draft", str(created_by), now, now, int(terms["version"]), scheduled_at))
            self._event(conn, "CREATED", created_by, campaign_id=campaign_id)
        return campaign_id

    def get_campaign(self, campaign_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM ad_campaigns WHERE campaign_id=?", (campaign_id,)).fetchone()
        if not row:
            return None
        item = dict(row)
        item["payload"] = json.loads(item["payload"])
        return item

    def recent_campaigns(self, limit: int = 50) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute("SELECT campaign_id FROM ad_campaigns ORDER BY created_at DESC, rowid DESC LIMIT ?",
                                (max(1, min(int(limit), 200)),)).fetchall()
        return [self.get_campaign(r["campaign_id"]) for r in rows]

    def update_draft(self, campaign_id: str, *, title=None, text=None, category=None, scheduled_at=None) -> bool:
        campaign = self.get_campaign(campaign_id)
        if not campaign or campaign["status"] not in {"draft", "rejected"}:
            return False
        payload = campaign["payload"]
        if text is not None:
            if not text.strip() or len(text) > 4000:
                raise AdPolicyError("ad text must be 1..4000 characters")
            payload["text"] = text
        with self._tx() as conn:
            conn.execute("UPDATE ad_campaigns SET title=?,category=?,payload=?,scheduled_at=?,status='draft',updated_at=?,reviewed_by=NULL,review_reason='',reviewed_at=NULL WHERE campaign_id=?",
                         ((title or campaign["title"]).strip()[:160], (category or campaign["category"]).strip(),
                          json.dumps(payload), scheduled_at if scheduled_at is not None else campaign["scheduled_at"],
                          self._now(), campaign_id))
            self._event(conn, "EDITED", campaign["created_by"], campaign_id=campaign_id)
        return True

    def submit_for_review(self, campaign_id: str, *, actor_id) -> bool:
        return self._transition(campaign_id, {"draft", "rejected"}, "in_review", actor_id, "SUBMITTED")

    def review(self, campaign_id: str, *, approved: bool, reviewer_id, reason: str) -> dict:
        """Human safety review. A reason is mandatory both ways."""
        if not reason.strip():
            raise AdPolicyError("review reason is required")
        campaign = self.get_campaign(campaign_id)
        if not campaign:
            raise AdPolicyError("campaign not found")
        if campaign["status"] != "in_review":
            raise AdPolicyError("campaign is not awaiting review")
        terms = self.current_terms()
        if approved and int(campaign["terms_version"]) != int(terms["version"]):
            raise AdPolicyError("terms changed since this campaign was drafted; edit and resubmit")
        with self._tx() as conn:
            conn.execute("UPDATE ad_campaigns SET status=?,reviewed_by=?,review_reason=?,reviewed_at=?,updated_at=? WHERE campaign_id=?",
                         ("approved" if approved else "rejected", str(reviewer_id), reason.strip(), self._now(), self._now(), campaign_id))
            self._event(conn, "APPROVED" if approved else "REJECTED", reviewer_id, campaign_id=campaign_id, reason=reason)
        return self.get_campaign(campaign_id)

    def queue(self, campaign_id: str, *, actor_id) -> int:
        """Fan out ONLY to destinations with active consent under current terms.

        Returns the number of deliveries created. Raises if ads are disabled,
        the campaign isn't approved, or nobody has consented."""
        if not self.enabled:
            raise AdPolicyError("advertising is disabled (ADS_ENABLED=false)")
        campaign = self.get_campaign(campaign_id)
        if not campaign:
            raise AdPolicyError("campaign not found")
        if campaign["status"] not in {"approved", "paused"}:
            raise AdPolicyError("only an approved campaign can be queued")
        terms = self.current_terms()
        if int(campaign["terms_version"]) != int(terms["version"]):
            raise AdPolicyError("terms changed since approval; resubmit for review")
        targets = self.consenting_destinations(campaign["category"])
        if not targets:
            raise AdPolicyError("no destination has consented to this category under the current terms")
        now = self._now()
        inserted = 0
        with self._tx() as conn:
            for target in targets:
                cur = conn.execute("INSERT OR IGNORE INTO ad_deliveries(delivery_id,campaign_id,destination_id,consent_terms_version,status,attempts,next_attempt_at) VALUES(?,?,?,?,?,?,?)",
                                   (self._id("addel"), campaign_id, target["destination_id"], int(target["terms_version"]), "pending", 0, now))
                inserted += cur.rowcount
            conn.execute("UPDATE ad_campaigns SET status='queued',updated_at=? WHERE campaign_id=?", (now, campaign_id))
            self._event(conn, "QUEUED", actor_id, campaign_id=campaign_id, reason=f"{inserted} consenting destinations")
        return inserted

    def pause(self, campaign_id, *, actor_id):
        return self._transition(campaign_id, {"queued", "running"}, "paused", actor_id, "PAUSED")

    def resume(self, campaign_id, *, actor_id):
        if not self.enabled:
            raise AdPolicyError("advertising is disabled (ADS_ENABLED=false)")
        return self._transition(campaign_id, {"paused"}, "queued", actor_id, "RESUMED")

    def cancel(self, campaign_id, *, actor_id, reason=""):
        ok = self._transition(campaign_id, CAMPAIGN_STATES - {"completed", "cancelled"}, "cancelled", actor_id, "CANCELLED", reason)
        if ok:
            with self._tx() as conn:
                conn.execute("UPDATE ad_deliveries SET status='cancelled' WHERE campaign_id=? AND status IN ('pending','processing')", (campaign_id,))
        return ok

    def _transition(self, campaign_id, allowed_from, to, actor_id, action, reason=""):
        with self._tx() as conn:
            row = conn.execute("SELECT status FROM ad_campaigns WHERE campaign_id=?", (campaign_id,)).fetchone()
            if not row or row["status"] not in allowed_from:
                return False
            conn.execute("UPDATE ad_campaigns SET status=?,updated_at=? WHERE campaign_id=?", (to, self._now(), campaign_id))
            self._event(conn, action, actor_id, campaign_id=campaign_id, reason=reason)
            return True

    # -- delivery worker boundary ------------------------------------------
    def claim(self, *, limit: int = 20) -> list[dict]:
        """Claim due deliveries. Re-checks consent at claim time: a destination
        that revoked between queue and send is skipped, never messaged."""
        if not self.enabled:
            return []
        now = self._now()
        result = []
        with self._tx() as conn:
            rows = conn.execute(
                "SELECT d.delivery_id, d.destination_id FROM ad_deliveries d JOIN ad_campaigns c ON c.campaign_id=d.campaign_id "
                "WHERE d.status='pending' AND d.next_attempt_at<=? AND c.status IN ('queued','running') "
                "AND (c.scheduled_at IS NULL OR c.scheduled_at<=?) ORDER BY d.rowid LIMIT ?",
                (now, now, max(1, int(limit)))).fetchall()
            for row in rows:
                conn.execute("UPDATE ad_deliveries SET status='processing',attempts=attempts+1 WHERE delivery_id=? AND status='pending'", (row["delivery_id"],))
                item = dict(conn.execute("SELECT * FROM ad_deliveries WHERE delivery_id=?", (row["delivery_id"],)).fetchone())
                result.append(item)
        kept = []
        for item in result:
            if self.consent(item["destination_id"]) is None:
                self.fail(item["delivery_id"], "consent no longer active", blocked=True)
                continue
            kept.append(item)
        return kept

    def complete(self, delivery_id: str, telegram_message_id: int) -> bool:
        with self._tx() as conn:
            cur = conn.execute("UPDATE ad_deliveries SET status='sent',telegram_message_id=?,sent_at=? WHERE delivery_id=? AND status='processing'",
                               (int(telegram_message_id), self._now(), delivery_id))
            if cur.rowcount:
                cid = conn.execute("SELECT campaign_id FROM ad_deliveries WHERE delivery_id=?", (delivery_id,)).fetchone()["campaign_id"]
                left = conn.execute("SELECT COUNT(*) FROM ad_deliveries WHERE campaign_id=? AND status IN ('pending','processing')", (cid,)).fetchone()[0]
                conn.execute("UPDATE ad_campaigns SET status=?,updated_at=? WHERE campaign_id=? AND status IN ('queued','running')",
                             ("completed" if left == 0 else "running", self._now(), cid))
            return cur.rowcount == 1

    def fail(self, delivery_id: str, error: str, *, blocked: bool = False, retry_seconds: int = 60) -> bool:
        with self._tx() as conn:
            cur = conn.execute("UPDATE ad_deliveries SET status=?,last_error=?,next_attempt_at=? WHERE delivery_id=? AND status='processing'",
                               ("blocked" if blocked else "pending", str(error)[:500],
                                self._now() + (0 if blocked else max(1, retry_seconds)), delivery_id))
            return cur.rowcount == 1

    def release(self, delivery_id: str, *, retry_seconds: int = 300) -> bool:
        with self._tx() as conn:
            cur = conn.execute("UPDATE ad_deliveries SET status='pending',attempts=MAX(attempts-1,0),next_attempt_at=? WHERE delivery_id=? AND status='processing'",
                               (self._now() + max(1, retry_seconds), delivery_id))
            return cur.rowcount == 1

    def summary(self, campaign_id: str) -> dict | None:
        campaign = self.get_campaign(campaign_id)
        if not campaign:
            return None
        with self._connect() as conn:
            counts = {r[0]: r[1] for r in conn.execute("SELECT status, COUNT(*) FROM ad_deliveries WHERE campaign_id=? GROUP BY status", (campaign_id,)).fetchall()}
        return {"campaign": campaign, "deliveries": counts}

    def events(self, campaign_id: str | None = None, limit: int = 100) -> list[dict]:
        with self._connect() as conn:
            if campaign_id:
                rows = conn.execute("SELECT * FROM ad_events WHERE campaign_id=? ORDER BY created_at DESC, rowid DESC LIMIT ?", (campaign_id, int(limit))).fetchall()
            else:
                rows = conn.execute("SELECT * FROM ad_events ORDER BY created_at DESC, rowid DESC LIMIT ?", (int(limit),)).fetchall()
        return [dict(r) for r in rows]
