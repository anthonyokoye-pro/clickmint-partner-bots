"""Persistent product features shared by the two network bots.

The Telegram handlers are intentionally thin; these objects keep the rules
atomic and offline-testable.  Chat ids, rather than usernames, are the stable
identity for destinations.  Every feature is scoped to a bot store.
"""
from __future__ import annotations

import secrets
import time
from collections import Counter


def now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())


class ReferralLedger:
    """A referral earns only after the referred member completes a real share."""
    def __init__(self, store, ledger, transactional=None):
        self.store, self.ledger = store, ledger
        self.transactional = transactional or getattr(ledger, "tx", None)
        self.key = "referrals"
        if not isinstance(store.get(self.key), dict):
            store[self.key] = {"codes": {}, "by_user": {}}
            store.sync()

    def create_code(self, owner_id) -> str:
        if self.transactional is not None:
            code = self.transactional.create_referral_code(owner_id)
            data = self.store[self.key]
            data.setdefault("codes", {})[code] = {"owner_id": int(owner_id), "uses": 0}
            self.store[self.key] = data; self.store.sync()
            return code
        code = secrets.token_urlsafe(6)
        data = self.store[self.key]
        data.setdefault("codes", {})[code] = {"owner_id": int(owner_id), "uses": 0}
        self.store[self.key] = data; self.store.sync()
        return code

    def attach(self, referred_id, code: str) -> bool:
        if self.transactional is not None:
            attached = self.transactional.attach_referral(referred_id, code)
            if not attached:
                return False
            data = self.store[self.key]
            row = data.setdefault("codes", {}).setdefault(code, {"uses": 0})
            ref = self.transactional.referral(code) if hasattr(self.transactional, "referral") else None
            row["uses"] = int(row.get("uses", 0)) + 1
            data.setdefault("by_user", {})[str(referred_id)] = {
                "referrer_id": int(ref["referrer_id"]) if ref and str(ref["referrer_id"]).isdigit() else (ref["referrer_id"] if ref else row.get("owner_id")),
                "completed": False,
            }
            self.store[self.key] = data; self.store.sync()
            return True
        data = self.store[self.key]; row = data.setdefault("codes", {}).get(code)
        key = str(referred_id)
        if not row or key in data.setdefault("by_user", {}):
            return False
        if int(row["owner_id"]) == int(referred_id):
            return False
        data["by_user"][key] = {"referrer_id": row["owner_id"], "completed": False}
        row["uses"] += 1
        self.store[self.key] = data; self.store.sync(); return True

    def complete_forward(self, referred_id) -> bool:
        data = self.store[self.key]; row = data.setdefault("by_user", {}).get(str(referred_id))
        if not row or row.get("completed"):
            return False
        # A referral bonus is minted only at this point, never at link creation.
        if self.transactional is not None:
            activity_id = f"forward:{referred_id}"
            if not self.transactional.qualify_referral(referred_id, activity_id=activity_id, reward_amount=1):
                return False
            if not self.transactional.confirm_referral_reward(referred_id, activity_id=activity_id):
                return False
            ref = self.transactional.attached_referral(referred_id)
            row["referrer_id"] = ref["referrer_id"] if ref else row.get("referrer_id")
        else:
            self.ledger.earn("@" + str(row["referrer_id"]), 1)
        row["completed"] = True; row["completed_at"] = now()
        self.store[self.key] = data; self.store.sync(); return True

    def stats(self, owner_id) -> dict:
        rows = [r for r in self.store[self.key].get("by_user", {}).values()
                if int(r.get("referrer_id", -1)) == int(owner_id)]
        return {"referred": len(rows), "completed": sum(bool(r.get("completed")) for r in rows),
                "pending": sum(not r.get("completed") for r in rows)}


class AvailablePostQueue:
    """Persistent available-post queue with owner-first ordering and claims."""
    def __init__(self, store, key="available_posts"):
        self.store, self.key = store, key
        if not isinstance(store.get(key), list):
            store[key] = []; store.sync()

    def add(self, post: dict, owner: bool = False) -> dict:
        item = {**post, "id": post.get("id") or secrets.token_hex(6),
                "owner": bool(owner), "created_at": post.get("created_at", now()),
                "claimed_by": None, "claimed_at": None}
        rows = self.store[self.key]; rows.append(item)
        self.store[self.key] = rows; self.store.sync(); return item

    def available(self, recipient_id=None, limit: int | None = None) -> list[dict]:
        rows = [r for r in self.store[self.key] if not r.get("claimed_by")]
        rows.sort(key=lambda r: (not r.get("owner", False), r.get("created_at", "")))
        return rows if limit is None else rows[:limit]

    def claim(self, post_id: str, recipient_id) -> dict | None:
        for row in self.store[self.key]:
            if row.get("id") == post_id and not row.get("claimed_by"):
                row["claimed_by"] = int(recipient_id); row["claimed_at"] = now()
                self.store[self.key] = self.store[self.key]; self.store.sync(); return row
        return None

    def count(self, recipient_id=None) -> int:
        return len(self.available(recipient_id))

    def retain_last(self, count: int = 1000) -> None:
        rows = self.store[self.key]
        # Claimed records remain audit history; only unclaimed old records are pruned.
        unclaimed = [r for r in rows if not r.get("claimed_by")]
        keep = set(id(r) for r in unclaimed[-count:])
        self.store[self.key] = [r for r in rows if r.get("claimed_by") or id(r) in keep]
        self.store.sync()


class BubbleNotifier:
    """Tracks one replaceable bubble per recipient instead of one message/post."""
    def __init__(self, store, key="availability_bubbles"):
        self.store, self.key = store, key
        if not isinstance(store.get(key), dict):
            store[key] = {}; store.sync()

    def set(self, recipient_id, count: int, message_id=None) -> dict:
        row = {"count": max(0, int(count)), "message_id": message_id, "updated_at": now()}
        data = self.store[self.key]
        data[str(recipient_id)] = row
        self.store[self.key] = data
        self.store.sync(); return row

    def get(self, recipient_id) -> dict:
        return self.store[self.key].get(str(recipient_id), {"count": 0, "message_id": None})


class AnnouncementBoard:
    def __init__(self, store, key="announcements"):
        self.store, self.key = store, key
        if not isinstance(store.get(key), list):
            store[key] = []; store.sync()

    def publish(self, author_id, text: str, categories=None) -> dict:
        item = {"id": secrets.token_hex(6), "author_id": int(author_id), "text": text,
                "categories": list(categories or []), "created_at": now()}
        rows = self.store[self.key]
        rows.append(item); self.store[self.key] = rows
        self.store.sync(); return item

    def recent(self, category=None, limit=20):
        rows = self.store[self.key]
        if category:
            rows = [r for r in rows if not r.get("categories") or category in r["categories"]]
        return rows[-limit:]


class RankVisibility:
    def __init__(self, store, key="settings"):
        self.store, self.key = store, key
        if not isinstance(store.get(key), dict):
            store[key] = {"public_rank": False}; store.sync()

    def public(self) -> bool:
        return bool(self.store[self.key].get("public_rank", False))

    def set_public(self, owner_id, value: bool, configured_owner_id) -> bool:
        if int(owner_id) != int(configured_owner_id):
            raise PermissionError("owner only")
        settings = self.store[self.key]
        settings["public_rank"] = bool(value)
        self.store[self.key] = settings; self.store.sync(); return bool(value)


class StatsBook:
    """Real observations only; missing values stay missing, never fabricated."""
    def __init__(self, store, key="channel_stats"):
        self.store, self.key = store, key
        if not isinstance(store.get(key), dict):
            store[key] = {}; store.sync()

    def record(self, channel_id, *, subscribers=None, views=None, reactions=None,
               forwards=None, observed_at=None) -> dict:
        row = self.store[self.key].setdefault(str(channel_id), {"samples": []})
        sample = {"observed_at": observed_at or now()}
        for k, v in (("subscribers", subscribers), ("views", views),
                     ("reactions", reactions), ("forwards", forwards)):
            if v is not None:
                if not isinstance(v, (int, float)) or v < 0:
                    raise ValueError(f"invalid {k}")
                sample[k] = v
        row.setdefault("samples", []).append(sample)
        row["latest"] = sample
        self.store[self.key] = self.store[self.key]
        self.store.sync(); return sample

    def latest(self, channel_id) -> dict:
        return self.store[self.key].get(str(channel_id), {}).get("latest", {})

    def provider(self, username):
        row = self.latest(username)
        return {"subs": row.get("subscribers"), "views": row.get("views"),
                "reactions": row.get("reactions"), "forwards": row.get("forwards")}


def category_counts(rows: list[dict]) -> dict[str, int]:
    counts = Counter()
    for row in rows:
        for cat in row.get("categories", []):
            counts[cat] += 1
    return dict(counts)


class ReroutePlanner:
    """Moves an unclaimed offer to related categories after a 12-hour timeout."""
    WINDOW_SECONDS = 12 * 60 * 60

    def __init__(self, store, key="reroute_queue"):
        self.store, self.key = store, key
        if not isinstance(store.get(key), list):
            store[key] = []; store.sync()

    def add(self, post: dict) -> dict:
        item = {**post, "created_at_epoch": post.get("created_at_epoch", time.time()),
                "rerouted": False, "related_categories": list(post.get("related_categories", []))}
        self.store[self.key].append(item); self.store[self.key] = self.store[self.key]
        self.store.sync(); return item

    def due(self, timestamp: float | None = None) -> list[dict]:
        stamp = timestamp or time.time()
        return [r for r in self.store[self.key]
                if not r.get("rerouted") and stamp - float(r.get("created_at_epoch", stamp)) >= self.WINDOW_SECONDS]

    def mark_rerouted(self, post_id: str) -> bool:
        for row in self.store[self.key]:
            if row.get("id") == post_id:
                row["rerouted"] = True; row["rerouted_at"] = now()
                self.store[self.key] = self.store[self.key]; self.store.sync(); return True
        return False
