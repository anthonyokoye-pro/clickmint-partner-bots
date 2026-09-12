import asyncio
import tempfile
from pathlib import Path

from delivery_worker import WorkerScope, WorkerStore, UnifiedDeliveryWorker


def test_unified_worker_rate_limits_and_records_metrics():
    with tempfile.TemporaryDirectory() as directory:
        store = WorkerStore(Path(directory) / "worker.sqlite3")
        items = [{"recipient_id": "1"}, {"recipient_id": "1"}]
        completed = []
        failed = []

        async def deliver(item):
            return item["recipient_id"]

        def claim(limit):
            result, items[:] = items[:limit], items[limit:]
            return result

        def complete(item, result):
            completed.append(result)

        def fail(item, reason, **kwargs):
            failed.append(reason)

        worker = UnifiedDeliveryWorker(store, batch_size=5)
        worker.register(WorkerScope("reward", claim, deliver, complete, fail,
                                    rate_per_second=0.001, burst=1))
        result = asyncio.run(worker.run_once())
        assert result == {"claimed": 2, "delivered": 1, "failed": 0, "rate_limited": 1}
        summary = store.summary()
        assert summary["reward"]["delivered"]["count"] == 1
        assert summary["reward"]["rate_limited"]["count"] == 1
        store.close()


if __name__ == "__main__":
    test_unified_worker_rate_limits_and_records_metrics()
    print("PASS unified delivery worker")
