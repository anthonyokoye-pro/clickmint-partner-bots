"""Persistent registry for a user's channels and groups.

The reward and partnership ledgers continue to store performance per channel.
This registry adds the missing ownership layer so one Telegram user can manage
multiple destinations without overwriting another destination's status.
"""
from __future__ import annotations

import time


class ChannelRegistry:
    MAX_CATEGORIES = 3
    KINDS = {"channel", "group"}

    def __init__(self, store, key: str = "managed_channels"):
        self.store = store
        self.key = key
        if not isinstance(store.get(key), dict):
            store[key] = {}
            store.sync()

    def _items(self) -> dict:
        value = self.store.get(self.key)
        return value if isinstance(value, dict) else {}

    def add(self, owner_id, chat_id, username: str, kind: str,
            categories: list[str] | None = None, size: int = 0,
            bot_added: bool = False) -> dict:
        if kind not in self.KINDS:
            raise ValueError("kind must be channel or group")
        cats = list(dict.fromkeys(categories or ["General"]))
        if not cats or len(cats) > self.MAX_CATEGORIES:
            raise ValueError("choose between one and three categories")
        if any(not isinstance(c, str) or not c.strip() for c in cats):
            raise ValueError("categories must be non-empty strings")
        key = str(chat_id or username).strip()
        if not key:
            raise ValueError("a chat id or username is required")
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        old = self._items().get(key, {})
        row = {
            **old, "chat_id": chat_id, "username": username,
            "owner_id": int(owner_id), "kind": kind, "categories": cats,
            "size": max(0, int(size)), "bot_added": bool(bot_added),
            "status": old.get("status", "ACTIVE" if bot_added else "REGISTERED"),
            "verified_state": old.get("verified_state", "VERIFIED" if bot_added else "REGISTERED"),
            "band": old.get("band", "C" if not bot_added else "B"),
            "created_at": old.get("created_at", now), "updated_at": now,
        }
        items = self._items(); items[key] = row
        self.store[self.key] = items; self.store.sync()
        return row

    def update(self, owner_id, chat_id, **changes) -> dict:
        key = str(chat_id)
        row = self._items().get(key)
        if not row or int(row.get("owner_id")) != int(owner_id):
            raise KeyError("channel not found or not owned by this user")
        allowed = {"username", "categories", "size", "bot_added", "status", "band",
                   "verified_state", "telegram_member_count", "telegram_member_count_source",
                   "telegram_member_count_checked_at", "telegram_chat_type", "telegram_bot_id", "canonical_chat_id",
                   "permissions", "verification_reasons", "last_verified_at"}
        unknown = set(changes) - allowed
        if unknown:
            raise ValueError(f"unsupported fields: {sorted(unknown)}")
        if "categories" in changes:
            cats = list(dict.fromkeys(changes["categories"]))
            if not 1 <= len(cats) <= self.MAX_CATEGORIES:
                raise ValueError("choose between one and three categories")
            changes["categories"] = cats
        row.update(changes); row["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        self.store[self.key] = self._items(); self.store.sync()
        return row

    def remove(self, owner_id, chat_id) -> bool:
        key = str(chat_id); row = self._items().get(key)
        if not row or int(row.get("owner_id")) != int(owner_id):
            return False
        items = self._items(); del items[key]
        self.store[self.key] = items; self.store.sync(); return True

    def mine(self, owner_id, kind: str | None = None) -> list[dict]:
        rows = [r for r in self._items().values() if int(r.get("owner_id", -1)) == int(owner_id)]
        return [r for r in rows if kind is None or r.get("kind") == kind]

    def get(self, chat_id) -> dict | None:
        return self._items().get(str(chat_id))

    def participation_allowed(self, chat_id) -> bool:
        row = self.get(chat_id)
        return bool(row and row.get("verified_state") == "VERIFIED"
                    and row.get("bot_added") is True
                    and row.get("status") == "ACTIVE")

    def set_bot_access(self, chat_id, added: bool) -> dict:
        row = self.get(chat_id)
        if not row:
            raise KeyError("channel not registered")
        row["bot_added"] = bool(added)
        # Unverified destinations always start in the lowest band. They can
        # improve later through real delivery/statistics, never fabricated data.
        row["verified_state"] = "VERIFIED" if added else "DISCONNECTED"
        if not added:
            row["status"] = "DISCONNECTED"
            row["band"] = "C"
        row["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        self.store[self.key] = self._items(); self.store.sync(); return row
