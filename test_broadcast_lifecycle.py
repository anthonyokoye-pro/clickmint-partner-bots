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
        assert queue.delete_draft(campaign_id)
        try:
            queue.queue_campaign(campaign_id, [1])
        except KeyError:
            pass
        else:
            raise AssertionError("deleted draft remained queueable")


if __name__ == "__main__":
    for test_case in (test_named_draft_edit_delete_and_queue_boundaries,
                      test_draft_delete_prevents_queueing):
        test_case()
        print(f"PASS {test_case.__name__}")
