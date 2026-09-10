"""Multi-process SQLite contention checks for task and broadcast claims."""
import multiprocessing as mp
import tempfile
from pathlib import Path

from broadcast_queue import BroadcastQueue
from task_marketplace import TaskMarketplace


def _claim_broadcast(path):
    return len(BroadcastQueue(path).claim(limit=1))


def test_broadcast_claim_is_single_winner_across_processes():
    with tempfile.TemporaryDirectory() as directory:
        path = str(Path(directory) / "broadcast.sqlite3")
        queue = BroadcastQueue(path)
        cid = queue.create_campaign(bot_scope="reward", created_by=1, payload={"text": "x"})
        queue.queue_campaign(cid, [42])
        with mp.Pool(2) as pool:
            results = pool.map(_claim_broadcast, [path, path])
        assert sum(results) == 1, results


def _claim_task(args):
    path, task_id, user_id = args
    try:
        return bool(TaskMarketplace(path).claim(task_id, user_id=user_id, destination_id=f"d{user_id}"))
    except Exception:
        return False


def test_task_capacity_is_single_winner_across_processes():
    with tempfile.TemporaryDirectory() as directory:
        path = str(Path(directory) / "tasks.sqlite3")
        repo = TaskMarketplace(path)
        task_id = repo.create_task(creator_user_id=1, category="Guides", title="race", required_performers=1)
        repo.publish(task_id)
        with mp.Pool(2) as pool:
            results = pool.map(_claim_task, [(path, task_id, 10), (path, task_id, 11)])
        assert sum(results) == 1, results


if __name__ == "__main__":
    test_broadcast_claim_is_single_winner_across_processes()
    test_task_capacity_is_single_winner_across_processes()
    print("PASS multiprocessing contention tests")
