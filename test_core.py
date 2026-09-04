"""Offline verification of the reward/partnership engine. Run: python test_core.py"""
import json, tempfile, os
from core import (CreditLedger, Distribution, Contract, DeliveryLog,
                  ReportRegistry, PerformanceEngine, tier_for_size,
                  OWNER_USERNAME, is_forward, FORWARD_ONLY, forward_source)

def new_ledger():
    f = tempfile.NamedTemporaryFile(delete=False)
    f.close()
    from store import JsonStore
    s = JsonStore(f.name)
    return CreditLedger(s), f.name

def test_tiering():
    assert tier_for_size(800) == "T1"
    assert tier_for_size(1000) == "T1"
    assert tier_for_size(2500) == "T2"
    assert tier_for_size(3200) == "T2"
    assert tier_for_size(4500) == "T3"
    assert tier_for_size(9000) == "T4"
    assert tier_for_size(500) == "T1"   # below range -> smallest tier
    print("OK tiering")

def test_credit_lifecycle():
    ledger, f = new_ledger()
    # register members (exact 1:1 rules)
    ledger.register("@a", 800)     # T1
    ledger.register("@b", 900)     # T1
    ledger.register("@c", 2500)    # T2
    # new member gets +2 seed
    assert ledger.balance("@a")["balance"] == 2
    # earn-first: @a can't spend before sharing at least one of others' posts
    ok, why = ledger.can_spend("@a")
    assert not ok, why
    # @a shares @b's post -> earns 1
    ledger.earn("@a")
    assert ledger.balance("@a")["balance"] == 3
    assert ledger.balance("@a")["earned"] == 1
    # now @a CAN spend (earned >= 1)
    ok, _ = ledger.can_spend("@a")
    assert ok
    # spend 1 pair
    ok, _ = ledger.spend("@a", 1)
    assert ok
    assert ledger.balance("@a")["balance"] == 2
    print("OK credit lifecycle")

def test_anticheat():
    ledger, f = new_ledger()
    ledger.register("@a", 800)
    # earn 5 credits
    for _ in range(5):
        ledger.earn("@a")
    assert ledger.balance("@a")["balance"] == 2 + 5
    # spending beyond balance fails
    ok, _ = ledger.spend("@a", 5)   # balance is 7, so ok
    assert ok
    ok, why = ledger.spend("@a", 99)
    assert not ok, why   # can't overspend
    print("OK anti-cheat (per-pair cost)")

def test_subscriber_tier_is_not_the_gate():
    # NEW behavior: matching is by PERFORMANCE band, NOT subscriber size tier.
    # A small high-performer can reach a big high-performer; size is only a tiebreaker.
    ledger, perf = make_perf()
    ledger.register("@small", 800)    # small size
    ledger.register("@big", 5000)     # big size
    for u in ("@small", "@big"):
        perf.mark_offered(u)
        perf.mark_posted(u)           # both reliable performers
    # even though they're in different SUBSCRIBER tiers, both are band A -> matched
    match = perf.match("@small", want_channels=5)
    assert "@big" in match
    print("OK subscriber-tier is no longer the gate (performance is)")

def test_owner_exemption():
    ledger, f = new_ledger()
    ledger.register(OWNER_USERNAME, 630, is_owner=True)
    # owner never earns/spends through the mechanism; always can route
    ok, _ = ledger.can_spend(OWNER_USERNAME)
    assert ok
    ok, _ = ledger.spend(OWNER_USERNAME, 5)   # 5 channels, no credits consumed logically
    assert ok
    assert ledger.balance(OWNER_USERNAME)["spent"] == 0
    print("OK owner exemption")

def test_partnership_contract():
    c = Contract()
    member = {"receive_types": []}
    c.set_contract(member, accept_types=["Airdrops", "Scam Alerts"],
                   receive_types=["Airdrops"])
    assert c.allows_receive(member, "Airdrops")
    assert not c.allows_receive(member, "AI Tools")   # not in receive list
    assert c.allows_receive(member, "Anything") is False
    # empty receive = accept all
    m2 = {"receive_types": []}
    assert c.allows_receive(m2, "Anything")
    print("OK partnership terms contract")

def test_forward_only():
    assert FORWARD_ONLY is True
    # A real forward exposes forward_from / forward_from_chat / forward_origin
    class FakeMsg:
        forward_from = "@someone"
        forward_from_chat = None
        forward_origin = None
    assert is_forward(FakeMsg()) is True

    # A copi/re-typed/re-uploaded message has none of these -> REJECTED
    class FakeCopy:
        forward_from = None
        forward_from_chat = None
        forward_origin = None
    assert is_forward(FakeCopy()) is False
    # forward_source is safe for both cases (forward_from here is a string "@someone")
    src = forward_source(FakeMsg())
    assert src in {"user:@someone", "unknown"}
    # a chat-forward source resolves to chat:
    class FakeChat:
        forward_from = None
        forward_from_chat = (lambda: None)()
        forward_origin = None
    FakeChat.forward_from_chat = type("C", (), {"username": "@sourcechan"})()
    assert forward_source(FakeChat()) == "chat:@sourcechan"
    print("OK forward-only rule")


def test_delivery_log():
    s, f = new_ledger_store()
    log = DeliveryLog(s)
    log.record(bot="reward", sender="@a", source="chat:@sourcechan",
               post_type="Airdrops", target_channel="@b", mode="chain",
               status="offered", forward_valid=True)
    log.record(bot="reward", sender="@a", source="chat:@sourcechan",
               post_type="Airdrops", target_channel="@b", mode="chain",
               status="agreed", forward_valid=True)
    log.record(bot="reward", sender="@a", source="chat:@sourcechan",
               post_type="Airdrops", target_channel="@c", mode="chain",
               status="skipped", forward_valid=True)
    log.record(bot="reward", sender="@a", source="unknown", post_type="X",
               target_channel="@d", mode="direct", status="delivered",
               forward_valid=False)   # a copy slipped? flagged
    assert log.count() == 4
    assert "2" not in log.summary()   # just sanity
    assert len(log.invalid_records()) == 1
    assert "delivered" in log.summary()
    assert len(log.last(2)) == 2
    print("OK delivery log (audit trail)")


# helper for a fresh JsonStore path (reuse new_ledger store)
def new_ledger_store():
    f = tempfile.NamedTemporaryFile(delete=False)
    f.close()
    from store import JsonStore
    return JsonStore(f.name), f.name


def make_perf():
    s, f = new_ledger_store()
    ledger = CreditLedger(s)
    perf = PerformanceEngine(ledger, s, views_provider=None)
    return ledger, perf


def test_report_system():
    s, f = new_ledger_store()
    rep = ReportRegistry(s)
    item = rep.report(sender="@badchan", reporter="@receiver",
                      reported_post={"target_channel": "@receiver",
                                     "post_type": "Airdrops"},
                      reason="scam/fraud")
    assert item["status"] == "pending"
    assert len(rep.pending()) == 1
    # bot does NOT auto-ban; admin reviews:
    rep.confirm(item["id"], action="restrict")
    assert rep.reports_against("@badchan") == 1
    item2 = rep.report(sender="@goodchan", reporter="@receiver",
                       reported_post={"target_channel": "@receiver", "post_type": "Airdrops"})
    rep.clear(item2["id"])
    assert rep.reports_against("@goodchan") == 0   # cleared = no penalty
    print("OK report system (manual-review, no auto-ban)")


def test_performance_score_and_demote():
    ledger, perf = make_perf()
    ledger.register("@high", 2000)   # big, but we say it performs
    ledger.register("@low", 2000)    # same size, but underperforms
    # simulate reliability: @high posts every offer; @low posts none
    perf.mark_offered("@high"); perf.mark_posted("@high")
    perf.mark_offered("@low"); perf.mark_offered("@low")
    s_high = perf.score("@high")
    s_low = perf.score("@low")
    assert s_high["band"] != "C"          # reliable -> not low band
    # demote @low via a confirmed report -> status changes + excluded
    rep = ReportRegistry(perf.store)
    r = rep.report(sender="@low", reporter="@x",
                   reported_post={"target_channel": "@y", "post_type": "Airdrops"})
    rep.confirm(r["id"], action="restrict")
    perf.set_status("@low", "RESTRICTED")
    assert perf.status("@low") == "RESTRICTED"
    match = perf.match("@high", want_channels=5)
    assert "@low" not in match           # DEMOTED/blocked channels are excluded
    print("OK performance score + demote/remove exclusion")


def test_performance_match_like_with_like():
    ledger, perf = make_perf()
    # three high performers, two low performers (same sizes, to prove size is NOT the key)
    for u, performed in [("@a", True), ("@b", True), ("@c", True),
                         ("@d", False), ("@e", False)]:
        ledger.register(u, 2500)
        perf.mark_offered(u)
        if performed:
            perf.mark_posted(u)
    # @a is a performer -> matched with other performers, not with @d/@e (lows)
    band_a = perf.score("@a")["band"]
    match = perf.match("@a", want_channels=5)
    assert all(perf.score(m)["band"] == band_a for m in match)
    assert "@d" not in match and "@e" not in match
    print("OK performance match (like-with-like, size is only tiebreaker)")


def test_views_pluggable():
    ledger, perf = make_perf()
    ledger.register("@v", 1000)
    # provide a view source -> reach ratio drives the score up
    perf.views_provider = lambda u: {"views": 700, "forwards": 20, "subs": 1000}  # 70% reach
    s = perf.score("@v")
    assert s["views"] == 700
    assert "reach" in s["components"]
    # without a views source the score gracefully uses the proxy (no crash, no fake views)
    perf.views_provider = None
    s2 = perf.score("@v")
    assert "reach" not in s2["components"]
    print("OK pluggable view source (observer or proxy)")


def test_direct_mode():
    # When a channel grants the bot admin, it's flagged as direct -> delivery is
    # automatic (no accept/reject), and it's excluded from the chain offer.
    ledger, perf = make_perf()
    ledger.register("@directchan", 1500)
    ledger.register("@chainchan", 1500)
    assert ledger.is_direct("@directchan") is False
    ledger.grant_direct("@directchan")
    assert ledger.is_direct("@directchan") is True
    ledger.revoke_direct("@directchan")
    assert ledger.is_direct("@directchan") is False
    print("OK direct-mode flag (grant/revoke)")


def test_scheduler_agreed_time():
    import time as _t
    from scheduler import Scheduler
    s, f = new_ledger_store()
    sc = Scheduler(s)
    now = _t.time()
    # schedule two posts: one in the past (should be due), one far future
    rec_past = sc.schedule(at=now - 10, target="@a", sender="@x")
    rec_future = sc.schedule(at=now + 10_000, target="@b", sender="@y")
    assert sc.pending_count() == 2
    # only the past one is due
    due = sc.due()
    assert len(due) == 1 and due[0]["id"] == rec_past["id"]
    # marking done removes it from future/due and counts as done
    sc.mark_done(rec_past["id"])
    assert sc.done_count() == 1
    assert sc.due() == []
    # cancel a future one
    assert sc.cancel(rec_future["id"]) is True
    assert sc.pending_count() == 0
    print("OK agreed-time scheduler (due, mark_done, cancel)")


if __name__ == "__main__":
    for fn in [test_tiering, test_credit_lifecycle, test_anticheat,
               test_subscriber_tier_is_not_the_gate, test_owner_exemption,
               test_partnership_contract, test_forward_only, test_delivery_log,
               test_report_system, test_performance_score_and_demote,
               test_performance_match_like_with_like, test_views_pluggable,
               test_direct_mode, test_scheduler_agreed_time]:
        fn()
    print("\nALL TESTS PASSED")


if __name__ == "__main__":
    for fn in [test_tiering, test_credit_lifecycle, test_anticheat,
               test_subscriber_tier_is_not_the_gate, test_owner_exemption,
               test_partnership_contract, test_forward_only, test_delivery_log,
               test_report_system, test_performance_score_and_demote,
               test_performance_match_like_with_like, test_views_pluggable,
               test_direct_mode]:
        fn()
    print("\nALL TESTS PASSED")


if __name__ == "__main__":
    for fn in [test_tiering, test_credit_lifecycle, test_anticheat,
               test_subscriber_tier_is_not_the_gate, test_owner_exemption,
               test_partnership_contract, test_forward_only, test_delivery_log,
               test_report_system, test_performance_score_and_demote,
               test_performance_match_like_with_like, test_views_pluggable]:
        fn()
    print("\nALL TESTS PASSED")
