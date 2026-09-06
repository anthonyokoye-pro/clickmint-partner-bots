"""Integration checks for activating the transactional compatibility facade."""
from pathlib import Path
import json
import tempfile

from core import TransactionalCreditLedger
from mint_ledger import TransactionalMintLedger
from migrate_mint import migrate
from store import JsonStore


def test_legacy_migration_preserves_balance():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        store_path = root / "legacy.json"
        store_path.write_text(json.dumps({"ledger": {
            "@alice": {"user_id": 42, "balance": 4, "earned": 3, "spent": 1}
        }}))
        report = migrate(str(store_path), str(root / "mint.sqlite3"))
        assert not report["errors"]
        db = TransactionalMintLedger(root / "mint.sqlite3")
        assert db.balance(42) == 4
        assert len(db.entries(42)) == 3


def test_transactional_facade_uses_ledger_for_spend_and_reward():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        facade = TransactionalCreditLedger(JsonStore(str(root / "state.json")), root / "mint.sqlite3")
        facade.register("@alice", 1000)
        facade.set_user_id("@alice", 42)
        assert facade.balance("@alice")["balance"] == 2
        facade.earn("@alice", idempotency_key="share:42:1")
        assert facade.balance("@alice")["balance"] == 3
        ok, _ = facade.spend("@alice", 2, idempotency_key="post:42:1")
        assert ok
        assert facade.balance("@alice")["balance"] == 1


if __name__ == "__main__":
    tests = [value for name, value in globals().items() if name.startswith("test_")]
    for test in tests:
        test()
        print("PASS", test.__name__)
    print(f"\n{len(tests)}/{len(tests)} transactional integration tests passed")
