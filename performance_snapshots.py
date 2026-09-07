"""Durable, explainable credibility snapshots for marketplace destinations."""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from credibility import CredibilityMetrics, calculate_credibility

SCHEMA = """
CREATE TABLE IF NOT EXISTS task_observations (
    observation_id INTEGER PRIMARY KEY AUTOINCREMENT,
    destination_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    category TEXT NOT NULL,
    success INTEGER NOT NULL CHECK(success IN (0,1)),
    observed_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_observations_destination
    ON task_observations(destination_id, observed_at);
CREATE TABLE IF NOT EXISTS credibility_snapshots (
    snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
    destination_id TEXT NOT NULL,
    score REAL NOT NULL,
    tier TEXT NOT NULL,
    confidence REAL NOT NULL,
    reliability REAL NOT NULL,
    consistency REAL NOT NULL,
    sample_size INTEGER NOT NULL,
    components TEXT NOT NULL,
    reason TEXT NOT NULL,
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_snapshots_destination
    ON credibility_snapshots(destination_id, created_at);
CREATE TABLE IF NOT EXISTS destination_controls (
    destination_id TEXT PRIMARY KEY,
    cooldown_until INTEGER NOT NULL DEFAULT 0,
    reason TEXT NOT NULL DEFAULT '',
    updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS performance_interventions (
    intervention_id INTEGER PRIMARY KEY AUTOINCREMENT,
    destination_id TEXT NOT NULL,
    action TEXT NOT NULL,
    reason TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_interventions_destination
    ON performance_interventions(destination_id, created_at);
"""


class PerformanceSnapshotRepository:
    def __init__(self, path: str | Path):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    def _connect(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def record_task_outcome(self, *, destination_id, user_id, category: str,
                            success: bool, observed_at: int | None = None) -> dict:
        now = int(observed_at or time.time())
        destination_id = str(destination_id)
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO task_observations(destination_id,user_id,category,success,observed_at) VALUES(?,?,?,?,?)",
                (destination_id, str(user_id), category, int(bool(success)), now),
            )
            row = conn.execute(
                "SELECT COUNT(*) AS claimed, COALESCE(SUM(success),0) AS successful "
                "FROM task_observations WHERE destination_id=?", (destination_id,),
            ).fetchone()
            claimed, successful = int(row["claimed"]), int(row["successful"])
            result = calculate_credibility(CredibilityMetrics(
                successful_tasks=successful, claimed_tasks=claimed,
                sample_size=claimed, quality_score=1.0, safety_score=1.0,
            ))
            components = {
                "successful_tasks": successful,
                "claimed_tasks": claimed,
                "quality_score": 1.0,
                "safety_score": 1.0,
            }
            recent = conn.execute(
                "SELECT success FROM task_observations WHERE destination_id=? "
                "ORDER BY observed_at DESC, observation_id DESC LIMIT 5", (destination_id,)
            ).fetchall()
            failures = sum(1 for item in recent if not item["success"])
            if len(recent) >= 3 and failures >= 3:
                cooldown_until = now + 3600
                control_reason = "repeated task execution failures"
            elif success:
                cooldown_until = 0
                control_reason = "successful outcome restored eligibility"
            else:
                cooldown_until = 0
                control_reason = "failure observed; monitoring"
            existing_control = conn.execute(
                "SELECT cooldown_until FROM destination_controls WHERE destination_id=?", (destination_id,)
            ).fetchone()
            was_suspended = bool(existing_control and int(existing_control["cooldown_until"]) > now)
            if cooldown_until > now and not was_suspended:
                conn.execute(
                    "INSERT INTO performance_interventions(destination_id,action,reason,actor_id,created_at) VALUES(?,?,?,?,?)",
                    (destination_id, "COOLDOWN_SET", control_reason, "system", now),
                )
            conn.execute(
                "INSERT INTO destination_controls(destination_id,cooldown_until,reason,updated_at) VALUES(?,?,?,?) "
                "ON CONFLICT(destination_id) DO UPDATE SET cooldown_until=excluded.cooldown_until, reason=excluded.reason, updated_at=excluded.updated_at",
                (destination_id, cooldown_until, control_reason, now),
            )
            conn.execute(
                "INSERT INTO credibility_snapshots(destination_id,score,tier,confidence,reliability,consistency,sample_size,components,reason,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (destination_id, result.score, result.tier, result.confidence,
                 result.reliability, result.consistency, claimed,
                 json.dumps(components, sort_keys=True),
                 "task outcome observed", now),
            )
            return self._snapshot(conn.execute(
                "SELECT * FROM credibility_snapshots WHERE destination_id=? ORDER BY snapshot_id DESC LIMIT 1",
                (destination_id,),
            ).fetchone())

    def _snapshot(self, row):
        if not row:
            return None
        result = dict(row)
        result["components"] = json.loads(result["components"] or "{}")
        return result

    def current(self, destination_id) -> dict | None:
        with self._connect() as conn:
            return self._snapshot(conn.execute(
                "SELECT * FROM credibility_snapshots WHERE destination_id=? ORDER BY snapshot_id DESC LIMIT 1",
                (str(destination_id),),
            ).fetchone())

    def history(self, destination_id, limit: int = 20) -> list[dict]:
        with self._connect() as conn:
            return [self._snapshot(row) for row in conn.execute(
                "SELECT * FROM credibility_snapshots WHERE destination_id=? ORDER BY snapshot_id DESC LIMIT ?",
                (str(destination_id), max(1, int(limit))),
            ).fetchall()]

    def suspended_destinations(self, limit: int = 50) -> list[dict]:
        now = int(time.time())
        with self._connect() as conn:
            return [dict(row) for row in conn.execute(
                "SELECT * FROM destination_controls WHERE cooldown_until>? "
                "ORDER BY cooldown_until ASC LIMIT ?", (now, max(1, int(limit)))
            ).fetchall()]

    def clear_cooldown(self, destination_id, *, reason: str = "manual intervention", now: int | None = None) -> bool:
        timestamp = int(now or time.time())
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE destination_controls SET cooldown_until=0, reason=?, updated_at=? "
                "WHERE destination_id=? AND cooldown_until>?",
                (reason, timestamp, str(destination_id), timestamp),
            )
            if cur.rowcount == 1:
                conn.execute(
                    "INSERT INTO performance_interventions(destination_id,action,reason,actor_id,created_at) VALUES(?,?,?,?,?)",
                    (str(destination_id), "COOLDOWN_CLEARED", reason, "admin", timestamp),
                )
            return cur.rowcount == 1

    def intervention_history(self, destination_id, limit: int = 20) -> list[dict]:
        with self._connect() as conn:
            return [dict(row) for row in conn.execute(
                "SELECT * FROM performance_interventions WHERE destination_id=? "
                "ORDER BY created_at DESC, intervention_id DESC LIMIT ?",
                (str(destination_id), max(1, int(limit))),
            ).fetchall()]

    def control(self, destination_id) -> dict:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM destination_controls WHERE destination_id=?", (str(destination_id),)).fetchone()
        return dict(row) if row else {"destination_id": str(destination_id), "cooldown_until": 0, "reason": "no control"}

    def is_suspended(self, destination_id, now: int | None = None) -> bool:
        return int(self.control(destination_id).get("cooldown_until", 0)) > int(now or time.time())

    def explain(self, destination_id) -> dict:
        current = self.current(destination_id)
        if not current:
            return {"status": "PROVISIONAL", "message": "No completed task observations yet."}
        return {
            "status": current["tier"],
            "score": current["score"],
            "confidence": current["confidence"],
            "reliability": current["reliability"],
            "sample_size": current["sample_size"],
            "components": current["components"],
            "message": "Score is provisional until more genuine task outcomes are observed.",
        }
