"""Compliance-first enforcement, incident, report, and safe-mode store.

This module records decisions; it does not infer guilt from one signal and does
not interact with Telegram enforcement systems. Serious actions remain explicit,
auditable administrator decisions (or a separately reviewed system action).
"""
from __future__ import annotations

import json
import secrets
import sqlite3
import time
from pathlib import Path

STATES = {"ACTIVE", "FLAGGED", "RESTRICTED", "SUSPENDED", "BANNED", "REMOVED"}
APPEAL_STATES = {"OPEN", "UNDER_REVIEW", "UPHELD", "OVERTURNED", "WITHDRAWN"}
REPORT_STATES = {"PENDING", "UNDER_REVIEW", "DISMISSED", "WARNED", "RESTRICTED", "SUSPENDED", "BANNED", "RESOLVED"}
CAPABILITIES = {"tasks", "distribution", "partnerships", "rewards", "campaigns", "registration", "automated_posting"}
SCHEMA = """
CREATE TABLE IF NOT EXISTS enforcement_entities (
 entity_id TEXT NOT NULL, entity_type TEXT NOT NULL,
 state TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '',
 actor_id TEXT NOT NULL, updated_at INTEGER NOT NULL,
 expires_at INTEGER, notes TEXT NOT NULL DEFAULT '',
 PRIMARY KEY(entity_id, entity_type)
);
CREATE TABLE IF NOT EXISTS enforcement_events (
 event_id TEXT PRIMARY KEY, entity_id TEXT NOT NULL, entity_type TEXT NOT NULL,
 action TEXT NOT NULL, previous_state TEXT NOT NULL, new_state TEXT NOT NULL,
 actor_id TEXT NOT NULL, reason TEXT NOT NULL, related_report_id TEXT,
 evidence_json TEXT NOT NULL DEFAULT '[]', duration_seconds INTEGER,
 created_at INTEGER NOT NULL, notes TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS compliance_reports (
 report_id TEXT PRIMARY KEY, reporter_id TEXT NOT NULL, entity_id TEXT NOT NULL,
 entity_type TEXT NOT NULL, subject TEXT NOT NULL DEFAULT '',
 status TEXT NOT NULL, reason TEXT NOT NULL, created_at INTEGER NOT NULL,
 updated_at INTEGER NOT NULL, reviewed_by TEXT, review_notes TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS compliance_evidence (
 evidence_id TEXT PRIMARY KEY, report_id TEXT, entity_id TEXT NOT NULL,
 entity_type TEXT NOT NULL, evidence_type TEXT NOT NULL, reference TEXT NOT NULL,
 captured_at INTEGER NOT NULL, captured_by TEXT NOT NULL, notes TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS compliance_appeals (
 appeal_id TEXT PRIMARY KEY, entity_id TEXT NOT NULL, entity_type TEXT NOT NULL,
 submitted_by TEXT NOT NULL, statement TEXT NOT NULL, status TEXT NOT NULL,
 created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
 decided_by TEXT, decision_notes TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS safety_controls (
 name TEXT PRIMARY KEY, enabled INTEGER NOT NULL, actor_id TEXT NOT NULL,
 reason TEXT NOT NULL, updated_at INTEGER NOT NULL
);
"""


class _ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


class EnforcementError(ValueError):
    pass


class EnforcementStore:
    def __init__(self, path: str | Path):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    def _connect(self):
        conn = sqlite3.connect(self.path, timeout=30, isolation_level=None, factory=_ClosingConnection)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    def _now(self):
        return int(time.time())

    def _id(self, prefix):
        return f"{prefix}_{secrets.token_hex(10)}"

    def register(self, entity_id, entity_type, *, actor_id="system", reason=""):
        with self._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO enforcement_entities(entity_id,entity_type,state,actor_id,updated_at) VALUES(?,?,?,?,?)",
                (str(entity_id), entity_type, "ACTIVE", str(actor_id), self._now()),
            )
        return self.get(entity_id, entity_type)

    def get(self, entity_id, entity_type) -> dict:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM enforcement_entities WHERE entity_id=? AND entity_type=?",
                               (str(entity_id), entity_type)).fetchone()
        if row:
            result = dict(row)
            if result["expires_at"] and result["expires_at"] <= self._now() and result["state"] in {"SUSPENDED", "RESTRICTED"}:
                self.restore(entity_id, entity_type, actor_id="system", reason="temporary enforcement expired")
                return self.get(entity_id, entity_type)
            return result
        return self.register(entity_id, entity_type)

    def enforce(self, entity_id, entity_type, state, *, actor_id, reason,
                related_report_id=None, evidence_ids=(), duration_seconds=None, notes="") -> dict:
        state = state.upper()
        if state not in STATES or state == "ACTIVE":
            raise EnforcementError("use restore() for ACTIVE")
        if not reason.strip():
            raise EnforcementError("enforcement reason is required")
        current = self.get(entity_id, entity_type)
        now = self._now()
        expires = now + int(duration_seconds) if duration_seconds else None
        event_id = self._id("enf")
        with self._connect() as conn:
            conn.execute("UPDATE enforcement_entities SET state=?,reason=?,actor_id=?,updated_at=?,expires_at=?,notes=? WHERE entity_id=? AND entity_type=?",
                         (state, reason, str(actor_id), now, expires, notes, str(entity_id), entity_type))
            conn.execute("INSERT INTO enforcement_events(event_id,entity_id,entity_type,action,previous_state,new_state,actor_id,reason,related_report_id,evidence_json,duration_seconds,created_at,notes) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         (event_id, str(entity_id), entity_type, state, current["state"], state, str(actor_id), reason,
                          related_report_id, json.dumps(list(evidence_ids)), duration_seconds, now, notes))
        return self.get(entity_id, entity_type)

    def restore(self, entity_id, entity_type, *, actor_id, reason, notes="") -> dict:
        current = self.get(entity_id, entity_type)
        if not reason.strip():
            raise EnforcementError("restore reason is required")
        now = self._now()
        with self._connect() as conn:
            conn.execute("UPDATE enforcement_entities SET state='ACTIVE',reason=?,actor_id=?,updated_at=?,expires_at=NULL,notes=? WHERE entity_id=? AND entity_type=?",
                         (reason, str(actor_id), now, notes, str(entity_id), entity_type))
            conn.execute("INSERT INTO enforcement_events(event_id,entity_id,entity_type,action,previous_state,new_state,actor_id,reason,created_at,notes) VALUES(?,?,?,?,?,?,?,?,?,?)",
                         (self._id("enf"), str(entity_id), entity_type, "RESTORE", current["state"], "ACTIVE", str(actor_id), reason, now, notes))
        return self.get(entity_id, entity_type)

    def allowed(self, entity_id, entity_type, capability: str) -> bool:
        if capability not in CAPABILITIES:
            raise EnforcementError("unknown capability")
        state = self.get(entity_id, entity_type)["state"]
        if self.emergency_enabled("safe_mode"):
            return False
        if state in {"BANNED", "REMOVED", "SUSPENDED"}:
            return False
        if state == "RESTRICTED" and capability in {"tasks", "distribution", "partnerships", "rewards", "campaigns", "automated_posting"}:
            return False
        return True

    def report(self, reporter_id, entity_id, entity_type, reason, *, subject="") -> dict:
        if not reason.strip():
            raise EnforcementError("report reason is required")
        now = self._now()
        report_id = self._id("rpt")
        with self._connect() as conn:
            conn.execute("INSERT INTO compliance_reports(report_id,reporter_id,entity_id,entity_type,subject,status,reason,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                         (report_id, str(reporter_id), str(entity_id), entity_type, subject, "PENDING", reason, now, now))
        return self.report_by_id(report_id)

    def report_by_id(self, report_id):
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM compliance_reports WHERE report_id=?", (report_id,)).fetchone()
        return dict(row) if row else None

    def review_report(self, report_id, status, *, reviewer_id, notes="") -> dict:
        status = status.upper()
        if status not in REPORT_STATES or status == "PENDING":
            raise EnforcementError("invalid review status")
        now = self._now()
        with self._connect() as conn:
            cur = conn.execute("UPDATE compliance_reports SET status=?,updated_at=?,reviewed_by=?,review_notes=? WHERE report_id=?",
                               (status, now, str(reviewer_id), notes, report_id))
            if cur.rowcount != 1:
                raise EnforcementError("report not found")
        return self.report_by_id(report_id)

    def list_reports(self, status=None, *, entity_id=None, entity_type=None, limit=50) -> list[dict]:
        query = "SELECT * FROM compliance_reports"
        clauses, params = [], []
        if status:
            clauses.append("status=?"); params.append(status.upper())
        if entity_id is not None:
            clauses.append("entity_id=?"); params.append(str(entity_id))
        if entity_type:
            clauses.append("entity_type=?"); params.append(entity_type)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY created_at DESC, rowid DESC LIMIT ?"
        params.append(max(1, min(int(limit), 500)))
        with self._connect() as conn:
            return [dict(row) for row in conn.execute(query, params).fetchall()]

    def evidence_for(self, entity_id, entity_type, *, report_id=None, limit=100) -> list[dict]:
        with self._connect() as conn:
            if report_id:
                rows = conn.execute("SELECT * FROM compliance_evidence WHERE report_id=? ORDER BY captured_at DESC LIMIT ?",
                                    (report_id, max(1, min(int(limit), 500)))).fetchall()
            else:
                rows = conn.execute("SELECT * FROM compliance_evidence WHERE entity_id=? AND entity_type=? ORDER BY captured_at DESC LIMIT ?",
                                    (str(entity_id), entity_type, max(1, min(int(limit), 500)))).fetchall()
        return [dict(row) for row in rows]

    # -- appeals ---------------------------------------------------------
    # An appeal is the subject's side of the record. Filing one never changes
    # enforcement state; only an explicit human decision (and a separate
    # restore()) does.
    def appeal(self, entity_id, entity_type, *, submitted_by, statement) -> dict:
        if not statement.strip():
            raise EnforcementError("appeal statement is required")
        if self.get(entity_id, entity_type)["state"] == "ACTIVE":
            raise EnforcementError("nothing to appeal: entity is active")
        if self.open_appeal(entity_id, entity_type):
            raise EnforcementError("an appeal is already open for this entity")
        now = self._now()
        appeal_id = self._id("apl")
        with self._connect() as conn:
            conn.execute("INSERT INTO compliance_appeals(appeal_id,entity_id,entity_type,submitted_by,statement,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                         (appeal_id, str(entity_id), entity_type, str(submitted_by), statement.strip(), "OPEN", now, now))
        return self.appeal_by_id(appeal_id)

    def appeal_by_id(self, appeal_id):
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM compliance_appeals WHERE appeal_id=?", (appeal_id,)).fetchone()
        return dict(row) if row else None

    def open_appeal(self, entity_id, entity_type):
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM compliance_appeals WHERE entity_id=? AND entity_type=? AND status IN ('OPEN','UNDER_REVIEW') ORDER BY created_at DESC LIMIT 1",
                               (str(entity_id), entity_type)).fetchone()
        return dict(row) if row else None

    def list_appeals(self, status=None, limit=50) -> list[dict]:
        with self._connect() as conn:
            if status:
                rows = conn.execute("SELECT * FROM compliance_appeals WHERE status=? ORDER BY created_at DESC, rowid DESC LIMIT ?",
                                    (status.upper(), max(1, min(int(limit), 500)))).fetchall()
            else:
                rows = conn.execute("SELECT * FROM compliance_appeals ORDER BY created_at DESC, rowid DESC LIMIT ?",
                                    (max(1, min(int(limit), 500)),)).fetchall()
        return [dict(row) for row in rows]

    def decide_appeal(self, appeal_id, status, *, decided_by, notes="") -> dict:
        status = status.upper()
        if status not in APPEAL_STATES or status == "OPEN":
            raise EnforcementError("invalid appeal decision")
        appeal = self.appeal_by_id(appeal_id)
        if not appeal:
            raise EnforcementError("appeal not found")
        if appeal["status"] not in {"OPEN", "UNDER_REVIEW"}:
            raise EnforcementError("appeal is already closed")
        with self._connect() as conn:
            conn.execute("UPDATE compliance_appeals SET status=?,updated_at=?,decided_by=?,decision_notes=? WHERE appeal_id=?",
                         (status, self._now(), str(decided_by), notes, appeal_id))
        if status == "OVERTURNED":
            self.restore(appeal["entity_id"], appeal["entity_type"], actor_id=decided_by,
                         reason=f"appeal {appeal_id} overturned", notes=notes)
        return self.appeal_by_id(appeal_id)

    def add_evidence(self, entity_id, entity_type, evidence_type, reference, *, captured_by, report_id=None, notes="") -> dict:
        if not reference.strip():
            raise EnforcementError("evidence reference is required")
        item = {"evidence_id": self._id("evd"), "report_id": report_id, "entity_id": str(entity_id), "entity_type": entity_type,
                "evidence_type": evidence_type, "reference": reference, "captured_at": self._now(), "captured_by": str(captured_by), "notes": notes}
        with self._connect() as conn:
            conn.execute("INSERT INTO compliance_evidence(evidence_id,report_id,entity_id,entity_type,evidence_type,reference,captured_at,captured_by,notes) VALUES(?,?,?,?,?,?,?,?,?)",
                         tuple(item.values()))
        return item

    def emergency_enabled(self, name="safe_mode") -> bool:
        with self._connect() as conn:
            row = conn.execute("SELECT enabled FROM safety_controls WHERE name=?", (name,)).fetchone()
        return bool(row and row[0])

    def set_emergency(self, enabled: bool, *, actor_id, reason) -> dict:
        if not reason.strip():
            raise EnforcementError("safe-mode reason is required")
        with self._connect() as conn:
            conn.execute("INSERT OR REPLACE INTO safety_controls(name,enabled,actor_id,reason,updated_at) VALUES(?,?,?,?,?)",
                         ("safe_mode", int(bool(enabled)), str(actor_id), reason, self._now()))
        return {"name": "safe_mode", "enabled": bool(enabled), "actor_id": str(actor_id), "reason": reason}

    def safety_status(self) -> dict:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM safety_controls WHERE name='safe_mode'").fetchone()
            pending = conn.execute("SELECT COUNT(*) FROM compliance_reports WHERE status IN ('PENDING','UNDER_REVIEW')").fetchone()[0]
            appeals = conn.execute("SELECT COUNT(*) FROM compliance_appeals WHERE status IN ('OPEN','UNDER_REVIEW')").fetchone()[0]
            states = {r[0]: r[1] for r in conn.execute("SELECT state, COUNT(*) FROM enforcement_entities GROUP BY state").fetchall()}
        return {"safe_mode": dict(row) if row else {"name": "safe_mode", "enabled": 0},
                "open_reports": int(pending), "open_appeals": int(appeals), "entities_by_state": states}

    def timeline(self, entity_id, entity_type, limit=100) -> list[dict]:
        with self._connect() as conn:
            return [dict(row) for row in conn.execute("SELECT * FROM enforcement_events WHERE entity_id=? AND entity_type=? ORDER BY created_at DESC, rowid DESC LIMIT ?",
                                                       (str(entity_id), entity_type, max(1, min(int(limit), 500)))).fetchall()]
