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
        # Every timestamp here is UTC — `tz_note` only labels the display.
        self.store = store
        self.key = key
        self.tz_note = tz_note
        if self.store.get(self.key) is None:
            self.store[self.key] = []
            self.store.sync()

    def schedule(self, at: float, target: str, sender: str,
                 from_chat_id=None, from_message_id=None,
                 post_type="General", **source_fields) -> dict:
        """Add a posting to fire at unix time `at`. Returns its record.
        Extra keyword fields (origin_chat_id/origin_message_id/origin_kind) are
        persisted so relay.resolve can pick a legal route at fire time."""
        rec = {
            "id": uuid.uuid4().hex[:10],
            "at": float(at),
            "target": target,
            "sender": sender,
            "post_type": post_type,
            "from_chat_id": from_chat_id,
            "from_message_id": from_message_id,
            **{k: v for k, v in source_fields.items() if k.startswith("origin_")},
            "fire_once": True,
            "done": False,
            "attempts": 0,
            "scheduled_ts": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()),
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

    def fail(self, rec_id: str, note: str = "", max_attempts: int = 3) -> bool:
        """Record a failed delivery attempt.

        A transient error (target briefly unreachable, rate limit) used to mark
        the posting done and silently drop an agreed partner slot. Now it is
        retried on the next loop and only given up after `max_attempts`.
        Returns True when the record has been given up on.
        """
        items = self.store[self.key]
        gave_up = False
        for r in items:
            if r.get("id") == rec_id:
                r["attempts"] = int(r.get("attempts", 0)) + 1
                r["last_error"] = note
                if r["attempts"] >= max_attempts:
                    r["done"] = True
                    r["posted_ts"] = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
                    r["note"] = f"gave up after {r['attempts']} attempts: {note}"
                    gave_up = True
                break
        self.store[self.key] = items
        self.store.sync()
        return gave_up

    def mark_done(self, rec_id: str, note: str = "") -> None:
        items = self.store[self.key]
        for r in items:
            if r.get("id") == rec_id:
                r["done"] = True
                r["posted_ts"] = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
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
            lines.append(f"#{r['id']} @ {time.strftime('%Y-%m-%d %H:%M', time.gmtime(r['at']))} "
                         f"({self.tz_note}) -> {r['target']} from {r['sender']} type={r['post_type']}")
        return "\n".join(lines)

    def due_text(self) -> str:
        d = self.due()
        if not d:
            return "Nothing due right now."
        return f"{len(d)} posting(s) due." + "\n".join(
            f"  #{r['id']} -> {r['target']} from {r['sender']}" for r in d)
