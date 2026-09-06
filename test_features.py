"""Offline audit tests for the persistent product features."""
import os
import tempfile

from store import JsonStore
from channel_registry import ChannelRegistry
from core import CreditLedger
from features import (ReferralLedger, AvailablePostQueue, BubbleNotifier,
                      AnnouncementBoard, RankVisibility, StatsBook, category_counts)
from governance import daily_post_cap
import ui


def fresh():
    d = tempfile.mkdtemp(); return JsonStore(os.path.join(d, "state.json"))


def test_channels_are_owned_and_independent():
    s = fresh(); r = ChannelRegistry(s)
    r.add(10, "-1001", "@one", "channel", ["AI Tools", "DeFi"], 100)
    r.add(10, "-1002", "@two", "group", ["General"], 20)
    assert len(r.mine(10)) == 2
    r.set_bot_access("-1001", False)
    assert r.get("-1001")["band"] == "C"
    assert r.remove(10, "-1002") and not r.remove(11, "-1001")


def test_referral_requires_completed_forward():
    s = fresh(); ledger = CreditLedger(s); refs = ReferralLedger(s, ledger)
    code = refs.create_code(1); assert refs.attach(2, code)
    assert refs.stats(1)["completed"] == 0
    assert refs.complete_forward(2)
    assert ledger.balance("@1")["balance"] == 3
    assert not refs.complete_forward(2)


def test_queue_owner_first_and_claims():
    s = fresh(); q = AvailablePostQueue(s)
    q.add({"id": "normal", "category": "AI Tools"})
    q.add({"id": "owner", "category": "General"}, owner=True)
    assert [x["id"] for x in q.available()] == ["owner", "normal"]
    assert q.claim("owner", 7)["claimed_by"] == 7
    assert q.count() == 1


def test_bubble_is_single_replaceable_record():
    s = fresh(); n = BubbleNotifier(s)
    n.set(4, 1, 20); n.set(4, 3, 21)
    assert n.get(4) == {"count": 3, "message_id": 21, "updated_at": n.get(4)["updated_at"]}


def test_role_counters_and_unconnected_limit():
    assert daily_post_cap(5000, "A", connected=False) == 3
    assert daily_post_cap(5000, "A", connected=True) == 4
    user_labels = [b.text for row in ui.main_menu("user", include_partnership=False).inline_keyboard for b in row]
    assert not any("Audit" in text for text in user_labels)
    counts = {"Review Queue": 2, "Pending Posts": 120, "Scheduled Posts": 0, "Direct Delivery": 1}
    labels = [b.text for row in ui.audit_menu(counts).inline_keyboard for b in row]
    assert any("Review Queue · 2" in text for text in labels)
    assert any("Pending Posts · 99+" in text for text in labels)


def test_announcements_stats_and_visibility():
    s = fresh(); board = AnnouncementBoard(s)
    board.publish(1, "hello", ["DeFi"]); board.publish(1, "all")
    assert len(board.recent("DeFi")) == 2
    stats = StatsBook(s); stats.record("@one", subscribers=100, views=80, reactions=4)
    assert stats.provider("@one")["views"] == 80
    vis = RankVisibility(s); assert not vis.public()
    assert vis.set_public(1, True, 1) and vis.public()
    assert category_counts([{"categories": ["DeFi", "AI Tools"]}]) == {"DeFi": 1, "AI Tools": 1}


if __name__ == "__main__":
    tests = [v for k, v in globals().items() if k.startswith("test_")]
    for test in tests:
        test(); print("PASS", test.__name__)
    print(f"\n{len(tests)}/{len(tests)} feature tests passed")
