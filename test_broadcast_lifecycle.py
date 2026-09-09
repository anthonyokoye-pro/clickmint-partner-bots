import tempfile
from pathlib import Path

from broadcast_queue import BroadcastQueue


def test_named_draft_edit_delete_and_queue_boundaries():
    with tempfile.TemporaryDirectory() as directory:
        queue = BroadcastQueue(Path(directory) / "broadcast.sqlite3")
        campaign_id = queue.create_campaign(
            bot_scope="reward", created_by=1, title="September update",
            payload={"text": "hello"}, scheduled_at=9999999999,
        )
        draft = queue.get_campaign(campaign_id)
        assert draft["title"] == "September update"
        assert queue.update_draft(campaign_id, title="Edited update", payload={"text": "edited"})
        assert queue.get_campaign(campaign_id)["title"] == "Edited update"
        assert queue.queue_campaign(campaign_id, [10]) == 1
        assert not queue.update_draft(campaign_id, title="too late", payload={"text": "x"})
        assert not queue.delete_draft(campaign_id)


def test_draft_delete_prevents_queueing():
    with tempfile.TemporaryDirectory() as directory:
        queue = BroadcastQueue(Path(directory) / "broadcast.sqlite3")
        campaign_id = queue.create_campaign(bot_scope="partnership", created_by=1,
                                            title="Remove me", payload={"text": "x"})
        assert queue.delete_draft(campaign_id, deleted_by=99)
        assert queue.get_campaign(campaign_id)["status"] == "deleted"
        try:
            queue.queue_campaign(campaign_id, [1])
        except ValueError:
            pass
        else:
            raise AssertionError("deleted draft remained queueable")


def test_retry_budget_becomes_terminal_and_stale_workers_are_recovered():
    with tempfile.TemporaryDirectory() as directory:
        queue = BroadcastQueue(Path(directory) / "broadcast.sqlite3")
        campaign_id = queue.create_campaign(bot_scope="reward", created_by=1, payload={"text": "hi"})
        queue.queue_campaign(campaign_id, [10, 11])
        claimed = sorted(queue.claim(limit=5), key=lambda row: row["recipient_id"])
        assert queue.fail(claimed[0]["delivery_id"], "temporary", max_attempts=1)
        assert queue.campaign_summary(campaign_id)["deliveries"]["failed"] == 1
        # Simulate a crashed worker by making the second processing lease old.
        with queue._tx() as conn:
            conn.execute("UPDATE broadcast_recipients SET next_attempt_at=1 WHERE delivery_id=?",
                         (claimed[1]["delivery_id"],))
        result = queue.recover_stale_processing(older_than_seconds=1, max_attempts=8)
        assert result == {"recovered": 1, "terminal": 0}
        assert queue.campaign_summary(campaign_id)["deliveries"]["pending"] == 1


def test_release_returns_delivery_without_burning_an_attempt():
    with tempfile.TemporaryDirectory() as directory:
        queue = BroadcastQueue(Path(directory) / "broadcast.sqlite3")
        campaign_id = queue.create_campaign(bot_scope="reward", created_by=1, payload={"text": "hi"})
        queue.queue_campaign(campaign_id, [10])
        claimed = queue.claim(limit=5)
        assert len(claimed) == 1 and claimed[0]["attempts"] == 1
        assert queue.release(claimed[0]["delivery_id"], retry_seconds=1)
        assert queue.claim(limit=5) == []          # not due yet
        import time; time.sleep(1.1)
        again = queue.claim(limit=5)
        assert len(again) == 1 and again[0]["attempts"] == 1, again


if __name__ == "__main__":
    for test_case in (test_named_draft_edit_delete_and_queue_boundaries,
                      test_draft_delete_prevents_queueing,
                      test_retry_budget_becomes_terminal_and_stale_workers_are_recovered,
                      test_release_returns_delivery_without_burning_an_attempt):
        test_case()
        print(f"PASS {test_case.__name__}")
