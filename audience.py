"""Audience resolution for administrative broadcasts.

The broadcast queue owns delivery state, not user discovery.  This service reads
known Reward Bot recipients from the legacy store during the migration period and
provides an explicit boundary so the Admin API never reaches into a Mint ledger's
private in-memory representation.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AudienceQuery:
    scope: str = "reward"
    status: str | None = None
    user_ids: tuple[str, ...] | None = None


class AudienceDirectory:
    """Resolve Telegram user IDs from the current authoritative user directory.

    Until the user-directory migration is complete, Reward Bot's JSON ledger is
    the compatibility source.  It is deliberately read-only here.  A later
    migration can replace this adapter without changing campaign services.
    """

    def __init__(self, reward_store=None, *, authoritative=None):
        self.reward_store = reward_store
        self.authoritative = authoritative

    def recipient_ids(self, query: AudienceQuery | None = None) -> list[str]:
        query = query or AudienceQuery()
        if query.scope != "reward":
            return []
        if self.authoritative is not None and hasattr(self.authoritative, "active_user_ids"):
            result = list(dict.fromkeys(str(value) for value in self.authoritative.active_user_ids()))
            requested = {str(value) for value in (query.user_ids or ())}
            return [value for value in result if not requested or value in requested]
        if self.reward_store is None:
            return []
        self.reward_store.reload()
        ledger = self.reward_store.get("ledger", {})
        if not isinstance(ledger, dict):
            return []
        requested = {str(value) for value in (query.user_ids or ())}
        result: list[str] = []
        seen: set[str] = set()
        for member in ledger.values():
            if not isinstance(member, dict) or member.get("user_id") is None:
                continue
            user_id = str(member["user_id"])
            if requested and user_id not in requested:
                continue
            if query.status and str(member.get("status", "ACTIVE")) != query.status:
                continue
            if user_id not in seen:
                seen.add(user_id)
                result.append(user_id)
        return result
