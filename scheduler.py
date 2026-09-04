"""Agreed-time scheduler for DIRECT-mode delivery.

Holds postings that should be posted at a specific future time, and fires the
ones whose time has arrived. Persisted to JsonStore so it survives restarts.

Made this a scheduler loop (rather than relying on a Bot API schedule_date flag)
because it's the version-independent, reliable way real scheduling bots work.
The posts themselves are delivered by caller-provided async `poster` (which will
call forwardMessage to keep attribution).

A delivery record:
  id, at (unix ts), target (channel), sender, post_type,
  from_chat_id, from_message_id, fire_once, done
"""
from __future__ import annotations
import time
import uuid


class Scheduler:
    def __init__(self, store, key="scheduled", tz_note="UTC"):
        self.store = store
        self.key = key
        self.tz_note = tz_note
        if self.store.get(self.key) is None:
            self.store[self.key] = []
            self.store.sync()

    def schedule(self, at: float, target: str, sender: str,
                 from_chat_id=None, from_message_id=None,
                 post_type="General") -> dict:
        """Add a posting to fire at unix time `at`. Returns its record."""
        rec = {
            "id": uuid.uuid4().hex[:10],
            "at": float(at),
            "target": target,
            "sender": sender,
            "post_type": post_type,
            "from_chat_id": from_chat_id,
            "from_message_id": from_message_id,
            "fire_once": True,
            "done": False,
            "scheduled_ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        items = self.store[self.key]
        items.append(rec)
        self.store[self.key] = items
        self.store.sync()
        return rec

    def due(self, now: float | None = None) -> list:
        """Return not-yet-done deliveries whose time has arrived, oldest first."""
        now = now if now is not None else time.time()
        items = self.store.get(self.key, [])
        due = [r for r in items if not r.get("done") and r["at"] <= now]
        due.sort(key=lambda r: r["at"])
        return due

    def future(self) -> list:
        now = time.time()
        items = self.store.get(self.key, [])
        return sorted([r for r in items if not r.get("done") and r["at"] > now],
                      key=lambda r: r["at"])

    def pending_count(self) -> int:
        items = self.store.get(self.key, [])
        return sum(1 for r in items if not r.get("done"))

    def done_count(self) -> int:
        return sum(1 for r in self.store.get(self.key, []) if r.get("done"))

    def mark_done(self, rec_id: str, note: str = "") -> None:
        items = self.store[self.key]
        for r in items:
            if r.get("id") == rec_id:
                r["done"] = True
                r["posted_ts"] = time.strftime("%Y-%m-%d %H:%M:%S")
                r["note"] = note
                break
        self.store[self.key] = items
        self.store.sync()

    def cancel(self, rec_id: str) -> bool:
        items = self.store[self.key]
        before = len(items)
        items = [r for r in items if r.get("id") != rec_id]
        removed = len(items) != before
        self.store[self.key] = items
        self.store.sync()
        return removed

    def upcoming_text(self) -> str:
        f = self.future()
        if not f:
            return "No scheduled posts."
        lines = [f"Scheduled direct posts ({len(f)}):", ""]
        for r in f:
            lines.append(f"#{r['id']} @ {time.strftime('%Y-%m-%d %H:%M', time.localtime(r['at']))} "
                         f"({self.tz_note}) -> {r['target']} from {r['sender']} type={r['post_type']}")
        return "\n".join(lines)

    def due_text(self) -> str:
        d = self.due()
        if not d:
            return "Nothing due right now."
        return f"{len(d)} posting(s) due." + "\n".join(
            f"  #{r['id']} -> {r['target']} from {r['sender']}" for r in d)
