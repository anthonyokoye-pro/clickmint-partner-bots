"""Offline tests for transactional task capacity and claims."""
import tempfile
import time
from pathlib import Path

from task_marketplace import TaskMarketplace, TaskUnavailable


def fresh():
    directory = tempfile.TemporaryDirectory(prefix="clickmint-tasks-")
    return directory, TaskMarketplace(Path(directory.name) / "tasks.sqlite3")


def test_capacity_allows_multiple_users_and_closes_at_full():
    directory, repo = fresh()
    try:
        task_id = repo.create_task(
            creator_user_id=1, category="AI Tools", title="Publish task",
            required_performers=2, reward_amount=2, minimum_tier="EMERGING",
        )
        assert repo.publish(task_id)
        task = repo.get_task(task_id)
        assert task["reward_amount"] == 2
        assert task["minimum_tier"] == "EMERGING"
        repo.claim(task_id, user_id=10, destination_id="channel-a")
        repo.claim(task_id, user_id=11, destination_id="channel-b")
        with_expected = repo.progress(task_id)
        assert with_expected["claimed"] == 2
        assert with_expected["remaining"] == 0
        try:
            repo.claim(task_id, user_id=12, destination_id="channel-c")
        except TaskUnavailable:
            pass
        else:
            raise AssertionError("third user claimed a full task")
    finally:
        directory.cleanup()


def test_completion_records_telegram_evidence_and_updates_progress():
    directory, repo = fresh()
    try:
        task_id = repo.create_task(
            creator_user_id=1, category="Guides", title="Publish guide",
            required_performers=1,
        )
        repo.publish(task_id)
        claim = repo.claim(task_id, user_id=10, destination_id="channel-a")
        completion = repo.complete(
            claim["claim_id"], telegram_chat_id="-1001",
            telegram_message_id=55, reward_event_id="reward-1",
        )
        assert completion["telegram_message_id"] == 55
        progress = repo.progress(task_id)
        assert progress["completed"] == 1
        assert progress["status"] == "full"
    finally:
        directory.cleanup()


def test_user_cannot_claim_same_task_twice():
    directory, repo = fresh()
    try:
        task_id = repo.create_task(
            creator_user_id=1, category="DeFi", title="Publish defi post",
            required_performers=3,
        )
        repo.publish(task_id)
        repo.claim(task_id, user_id=10, destination_id="channel-a")
        try:
            repo.claim(task_id, user_id=10, destination_id="channel-b")
        except TaskUnavailable:
            pass
        else:
            raise AssertionError("duplicate user claim was accepted")
    finally:
        directory.cleanup()


def test_claim_count_since_supports_daily_limits():
    directory, repo = fresh()
    try:
        task_id = repo.create_task(creator_user_id=1, category="Guides", title="Quota", required_performers=2)
        repo.publish(task_id)
        repo.claim(task_id, user_id=10, destination_id="channel-a")
        assert repo.user_claim_count_since(10, int(time.time()) - 60) == 1
        assert repo.user_claim_count_since(10, int(time.time()) - 60, "channel-a") == 1
        assert repo.user_claim_count_since(10, int(time.time()) - 60, "channel-b") == 0
        assert repo.user_claim_count_since(11, int(time.time()) - 60) == 0
    finally:
        directory.cleanup()


def test_recovery_expires_claim_and_reopens_capacity():
    directory, repo = fresh()
    try:
        task_id = repo.create_task(
            creator_user_id=1, category="Guides", title="Recover stale claim",
            required_performers=2,
        )
        repo.publish(task_id)
        repo.claim(task_id, user_id=10, destination_id="channel-a", lease_seconds=1)
        repo.claim(task_id, user_id=11, destination_id="channel-b", lease_seconds=3600)
        # Force the task into full state through a completed second claim.
        # The stale first claim is still occupying a slot until recovery.
        time.sleep(1.1)
        result = repo.recover_expired()
        assert result["expired_claims"] >= 1
        assert repo.progress(task_id)["claimed"] == 1
        assert repo.progress(task_id)["status"] == "published"
    finally:
        directory.cleanup()


def test_expired_claim_cannot_complete():
    directory, repo = fresh()
    try:
        task_id = repo.create_task(
            creator_user_id=1, category="Testnets", title="Publish testnet post",
            required_performers=1,
        )
        repo.publish(task_id)
        claim = repo.claim(task_id, user_id=10, destination_id="channel-a", lease_seconds=1)
        time.sleep(1.1)
        try:
            repo.complete(claim["claim_id"], telegram_chat_id="-1001", telegram_message_id=66)
        except TaskUnavailable:
            pass
        else:
            raise AssertionError("expired claim was completed")
    finally:
        directory.cleanup()
