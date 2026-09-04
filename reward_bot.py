"""
REWARD / EXCHANGE BOT  (aiogram)  — tiered 1:1 credit network with terms + limits.

Business rules live in core.py and governance.py (both unit-tested). This module
only talks to Telegram. Fill in BOT_TOKEN (from @BotFather), OWNER_USER_ID (your
numeric Telegram id) so the owner/admin/user menus branch correctly.

NEW FEATURES (governance.py):
  • Post LIMIT scales with subscriber size + performance band (a small channel
    can't spam 10 posts/day). Owner unlimited; non-active = 0.
  • SUBMISSION GATE: sender must accept terms, declare a NICHE category, and the
    category must match what the target channel agreed to receive. No spam /
    money-asking / scam / fraud. No third-party ad-service posts. Borderline posts
    go to a human Review queue (never an instant auto-ban).
  • Attribution @username tip (advice only, never required).
  • Peer-to-peer PARTNER exchange with the owner as mediator / registrar (notified
    on open, renew, close; owner gives the final close).
  • Post STYLE: forward / direct / pin + loud / silent notifications.
  • Button menus everywhere (commands still work as a fallback).
  • ADMIN invite-code login: an admin must contact you first; you issue a one-time
    code; they redeem it to unlock a SCOPED admin menu (reward / partnership / both).
"""
import asyncio
import logging
import time
import datetime as dt
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import (InlineKeyboardMarkup, InlineKeyboardButton,
                           ReplyKeyboardMarkup, KeyboardButton)

from core import (CreditLedger, Distribution, DeliveryLog, ReportRegistry,
                  PerformanceEngine, OWNER_USERNAME, is_forward, forward_source)
from store import JsonStore
from scheduler import Scheduler
from governance import (SubmissionGate, ReviewQueue, PartnerContractRegistry,
                        RoleRegistry, POST_CATEGORIES, TERMS_TEXT,
                        daily_post_cap, SCOPES)
import config
import ui

logging.basicConfig(level=logging.INFO)
BOT_TOKEN = config.REWARD_BOT_TOKEN          # from @BotFather, via env var
OWNER_USER_ID = config.OWNER_USER_ID         # your numeric Telegram user id (owner)

store = JsonStore(config.REWARD_STORE_PATH)
ledger = CreditLedger(store)
ledger.owner_user_id = config.OWNER_USER_ID      # owner recognised by numeric id too
audit = DeliveryLog(store)
reports = ReportRegistry(store)
perf = PerformanceEngine(ledger, store, views_provider=None)
sched = Scheduler(store)

gate = SubmissionGate(store)
review = ReviewQueue(store)
contracts = PartnerContractRegistry(store)
roles = RoleRegistry(store, owner_user_id=OWNER_USER_ID)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


def _uid(msg) -> int:
    return msg.from_user.id


def _uname(msg_or_cb) -> str:
    u = getattr(msg_or_cb, "from_user", None) or getattr(msg_or_cb, "message", None)
    if u is None:
        return "@?"
    u = u.from_user if hasattr(u, "from_user") else u
    return "@" + (getattr(u, "username", None) or str(getattr(u, "id", "?")))


def _can_panel(uid, scope) -> bool:
    """owner always; admin only within their scope."""
    return roles.has_access(uid, scope)


# ---------------------------------------------------------------------------
# /start  -> branch by role: Owner / Admin / User, all via BUTTONS
# ---------------------------------------------------------------------------
@dp.message(Command("start"))
async def start(msg: types.Message):
    uid = _uid(msg)
    if uid <= 0:
        pass  # aiogram always gives a real user id
    role = roles.role(uid)
    if role == "owner":
        await msg.answer(
            "🛠 Welcome, Owner. Your menu controls everyone — users, admins, "
            "contracts, and the reward/partnership networks.",
            reply_markup=ui.main_menu("owner"))
    elif role == "admin":
        await msg.answer("🛠 Admin menu. Use it to moderate the network you're scoped to.",
                         reply_markup=ui.main_menu("admin"))
    else:
        head = ("Welcome to CLICKMINT.\n"
                "To use the network, register your channel first:\n"
                "Send: /start @yourchannel <subscriber_count>\n"
                "e.g.  /start @MyChan 1200")
        await msg.answer(head,
                         reply_markup=ui.main_menu("user"))


# admins log in via a one-time invite code (must have contacted the owner first)
@dp.message(Command("adminlogin"))
async def admin_login(msg: types.Message):
    parts = msg.text.split()
    if len(parts) < 2:
        await msg.answer("Enter the one-time admin code you received.\n"
                         "Usage: /adminlogin <CODE>")
        return
    ok, txt = roles.redeem_invite(_uid(msg), parts[1])
    await msg.answer(txt + (" Open your admin menu: /start" if ok else ""))


# ---------------------------------------------------------------------------
# MAIN MENU navigation (buttons)
# ---------------------------------------------------------------------------
@dp.callback_query(lambda c: c.data and c.data.startswith("menu:"))
async def menu_nav(cb: types.CallbackQuery):
    _, which = cb.data.split(":", 1)
    uid = cb.from_user.id
    role = roles.role(uid)
    if which == "hub":
        await cb.message.edit_text("Choose an option:", reply_markup=ui.main_menu(role))
    elif which == "owner":
        await cb.message.edit_text("🛠 Owner Panel", reply_markup=ui.role_menu("owner"))
    elif which == "admin":
        await cb.message.edit_text("🛠 Admin Panel", reply_markup=ui.role_menu("admin"))
    elif which == "submit":
        await submit_menu(cb)
        return
    elif which == "cap":
        await show_cap(cb)
        return
    elif which == "partners":
        await partner_menu(cb)
        return
    elif which == "contract":
        m = ledger._m(_uname(cb))
        await cb.message.edit_text(
            f"📜 Your contract ({_uname(cb)}):\n"
            f"  Accept: {m.get('accept_types') or 'not set'}\n"
            f"  Receive: {m.get('receive_types') or 'not set'}\n"
            f"  Size: {m.get('size',0)}  Band: {perf.score(_uname(cb))['band']}\n"
            f"  Daily cap: {daily_post_cap(m.get('size',0), perf.score(_uname(cb))['band'], m.get('status','ACTIVE'), m.get('is_owner',False))}")
        return
    elif which == "audit" or which == "rank":
        if not _can_panel(uid, "reward"):
            await cb.answer("Owner/admin only.", show_alert=True)
            return
        await cb.message.edit_text("Use your Admin panel to view the audit log & rank.",
                                   reply_markup=ui.role_menu(role))
        return
    await cb.answer()


async def submit_menu(cb: types.CallbackQuery):
    """Step 1 of submission: accept terms, then pick a category."""
    await cb.message.edit_text(
        TERMS_TEXT,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton("✅ Accept & continue", callback_data="terms:accept")],
            [InlineKeyboardButton("❌ Not now", callback_data="menu:hub")],
        ]))


@dp.callback_query(lambda c: c.data and c.data.startswith("terms:"))
async def accept_terms(cb: types.CallbackQuery):
    if cb.data == "terms:accept":
        store["accepted_terms"] = _uname(cb)
        store.sync()
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(t, callback_data=f"cat:{t}")] for t in POST_CATEGORIES
        ] + [[InlineKeyboardButton("⬅️ Back", callback_data="menu:hub")]])
        await cb.message.edit_text(
            "Select the NICHE category of your post (must match what the target "
            "channel has agreed to receive):", reply_markup=kb)
    else:
        await cb.message.edit_text("No problem. Send a command or use the menu.",
                                   reply_markup=ui.main_menu("user"))
    await cb.answer()


@dp.callback_query(lambda c: c.data and c.data.startswith("cat:"))
async def pick_category(cb: types.CallbackQuery):
    cat = cb.data.split(":", 1)[1]
    store["pending_cat"] = cat
    store["pending_user"] = _uname(cb)
    store.sync()
    # Ask post style (forward / direct / pin) + notification (loud / silent).
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton("🔁 Forward", callback_data="style:fwd"),
         InlineKeyboardButton("⚡ Direct", callback_data="style:direct"),
         InlineKeyboardButton("📌 Pin", callback_data="style:pin")],
        [InlineKeyboardButton("🔔 Loud", callback_data="ntf:loud"),
         InlineKeyboardButton("🔕 Silent", callback_data="ntf:silent")],
        [InlineKeyboardButton("✅ Submit (you must FORWARD the post next)",
                              callback_data="style:submit")],
        [InlineKeyboardButton("⬅️ Back", callback_data="menu:hub")],
    ])
    await cb.message.edit_text(
        f"Category: **{cat}**\n\nChoose post style & notification:\n\n"
        "📌 Note: a 'Loud' pin is only possible in a GROUP; in a CHANNEL, Telegram "
        "always pins silently. Loud/silent fully works for posting.",
        reply_markup=kb)
    await cb.answer()


@dp.callback_query(lambda c: c.data and c.data.startswith("style:"))
async def pick_style(cb: types.CallbackQuery):
    parts = cb.data.split(":")
    style = parts[1]
    if style == "submit":
        await cb.message.edit_text(
            "Now **forward** the post you want to distribute here (so its "
            "attribution stays intact).\n\n" + gate.attribution_tip())
        await cb.answer()
        return
    store["pending_style"] = style
    store.sync()
    await cb.answer(f"Style: {style}")
    await cb.message.edit_text(f"Post style set to **{style}**. Choose notification:",
                               reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                                   [InlineKeyboardButton("🔔 Loud", callback_data="ntf:loud"),
                                    InlineKeyboardButton("🔕 Silent", callback_data="ntf:silent")],
                                   [InlineKeyboardButton("✅ Submit", callback_data="style:submit")],
                               ]))


@dp.callback_query(lambda c: c.data and c.data.startswith("ntf:"))
async def pick_ntf(cb: types.CallbackQuery):
    store["pending_ntf"] = cb.data.split(":")[1]
    store.sync()
    await cb.answer(f"Notification: {cb.data.split(':')[1]}")
    await cb.message.edit_text("Now **forward** the post here to distribute it.",
                               reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                                   [InlineKeyboardButton("✅ Submit", callback_data="style:submit")],
                               ]))


async def show_cap(cb: types.CallbackQuery):
    """Reached via menu_nav (which == 'cap'). Displays the user's daily post cap."""
    u = _uname(cb)
    m = ledger._m(u)
    s = perf.score(u)
    cap = daily_post_cap(m.get("size", 0), s["band"], m.get("status", "ACTIVE"),
                         m.get("is_owner", False))
    await cb.message.edit_text(
        f"📊 Daily post cap for {u}:\n"
        f"  Subscribers: {m.get('size',0)}   Band: {s['band']}   Status: {m.get('status','ACTIVE')}\n"
        f"  → **{cap if cap!= -1 else 'unlimited'} post(s) per day**\n\n"
        "The cap scales with channel size × performance. A bigger, better-performing "
        "channel gets more slots; a small/underperforming one stays low so nobody spams.",
        reply_markup=back_btn_q("menu:hub"))
    await cb.answer()


def back_btn_q(cb: str):
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton("⬅️ Back", callback_data=cb)]])


# ---------------------------------------------------------------------------
# FORWARD handler -> run the SUBMISSION GATE, then enforce the cap, then distribute
# ---------------------------------------------------------------------------
@dp.message(lambda m: is_forward(m))
async def on_forward(msg: types.Message):
    u = _uname(msg)
    ledger.set_user_id(u, msg.from_user.id)   # remember id so we can DM them later

    # ===== OWNER FAST-PATH: fully exempt — no terms/category/gate/cap =====
    # The owner bypasses the entire submission funnel. Route freely, to any channel,
    # with no credit cost and no daily cap. Authorised by OWNER_USER_ID (numeric) OR
    # the reserved owner username.
    if ledger._is_exempt(u):
        await owner_forward(msg, u)
        return

    # 1) terms accepted?
    if store.get("accepted_terms") != u:
        await msg.answer("First accept the posting terms: /start → Submit a post.",
                         reply_markup=ui.main_menu(roles.role(msg.from_user.id)))
        return
    # 2) category declared?
    cat = store.get("pending_cat")
    if not cat:
        await msg.answer("Please pick your post's niche category first (/start → Submit).")
        return
    # 3) daily cap check (size + performance).
    m = ledger._m(u)
    s = perf.score(u)
    cap = daily_post_cap(m.get("size", 0), s["band"], m.get("status", "ACTIVE"),
                         m.get("is_owner", False))
    day = time.strftime("%Y-%m-%d")
    if m.get("last_cap_day") != day:
        m["last_cap_day"] = day
        m["cap_used_today"] = 0
    if cap != -1 and m.get("cap_used_today", 0) >= cap:
        await msg.answer(f"⛔ Daily post cap reached ({cap}). "
                         "Your cap is based on size × performance. It resets tomorrow "
                         "(UTC).")
        return
    # 4) submission gate (needs target receive-types; gather from ledger targets).
    gate_target_types = []
    ok, verdict, why = gate.gate(cat, (msg.text or ""), gate_target_types)
    if not ok:
        await msg.answer(f"⛔ Refused: {why}\n\n{gate.attribution_tip()}")
        return
    if verdict == "review":
        # human review queue
        item = review.submit(cat, u, msg.text or "", forward_source(msg), reason=why)
        await msg.answer(f"⚠ This post hits a borderline pattern. It's queued for "
                         f"human review (#{item['id']}). You'll be notified when it's "
                         "approved.")
        return
    # 5) attribution tip (advice only)
    await msg.answer(gate.attribution_tip())
    ok_spend, why_spend = ledger.can_spend(u)
    if not ok_spend:
        await msg.answer(f"Not eligible yet: {why_spend}")
        return
    # 6) ask how many (post->channel) pairs (1..5), style preserved from pending.
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton("1", callback_data="s:1"), InlineKeyboardButton("2", callback_data="s:2")],
        [InlineKeyboardButton("3", callback_data="s:3"), InlineKeyboardButton("4", callback_data="s:4")],
        [InlineKeyboardButton("5", callback_data="s:5")],
    ])
    await msg.answer("How many (post→channel) pairs? 1 credit each. "
                     "Sending ONE post to 5 channels = 5 credits.",
                     reply_markup=kb)
    from_chat = getattr(msg, "forward_from_chat", None)
    store["pending"] = {
        "user": u, "tier": ledger.balance(u)["tier"], "post_type": cat,
        "source": forward_source(msg), "style": store.get("pending_style", "fwd"),
        "ntf": store.get("pending_ntf", "loud"),
        "forward_from_chat_id": from_chat.id if from_chat else msg.chat.id,
        "forward_from_message_id": getattr(msg, "forward_from_message_id", None),
    }
    store.sync()


async def owner_forward(msg: types.Message, u: str):
    """The owner's exempt path: skip terms/category/gate/cap, just pick how many
    (post->channel) pairs to route. No credits, no cap, route to any channel."""
    from_chat = getattr(msg, "forward_from_chat", None)
    style = store.get("pending_style", "fwd")
    store["pending"] = {
        "user": u, "tier": ledger.balance(u)["tier"], "post_type": store.get("pending_cat", "General"),
        "source": forward_source(msg), "style": style,
        "ntf": store.get("pending_ntf", "loud"),
        "forward_from_chat_id": from_chat.id if from_chat else msg.chat.id,
        "forward_from_message_id": getattr(msg, "forward_from_message_id", None),
        "owner_exempt": True,
    }
    store.sync()
    await msg.answer(
        "👑 **Owner mode** — you're exempt: no terms, no category check, no cap, "
        "no credit cost.\n\nSend the post style too if you want one: use the Submit menu "
        "to pre-pick forward/direct/pin + loud/silent, or it defaults to **Forward+loud**.\n\n"
        "How many (post→channel) pairs to route? (Free — you're exempt.)",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton("1", callback_data="s:1"), InlineKeyboardButton("2", callback_data="s:2")],
            [InlineKeyboardButton("3", callback_data="s:3"), InlineKeyboardButton("4", callback_data="s:4")],
            [InlineKeyboardButton("5", callback_data="s:5")],
            [InlineKeyboardButton("All matching", callback_data="s:all")],
        ]))


@dp.callback_query(lambda c: c.data and c.data.startswith("s:"))
def owner_targets(post_type: str = "General") -> list[str]:
    """The owner routes to EVERY active, non-blocked channel (any size/band/tier)."""
    out = []
    for username, m in ledger.ledger.items():
        if m.get("is_owner"):
            continue
        if m.get("status") in ("RESTRICTED", "REMOVED"):
            continue
        out.append(username)
    return out


async def pick_spend(cb: types.CallbackQuery):
    sender = _uname(cb)
    raw = cb.data.split(":")[1]
    is_owner = ledger._is_exempt(sender)
    pending = store.get("pending", {})

    if raw == "all":
        # Owner only: route to every ACTIVE channel (any size/band), no limit.
        if not is_owner:
            await cb.answer("Only the owner can route to all.", show_alert=True)
            return
        targets = owner_targets(pending.get("post_type", "General"))
    else:
        n = int(raw)
        ok, why = ledger.spend(sender, n)
        if not ok:
            await cb.answer(why, show_alert=True)
            return
        # The owner routes anywhere (not just same-band); others match like-with-like.
        if is_owner:
            pool = owner_targets(pending.get("post_type", "General"))
            targets = pool[:n] if n else []
        else:
            targets = perf.match(sender, want_channels=n,
                                 post_type=pending.get("post_type", "General"))
    await cb.message.answer(f"Distributing to {len(targets)} channel(s): {targets or 'none'}.")
    source = pending.get("source", "unknown")
    style = pending.get("style", "fwd")
    ntf = pending.get("ntf", "loud") == "silent"
    for target in targets:
        perf.mark_offered(target)
        m = ledger._m(target)
        m["cap_used_today"] = m.get("cap_used_today", 0) + 1
        ledger.save()
        if ledger.is_direct(target):
            await direct_deliver(cb, sender, target, pending, style=style, silent=ntf)
        else:
            audit.record(bot="reward", sender=sender, source=source,
                         post_type=pending.get("post_type", "General"),
                         target_channel=target, mode="chain", status="offered",
                         forward_valid=True, style=style)
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton("✅ Agree (forward it)", callback_data=f"chain:agree:{sender}"),
                 InlineKeyboardButton("❌ Disagree (skip)", callback_data=f"chain:dis:{sender}")],
                [InlineKeyboardButton("🚩 Report as scam/fraud", callback_data=f"report:{sender}:{target}")],
            ])
            await bot.send_message(target,
                                   f"{sender} has a {style} post ({pending.get('post_type','General')}) "
                                   f"for your band. Agree to forward, skip, or report.",
                                   reply_markup=kb)
    await cb.answer("done", show_alert=False)


async def direct_deliver(cb, sender, target, pending, style="fwd", silent=False):
    """Direct delivery to a channel where the bot is admin. forwardMessage keeps
    attribution. If style == 'pin', also pin after posting (channel pin = silent)."""
    f_chat_id = pending.get("forward_from_chat_id")
    f_msg_id = pending.get("forward_from_message_id")
    try:
        if f_chat_id is not None and f_msg_id is not None:
            sent = await bot.forward_message(chat_id=target, from_chat_id=f_chat_id,
                                             message_id=f_msg_id,
                                             disable_notification=silent)
        else:
            sent = await bot.send_message(target,
                                          f"[direct] forwarded post from {sender}",
                                          disable_notification=silent)
        perf.mark_posted(target)
        if style == "pin":
            try:
                await bot.pin_chat_message(chat_id=target, message_id=sent.message_id)
            except Exception as e:
                logging.warning("pin failed on %s: %s", target, e)
        audit.record(bot="reward", sender=sender, target_channel=target,
                     mode="direct", status="delivered", forward_valid=True, style=style)
        await cb.message.answer(f"⚡ Auto-posted to {target} (direct, {style}).")
    except Exception as e:
        audit.record(bot="reward", sender=sender, target_channel=target,
                     mode="direct", status="offered", forward_valid=True,
                     error=str(e)[:120])
        await cb.message.answer(f"Direct delivery to {target} failed ({e}). Fell back to offer.")


@dp.callback_query(lambda c: c.data and c.data.startswith("chain:"))
async def chain_delivery(cb: types.CallbackQuery):
    _, decision, sender = cb.data.split(":")
    target = _uname(cb)
    if decision == "agree":
        ledger.earn(sender)
        perf.mark_posted(target)
        audit.record(bot="reward", sender=sender, target_channel=target,
                     mode="chain", status="agreed", forward_valid=True)
        await cb.message.answer("Thanks! Forward the post to your channel to complete "
                                "this (using forwardMessage keeps the original attribution).")
    else:
        audit.record(bot="reward", sender=sender, target_channel=target,
                     mode="chain", status="skipped", forward_valid=True)
        await cb.message.answer("Skipped. Moving to the next target.")
    await cb.message.edit_reply_markup(None)
    await cb.answer()


@dp.callback_query(lambda c: c.data and c.data.startswith("report:"))
async def report_post(cb: types.CallbackQuery):
    _, sender, target = cb.data.split(":")
    reporter = _uname(cb)
    item = reports.report(sender=sender, reporter=reporter,
                          reported_post={"target_channel": target,
                                         "post_type": store.get("pending", {}).get("post_type", "General")},
                          reason="receiver flagged as inappropriate/scam/fraud")
    audit.record(bot="reward", sender=sender, target_channel=target,
                 mode="chain", status="skipped", forward_valid=True)
    await cb.message.answer(f"🚩 Report #{item['id']} logged against {sender}. Admins will review.")
    await cb.message.edit_reply_markup(None)
    await cb.answer()


# ---------------------------------------------------------------------------
# PARTNER menu (peer-to-peer partnerships; owner-as-mediator)
# ---------------------------------------------------------------------------
async def partner_menu(cb: types.CallbackQuery):
    u = _uname(cb)
    active = contracts.list("ACTIVE") + contracts.list("RENEWED")
    mine = [r for r in active if u in (r.get("a"), r.get("b"))]
    lines = [f"🤝 Partnerships for {u}:",
             "Tap a partner to view the contract, renew it, or request close.",
             "To OPEN a new one, tap ➕ New partnership and enter the other channel's "
             "@username.", ""]
    buttons = []
    for r in mine[:8]:
        other = r["b"] if r["a"] == u else r["a"]
        buttons.append([InlineKeyboardButton(
            f"• {other}  [{r['status']}]", callback_data=f"partner:view:{r['id']}")])
    buttons += [
        [InlineKeyboardButton("➕ New partnership", callback_data="partner:new")],
        [InlineKeyboardButton("⬅️ Back", callback_data="menu:hub")],
    ]
    if not mine:
        lines.append("(No active partnerships yet.)")
    await cb.message.edit_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await cb.answer()


@dp.callback_query(lambda c: c.data and c.data.startswith("partner:"))
async def partner_flow(cb: types.CallbackQuery):
    parts = cb.data.split(":")
    act = parts[1]
    if act == "new":
        await cb.message.edit_text(
            "Enter the other channel's **@username** (they must have added the bot as "
            "admin in that channel too, so both can post):")
        store["partner_phase"] = "username"
        store.sync()
        await cb.answer()
        return
    if act == "view":
        rid = parts[2]
        r = contracts.get(rid)
        if not r:
            await cb.answer("Contract not found.", show_alert=True)
            return
        other = r["b"] if _uname(cb) == r["a"] else r["a"]
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton("🔄 Renew", callback_data=f"partner:renew:{rid}"),
             InlineKeyboardButton("🔒 Request close", callback_data=f"partner:close:{rid}")],
            [InlineKeyboardButton("⬅️ Back", callback_data="menu:partners")],
        ])
        await cb.message.edit_text(
            f"📜 Contract with {other} [{r['status']}]\n"
            f"  {r['contract']}\n"
            f"  Opened: {r['opened']}\n"
            "  Either side may request close; the owner gives the FINAL close so it's "
            "recorded.", reply_markup=kb)
        await cb.answer()
        return
    if act == "renew":
        r = contracts.renew(parts[2], note=f"renewed by {_uname(cb)}")
        owner_notify(f"🔄 Partnership RENEWED {r['id']}: {r['a']} <-> {r['b']}")
        await cb.answer("Partnership renewed. Owner notified.")
        await cb.message.edit_text("✅ Renewed. Owner notified.", reply_markup=ui.role_menu(roles.role(cb.from_user.id)))
        return
    if act == "close":
        r = contracts.get(parts[2])
        if not r:
            await cb.answer("not found", show_alert=True)
            return
        r = contracts.request_close(parts[2], by=_uname(cb), reason="requested by partner")
        owner_notify(f"🔒 CLOSE REQUESTED {r['id']}: {r['a']} <-> {r['b']} by {_uname(cb)}\n"
                     "You give the final close (/partner_close).")
        await cb.answer("Close requested. Owner notified; you'll get the final close.")
        await cb.message.edit_text("🔒 Close requested. Awaiting owner's final close.",
                                   reply_markup=ui.role_menu(roles.role(cb.from_user.id)))
        return


@dp.message()
async def partner_username_capture(msg: types.Message):
    """Capture the partner @username typed by a user during 'New partnership'."""
    if msg.text and msg.text.startswith("/"):
        return  # let commands through
    if store.get("partner_phase") != "username":
        return  # not in this flow -> let forward guard handle it
    u = _uname(msg)
    other = msg.text.strip()
    if not other.startswith("@"):
        other = "@" + other
    if other == u:
        await msg.answer("You can't partner with yourself.")
        return
    # Both must be registered in the ledger.
    if other not in ledger.ledger:
        await msg.answer(f"{other} is not registered with CLICKMINT yet.")
        store["partner_phase"] = None
        store.sync()
        return
    m = ledger._m(u)
    other_m = ledger._m(other)
    if not (m.get("direct_mode") and other_m.get("direct_mode")):
        await msg.answer("Both channels must have added the bot as admin (Post Messages) "
                         "so you can post into each other. Fix that on both sides, then retry.")
        store["partner_phase"] = None
        store.sync()
        return
    fields = contracts.self_contract_fields()
    rec = contracts.open(u, other, {"note": f"opened by {u}",
                                    "types": store.get("pending_cat", "General")})
    owner_notify(f"🤝 NEW PARTNERSHIP OPENED {rec['id']}: {u} <-> {other}")
    await msg.answer(
        f"🤝 Partnership opened between {u} and {other} (#{rec['id']}).\n\n"
        "Agree these fields to keep it healthy:\n" + fields +
        "\nOwner was notified and is the mediator. Both sides must honour the terms "
        "(niche-only, no-ads, no-spam, safety). Either can request close if it stops "
        "benefiting.")
    store["partner_phase"] = None
    store.sync()


def owner_notify(text: str):
    """Best-effort DM to the owner (and any scoped admins) about contract events."""
    if not roles.owner_user_id or roles.owner_user_id == "0":
        return
    asyncio.ensure_future(_notify_owner(text))


async def _notify_owner(text: str):
    try:
        await bot.send_message(int(roles.owner_user_id), text)
    except Exception as e:
        logging.warning("owner notify failed: %s", e)


# ---------------------------------------------------------------------------
# OWNER / ADMIN PANEL: review queue, admins, terms (buttons)
# ---------------------------------------------------------------------------
@dp.callback_query(lambda c: c.data and c.data.startswith("panel:"))
async def panel(cb: types.CallbackQuery):
    _, which = cb.data.split(":", 1)
    uid = cb.from_user.id
    if which in ("review", "admins", "terms"):
        if not _can_panel(uid, "reward") and not _can_panel(uid, "partnership"):
            await cb.answer("Owner/admin only.", show_alert=True)
            return
    if which == "review":
        pending = review.pending()
        if not pending:
            await cb.message.edit_text("✅ No posts awaiting human review.",
                                       reply_markup=ui.role_menu(roles.role(uid)))
            return
        lines = [f"{len(pending)} post(s) awaiting review:", ""]
        kb = []
        for it in pending[:8]:
            lines.append(f"#{it['id']} | {it['category']} | by {it['sender']}\n  {it['text'][:80]}")
            kb.append([InlineKeyboardButton(f"✅ Approve #{it['id']}", callback_data=f"review:approve:{it['id']}"),
                       InlineKeyboardButton(f"❌ Reject #{it['id']}", callback_data=f"review:reject:{it['id']}")])
        kb.append([InlineKeyboardButton("⬅️ Back", callback_data="menu:admin")])
        await cb.message.edit_text("\n".join(lines[:40]), reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
        await cb.answer()
        return
    if which == "admins":
        if not roles.is_owner(uid):
            await cb.answer("Owner only.", show_alert=True)
            return
        await cb.message.edit_text(
            "👤 Manage admins.\n"
            "• To invite an admin: they must contact you FIRST. Then tap Generate code, "
            "pick a scope, and send them the code — they enter it with /adminlogin.\n"
            "• Admins are SCOPED: reward, partnership, or both.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton("🔑 Generate invite (reward)", callback_data="admin:invite:reward"),
                 InlineKeyboardButton("🔑 Generate invite (partnership)", callback_data="admin:invite:partnership")],
                [InlineKeyboardButton("🔑 Generate invite (both)", callback_data="admin:invite:both")],
                [InlineKeyboardButton("⬅️ Back", callback_data="menu:owner")],
            ]))
        await cb.answer()
        return
    if which == "terms":
        await cb.message.edit_text(TERMS_TEXT, reply_markup=back_btn_q("menu:owner"))
        await cb.answer()
        return
    await cb.answer()


@dp.callback_query(lambda c: c.data and c.data.startswith("admin:invite:"))
async def gen_invite(cb: types.CallbackQuery):
    if not roles.is_owner(cb.from_user.id):
        await cb.answer("Owner only.", show_alert=True)
        return
    scope = cb.data.split(":")[2]
    scopes = {"reward": ["reward"], "partnership": ["partnership"], "both": ["reward", "partnership"]}[scope]
    code = roles.create_invite(scopes, created_by=cb.from_user.id)
    await cb.message.edit_text(
        f"🔑 One-time admin code: **{code}**\n\n"
        f"Scope: {scopes}\n"
        "Send this to the trusted person. They run:\n  /adminlogin <CODE>\n"
        "Then open their menu with /start. A code is single-use.")
    await cb.answer()


@dp.callback_query(lambda c: c.data and c.data.startswith("review:"))
async def decide_review(cb: types.CallbackQuery):
    if not _can_panel(cb.from_user.id, "reward"):
        await cb.answer("Owner/admin only.", show_alert=True)
        return
    _, action, rid = cb.data.split(":")
    item = review.decide(int(rid), approve=(action == "approve"),
                         note=f"{action} by {_uname(cb)}")
    if not item:
        await cb.answer("not found", show_alert=True)
        return
    # Notify the sender of the outcome (handled by the notify loop, which owns DM rights).
    review.mark_notify(int(rid), via="reward")
    await cb.answer(f"#{rid} {action}d.")
    await cb.message.edit_text(f"#{rid} {action}d. Sender will be notified.",
                               reply_markup=back_btn_q("menu:admin"))


async def _notify_review_sender(item: dict):
    """DM the sender that their queued post was approved/rejected."""
    sender = item.get("sender")
    uid = ledger.user_id(sender) if sender else None
    if not uid:
        return False
    ok = item.get("status") == "approved"
    try:
        await bot.send_message(
            uid,
            (f"✅ Good news — your post was **approved** and can now be distributed. "
             f"Resubmit it (category: {item.get('category')}).") if ok
            else "❌ Your submitted post was **not approved** by review. Please "
                 "resubmit something that follows the terms.")
        return True
    except Exception as e:
        logging.warning("notify sender %s failed: %s", sender, e)
        return False


async def notify_loop():
    """Background: hand out queued review outcomes to the senders (reward scope)."""
    while True:
        try:
            for item in review.pending_notify("reward"):
                if await _notify_review_sender(item):
                    review.clear_notify(item["id"])
        except Exception as e:
            logging.warning("notify_loop error: %s", e)
        await asyncio.sleep(15)


# ---------------------------------------------------------------------------
# Legacy command fallbacks (still work for power users)
# ---------------------------------------------------------------------------
@dp.message(Command("agree"))
async def agree(msg: types.Message):
    u = _uname(msg)
    m = ledger.earn(u)
    await msg.answer(f"+1 credit. Balance {m['balance']} (earned {m['earned']}).")


@dp.message(Command("rank"))
async def rank_view(msg: types.Message):
    if not _can_panel(msg.from_user.id, "reward"):
        await msg.answer("Owner/admin only.")
        return
    lines = ["PERFORMANCE RANK (band A/B/C):", ""]
    for username, m in ledger.ledger.items():
        if m.get("is_owner"):
            continue
        s = perf.score(username)
        cap = daily_post_cap(m.get("size", 0), s["band"], m.get("status", "ACTIVE"))
        lines.append(f"{username:<22} {s['band']}  {s['score']:.2f}  {s['status']}  "
                     f"direct={'✔' if ledger.is_direct(username) else '—'}  "
                     f"cap={cap}  ({m.get('size',0)})")
    await msg.answer("\n".join(lines[:55]))


@dp.message(Command("audit"))
async def audit_view(msg: types.Message):
    if not _can_panel(msg.from_user.id, "reward"):
        await msg.answer("Owner/admin only.")
        return
    log = audit.last(15)
    lines = [audit.summary(), ""]
    for r in reversed(log):
        lines.append(f"[{r.get('ts')}] {r.get('sender')} -> {r.get('target_channel')} "
                     f"| {r.get('status')} | {r.get('post_type')} | fwd={'✅' if r.get('forward_valid', True) else '❌'}")
    await msg.answer("\n".join(lines[:55]))


@dp.message(Command("reports"))
async def view_reports(msg: types.Message):
    if not _can_panel(msg.from_user.id, "reward"):
        await msg.answer("Owner/admin only.")
        return
    pending = reports.pending()
    if not pending:
        await msg.answer("✅ No pending reports.")
        return
    lines = [f"{len(pending)} pending report(s):", ""]
    for r in pending:
        lines.append(f"#{r['id']} | {r['sender']} reported by {r['reporter']}")
    await msg.answer("\n".join(lines[:55]))


# --- scheduling commands kept from before ---
@dp.message(Command("schedule"))
async def schedule_direct(msg: types.Message):
    if not _can_panel(msg.from_user.id, "reward"):
        await msg.answer("Owner/admin only.")
        return
    parts = msg.text.split()
    if len(parts) < 4:
        await msg.answer("Usage: /schedule <@target> <YYYY-MM-DD> <HH:MM>  (UTC, 24h)")
        return
    target, day_str, hm = parts[1], parts[2], parts[3]
    if not target.startswith("@"):
        target = "@" + target
    try:
        at = dt.datetime.strptime(f"{day_str} {hm}", "%Y-%m-%d %H:%M").timestamp()
    except ValueError:
        await msg.answer("Bad time. Use YYYY-MM-DD HH:MM in UTC.")
        return
    if at < time.time():
        await msg.answer("That time is in the past.")
        return
    pending = store.get("pending", {})
    rec = sched.schedule(at=at, target=target, sender=pending.get("user", _uname(msg)),
                         from_chat_id=pending.get("forward_from_chat_id"),
                         from_message_id=pending.get("forward_from_message_id"),
                         post_type=pending.get("post_type", "General"))
    await msg.answer(f"⏰ Scheduled #{rec['id']} to {target} at {day_str} {hm} UTC.")
    audit.record(bot="reward", sender=pending.get("user", _uname(msg)),
                 target_channel=target, mode="direct", status="scheduled",
                 forward_valid=True, scheduled_at=f"{day_str} {hm} UTC")


async def run_due():
    for rec in sched.due():
        try:
            if rec.get("from_chat_id") is not None and rec.get("from_message_id") is not None:
                await bot.forward_message(chat_id=rec["target"],
                                          from_chat_id=rec["from_chat_id"],
                                          message_id=rec["from_message_id"])
            else:
                await bot.send_message(rec["target"], f"[direct] posted from {rec['sender']}")
            perf.mark_posted(rec["target"])
            sched.mark_done(rec["id"])
            audit.record(bot="reward", sender=rec["sender"], target_channel=rec["target"],
                         mode="direct", status="delivered", forward_valid=True)
        except Exception as e:
            sched.mark_done(rec["id"], note=str(e)[:120])
            audit.record(bot="reward", sender=rec["sender"], target_channel=rec["target"],
                         mode="direct", status="failed", forward_valid=True, error=str(e)[:120])


async def scheduler_loop():
    while True:
        try:
            await run_due()
        except Exception as e:
            logging.warning("scheduler_loop error: %s", e)
        await asyncio.sleep(30)


async def main():
    asyncio.create_task(scheduler_loop())
    asyncio.create_task(notify_loop())
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
