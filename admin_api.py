"""Read-only authenticated Admin Mini App application boundary."""
from __future__ import annotations

from webapp_auth import validate_init_data


class AdminAuthorizationError(PermissionError):
    pass


def _safe_preview(payload: dict) -> str:
    """Escaped HTML preview of a draft; falls back to escaped plain text."""
    from html import escape
    from tg_entities import to_html
    text = payload.get("text") or ""
    try:
        return to_html(text, payload.get("entities") or [])
    except ValueError:
        return escape(text)


class AdminReadAPI:
    """Application service independent of HTTP framework or Telegram web server."""
    def __init__(self, *, bot_token, owner_id, roles, channels, marketplace,
                 snapshots, broadcasts, ledger=None, enforcement=None, ads=None):
        self.bot_token = bot_token
        self.owner_id = int(owner_id)
        self.roles = roles
        self.channels = channels
        self.marketplace = marketplace
        self.snapshots = snapshots
        self.broadcasts = broadcasts
        self.ledger = ledger
        self.enforcement = enforcement
        self.ads = ads

    def authenticate(self, init_data: str):
        identity = validate_init_data(init_data, self.bot_token)
        if identity.user_id != self.owner_id and not self.roles.is_admin(identity.user_id):
            raise AdminAuthorizationError("admin access required")
        return identity

    # -- advertising (separate model, separate role) -------------------------
    def _ads_manager(self, init_data: str):
        """Ads Manager = owner or an admin holding the 'ads' scope. Network
        moderators without that scope cannot touch advertising, and vice versa."""
        identity = self.authenticate(init_data)
        if self.ads is None:
            raise RuntimeError("advertising store required")
        if identity.user_id != self.owner_id and not self.roles.is_admin(identity.user_id, "ads"):
            raise AdminAuthorizationError("Ads Manager scope required")
        return identity

    def ads_overview(self, init_data: str) -> dict:
        self.authenticate(init_data)   # read-only summary is visible to any admin
        if self.ads is None:
            raise RuntimeError("advertising store required")
        campaigns = self.ads.recent_campaigns(limit=50)
        return {"enabled": self.ads.enabled, "terms": self.ads.current_terms(),
                "consents": len(self.ads.consenting_destinations()),
                "campaigns": [{**{k: c[k] for k in ("campaign_id", "title", "advertiser_label", "category", "status", "terms_version", "review_reason")},
                               "deliveries": self.ads.summary(c["campaign_id"])["deliveries"]} for c in campaigns]}

    def publish_ad_terms(self, init_data: str, text: str) -> dict:
        identity = self.authenticate(init_data)
        if identity.user_id != self.owner_id:
            raise AdminAuthorizationError("owner approval required to publish advertising terms")
        if self.ads is None:
            raise RuntimeError("advertising store required")
        terms = self.ads.publish_terms(text, published_by=identity.user_id)
        self._audit(identity.user_id, "AD_TERMS_PUBLISHED", "ad_terms", str(terms["version"]), text[:200])
        return terms

    def create_ad_campaign(self, init_data: str, title: str, advertiser_label: str, category: str,
                           text: str, scheduled_at=None) -> dict:
        identity = self._ads_manager(init_data)
        campaign_id = self.ads.create_campaign(title=title, advertiser_label=advertiser_label, category=category,
                                               text=text, created_by=identity.user_id, scheduled_at=scheduled_at)
        self._audit(identity.user_id, "AD_CAMPAIGN_CREATED", "ad_campaign", campaign_id, advertiser_label)
        return {"campaign_id": campaign_id, "status": "draft"}

    def update_ad_draft(self, init_data: str, campaign_id: str, title=None, text=None, category=None, scheduled_at=None) -> dict:
        identity = self._ads_manager(init_data)
        if not self.ads.update_draft(campaign_id, title=title, text=text, category=category, scheduled_at=scheduled_at):
            raise ValueError("only a draft or rejected campaign can be edited")
        self._audit(identity.user_id, "AD_CAMPAIGN_EDITED", "ad_campaign", campaign_id, "Mini App edit")
        return {"campaign_id": campaign_id, "status": "draft"}

    def ad_campaign_action(self, init_data: str, campaign_id: str, action: str, reason: str = "") -> dict:
        if action in {"approve", "reject"}:
            # Safety review is the OWNER's call, and it always carries a reason.
            identity = self.authenticate(init_data)
            if identity.user_id != self.owner_id:
                raise AdminAuthorizationError("owner approval required for ad safety review")
            if self.ads is None:
                raise RuntimeError("advertising store required")
            result = self.ads.review(campaign_id, approved=(action == "approve"), reviewer_id=identity.user_id, reason=reason)
            self._audit(identity.user_id, f"AD_CAMPAIGN_{action.upper()}D", "ad_campaign", campaign_id, reason)
            return {"campaign_id": campaign_id, "status": result["status"]}
        identity = self._ads_manager(init_data)
        if action == "submit":
            ok = self.ads.submit_for_review(campaign_id, actor_id=identity.user_id)
        elif action == "queue":
            inserted = self.ads.queue(campaign_id, actor_id=identity.user_id)
            self._audit(identity.user_id, "AD_CAMPAIGN_QUEUED", "ad_campaign", campaign_id, f"{inserted} consenting destinations")
            return {"campaign_id": campaign_id, "status": "queued", "new_deliveries": inserted}
        elif action == "pause":
            ok = self.ads.pause(campaign_id, actor_id=identity.user_id)
        elif action == "resume":
            ok = self.ads.resume(campaign_id, actor_id=identity.user_id)
        elif action == "cancel":
            ok = self.ads.cancel(campaign_id, actor_id=identity.user_id, reason=reason)
        else:
            raise ValueError("unsupported ad campaign action")
        if not ok:
            raise ValueError("ad campaign action not available in its current state")
        self._audit(identity.user_id, f"AD_CAMPAIGN_{action.upper()}", "ad_campaign", campaign_id, reason or "Mini App action")
        return {"campaign_id": campaign_id, "action": action, "status": "applied"}

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
                               scheduled_at: int | None = None, title: str = "") -> dict:
        identity = self.authenticate(init_data)
        if not text or len(text) > 4000:
            raise ValueError("explicit text is required and must be at most 4000 characters")
        campaign_id = self.broadcasts.create_campaign(
            bot_scope="reward", created_by=identity.user_id,
            payload={"text": text, "entities": [], "audience": "known_reward_members", "composed_in": "mini_app"},
            scheduled_at=scheduled_at, title=title or "Untitled Reward broadcast",
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

    def update_reward_draft(self, init_data: str, campaign_id: str, title: str, text: str, scheduled_at=None) -> dict:
        identity = self.authenticate(init_data)
        if not title.strip() or not text.strip() or len(text) > 4000:
            raise ValueError("title and text are required; text must be at most 4000 characters")
        # Editing in the Mini App drops captured formatting on purpose: the text box
        # is plain text and we never reconstruct entities from typed markup.
        if not self.broadcasts.update_draft(campaign_id, title=title, payload={"text": text, "entities": [], "audience": "known_reward_members", "composed_in": "mini_app"}, scheduled_at=scheduled_at):
            raise ValueError("only an existing draft can be edited")
        self._audit(identity.user_id, "REWARD_BROADCAST_EDITED", "broadcast_campaign", campaign_id, "Mini App draft edit")
        return {"campaign_id": campaign_id, "status": "draft"}

    def delete_reward_draft(self, init_data: str, campaign_id: str) -> dict:
        identity = self.authenticate(init_data)
        if not self.broadcasts.delete_draft(campaign_id):
            raise ValueError("only an existing draft can be deleted")
        self._audit(identity.user_id, "REWARD_BROADCAST_DELETED", "broadcast_campaign", campaign_id, "Mini App draft deletion")
        return {"campaign_id": campaign_id, "status": "deleted"}

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

    def enforcement_details(self, init_data: str, entity_id, entity_type: str) -> dict:
        self.authenticate(init_data)
        if self.enforcement is None:
            raise RuntimeError("enforcement store required")
        return {"entity": self.enforcement.get(entity_id, entity_type),
                "timeline": self.enforcement.timeline(entity_id, entity_type)}

    def enforce_entity(self, init_data: str, entity_id, entity_type: str,
                       action: str, reason: str, duration_seconds=None, notes="",
                       related_report_id=None) -> dict:
        identity = self.authenticate(init_data)
        if identity.user_id != self.owner_id:
            raise AdminAuthorizationError("owner approval required for enforcement")
        if self.enforcement is None:
            raise RuntimeError("enforcement store required")
        if action == "restore":
            result = self.enforcement.restore(entity_id, entity_type, actor_id=identity.user_id, reason=reason, notes=notes)
        else:
            if action.upper() not in {"FLAGGED", "RESTRICTED", "SUSPENDED", "BANNED", "REMOVED"}:
                raise ValueError("unsupported enforcement action")
            if duration_seconds is not None:
                duration_seconds = int(duration_seconds)
                if duration_seconds <= 0 or duration_seconds > 365 * 86400:
                    raise ValueError("duration_seconds must be between 1 second and 365 days")
            result = self.enforcement.enforce(entity_id, entity_type, action.upper(), actor_id=identity.user_id,
                                              reason=reason, duration_seconds=duration_seconds, notes=notes,
                                              related_report_id=related_report_id)
            if related_report_id:
                self.enforcement.review_report(related_report_id, action.upper() if action.upper() in {"RESTRICTED", "SUSPENDED", "BANNED"} else "RESOLVED",
                                               reviewer_id=identity.user_id, notes=notes or reason)
        self._audit(identity.user_id, f"ENFORCEMENT_{action.upper()}", "enforcement_entity", str(entity_id), reason)
        return result

    # -- reports / evidence / appeals (human-in-the-loop only) ---------------
    def _require_enforcement(self):
        if self.enforcement is None:
            raise RuntimeError("enforcement store required")
        return self.enforcement

    def safety_overview(self, init_data: str) -> dict:
        self.authenticate(init_data)
        store = self._require_enforcement()
        return {"status": store.safety_status(),
                "reports": store.list_reports("PENDING", limit=20) + store.list_reports("UNDER_REVIEW", limit=20),
                "appeals": store.list_appeals("OPEN", limit=20) + store.list_appeals("UNDER_REVIEW", limit=20)}

    def list_reports(self, init_data: str, status: str | None = None, limit: int = 50) -> list[dict]:
        self.authenticate(init_data)
        return self._require_enforcement().list_reports(status, limit=limit)

    def report_details(self, init_data: str, report_id: str) -> dict:
        self.authenticate(init_data)
        store = self._require_enforcement()
        report = store.report_by_id(report_id)
        if not report:
            raise ValueError("report not found")
        return {"report": report,
                "evidence": store.evidence_for(report["entity_id"], report["entity_type"], report_id=report_id),
                "entity": store.get(report["entity_id"], report["entity_type"])}

    def file_report(self, init_data: str, entity_id, entity_type: str, reason: str, subject: str = "") -> dict:
        identity = self.authenticate(init_data)
        if entity_type not in {"user", "channel", "group"}:
            raise ValueError("entity_type must be user, channel or group")
        report = self._require_enforcement().report(identity.user_id, entity_id, entity_type, reason, subject=subject)
        self._audit(identity.user_id, "COMPLIANCE_REPORT_FILED", "compliance_report", report["report_id"], reason)
        return report

    def review_report(self, init_data: str, report_id: str, status: str, notes: str = "") -> dict:
        """Record a human decision on a report. Never changes enforcement state:
        an action, if warranted, is a separate explicit enforce_entity() call."""
        identity = self.authenticate(init_data)
        report = self._require_enforcement().review_report(report_id, status, reviewer_id=identity.user_id, notes=notes)
        self._audit(identity.user_id, f"COMPLIANCE_REPORT_{status.upper()}", "compliance_report", report_id, notes or "Mini App review")
        return report

    def add_evidence(self, init_data: str, entity_id, entity_type: str, evidence_type: str,
                     reference: str, report_id: str | None = None, notes: str = "") -> dict:
        identity = self.authenticate(init_data)
        if evidence_type not in {"message", "screenshot", "link", "log", "note"}:
            raise ValueError("evidence_type must be message, screenshot, link, log or note")
        item = self._require_enforcement().add_evidence(entity_id, entity_type, evidence_type, reference,
                                                        captured_by=identity.user_id, report_id=report_id, notes=notes)
        self._audit(identity.user_id, "COMPLIANCE_EVIDENCE_ADDED", "compliance_evidence", item["evidence_id"], reference[:200])
        return item

    def list_appeals(self, init_data: str, status: str | None = None, limit: int = 50) -> list[dict]:
        self.authenticate(init_data)
        return self._require_enforcement().list_appeals(status, limit=limit)

    def decide_appeal(self, init_data: str, appeal_id: str, decision: str, notes: str = "") -> dict:
        identity = self.authenticate(init_data)
        if decision.upper() == "OVERTURNED" and identity.user_id != self.owner_id:
            raise AdminAuthorizationError("owner approval required to overturn enforcement")
        appeal = self._require_enforcement().decide_appeal(appeal_id, decision, decided_by=identity.user_id, notes=notes)
        self._audit(identity.user_id, f"APPEAL_{decision.upper()}", "compliance_appeal", appeal_id, notes or "Mini App decision")
        self._notify_appellant(appeal)
        return appeal

    def _notify_appellant(self, appeal: dict) -> None:
        """Keep the /appeal promise: tell the member the final decision.

        Delivered through the durable outbox the reward bot already drains, so
        the Mini App never talks to Telegram directly. Interim states are silent."""
        outcome = {"UPHELD": "Your appeal was reviewed by a human and the restriction stands.",
                   "OVERTURNED": "Your appeal was reviewed by a human and the restriction has been lifted. Welcome back.",
                   "WITHDRAWN": "Your appeal has been closed as withdrawn."}.get(appeal.get("status"))
        if not outcome or self.ledger is None or not hasattr(self.ledger, "tx"):
            return
        try:
            recipient = int(appeal["submitted_by"])
        except (TypeError, ValueError):
            return
        text = f"📨 Appeal {appeal['appeal_id']}: {outcome}"
        if appeal.get("decision_notes"):
            text += f"\nReviewer note: {appeal['decision_notes'][:300]}"
        self.ledger.tx.enqueue_outbox(event_type="OWNER_NOTIFICATION", recipient_id=recipient, payload=text,
                                      idempotency_key=f"appeal-decision:{appeal['appeal_id']}:{appeal['status']}")

    def emergency_mode(self, init_data: str, enabled: bool, reason: str) -> dict:
        identity = self.authenticate(init_data)
        if identity.user_id != self.owner_id:
            raise AdminAuthorizationError("owner approval required for safe mode")
        if self.enforcement is None:
            raise RuntimeError("enforcement store required")
        result = self.enforcement.set_emergency(bool(enabled), actor_id=identity.user_id, reason=reason)
        self._audit(identity.user_id, "SAFE_MODE_ENABLED" if enabled else "SAFE_MODE_DISABLED", "safety_control", "safe_mode", reason)
        return result

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
                "title": campaign.get("title") or campaign.get("payload", {}).get("text", "")[:80],
                "status": campaign["status"],
                "composed_in": campaign.get("payload", {}).get("composed_in", "legacy"),
                "entity_count": len(campaign.get("payload", {}).get("entities") or []),
                "preview_html": _safe_preview(campaign.get("payload", {})),
                "deliveries": self.broadcasts.campaign_summary(campaign["campaign_id"]).get("deliveries", {}),
            } for campaign in campaigns[:20]],
        }
