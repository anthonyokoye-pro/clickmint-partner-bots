"""Bot WIRING tests — the layer the old suite never touched.

`test_core.py` and `test_governance.py` prove the rules engine. Nothing proved
that the three aiogram bots are actually wired to that engine, which is exactly
where the 2026-09 audit found its worst bugs:

  • every inline button was built with a positional argument, which aiogram 3
    rejects — so every menu raised TypeError at runtime,
  • the `s:` callback decorator sat on a plain helper function, so `pick_spend`
    was never registered and tapping "1..5" silently did nothing,
  • the admin bot's message handler used `types.Message` as a *filter*,
  • the submission funnel kept its state in global keys, so two members using
    the bot at once overwrote each other.

These tests drive real Update objects through each Dispatcher against a mocked
Telegram session, so nothing here touches the network. Run: python3 test_bots.py
"""
from __future__ import annotations

import asyncio
import datetime as dt
import inspect
import logging
import os
import sys
import tempfile
import traceback

# --- isolate the stores + a known owner BEFORE the bot modules are imported ---
_TMP = tempfile.mkdtemp(prefix="clickmint-tests-")
os.environ["STORE_DIR"] = _TMP
os.environ["REWARD_BOT_TOKEN"] = "111111:TESTTOKENTESTTOKENTESTTOKENTESTTOKEN"
os.environ["PARTNER_BOT_TOKEN"] = "222222:TESTTOKENTESTTOKENTESTTOKENTESTTOKEN"
os.environ["ADMIN_BOT_TOKEN"] = "333333:TESTTOKENTESTTOKENTESTTOKENTESTTOKEN"
OWNER_ID = 555000555
os.environ["OWNER_USER_ID"] = str(OWNER_ID)

from aiogram.client.session.base import BaseSession                # noqa: E402
from aiogram.types import (CallbackQuery, Chat, Message,           # noqa: E402
                           MessageOriginUser, Update, User)

import admin_bot                                                   # noqa: E402
import partnership_bot                                             # noqa: E402
import reward_bot                                                  # noqa: E402

logging.getLogger("aiogram").setLevel(logging.WARNING)   # keep the output readable


# ---------------------------------------------------------------------------
# A Telegram session that answers every API call locally.
# ---------------------------------------------------------------------------
class MockSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.calls: list[tuple[str, dict]] = []

    async def close(self):
        pass

    async def stream_content(self, *args, **kwargs):    # pragma: no cover
        yield b""

    async def make_request(self, bot, method, timeout=None):
        name = type(method).__name__
        data = method.model_dump(exclude_none=True)
        self.calls.append((name, data))
        if name in ("SendMessage", "ForwardMessage", "EditMessageText"):
            return Message(message_id=len(self.calls) + 1000,
                           date=dt.datetime.now(dt.timezone.utc),
                           chat=Chat(id=1, type="private"),
                           text=str(data.get("text", "")))
        return True

    # convenience helpers for the assertions
    def sent(self) -> list[dict]:
        return [d for n, d in self.calls if n == "SendMessage"]

    def edits(self) -> list[dict]:
        return [d for n, d in self.calls if n == "EditMessageText"]

    def texts(self) -> str:
        return "\n".join(str(d.get("text", "")) for _, d in self.calls)

    def names(self) -> list[str]:
        return [n for n, _ in self.calls]

    def reset(self):
        self.calls.clear()


def _fresh_bot(module) -> MockSession:
    """Give a bot module a fresh mocked session and clear its state file."""
    session = MockSession()
    module.bot.session = session
    module.store._data = {}
    module.store._dirty = set()
    if hasattr(module, "ledger"):
        module.ledger.ledger = {}
        module.ledger.store["ledger"] = {}
    module.store.sync()
    for attr in ("audit", "reports", "review", "contracts", "sched"):
        obj = getattr(module, attr, None)
        if obj is not None and hasattr(obj, "key"):
            module.store[obj.key] = []
    module.store["roles_users"] = {}
    module.store["roles_invites"] = {}
    module.store["sessions"] = {}
    module.store.sync()
    return session


def _errors_recorded(dp) -> list:
    """aiogram swallows handler exceptions into its error observer; capture them
    so a crashing handler fails the test instead of passing silently."""
    seen = []
    if not getattr(dp, "_test_error_hook", False):
        @dp.errors()
        async def _hook(event):
            seen.append(event.exception)
            return True
        dp._test_error_hook = True
        dp._test_errors = seen
    return dp._test_errors


# ---------------------------------------------------------------------------
# Update builders
# ---------------------------------------------------------------------------
_uid_seq = [9000]


def make_user(uid: int, username: str | None = "member") -> User:
    return User(id=uid, is_bot=False, first_name=username or "N", username=username)


def make_message(text: str, uid: int = 1001, username: str = "alice",
                 forwarded: bool = False, caption: str | None = None) -> Message:
    _uid_seq[0] += 1
    origin = None
    if forwarded:
        origin = MessageOriginUser(type="user",
                                   date=dt.datetime.now(dt.timezone.utc),
                                   sender_user=make_user(4242, "sourcechan"))
    return Message(message_id=_uid_seq[0],
                   date=dt.datetime.now(dt.timezone.utc),
                   chat=Chat(id=uid, type="private"),
                   from_user=make_user(uid, username),
                   text=text if caption is None else None,
                   caption=caption,
                   forward_origin=origin)


def make_callback(data: str, uid: int = 1001, username: str = "alice") -> CallbackQuery:
    _uid_seq[0] += 1
    msg = Message(message_id=_uid_seq[0],
                  date=dt.datetime.now(dt.timezone.utc),
                  chat=Chat(id=uid, type="private"),
                  from_user=make_user(0, "clickmintbot"),
                  text="menu")
    return CallbackQuery(id=str(_uid_seq[0]), from_user=make_user(uid, username),
                         chat_instance="ci", data=data, message=msg)


async def feed(module, event) -> list:
    dp = module.dp
    errs = _errors_recorded(dp)
    before = len(errs)
    if isinstance(event, Message):
        upd = Update(update_id=_uid_seq[0], message=event)
    else:
        upd = Update(update_id=_uid_seq[0], callback_query=event)
    await dp.feed_update(module.bot, upd)
    return errs[before:]


def run(coro):
    return asyncio.run(coro)


def assert_no_errors(errs, what: str):
    if errs:
        raise AssertionError(f"{what} raised: {errs[0]!r}\n" +
                             "".join(traceback.format_exception(errs[0])))


# ---------------------------------------------------------------------------
# 1) Handler registration
# ---------------------------------------------------------------------------
def test_all_handlers_are_coroutines():
    """A sync function registered as a handler returns a value instead of doing
    the work — that is how `pick_spend` went missing behind `owner_targets`."""
    for module in (reward_bot, partnership_bot, admin_bot):
        for obs_name, observer in module.dp.observers.items():
            for h in observer.handlers:
                cb = h.callback
                assert inspect.iscoroutinefunction(cb), (
                    f"{module.__name__}.{getattr(cb, '__name__', cb)} is registered "
                    f"on '{obs_name}' but is not async")
    print("OK every registered handler is a coroutine")


def test_expected_callbacks_are_registered():
    names = {h.callback.__name__ for h in reward_bot.dp.observers["callback_query"].handlers}
    for required in ("menu_nav", "accept_terms", "pick_category", "pick_style",
                     "pick_ntf", "pick_spend", "chain_delivery", "report_post",
                     "partner_flow", "panel", "gen_invite", "decide_review"):
        assert required in names, f"reward_bot: {required} is not registered"
    msg_names = {h.callback.__name__ for h in reward_bot.dp.observers["message"].handlers}
    for required in ("start", "register_cmd", "on_forward", "admin_login"):
        assert required in msg_names, f"reward_bot: {required} is not registered"
    assert "agree" not in msg_names, "the /agree credit printer must stay removed"
    print("OK reward bot registers the whole funnel")


def test_menus_build_without_positional_args():
    """aiogram 3 rejects positional InlineKeyboardButton args; every menu the
    bots can render must construct cleanly."""
    import ui
    for role in ("owner", "admin", "user"):
        assert ui.main_menu(role).inline_keyboard
        assert ui.role_menu(role).inline_keyboard
    assert reward_bot.back_btn_q("menu:hub").inline_keyboard
    assert admin_bot._dashboard_kb().inline_keyboard
    print("OK all shared menus build under aiogram 3")


# ---------------------------------------------------------------------------
# 2) Reward bot — registration, funnel, credits, caps
# ---------------------------------------------------------------------------
def test_start_registers_a_channel():
    s = _fresh_bot(reward_bot)
    errs = run(feed(reward_bot, make_message("/start @MyChan 1200", uid=1001)))
    assert_no_errors(errs, "/start with args")
    assert "@MyChan" in reward_bot.ledger.ledger
    assert reward_bot.ledger.ledger["@MyChan"]["size"] == 1200
    assert "Registered @MyChan" in s.texts()
    # a typo answers with help instead of exploding
    s.reset()
    errs = run(feed(reward_bot, make_message("/register @Other twelve", uid=1002)))
    assert_no_errors(errs, "/register with a bad number")
    assert "not a number" in s.texts()
    print("OK /start and /register actually register a channel")


def test_menu_and_cap_screens_render():
    s = _fresh_bot(reward_bot)
    run(feed(reward_bot, make_message("/start @MyChan 1200", uid=1001)))
    s.reset()
    for data in ("menu:hub", "menu:cap", "menu:contract", "menu:submit", "menu:partners"):
        errs = run(feed(reward_bot, make_callback(data, uid=1001, username="MyChan")))
        assert_no_errors(errs, f"callback {data}")
    assert s.edits(), "menu navigation produced no screen"
    print("OK menu screens render (hub/cap/contract/submit/partners)")


def _register(module, username: str, uid: int, size: int, **flags):
    m = module.ledger.register("@" + username, size, **flags)
    module.ledger.set_user_id("@" + username, uid)
    return m


def test_full_submission_funnel_distributes_and_charges():
    s = _fresh_bot(reward_bot)
    _register(reward_bot, "alice", 1001, 2500)
    _register(reward_bot, "bob", 1002, 2500)
    _register(reward_bot, "carol", 1003, 2500)
    reward_bot.ledger.earn("@alice")            # earn-first satisfied
    for u in ("@alice", "@bob", "@carol"):      # same performance band
        reward_bot.perf.mark_offered(u)
        reward_bot.perf.mark_posted(u)
    run(feed(reward_bot, make_callback("terms:accept", 1001, "alice")))
    run(feed(reward_bot, make_callback("cat:Airdrops", 1001, "alice")))
    s.reset()
    errs = run(feed(reward_bot, make_message("A verified airdrop guide.", uid=1001,
                                             username="alice", forwarded=True)))
    assert_no_errors(errs, "forwarding a post")
    assert "How many" in s.texts()
    before = reward_bot.ledger.balance("@alice")["balance"]
    s.reset()
    errs = run(feed(reward_bot, make_callback("s:1", 1001, "alice")))
    assert_no_errors(errs, "picking 1 pair")
    offers = [d for d in s.sent() if str(d.get("chat_id", "")).startswith("@")]
    assert offers, "no offer was sent to any target channel"
    after = reward_bot.ledger.balance("@alice")["balance"]
    assert after == before - 1, "one pair must cost exactly one credit"
    assert reward_bot.ledger.cap_used("@alice") == 1, "the SENDER's cap must be charged"
    assert reward_bot.ledger.cap_used("@bob") == 0, "receivers must not be charged"
    print("OK submission funnel: routes, charges 1 credit, uses the sender's cap")


def test_two_members_do_not_share_funnel_state():
    _fresh_bot(reward_bot)
    _register(reward_bot, "alice", 1001, 2500)
    _register(reward_bot, "bob", 1002, 2500)
    run(feed(reward_bot, make_callback("terms:accept", 1001, "alice")))
    run(feed(reward_bot, make_callback("cat:Airdrops", 1001, "alice")))
    run(feed(reward_bot, make_callback("terms:accept", 1002, "bob")))
    run(feed(reward_bot, make_callback("cat:DeFi", 1002, "bob")))
    assert reward_bot._session(1001)["cat"] == "Airdrops"
    assert reward_bot._session(1002)["cat"] == "DeFi"
    print("OK per-user session state (no cross-member clobbering)")


def test_forward_without_terms_is_refused():
    s = _fresh_bot(reward_bot)
    _register(reward_bot, "dave", 1004, 2500)
    errs = run(feed(reward_bot, make_message("hello", uid=1004, username="dave",
                                             forwarded=True)))
    assert_no_errors(errs, "forward before accepting terms")
    assert "accept the posting terms" in s.texts().lower()
    print("OK terms are required before a post is accepted")


def test_captioned_scam_post_is_still_gated():
    """The gate reads captions too — a media post carries its words there."""
    s = _fresh_bot(reward_bot)
    _register(reward_bot, "erin", 1005, 2500)
    reward_bot.ledger.earn("@erin")
    run(feed(reward_bot, make_callback("terms:accept", 1005, "erin")))
    run(feed(reward_bot, make_callback("cat:Airdrops", 1005, "erin")))
    s.reset()
    errs = run(feed(reward_bot, make_message("", uid=1005, username="erin",
                                             forwarded=True,
                                             caption="send me your seed phrase")))
    assert_no_errors(errs, "captioned submission")
    assert "Refused" in s.texts()
    assert reward_bot.perf.status("@erin") == "ACTIVE"   # refused, never banned
    print("OK captions go through the gate (and refusal is not a ban)")


def test_borderline_post_goes_to_human_review():
    s = _fresh_bot(reward_bot)
    _register(reward_bot, "frank", 1006, 2500)
    reward_bot.ledger.earn("@frank")
    run(feed(reward_bot, make_callback("terms:accept", 1006, "frank")))
    run(feed(reward_bot, make_callback("cat:Airdrops", 1006, "frank")))
    s.reset()
    errs = run(feed(reward_bot, make_message("earn money, limited time offer",
                                             uid=1006, username="frank",
                                             forwarded=True)))
    assert_no_errors(errs, "borderline submission")
    assert len(reward_bot.review.pending()) == 1
    assert "human review" in s.texts()
    assert reward_bot.perf.status("@frank") == "ACTIVE"
    print("OK borderline posts queue for a human, nobody is banned")


def test_chain_agree_credits_the_sharer_not_the_sender():
    _fresh_bot(reward_bot)
    _register(reward_bot, "alice", 1001, 2500)
    _register(reward_bot, "bob", 1002, 2500)
    sender_before = reward_bot.ledger.balance("@alice")["balance"]
    target_before = reward_bot.ledger.balance("@bob")["balance"]
    errs = run(feed(reward_bot, make_callback("chain:agree:@alice", 1002, "bob")))
    assert_no_errors(errs, "chain agree")
    assert reward_bot.ledger.balance("@bob")["balance"] == target_before + 1
    assert reward_bot.ledger.balance("@alice")["balance"] == sender_before
    print("OK the credit goes to the channel that shares, not the one that posts")


def test_report_is_filed_pending_for_a_human():
    s = _fresh_bot(reward_bot)
    _register(reward_bot, "alice", 1001, 2500)
    _register(reward_bot, "bob", 1002, 2500)
    errs = run(feed(reward_bot, make_callback("report:@alice:@bob", 1002, "bob")))
    assert_no_errors(errs, "reporting a post")
    pending = reward_bot.reports.pending()
    assert len(pending) == 1 and pending[0]["sender"] == "@alice"
    assert reward_bot.perf.status("@alice") == "ACTIVE"      # never auto-banned
    assert "nothing is auto-banned" in s.texts()
    print("OK a report is filed as pending for a human, with no auto-ban")


def test_owner_bypasses_credits_caps_and_funnel():
    s = _fresh_bot(reward_bot)
    _register(reward_bot, "owner", OWNER_ID, 630)
    for name, uid in (("bob", 1002), ("carol", 1003)):
        _register(reward_bot, name, uid, 2500)
    s.reset()
    # no terms, no category, no credits: the owner just forwards
    errs = run(feed(reward_bot, make_message("owner post", uid=OWNER_ID,
                                             username="owner", forwarded=True)))
    assert_no_errors(errs, "owner forward")
    assert "Owner mode" in s.texts()
    s.reset()
    errs = run(feed(reward_bot, make_callback("s:all", OWNER_ID, "owner")))
    assert_no_errors(errs, "owner routing to all")
    targets = {d.get("chat_id") for d in s.sent() if str(d.get("chat_id", "")).startswith("@")}
    assert {"@bob", "@carol"} <= targets
    assert reward_bot.ledger.balance("@owner")["spent"] == 0
    assert reward_bot.ledger.cap_used("@owner") == 0
    print("OK owner exemption end-to-end (no funnel, no credits, no cap)")


def test_non_owner_cannot_route_to_all():
    s = _fresh_bot(reward_bot)
    _register(reward_bot, "alice", 1001, 2500)
    _register(reward_bot, "bob", 1002, 2500)
    reward_bot._session_set(1001, pending={"user": "@alice", "post_type": "General",
                                           "from_chat_id": 1001, "from_message_id": 5})
    s.reset()
    errs = run(feed(reward_bot, make_callback("s:all", 1001, "alice")))
    assert_no_errors(errs, "non-owner s:all")
    assert not [d for d in s.sent() if str(d.get("chat_id", "")).startswith("@")]
    print("OK only the owner can route to every channel")


def test_admin_invite_codes_are_owner_only():
    s = _fresh_bot(reward_bot)
    errs = run(feed(reward_bot, make_callback("admin:invite:both", 1001, "alice")))
    assert_no_errors(errs, "non-owner invite attempt")
    assert not reward_bot.store.get("roles_invites"), "a non-owner generated a code"
    s.reset()
    errs = run(feed(reward_bot, make_callback("admin:invite:reward", OWNER_ID, "owner")))
    assert_no_errors(errs, "owner invite")
    codes = reward_bot.store.get("roles_invites") or {}
    assert len(codes) == 1
    code = list(codes)[0]
    # a scoped admin gets exactly that scope, and the code is single-use
    run(feed(reward_bot, make_message(f"/adminlogin {code}", uid=1007, username="mod")))
    assert reward_bot.roles.has_access(1007, "reward") is True
    assert reward_bot.roles.has_access(1007, "partnership") is False
    run(feed(reward_bot, make_message(f"/adminlogin {code}", uid=1008, username="mod2")))
    assert reward_bot.roles.has_access(1008, "reward") is False
    print("OK only the owner grants scoped, single-use admin codes")


def test_report_review_is_human_and_reversible():
    """A pending report only bites when a human taps an action."""
    s = _fresh_bot(reward_bot)
    _register(reward_bot, "owner", OWNER_ID, 630)
    _register(reward_bot, "alice", 1001, 2500)
    _register(reward_bot, "bob", 1002, 2500)
    run(feed(reward_bot, make_callback("report:@alice:@bob", 1002, "bob")))
    rid = reward_bot.reports.pending()[0]["id"]
    assert reward_bot.perf.status("@alice") == "ACTIVE"
    # a normal member cannot action it
    errs = run(feed(reward_bot, make_callback(f"rep:restrict:{rid}", 1001, "alice")))
    assert_no_errors(errs, "member trying to action a report")
    assert reward_bot.perf.status("@alice") == "ACTIVE"
    # the owner dismisses it -> still no penalty
    s.reset()
    errs = run(feed(reward_bot, make_callback(f"rep:clear:{rid}", OWNER_ID, "owner")))
    assert_no_errors(errs, "owner dismissing a report")
    assert reward_bot.perf.status("@alice") == "ACTIVE"
    assert reward_bot.reports.reports_against("@alice") == 0
    # a second report, this time confirmed -> the human restricts the channel
    run(feed(reward_bot, make_callback("report:@alice:@bob", 1002, "bob")))
    rid2 = reward_bot.reports.pending()[0]["id"]
    errs = run(feed(reward_bot, make_callback(f"rep:restrict:{rid2}", OWNER_ID, "owner")))
    assert_no_errors(errs, "owner restricting a channel")
    assert reward_bot.perf.status("@alice") == "RESTRICTED"
    print("OK reports are actioned by a human, both ways")


def test_owner_panels_all_render():
    s = _fresh_bot(reward_bot)
    _register(reward_bot, "owner", OWNER_ID, 630)
    _register(reward_bot, "alice", 1001, 2500)
    for data in ("panel:audit", "panel:reports", "panel:rank", "panel:direct",
                 "panel:scheduled", "panel:review", "panel:admins", "panel:terms"):
        s.reset()
        errs = run(feed(reward_bot, make_callback(data, OWNER_ID, "owner")))
        assert_no_errors(errs, f"owner {data}")
        assert s.edits(), f"{data} rendered nothing (dead button)"
    print("OK every owner panel button renders something")


def test_panels_are_closed_to_regular_users():
    s = _fresh_bot(reward_bot)
    _register(reward_bot, "alice", 1001, 2500)
    for data in ("panel:review", "panel:admins", "panel:audit", "panel:reports",
                 "panel:rank", "panel:direct", "panel:scheduled"):
        s.reset()
        errs = run(feed(reward_bot, make_callback(data, 1001, "alice")))
        assert_no_errors(errs, data)
        assert not s.edits(), f"{data} leaked a panel to a normal user"
    s.reset()
    errs = run(feed(reward_bot, make_message("/rank", uid=1001, username="alice")))
    assert_no_errors(errs, "/rank as user")
    assert "Owner/admin only" in s.texts()
    print("OK owner/admin panels stay closed to regular members")


# ---------------------------------------------------------------------------
# 3) Partnership bot
# ---------------------------------------------------------------------------
def test_partnership_start_and_contract():
    s = _fresh_bot(partnership_bot)
    errs = run(feed(partnership_bot, make_message("/start @PartnerChan 3000", uid=2001,
                                                  username="partner")))
    assert_no_errors(errs, "partnership /start")
    assert partnership_bot.ledger.ledger["@PartnerChan"]["is_partner"] is True
    # a bad subscriber count must not raise out of the handler
    s.reset()
    errs = run(feed(partnership_bot, make_message("/start @X lots", uid=2001,
                                                  username="partner")))
    assert_no_errors(errs, "partnership /start with a bad number")
    assert "Usage" in s.texts()
    # a user with NO telegram username used to crash the contract flow ("@"+None)
    s.reset()
    errs = run(feed(partnership_bot, make_message("/contract", uid=2002, username=None)))
    assert_no_errors(errs, "/contract without a username")
    errs = run(feed(partnership_bot, make_callback("ctype:Airdrops", 2002, None)))
    assert_no_errors(errs, "picking a contract type without a username")
    assert partnership_bot.ledger._m("@2002")["accept_types"] == ["Airdrops"]
    print("OK partnership registration + contract survive bad input")


def test_partnership_report_is_actually_filed():
    _fresh_bot(partnership_bot)
    partnership_bot.ledger.register("@a", 2000, is_partner=True)
    partnership_bot.ledger.register("@b", 2000, is_partner=True)
    errs = run(feed(partnership_bot, make_callback("chk:report:@a", 2003, "b")))
    assert_no_errors(errs, "partner report")
    assert len(partnership_bot.reports.pending()) == 1
    print("OK the partnership 'Report' button files a real report")


def test_partnership_offer_respects_receive_types_and_cap():
    s = _fresh_bot(partnership_bot)
    partnership_bot.ledger.register("@sender", 2000, is_partner=True)
    partnership_bot.ledger.set_user_id("@sender", 2010)
    wanted = partnership_bot.ledger.register("@wants", 2000, is_partner=True)
    wanted["receive_types"] = ["Airdrops"]
    other = partnership_bot.ledger.register("@doesnt", 2000, is_partner=True)
    other["receive_types"] = ["DeFi"]
    partnership_bot.ledger.save()
    run(feed(partnership_bot, make_callback("terms:accept", 2010, "sender")))
    run(feed(partnership_bot, make_callback("cat:Airdrops", 2010, "sender")))
    s.reset()
    errs = run(feed(partnership_bot, make_message("clean airdrop guide", uid=2010,
                                                  username="sender", forwarded=True)))
    assert_no_errors(errs, "partner forward")
    offered = {d.get("chat_id") for d in s.sent() if str(d.get("chat_id", "")).startswith("@")}
    assert offered == {"@wants"}, f"offered to {offered}"
    assert partnership_bot.ledger.cap_used("@sender") == 1, "the cap must be enforced here too"
    print("OK partnership offers honour receive types and the daily cap")


# ---------------------------------------------------------------------------
# 4) Admin bot
# ---------------------------------------------------------------------------
def test_owner_menu_is_not_reachable_by_a_member():
    s = _fresh_bot(reward_bot)
    _register(reward_bot, "alice", 1001, 2500)
    for data in ("menu:owner", "menu:admin"):
        s.reset()
        errs = run(feed(reward_bot, make_callback(data, 1001, "alice")))
        assert_no_errors(errs, data)
        assert not s.edits(), f"{data} showed a privileged menu to a member"
    s.reset()
    errs = run(feed(reward_bot, make_callback("menu:owner", OWNER_ID, "owner")))
    assert_no_errors(errs, "owner menu for the owner")
    assert s.edits(), "the owner could not open their own panel"
    print("OK privileged menus are role-gated, not just the panels behind them")


def test_admin_dashboard_owner_only_and_back_works():
    session = MockSession()
    admin_bot.bot.session = session
    errs = run(feed(admin_bot, make_message("/start", uid=1001, username="alice")))
    assert_no_errors(errs, "admin /start as a normal user")
    assert "owner's admin panel" in session.texts()
    session.reset()
    errs = run(feed(admin_bot, make_message("/start", uid=OWNER_ID, username="owner")))
    assert_no_errors(errs, "admin /start as owner")
    assert "ADMIN DASHBOARD" in session.texts()
    # every dashboard panel, including the Back button that used to be shadowed
    for data in ("dash:cap", "dash:rev:reward", "dash:rev:partnership",
                 "dash:contracts", "dash:admins", "dash:back"):
        session.reset()
        errs = run(feed(admin_bot, make_callback(data, OWNER_ID, "owner")))
        assert_no_errors(errs, f"admin panel {data}")
        assert session.edits(), f"{data} rendered nothing"
    print("OK admin dashboard: owner-only, every panel + Back renders")


def test_admin_invite_button_is_owner_only():
    session = MockSession()
    admin_bot.bot.session = session
    errs = run(feed(admin_bot, make_callback("inv:both", 1001, "alice")))
    assert_no_errors(errs, "non-owner invite in admin bot")
    assert not session.edits()
    errs = run(feed(admin_bot, make_callback("inv:both", OWNER_ID, "owner")))
    assert_no_errors(errs, "owner invite in admin bot")
    assert "invite code" in session.texts().lower()
    print("OK admin-bot invite codes are owner-only")


ALL_TESTS = [
    test_all_handlers_are_coroutines,
    test_expected_callbacks_are_registered,
    test_menus_build_without_positional_args,
    test_start_registers_a_channel,
    test_menu_and_cap_screens_render,
    test_full_submission_funnel_distributes_and_charges,
    test_two_members_do_not_share_funnel_state,
    test_forward_without_terms_is_refused,
    test_captioned_scam_post_is_still_gated,
    test_borderline_post_goes_to_human_review,
    test_chain_agree_credits_the_sharer_not_the_sender,
    test_report_is_filed_pending_for_a_human,
    test_owner_bypasses_credits_caps_and_funnel,
    test_non_owner_cannot_route_to_all,
    test_admin_invite_codes_are_owner_only,
    test_report_review_is_human_and_reversible,
    test_owner_panels_all_render,
    test_panels_are_closed_to_regular_users,
    test_partnership_start_and_contract,
    test_partnership_report_is_actually_filed,
    test_partnership_offer_respects_receive_types_and_cap,
    test_owner_menu_is_not_reachable_by_a_member,
    test_admin_dashboard_owner_only_and_back_works,
    test_admin_invite_button_is_owner_only,
]


if __name__ == "__main__":
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
    print(f"\nALL BOT WIRING TESTS PASSED ({total})")
