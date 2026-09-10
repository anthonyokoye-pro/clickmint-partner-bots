import tempfile
from pathlib import Path

from mint_ledger import TransactionalMintLedger
from user_directory import UserDirectory
from task_marketplace import TaskMarketplace
from services.task_service import TaskService


def test_user_directory_is_the_only_live_audience_boundary():
    with tempfile.TemporaryDirectory() as directory:
        users = UserDirectory(TransactionalMintLedger(Path(directory) / "mint.sqlite3"))
        users.ensure(42)
        users.ensure(99, status="banned")
        assert users.list_active_user_ids() == ["42"]
        assert not users.is_active(99)


def test_task_service_exposes_transactional_lifecycle():
    with tempfile.TemporaryDirectory() as directory:
        service = TaskService(TaskMarketplace(Path(directory) / "tasks.sqlite3"))
        task_id = service.repo.create_task(creator_user_id=1, category="Guides", title="Service", required_performers=1)
        assert service.publish(task_id)["status"] == "published"
        claim = service.claim(task_id, user_id=2, destination_id="@dest")
        completion = service.complete(claim["claim_id"], telegram_chat_id="-100", telegram_message_id=1)
        assert service.reconcile()["marked_full"] == 0
        mint = TransactionalMintLedger(Path(directory) / "mint.sqlite3")
        result = service.reconcile_rewards(mint)
        assert result["credited"] == 1
        assert mint.balance(2) == 1
        assert service.reconcile_rewards(mint)["already_reconciled"] == 1
        events = []
        assert service.drain_outbox(events.append)["sent"] >= 3
        assert events[0]["event_type"] == "TASK_CREATED"


if __name__ == "__main__":
    test_user_directory_is_the_only_live_audience_boundary()
    test_task_service_exposes_transactional_lifecycle()
    print("PASS service boundaries")
