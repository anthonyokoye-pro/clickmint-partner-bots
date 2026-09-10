"""Transactional Task Marketplace application boundary."""
from __future__ import annotations

from task_marketplace import TaskMarketplace, TaskNotFound, TaskUnavailable


class TaskService:
    def __init__(self, repository: TaskMarketplace):
        self.repo = repository

    def publish(self, task_id: str) -> dict:
        if not self.repo.publish(task_id):
            raise TaskUnavailable("task is not a draft")
        return self.repo.get_task(task_id)

    def cancel(self, task_id: str) -> dict:
        if not self.repo.cancel(task_id):
            raise TaskUnavailable("task is not cancellable")
        return self.repo.get_task(task_id)

    def reconcile(self) -> dict:
        return self.repo.reconcile()

    def claim(self, task_id: str, *, user_id, destination_id, lease_seconds=3600) -> dict:
        return self.repo.claim(task_id, user_id=user_id, destination_id=destination_id,
                               lease_seconds=lease_seconds)

    def complete(self, claim_id: str, **evidence) -> dict:
        return self.repo.complete(claim_id, **evidence)
