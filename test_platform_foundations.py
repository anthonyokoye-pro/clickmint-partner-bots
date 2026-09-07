"""Tests for connection, eligibility, referral, and broadcast foundations."""
import tempfile
import time
from pathlib import Path

from broadcast_queue import BroadcastQueue
from channel_connection import TelegramPermissionSnapshot, ConnectionState, verify_permissions
from eligibility import EligibilityContext, can_claim_task
from referral_ranking import ReferralRecord, allocate_monthly_rewards, rank_referrers


def test_channel_permissions_fail_closed():
    result = verify_permissions(TelegramPermissionSnapshot(
        chat_id="-100", chat_type="channel", member_status="administrator",
        can_post_messages=False,
    ))
    assert not result.eligible
    assert result.state == ConnectionState.DEGRADED


def test_verified_channel_can_execute():
    result = verify_permissions(TelegramPermissionSnapshot(
        chat_id="-100", chat_type="channel", member_status="administrator",
        can_post_messages=True,
    ))
    assert result.eligible
    assert result.state == ConnectionState.VERIFIED


def test_eligibility_returns_explainable_reasons():
    result = can_claim_task(EligibilityContext(
        account_active=True, destination_verified=False,
        destination_status="ACTIVE", credibility_score=65,
        category_match=True, daily_limit_remaining=0,
    ))
    assert not result.eligible
    assert "destination is not verified" in result.reasons
    assert "daily task limit reached" in result.reasons


def test_referral_ranking_excludes_low_quality_referrals():
    records = [
        ReferralRecord("a", "1", "qualified", retained=True, qualified_activity_count=2),
        ReferralRecord("a", "2", "qualified", retained=False, qualified_activity_count=2),
        ReferralRecord("b", "3", "qualified", retained=True, qualified_activity_count=1),
        ReferralRecord("b", "4", "qualified", retained=True, qualified_activity_count=1, fraud_flag=True),
    ]
    ranking = rank_referrers(records)
    assert ranking[0]["referrer_id"] == "a"
    rewards = allocate_monthly_rewards(ranking, pool=10, winners=2)
    assert sum(row["reward_amount"] for row in rewards) == 10


def test_broadcast_queue_is_scoped_and_retryable():
    with tempfile.TemporaryDirectory() as directory:
        queue = BroadcastQueue(Path(directory) / "broadcast.sqlite3")
        campaign = queue.create_campaign(bot_scope="reward", created_by=1, payload={"text": "hello"})
        assert queue.queue_campaign(campaign, [10, 11, 11]) == 2
        claimed = queue.claim(limit=1)
        assert len(claimed) == 1
        assert queue.complete(claimed[0]["delivery_id"], 99)
        claimed_again = queue.claim(limit=5)
        assert len(claimed_again) == 1
        assert queue.fail(claimed_again[0]["delivery_id"], "temporary", retry_seconds=1)
        future = queue.create_campaign(bot_scope="partnership", created_by=1,
                                       payload={"text": "later"},
                                       scheduled_at=int(time.time()) + 3600)
        assert queue.queue_campaign(future, [12]) == 1
        assert queue.claim(bot_scope="reward", limit=10) == []
        assert queue.claim(bot_scope="partnership", limit=10) == []
