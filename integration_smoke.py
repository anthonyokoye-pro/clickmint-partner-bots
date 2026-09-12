"""Offline integration smoke checks for non-financial platform services."""
from __future__ import annotations

from pathlib import Path

from broadcast_queue import BroadcastQueue
from mint_ledger import TransactionalMintLedger
from performance_snapshots import PerformanceSnapshotRepository
from task_marketplace import TaskMarketplace
from admin_idempotency import IdempotencyStore


def smoke_initialize(root: str | Path) -> dict:
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    stores = {
        "mint": TransactionalMintLedger(root / "mint.sqlite3"),
        "tasks": TaskMarketplace(root / "tasks.sqlite3"),
        "broadcast": BroadcastQueue(root / "broadcast.sqlite3"),
        "credibility": PerformanceSnapshotRepository(root / "credibility.sqlite3"),
        "admin_api": IdempotencyStore(root / "admin_api.sqlite3"),
    }
    reward = stores["broadcast"].create_campaign(
        bot_scope="reward", created_by="smoke", payload={"text": "smoke"})
    partnership = stores["broadcast"].create_campaign(
        bot_scope="partnership", created_by="smoke", payload={"text": "smoke"})
    assert stores["broadcast"].get_campaign(reward)["bot_scope"] == "reward"
    assert stores["broadcast"].get_campaign(partnership)["bot_scope"] == "partnership"
    return {"stores": list(stores), "scopes_isolated": True}
