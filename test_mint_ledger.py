"""Offline tests for the transactional Mint/referral foundation.

This test module intentionally uses only the Python standard library so the
zero-dependency local test runner remains usable before the production database
migration.
"""
from contextlib import contextmanager
from pathlib import Path
import tempfile

from mint_ledger import InsufficientMint, TransactionalMintLedger


@contextmanager
def fresh():
    with tempfile.TemporaryDirectory(prefix="clickmint-mint-test-") as directory:
        yield TransactionalMintLedger(Path(directory) / "mint.sqlite3")


def expect(exception, fn):
    try:
        fn()
    except exception:
        return
    raise AssertionError(f"expected {exception.__name__}")


def test_idempotent_credit_and_append_only_history():
    with fresh() as db:
        first = db.credit("100", 10, entry_type="ONBOARDING", idempotency_key="seed:100")
        second = db.credit("100", 10, entry_type="ONBOARDING", idempotency_key="seed:100")
        assert first == second
        assert db.balance("100") == 10
        assert len(db.entries("100")) == 1


def test_atomic_debit_prevents_negative_balance():
    with fresh() as db:
        db.credit("100", 2, entry_type="TEST", idempotency_key="credit:100")
        expect(InsufficientMint, lambda: db.debit("100", 3, entry_type="POSTING_SPEND", idempotency_key="debit:100"))
        assert db.balance("100") == 2


def test_post_order_debits_once_and_refunds_as_compensating_entry():
    with fresh() as db:
        db.credit("100", 5, entry_type="TEST", idempotency_key="credit:100")
        order = db.create_post_order("100", amount=2, source_id="post-1", idempotency_key="order:1")
        assert db.create_post_order("100", amount=2, source_id="post-1", idempotency_key="order:1") == order
        assert db.balance("100") == 3
        assert db.complete_post_order(order)
        refund = db.refund_post_order(order, reason="delivery failed")
        assert refund
        assert db.refund_post_order(order, reason="delivery failed") is None
        assert db.balance("100") == 5
        assert {row["entry_type"] for row in db.entries("100")} == {"TEST", "POSTING_SPEND", "POSTING_REFUND"}


def test_first_touch_referral_qualification_and_duplicate_protection():
    with fresh() as db:
        code_a = db.create_referral_code("referrer-a")
        code_b = db.create_referral_code("referrer-b")
        assert db.attach_referral("new-user", code_a)
        assert not db.attach_referral("new-user", code_b)
        assert db.qualify_referral("new-user", activity_id="qualified-post", reward_amount=7)
        assert not db.qualify_referral("new-user", activity_id="qualified-post", reward_amount=7)
        assert db.confirm_referral_reward("new-user", activity_id="qualified-post")
        assert db.balance("referrer-a") == 7
        assert db.balance("referrer-b") == 0
        assert db.confirm_referral_reward("new-user", activity_id="qualified-post") is None


def test_open_fraud_flag_blocks_referral_confirmation():
    with fresh() as db:
        code = db.create_referral_code("referrer")
        assert db.attach_referral("new-user", code)
        db.flag_fraud("new-user", flag_type="sybil_cluster", reason="test flag")
        assert not db.qualify_referral("new-user", activity_id="activity-1")
        assert db.balance("referrer") == 0


def test_pending_credit_is_not_spendable_until_confirmed():
    with fresh() as db:
        entry = db.credit("100", 4, entry_type="CAMPAIGN_REWARD", idempotency_key="pending:1", pending=True)
        assert db.balance("100") == 0
        assert db.balance("100", include_pending=True) == 4
        assert db.confirm_entry(entry)
        assert db.balance("100") == 4


def test_outbox_is_idempotent_and_retryable():
    with fresh() as db:
        first = db.enqueue_outbox(event_type="MINT_REWARD", recipient_id=100, payload="hello", idempotency_key="notice:1")
        assert db.enqueue_outbox(event_type="MINT_REWARD", recipient_id=100, payload="hello", idempotency_key="notice:1") == first
        claimed = db.claim_outbox()
        assert len(claimed) == 1 and claimed[0]["status"] == "processing"
        assert db.fail_outbox(first, "temporary", retry_delay=0)
        claimed_again = db.claim_outbox()
        assert len(claimed_again) == 1 and claimed_again[0]["attempts"] == 2
        assert db.complete_outbox(first)
        assert db.claim_outbox() == []
        performance = db.outbox_performance()
        assert performance["total"] == 1
        assert performance["retry_rate"] == 1.0


def test_admin_can_pause_and_retry_failed_outbox_event():
    with fresh() as db:
        event = db.enqueue_outbox(event_type="TASK_MINT_REWARD", recipient_id=7,
                                  payload="{}", idempotency_key="task:pause")
        assert db.get_outbox_event(event)["event_type"] == "TASK_MINT_REWARD"
        db.claim_outbox()
        assert db.fail_outbox(event, "manual pause", permanent=True)
        assert db.claim_outbox() == []
        assert db.retry_outbox(event)
        assert len(db.claim_outbox()) == 1


def test_channel_permission_audit_is_queryable():
    with fresh() as db:
        db.add_audit_event(
            actor_type="telegram", actor_id="42",
            action="CHANNEL_STATUS_DEGRADED", object_type="channel",
            object_id="-1001", reason="ACTIVE -> DEGRADED: cannot post",
        )
        db.add_audit_event(
            actor_type="telegram", actor_id="42",
            action="CHANNEL_STATUS_ACTIVE", object_type="channel",
            object_id="-1001", reason="DEGRADED -> ACTIVE: verified",
        )
        events = db.recent_audit_events(object_type="channel", limit=10)
        assert len(events) == 2
        assert {event["action"] for event in events} == {
            "CHANNEL_STATUS_ACTIVE", "CHANNEL_STATUS_DEGRADED"
        }
        assert all(event["object_id"] == "-1001" for event in events)
        db.add_audit_event(actor_type="admin", actor_id="9",
                           action="OUTBOX_RETRY", object_type="outbox",
                           object_id="outbox-1", reason="manual retry")
        outbox_events = db.recent_audit_events(object_type="outbox")
        assert outbox_events[0]["actor_id"] == "9"
        db.add_audit_event(actor_type="system", actor_id="health-monitor",
                           action="OUTBOX_ALERT_RAISED", object_type="outbox_health",
                           object_id="global", reason="failed events")
        assert len(db.active_health_alerts()) == 1
        db.add_audit_event(actor_type="system", actor_id="health-monitor",
                           action="OUTBOX_ALERT_CLEARED", object_type="outbox_health",
                           object_id="global", reason="failed events")
        assert db.active_health_alerts() == []


def test_referral_history_and_reversal_are_auditable():
    with fresh() as db:
        code = db.create_referral_code("referrer")
        assert db.attach_referral("new", code)
        assert db.qualify_referral("new", activity_id="activity")
        entry = db.confirm_referral_reward("new", activity_id="activity")
        assert db.referral_stats("referrer")["qualified"] == 1
        assert len(db.referral_history("referrer")) == 1
        snapshot = db.create_referral_ranking_snapshot(
            period="2026-09", pool=10, winners=1,
            ranking=[{"referrer_id": "referrer", "score": 80}],
            allocation=[{"referrer_id": "referrer", "reward_amount": 10}],
        )
        assert snapshot["status"] == "preview"
        assert db.approve_referral_ranking("2026-09", "admin-1")
        assert db.referral_ranking_snapshot("2026-09")["status"] == "approved"
        reverse = db.reverse(entry, idempotency_key="reverse:1", reason="fraud review")
        assert reverse and db.balance("referrer") == 0
        assert db.entries("referrer")[0]["entry_type"] == "REVERSAL"


if __name__ == "__main__":
    tests = [value for name, value in globals().items() if name.startswith("test_")]
    for test in tests:
        test()
        print("PASS", test.__name__)
    print(f"\n{len(tests)}/{len(tests)} transactional Mint tests passed")
