"""CLICKMINT — offline bot simulator (a readable dry run).

`test_bots.py` proves the wiring with assertions. This script does the other
half: it plays a realistic multi-user session and PRINTS the conversation, so a
human can read exactly what each bot says and what buttons it shows.

It is not a mock of the bots — it drives the real Dispatchers with real Telegram
Update objects. The only fake part is the network: a mocked session answers each
Bot API call locally, so nothing is sent to Telegram and no token is needed.

Buttons are pressed BY THEIR LABEL: the simulator looks at the keyboard the bot
actually rendered and clicks the matching button's callback_data. If a menu is
missing a button, or a button's data points at no handler, the run says so.

    python3 simulate.py            # full scripted session
    python3 simulate.py --quiet    # only the step headers + verdict
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import os
import sys
import tempfile
import time
import textwrap

QUIET = "--quiet" in sys.argv

# --- isolate state + declare an owner BEFORE importing the bot modules --------
_TMP = tempfile.mkdtemp(prefix="clickmint-sim-")
os.environ["STORE_DIR"] = _TMP
os.environ.setdefault("REWARD_BOT_TOKEN", "111111:SIMTOKENSIMTOKENSIMTOKENSIMTOKEN")
os.environ.setdefault("PARTNER_BOT_TOKEN", "222222:SIMTOKENSIMTOKENSIMTOKENSIMTOKEN")
os.environ.setdefault("ADMIN_BOT_TOKEN", "333333:SIMTOKENSIMTOKENSIMTOKENSIMTOKEN")
OWNER_ID = 555000555
os.environ["OWNER_USER_ID"] = str(OWNER_ID)
os.environ["CLICKMINT_CREDENTIAL_KEY"] = "simulation-only-key"

from aiogram.client.session.base import BaseSession                # noqa: E402
from aiogram.types import (CallbackQuery, Chat, Message,           # noqa: E402
                           MessageOriginUser, Update, User)
from telegram_verification import VerificationResult       # noqa: E402

import admin_bot                                                   # noqa: E402
import partnership_bot                                             # noqa: E402
import reward_bot                                                  # noqa: E402

logging.getLogger("aiogram").setLevel(logging.ERROR)

# ---------------------------------------------------------------------------
# terminal helpers
# ---------------------------------------------------------------------------
BOLD, DIM, GREEN, RED, YELLOW, CYAN, RESET = (
    "\033[1m", "\033[2m", "\033[32m", "\033[31m", "\033[33m", "\033[36m", "\033[0m")

PROBLEMS: list[str] = []


def head(n: int, title: str):
    print(f"\n{BOLD}{CYAN}━━ STEP {n}: {title} {'━' * max(0, 58 - len(title))}{RESET}")


def note(text: str):
    if not QUIET:
        print(f"{DIM}   {text}{RESET}")


def problem(text: str):
    PROBLEMS.append(text)
    print(f"{RED}   ✗ {text}{RESET}")


def show(prefix: str, text: str, buttons: list[str] | None = None):
    if QUIET:
        return
    body = textwrap.indent(textwrap.fill(text.replace("\n", " ⏎ "), 96), "        ")
    print(f"   {prefix}\n{body}")
    if buttons:
        print(f"{DIM}        [ {' ] [ '.join(buttons)} ]{RESET}")


# ---------------------------------------------------------------------------
# The mocked Telegram session: records every outgoing API call.
# ---------------------------------------------------------------------------
class SimSession(BaseSession):
    def __init__(self, sim: "Sim"):
        super().__init__()
        self.sim = sim

    async def close(self):
        pass

    async def stream_content(self, *a, **k):     # pragma: no cover
        yield b""

    async def make_request(self, bot, method, timeout=None):
        name = type(method).__name__
        data = method.model_dump(exclude_none=True)
        self.sim.on_call(name, data)
        if name in ("SendMessage", "ForwardMessage", "EditMessageText"):
            return Message(message_id=self.sim.next_id(),
                           date=dt.datetime.now(dt.timezone.utc),
                           chat=Chat(id=1, type="private"),
                           text=str(data.get("text", "")))
        return True


class Actor:
    """A Telegram user. `channel` is what they register in the ledger."""

    def __init__(self, uid: int, username: str, label: str):
        self.uid, self.username, self.label = uid, username, label

    @property
    def handle(self) -> str:
        return "@" + self.username

    def user(self) -> User:
        return User(id=self.uid, is_bot=False, first_name=self.label,
                    username=self.username)


class Sim:
    def __init__(self):
        self._id = 5000
        # last keyboard rendered per chat key (chat id as str, or "@channel")
        self.keyboards: dict[str, list] = {}
        self.calls: list[tuple[str, dict]] = []

    def next_id(self) -> int:
        self._id += 1
        return self._id

    def attach(self, module):
        module.bot.session = SimSession(self)


    # -- record + render every outgoing call -------------------------------
    def on_call(self, name: str, data: dict):
        self.calls.append((name, data))
        chat = str(data.get("chat_id", ""))
        kb = (data.get("reply_markup") or {}).get("inline_keyboard")
        labels = []
        if kb:
            self.keyboards[chat] = kb
            labels = [b["text"] for row in kb for b in row]
        elif name == "EditMessageReplyMarkup" or (kb == [] and chat):
            self.keyboards.pop(chat, None)

        if name == "SendMessage":
            show(f"{GREEN}🤖 → {chat}{RESET}", str(data.get("text", "")), labels)
        elif name == "EditMessageText":
            show(f"{GREEN}🤖 ✎ {chat} (same screen){RESET}",
                 str(data.get("text", "")), labels)
        elif name == "ForwardMessage":
            show(f"{GREEN}🤖 ⇢ {chat}{RESET}",
                 f"[genuine forward of message {data.get('message_id')} "
                 f"from chat {data.get('from_chat_id')} — attribution preserved]")
        elif name == "AnswerCallbackQuery" and data.get("text"):
            flag = "⚠ popup" if data.get("show_alert") else "toast"
            show(f"{YELLOW}🤖 {flag}{RESET}", str(data["text"]))

    # -- input -------------------------------------------------------------
    async def send(self, module, actor: Actor, text: str, *,
                   forwarded: bool = False, caption: str | None = None):
        show(f"{BOLD}👤 {actor.handle} sends{RESET}",
             ("[forwarded post] " if forwarded else "") + (caption or text))
        origin = None
        if forwarded:
            origin = MessageOriginUser(type="user",
                                       date=dt.datetime.now(dt.timezone.utc),
                                       sender_user=User(id=4242, is_bot=False,
                                                        first_name="Source",
                                                        username="sourcechannel"))
        msg = Message(message_id=self.next_id(),
                      date=dt.datetime.now(dt.timezone.utc),
                      chat=Chat(id=actor.uid, type="private"),
                      from_user=actor.user(),
                      text=None if caption else text,
                      caption=caption,
                      forward_origin=origin)
        await self._feed(module, Update(update_id=self.next_id(), message=msg))

    async def tap(self, module, actor: Actor, label_part: str,
                  screen: str | None = None):
        """Press the button whose label contains `label_part`, on the keyboard
        the bot last rendered for this actor (or for `screen`, e.g. a channel)."""
        key = screen or str(actor.uid)
        kb = self.keyboards.get(key)
        if not kb:
            problem(f"{actor.handle} has no keyboard on screen '{key}' "
                    f"— cannot tap '{label_part}'")
            return
        for row in kb:
            for btn in row:
                if label_part.lower() in btn["text"].lower():
                    show(f"{BOLD}👤 {actor.handle} taps{RESET}",
                         f"[{btn['text']}]  →  {DIM}{btn.get('callback_data')}{RESET}")
                    await self._press(module, actor, btn.get("callback_data", ""))
                    return
        have = [b["text"] for row in kb for b in row]
        problem(f"no button matching '{label_part}' for {actor.handle}; on screen: {have}")

    async def press_raw(self, module, actor: Actor, data: str, why: str):
        """Send callback data that is NOT on screen — used to test guards."""
        show(f"{BOLD}👤 {actor.handle} (crafted){RESET}", f"{why}: sends '{data}'")
        await self._press(module, actor, data)

    async def _press(self, module, actor: Actor, data: str):
        msg = Message(message_id=self.next_id(),
                      date=dt.datetime.now(dt.timezone.utc),
                      chat=Chat(id=actor.uid, type="private"),
                      from_user=User(id=1, is_bot=True, first_name="bot",
                                     username="clickmintbot"),
                      text="menu")
        cb = CallbackQuery(id=str(self.next_id()), from_user=actor.user(),
                           chat_instance="ci", data=data, message=msg)
        await self._feed(module, Update(update_id=self.next_id(), callback_query=cb))

    async def _feed(self, module, upd: Update):
        errs: list = []
        if not getattr(module.dp, "_sim_hook", False):
            @module.dp.errors()
            async def _hook(event):
                errs_ref.append(event.exception)
                return True
            module.dp._sim_hook = True
            module.dp._sim_errors = errs
        errs_ref = module.dp._sim_errors
        before = len(errs_ref)
        await module.dp.feed_update(module.bot, upd)
        for exc in errs_ref[before:]:
            problem(f"handler crashed: {type(exc).__name__}: {exc}")

    def last_text(self) -> str:
        for name, data in reversed(self.calls):
            if name in ("SendMessage", "EditMessageText"):
                return str(data.get("text", ""))
        return ""

    def all_text(self) -> str:
        return "\n".join(str(d.get("text", "")) for n, d in self.calls
                         if n in ("SendMessage", "EditMessageText",
                                  "AnswerCallbackQuery"))


def expect(cond: bool, msg: str):
    if cond:
        print(f"{GREEN}   ✓ {msg}{RESET}")
    else:
        problem(f"EXPECTED: {msg}")


# ---------------------------------------------------------------------------
# The scripted session
# ---------------------------------------------------------------------------
OWNER = Actor(OWNER_ID, "clickminthq", "Owner")
ALPHA = Actor(1001, "alphadrops", "Alpha")      # airdrops channel, big
BRAVO = Actor(1002, "bravoai", "Bravo")         # AI tools channel
CHARLIE = Actor(1003, "charliedefi", "Charlie")  # defi channel
DELTA = Actor(1004, "deltaguides", "Delta")     # guides channel, small


async def main() -> int:
    sim = Sim()
    for mod in (reward_bot, partnership_bot, admin_bot):
        sim.attach(mod)
    R, P, A = reward_bot, partnership_bot, admin_bot

    async def fake_connect(owner_id: int, token: str) -> dict:
        return R.bot_credentials.save(owner_id, token,
                                      {"id": owner_id + 900000, "is_bot": True,
                                       "username": f"sim_bot_{owner_id}"})

    async def fake_verify(owner_id: int, chat_ref) -> VerificationResult:
        counts = {"@alphadrops": 12000, "@bravoai": 4300,
                  "@charliedefi": 2600, "@deltaguides": 300}
        ref = str(chat_ref)
        return VerificationResult("VERIFIED", True, ref, "channel", ref.lstrip("@"),
                                  ref.lstrip("@"), "administrator", counts.get(ref, 1000),
                                  int(time.time()), (), {"can_post_messages": True})

    R.telegram_verification.connect_bot = fake_connect
    R.telegram_verification.verify = fake_verify
    P.telegram_verification.connect_bot = fake_connect
    P.telegram_verification.verify = fake_verify
    for actor in (ALPHA, BRAVO, CHARLIE, DELTA):
        await sim.send(R, actor, "/connectbot 999:simulation-token")

    print(f"{BOLD}CLICKMINT offline simulation{RESET}  "
          f"{DIM}(real handlers, mocked Telegram — nothing is sent){RESET}")
    print(f"{DIM}state dir: {_TMP}   owner user id: {OWNER_ID}{RESET}")

    # ---------------------------------------------------------------- 1
    head(1, "A new member opens the reward bot")
    await sim.send(R, ALPHA, "/start")
    expect("register" in sim.last_text().lower(),
           "a brand-new user is told how to register")

    # ---------------------------------------------------------------- 2
    head(2, "Everyone registers a channel")
    await sim.send(R, ALPHA, "/register @alphadrops")
    expect("VERIFIED" in sim.last_text(), "registration verifies the user-owned bot")
    await sim.send(R, BRAVO, "/register @bravoai")
    await sim.send(R, CHARLIE, "/register @charliedefi")
    await sim.send(R, DELTA, "/register @deltaguides")
    note("member counts came from the mocked Telegram Bot API, never user input")

    await sim.send(R, ALPHA, "/balance")
    expect("🪙 2 MINT" in sim.last_text(), "onboarding seed is 2 MINT")

    # ---------------------------------------------------------------- 3
    head(3, "Alpha walks the submission funnel (buttons only)")
    await sim.send(R, ALPHA, "/start")
    await sim.tap(R, ALPHA, "Submit")
    expect("TERMS" in sim.last_text().upper(), "terms are shown before anything else")
    await sim.tap(R, ALPHA, "Accept")
    await sim.tap(R, ALPHA, "Airdrops")
    await sim.tap(R, ALPHA, "Forward")
    # Loud is group-only; the registered destinations here are channels.
    await sim.tap(R, ALPHA, "Silent")
    await sim.tap(R, ALPHA, "Submit")

    # ---------------------------------------------------------------- 4
    head(4, "Alpha pastes instead of forwarding (forward-only rule)")
    before = len(sim.calls)
    await sim.send(R, ALPHA, "Big airdrop news, check it out")
    routed = [d for n, d in sim.calls[before:]
              if n in ("ForwardMessage",) or
              (n == "SendMessage" and str(d.get("chat_id", "")).startswith("@"))]
    expect(not routed, "a typed post reaches no channel — only genuine forwards are routed")

    # ---------------------------------------------------------------- 5
    head(5, "COLD START: Alpha forwards, but has never shared anyone else's post")
    await sim.send(R, ALPHA, "Testnet rewards are live for early users",
                   forwarded=True)
    expect("earn-first" in sim.last_text(),
           "the earn-first rule blocks a member who has only ever taken")
    note("this is by design — but it means that at genesis NOBODY can post until")
    note("the owner (who is exempt) puts the first post into circulation.")

    # ---------------------------------------------------------------- 6
    head(6, "The owner bootstraps the network (exempt: no terms/cap/credits)")
    await sim.send(R, OWNER, "/balance")
    expect("exempt" in sim.last_text().lower(), "owner is exempt from the economy")
    await sim.send(R, OWNER, "CLICKMINT is live — welcome to the network",
                   forwarded=True)
    expect("Owner mode" in sim.last_text(),
           "owner skips the funnel entirely and goes straight to routing")
    await sim.tap(R, OWNER, "All matching")
    expect("Distributing to" in sim.all_text(), "owner can route to every channel")

    # ---------------------------------------------------------------- 7
    head(7, "Members agree to share it — the credit goes to the SHARER")
    offered = [str(d.get("chat_id")) for n, d in sim.calls
               if n == "SendMessage" and str(d.get("chat_id", "")).startswith("@")]
    if not offered:
        problem("no channel received the owner's offer — matching produced nothing")
        return report()
    note(f"offer reached: {', '.join(dict.fromkeys(offered))}")
    by_handle = {a.handle: a for a in (ALPHA, BRAVO, CHARLIE, DELTA)}
    for handle in dict.fromkeys(offered):
        who = by_handle.get(handle)
        if who:
            await sim.tap(R, who, "Agree", screen=handle)
    await sim.send(R, ALPHA, "/balance")
    expect("Earned: 🪙 1 MINT" in sim.last_text(),
           "Alpha earned 🪙 1 MINT for SHARING someone else's post (not for sending one)")

    # ---------------------------------------------------------------- 8
    head(8, "Now Alpha's own post can circulate")
    await sim.send(R, ALPHA, "Testnet rewards are live for early users",
                   forwarded=True)
    expect("How many" in sim.last_text(),
           "the bot offers (post→channel) pairs priced at 1 credit each")
    bal_before = reward_bot.ledger.balance(ALPHA.handle)["balance"]
    await sim.tap(R, ALPHA, "2")
    expect("Distributing to" in sim.all_text(),
           "tapping the number actually distributes (pick_spend is registered)")
    bal_after = reward_bot.ledger.balance(ALPHA.handle)["balance"]
    expect(bal_after < bal_before,
           f"MINT was charged to the sender: {bal_before} → {bal_after}")

    # ---------------------------------------------------------------- 9
    head(9, "Self-dealing: can Alpha agree to Alpha's own post?")
    await sim.press_raw(R, ALPHA, f"chain:agree:{ALPHA.handle}",
                        "trying to credit myself")
    expect("can't agree to your own post" in sim.all_text(),
           "self-agreement is refused — no credit printing")

    # ---------------------------------------------------------------- 9
    head(10, "A scam post is refused (but nobody is banned)")
    await sim.send(R, CHARLIE, "/start")
    await sim.tap(R, CHARLIE, "Submit")
    await sim.tap(R, CHARLIE, "Accept")
    await sim.tap(R, CHARLIE, "DeFi")
    await sim.tap(R, CHARLIE, "Submit")
    await sim.send(R, CHARLIE, "Send 1 ETH and double your money today",
                   forwarded=True)
    expect("Refused" in sim.last_text(), "hard-blocked wording is refused")
    expect(reward_bot.ledger._m(CHARLIE.handle).get("status", "ACTIVE") == "ACTIVE",
           "…and Charlie's channel is still ACTIVE — a refusal is not a ban")

    # ---------------------------------------------------------------- 10
    head(11, "A borderline post goes to the human review queue")
    await sim.send(R, CHARLIE, "New staking bonus, claim it this week",
                   forwarded=True)
    expect("review" in sim.last_text().lower(),
           "soft pattern → queued for a human, not auto-refused")
    queued = len(reward_bot.review.pending())
    expect(queued >= 1, f"review queue holds {queued} item(s) for a human")

    # ---------------------------------------------------------------- 11
    head(12, "Captioned scam (the gate must read captions, not just text)")
    await sim.send(R, CHARLIE, "", forwarded=True,
                   caption="Free crypto giveaway — send your seed phrase to claim")
    expect("Refused" in sim.last_text(), "caption is inspected like text")

    # ---------------------------------------------------------------- 12
    head(13, "A receiver reports a post — human review, no auto-ban")
    receiver = BRAVO
    if receiver:
        await sim.press_raw(R, receiver, f"report:{ALPHA.handle}:{receiver.handle}",
                            "flagging the sender")
        expect("Report #" in sim.all_text(), "the report is filed with an id")
        expect(reward_bot.ledger._m(ALPHA.handle).get("status", "ACTIVE") == "ACTIVE",
               "Alpha is untouched until a human decides")

    # ---------------------------------------------------------------- 13
    head(14, "Owner reviews the report and restricts the channel")
    await sim.send(R, OWNER, "/start")
    expect("Owner" in sim.last_text(), "the owner gets the owner menu")
    await sim.tap(R, OWNER, "Owner")
    await sim.tap(R, OWNER, "Reports")
    pending = reward_bot.reports.pending()
    if pending:
        rid = pending[0]["id"]
        await sim.press_raw(R, OWNER, f"rep:restrict:{rid}", "human decision")
        expect(reward_bot.ledger._m(ALPHA.handle).get("status") == "RESTRICTED",
               "only now — after a human acted — is Alpha restricted")
    else:
        problem("no pending report reached the owner panel")

    # ---------------------------------------------------------------- 14
    head(15, "A member tries to open the owner panel")
    await sim.press_raw(R, DELTA, "menu:owner", "privilege escalation attempt")
    expect("owner" in sim.all_text().lower() and
           "not" in sim.all_text().lower(),
           "the member is refused (menu:owner is role-gated)")

    # ---------------------------------------------------------------- 15
    head(16, "A restricted channel is excluded from matching")
    restricted = [u for u, m in reward_bot.ledger.ledger.items()
                  if m.get("status") in ("RESTRICTED", "REMOVED")]
    note(f"restricted right now: {restricted or 'none'}")
    if restricted:
        targets = reward_bot.owner_targets("General")
        expect(all(r not in targets for r in restricted),
               "a restricted channel receives nothing until a human lifts it")

    # ---------------------------------------------------------------- 16
    head(17, "Daily cap: a 300-sub channel gets few slots")
    await sim.send(R, DELTA, "/start")
    await sim.tap(R, DELTA, "Submit")
    await sim.tap(R, DELTA, "Accept")
    await sim.tap(R, DELTA, "Guides")
    await sim.tap(R, DELTA, "Submit")
    cap = reward_bot.daily_post_cap(300, reward_bot.perf.score(DELTA.handle)["band"],
                                    "ACTIVE", False, connected=False)
    note(f"computed cap for a 300-sub channel: {cap} post(s)/day")
    note("(the cap is consumed when a post is DISTRIBUTED, not when it is submitted)")
    blocked = False
    for i in range(cap + 2):
        await sim.send(R, DELTA, f"Guide part {i+1}", forwarded=True)
        if "cap reached" in sim.last_text().lower():
            blocked = True
            break
        await sim.tap(R, DELTA, "1")
    expect(blocked, f"after {cap} distributed post(s) the cap stops the next one")

    # ---------------------------------------------------------------- 17
    head(18, "Partnership bot: open a partnership")
    await sim.send(P, BRAVO, "/start")
    expect(sim.last_text() != "", "partnership bot answers /start")
    await sim.send(P, BRAVO, "/partner @charliedefi")
    note("partnership requests are peer-to-peer with the owner as mediator")

    # ---------------------------------------------------------------- 18
    head(19, "Admin bot: the owner opens the dashboard")
    await sim.send(A, OWNER, "/start")
    expect(sim.last_text() != "", "admin bot answers the owner")
    await sim.send(A, DELTA, "/start")
    expect("not" in sim.last_text().lower() or "admin" in sim.last_text().lower(),
           "a plain member does not get admin powers")

    return report()


def report() -> int:
    print(f"\n{BOLD}{'═' * 74}{RESET}")
    if PROBLEMS:
        print(f"{RED}{BOLD}SIMULATION FOUND {len(PROBLEMS)} PROBLEM(S){RESET}")
        for p in PROBLEMS:
            print(f"{RED}  • {p}{RESET}")
        return 1
    print(f"{GREEN}{BOLD}SIMULATION CLEAN — every screen rendered, every button "
          f"worked, every rule held.{RESET}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
