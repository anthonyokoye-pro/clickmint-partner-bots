"""Read-only authenticated Admin Mini App application boundary."""
from __future__ import annotations

from webapp_auth import validate_init_data


class AdminAuthorizationError(PermissionError):
    pass


class AdminReadAPI:
    """Application service independent of HTTP framework or Telegram web server."""
    def __init__(self, *, bot_token, owner_id, roles, channels, marketplace,
                 snapshots, broadcasts, ledger=None):
        self.bot_token = bot_token
        self.owner_id = int(owner_id)
        self.roles = roles
        self.channels = channels
        self.marketplace = marketplace
        self.snapshots = snapshots
        self.broadcasts = broadcasts
        self.ledger = ledger

    def authenticate(self, init_data: str):
        identity = validate_init_data(init_data, self.bot_token)
        if identity.user_id != self.owner_id and not self.roles.is_admin(identity.user_id):
            raise AdminAuthorizationError("admin access required")
        return identity

    def _audit(self, actor_id: int, action: str, object_type: str, object_id: str, reason: str):
        if self.ledger is not None and hasattr(self.ledger, "tx"):
            self.ledger.tx.add_audit_event(
                actor_type="admin", actor_id=str(actor_id), action=action,
                object_type=object_type, object_id=str(object_id), reason=reason,
            )

    def referral_snapshot(self, init_data: str, period: str) -> dict:
        self.authenticate(init_data)
        if self.ledger is None or not hasattr(self.ledger, "tx"):
            raise RuntimeError("transactional ledger required")
        snapshot = self.ledger.tx.referral_ranking_snapshot(period)
        if not snapshot:
            raise ValueError("snapshot not found")
        return snapshot

    def approve_referral_ranking(self, init_data: str, period: str, reason: str = "") -> dict:
        identity = self.authenticate(init_data)
        if self.ledger is None or not hasattr(self.ledger, "tx"):
            raise RuntimeError("transactional ledger required")
        if not self.ledger.tx.approve_referral_ranking(period, identity.user_id):
            raise ValueError("snapshot not found or already finalized")
        self._audit(identity.user_id, "REFERRAL_RANKING_APPROVED", "referral_snapshot", period, reason or "Mini App approval")
        return {"period": period, "status": "approved"}

    def create_reward_campaign(self, init_data: str, text: str,
                               scheduled_at: int | None = None) -> dict:
        identity = self.authenticate(init_data)
        if not text or len(text) > 4000:
            raise ValueError("explicit text is required and must be at most 4000 characters")
        campaign_id = self.broadcasts.create_campaign(
            bot_scope="reward", created_by=identity.user_id,
            payload={"text": text, "audience": "known_reward_members"},
            scheduled_at=scheduled_at,
        )
        self._audit(identity.user_id, "REWARD_BROADCAST_CREATED", "broadcast_campaign", campaign_id, "Mini App campaign creation")
        return {"campaign_id": campaign_id, "status": "draft"}

    def queue_reward_campaign(self, init_data: str, campaign_id: str) -> dict:
        identity = self.authenticate(init_data)
        campaign = self.broadcasts.get_campaign(campaign_id)
        if not campaign or campaign.get("bot_scope") != "reward":
            raise ValueError("campaign not found")
        if self.ledger is None:
            raise RuntimeError("ledger required for audience selection")
        recipients = [member.get("user_id") for member in self.ledger.ledger.values() if member.get("user_id")]
        inserted = self.broadcasts.queue_campaign(campaign_id, recipients)
        self._audit(identity.user_id, "REWARD_BROADCAST_QUEUED", "broadcast_campaign", campaign_id, f"{inserted} recipients")
        return {"campaign_id": campaign_id, "status": "queued", "new_deliveries": inserted}

    def broadcast_action(self, init_data: str, campaign_id: str, action: str,
                         reason: str = ""):
        identity = self.authenticate(init_data)
        actions = {"pause": self.broadcasts.pause, "resume": self.broadcasts.resume,
                   "cancel": self.broadcasts.cancel}
        if action not in actions:
            raise ValueError("unsupported campaign action")
        result = actions[action](campaign_id)
        if not result:
            raise ValueError("campaign action not available")
        self._audit(identity.user_id, f"REWARD_BROADCAST_{action.upper()}",
                    "broadcast_campaign", campaign_id, reason or "Mini App action")
        return {"campaign_id": campaign_id, "action": action, "status": "applied"}

    def clear_performance_cooldown(self, init_data: str, destination_id,
                                   reason: str = "") -> dict:
        identity = self.authenticate(init_data)
        if not self.snapshots.clear_cooldown(destination_id, reason or "Mini App intervention"):
            raise ValueError("no active cooldown")
        self._audit(identity.user_id, "PERFORMANCE_COOLDOWN_CLEARED",
                    "destination_control", str(destination_id), reason or "Mini App intervention")
        return {"destination_id": str(destination_id), "status": "cleared"}

    def audit_events(self, init_data: str, *, object_type: str | None = None,
                     limit: int = 50) -> list[dict]:
        self.authenticate(init_data)
        if self.ledger is None or not hasattr(self.ledger, "tx"):
            return []
        return self.ledger.tx.recent_audit_events(
            object_type=object_type, limit=max(1, min(int(limit), 100)))

    def performance_history(self, init_data: str, destination_id,
                            limit: int = 20) -> dict:
        self.authenticate(init_data)
        return {
            "destination_id": str(destination_id),
            "current": self.snapshots.current(destination_id),
            "history": self.snapshots.history(destination_id, limit=max(1, min(int(limit), 100))),
            "interventions": self.snapshots.intervention_history(destination_id, limit=max(1, min(int(limit), 100))),
            "control": self.snapshots.control(destination_id),
        }

    def dashboard(self, init_data: str, *, task_limit: int = 20,
                  task_category: str | None = None,
                  broadcast_status: str | None = None) -> dict:
        identity = self.authenticate(init_data)
        uid = identity.user_id
        task_limit = max(1, min(int(task_limit), 100))
        channels = self.channels.mine(uid)
        channel_views = []
        for channel in channels:
            destination = channel.get("chat_id") or channel.get("username")
            channel_views.append({
                "destination": destination,
                "username": channel.get("username"),
                "status": channel.get("status", "ACTIVE"),
                "categories": list(channel.get("categories") or []),
                "credibility": self.snapshots.explain(destination),
            })
        categories = [task_category] if task_category else None
        tasks = self.marketplace.available_for(uid, categories=categories, limit=task_limit)
        campaigns = self.broadcasts.recent_campaigns(bot_scope="reward", limit=100)
        if broadcast_status:
            campaigns = [campaign for campaign in campaigns if campaign["status"] == broadcast_status]
        return {
            "user": {"id": identity.user_id, "username": identity.username},
            "channels": channel_views,
            "tasks": [{"task_id": task["task_id"], "title": task["title"],
                       "category": task["category"], "reward_amount": task.get("reward_amount", 1)}
                      for task in tasks],
            "broadcasts": [{
                "campaign_id": campaign["campaign_id"],
                "status": campaign["status"],
                "deliveries": self.broadcasts.campaign_summary(campaign["campaign_id"]).get("deliveries", {}),
            } for campaign in campaigns[:20]],
        }
