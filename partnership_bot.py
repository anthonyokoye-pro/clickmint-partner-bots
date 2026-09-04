"""
PARTNERSHIP BOT  (aiogram)  — curated MAIN partners, with a terms contract.

SEPARATE from the reward bot. For the quality partners you chose and agreed terms with.
Shares core.py + governance.py so the rules/roles/limits are consistent.

NEW: button menus, role-gated (owner/admin/user via invite code), a submission
gate (niche category + no spam/ads + human review), and a post-style selector
(forward / direct / pin + loud / silent).
"""
import asyncio
import logging
import time
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from core import (Contract, CreditLedger, DeliveryLog, ReportRegistry,
                  PerformanceEngine, POST_TYPES, is_forward, forward_source)
from store import JsonStore
from governance import (SubmissionGate, ReviewQueue, RoleRegistry, POST_CATEGORIES,
                        TERMS_TEXT, daily_post_cap)
import config
import ui

logging.basicConfig(level=logging.INFO)
BOT_TOKEN = config.PARTNER_BOT_TOKEN
OWNER_USER_ID = config.OWNER_USER_ID

store = JsonStore(config.PARTNER_STORE_PATH)
ledger = CreditLedger(store)
ledger.owner_user_id = config.OWNER_USER_ID      # owner recognised by numeric id too
contract = Contract()
audit = DeliveryLog(store)
reports = ReportRegistry(store)
perf = PerformanceEngine(ledger, store, views_provider=None)
gate = SubmissionGate(store)
review = ReviewQueue(store)
roles = RoleRegistry(store, owner_user_id=OWNER_USER_ID)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


def _uid(msg) -> int:
    return msg.from_user.id


def _uname(msg) -> str:
    u = getattr(msg, "from_user", None)
    return "@" + (getattr(u, "username", None) or str(getattr(u, "id", "?")))


# --- per-user session state (never a single global "who is submitting" key) ---
SESSION_TTL = 24 * 3600


def _sessions() -> dict:
    s = store.get("sessions")
    return s if isinstance(s, dict) else {}


def _session(uid) -> dict:
    return _sessions().get(str(uid), {})


def _session_set(uid, **kw) -> dict:
    sessions = _sessions()
    s = dict(sessions.get(str(uid), {}))
    s.update(kw)
    s["ts"] = time.time()
    sessions[str(uid)] = s
    now = time.time()
    for k in [k for k, v in sessions.items()
              if now - float(v.get("ts", now)) > SESSION_TTL]:
        sessions.pop(k, None)
    store["sessions"] = sessions
    store.sync()
    return s


def _submitted_text(msg) -> str:
    """Captions count: a forwarded image post keeps its words in `caption`."""
    return (getattr(msg, "text", None) or getattr(msg, "caption", None) or "")


@dp.message(Command("start"))
async def start(msg: types.Message):
    parts = (msg.text or "").split()
    if len(parts) >= 3:
        username = parts[1] if parts[1].startswith("@") else "@" + parts[1]
        try:
            size = int(parts[2].replace(",", "").replace("_", ""))
        except ValueError:
            # `int()` on a typo used to raise straight out of the handler.
            await msg.answer("Usage: /start @yourchannel <subscriber_count>\n"
                             "e.g.  /start @MyChan 1200")
            return
        if size < 0 or size > 100_000_000:
            await msg.answer("That subscriber count isn't plausible.")
            return
        m = ledger.register(username, size, is_partner=True)
        ledger.set_user_id(username, _uid(msg))
        await msg.answer(f"Partner {username} registered ({m['tier']}, {size} subs).",
                         reply_markup=ui.main_menu(roles.role(_uid(msg))))
        return
    role = roles.role(_uid(msg))
    head = ("Partnership bot. Set your terms with /contract (what you ACCEPT & RECEIVE). "
            "Button menu below.")
    if role == "owner":
        head = "🛠 Partnership bot — Owner menu."
    elif role == "admin":
        head = "🛠 Partnership bot — Admin menu."
    await msg.answer(head, reply_markup=ui.main_menu(role))


@dp.message(Command("adminlogin"))
async def admin_login(msg: types.Message):
    parts = msg.text.split()
    if len(parts) < 2:
        await msg.answer("Usage: /adminlogin <CODE>")
        return
    ok, txt = roles.redeem_invite(_uid(msg), parts[1])
    await msg.answer(txt + (" Open your admin menu: /start" if ok else ""))


@dp.message(Command("contract"))
async def contract_greet(msg: types.Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t, callback_data=f"ctype:{t}")] for t in POST_TYPES
    ] + [[InlineKeyboardButton(text="✅ Done", callback_data="ctype:__done__")]])
    await msg.answer("Choose the post types you'll ACCEPT & PUBLISH "
                     "(tap several, then Done):", reply_markup=kb)
    # per user: the old build kept one global contract_phase/contract_user, so two
    # partners editing their terms at the same time wrote into each other's row —
    # and a user without a Telegram @username stored None, then crashed on "@"+None.
    _session_set(_uid(msg), contract_phase="accept")


@dp.callback_query(lambda c: c.data and c.data.startswith("ctype:"))
async def contract_pick(cb: types.CallbackQuery):
    t = cb.data.split(":", 1)[1]
    uid = cb.from_user.id
    sess = _session(uid)
    phase = sess.get("contract_phase", "accept")
    username = _uname(cb)
    m = ledger._m(username)
    key = "accept_types" if phase == "accept" else "receive_types"
    if t != "__done__":
        if t not in POST_TYPES:
            await cb.answer("Unknown post type.", show_alert=True)
            return
        current = list(m.get(key, []))
        if t not in current:
            current.append(t)
        m[key] = sorted(set(current))
        ledger.save()
        await cb.answer(f"{key.split('_')[0].title()}: {m[key]}")
        return
    if phase == "accept":
        _session_set(uid, contract_phase="receive")
        await cb.answer("Now pick what you want to RECEIVE.")
        await cb.message.edit_text(
            "Now choose the post types you want to RECEIVE (tap several, then Done):",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=pt, callback_data=f"ctype:{pt}")] for pt in POST_TYPES
            ] + [[InlineKeyboardButton(text="✅ Done", callback_data="ctype:__done__")]]))
        return
    _session_set(uid, contract_phase="done")
    contract.set_contract(m, m.get("accept_types", []), m.get("receive_types", []))
    ledger.save()
    await cb.answer("Contract saved.")
    await cb.message.edit_text(f"Contract saved for {username}.\n"
                               f"Accept: {m['accept_types'] or 'anything'}\n"
                               f"Receive: {m['receive_types'] or 'anything'}")


# --- SUBMISSION GATE + category picker + style ---
@dp.callback_query(lambda c: c.data and c.data.startswith("menu:"))
async def menu_nav(cb: types.CallbackQuery):
    which = cb.data.split(":", 1)[1]
    if which == "submit":
        await cb.message.edit_text(
            TERMS_TEXT,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✅ Accept & continue", callback_data="terms:accept")],
                [InlineKeyboardButton(text="❌ Not now", callback_data="menu:hub")]]))
    else:
        await cb.message.edit_text("Choose an option:", reply_markup=ui.main_menu(roles.role(cb.from_user.id)))
    await cb.answer()


@dp.callback_query(lambda c: c.data and c.data.startswith("terms:"))
async def accept_terms(cb: types.CallbackQuery):
    if cb.data != "terms:accept":
        await cb.message.edit_text("No problem.",
                                   reply_markup=ui.main_menu(roles.role(cb.from_user.id)))
        await cb.answer()
        return
    _session_set(cb.from_user.id, accepted_terms=True)
    await cb.message.edit_text("Select your post's NICHE category:",
                               reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                                   [InlineKeyboardButton(text=t, callback_data=f"cat:{t}")] for t in POST_CATEGORIES]))
    await cb.answer()


@dp.callback_query(lambda c: c.data and c.data.startswith("cat:"))
async def pick_category(cb: types.CallbackQuery):
    cat = cb.data.split(":", 1)[1]
    if cat not in POST_CATEGORIES:
        await cb.answer("Unknown category.", show_alert=True)
        return
    _session_set(cb.from_user.id, cat=cat)
    await cb.message.edit_text(
        f"Category: **{cat}**. Now **forward** the post to distribute it to partner "
        "channels that receive this category.\n\n" + gate.attribution_tip(),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Back", callback_data="menu:hub")]]))
    await cb.answer()


# --- forward handler: run the gate ---
@dp.message(lambda m: is_forward(m))
async def on_partner_forward(msg: types.Message):
    username = _uname(msg)
    uid = _uid(msg)
    ledger.set_user_id(username, uid)   # remember id to DM later
    sess = _session(uid)
    if not sess.get("accepted_terms"):
        await msg.answer("Accept the posting terms first (/start → Submit a post).")
        return
    cat = sess.get("cat")
    if not cat:
        await msg.answer("Pick your post's niche category first.")
        return
    # Daily post cap (size x performance) applies here too — it was imported but
    # never enforced, so the partnership bot had no limit at all.
    m = ledger._m(username)
    band = perf.score(username)["band"]
    cap = daily_post_cap(m.get("size", 0), band, m.get("status", "ACTIVE"),
                         m.get("is_owner", False))
    if cap == 0:
        await msg.answer("⛔ Your channel is not currently active in the network.")
        return
    left = ledger.cap_left(username, cap)
    if left == 0:
        await msg.answer(f"⛔ Daily post cap reached ({cap}). It resets at 00:00 UTC. "
                         "The cap is size × performance, not size alone.")
        return
    # gate (targets' receive types are checked per-partner below).
    ok, verdict, why = gate.gate(cat, _submitted_text(msg), [])
    if not ok:
        await msg.answer(f"⛔ Refused: {why}\n\n{gate.attribution_tip()}")
        return
    if verdict == "review":
        item = review.submit(cat, username, _submitted_text(msg), forward_source(msg),
                             reason=why)
        await msg.answer(f"⚠ Queued for human review (#{item['id']}). "
                         "A person decides — nothing is auto-banned.")
        return
    await msg.answer(gate.attribution_tip())
    offered = []
    for other, om in ledger.ledger.items():
        if other == username or not om.get("is_partner"):
            continue
        if om.get("status") in ("RESTRICTED", "REMOVED"):
            continue
        if contract.allows_receive(om, cat):
            offered.append(other)
    if not offered:
        await msg.answer(f"No partner currently receives '{cat}' posts. "
                         "Nothing was sent and no cap slot was used.")
        return
    ledger.consume_cap(username, cap, 1)
    await msg.answer(f"Offering your '{cat}' post to {len(offered)} partners: {offered}.")
    for target in offered:
        audit.record(bot="partnership", sender=username, source=forward_source(msg),
                     post_type=cat, target_channel=target, mode="chain",
                     status="offered", forward_valid=True)
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Agree (post it)", callback_data=f"chk:agree:{username}"),
             InlineKeyboardButton(text="❌ Disagree (skip)", callback_data=f"chk:dis:{username}")],
            [InlineKeyboardButton(text="🚩 Report", callback_data=f"chk:report:{username}")],
        ])
        try:
            await bot.send_message(target, f"{username} shared a '{cat}' post. Post it?",
                                   reply_markup=kb)
        except Exception as e:
            # one unreachable partner must not abort the rest of the offer
            logging.warning("offer to %s failed: %s", target, e)
            audit.record(bot="partnership", sender=username, post_type=cat,
                         target_channel=target, mode="chain", status="failed",
                         forward_valid=True, error=str(e)[:120])


@dp.callback_query(lambda c: c.data and c.data.startswith("chk:"))
async def chain_choice(cb: types.CallbackQuery):
    parts = cb.data.split(":", 2)
    if len(parts) < 3:
        await cb.answer("Malformed action.", show_alert=True)
        return
    _, decision, sender = parts
    target = _uname(cb)
    if target == sender:
        await cb.answer("You can't act on your own post.", show_alert=True)
        return
    if decision == "agree":
        audit.record(bot="partnership", sender=sender, target_channel=target,
                     mode="chain", status="agreed", forward_valid=True)
        await cb.message.answer("Thanks! Post it to complete.")
    elif decision == "report":
        # This used to only write an audit line and tell the reporter "Admins
        # review" — no report was ever filed, so nobody could review anything.
        item = reports.report(sender=sender, reporter=target,
                              reported_post={"target_channel": target},
                              reason="partner flagged the post")
        audit.record(bot="partnership", sender=sender, target_channel=target,
                     mode="chain", status="skipped", forward_valid=True)
        await cb.message.answer(f"🚩 Report #{item['id']} filed against {sender}. "
                                "A human reviews it — nothing is auto-banned.")
    else:
        audit.record(bot="partnership", sender=sender, target_channel=target,
                     mode="chain", status="skipped", forward_valid=True)
        await cb.message.answer("Skipped. Next partner.")
    try:
        await cb.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await cb.answer()


# --- owner/admin panel (review queue) ---
@dp.callback_query(lambda c: c.data and c.data.startswith("panel:review"))
async def show_review(cb: types.CallbackQuery):
    if not roles.has_access(cb.from_user.id, "partnership"):
        await cb.answer("Owner/admin only.", show_alert=True)
        return
    pending = review.pending()
    if not pending:
        await cb.message.edit_text("✅ No posts awaiting review.",
                                   reply_markup=ui.role_menu(roles.role(cb.from_user.id)))
        return
    lines = [f"{len(pending)} post(s) awaiting review:", ""]
    kb = []
    for it in pending[:8]:
        lines.append(f"#{it['id']} | {it['category']} | by {it['sender']}\n  {it['text'][:80]}")
        kb.append([InlineKeyboardButton(text=f"✅ #{it['id']}", callback_data=f"review:approve:{it['id']}"),
                   InlineKeyboardButton(text=f"❌ #{it['id']}", callback_data=f"review:reject:{it['id']}")])
    await cb.message.edit_text("\n".join(lines[:40]), reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    await cb.answer()


@dp.callback_query(lambda c: c.data and c.data.startswith("review:"))
async def decide_review(cb: types.CallbackQuery):
    if not roles.has_access(cb.from_user.id, "partnership"):
        await cb.answer("Owner/admin only.", show_alert=True)
        return
    _, action, rid = cb.data.split(":")
    if not review.decide(int(rid), approve=(action == "approve"),
                         note=f"{action} by {_uname(cb)}"):
        await cb.answer("That queue item no longer exists.", show_alert=True)
        return
    review.mark_notify(int(rid), via="partnership")
    await cb.answer(f"#{rid} {action}d.")
    await cb.message.edit_text(f"#{rid} {action}d. Sender will be notified.",
                               reply_markup=ui.role_menu(roles.role(cb.from_user.id)))


async def _notify_review_sender(item: dict):
    sender = item.get("sender")
    uid = ledger.user_id(sender) if sender else None
    if not uid:
        return False
    ok = item.get("status") == "approved"
    try:
        await bot.send_message(
            uid,
            (f"✅ Your post was **approved** (category {item.get('category')}). "
             f"Forward it again to offer it to partners.") if ok
            else "❌ Your submitted post was **not approved**. Please follow the terms "
                 "and resubmit.")
        return True
    except Exception as e:
        logging.warning("notify sender %s failed: %s", sender, e)
        return False


async def notify_loop():
    """Hand out partner-scope review outcomes to their senders."""
    while True:
        try:
            for item in review.pending_notify("partnership"):
                if await _notify_review_sender(item):
                    review.clear_notify(item["id"])
        except Exception as e:
            logging.warning("notify_loop error: %s", e)
        await asyncio.sleep(15)


# --- owner/admin audit ---
@dp.message(Command("audit"))
async def audit_view(msg: types.Message):
    if not roles.has_access(_uid(msg), "partnership"):
        await msg.answer("Owner/admin only.")
        return
    log = audit.last(15)
    lines = [audit.summary(), ""]
    for r in reversed(log):
        lines.append(f"[{r.get('ts')}] {r.get('sender')} -> {r.get('target_channel')} "
                     f"| {r.get('status')} | {r.get('post_type')} | fwd={'✅' if r.get('forward_valid', True) else '❌'}")
    await msg.answer("\n".join(lines[:55]))


async def main():
    asyncio.create_task(notify_loop())
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
