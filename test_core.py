"""Offline verification of the reward/partnership engine. Run: python test_core.py"""
import tempfile
from core import (CreditLedger, Contract, DeliveryLog,
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


# ---------------------------------------------------------------------------
# Regression tests added by the 2026-09 audit
# ---------------------------------------------------------------------------
def test_report_context_cannot_overwrite_report_fields():
    """A report is filed as PENDING even when the caller passes a delivery-log
    row (which carries its own status/sender/id) as the post context."""
    s, f = new_ledger_store()
    rep = ReportRegistry(s)
    delivery_row = {"id": 999, "status": "delivered", "sender": "@notthisone",
                    "target_channel": "@receiver", "post_type": "Airdrops"}
    item = rep.report(sender="@badchan", reporter="@receiver",
                      reported_post=delivery_row, reason="scam")
    assert item["status"] == "pending"          # not "delivered"
    assert item["sender"] == "@badchan"         # not the row's sender
    assert item["id"] != 999
    assert item["post"]["status"] == "delivered"    # context is kept, but nested
    assert len(rep.pending()) == 1
    # ids stay unique even after the list is pruned
    second = rep.report(sender="@x", reporter="@y", reported_post={})
    assert second["id"] != item["id"]
    print("OK report context can't clobber report bookkeeping")


def test_no_auto_ban_on_report():
    """A filed report never changes a channel's status by itself."""
    ledger, perf = make_perf()
    ledger.register("@accused", 1000)
    rep = ReportRegistry(perf.store)
    for _ in range(5):                      # five reports, still no ban
        rep.report(sender="@accused", reporter="@r", reported_post={})
    assert perf.status("@accused") == "ACTIVE"
    assert "@accused" in perf.match("@accused", 5) + ["@accused"]
    # only a human confirmation + explicit status change restricts it
    pending = rep.pending()
    assert len(pending) == 5
    rep.confirm(pending[0]["id"], action="restrict")
    assert perf.status("@accused") == "ACTIVE"      # still not automatic
    perf.set_status("@accused", "RESTRICTED")       # the human acts
    assert perf.status("@accused") == "RESTRICTED"
    print("OK no auto-ban: reports need a human decision")


def test_daily_cap_accounting_is_the_senders():
    ledger, f = new_ledger()
    ledger.register("@sender", 3000)
    assert ledger.cap_used("@sender") == 0
    assert ledger.cap_left("@sender", 3) == 3
    ok, _ = ledger.consume_cap("@sender", 3, 2)
    assert ok and ledger.cap_used("@sender") == 2 and ledger.cap_left("@sender", 3) == 1
    # over-consuming is refused ATOMICALLY (nothing partially consumed)
    ok, why = ledger.consume_cap("@sender", 3, 2)
    assert not ok and ledger.cap_used("@sender") == 2
    ok, _ = ledger.consume_cap("@sender", 3, 1)
    assert ok and ledger.cap_left("@sender", 3) == 0
    print("OK daily cap accounting (sender-side, atomic)")


def test_owner_cap_is_unlimited():
    ledger, f = new_ledger()
    ledger.register(OWNER_USERNAME, 630, is_owner=True)
    assert ledger.cap_left(OWNER_USERNAME, -1) == -1
    ok, _ = ledger.consume_cap(OWNER_USERNAME, -1, 50)
    assert ok
    assert ledger.cap_used(OWNER_USERNAME) == 0     # never charged
    print("OK owner bypasses the daily cap")


def test_refund_does_not_count_as_earning():
    ledger, f = new_ledger()
    ledger.register("@a", 800)
    ledger.earn("@a")                    # earned 1 -> may spend
    ok, _ = ledger.spend("@a", 2)
    assert ok
    before = ledger.balance("@a")
    ledger.refund("@a", 2)
    after = ledger.balance("@a")
    assert after["balance"] == before["balance"] + 2
    assert after["spent"] == before["spent"] - 2
    assert after["earned"] == before["earned"]      # a refund is NOT an earn
    print("OK refund restores credits without faking an earn")


def test_match_respects_target_receive_types():
    """Quality/contract rule: never offer a category a channel doesn't receive."""
    ledger, perf = make_perf()
    for u in ("@sender", "@airdropsonly", "@anything"):
        ledger.register(u, 2000)
        perf.mark_offered(u)
        perf.mark_posted(u)
    ledger._m("@airdropsonly")["receive_types"] = ["Airdrops"]
    ledger._m("@anything")["receive_types"] = []
    ledger.save()
    got = perf.match("@sender", want_channels=5, post_type="DeFi")
    assert "@airdropsonly" not in got
    assert "@anything" in got
    got2 = perf.match("@sender", want_channels=5, post_type="Airdrops")
    assert "@airdropsonly" in got2
    print("OK matching honours each target's receive contract")


def test_views_provider_failure_never_fabricates():
    """A broken/hostile provider degrades to the proxy — it never invents views."""
    ledger, perf = make_perf()
    ledger.register("@v", 1000)

    def boom(_):
        raise RuntimeError("provider down")

    perf.views_provider = boom
    s = perf.score("@v")
    assert s["views"] is None and "reach" not in s["components"]
    # junk payloads are ignored rather than trusted
    for junk in ({"views": "1200"}, {"views": -5}, {"views": None}, "not-a-dict"):
        perf.views_provider = lambda _u, j=junk: j
        assert perf.score("@v")["views"] is None
    # a real payload IS used, and a live subs count beats the stored size
    perf.views_provider = lambda _u: {"views": 300, "forwards": 10, "reactions": 20,
                                      "subs": 1000}
    s2 = perf.score("@v")
    assert s2["views"] == 300 and "reach" in s2["components"]
    assert 0 < s2["components"]["engagement"] <= 1.0
    print("OK view provider: real data only, never fabricated")


def test_store_is_atomic_and_merges_concurrent_writers():
    """All three bots share these files; one must not clobber another's keys."""
    from store import JsonStore
    import json as _json
    f = tempfile.NamedTemporaryFile(delete=False)
    f.close()
    a = JsonStore(f.name)          # e.g. the reward bot
    b = JsonStore(f.name)          # e.g. the admin bot, same file
    a["ledger"] = {"@x": 1}
    a.sync()
    b.reload()
    b["review_queue"] = [{"id": 1}]
    b.sync()                       # b must not wipe a's ledger
    a["ledger"] = {"@x": 2}
    a.sync()                       # a must not wipe b's review queue
    on_disk = _json.load(open(f.name))
    assert on_disk["ledger"] == {"@x": 2}
    assert on_disk["review_queue"] == [{"id": 1}]
    # the file is always valid JSON (written via a temp file + atomic replace)
    assert _json.load(open(f.name))
    print("OK JSON store: atomic writes + no lost updates across bots")


def test_forward_only_still_blocks_copies():
    """The forward-only guarantee, restated as a regression test."""
    class Copy:
        forward_from = None
        forward_from_chat = None
        forward_origin = None
    assert is_forward(Copy()) is False
    class OriginOnly:                     # Bot API 7.0+ style forward
        forward_from = None
        forward_from_chat = None
        forward_origin = object()
    assert is_forward(OriginOnly()) is True
    assert forward_source(OriginOnly()).startswith("origin:")
    print("OK forward-only accepts genuine forwards only")



# --- added after the 2026-09 dry-run simulation (simulate.py) ---------------
def test_owner_is_exempt_from_the_very_first_message():
    """The owner id used to be linked to the ledger row only inside the forward
    handler, so an owner who typed /balance (or opened any menu) first was
    treated as an ordinary member: charged credits and capped."""
    ledger, _ = new_ledger()
    ledger.owner_user_id = 777001
    ledger.set_user_id("@bossman", 777001)       # what the bots now do on EVERY update
    assert ledger._is_exempt("@bossman")
    assert ledger._m("@bossman")["is_owner"] is True
    ok, _why = ledger.can_spend("@bossman")
    assert ok, "owner must be able to route before earning anything"
    print("OK owner exemption is sealed on first contact (not only after a forward)")


def test_owner_username_match_is_case_insensitive():
    """Telegram treats @ClickMintHQ and @clickminthq as the same account; an
    exact-case compare silently demoted the owner to member rules."""
    ledger, _ = new_ledger()
    assert ledger._is_exempt(OWNER_USERNAME.lower())
    assert ledger._is_exempt(OWNER_USERNAME.upper())
    print("OK owner @username is matched case-insensitively")


def test_best_performer_is_not_stranded_by_band_matching():
    """Strict band equality deadlocked the network: the first channel to
    out-perform everyone became the only member of band A, so match() returned
    [] and its posts could never be distributed."""
    ledger, perf = make_perf()
    for u in ("@star", "@mid1", "@mid2"):
        ledger.register(u, 2000)
    perf.mark_offered("@star"); perf.mark_posted("@star")      # band A, alone
    for u in ("@mid1", "@mid2"):
        perf.mark_offered(u); perf.mark_offered(u); perf.mark_posted(u)   # lower band
    assert perf.score("@star")["band"] == "A"
    assert all(perf.score(u)["band"] != "A" for u in ("@mid1", "@mid2"))
    got = perf.match("@star", want_channels=5)
    assert got, "the top performer must still reach somebody"
    assert set(got) <= {"@mid1", "@mid2"}
    print("OK band matching widens instead of stranding the best channel")


def test_band_widening_never_overrides_a_same_band_peer():
    """Widening is a fallback only: if same-band peers exist they win outright,
    so 'quality over size' still governs who sees a post first."""
    ledger, perf = make_perf()
    for u in ("@a", "@peer", "@weak"):
        ledger.register(u, 2000)
    for u in ("@a", "@peer"):
        perf.mark_offered(u); perf.mark_posted(u)
    perf.mark_offered("@weak"); perf.mark_offered("@weak")     # offered, never posted
    got = perf.match("@a", want_channels=5)
    assert got == ["@peer"], got
    print("OK same-band peers still take precedence over widened matches")


def test_match_honours_min_status():
    """`min_status` was accepted and then ignored entirely."""
    ledger, perf = make_perf()
    for u in ("@a", "@watched"):
        ledger.register(u, 1500)
        perf.mark_offered(u); perf.mark_posted(u)
    perf.set_status("@watched", "WATCH")
    assert "@watched" in perf.match("@a", 5)                   # default: WATCH is fine
    assert "@watched" not in perf.match("@a", 5, min_status="ACTIVE")
    print("OK match() actually applies min_status")


ALL_TESTS = [
    test_tiering, test_credit_lifecycle, test_anticheat,
    test_subscriber_tier_is_not_the_gate, test_owner_exemption,
    test_partnership_contract, test_forward_only, test_delivery_log,
    test_report_system, test_performance_score_and_demote,
    test_performance_match_like_with_like, test_views_pluggable,
    test_direct_mode, test_scheduler_agreed_time,
    # --- added by the 2026-09 audit ---
    test_report_context_cannot_overwrite_report_fields,
    test_no_auto_ban_on_report,
    test_daily_cap_accounting_is_the_senders,
    test_owner_cap_is_unlimited,
    test_refund_does_not_count_as_earning,
    test_match_respects_target_receive_types,
    test_views_provider_failure_never_fabricates,
    test_store_is_atomic_and_merges_concurrent_writers,
    test_forward_only_still_blocks_copies,
    # --- added after the dry-run simulation ---
    test_owner_is_exempt_from_the_very_first_message,
    test_owner_username_match_is_case_insensitive,
    test_best_performer_is_not_stranded_by_band_matching,
    test_band_widening_never_overrides_a_same_band_peer,
    test_match_honours_min_status,
]


# ONE runner, at the very END of the file. (There used to be three stacked
# runners here: the suite ran three times and the last two lists were stale, so
# newer tests were silently skipped on those passes.) Exits non-zero on failure
# so CI actually fails.
if __name__ == "__main__":
    import sys
    import traceback

    failed = []
    for fn in ALL_TESTS:
        try:
            fn()
        except Exception as exc:            # noqa: BLE001 - test runner
            failed.append(fn.__name__)
            print(f"FAIL {fn.__name__}: {exc}")
            traceback.print_exc()
    total = len(ALL_TESTS)
    if failed:
        print(f"\n{total - len(failed)}/{total} passed — FAILED: {failed}")
        sys.exit(1)
    print(f"\nALL TESTS PASSED ({total})")
