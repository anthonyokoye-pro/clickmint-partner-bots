"""Admin Mini App application factory with fail-closed startup checks."""
from __future__ import annotations

from pathlib import Path

from admin_deploy import admin_preflight
from admin_http import AdminWSGI
from admin_idempotency import IdempotencyStore


def build_admin_app(api, *, root: str | Path, bot_token: str, owner_id: str,
                    allowed_origins: list[str], idempotency_path: str | Path):
    errors = admin_preflight(
        root, bot_token=bot_token, owner_id=owner_id,
        allowed_origins=allowed_origins,
    )
    if errors:
        raise RuntimeError("Admin startup preflight failed: " + "; ".join(errors))
    replay_store = IdempotencyStore(idempotency_path)
    return AdminWSGI(api, allowed_origins=set(allowed_origins),
                     idempotency_store=replay_store)
