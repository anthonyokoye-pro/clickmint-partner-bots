"""Broadcast application boundary; bot handlers do not own queue transitions."""
from __future__ import annotations

from broadcast_queue import BroadcastQueue


class BroadcastService:
    def __init__(self, queue: BroadcastQueue, audience):
        self.queue = queue
        self.audience = audience

    def queue_reward(self, campaign_id: str) -> dict:
        campaign = self.queue.get_campaign(campaign_id)
        if not campaign or campaign.get("bot_scope") != "reward":
            raise ValueError("campaign not found")
        recipients = self.audience.recipient_ids()
        if not recipients:
            raise ValueError("no eligible Reward Bot recipients were found")
        inserted = self.queue.queue_campaign(campaign_id, recipients)
        return {"campaign_id": campaign_id, "status": "queued", "new_deliveries": inserted}

    def recover(self, *, older_than_seconds=900) -> dict:
        return self.queue.recover_stale_processing(older_than_seconds=older_than_seconds)

    def reconcile(self) -> dict:
        changed = 0
        for campaign in self.queue.recent_campaigns(bot_scope="reward", limit=1000):
            summary = self.queue.campaign_summary(campaign["campaign_id"])
            if summary and summary.get("status") == "queued":
                # campaign_summary is derived; completion is finalized by queue.complete/fail.
                changed += 0
        return {"campaigns_checked": len(self.queue.recent_campaigns(bot_scope="reward", limit=1000)),
                "changed": changed}
