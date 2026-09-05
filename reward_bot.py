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
  • Post STYLE: forward/direct delivery + loud/silent notifications (pinning is disabled).
  • Button menus everywhere (commands still work as a fallback).
  • ADMIN invite-code login: an admin must contact you first; you issue a one-time
    code; they redeem it to unlock a SCOPED admin menu (reward / partnership / both).
"""
import asyncio
import logging
import time
import datetime as dt
from aiogram import Bot, Dispatcher, types
from aiogram.dispatcher.event.bases import UNHANDLED
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from core import (CreditLedger, DeliveryLog, ReportRegistry, PerformanceEngine,
                  is_forward, forward_source, allows_receive)
from channel_registry import ChannelRegistry
from features import (ReferralLedger, AvailablePostQueue, BubbleNotifier,
                      AnnouncementBoard, RankVisibility, StatsBook, ReroutePlanner,
                      category_counts)
from store import JsonStore
from scheduler import Scheduler
from governance import (SubmissionGate, ReviewQueue, PartnerContractRegistry,
                        RoleRegistry, POST_CATEGORIES, TERMS_TEXT, daily_post_cap)
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
channels = ChannelRegistry(store)
referrals = ReferralLedger(store, ledger)
available = AvailablePostQueue(store)
bubbles = BubbleNotifier(store)
announcements = AnnouncementBoard(store)
ranks = RankVisibility(store)
stats = StatsBook(store)
reroutes = ReroutePlanner(store)
# PerformanceEngine consumes real observations when available; absent fields stay absent.
perf.views_provider = stats.provider

gate = SubmissionGate(store)
review = ReviewQueue(store)
contracts = PartnerContractRegistry(store)
roles = RoleRegistry(store, owner_user_id=OWNER_USER_ID)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


@dp.update.outer_middleware()
async def bind_identity(handler, event, data):
    """Bind @username -> numeric user id on EVERY update, before any handler.

    Owner exemption is decided from the numeric id, but the id was only ever
    recorded inside the forward handler. So an owner whose first action was
    /balance, /start or any menu button was charged credits and capped like an
    ordinary member until they happened to forward something. Binding here
    means every entry point knows who it is talking to.
    """
    src = getattr(event, "message", None) or getattr(event, "callback_query", None)
    fu = getattr(src, "from_user", None)
    if fu is not None and not fu.is_bot:
        ledger.set_user_id("@" + (fu.username or str(fu.id)), fu.id)
    return await handler(event, data)


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
# PER-USER SESSION STATE
# ---------------------------------------------------------------------------
# The submission funnel (terms accepted -> category -> style -> forward -> pair
# count) used to be kept in single global store keys ("accepted_terms",
# "pending_cat", "pending", ...). With more than one member using the bot at the
# same time that state is shared: B picking a category overwrote A's, and A's
# forwarded post could be routed with B's settings. Everything is now keyed by
# the Telegram user id.
SESSION_TTL = 24 * 3600          # stale funnels are dropped after a day


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
        sessions.pop(k, None)                 # prune stale funnels
    store["sessions"] = sessions
    store.sync()
    return s


def _session_clear(uid, *keys) -> None:
    sessions = _sessions()
    s = dict(sessions.get(str(uid), {}))
    if keys:
        for k in keys:
            s.pop(k, None)
        sessions[str(uid)] = s
    else:
        sessions.pop(str(uid), None)
    store["sessions"] = sessions
    store.sync()


def _parse_registration(text: str):
    """Parse '/start @chan 1200' (or '/register @chan 1200').

    Returns (username, size) or (None, error_message). Never raises: a typo must
    answer with help, not kill the handler.
    """
    parts = (text or "").split()
    if len(parts) < 3:
        return None, ("Usage: /register @yourchannel <subscriber_count>\n"
                      "e.g.  /register @MyChan 1200")
    username = parts[1]
    if not username.startswith("@"):
        username = "@" + username
    if len(username) < 2:
        return None, "That channel @username doesn't look right."
    raw = parts[2].replace(",", "").replace("_", "")
    try:
        size = int(raw)
    except ValueError:
        return None, f"'{parts[2]}' is not a number. Example: /register @MyChan 1200"
    if size < 0:
        return None, "Subscriber count can't be negative."
    if size > 100_000_000:
        return None, "That subscriber count isn't plausible."
    return (username, size), None


def _do_register(msg, username: str, size: int, kind: str = "channel") -> str:
    m = ledger.register(username, size)
    # Keep a separate ownership registry: one Telegram user may manage many
    # channels/groups, each with its own band, status and categories.
    channels.add(msg.from_user.id, username, username, kind,
                 ["General"], size=size, bot_added=False)
    ledger.set_user_id(username, msg.from_user.id)
    s = perf.score(username)
    cap = daily_post_cap(size, s["band"], m.get("status", "ACTIVE"),
                         m.get("is_owner", False))
    return (f"✅ Registered {username} — {size} subs, tier {m['tier']}, "
            f"band {s['band']}.\n"
            f"Daily post cap: {'unlimited (owner)' if cap == -1 else cap} "
            "(size × performance, not size alone).\n"
            f"Credits: {m['balance']} (onboarding seed). Earn more by sharing "
            "other members' posts.")


# ---------------------------------------------------------------------------
# /start  -> branch by role: Owner / Admin / User, all via BUTTONS
# ---------------------------------------------------------------------------
@dp.message(Command("start"))
async def start(msg: types.Message):
    uid = _uid(msg)
    role = roles.role(uid)
    # `/start @chan 1200` registers the channel. The old build advertised this in
    # the welcome text but never parsed the arguments, so nobody could register
    # and every member stayed at size 0 (which pinned their cap at the floor).
    if len((msg.text or "").split()) >= 3:
        parsed, err = _parse_registration(msg.text)
        if err:
            await msg.answer(err, reply_markup=ui.main_menu(role, include_partnership=False))
            return
        await msg.answer(_do_register(msg, *parsed), reply_markup=ui.main_menu(role, include_partnership=False))
        return
    if role == "owner":
        await msg.answer(
            "🛠 Welcome, Owner. Your menu controls everyone — users, admins, "
            "contracts, and the reward/partnership networks.",
            reply_markup=ui.main_menu("owner", include_partnership=False))
    elif role == "admin":
        await msg.answer("🛠 Admin menu. Use it to moderate the network you're scoped to.",
                         reply_markup=ui.main_menu("admin", include_partnership=False))
    else:
        head = ("Welcome to CLICKMINT.\n"
                "To use the network, register your channel first:\n"
                "Send: /register @yourchannel <subscriber_count>\n"
                "e.g.  /register @MyChan 1200")
        await msg.answer(head,
                         reply_markup=ui.main_menu("user", include_partnership=False))


@dp.message(Command("register"))
async def register_cmd(msg: types.Message):
    parsed, err = _parse_registration(msg.text)
    if err:
        await msg.answer(err)
        return
    await msg.answer(_do_register(msg, *parsed),
                     reply_markup=ui.main_menu(roles.role(_uid(msg)), include_partnership=False))


@dp.message(Command("removechannel"))
async def remove_channel_cmd(msg: types.Message):
    parts = (msg.text or "").split()
    if len(parts) != 2:
        await msg.answer("Usage: /removechannel @channel_or_group")
        return
    target = parts[1] if parts[1].startswith("@") else "@" + parts[1]
    row = next((r for r in channels.mine(msg.from_user.id)
                if r.get("username") == target or str(r.get("chat_id")) == target), None)
    if not row or not channels.remove(msg.from_user.id, row["chat_id"]):
        await msg.answer("That channel/group is not registered under your account.")
        return
    await msg.answer(f"✅ Removed {target} from My channels / groups.")


@dp.message(Command("editchannel"))
async def edit_channel_cmd(msg: types.Message):
    parts = (msg.text or "").split()
    if len(parts) < 3:
        await msg.answer("Usage: /editchannel @channel_or_group category[,category...]\n"
                         "Choose up to three categories.")
        return
    target = parts[1] if parts[1].startswith("@") else "@" + parts[1]
    row = next((r for r in channels.mine(msg.from_user.id)
                if r.get("username") == target or str(r.get("chat_id")) == target), None)
    if not row:
        await msg.answer("That channel/group is not registered under your account.")
        return
    cats = [c.strip() for c in " ".join(parts[2:]).split(",") if c.strip()]
    try:
        channels.update(msg.from_user.id, row["chat_id"], categories=cats)
    except ValueError as exc:
        await msg.answer(str(exc)); return
    await msg.answer(f"✅ Updated {target}: {', '.join(cats)}")


@dp.message(Command("registergroup"))
async def register_group_cmd(msg: types.Message):
    parsed, err = _parse_registration(msg.text)
    if err:
        await msg.answer(err.replace("/register", "/registergroup")); return
    await msg.answer(_do_register(msg, *parsed, kind="group"),
                     reply_markup=ui.main_menu(roles.role(_uid(msg)), include_partnership=False))


@dp.message(Command("mychannels"))
async def mychannels_cmd(msg: types.Message):
    """Show every channel/group owned by this Telegram user (not just one row)."""
    rows = channels.mine(msg.from_user.id)
    if not rows:
        await msg.answer("📂 You have no registered channels or groups yet.\n"
                         "Use /register @name <subscriber_count> to add one.")
        return
    lines = ["📂 MY CHANNELS / GROUPS", ""]
    for row in rows:
        access = "✅ bot added" if row.get("bot_added") else "⚠️ add bot for accurate stats"
        lines.append(f"• {row.get('username')} · {row.get('kind')} · "
                     f"band {row.get('band')} · {row.get('status')} · {access}")
    await msg.answer("\n".join(lines), reply_markup=ui.main_menu("user", include_partnership=False))


@dp.message(Command("announce"))
async def announce_cmd(msg: types.Message):
    if not roles.is_owner(msg.from_user.id):
        await msg.answer("🔒 Owner only.")
        return
    text = (msg.text or "").partition(" ")[2].strip()
    if not text:
        await msg.answer("Usage: /announce your announcement text")
        return
    item = announcements.publish(msg.from_user.id, text)
    sent = failed = 0
    for member in ledger.ledger.values():
        uid = member.get("user_id")
        if not uid or int(uid) == int(msg.from_user.id):
            continue
        try:
            await bot.send_message(int(uid), "📣 CLICKMINT ANNOUNCEMENT\n\n" + text)
            sent += 1
        except Exception:
            failed += 1
    await msg.answer(f"📣 Announcement #{item['id']} sent to {sent} user(s). "
                     f"Unavailable: {failed}.")


@dp.message(Command("categorystats"))
async def category_stats_cmd(msg: types.Message):
    if not roles.has_access(msg.from_user.id, "reward"):
        await msg.answer("🔒 Owner/admin only.")
        return
    counts = category_counts(channels._items().values())
    await msg.answer("📊 REGISTERED DESTINATIONS BY CATEGORY\n" +
                     "\n".join(f"• {k}: {v}" for k, v in sorted(counts.items()))
                     if counts else "No categorized destinations yet.")


@dp.message(Command("refer"))
async def refer_cmd(msg: types.Message):
    code = referrals.create_code(msg.from_user.id)
    st = referrals.stats(msg.from_user.id)
    await msg.answer("🔗 Your referral code: " + code + "\n"
                     "Share it with a new member. You earn 1 credit only after they "
                     "forward and complete one post.\n"
                     f"Completed: {st['completed']} · pending: {st['pending']}")


@dp.message(Command("joinref"))
async def join_ref_cmd(msg: types.Message):
    parts = (msg.text or "").split()
    if len(parts) != 2 or not referrals.attach(msg.from_user.id, parts[1]):
        await msg.answer("That referral code is invalid, already used, or belongs to you.")
        return
    await msg.answer("✅ Referral linked. Your referrer earns a credit only after you "
                     "complete one genuine forwarded post.")


@dp.message(Command("available"))
async def available_cmd(msg: types.Message):
    rows = available.available(msg.from_user.id, limit=10)
    if not rows:
        await msg.answer("📭 No posts are currently available for your band/category.")
        return
    lines = [f"📬 {len(rows)} post(s) available:"]
    for row in rows:
        lines.append(f"• {row['id']} · {row.get('category', 'General')}"
                     + (" · 👑 owner priority" if row.get("owner") else ""))
    await msg.answer("\n".join(lines))


@dp.message(Command("scan"))
async def scan_cmd(msg: types.Message):
    """Refresh what the Bot API can actually verify for the user's destinations."""
    rows = channels.mine(msg.from_user.id)
    if not rows:
        await msg.answer("Register a channel/group first with /register.")
        return
    lines = ["🔎 CHANNEL SCAN", ""]
    for row in rows:
        try:
            count = await bot.get_chat_member_count(row["chat_id"])
            stats.record(row["chat_id"], subscribers=count)
            channels.update(msg.from_user.id, row["chat_id"], size=count,
                            bot_added=True)
            ledger.register(row["username"], count)
            lines.append(f"✅ {row['username']}: {count} members/subscribers")
        except Exception as exc:
            channels.set_bot_access(row["chat_id"], False)
            lines.append(f"⚠️ {row['username']}: bot access unavailable ({type(exc).__name__})")
    lines.append("\nViews/reactions/forwards are recorded only when a real stats provider "
                 "or channel observation is available; no numbers are invented.")
    await msg.answer("\n".join(lines))


@dp.message(Command("balance"))
async def balance_cmd(msg: types.Message):
    u = _uname(msg)
    b = ledger.balance(u)
    if ledger._is_exempt(u):
        await msg.answer("👑 Owner — exempt from credits, caps and funnels.")
        return
    await msg.answer(
        f"💳 {u}: balance {b['balance']} · earned {b['earned']} · spent {b['spent']}\n"
        "You earn 1 credit each time you share another member's post, and spend "
        "1 credit per (post → channel) pair.")


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
        await cb.message.edit_text("Choose an option:", reply_markup=ui.main_menu(role, include_partnership=False))
    elif which == "cancel":
        _session_clear(uid)
        await cb.message.edit_text("❌ Cancelled. Nothing was submitted.",
                                   reply_markup=ui.main_menu(role, include_partnership=False))
    elif which == "channels":
        rows = channels.mine(uid)
        if not rows:
            text = "📂 No channels/groups registered yet.\nUse /register @name <subscriber_count>."
        else:
            text = "📂 MY CHANNELS / GROUPS\n\n" + "\n".join(
                f"• {r.get('username')} · {r.get('kind')} · band {r.get('band')} · "
                f"{('✅ bot added' if r.get('bot_added') else '⚠️ bot not added')}"
                for r in rows)
        await cb.message.edit_text(text, reply_markup=ui.main_menu(role, include_partnership=False))
    elif which == "owner":
        # A normal member could open the owner panel screen just by sending the
        # callback data — the panels themselves were guarded, but the menu wasn't.
        if role != "owner":
            await cb.answer("Owner only.", show_alert=True)
            return
        await cb.message.edit_text("🛠 Owner Panel", reply_markup=ui.role_menu("owner"))
    elif which == "admin":
        if role not in ("owner", "admin"):
            await cb.answer("Owner/admin only.", show_alert=True)
            return
        await cb.message.edit_text("🛠 Admin Panel", reply_markup=ui.role_menu(role))
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
            [InlineKeyboardButton(text="✅ Accept & continue", callback_data="terms:accept")],
            [InlineKeyboardButton(text="❌ Not now", callback_data="menu:hub")],
        ]))


@dp.callback_query(lambda c: c.data and c.data.startswith("terms:"))
async def accept_terms(cb: types.CallbackQuery):
    if cb.data == "terms:accept":
        _session_set(cb.from_user.id, accepted_terms=True)
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=t, callback_data=f"cat:{t}")] for t in POST_CATEGORIES
        ] + [[InlineKeyboardButton(text="⬅️ Back", callback_data="menu:hub")]])
        await cb.message.edit_text(
            "Select the NICHE category of your post (must match what the target "
            "channel has agreed to receive):", reply_markup=kb)
    else:
        await cb.message.edit_text("No problem. Send a command or use the menu.",
                                   reply_markup=ui.main_menu(roles.role(cb.from_user.id), include_partnership=False))
    await cb.answer()


@dp.callback_query(lambda c: c.data and c.data.startswith("cat:"))
async def pick_category(cb: types.CallbackQuery):
    cat = cb.data.split(":", 1)[1]
    if cat not in POST_CATEGORIES:
        await cb.answer("Unknown category.", show_alert=True)
        return
    _session_set(cb.from_user.id, cat=cat)
    # Pinning forwarded posts is intentionally unavailable to both owners and users.
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔁 Forward", callback_data="style:fwd"),
         InlineKeyboardButton(text="⚡ Direct", callback_data="style:direct")],
        [InlineKeyboardButton(text="🔔 Loud", callback_data="ntf:loud"),
         InlineKeyboardButton(text="🔕 Silent", callback_data="ntf:silent")],
        [InlineKeyboardButton(text="✅ Submit (you must FORWARD the post next)",
                              callback_data="style:submit")],
        [InlineKeyboardButton(text="⬅️ Back", callback_data="menu:hub"),
         InlineKeyboardButton(text="❌ Cancel", callback_data="menu:cancel")],
    ])
    await cb.message.edit_text(
        f"Category: **{cat}**\n\nChoose post style & notification:\n\n"
        "Loud delivery is for groups. Silent delivery can target either a group or a channel.",
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
    if style not in ("fwd", "direct"):
        await cb.answer("Only forward or direct delivery is available; pinning is disabled.", show_alert=True)
        return
    _session_set(cb.from_user.id, style=style)
    await cb.answer(f"Style: {style}")
    await cb.message.edit_text(f"Post style set to **{style}**. Choose notification:",
                               reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                                   [InlineKeyboardButton(text="🔔 Loud", callback_data="ntf:loud"),
                                    InlineKeyboardButton(text="🔕 Silent", callback_data="ntf:silent")],
                                   [InlineKeyboardButton(text="✅ Submit", callback_data="style:submit")],
                               ]))


@dp.callback_query(lambda c: c.data and c.data.startswith("ntf:"))
async def pick_ntf(cb: types.CallbackQuery):
    ntf = cb.data.split(":")[1]
    if ntf not in ("loud", "silent"):
        await cb.answer("Unknown option.", show_alert=True)
        return
    _session_set(cb.from_user.id, ntf=ntf)
    await cb.answer(f"Notification: {ntf}")
    await cb.message.edit_text("Now **forward** the post here to distribute it.",
                               reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                                   [InlineKeyboardButton(text="✅ Submit", callback_data="style:submit")],
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
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Back", callback_data=cb)]])


# ---------------------------------------------------------------------------
# FORWARD handler -> run the SUBMISSION GATE, then enforce the cap, then distribute
# ---------------------------------------------------------------------------
@dp.message(lambda m: is_forward(m))
async def on_forward(msg: types.Message):
    u = _uname(msg)
    uid = msg.from_user.id
    ledger.set_user_id(u, uid)                # remember id so we can DM them later

    # ===== OWNER FAST-PATH: fully exempt — no terms/category/gate/cap =====
    # The owner bypasses the entire submission funnel. Route freely, to any channel,
    # with no credit cost and no daily cap. Authorised by OWNER_USER_ID (numeric) OR
    # the reserved owner username.
    if ledger._is_exempt(u):
        await owner_forward(msg, u)
        return

    sess = _session(uid)
    # 1) terms accepted? (per user — never a single global flag)
    if not sess.get("accepted_terms"):
        await msg.answer("First accept the posting terms: /start → Submit a post.",
                         reply_markup=ui.main_menu(roles.role(uid), include_partnership=False))
        return
    # 2) category declared?
    cat = sess.get("cat")
    if not cat:
        await msg.answer("Please pick your post's niche category first (/start → Submit).")
        return
    # 3) daily cap check (size + performance) — informational here; the slots are
    #    only CONSUMED once the member picks how many channels to route to.
    m = ledger._m(u)
    s = perf.score(u)
    cap = daily_post_cap(m.get("size", 0), s["band"], m.get("status", "ACTIVE"),
                         m.get("is_owner", False))
    left = ledger.cap_left(u, cap)
    if left == 0:
        await msg.answer(f"⛔ Daily post cap reached ({cap}). "
                         "Your cap is based on size × performance and resets at "
                         "00:00 UTC.")
        return
    # 4) submission gate. Category validity + terms are checked here; the
    #    per-target category match is enforced again at matching time, because
    #    each receiving channel has its own contract.
    ok, verdict, why = gate.gate(cat, _submitted_text(msg), [])
    if not ok:
        await msg.answer(f"⛔ Refused: {why}\n\n{gate.attribution_tip()}")
        return
    if verdict == "review":
        # human review queue — a borderline pattern NEVER auto-bans anyone
        item = review.submit(cat, u, _submitted_text(msg), forward_source(msg),
                             reason=why)
        await msg.answer(f"⚠ This post hits a borderline pattern. It's queued for "
                         f"human review (#{item['id']}). You'll be notified when it's "
                         "approved.")
        return
    # 5) retain the offer in the persistent queue. Owner submissions are always
    # ordered first; claimed history is retained for audit/new-member delivery.
    queue_item = {"id": f"{msg.chat.id}:{msg.message_id}",
                  "source_chat_id": msg.chat.id, "source_message_id": msg.message_id,
                  "sender": u, "category": cat,
                  "related_categories": [c for c in POST_CATEGORIES if c != cat]}
    available.add(queue_item, owner=ledger._is_exempt(u))
    reroutes.add(queue_item)
    bubbles.set(uid, available.count(uid))
    # 6) attribution tip (advice only)
    await msg.answer(gate.attribution_tip())
    ok_spend, why_spend = ledger.can_spend(u)
    if not ok_spend:
        await msg.answer(f"Not eligible yet: {why_spend}")
        return
    # 6) ask how many (post->channel) pairs — never offer more than the member
    #    can actually afford in credits AND in remaining daily cap slots.
    balance = ledger.balance(u)["balance"]
    max_pairs = min(5, balance, left)
    if max_pairs < 1:
        await msg.answer("You have no credits left today. Share another member's "
                         "post to earn one.")
        return
    row = [InlineKeyboardButton(text=str(n), callback_data=f"s:{n}")
           for n in range(1, max_pairs + 1)]
    kb = InlineKeyboardMarkup(inline_keyboard=[row[i:i + 3] for i in range(0, len(row), 3)])
    await msg.answer(f"How many (post→channel) pairs? 1 credit each "
                     f"(balance {balance}, {left} cap slot(s) left today). "
                     "Sending ONE post to 5 channels = 5 credits.",
                     reply_markup=kb)
    _session_set(uid, pending=_pending_from(msg, u, cat, sess))


def _submitted_text(msg) -> str:
    """The text the gate should read: a forwarded photo/video carries its words
    in `caption`, not `text`. Reading only `text` let captioned scam posts walk
    straight through the gate."""
    return (getattr(msg, "text", None) or getattr(msg, "caption", None) or "")


def _pending_from(msg, u: str, cat: str, sess: dict, owner_exempt: bool = False) -> dict:
    """Snapshot of the post being distributed.

    We remember the copy the member forwarded INTO the bot chat
    (chat_id + message_id). Re-forwarding that keeps Telegram's original
    attribution header, and unlike forwarding from the source channel it works
    without the bot being a member of that channel.
    """
    return {
        "user": u, "tier": ledger.balance(u)["tier"], "post_type": cat,
        "source": forward_source(msg),
        "style": sess.get("style", "fwd"),
        "ntf": sess.get("ntf", "loud"),
        "from_chat_id": msg.chat.id,
        "from_message_id": msg.message_id,
        "owner_exempt": owner_exempt,
    }


async def owner_forward(msg: types.Message, u: str):
    """The owner's exempt path: skip terms/category/gate/cap, just pick how many
    (post->channel) pairs to route. No credits, no cap, route to any channel."""
    sess = _session(msg.from_user.id)
    _session_set(msg.from_user.id,
                 pending=_pending_from(msg, u, sess.get("cat", "General"), sess,
                                       owner_exempt=True))
    await msg.answer(
        "👑 **Owner mode** — you're exempt: no terms, no category check, no cap, "
        "no credit cost.\n\nSend the post style too if you want one: use the Submit menu "
        "to pre-pick forward/direct + loud/silent, or it defaults to **Forward+loud**.\n\n"
        "How many (post→channel) pairs to route? (Free — you're exempt.)",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="1", callback_data="s:1"), InlineKeyboardButton(text="2", callback_data="s:2")],
            [InlineKeyboardButton(text="3", callback_data="s:3"), InlineKeyboardButton(text="4", callback_data="s:4")],
            [InlineKeyboardButton(text="5", callback_data="s:5")],
            [InlineKeyboardButton(text="All matching", callback_data="s:all")],
        ]))


def owner_targets(post_type: str = "General", sender: str | None = None) -> list[str]:
    """The owner routes to EVERY active, non-blocked channel that accepts this
    category (any size/band/tier). Not a Telegram handler — a plain helper.

    `sender` is excluded: the owner's own channel used to appear in its own
    distribution list, so the owner was offered their own post and could tap
    "Agree" on it (the self-deal guard only fires at that later step). Any
    exempt/owner row is skipped for the same reason.
    """
    out = []
    for username, m in ledger.ledger.items():
        if m.get("is_owner") or ledger._is_exempt(username):
            continue
        if sender and username == sender:
            continue
        if m.get("status") in ("RESTRICTED", "REMOVED"):
            continue
        if not allows_receive(m, post_type):
            continue
        out.append(username)
    return out


# NOTE: this decorator used to sit on `owner_targets` (the helper right above),
# which meant the *helper* was registered as the s: handler and `pick_spend` was
# never wired up at all — tapping 1..5 did nothing and no post was ever routed.
@dp.callback_query(lambda c: c.data and c.data.startswith("s:"))
async def pick_spend(cb: types.CallbackQuery):
    sender = _uname(cb)
    uid = cb.from_user.id
    raw = cb.data.split(":")[1]
    is_owner = ledger._is_exempt(sender)
    sess = _session(uid)
    pending = sess.get("pending") or {}
    if not pending:
        await cb.answer("That submission expired — forward the post again.",
                        show_alert=True)
        return
    post_type = pending.get("post_type", "General")

    if raw == "all":
        # Owner only: route to every ACTIVE channel (any size/band), no limit.
        if not is_owner:
            await cb.answer("Only the owner can route to all.", show_alert=True)
            return
        targets = owner_targets(post_type, sender=sender)
    elif is_owner:
        try:
            n = int(raw)
        except ValueError:
            await cb.answer("Bad option.", show_alert=True)
            return
        targets = owner_targets(post_type, sender=sender)[:max(n, 0)]
    else:
        try:
            n = int(raw)
        except ValueError:
            await cb.answer("Bad option.", show_alert=True)
            return
        if n < 1:
            await cb.answer("Pick at least one channel.", show_alert=True)
            return
        # Find the real targets FIRST. Charging n credits and then discovering
        # only 2 matching channels exist used to burn the difference for nothing.
        targets = perf.match(sender, want_channels=n, post_type=post_type)
        if not targets:
            await cb.answer("No matching channel in your performance band accepts "
                            f"'{post_type}' right now. Nothing was charged.",
                            show_alert=True)
            return
        # The SENDER's daily cap is what limits posting — the old build charged the
        # cap to each RECEIVER instead, so the sender's cap never applied at all.
        m = ledger._m(sender)
        cap = daily_post_cap(m.get("size", 0), perf.score(sender)["band"],
                             m.get("status", "ACTIVE"), m.get("is_owner", False))
        left = ledger.cap_left(sender, cap)
        if left != -1 and left < len(targets):
            targets = targets[:left]
        # Loud notifications are meaningful only in groups. Silent delivery may
        # target either groups or channels. Apply this before charging credits.
        if pending.get("ntf", "loud") != "silent":
            targets = [t for t in targets
                       if (channels.get(t) or {}).get("kind", "group") == "group"]
        if not targets:
            await cb.answer("No eligible group destination for loud delivery.",
                            show_alert=True)
            return
        ok, why = ledger.spend(sender, len(targets))
        if not ok:
            await cb.answer(why, show_alert=True)
            return
        ok_cap, why_cap = ledger.consume_cap(sender, cap, len(targets))
        if not ok_cap:                      # cap was eaten between the two checks
            ledger.refund(sender, len(targets))  # give back what we just charged
            await cb.answer(why_cap, show_alert=True)
            return

    _session_clear(uid, "pending")          # one submission = one distribution
    await cb.message.answer(f"Distributing to {len(targets)} channel(s): {targets or 'none'}.")
    source = pending.get("source", "unknown")
    style = pending.get("style", "fwd")
    ntf = pending.get("ntf", "loud") == "silent"
    for target in targets:
        perf.mark_offered(target)
        if ledger.is_direct(target):
            await direct_deliver(cb, sender, target, pending, style=style, silent=ntf)
        else:
            audit.record(bot="reward", sender=sender, source=source,
                         post_type=post_type,
                         target_channel=target, mode="chain", status="offered",
                         forward_valid=True, style=style,
                         source_chat_id=pending.get("from_chat_id"),
                         source_message_id=pending.get("from_message_id"))
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✅ Agree (forward it)", callback_data=f"chain:agree:{sender}"),
                 InlineKeyboardButton(text="❌ Disagree (skip)", callback_data=f"chain:dis:{sender}")],
                [InlineKeyboardButton(text="🚩 Report as scam/fraud", callback_data=f"report:{sender}:{target}")],
            ])
            try:
                await bot.send_message(target,
                                       f"{sender} has a {style} post ({post_type}) "
                                       f"for your band. Agree to forward, skip, or report.",
                                       reply_markup=kb)
            except Exception as e:
                # One unreachable channel must not abort the whole distribution.
                logging.warning("offer to %s failed: %s", target, e)
                audit.record(bot="reward", sender=sender, source=source,
                             post_type=post_type, target_channel=target,
                             mode="chain", status="failed", forward_valid=True,
                             error=str(e)[:120])
    if targets:
        # Referral reward is deliberately tied to a completed distribution, not
        # merely submitting a forward or opening a referral link.
        referrals.complete_forward(uid)
    await cb.answer("done", show_alert=False)


async def direct_deliver(cb, sender, target, pending, style="fwd", silent=False):
    """Direct delivery where the bot is an administrator. forwardMessage keeps
    the original post and its inline keyboard; forwarded posts are never pinned."""
    f_chat_id = pending.get("from_chat_id", pending.get("forward_from_chat_id"))
    f_msg_id = pending.get("from_message_id", pending.get("forward_from_message_id"))
    if f_chat_id is None or f_msg_id is None:
        # FORWARD-ONLY: no genuine message to forward means nothing gets posted.
        # (The old build sent a "[direct] forwarded post from @x" placeholder —
        # an un-attributed bot message pretending to be the member's post.)
        audit.record(bot="reward", sender=sender, target_channel=target,
                     mode="direct", status="failed", forward_valid=False,
                     error="no source message to forward")
        await cb.message.answer(f"⚠ Nothing to forward to {target} — resubmit the "
                                "post as a forward.")
        return
    try:
        sent = await bot.forward_message(chat_id=target, from_chat_id=f_chat_id,
                                         message_id=f_msg_id,
                                         disable_notification=silent)
        perf.mark_posted(target)
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
    parts = cb.data.split(":", 2)
    if len(parts) < 3:
        await cb.answer("Malformed action.", show_alert=True)
        return
    _, decision, sender = parts
    target = _uname(cb)
    if target == sender:
        await cb.answer("You can't agree to your own post.", show_alert=True)
        return
    if decision == "agree":
        # The credit goes to the channel that SHARES someone else's post — i.e.
        # the target that just agreed. Crediting the sender would let anyone mint
        # credits simply by broadcasting their own posts.
        offered = next((r for r in reversed(audit.all())
                        if r.get("sender") == sender
                        and r.get("target_channel") == target
                        and r.get("status") == "offered"), None)
        if not offered:
            # Backward-compatible completion for offers created by an older
            # process before delivery rows were persisted. New UI offers always
            # have a row and follow the validated forwarding path below.
            ledger.earn(target)
            perf.mark_posted(target)
            audit.record(bot="reward", sender=sender, target_channel=target,
                         mode="chain", status="agreed", forward_valid=True,
                         legacy_offer=True)
            await cb.message.answer("Thanks! The post was marked shared. +1 credit.")
            await cb.answer()
            return
        if offered.get("source_chat_id") and offered.get("source_message_id"):
            try:
                # Telegram's forwardMessage preserves the original message's inline
                # keyboard, caption, media, and attribution. Never rebuild it as
                # plain text: that silently drops inline buttons.
                await bot.forward_message(
                    chat_id=target,
                    from_chat_id=offered["source_chat_id"],
                    message_id=offered["source_message_id"],
                    disable_notification=False)
            except Exception as exc:
                await cb.answer(f"Could not forward to {target}: {str(exc)[:80]}",
                                show_alert=True)
                return
        # Older offers created before source ids were persisted can still be
        # completed and credited; new offers always take the preservation path above.
        ledger.earn(target)
        perf.mark_posted(target)
        offered["status"] = "delivered"
        audit.store[audit.key] = audit.all()
        audit.store.sync()
        bal = ledger.balance(target)["balance"]
        await cb.message.answer("✅ Posted with the original inline buttons and attribution. "
                                f"+1 credit — balance {bal}.")
    else:
        audit.record(bot="reward", sender=sender, target_channel=target,
                     mode="chain", status="skipped", forward_valid=True)
        await cb.message.answer("Skipped. Moving to the next target.")
    try:
        await cb.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass                       # the buttons were already cleared
    await cb.answer()


@dp.callback_query(lambda c: c.data and c.data.startswith("report:"))
async def report_post(cb: types.CallbackQuery):
    parts = cb.data.split(":")
    if len(parts) < 3:
        await cb.answer("Malformed action.", show_alert=True)
        return
    _, sender, target = parts[0], parts[1], parts[2]
    reporter = _uname(cb)
    # A report NEVER bans anyone by itself: it is filed as `pending` and only a
    # human (owner/scoped admin) can confirm it and apply an action.
    item = reports.report(sender=sender, reporter=reporter,
                          reported_post={"target_channel": target},
                          reason="receiver flagged as inappropriate/scam/fraud")
    audit.record(bot="reward", sender=sender, target_channel=target,
                 mode="chain", status="skipped", forward_valid=True)
    await cb.message.answer(f"🚩 Report #{item['id']} logged against {sender}. "
                            "A human reviews every report — nothing is auto-banned.")
    owner_notify(f"🚩 New report #{item['id']}: {reporter} flagged {sender}. "
                 "Review it in the admin panel.")
    try:
        await cb.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
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
        buttons.append([InlineKeyboardButton(text=f"• {other}  [{r['status']}]", callback_data=f"partner:view:{r['id']}")])
    buttons += [
        [InlineKeyboardButton(text="➕ New partnership", callback_data="partner:new")],
        [InlineKeyboardButton(text="⬅️ Back", callback_data="menu:hub")],
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
        _session_set(cb.from_user.id, partner_phase="username")
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
            [InlineKeyboardButton(text="🔄 Renew", callback_data=f"partner:renew:{rid}"),
             InlineKeyboardButton(text="🔒 Request close", callback_data=f"partner:close:{rid}")],
            [InlineKeyboardButton(text="⬅️ Back", callback_data="menu:partners")],
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
        if not r:
            await cb.answer("That contract is closed or unknown.", show_alert=True)
            return
        if _uname(cb) not in (r.get("a"), r.get("b")):
            await cb.answer("Only the two partners can renew this contract.",
                            show_alert=True)
            return
        owner_notify(f"🔄 Partnership RENEWED {r['id']}: {r['a']} <-> {r['b']}")
        await cb.answer("Partnership renewed. Owner notified.")
        await cb.message.edit_text("✅ Renewed. Owner notified.", reply_markup=ui.role_menu(roles.role(cb.from_user.id)))
        return
    if act == "close":
        r = contracts.get(parts[2])
        if not r:
            await cb.answer("not found", show_alert=True)
            return
        if _uname(cb) not in (r.get("a"), r.get("b")):
            await cb.answer("Only the two partners can close this contract.",
                            show_alert=True)
            return
        r = contracts.request_close(parts[2], by=_uname(cb), reason="requested by partner")
        if not r:
            await cb.answer("That contract is already closed.", show_alert=True)
            return
        owner_notify(f"🔒 CLOSE REQUESTED {r['id']}: {r['a']} <-> {r['b']} by {_uname(cb)}\n"
                     "You give the final close (/partner_close).")
        await cb.answer("Close requested. Owner notified; you'll get the final close.")
        await cb.message.edit_text("🔒 Close requested. Awaiting owner's final close.",
                                   reply_markup=ui.role_menu(roles.role(cb.from_user.id)))
        return


# This catch-all is registered BEFORE /rank, /audit, /reports and /schedule.
# aiogram stops at the first matching handler and treats a plain `return` as
# "handled", so as a bare `@dp.message()` it silently swallowed every command
# defined below it — those four commands never ran. It now (a) never matches a
# command and (b) returns UNHANDLED when it has nothing to capture, so the
# update keeps flowing to the handlers underneath.
@dp.message(lambda m: not (m.text or "").startswith("/"))
async def partner_username_capture(msg: types.Message):
    """Capture the partner @username typed by a user during 'New partnership'."""
    uid = msg.from_user.id
    sess = _session(uid)
    if sess.get("partner_phase") != "username":
        return UNHANDLED       # not in this flow -> let other handlers try
    u = _uname(msg)
    other = (msg.text or "").strip()
    if not other:
        await msg.answer("Send the partner channel's @username.")
        return
    if not other.startswith("@"):
        other = "@" + other
    if other.lower() == u.lower():
        await msg.answer("You can't partner with yourself.")
        return
    # Both must be registered in the ledger.
    if other not in ledger.ledger:
        await msg.answer(f"{other} is not registered with CLICKMINT yet.")
        _session_clear(uid, "partner_phase")
        return
    existing = [r for r in contracts.between(u, other)
                if r.get("status") in ("ACTIVE", "RENEWED", "CLOSE_REQUESTED")]
    if existing:
        await msg.answer(f"You already have an open contract with {other} "
                         f"(#{existing[0]['id']}).")
        _session_clear(uid, "partner_phase")
        return
    m = ledger._m(u)
    other_m = ledger._m(other)
    if not (m.get("direct_mode") and other_m.get("direct_mode")):
        await msg.answer("Both channels must have added the bot as admin (Post Messages) "
                         "so you can post into each other. Fix that on both sides, then retry.")
        _session_clear(uid, "partner_phase")
        return
    fields = contracts.self_contract_fields()
    rec = contracts.open(u, other, {"note": f"opened by {u}",
                                    "types": sess.get("cat", "General")})
    owner_notify(f"🤝 NEW PARTNERSHIP OPENED {rec['id']}: {u} <-> {other}")
    await msg.answer(
        f"🤝 Partnership opened between {u} and {other} (#{rec['id']}).\n\n"
        "Agree these fields to keep it healthy:\n" + fields +
        "\nOwner was notified and is the mediator. Both sides must honour the terms "
        "(niche-only, no-ads, no-spam, safety). Either can request close if it stops "
        "benefiting.")
    _session_clear(uid, "partner_phase")


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
    # Every panel except the public terms text is owner/admin only. (The old
    # build only guarded three of them; audit / reports / rank / direct /
    # scheduled were unguarded — and, worse, unimplemented: those buttons in
    # ui.role_menu fell through and did nothing at all.)
    if which != "terms":
        if not _can_panel(uid, "reward") and not _can_panel(uid, "partnership"):
            await cb.answer("Owner/admin only.", show_alert=True)
            return
    if which == "audit":
        log = audit.last(12)
        lines = [audit.summary(), ""]
        for r in reversed(log):
            lines.append(f"[{r.get('ts')}] {r.get('sender')} -> {r.get('target_channel')} "
                         f"| {r.get('status')} | {r.get('post_type')}")
        await _panel_edit(cb, "\n".join(lines[:45]) or "No deliveries yet.",
                          ui.role_menu(roles.role(uid)))
        await cb.answer()
        return
    if which == "reports":
        await show_reports_panel(cb)
        return
    if which == "rank":
        await _panel_edit(cb, _rank_text(), ui.role_menu(roles.role(uid)))
        await cb.answer()
        return
    if which == "direct":
        rows = [f"{u:<22} {'direct ✔' if m.get('direct_mode') else 'chain —'}"
                for u, m in ledger.ledger.items()]
        await _panel_edit(cb, "⚡ DELIVERY MODE PER CHANNEL\n\n" +
                          ("\n".join(rows[:40]) or "No channels registered yet."),
                          ui.role_menu(roles.role(uid)))
        await cb.answer()
        return
    if which == "scheduled":
        await _panel_edit(cb, sched.upcoming_text(), ui.role_menu(roles.role(uid)))
        await cb.answer()
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
            kb.append([InlineKeyboardButton(text=f"✅ Approve #{it['id']}", callback_data=f"review:approve:{it['id']}"),
                       InlineKeyboardButton(text=f"❌ Reject #{it['id']}", callback_data=f"review:reject:{it['id']}")])
        kb.append([InlineKeyboardButton(text="⬅️ Back", callback_data="menu:admin")])
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
                [InlineKeyboardButton(text="🔑 Generate invite (reward)", callback_data="admin:invite:reward"),
                 InlineKeyboardButton(text="🔑 Generate invite (partnership)", callback_data="admin:invite:partnership")],
                [InlineKeyboardButton(text="🔑 Generate invite (both)", callback_data="admin:invite:both")],
                [InlineKeyboardButton(text="⬅️ Back", callback_data="menu:owner")],
            ]))
        await cb.answer()
        return
    if which == "terms":
        await cb.message.edit_text(TERMS_TEXT, reply_markup=back_btn_q("menu:owner"))
        await cb.answer()
        return
    await cb.answer()


async def _panel_edit(cb, text, kb):
    """Render a panel, tolerating Telegram's "message is not modified"."""
    try:
        await cb.message.edit_text(text, reply_markup=kb)
    except Exception as e:
        if "not modified" not in str(e).lower():
            logging.warning("panel render failed: %s", e)


def _rank_text() -> str:
    lines = ["🏆 PERFORMANCE RANK (band A/B/C — performance, not size):", ""]
    for username, m in ledger.ledger.items():
        if m.get("is_owner"):
            continue
        s = perf.score(username)
        cap = daily_post_cap(m.get("size", 0), s["band"], m.get("status", "ACTIVE"))
        lines.append(f"{username:<22} {s['band']}  {s['score']:.2f}  {s['status']}  "
                     f"direct={'✔' if ledger.is_direct(username) else '—'}  "
                     f"cap={cap}  ({m.get('size', 0)})")
    if len(lines) == 2:
        lines.append("No channels registered yet.")
    return "\n".join(lines[:50])


async def show_reports_panel(cb: types.CallbackQuery):
    """Pending reports + the HUMAN actions. Nothing here is automatic: a report
    only affects a channel when a person taps one of these buttons."""
    uid = cb.from_user.id
    pending = reports.pending()
    if not pending:
        await _panel_edit(cb, "✅ No pending reports.", ui.role_menu(roles.role(uid)))
        await cb.answer()
        return
    lines = [f"🚩 {len(pending)} pending report(s) — a human decides each one:", ""]
    kb = []
    for r in pending[:5]:
        lines.append(f"#{r['id']} {r['sender']} — reported by {r['reporter']}\n"
                     f"   {r.get('reason', '')[:70]}")
        kb.append([InlineKeyboardButton(text=f"👍 Dismiss #{r['id']}",
                                        callback_data=f"rep:clear:{r['id']}"),
                   InlineKeyboardButton(text=f"⚠️ Warn #{r['id']}",
                                        callback_data=f"rep:warn:{r['id']}")])
        kb.append([InlineKeyboardButton(text=f"⛔ Restrict #{r['id']}",
                                        callback_data=f"rep:restrict:{r['id']}"),
                   InlineKeyboardButton(text=f"🚫 Remove #{r['id']}",
                                        callback_data=f"rep:remove:{r['id']}")])
    kb.append([InlineKeyboardButton(text="⬅️ Back", callback_data="menu:hub")])
    await _panel_edit(cb, "\n".join(lines[:40]),
                      InlineKeyboardMarkup(inline_keyboard=kb))
    await cb.answer()


@dp.callback_query(lambda c: c.data and c.data.startswith("rep:"))
async def decide_report(cb: types.CallbackQuery):
    """The human step behind a report. Until this runs, a reported channel keeps
    its status — one report (or one keyword) never bans anybody."""
    uid = cb.from_user.id
    if not _can_panel(uid, "reward"):
        await cb.answer("Owner/admin only.", show_alert=True)
        return
    parts = cb.data.split(":")
    if len(parts) < 3:
        await cb.answer("Malformed action.", show_alert=True)
        return
    action, rid = parts[1], parts[2]
    try:
        rid = int(rid)
    except ValueError:
        await cb.answer("Bad report id.", show_alert=True)
        return
    try:
        if action == "clear":
            item = reports.clear(rid)
            await cb.answer(f"#{rid} dismissed — no penalty.")
        else:
            item = reports.confirm(rid, action=action, note=f"by {_uname(cb)}")
            if action == "restrict":
                perf.set_status(item["sender"], "RESTRICTED")
            elif action == "remove":
                perf.set_status(item["sender"], "REMOVED")
            elif action == "warn":
                perf.set_status(item["sender"], "WATCH")
            await cb.answer(f"#{rid} confirmed: {action}.")
    except KeyError:
        await cb.answer("That report no longer exists.", show_alert=True)
        return
    owner_notify(f"🚩 Report #{rid} on {item['sender']}: {action} by {_uname(cb)}.")
    await show_reports_panel(cb)


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
            # Unclaimed offers get one related-category opportunity after 12h.
            for item in reroutes.due():
                related = item.get("related_categories", [])
                if related:
                    copy = dict(item)
                    copy["id"] = f"{item.get('id')}:reroute"
                    copy["category"] = related[0]
                    available.add(copy, owner=bool(item.get("owner")))
                reroutes.mark_rerouted(item.get("id"))
        except Exception as e:
            logging.warning("notify_loop error: %s", e)
        await asyncio.sleep(15)


# ---------------------------------------------------------------------------
# Legacy command fallbacks (still work for power users)
# ---------------------------------------------------------------------------
# NOTE: there used to be an `/agree` command here that called `ledger.earn()` for
# whoever typed it — i.e. a self-service credit printer that let any member mint
# unlimited credits and bypass the earn-first rule. Agreeing is only meaningful
# for a real offer, so it now lives exclusively on the offer's inline buttons
# (`chain:agree:<sender>`), where the credit is granted to the channel that
# actually shares someone else's post.


@dp.message(Command("rankpublic"))
async def rank_public_cmd(msg: types.Message):
    if not roles.is_owner(msg.from_user.id):
        await msg.answer("🔒 Owner only."); return
    parts = (msg.text or "").split()
    if len(parts) != 2 or parts[1].lower() not in ("on", "off"):
        await msg.answer("Usage: /rankpublic on|off (default is off)"); return
    value = ranks.set_public(msg.from_user.id, parts[1].lower() == "on", OWNER_USER_ID)
    await msg.answer("✅ Public rank is now " + ("ON" if value else "OFF") + ".")


@dp.message(Command("rank"))
async def rank_view(msg: types.Message):
    if not _can_panel(msg.from_user.id, "reward") and not ranks.public():
        await msg.answer("Owner/admin only — rank display is currently private."); return
    await msg.answer(_rank_text())


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
        # The prompt promises UTC. strptime().timestamp() interpreted the time in
        # the SERVER's local zone, so a box on anything but UTC posted at the
        # wrong hour — the one thing a scheduled partner slot must get right.
        at = dt.datetime.strptime(f"{day_str} {hm}", "%Y-%m-%d %H:%M").replace(
            tzinfo=dt.timezone.utc).timestamp()
    except ValueError:
        await msg.answer("Bad time. Use YYYY-MM-DD HH:MM in UTC.")
        return
    if at < time.time():
        await msg.answer("That time is in the past (times are UTC).")
        return
    destination = channels.get(target)
    if not destination or not destination.get("bot_added"):
        await msg.answer("⛔ Scheduling requires the bot to be added to that channel/group "
                         "so Telegram can publish automatically.")
        return
    pending = _session(msg.from_user.id).get("pending") or {}
    if not pending.get("from_message_id"):
        await msg.answer("Forward the post to me first, then schedule it — "
                         "otherwise there's nothing to deliver.")
        return
    rec = sched.schedule(at=at, target=target, sender=pending.get("user", _uname(msg)),
                         from_chat_id=pending.get("from_chat_id"),
                         from_message_id=pending.get("from_message_id"),
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
                # Forward-only: without the original message there is nothing
                # genuine to deliver, so we do NOT post a fabricated stand-in.
                sched.mark_done(rec["id"], note="no source message — skipped")
                audit.record(bot="reward", sender=rec["sender"], target_channel=rec["target"],
                             mode="direct", status="failed", forward_valid=False,
                             error="no source message to forward")
                continue
            perf.mark_posted(rec["target"])
            sched.mark_done(rec["id"])
            audit.record(bot="reward", sender=rec["sender"], target_channel=rec["target"],
                         mode="direct", status="delivered", forward_valid=True)
        except Exception as e:
            # Retry a transient failure instead of dropping an agreed slot.
            gave_up = sched.fail(rec["id"], note=str(e)[:120])
            if gave_up:
                audit.record(bot="reward", sender=rec["sender"], target_channel=rec["target"],
                             mode="direct", status="failed", forward_valid=True,
                             error=str(e)[:120])


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
