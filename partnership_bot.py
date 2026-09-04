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
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from core import (Contract, CreditLedger, DeliveryLog, POST_TYPES,
                  OWNER_USERNAME, is_forward, forward_source)
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


@dp.message(Command("start"))
async def start(msg: types.Message):
    parts = msg.text.split()
    if len(parts) >= 3:
        username, size = parts[1], int(parts[2])
        m = ledger.register(username, size, is_partner=True)
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
        [InlineKeyboardButton(t, callback_data=f"ctype:{t}")] for t in POST_TYPES
    ])
    await msg.answer("Choose the post types you'll ACCEPT & PUBLISH:", reply_markup=kb)
    store["contract_phase"] = "accept"
    store["contract_user"] = msg.from_user.username
    store.sync()


@dp.callback_query(lambda c: c.data and c.data.startswith("ctype:"))
async def contract_pick(cb: types.CallbackQuery):
    t = cb.data.split(":")[1]
    phase = store.get("contract_phase", "accept")
    username = "@" + store.get("contract_user", "")
    m = ledger._m(username)
    key = "accept_types" if phase == "accept" else "receive_types"
    current = list(m.get(key, []))
    if t not in current:
        current.append(t)
    m[key] = sorted(set(current))
    store.sync()
    if phase == "accept":
        store["contract_phase"] = "receive"
        store.sync()
        await cb.answer(f"Accepting: {current}")
        await cb.message.edit_text("Now choose the post types you want to RECEIVE:",
                                   reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                                       [InlineKeyboardButton(t, callback_data=f"ctype:{t}")] for t in POST_TYPES]))
    else:
        store["contract_phase"] = "done"
        store.sync()
        contract.set_contract(m, m.get("accept_types", []), current)
        await cb.answer(f"Contract saved.")
        await cb.message.edit_text(f"Contract saved for {username}.\nAccept: {m['accept_types']}\nReceive: {m['receive_types']}")


# --- SUBMISSION GATE + category picker + style ---
@dp.callback_query(lambda c: c.data and c.data.startswith("menu:"))
async def menu_nav(cb: types.CallbackQuery):
    which = cb.data.split(":", 1)[1]
    if which == "submit":
        await cb.message.edit_text(
            TERMS_TEXT,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton("✅ Accept & continue", callback_data="terms:accept")],
                [InlineKeyboardButton("❌ Not now", callback_data="menu:hub")]]))
    else:
        await cb.message.edit_text("Choose an option:", reply_markup=ui.main_menu(roles.role(cb.from_user.id)))
    await cb.answer()


@dp.callback_query(lambda c: c.data and c.data.startswith("terms:"))
async def accept_terms(cb: types.CallbackQuery):
    store["accepted_terms"] = _uname(cb)
    store.sync()
    await cb.message.edit_text("Select your post's NICHE category:",
                               reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                                   [InlineKeyboardButton(t, callback_data=f"cat:{t}")] for t in POST_CATEGORIES]))
    await cb.answer()


@dp.callback_query(lambda c: c.data and c.data.startswith("cat:"))
async def pick_category(cb: types.CallbackQuery):
    cat = cb.data.split(":", 1)[1]
    store["pending_cat"] = cat
    store["pending_user"] = _uname(cb)
    store.sync()
    await cb.message.edit_text(
        f"Category: **{cat}**. Now **forward** the post to distribute it to partner "
        "channels that receive this category.\n\n" + gate.attribution_tip(),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton("⬅️ Back", callback_data="menu:hub")]]))
    await cb.answer()


# --- forward handler: run the gate ---
@dp.message(lambda m: is_forward(m))
async def on_partner_forward(msg: types.Message):
    username = _uname(msg)
    ledger.set_user_id(username, msg.from_user.id)   # remember id to DM later
    if store.get("accepted_terms") != username:
        await msg.answer("Accept the posting terms first (/start → Submit a post).")
        return
    cat = store.get("pending_cat")
    if not cat:
        await msg.answer("Pick your post's niche category first.")
        return
    # gate (targets' receive types gathered from ledger partners below).
    ok, verdict, why = gate.gate(cat, msg.text or "", [])
    if not ok:
        await msg.answer(f"⛔ Refused: {why}\n\n{gate.attribution_tip()}")
        return
    if verdict == "review":
        item = review.submit(cat, username, msg.text or "", forward_source(msg), reason=why)
        await msg.answer(f"⚠ Queued for human review (#{item['id']}). You'll be told when approved.")
        return
    await msg.answer(gate.attribution_tip())
    offered = []
    for other, om in ledger.ledger.items():
        if other == username or not om.get("is_partner"):
            continue
        if contract.allows_receive(om, cat):
            offered.append(other)
    await msg.answer(f"Offering your '{cat}' post to {len(offered)} partners: {offered}.")
    for target in offered:
        audit.record(bot="partnership", sender=username, source=forward_source(msg),
                     post_type=cat, target_channel=target, mode="chain",
                     status="offered", forward_valid=True)
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton("✅ Agree (post it)", callback_data=f"chk:agree:{username}"),
             InlineKeyboardButton("❌ Disagree (skip)", callback_data=f"chk:dis:{username}")],
            [InlineKeyboardButton("🚩 Report", callback_data=f"chk:report:{username}")],
        ])
        await bot.send_message(target, f"{username} shared a '{cat}' post. Post it?", reply_markup=kb)


@dp.callback_query(lambda c: c.data and c.data.startswith("chk:"))
async def chain_choice(cb: types.CallbackQuery):
    _, decision, sender = cb.data.split(":")
    target = _uname(cb)
    if decision == "agree":
        audit.record(bot="partnership", sender=sender, target_channel=target,
                     mode="chain", status="agreed", forward_valid=True)
        await cb.message.answer("Thanks! Post it to complete.")
    elif decision == "report":
        audit.record(bot="partnership", sender=sender, target_channel=target,
                     mode="chain", status="skipped", forward_valid=True)
        await cb.message.answer("🚩 Reported. Admins review.")
    else:
        audit.record(bot="partnership", sender=sender, target_channel=target,
                     mode="chain", status="skipped", forward_valid=True)
        await cb.message.answer("Skipped. Next partner.")
    await cb.message.edit_reply_markup(None)
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
        kb.append([InlineKeyboardButton(f"✅ #{it['id']}", callback_data=f"review:approve:{it['id']}"),
                   InlineKeyboardButton(f"❌ #{it['id']}", callback_data=f"review:reject:{it['id']}")])
    await cb.message.edit_text("\n".join(lines[:40]), reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    await cb.answer()


@dp.callback_query(lambda c: c.data and c.data.startswith("review:"))
async def decide_review(cb: types.CallbackQuery):
    if not roles.has_access(cb.from_user.id, "partnership"):
        await cb.answer("Owner/admin only.", show_alert=True)
        return
    _, action, rid = cb.data.split(":")
    review.decide(int(rid), approve=(action == "approve"), note=f"{action} by {_uname(cb)}")
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
