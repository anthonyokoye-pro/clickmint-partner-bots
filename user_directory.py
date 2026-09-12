"""Canonical ClickMint user-directory boundary.

Mint SQLite owns account identity/status. Legacy JSON is accepted only by the
explicit migration method and is never read for live audience resolution.
"""
from __future__ import annotations

from pathlib import Path


class UserDirectory:
    def __init__(self, mint_ledger):
        self.mint = mint_ledger

    def ensure(self, user_id, *, status: str = "active") -> None:
        self.mint.ensure_account(str(user_id), status=status)

    def list_active_user_ids(self, *, limit: int = 100000) -> list[str]:
        return list(self.mint.active_user_ids(limit=limit))

    def is_active(self, user_id) -> bool:
        return str(user_id) in set(self.list_active_user_ids())

    def migrate_legacy_members(self, legacy_members: dict) -> dict:
        """Import identity/status only; balances remain owned by Mint migration."""
        imported = skipped = 0
        for username, member in (legacy_members or {}).items():
            if not isinstance(member, dict) or member.get("user_id") is None:
                skipped += 1
                continue
            status = str(member.get("status", "active")).lower()
            self.ensure(member["user_id"], status=status if status in {"active", "provisional", "restricted", "suspended", "banned"} else "active")
            imported += 1
        return {"imported": imported, "skipped": skipped}
