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
import csv
import hashlib
import io
import json
import logging
import os
import time
import datetime as dt
from html import escape
from aiogram import Bot, Dispatcher, types
from aiogram.dispatcher.event.bases import UNHANDLED
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo

from core import (CreditLedger, TransactionalCreditLedger, DeliveryLog, ReportRegistry,
                  PerformanceEngine, is_forward, forward_source, allows_receive)
from channel_registry import ChannelRegistry
from verification_store import VerificationStore
from onboarding_api import redact
from features import (ReferralLedger, AvailablePostQueue, BubbleNotifier,
                      AnnouncementBoard, RankVisibility, StatsBook, ReroutePlanner,
                      category_counts)
from task_marketplace import TaskMarketplace, TaskUnavailable, TaskNotFound
from channel_connection import TelegramPermissionSnapshot, verify_permissions
from telegram_verification import BotCredentialStore, TelegramVerificationService, CredentialError
import relay
from credibility import tier_for_score
from performance_snapshots import PerformanceSnapshotRepository
from referral_ranking import ReferralRecord, rank_referrers, allocate_monthly_rewards
from broadcast_queue import BroadcastQueue
from enforcement import EnforcementStore, EnforcementError
from enforcement_gate import EnforcementGate
from ad_campaigns import AdCampaignStore, AdPolicyError
from destination_state import (DestinationStateMachine, VState, IllegalTransition, classify_telegram_error,
                               apply_verification_result, failed_result_from_reason)
from currency import MINT_ICON, MINT_NAME, amount, amount_short, balance_line
from store import JsonStore
from scheduler import Scheduler
from governance import (SubmissionGate, ReviewQueue, PartnerContractRegistry,
                        RoleRegistry, POST_CATEGORIES, TERMS_TEXT, daily_post_cap)
import config
import tg_entities
import ui
from platform_store import DeliveryAudit, SharedKV
from delivery_worker import WorkerStore, admit_delivery
from user_directory import UserDirectory
from audience import AudienceDirectory
from services.broadcast_service import BroadcastService

logging.basicConfig(level=logging.INFO)
BOT_TOKEN = config.REWARD_BOT_TOKEN          # from @BotFather, via env var
OWNER_USER_ID = config.OWNER_USER_ID         # your numeric Telegram user id (owner)
_HEALTH_ALERT_LAST = 0
_HEALTH_ALERTED = set()

store = JsonStore(config.REWARD_STORE_PATH)
if config.MINT_LEDGER_MODE == "transactional":
    ledger = TransactionalCreditLedger(store, config.MINT_DB_PATH)
else:
    ledger = CreditLedger(store)
ledger.owner_user_id = config.OWNER_USER_ID      # owner recognised by numeric id too
audit = DeliveryAudit(config.PLATFORM_DB_PATH, legacy=store)     # shared SQLite, JSON migrated once
users = UserDirectory(ledger.tx) if hasattr(ledger, "tx") else None
if users is not None:
    users.migrate_legacy_members(store.get("ledger", {}))
reports = ReportRegistry(store)
perf = PerformanceEngine(ledger, store, views_provider=None)
sched = Scheduler(store)
# Destinations + bot credentials live in the SQLite verification DB shared with
# the other bot; every other key still goes to this bot's JsonStore.
verification_store = VerificationStore(config.VERIFICATION_DB_PATH, legacy=store)
channels = ChannelRegistry(verification_store)
destination_states = DestinationStateMachine(channels)   # the ONLY writer of verified_state/status
bot_credentials = BotCredentialStore(verification_store)
telegram_verification = TelegramVerificationService(bot_credentials)
referrals = ReferralLedger(store, ledger)
available = AvailablePostQueue(store)
bubbles = BubbleNotifier(store)
announcements = AnnouncementBoard(store)
ranks = RankVisibility(store)
stats = StatsBook(store)
reroutes = ReroutePlanner(store)
# New marketplace state is additive and isolated from the legacy available-post
# queue until task creation/execution migration is complete.
marketplace = TaskMarketplace(config.TASK_DB_PATH)
credibility_snapshots = PerformanceSnapshotRepository(config.CREDIBILITY_DB_PATH)
broadcasts = BroadcastQueue(config.BROADCAST_DB_PATH)
broadcast_service = BroadcastService(broadcasts, AudienceDirectory(authoritative=users)) if users is not None else None
worker_store = WorkerStore(config.WORKER_DB_PATH)
# Shared compliance boundary: every participation path asks this ONE gate.
# It only reads states a human recorded; it never decides guilt.
enforcement = EnforcementStore(config.ENFORCEMENT_DB_PATH)
enforcement_gate = EnforcementGate(enforcement, owner_user_id=config.OWNER_USER_ID)
# Advertising is a separate, consent-gated model. Disabled by default.
ads = AdCampaignStore(config.ADS_DB_PATH, enabled=config.ADS_ENABLED)
# PerformanceEngine consumes real observations when available; absent fields stay absent.
perf.views_provider = stats.provider


def _menu(role: str, uid: int | None = None):
    """Reward-bot hub with the live available-post counter."""
    return ui.main_menu(role, include_partnership=False,
                        available_count=available.count(uid) if uid is not None else 0)


def _is_connected(username: str) -> bool:
    """Fail-closed destination gate used by every participation path."""
    return channels.participation_allowed(username)


def _enforcement_block(uid: int, capability: str, *destinations) -> str | None:
    """Return a user-facing block message, or None when the actor may proceed.

    Checks the acting user AND every destination they act through, so a
    restricted channel cannot be used by an unrestricted owner (or vice versa).
    """
    subjects = [(uid, "user")] + [(d, "channel") for d in destinations if d]
    decision = enforcement_gate.check_many(subjects, capability, is_owner=roles.is_owner(uid))
    if decision:
        return None
    return enforcement_gate.block_message(decision)


def _audit_counts() -> dict[str, int]:
    return {
        "Review Queue": len(review.pending()),
        "Pending Posts": available.count(),
        "Scheduled Posts": sched.pending_count(),
        "Auto-post destinations": sum(1 for r in channels._items().values() if r.get("auto_post")),
    }


gate = SubmissionGate(store)
review = ReviewQueue(store)
contracts = PartnerContractRegistry(store)
platform_kv = SharedKV(config.PLATFORM_DB_PATH, legacy=store)
roles = RoleRegistry(platform_kv, owner_user_id=OWNER_USER_ID)   # one role table for every bot

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
    """Parse a destination only; Telegram is the source of truth for size."""
    parts = (text or "").split()
    if len(parts) != 2:
        return None, "Usage: /register @yourchannel\nSubscriber/member counts are retrieved from Telegram automatically."
    username = parts[1]
    if not username.startswith("@"):
        username = "@" + username
    if len(username) < 2 or " " in username:
        return None, "That Telegram destination doesn't look right."
    return username, None


async def _register_from_telegram(msg, destination: str, kind: str = "channel") -> str:
    """Resolve, verify, and register a destination with the user's own bot."""
    owner = msg.from_user.id
    blocked = _enforcement_block(owner, "registration", destination)
    if blocked:
        return blocked
    channels.add(owner, destination, destination, kind, ["General"], size=0, bot_added=False)
    row = channels.get(destination)
    destination_states.transition(owner, destination, VState.VERIFYING, reason="registration requested", source="register")
    result = await telegram_verification.verify(owner, destination)
    ok, _ = _apply_verification(channels.get(destination), result, source="register")
    if not ok:
        return (f"❌ <b>Verification failed for {escape(destination)}</b>\n\n" +
                "\n".join(f"• {escape(reason)}" for reason in result.reasons) +
                "\n\nAdd your bot as an administrator with posting permission, then use /scan.")
    size = result.member_count or 0
    m = ledger.register(destination, size)
    channels.update(owner, destination, telegram_bot_id=bot_credentials.public(owner)["bot_id"])
    ledger.set_user_id(destination, owner)
    cap = daily_post_cap(size, perf.score(destination)["band"], m.get("status", "ACTIVE"),
                         m.get("is_owner", False), connected=True)
    return (f"✅ <b>DESTINATION VERIFIED</b> — {escape(destination)}\n\n"
            f"🤖 Bot: @{escape(str(bot_credentials.public(owner).get('username') or 'connected bot'))}\n"
            f"👥 Members/subscribers: {size:,} (Telegram API)\n"
            f"🔐 Administrator and required permissions: verified\n"
            f"📤 Daily delivery limit: {'UNLIMITED' if cap == -1 else cap}")


# ---------------------------------------------------------------------------
# User-owned bot connection and destination registration
# ---------------------------------------------------------------------------
def _connect_prompt():
    """Prefer the HTTPS Mini App for the token; fall back to in-chat only when
    no onboarding URL is configured."""
    if config.ONBOARDING_WEBAPP_URL:
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
            text="🔐 Connect bot securely", web_app=WebAppInfo(url=config.ONBOARDING_WEBAPP_URL))]])
        return ("🔐 <b>Connect your bot</b>\n\nTap the button to paste your BotFather token on a secure "
                "page. It travels over HTTPS only, is encrypted at rest, and never appears in this chat.",), \
               {"parse_mode": "HTML", "reply_markup": kb}
    return ("Usage: /connectbot <token from BotFather>\nYour token is encrypted and never shown back.",), {}


@dp.message(Command("connectbot"))
async def connect_bot_cmd(msg: types.Message):
    if getattr(msg.chat, "type", "private") != "private":
        await msg.answer("🔒 For security, connect your bot only in a private chat with ClickMint.")
        return
    parts = (msg.text or "").split(maxsplit=1)
    if len(parts) != 2:
        args, kwargs = _connect_prompt()
        await msg.answer(*args, **kwargs)
        return
    if config.ONBOARDING_WEBAPP_URL:
        # A token was pasted into chat although the secure page exists: still
        # honour it (deleting the message below) but tell the member to rotate.
        pasted_in_chat = True
    else:
        pasted_in_chat = False
    previous = bot_credentials.public(msg.from_user.id)
    try:
        # Remove the token message immediately where Telegram permits it; tokens
        # must not remain visible in the conversation history.
        try:
            await msg.delete()
        except Exception:
            logging.warning("could not delete bot-token message")
        identity = await telegram_verification.connect_bot(msg.from_user.id, parts[1].strip())
    except Exception as exc:
        await msg.answer(f"❌ Bot connection failed: {escape(redact(str(exc))[:200])}")
        return
    if previous and int(previous.get("bot_id", -1)) != int(identity.get("bot_id", -2)):
        for row in channels.mine(msg.from_user.id):
            try:
                destination_states.transition(msg.from_user.id, row.get("chat_id") or row.get("username"),
                                              VState.DISCONNECTED, reason="connected bot changed; re-verify",
                                              source="owner",
                                              verification_reasons=["Connected bot changed; destination must be re-verified"])
            except IllegalTransition:
                pass
    await msg.answer(f"✅ Connected @{escape(str(identity.get('username') or 'your bot'))}.\n"
                     "Now add it as an administrator with posting permission, then use /register @destination."
                     + ("\n\n⚠️ You pasted the token into chat. Next time use the 🔐 secure page (/connectbot with no "
                        "arguments); consider /revoke in @BotFather and reconnecting." if pasted_in_chat else ""))


@dp.message(Command("botstatus"))
async def bot_status_cmd(msg: types.Message):
    identity = bot_credentials.public(msg.from_user.id)
    if not identity:
        await msg.answer("❌ No Telegram bot connected. Use /connectbot in this private chat.")
        return
    destinations = channels.mine(msg.from_user.id)
    verified = sum(1 for row in destinations if row.get("verified_state") == "VERIFIED"
                   and row.get("bot_added") is True)
    await msg.answer(
        f"🤖 <b>CONNECTED TELEGRAM BOT</b>\n\n"
        f"Username: @{escape(str(identity.get('username') or 'unknown'))}\n"
        f"Bot ID: <code>{identity.get('bot_id')}</code>\n"
        f"Verified destinations: {verified}/{len(destinations)}\n\n"
        "Use /scan to refresh administrator permissions and Telegram statistics.",
        parse_mode="HTML")


@dp.callback_query(lambda c: c.data == "bot:status")
async def bot_status_callback(cb: types.CallbackQuery):
    identity = bot_credentials.public(cb.from_user.id)
    if not identity:
        text = "❌ <b>No Telegram bot connected</b>\n\nUse /connectbot in this private chat."
    else:
        destinations = channels.mine(cb.from_user.id)
        verified = sum(1 for row in destinations if row.get("verified_state") == "VERIFIED"
                       and row.get("bot_added") is True)
        text = ("🤖 <b>CONNECTED TELEGRAM BOT</b>\n\n"
                f"Username: @{escape(str(identity.get('username') or 'unknown'))}\n"
                f"Bot ID: <code>{identity.get('bot_id')}</code>\n"
                f"Verified destinations: {verified}/{len(destinations)}")
    await cb.message.edit_text(text, parse_mode="HTML",
                               reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                                   [InlineKeyboardButton(text="🔄 Refresh verification", callback_data="stats:all")],
                                   [InlineKeyboardButton(text="⬅️ Back", callback_data="menu:hub")]]))
    await cb.answer()


@dp.message(Command("disconnectbot"))
async def disconnect_bot_cmd(msg: types.Message):
    bot_credentials.remove(msg.from_user.id)
    # Without a credential nothing can be verified or posted: every destination
    # owned by this user drops to DISCONNECTED (previously they stayed VERIFIED).
    for row in channels.mine(msg.from_user.id):
        try:
            destination_states.transition(msg.from_user.id, row.get("chat_id") or row.get("username"),
                                          VState.DISCONNECTED, reason="Telegram bot credential removed",
                                          source="owner", verification_reasons=["Telegram bot credential removed"])
        except IllegalTransition:
            pass
    await msg.answer("✅ Your Telegram bot credential and destination access were removed from ClickMint.")


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
    if len((msg.text or "").split()) >= 2:
        parsed, err = _parse_registration(msg.text)
        if err:
            await msg.answer(err, reply_markup=_menu(role, uid))
            return
        try:
            text = await _register_from_telegram(msg, parsed)
        except CredentialError:
            text = "❌ Connect your own Telegram bot first with /connectbot <token from BotFather>."
        await msg.answer(text, parse_mode="HTML", reply_markup=_menu(role, uid))
        return
    if role == "owner":
        await msg.answer(
            "🛠 Welcome, Owner. Your menu controls everyone — users, admins, "
            "contracts, and the reward/partnership networks.",
            reply_markup=_menu("owner", msg.from_user.id))
    elif role == "admin":
        await msg.answer("🛠 Admin menu. Use it to moderate the network you're scoped to.",
                         reply_markup=_menu("admin", msg.from_user.id))
    else:
        head = ("Welcome to CLICKMINT.\n"
                "To use the network, register your channel first:\n"
                "First connect your bot with /connectbot, then use /register @yourchannel")
        await msg.answer(head,
                         reply_markup=_menu("user", msg.from_user.id))


@dp.message(Command("register"))
async def register_cmd(msg: types.Message):
    parsed, err = _parse_registration(msg.text)
    if err:
        await msg.answer(err)
        return
    try:
        text = await _register_from_telegram(msg, parsed)
    except CredentialError:
        text = "❌ Connect your own Telegram bot first with /connectbot <token from BotFather>."
    await msg.answer(text, parse_mode="HTML",
                     reply_markup=_menu(roles.role(_uid(msg)), _uid(msg)))


@dp.message(Command("removechannel"))
async def remove_channel_cmd(msg: types.Message):
    parts = (msg.text or "").split()
    if len(parts) != 2:
        await msg.answer("Usage: /removechannel @channel_or_group")
        return
    target = parts[1] if parts[1].startswith("@") else "@" + parts[1]
    row = next((r for r in channels.mine(msg.from_user.id)
                if r.get("username") == target or str(r.get("chat_id")) == target), None)
    if not row:
        await msg.answer("That channel/group is not registered under your account.")
        return
    try:
        destination_states.transition(msg.from_user.id, row["chat_id"], VState.REMOVED, reason="owner removed", source="owner")
    except IllegalTransition:
        pass
    channels.remove(msg.from_user.id, row["chat_id"])
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
    try:
        text = await _register_from_telegram(msg, parsed, kind="group")
    except CredentialError:
        text = "❌ Connect your own Telegram bot first with /connectbot <token from BotFather>."
    await msg.answer(text, parse_mode="HTML",
                     reply_markup=_menu(roles.role(_uid(msg)), _uid(msg)))


@dp.message(Command("mychannels"))
async def mychannels_cmd(msg: types.Message):
    """Every channel/group owned by this user, each with inline controls."""
    await _send_mychannels(msg, msg.from_user.id, edit=False)


def _auto_post_enabled(target) -> bool:
    """Direct delivery is a destination capability: the owner turned Auto-post on
    AND a legal relay route still exists right now (rights can be lost later)."""
    row = channels.get(target)
    if not row or not row.get("auto_post") or not channels.participation_allowed(target):
        return False
    return _auto_post_explainer(row) is None


def _auto_post_explainer(row: dict) -> str | None:
    return relay.auto_post_blocker(row, platform_bot_id=_platform_bot_id(),
                                   owner_rows=channels.mine(int(row["owner_id"])))


def _dest_label(row: dict) -> str:
    icon = {"ACTIVE": "✅", "DEGRADED": "⚠️", "VERIFYING": "⏳", "REGISTERED": "🆕"}.get(row.get("status"), "⛔")
    return f"{icon} {row.get('username') or row.get('chat_id')} · {row.get('kind')} · {row.get('status')}"


def _mychannels_view(uid: int):
    rows = channels.mine(uid)
    if not rows:
        return ("📂 You have no registered channels or groups yet.\n"
                "Use /connectbot, then /register @name to add one.",), {"reply_markup": _menu("user", uid)}
    lines = ["📂 MY CHANNELS / GROUPS", ""]
    kb = []
    for i, row in enumerate(rows):
        lines.append(f"• {_dest_label(row)} · tier {row.get('band')}")
        if row.get("status") != "ACTIVE" and row.get("verification_reasons"):
            lines.append(f"    ↳ {row['verification_reasons'][0][:90]}")
        key = row.get("chat_id") or row.get("username")
        auto = "⚡ Auto-post ON" if row.get("auto_post") else "⚡ Auto-post off"
        kb.append([InlineKeyboardButton(text=f"🔄 Re-verify {row.get('username') or key}"[:60], callback_data=f"dest:verify:{i}"),
                   InlineKeyboardButton(text="ℹ️", callback_data=f"dest:info:{i}"),
                   InlineKeyboardButton(text="🗑", callback_data=f"dest:remove:{i}")])
        kb.append([InlineKeyboardButton(text=auto, callback_data=f"dest:auto:{i}")])
    lines += ["", "Re-verify runs a live Telegram permission check with YOUR bot.",
              "⚡ Auto-post lets other members' forwards land here automatically (you choose per destination)."]
    return ("\n".join(lines),), {"reply_markup": InlineKeyboardMarkup(inline_keyboard=kb)}


async def _send_mychannels(msg_or_cb, uid: int, *, edit: bool):
    args, kwargs = _mychannels_view(uid)
    target = msg_or_cb.message if edit else msg_or_cb
    try:
        if edit:
            await target.edit_text(*args, **kwargs)
        else:
            await target.answer(*args, **kwargs)
    except Exception as exc:
        if edit and "not modified" not in str(exc).lower():
            await msg_or_cb.message.answer(*args, **kwargs)


@dp.callback_query(lambda c: c.data and c.data.startswith("dest:"))
async def destination_control(cb: types.CallbackQuery):
    """Inline verification controls. Every action is scoped to the caller's own
    rows by index-at-render-time, then re-resolved by key to avoid stale taps."""
    parts = cb.data.split(":")
    if len(parts) != 3:
        await cb.answer("Malformed action.", show_alert=True); return
    action, idx = parts[1], parts[2]
    uid = cb.from_user.id
    rows = channels.mine(uid)
    try:
        row = rows[int(idx)]
    except (ValueError, IndexError):
        await cb.answer("That list is out of date — reopening.", show_alert=True)
        await _send_mychannels(cb, uid, edit=True); return
    key = row.get("chat_id") or row.get("username")
    label = row.get("username") or key
    if action == "info":
        checks = row.get("verification_checks") or {}
        icon = lambda name: "✅" if checks.get(name) == "passed" else ("⚠️" if checks.get(name) == "unavailable" else "❌")
        hist = (row.get("state_history") or [])[-5:]
        text = [f"ℹ️ <b>{escape(str(label))}</b>", f"State: {row.get('verified_state')} · status {row.get('status')}",
                f"Members: {row.get('telegram_member_count', row.get('size', 0)):,} ({row.get('telegram_member_count_source', 'unknown')})",
                f"Last verified: {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime(int(row.get('last_verified_at') or 0))) if row.get('last_verified_at') else 'never'}",
                f"Last error: {row.get('last_error_kind') or 'none'}", "",
                f"{icon('destination')} destination  {icon('bot_membership')} membership  {icon('administrator')} admin  {icon('permissions')} rights",
                "", "Recent transitions:"] + [f"• {h['previous']} → {h['new']} ({h['source']}): {h['reason'][:60]}" for h in hist]
        await cb.message.answer("\n".join(text), parse_mode="HTML")
        await cb.answer(); return
    if action == "auto":
        if row.get("auto_post"):
            channels.update(uid, key, auto_post=False)
            await cb.answer("Auto-post off: you will get offers to accept instead.")
        else:
            why = _auto_post_explainer(row)
            if why:
                await cb.answer(f"Can't enable Auto-post: {why}"[:200], show_alert=True); return
            channels.update(uid, key, auto_post=True)
            await cb.answer("Auto-post on for this destination.")
        await _send_mychannels(cb, uid, edit=True); return
    if action == "remove":
        try:
            destination_states.transition(uid, key, VState.REMOVED, reason="owner removed via /mychannels", source="owner")
        except IllegalTransition:
            pass
        channels.remove(uid, key)
        await cb.answer(f"Removed {label}.")
        await _send_mychannels(cb, uid, edit=True); return
    if action == "verify":
        blocked = _enforcement_block(uid, "registration", key)
        if blocked:
            await cb.answer(blocked[:200], show_alert=True); return
        try:
            destination_states.transition(uid, key, VState.VERIFYING, reason="owner requested re-verify", source="scan")
        except IllegalTransition as exc:
            await cb.answer(str(exc)[:200], show_alert=True); return
        try:
            result = await telegram_verification.verify(uid, channels.telegram_reference(row))
        except CredentialError:
            result = _failed_result(row, "connect your bot with /connectbot")
        except Exception as exc:
            info = classify_telegram_error(exc)
            if info.kind.value == "unknown":
                logging.exception("re-verify of %s raised", key)
            result = _failed_result(row, f"Telegram verification failed: {info.kind.value}")
        ok, why = _apply_verification(channels.get(key), result, source="scan")
        await cb.answer(("✅ Verified" if ok else "❌ " + why)[:200], show_alert=not ok)
        await _send_mychannels(cb, uid, edit=True); return
    await cb.answer("Unknown action.", show_alert=True)


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
    await msg.answer("🔗 <b>YOUR REFERRAL CODE</b>\n\n" + code + "\n\n"
                     f"Share it with a new member. You earn {amount(1)} only after they "
                     "forward and complete one post.\n\n"
                     f"✅ Completed: {st['completed']}\n⏳ Pending: {st['pending']}",
                     parse_mode="HTML")


@dp.message(Command("referrals"))
async def referrals_cmd(msg: types.Message):
    uid = msg.from_user.id
    if hasattr(ledger, "tx"):
        st = ledger.tx.referral_stats(uid)
        history = ledger.tx.referral_history(uid, limit=8)
        lines = ["🔗 <b>REFERRAL HISTORY</b>", "",
                 f"Referred: {st['referred']}",
                 f"Qualified: {st['qualified']}",
                 f"Pending: {st['pending']}",
                 f"Reversed: {st['reversed']}" ]
        if history:
            lines += ["", "Recent referrals:"]
            lines += [f"• {row['referred_id']} · {row['status']}" for row in history]
        await msg.answer("\n".join(lines), parse_mode="HTML")
        return
    st = referrals.stats(uid)
    await msg.answer(f"🔗 Referrals: {st['referred']} · qualified {st['completed']} · pending {st['pending']}")


@dp.message(Command("referralrank"))
async def referral_rank_cmd(msg: types.Message):
    if not _can_panel(msg.from_user.id, "reward"):
        await msg.answer("🔒 Reward owner/admin access required.")
        return
    if not hasattr(ledger, "tx"):
        await msg.answer("Referral ranking preview requires transactional Mint mode.")
        return
    parts = (msg.text or "").split()
    try:
        pool = int(parts[1]) if len(parts) > 1 else 0
        winners = int(parts[2]) if len(parts) > 2 else 10
        if pool < 0 or winners < 0:
            raise ValueError
    except ValueError:
        await msg.answer("Usage: /referralrank [pool] [winners]")
        return
    records = [ReferralRecord(
        referrer_id=str(row["referrer_id"]),
        referred_id=str(row["referred_id"]),
        status=row["status"],
        retained=row["status"] == "qualified",
        qualified_activity_count=1 if row["status"] == "qualified" else 0,
    ) for row in ledger.tx.all_referrals(limit=10000)]
    ranking = rank_referrers(records)
    allocation = allocate_monthly_rewards(ranking, pool=pool, winners=winners) if pool else []
    period = dt.datetime.now().strftime("%Y-%m")
    existing_snapshot = ledger.tx.referral_ranking_snapshot(period)
    snapshot_note = ""
    if not existing_snapshot and pool:
        try:
            ledger.tx.create_referral_ranking_snapshot(
                period=period, pool=pool, winners=winners,
                ranking=ranking, allocation=allocation)
            snapshot_note = f"Snapshot created for {period}."
        except Exception:
            snapshot_note = f"A snapshot already exists for {period}."
    elif existing_snapshot:
        snapshot_note = f"Snapshot {period}: {existing_snapshot['status']}."
    lines = ["🏆 <b>REFERRAL RANKING PREVIEW</b>", "", snapshot_note]
    for index, row in enumerate(ranking[:10], 1):
        reward = next((item["reward_amount"] for item in allocation if item["referrer_id"] == row["referrer_id"]), 0)
        lines.append(f"{index}. {row['referrer_id']} · score {row['score']:.2f} · qualified {row['qualified_referrals']} · preview Mint {reward}")
    lines.extend(["", "This is a preview only. No referral rewards are distributed automatically."])
    await msg.answer("\n".join(lines), parse_mode="HTML")


@dp.message(Command("approvereferral"))
async def approve_referral_cmd(msg: types.Message):
    if not _can_panel(msg.from_user.id, "reward"):
        await msg.answer("🔒 Reward owner/admin access required.")
        return
    parts = (msg.text or "").split()
    period = parts[1] if len(parts) > 1 else dt.datetime.now().strftime("%Y-%m")
    if not hasattr(ledger, "tx"):
        await msg.answer("Transactional referral snapshots are not enabled.")
        return
    ok = ledger.tx.approve_referral_ranking(period, msg.from_user.id)
    if ok:
        ledger.tx.add_audit_event(
            actor_type="admin", actor_id=str(msg.from_user.id),
            action="REFERRAL_RANKING_APPROVED", object_type="referral_snapshot",
            object_id=period, reason="monthly ranking approval",
        )
    await msg.answer("✅ Referral ranking snapshot approved." if ok else "Snapshot not found or already finalized.")


@dp.message(Command("joinref"))
async def join_ref_cmd(msg: types.Message):
    parts = (msg.text or "").split()
    if len(parts) != 2 or not referrals.attach(msg.from_user.id, parts[1]):
        await msg.answer("That referral code is invalid, already used, or belongs to you.")
        return
    await msg.answer(f"✅ <b>REFERRAL LINKED</b>\n\nYour referrer earns {amount(1)} only after you "
                     "complete one genuine forwarded post.", parse_mode="HTML")


@dp.message(Command("available"))
async def available_cmd(msg: types.Message):
    rows = available.available(msg.from_user.id, limit=10)
    if not rows:
        await msg.answer("📭 No posts are currently available for your performance tier/category.")
        return
    lines = [f"📬 {len(rows)} post(s) available:"]
    for row in rows:
        lines.append(f"• {row['id']} · {row.get('category', 'General')}"
                     + (" · 👑 owner priority" if row.get("owner") else ""))
    await msg.answer("\n".join(lines))


def _elapsed_seconds(timestamp) -> int:
    if timestamp is None:
        return 0
    return max(0, int(time.time()) - int(timestamp))


def _duration_label(seconds: int) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {seconds}s"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h {minutes}m"
    days, hours = divmod(hours, 24)
    return f"{days}d {hours}h"


def _elapsed_label(timestamp) -> str:
    if timestamp is None:
        return "none"
    return _duration_label(_elapsed_seconds(timestamp))


def _remaining_label(timestamp) -> str:
    if timestamp is None:
        return "none"
    return _duration_label(max(0, int(timestamp) - int(time.time())))


def _outbox_age_marker(event: dict) -> str:
    threshold = int(os.getenv("OUTBOX_STALE_WARN_SECONDS", "3600"))
    age = _elapsed_seconds(event.get("created_at"))
    return "🚨 STALE" if age >= threshold else ""


def _record_outbox_admin_action(actor_id, action: str, event_id: str, reason: str):
    if hasattr(ledger, "tx"):
        ledger.tx.add_audit_event(
            actor_type="admin", actor_id=str(actor_id), action=action,
            object_type="outbox", object_id=str(event_id), reason=reason,
        )


def _failed_result(row: dict, reason: str | None):
    return failed_result_from_reason(row, reason)


def _apply_verification(row: dict, result, *, source: str) -> tuple[bool, str]:
    """Every verification outcome in this bot goes through the shared state machine."""
    ok, why = apply_verification_result(destination_states, row, result, source=source,
                                        on_change=_record_channel_state_change)
    if not ok and "not permitted" in why:
        logging.warning("verification result ignored for %s: %s", row.get("chat_id"), why)
    return ok, why


def _record_channel_state_change(row: dict, previous: str, current: str, reason: str):
    """Persist permission-state transitions for audit and reconciliation review."""
    chat_id = row.get("chat_id") or row.get("username")
    owner_id = row.get("owner_id")
    if hasattr(ledger, "tx"):
        ledger.tx.add_audit_event(
            actor_type="telegram",
            actor_id=str(owner_id),
            action=f"CHANNEL_STATUS_{current}",
            object_type="channel",
            object_id=str(chat_id),
            reason=f"{previous} -> {current}: {reason}",
        )
    else:
        audit.record(
            bot="reward", sender=str(owner_id), target_channel=str(chat_id),
            post_type="channel_permission_reconciliation",
            decision=f"{previous}->{current}", reason=reason,
        )


def _task_categories(uid: int):
    """Categories from the user's registered destinations."""
    categories = []
    for row in channels.mine(uid):
        categories.extend(row.get("categories") or [])
    return sorted(set(categories)) or ["General"]


def _destination_daily_limit(destination_id) -> int:
    snapshot = credibility_snapshots.current(destination_id)
    if not snapshot:
        return int(os.getenv("TASK_DAILY_LIMIT_PROVISIONAL", "3"))
    limits = {
        "PROVISIONAL": int(os.getenv("TASK_DAILY_LIMIT_PROVISIONAL", "3")),
        "EMERGING": int(os.getenv("TASK_DAILY_LIMIT_EMERGING", "6")),
        "ESTABLISHED": int(os.getenv("TASK_DAILY_LIMIT_ESTABLISHED", "10")),
        "PROVEN": int(os.getenv("TASK_DAILY_LIMIT_PROVEN", "15")),
        "PREMIER": int(os.getenv("TASK_DAILY_LIMIT_PREMIER", "20")),
    }
    limit = limits.get(snapshot["tier"], limits["PROVISIONAL"])
    if float(snapshot.get("confidence", 0)) < 0.20:
        limit = min(limit, int(os.getenv("TASK_DAILY_LIMIT_LOW_CONFIDENCE", "5")))
    return limit


def _eligible_marketplace_tasks(uid: int) -> list[dict]:
    """Return tasks matching at least one active destination and its tier."""
    destinations = [row for row in channels.mine(uid)
                    if row.get("status", "ACTIVE") == "ACTIVE"
                    and channels.participation_allowed(row.get("chat_id") or row.get("username"))]
    if not destinations:
        return []
    tier_order = {"PROVISIONAL": 0, "EMERGING": 1, "ESTABLISHED": 2,
                  "PROVEN": 3, "PREMIER": 4}
    day_start = int(time.time()) - (int(time.time()) % 86400)
    available_destinations = [destination for destination in destinations
                              if marketplace.user_claim_count_since(
                                  uid, day_start,
                                  destination.get("chat_id") or destination.get("username"))
                              < _destination_daily_limit(destination.get("chat_id") or destination.get("username"))]
    if not available_destinations:
        return []
    tasks = marketplace.available_for(uid, categories=_task_categories(uid), limit=50)
    eligible = []
    for task in tasks:
        required = task.get("minimum_tier", "PROVISIONAL")
        for destination in available_destinations:
            destination_id = destination.get("chat_id") or destination.get("username")
            if credibility_snapshots.is_suspended(destination_id):
                continue
            snapshot = credibility_snapshots.current(destination_id)
            if snapshot:
                actual = snapshot["tier"]
            else:
                key = destination.get("username") or ("@" + str(uid))
                score = float(perf.score(key).get("score", 0.5)) * 100
                actual = tier_for_score(score)
            if tier_order.get(actual, 0) >= tier_order.get(required, 0):
                eligible.append(task)
                break
    # Recommend the most relevant work first: category match is already enforced,
    # then higher reward and remaining capacity, with older tasks first as a tie-break.
    eligible.sort(key=lambda task: (
        -int(task.get("reward_amount", 1)),
        -int(task.get("required_performers", 0)),
        int(task.get("created_at", 0)),
    ))
    return eligible


def _task_menu(tasks: list[dict]) -> InlineKeyboardMarkup:
    rows = []
    for task in tasks[:8]:
        progress = marketplace.progress(task["task_id"])
        rows.append([
            InlineKeyboardButton(
                text=f"📋 {task['title'][:24]} · {progress['completed']}/{progress['required']}",
                callback_data=f"task:view:{task['task_id']}"),
            InlineKeyboardButton(text="ℹ️", callback_data=f"task:why:{task['task_id']}")
        ])
    rows.append([InlineKeyboardButton(text="🔄 Refresh tasks", callback_data="menu:tasks")])
    rows.append([InlineKeyboardButton(text="ℹ️ Why am I not eligible?", callback_data="task:eligibility")])
    rows.append([InlineKeyboardButton(text="⬅️ Back", callback_data="menu:hub")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@dp.callback_query(lambda c: c.data == "task:eligibility")
async def task_eligibility_explanation(cb: types.CallbackQuery):
    uid = cb.from_user.id
    destinations = channels.mine(uid)
    active = [row for row in destinations
              if row.get("status", "ACTIVE") == "ACTIVE"
              and channels.participation_allowed(row.get("chat_id") or row.get("username"))]
    categories = sorted({category for row in active for category in (row.get("categories") or [])})
    if active:
        tier_order = {"PROVISIONAL": 0, "EMERGING": 1, "ESTABLISHED": 2,
                      "PROVEN": 3, "PREMIER": 4}
        tiers = []
        for row in active:
            key = row.get("username") or ("@" + str(uid))
            score = float(perf.score(key).get("score", 0.5)) * 100
            tiers.append(tier_for_score(score))
        current_tier = max(tiers, key=lambda tier: tier_order.get(tier, 0))
    else:
        current_tier = "None"
    tasks = _eligible_marketplace_tasks(uid)
    lines = [
        "ℹ️ <b>TASK ELIGIBILITY</b>",
        "",
        f"Active destinations: {len(active)}",
        f"Categories: {', '.join(categories) if categories else 'None registered'}",
        f"Highest detected credibility: {current_tier}",
        f"Currently eligible tasks: {len(tasks)}",
        "Daily task limits: " + ", ".join(
            f"{row.get('username') or row.get('chat_id')} "
            f"{marketplace.user_claim_count_since(uid, int(time.time()) - (int(time.time()) % 86400), row.get('chat_id') or row.get('username'))}/{_destination_daily_limit(row.get('chat_id') or row.get('username'))}"
            for row in active
        ),
        "",
        "Tasks require an active connected destination, a compatible category, "
        "sufficient credibility, available capacity, and no previous claim.",
    ]
    if not active:
        lines.extend(["", "Next step: connect a Telegram channel and complete verification."])
    elif not categories:
        lines.extend(["", "Next step: add a compatible category to your channel profile."])
    await cb.message.edit_text(
        "\n".join(lines), parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Refresh tasks", callback_data="menu:tasks")],
            [InlineKeyboardButton(text="⬅️ Back", callback_data="menu:hub")],
        ]),
    )
    await cb.answer()


async def show_tasks_message(target, uid: int, *, edit: bool = False):
    rows = _eligible_marketplace_tasks(uid)[:8]
    if not rows:
        text = ("📋 <b>TASK MARKETPLACE</b>\n\n"
                "No recommended tasks are currently available.\n\n"
                "Connect and verify a channel to become eligible for channel-posting tasks.")
        markup = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📂 My channels", callback_data="menu:channels")],
            [InlineKeyboardButton(text="⬅️ Back", callback_data="menu:hub")],
        ])
    else:
        text = ("📋 <b>TASK MARKETPLACE</b>\n\n"
                "Tasks below are candidates for your registered categories.\n"
                "Final eligibility is checked again before claiming.\n\n"
                + "\n".join(
                    f"• {r['title']} — {marketplace.progress(r['task_id'])['completed']}/{r['required_performers']} completed"
                    for r in rows))
        markup = _task_menu(rows)
    if edit:
        await target.edit_text(text, parse_mode="HTML", reply_markup=markup)
    else:
        await target.answer(text, parse_mode="HTML", reply_markup=markup)


@dp.message(Command("tasks"))
async def tasks_cmd(msg: types.Message):
    await show_tasks_message(msg, msg.from_user.id)


@dp.message(Command("createtask"))
async def create_task_cmd(msg: types.Message):
    """Owner-only bootstrap for marketplace tasks.

    This intentionally remains a small administrative fallback until the Admin
    Bot/Mini App task composer is implemented.
    """
    if not roles.is_owner(msg.from_user.id):
        await msg.answer("🔒 Owner only.")
        return
    parts = (msg.text or "").split(maxsplit=3)
    if len(parts) < 4:
        await msg.answer("Usage: /createtask <category> <performers> <title>")
        return
    category = parts[1]
    try:
        performers = int(parts[2])
    except ValueError:
        await msg.answer("The performer count must be a positive number.")
        return
    try:
        task_id = marketplace.create_task(
            creator_user_id=msg.from_user.id,
            category=category,
            title=parts[3],
            required_performers=performers,
            # Bootstrap command creates a platform-generated text task. Forwarded
            # task creation will later store source_chat_id/source_message_id.
            payload={"text": parts[3]},
        )
        marketplace.publish(task_id)
    except ValueError as exc:
        await msg.answer(str(exc))
        return
    await msg.answer(
        f"✅ Task published: <code>{task_id}</code>\n"
        f"Required performers: {performers}",
        parse_mode="HTML", reply_markup=_menu("owner", msg.from_user.id))


@dp.callback_query(lambda c: c.data == "task:create")
async def begin_forwarded_task(cb: types.CallbackQuery):
    if not roles.is_owner(cb.from_user.id):
        await cb.answer("Owner only.", show_alert=True)
        return
    pending = _session(cb.from_user.id).get("pending") or {}
    if not pending.get("from_chat_id") or not pending.get("from_message_id"):
        await cb.answer("Forward the post first.", show_alert=True)
        return
    categories = [
        [InlineKeyboardButton(text=category, callback_data=f"task:category:{category}")]
        for category in POST_CATEGORIES
    ]
    categories.append([InlineKeyboardButton(text="❌ Cancel", callback_data="menu:cancel")])
    await cb.message.edit_text(
        "📋 <b>CREATE MARKETPLACE TASK</b>\n\n"
        "The forwarded post is saved with its original Telegram source.\n"
        "Choose its category:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=categories),
    )
    await cb.answer()


@dp.callback_query(lambda c: c.data and c.data.startswith("task:category:"))
async def forwarded_task_category(cb: types.CallbackQuery):
    if not roles.is_owner(cb.from_user.id):
        await cb.answer("Owner only.", show_alert=True)
        return
    category = cb.data.split(":", 2)[2]
    if category not in POST_CATEGORIES:
        await cb.answer("Unknown category.", show_alert=True)
        return
    _session_set(cb.from_user.id, task_category=category)
    buttons = [
        [InlineKeyboardButton(text=str(count), callback_data=f"task:performers:{count}")]
        for count in (1, 2, 3, 5, 10, 20)
    ]
    buttons.append([InlineKeyboardButton(text="❌ Cancel", callback_data="menu:cancel")])
    await cb.message.edit_text(
        f"📋 <b>TASK CATEGORY: {category}</b>\n\n"
        "How many different eligible users must publish this post?",
        parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await cb.answer()


def _task_settings_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏱ 6 hours", callback_data="task:setting:expiry:21600"),
         InlineKeyboardButton(text="⏱ 24 hours", callback_data="task:setting:expiry:86400")],
        [InlineKeyboardButton(text="⏱ 72 hours", callback_data="task:setting:expiry:259200")],
        [InlineKeyboardButton(text="🪙 1 Mint", callback_data="task:setting:reward:1"),
         InlineKeyboardButton(text="🪙 2 Mint", callback_data="task:setting:reward:2"),
         InlineKeyboardButton(text="🪙 5 Mint", callback_data="task:setting:reward:5")],
        [InlineKeyboardButton(text="🌱 Provisional+", callback_data="task:setting:tier:PROVISIONAL"),
         InlineKeyboardButton(text="🌿 Emerging+", callback_data="task:setting:tier:EMERGING")],
        [InlineKeyboardButton(text="🌳 Established+", callback_data="task:setting:tier:ESTABLISHED")],
        [InlineKeyboardButton(text="✅ Publish task", callback_data="task:publish")],
        [InlineKeyboardButton(text="❌ Cancel", callback_data="menu:cancel")],
    ])


@dp.callback_query(lambda c: c.data and c.data.startswith("task:performers:"))
async def forwarded_task_performers(cb: types.CallbackQuery):
    if not roles.is_owner(cb.from_user.id):
        await cb.answer("Owner only.", show_alert=True)
        return
    try:
        performers = int(cb.data.split(":", 2)[2])
    except ValueError:
        await cb.answer("Invalid performer count.", show_alert=True)
        return
    if performers < 1:
        await cb.answer("Performers must be positive.", show_alert=True)
        return
    _session_set(cb.from_user.id, task_performers=performers)
    await cb.message.edit_text(
        "📋 <b>TASK SETTINGS</b>\n\n"
        f"Performers: {performers}\n\n"
        "Choose expiry, reward, and minimum credibility tier.\n"
        "You can change a setting by tapping another option, then publish.",
        parse_mode="HTML", reply_markup=_task_settings_keyboard(),
    )
    await cb.answer()


@dp.callback_query(lambda c: c.data and c.data.startswith("task:setting:"))
async def forwarded_task_setting(cb: types.CallbackQuery):
    if not roles.is_owner(cb.from_user.id):
        await cb.answer("Owner only.", show_alert=True)
        return
    parts = cb.data.split(":", 3)
    if len(parts) != 4:
        await cb.answer("Invalid task setting.", show_alert=True)
        return
    kind, value = parts[2], parts[3]
    if kind == "expiry":
        _session_set(cb.from_user.id, task_expiry_seconds=int(value))
    elif kind == "reward":
        _session_set(cb.from_user.id, task_reward=int(value))
    elif kind == "tier":
        _session_set(cb.from_user.id, task_minimum_tier=value)
    else:
        await cb.answer("Unknown task setting.", show_alert=True)
        return
    await cb.answer("Setting saved")


@dp.callback_query(lambda c: c.data == "task:publish")
async def create_forwarded_task(cb: types.CallbackQuery):
    if not roles.is_owner(cb.from_user.id):
        await cb.answer("Owner only.", show_alert=True)
        return
    session = _session(cb.from_user.id)
    pending = session.get("pending") or {}
    category = session.get("task_category")
    performers = session.get("task_performers")
    expiry_seconds = session.get("task_expiry_seconds", 86400)
    reward_amount = session.get("task_reward", 1)
    minimum_tier = session.get("task_minimum_tier", "PROVISIONAL")
    if not category or not performers or not pending.get("from_chat_id") or not pending.get("from_message_id"):
        await cb.answer("That forwarded task setup is incomplete.", show_alert=True)
        return
    title = pending.get("source_text") or f"Forwarded {category} post"
    try:
        task_id = marketplace.create_task(
            creator_user_id=cb.from_user.id,
            category=category,
            title=title[:120],
            required_performers=performers,
            expires_at=int(time.time()) + int(expiry_seconds),
            reward_amount=reward_amount,
            minimum_tier=minimum_tier,
            allowed_categories=[category],
            payload={
                "source_chat_id": pending["from_chat_id"],
                "source_message_id": pending["from_message_id"],
                **relay.Source.from_record(pending).as_record(),
                "source": pending.get("source", "unknown"),
                "silent": pending.get("ntf") == "silent",
            },
        )
        marketplace.publish(task_id)
    except (ValueError, TypeError) as exc:
        await cb.answer(str(exc), show_alert=True)
        return
    _session_clear(cb.from_user.id)
    await cb.message.edit_text(
        "✅ <b>FORWARDED TASK PUBLISHED</b>\n\n"
        f"Task: <code>{task_id}</code>\n"
        f"Category: {escape(str(category))}\n"
        f"Performers: {performers}\n"
        f"Reward: 🪙 {reward_amount}\n"
        f"Minimum tier: {minimum_tier}\n\n"
        "The original post will be forwarded to each eligible connected channel.",
        parse_mode="HTML", reply_markup=_menu("owner", cb.from_user.id),
    )
    await cb.answer("Task published")


@dp.callback_query(lambda c: c.data and c.data.startswith("task:why:"))
async def task_recommendation_reason(cb: types.CallbackQuery):
    task_id = cb.data.split(":", 2)[2]
    task = marketplace.get_task(task_id)
    if not task:
        await cb.answer("That task is no longer available.", show_alert=True)
        return
    destinations = [row for row in channels.mine(cb.from_user.id)
                    if row.get("status", "ACTIVE") == "ACTIVE"
                    and channels.participation_allowed(row.get("chat_id") or row.get("username"))]
    categories = _task_categories(cb.from_user.id)
    matching = [row for row in destinations
                if task.get("category") in (row.get("categories") or [])]
    reasons = [f"Category match: {task.get('category')}",
               f"Reward: {int(task.get('reward_amount', 1))} Mint",
               f"Minimum tier: {task.get('minimum_tier', 'PROVISIONAL')}+",
               f"Available capacity: {task.get('required_performers', 0)} performers"]
    if matching:
        reasons.append(f"Compatible destinations: {len(matching)}")
    else:
        reasons.append("This task is visible through your current marketplace category profile.")
    await cb.answer("\n".join(reasons), show_alert=True)


@dp.callback_query(lambda c: c.data and c.data.startswith("task:view:"))
async def task_action(cb: types.CallbackQuery):
    parts = cb.data.split(":", 2)
    if len(parts) != 3 or parts[1] != "view":
        await cb.answer("Unknown task action.", show_alert=True)
        return
    task_id = parts[2]
    try:
        rows = _eligible_marketplace_tasks(cb.from_user.id)
        task = next((row for row in rows if row["task_id"] == task_id), None)
        if not task:
            await cb.answer("That task is no longer available.", show_alert=True)
            return
        progress = marketplace.progress(task_id)
        text = (f"📋 <b>{escape(str(task['title']))}</b>\n\n"
                f"Category: {escape(str(task['category']))}\n"
                f"Progress: {progress['completed']} / {progress['required']} completed\n"
                f"Claimed now: {progress['claimed']}\n"
                f"Remaining: {progress['remaining']}\n"
                f"Reward: 🪙 {int(task.get('reward_amount', 1))}\n"
                f"Minimum credibility: {escape(str(task.get('minimum_tier', 'PROVISIONAL')))}+\n\n"
                "A verified connected channel is required before claiming.")
        await cb.message.edit_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Choose channel", callback_data=f"task:choose:{task_id}")],
            [InlineKeyboardButton(text="⬅️ Back to tasks", callback_data="menu:tasks")],
        ]))
        await cb.answer()
    except TaskNotFound:
        await cb.answer("That task no longer exists.", show_alert=True)


@dp.callback_query(lambda c: c.data and c.data.startswith("task:choose:"))
async def choose_task_channel(cb: types.CallbackQuery):
    task_id = cb.data.split(":", 2)[2]
    rows = channels.mine(cb.from_user.id)
    candidates = [row for row in rows
                  if row.get("status", "ACTIVE") == "ACTIVE"
                  and channels.participation_allowed(row.get("chat_id") or row.get("username"))]
    if not candidates:
        await cb.answer("Register a channel first.", show_alert=True)
        return
    # Store the destination list in the user session; callback data contains only
    # a short index so it remains within Telegram's callback-data limit.
    _session_set(cb.from_user.id, task_id=task_id,
                 task_destinations=[row.get("chat_id") or row.get("username") for row in candidates])
    buttons = []
    for index, row in enumerate(candidates):
        buttons.append([InlineKeyboardButton(
            text=f"📢 {row.get('username') or row.get('chat_id')}",
            callback_data=f"task:claim:{task_id}:{index}")])
    buttons.append([InlineKeyboardButton(text="⬅️ Back", callback_data=f"task:view:{task_id}")])
    await cb.message.edit_text(
        "📢 <b>CHOOSE EXECUTION CHANNEL</b>\n\n"
        "Select the connected channel where the original post should be published.",
        parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await cb.answer()


async def _verify_task_channel(row: dict):
    """Recheck the destination with its owner's bot, never the shared bot."""
    chat_id = channels.telegram_reference(row)
    if not chat_id:
        return None, "destination has no Telegram chat id"
    try:
        result = await telegram_verification.verify(int(row["owner_id"]), chat_id)
    except CredentialError:
        return None, "connect your bot with /connectbot"
    except Exception as exc:
        return None, f"Telegram verification failed: {classify_telegram_error(exc).kind.value}"
    # Record the real Telegram outcome either way, through the state machine.
    _apply_verification(row, result, source="delivery")
    if not result.eligible:
        return None, "; ".join(result.reasons)
    return {"row": row, "chat_id": int(result.chat_id), "verification": result}, None


async def _reconcile_registered_channels(uid: int) -> list[str]:
    """Refresh Telegram permission state without trusting the local bot_added flag."""
    changes = []
    for row in channels.mine(uid):
        before = row.get("status", "ACTIVE")
        verified, reason = await _verify_task_channel(row)
        if not verified:
            failed = _failed_result(row, reason)
            if failed is not None and channels.get(row["chat_id"]).get("status") == before:
                _apply_verification(row, failed, source="scan")
        after = channels.get(row["chat_id"]).get("status", before)
        if before != after:
            label = row.get("username") or row.get("chat_id")
            changes.append(f"{label}: {after.lower()} ({reason or 'permissions verified'})")
    return changes


class RelayUnavailable(RuntimeError):
    """No bot can legally forward this source to this destination."""


_PLATFORM_BOT_ID: int | None = None


def _platform_bot_id() -> int | None:
    global _PLATFORM_BOT_ID
    if _PLATFORM_BOT_ID is None:
        try:
            _PLATFORM_BOT_ID = int(str(BOT_TOKEN).split(":", 1)[0])
        except (ValueError, AttributeError):
            _PLATFORM_BOT_ID = None
    return _PLATFORM_BOT_ID


def _relay_plan(source: relay.Source, target, *, platform_may_try: bool = False) -> tuple[relay.Plan, dict | None]:
    row = channels.get(target)
    if not row:
        return relay.Plan("unavailable", "", None, None, "destination is not registered"), None
    dest = relay.destination_from_registry(row, platform_bot_id=_platform_bot_id(),
                                           owner_rows=channels.mine(int(row["owner_id"])) if row.get("owner_id") is not None else [])
    return relay.resolve(source, dest, platform_may_try=platform_may_try), row


async def _relay_forward(source: relay.Source, target, *, silent: bool = False, platform_may_try: bool = False):
    """Forward `source` to `target` with the ONE bot that can legally read it.

    Returns (sent_message, plan). Raises RelayUnavailable when no route exists so
    callers fail closed and audit the reason instead of posting a stand-in.
    """
    plan, row = _relay_plan(source, target, platform_may_try=platform_may_try)
    if not plan.ok:
        raise RelayUnavailable(plan.reason)
    if plan.forwarder == "platform":
        sent = await bot.forward_message(chat_id=target, from_chat_id=plan.from_chat_id,
                                         message_id=int(plan.message_id), disable_notification=silent)
        return sent, plan
    owned_bot = telegram_verification.make_bot(telegram_verification.credentials.token(int(row["owner_id"])))
    try:
        sent = await owned_bot.forward_message(chat_id=target, from_chat_id=plan.from_chat_id,
                                               message_id=int(plan.message_id), disable_notification=silent)
        return sent, plan
    finally:
        if owned_bot is not bot:
            await owned_bot.session.close()


async def _execute_task_payload(task: dict, chat_id, owner_id: int):
    """Publish only content explicitly stored with the task.

    Forwarded tasks require source_chat_id/source_message_id. Text tasks are
    platform-generated tasks. A title alone is never fabricated into a post.
    """
    payload = task.get("payload") or {}
    source = relay.Source.from_record(payload)
    if source.has_inbox or source.has_channel_origin:
        sent, _plan = await _relay_forward(source, chat_id, silent=bool(payload.get("silent", False)))
        return sent
    text = payload.get("text")
    if text:
        owned_bot = telegram_verification.make_bot(telegram_verification.credentials.token(int(owner_id)))
        try:
            return await owned_bot.send_message(chat_id, text)
        finally:
            if owned_bot is not bot:
                await owned_bot.session.close()
    raise ValueError("task has no executable Telegram payload")


@dp.callback_query(lambda c: c.data and c.data.startswith("task:claim:"))
async def claim_task(cb: types.CallbackQuery):
    parts = cb.data.split(":", 3)
    if len(parts) != 4:
        await cb.answer("Choose a channel before claiming.", show_alert=True)
        return
    task_id, destination_index = parts[2], parts[3]
    session = _session(cb.from_user.id)
    destinations = session.get("task_destinations") or []
    try:
        selected_chat_id = destinations[int(destination_index)]
    except (ValueError, IndexError):
        await cb.answer("That channel selection expired.", show_alert=True)
        return
    task = marketplace.get_task(task_id)
    if not task:
        await cb.answer("That task no longer exists.", show_alert=True)
        return
    blocked = _enforcement_block(cb.from_user.id, "tasks", selected_chat_id)
    if blocked:
        await cb.answer(blocked[:200], show_alert=True)
        return
    rows = channels.mine(cb.from_user.id)
    selected = next((row for row in rows
                     if (row.get("chat_id") or row.get("username")) == selected_chat_id), None)
    if not selected or selected.get("status", "ACTIVE") != "ACTIVE":
        await cb.answer("That channel is no longer active.", show_alert=True)
        return
    ledger_key = selected.get("username") or ("@" + str(cb.from_user.id))
    profile = perf.score(ledger_key)
    actual_tier = tier_for_score(float(profile.get("score", 0.5)) * 100)
    tier_order = {"PROVISIONAL": 0, "EMERGING": 1, "ESTABLISHED": 2, "PROVEN": 3, "PREMIER": 4}
    required_tier = task.get("minimum_tier", "PROVISIONAL")
    if tier_order.get(actual_tier, 0) < tier_order.get(required_tier, 0):
        await cb.answer(f"This task requires {required_tier}+ credibility.", show_alert=True)
        return
    verified, verification_error = await _verify_task_channel(selected)
    if not verified:
        await cb.answer(f"Task execution blocked: {verification_error}", show_alert=True)
        return
    destination = verified["row"]
    destination_id = destination.get("chat_id") or destination.get("username")
    if credibility_snapshots.is_suspended(destination_id):
        control = credibility_snapshots.control(destination_id)
        await cb.answer(f"Channel temporarily suspended: {control.get('reason')}", show_alert=True)
        return
    day_start = int(time.time()) - (int(time.time()) % 86400)
    daily_limit = _destination_daily_limit(destination_id)
    if marketplace.user_claim_count_since(cb.from_user.id, day_start, destination_id) >= daily_limit:
        await cb.answer(f"This channel reached its daily limit ({daily_limit}).", show_alert=True)
        return
    try:
        claim = marketplace.claim(task_id, user_id=cb.from_user.id,
                                  destination_id=destination_id)
        sent = await _execute_task_payload(task, verified["chat_id"], int(destination["owner_id"]))
        completion = marketplace.complete(
            claim["claim_id"],
            telegram_chat_id=verified["chat_id"],
            telegram_message_id=sent.message_id,
            reward_event_id=f"task:{claim['claim_id']}",
        )
        credibility_snapshots.record_task_outcome(
            destination_id=destination_id, user_id=cb.from_user.id,
            category=task["category"], success=True,
        )
        # Telegram completion and Mint live in separate stores. In transactional
        # mode, enqueue the reward durably and let the outbox worker issue it.
        # The idempotency key makes retries safe after worker or process failure.
        username = "@" + (cb.from_user.username or str(cb.from_user.id))
        reward_amount = int(task.get("reward_amount", 1))
        reward_key = f"task:{completion['completion_id']}"
        if hasattr(ledger, "tx"):
            ledger.tx.enqueue_outbox(
                event_type="TASK_MINT_REWARD",
                recipient_id=cb.from_user.id,
                payload=json.dumps({
                    "username": username,
                    "amount": reward_amount,
                    "idempotency_key": reward_key,
                    "task_id": task_id,
                    "completion_id": completion["completion_id"],
                }, separators=(",", ":")),
                idempotency_key=reward_key,
            )
            await _process_mint_outbox()
        else:
            ledger.earn(username, reward_amount)
    except (TaskUnavailable, TaskNotFound, ValueError) as exc:
        await cb.answer(str(exc), show_alert=True)
        return
    except Exception as exc:
        # A Telegram or persistence failure must not leave an active claim that
        # looks completed. The lease can also expire naturally after a restart.
        try:
            if 'claim' in locals():
                marketplace.release_claim(claim["claim_id"], reason=str(exc)[:120])
                credibility_snapshots.record_task_outcome(
                    destination_id=destination_id, user_id=cb.from_user.id,
                    category=task.get("category", "unknown"), success=False,
                )
        except Exception:
            logging.exception("could not release failed task claim")
        logging.warning("task execution failed: %s", exc)
        await cb.answer("Task execution failed; the claim was released.", show_alert=True)
        return
    await cb.message.edit_text(
        "✅ <b>TASK COMPLETED</b>\n\n"
        f"Published to: <code>{verified['chat_id']}</code>\n"
        f"Telegram message: <code>{sent.message_id}</code>\n"
        f"Mint reward: 🪙 {int(task.get('reward_amount', 1))}\n"
        "Mint reward queued after successful Telegram publication; retries are automatic if needed.",
        parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📋 Back to tasks", callback_data="menu:tasks")],
            [InlineKeyboardButton(text="⬅️ Main menu", callback_data="menu:hub")],
        ]))
    await cb.answer("Task completed")


@dp.my_chat_member()
async def my_chat_member_changed(update: types.ChatMemberUpdated):
    """React immediately when Telegram changes the bot's membership or rights."""
    row = channels.get(update.chat.id)
    if not row:
        row = next((item for item in channels.mine(update.from_user.id)
                    if str(item.get("chat_id")) == str(update.chat.id)), None)
    if not row:
        return
    previous = row.get("status", "ACTIVE")
    verified, reason = await _verify_task_channel(row)
    if not verified:
        failed = _failed_result(row, reason)
        if failed is not None and channels.get(row.get("chat_id") or update.chat.id).get("status") == previous:
            _apply_verification(row, failed, source="telegram_event")
    status = channels.get(row.get("chat_id") or update.chat.id).get("status", previous)
    if previous != status:
        label = row.get("username") or update.chat.id
        try:
            await bot.send_message(
                int(row["owner_id"]),
                f"🔎 Telegram access updated for {label}: {status.lower()}.\\n"
                f"{reason or 'Posting permissions verified.'}\\n\\n"
                "Marketplace eligibility has been refreshed.",
            )
        except Exception:
            logging.exception("could not notify channel owner about membership change")


@dp.message(Command("verification"))
async def verification_cmd(msg: types.Message):
    parts = (msg.text or "").split()
    target = parts[1] if len(parts) > 1 else None
    row = next((r for r in channels.mine(msg.from_user.id)
                if not target or r.get("username") == target
                or str(r.get("chat_id")) == target), None)
    if not row:
        await msg.answer("Usage: /verification @destination")
        return
    checks = row.get("verification_checks") or {}
    labels = {"destination": "Telegram destination", "bot_membership": "Bot membership",
              "administrator": "Administrator status", "permissions": "Required permissions",
              "accessibility": "Destination accessibility", "member_count": "Member count",
              "eligibility": "Eligibility"}
    lines = [f"🔍 <b>VERIFICATION DETAILS</b> — {escape(str(row.get('username')))}", "",
             f"State: <b>{escape(str(row.get('verified_state', 'REGISTERED')))}</b>"]
    for key, label in labels.items():
        lines.append(f"{('✅' if checks.get(key) == 'passed' else '⚠️' if checks.get(key) == 'unavailable' else '❌')} {label}: {checks.get(key, 'not checked')}")
    reasons = row.get("verification_reasons") or []
    if reasons:
        lines.extend(["", "<b>Corrective action:</b>", *[f"• {escape(str(reason))}" for reason in reasons]])
    lines.extend(["", "Run /scan to perform fresh Telegram checks."])
    await msg.answer("\n".join(lines), parse_mode="HTML")


@dp.message(Command("stats"))
async def stats_cmd(msg: types.Message):
    """Manual Telegram-backed statistics refresh; no user-entered counts."""
    target = (msg.text or "").split()[1] if len((msg.text or "").split()) > 1 else None
    rows = channels.mine(msg.from_user.id)
    row = next((r for r in rows if not target or r.get("username") == target
                or str(r.get("chat_id")) == target), None)
    if not row:
        await msg.answer("Usage: /stats @destination")
        return
    try:
        result = await telegram_verification.verify(msg.from_user.id, channels.telegram_reference(row))
    except CredentialError:
        await msg.answer("❌ Connect your own Telegram bot first with /connectbot.")
        return
    ok, _ = _apply_verification(row, result, source="scan")
    if not ok:
        await msg.answer("❌ <b>Verification failed</b>\n" + "\n".join(result.reasons), parse_mode="HTML")
        return
    if result.member_count is not None:
        channels.update(msg.from_user.id, row["chat_id"], size=result.member_count,
                        telegram_member_count=result.member_count,
                        telegram_member_count_source="telegram_api",
                        telegram_member_count_checked_at=result.checked_at)
        await msg.answer(f"📊 <b>Channel statistics</b>\n\n{escape(str(row.get('username')))}\n"
                         f"Members/subscribers: <b>{result.member_count:,}</b>\n"
                         f"Source: Telegram Bot API\nChecked: {dt.datetime.fromtimestamp(result.checked_at, dt.timezone.utc).isoformat()}\n"
                         f"Verification: {'✅ Passed' if result.eligible else '❌ Failed'}", parse_mode="HTML")
    else:
        await msg.answer("⚠️ Telegram did not return a current member count. No statistic was invented.")


@dp.callback_query(lambda c: c.data == "stats:all")
async def stats_all_callback(cb: types.CallbackQuery):
    rows = channels.mine(cb.from_user.id)
    if not rows:
        await cb.answer("Register a destination first.", show_alert=True)
        return
    lines = ["📊 <b>TELEGRAM STATISTICS</b>", ""]
    for row in rows:
        try:
            result = await telegram_verification.verify(cb.from_user.id, channels.telegram_reference(row))
            ok, _ = _apply_verification(row, result, source="scan")
            if not ok:
                lines.append(f"❌ {escape(str(row.get('username')))}: {'; '.join(result.reasons)}")
                continue
            if result.member_count is None:
                lines.append(f"⚠️ {escape(str(row.get('username')))}: member count unavailable")
                continue
            channels.update(cb.from_user.id, row["chat_id"], size=result.member_count,
                            telegram_member_count=result.member_count,
                            telegram_member_count_source="telegram_api",
                            telegram_member_count_checked_at=result.checked_at)
            lines.append(f"✅ {escape(str(row.get('username')))}: <b>{result.member_count:,}</b> members · Telegram API")
        except CredentialError:
            lines.append("❌ Connect your own bot first with /connectbot.")
            break
        except Exception:
            lines.append(f"⚠️ {escape(str(row.get('username')))}: Telegram refresh failed")
    await cb.message.edit_text("\n".join(lines), parse_mode="HTML",
                               reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                                   [InlineKeyboardButton(text="🔄 Refresh", callback_data="stats:all")],
                                   [InlineKeyboardButton(text="⬅️ Back", callback_data="menu:hub")]]))
    await cb.answer()


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
            result = await telegram_verification.verify(msg.from_user.id, channels.telegram_reference(row))
            checks = result.checks or {}
            icon = lambda name: "✅" if checks.get(name) == "passed" else ("⚠️" if checks.get(name) == "unavailable" else "❌")
            lines.append(f"🔍 Verifying {row['username']}")
            lines.append(f"{icon('destination')} Resolving Telegram destination")
            lines.append(f"{icon('bot_membership')} Checking bot membership")
            lines.append(f"{icon('administrator')} Checking administrator status")
            lines.append(f"{icon('permissions')} Checking required permissions")
            ok, _ = _apply_verification(row, result, source="scan")
            if not ok:
                lines.append(f"❌ {row['username']}: {'; '.join(result.reasons)}")
                continue
            lines.append(f"{icon('accessibility')} Checking destination accessibility")
            lines.append(f"{icon('member_count')} Retrieving member count")
            lines.append(f"{icon('eligibility')} Checking eligibility")
            count = result.member_count or 0
            stats.record(row["chat_id"], subscribers=count)
            ledger.register(row["username"], count)
            lines.append(f"✅ {row['username']}: verified; {count:,} members/subscribers (Telegram API)")
        except Exception as exc:
            info = classify_telegram_error(exc)
            try:
                destination_states.transition(msg.from_user.id, row["chat_id"],
                                              info.verification_state or VState.DEGRADED,
                                              reason=f"reconciliation failed: {info.kind.value}", source="scan",
                                              verification_reasons=[f"reconciliation failed: {info.kind.value}"],
                                              last_error_kind=info.kind.value)
            except IllegalTransition:
                pass
            lines.append(f"⚠️ {row['username']}: reconciliation failed ({info.kind.value})")
    lines.append("\nViews/reactions/forwards are recorded only when a real stats provider "
                 "or channel observation is available; no numbers are invented.")
    await msg.answer("\n".join(lines))


def _credibility_text(uid: int) -> str:
    rows = channels.mine(uid)
    if not rows:
        return "🌱 No connected destinations yet. Register a channel first."
    lines = ["📊 <b>CREDIBILITY</b>", ""]
    for row in rows:
        destination = row.get("chat_id") or row.get("username")
        explanation = credibility_snapshots.explain(destination)
        lines.append(
            f"📢 {escape(str(row.get('username') or destination))}\n"
            f"Tier: {explanation['status']} · Score: {explanation.get('score', '—')}\n"
            f"Confidence: {explanation.get('confidence', 0):.0%} · "
            f"Observations: {explanation.get('sample_size', 0)}\n"
            f"{explanation['message']}\n"
        )
    return "\n".join(lines)


@dp.callback_query(lambda c: c.data == "menu:credibility")
async def credibility_menu(cb: types.CallbackQuery):
    await cb.message.edit_text(
        _credibility_text(cb.from_user.id), parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Refresh", callback_data="menu:credibility")],
            [InlineKeyboardButton(text="📜 Score history", callback_data="credibility:history")],
            [InlineKeyboardButton(text="⬅️ Back", callback_data="menu:hub")],
        ]),
    )
    await cb.answer()


@dp.message(Command("credibility"))
async def credibility_cmd(msg: types.Message):
    rows = channels.mine(msg.from_user.id)
    if not rows:
        await msg.answer("🌱 No connected destinations yet. Register a channel first.")
        return
    lines = ["📊 <b>CREDIBILITY</b>", ""]
    for row in rows:
        destination = row.get("chat_id") or row.get("username")
        explanation = credibility_snapshots.explain(destination)
        lines.append(
            f"📢 {escape(str(row.get('username') or destination))}\n"
            f"Tier: {explanation['status']} · Score: {explanation.get('score', '—')}\n"
            f"Confidence: {explanation.get('confidence', 0):.0%} · "
            f"Observations: {explanation.get('sample_size', 0)}\n"
            f"{explanation['message']}\n"
        )
    await msg.answer("\n".join(lines), parse_mode="HTML")


@dp.message(Command("balance"))
async def balance_cmd(msg: types.Message):
    u = _uname(msg)
    b = ledger.balance(u)
    if ledger._is_exempt(u):
        await msg.answer(f"👑 <b>OWNER WALLET</b>\n\n"
                         f"You are exempt from {MINT_NAME} costs, daily caps, and submission funnels.",
                         parse_mode="HTML")
        return
    await msg.answer(
        balance_line(b["balance"], b["earned"], b["spent"]) + "\n\n"
        f"<b>How {MINT_NAME} works</b>\n"
        f"• Earn {amount(1)} when you complete another member's post.\n"
        f"• Spend {amount(1)} for each post → channel delivery.\n"
        "• One post sent to five destinations costs five units.",
        parse_mode="HTML")


# admins log in via a one-time invite code (must have contacted the owner first)
@dp.message(Command("outbox"))
async def outbox_admin_cmd(msg: types.Message):
    if not _can_panel(msg.from_user.id, "reward"):
        await msg.answer("🔒 Reward owner/admin access required.")
        return
    if not hasattr(ledger, "tx"):
        await msg.answer("Transactional outbox requires transactional Mint mode.")
        return
    events = ledger.tx.recent_outbox_events(status="failed", limit=20)
    if not events:
        await msg.answer("✅ No failed or paused outbox events.")
        return
    lines = ["⚠️ <b>FAILED OUTBOX EVENTS</b>", ""]
    for event in events:
        lines.append(
            f"<code>{event['event_id']}</code> | {event['event_type']} | "
            f"attempts={event['attempts']}\n{event.get('last_error') or 'retryable'}"
        )
    lines.append("\nUse /retryoutbox &lt;event_id&gt; or /pauseoutbox &lt;event_id&gt;.")
    await msg.answer("\n".join(lines), parse_mode="HTML")


@dp.message(Command("retryoutbox"))
async def retry_outbox_cmd(msg: types.Message):
    if not _can_panel(msg.from_user.id, "reward"):
        await msg.answer("🔒 Reward owner/admin access required.")
        return
    parts = (msg.text or "").split()
    if len(parts) != 2 or not hasattr(ledger, "tx"):
        await msg.answer("Usage: /retryoutbox <event_id>")
        return
    ok = ledger.tx.retry_outbox(parts[1])
    if ok:
        _record_outbox_admin_action(msg.from_user.id, "OUTBOX_RETRY", parts[1], "manual retry command")
    await msg.answer("✅ Event returned to the retry queue." if ok else "Event not found or not failed.")


@dp.message(Command("pauseoutbox"))
async def pause_outbox_cmd(msg: types.Message):
    if not _can_panel(msg.from_user.id, "reward"):
        await msg.answer("🔒 Reward owner/admin access required.")
        return
    parts = (msg.text or "").split()
    if len(parts) != 2 or not hasattr(ledger, "tx"):
        await msg.answer("Usage: /pauseoutbox <event_id>")
        return
    ok = ledger.tx.fail_outbox(parts[1], "paused by administrator", permanent=True)
    if ok:
        _record_outbox_admin_action(msg.from_user.id, "OUTBOX_PAUSE", parts[1], "manual pause command")
    await msg.answer("⏸ Event paused safely." if ok else "Event not found or not processing.")


@dp.message(Command("broadcastpreview"))
async def broadcast_preview_cmd(msg: types.Message):
    if not _can_panel(msg.from_user.id, "reward"):
        await msg.answer("🔒 Reward owner/admin access required.")
        return
    text = (msg.text or "").partition(" ")[2].strip()
    if not text:
        await msg.answer("Usage: /broadcastpreview <explicit message text>")
        return
    recipients = users.list_active_user_ids() if users is not None else []
    campaign_id = broadcasts.create_campaign(
        bot_scope="reward", created_by=msg.from_user.id,
        payload={"text": text, "audience": "known_reward_members"},
    )
    await msg.answer(
        "📣 <b>REWARD BROADCAST PREVIEW</b>\n\n"
        f"Campaign: <code>{campaign_id}</code>\n"
        f"Recipients eligible for queueing: {len(set(recipients))}\n\n"
        "No messages have been sent. Review the text and explicitly queue it with:\n"
        f"<code>/broadcastqueue {campaign_id}</code>",
        parse_mode="HTML",
    )


@dp.message(Command("broadcastqueue"))
async def broadcast_queue_cmd(msg: types.Message):
    if not _can_panel(msg.from_user.id, "reward"):
        await msg.answer("🔒 Reward owner/admin access required.")
        return
    parts = (msg.text or "").split()
    if len(parts) != 2:
        await msg.answer("Usage: /broadcastqueue <campaign_id>")
        return
    campaign = broadcasts.get_campaign(parts[1])
    if not campaign or campaign.get("bot_scope") != "reward":
        await msg.answer("Reward campaign not found.")
        return
    try:
        result = broadcast_service.queue_reward(parts[1]) if broadcast_service is not None else {"new_deliveries": 0}
        inserted = result["new_deliveries"]
    except (KeyError, ValueError) as exc:
        await msg.answer(str(exc))
        return
    if hasattr(ledger, "tx"):
        ledger.tx.add_audit_event(
            actor_type="admin", actor_id=str(msg.from_user.id),
            action="REWARD_BROADCAST_QUEUED", object_type="broadcast_campaign",
            object_id=parts[1], reason=f"{inserted} recipients queued",
        )
    await msg.answer(
        f"✅ Reward campaign queued.\nCampaign: <code>{parts[1]}</code>\n"
        f"New deliveries: {inserted}\n\nDelivery will use the durable retry queue.",
        parse_mode="HTML",
    )


@dp.message(Command("broadcaststatus"))
async def broadcast_status_cmd(msg: types.Message):
    if not _can_panel(msg.from_user.id, "reward"):
        await msg.answer("🔒 Reward owner/admin access required.")
        return
    parts = (msg.text or "").split()
    if len(parts) != 2:
        await msg.answer("Usage: /broadcaststatus <campaign_id>")
        return
    summary = broadcasts.campaign_summary(parts[1])
    if not summary:
        await msg.answer("Campaign not found.")
        return
    deliveries = summary["deliveries"]
    await msg.answer(
        f"📣 Campaign <code>{parts[1]}</code>\nStatus: {summary['status']}\n" +
        "\n".join(f"{key}: {value}" for key, value in deliveries.items()) +
        "\n\nUse /broadcastpause, /broadcastresume, or /broadcastcancel as needed.",
        parse_mode="HTML",
    )


@dp.message(Command("broadcastpause"))
async def broadcast_pause_cmd(msg: types.Message):
    if not _can_panel(msg.from_user.id, "reward"):
        await msg.answer("🔒 Reward owner/admin access required.")
        return
    parts = (msg.text or "").split()
    ok = len(parts) == 2 and broadcasts.pause(parts[1])
    await msg.answer("⏸ Campaign paused." if ok else "Campaign not found or not pausable.")


@dp.message(Command("broadcastresume"))
async def broadcast_resume_cmd(msg: types.Message):
    if not _can_panel(msg.from_user.id, "reward"):
        await msg.answer("🔒 Reward owner/admin access required.")
        return
    parts = (msg.text or "").split()
    ok = len(parts) == 2 and broadcasts.resume(parts[1])
    await msg.answer("▶️ Campaign resumed." if ok else "Campaign not found or not paused.")


@dp.message(Command("broadcastcancel"))
async def broadcast_cancel_cmd(msg: types.Message):
    if not _can_panel(msg.from_user.id, "reward"):
        await msg.answer("🔒 Reward owner/admin access required.")
        return
    parts = (msg.text or "").split()
    cancelled = broadcasts.cancel(parts[1]) if len(parts) == 2 else 0
    await msg.answer(f"🛑 Campaign cancelled; {cancelled} deliveries stopped." if len(parts) == 2 else "Usage: /broadcastcancel <campaign_id>")


@dp.message(Command("exportsecurityaudit"))
async def export_security_audit(msg: types.Message):
    if not _can_panel(msg.from_user.id, "reward"):
        await msg.answer("🔒 Reward owner/admin access required.")
        return
    if not hasattr(ledger, "tx"):
        await msg.answer("Transactional security audit export requires transactional Mint mode.")
        return
    parts = (msg.text or "").split()
    fmt = parts[1].lower() if len(parts) > 1 else "csv"
    if fmt not in {"csv", "json"}:
        await msg.answer("Usage: /exportsecurityaudit [csv|json]")
        return
    events = ledger.tx.recent_audit_events(object_type="admin_security", limit=10000)
    if fmt == "json":
        data = json.dumps(events, indent=2, sort_keys=True).encode("utf-8")
        filename = "admin-security-audit.json"
    else:
        output = io.StringIO()
        fields = ["audit_id", "created_at", "actor_type", "actor_id", "action", "object_type", "object_id", "reason"]
        writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(events)
        data = output.getvalue().encode("utf-8")
        filename = "admin-security-audit.csv"
    await msg.answer_document(types.BufferedInputFile(data, filename=filename),
                              caption=f"Admin security audit export ({len(events)} events)")


@dp.message(Command("auditretention"))
async def audit_retention_cmd(msg: types.Message):
    if not _can_panel(msg.from_user.id, "reward"):
        await msg.answer("🔒 Reward owner/admin access required.")
        return
    if not hasattr(ledger, "tx"):
        await msg.answer("Transactional audit retention requires transactional Mint mode.")
        return
    parts = (msg.text or "").split()
    try:
        days = int(parts[1]) if len(parts) > 1 else 365
        if days < 1 or days > 3650:
            raise ValueError
    except ValueError:
        await msg.answer("Usage: /auditretention [days 1-3650]")
        return
    report = ledger.tx.audit_retention_report(older_than_days=days)
    lines = [
        "🗄 <b>AUDIT RETENTION PLAN</b>", "",
        f"Older than: {days} days",
        f"Total events: {report['total_events']}",
        f"Archival candidates: {report['archival_candidates']}",
        f"Oldest event: {report['oldest_event'] or 'none'}",
        "", "Candidates by object type:",
    ]
    lines.extend(f"• {item['object_type']}: {item['count']}" for item in report["candidates_by_type"])
    lines.append("\nNo destructive action was taken. Export and independently archive before any future deletion policy.")
    await msg.answer("\n".join(lines), parse_mode="HTML")


@dp.message(Command("audithealth"))
async def audit_health_cmd(msg: types.Message):
    if not _can_panel(msg.from_user.id, "reward"):
        await msg.answer("🔒 Reward owner/admin access required.")
        return
    if not hasattr(ledger, "tx"):
        await msg.answer("Transactional audit health requires transactional Mint mode.")
        return
    health = ledger.tx.audit_health()
    db_size = os.path.getsize(ledger.tx.path) if os.path.exists(ledger.tx.path) else 0
    size_kb = db_size / 1024
    lines = [
        "🩺 <b>AUDIT STORAGE HEALTH</b>",
        "",
        f"Database size: {size_kb:.1f} KB",
        f"Total audit events: {health['total']}",
        f"Channel permission events: {health['channel_events']}",
        f"Oldest timestamp: {health['oldest'] or 'none'}",
        f"Newest timestamp: {health['newest'] or 'none'}",
        "",
        "Events by action:",
    ]
    lines.extend(f"• {item['action']}: {item['count']}" for item in health["actions"][:10])
    lines.extend(["", "Primary audit history is append-only; export before any future archival."])
    await msg.answer("\n".join(lines), parse_mode="HTML")


@dp.message(Command("exportaudit"))
async def export_channel_audit(msg: types.Message):
    """Export channel permission history without mutating the append-only log."""
    if not _can_panel(msg.from_user.id, "reward"):
        await msg.answer("🔒 Reward owner/admin access required.")
        return
    if not hasattr(ledger, "tx"):
        await msg.answer("Transactional audit export requires transactional Mint mode.")
        return
    parts = (msg.text or "").split()
    fmt = parts[1].lower() if len(parts) > 1 else "csv"
    if fmt not in {"csv", "json"}:
        await msg.answer("Usage: /exportaudit [csv|json] [start YYYY-MM-DD] [end YYYY-MM-DD]")
        return
    try:
        start_at = None
        end_at = None
        if len(parts) >= 3:
            start_at = int(dt.datetime.strptime(parts[2], "%Y-%m-%d").timestamp()) - 1
        if len(parts) >= 4:
            # End dates are inclusive for administrators; query through midnight
            # of the following day.
            end_at = int((dt.datetime.strptime(parts[3], "%Y-%m-%d") + dt.timedelta(days=1)).timestamp())
        if len(parts) > 4:
            raise ValueError
    except ValueError:
        await msg.answer("Usage: /exportaudit [csv|json] [start YYYY-MM-DD] [end YYYY-MM-DD]")
        return
    events = ledger.tx.recent_audit_events(
        object_type="channel", after_created_at=start_at,
        before_created_at=end_at, limit=10000)
    if fmt == "json":
        data = json.dumps(events, indent=2, sort_keys=True).encode("utf-8")
        filename = "channel-audit.json"
    else:
        output = io.StringIO()
        fields = ["audit_id", "created_at", "actor_type", "actor_id",
                  "action", "object_type", "object_id", "reason"]
        writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(events)
        data = output.getvalue().encode("utf-8")
        filename = "channel-audit.csv"
    await msg.answer_document(types.BufferedInputFile(data, filename=filename),
                              caption=f"Channel permission audit export ({len(events)} events)")


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
@dp.callback_query(lambda c: c.data == "credibility:history")
async def credibility_history(cb: types.CallbackQuery):
    rows = channels.mine(cb.from_user.id)
    lines = ["📜 <b>CREDIBILITY HISTORY</b>", ""]
    for row in rows:
        destination = row.get("chat_id") or row.get("username")
        history = credibility_snapshots.history(destination, limit=5)
        lines.append(f"📢 {escape(str(row.get('username') or destination))}")
        if not history:
            lines.append("• No observations yet")
            continue
        for index, snapshot in enumerate(history):
            previous = history[index + 1]["score"] if index + 1 < len(history) else None
            delta = ""
            if previous is not None:
                delta = f" ({snapshot['score'] - previous:+.2f})"
            lines.append(
                f"• {snapshot['tier']} · {snapshot['score']:.2f}{delta} · "
                f"confidence {snapshot['confidence']:.0%} · {snapshot['created_at']}"
            )
        lines.append("")
    await cb.message.edit_text(
        "\n".join(lines), parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Back to credibility", callback_data="menu:credibility")],
        ]),
    )
    await cb.answer()


@dp.callback_query(lambda c: c.data and c.data.startswith("menu:"))
async def menu_nav(cb: types.CallbackQuery):
    _, which = cb.data.split(":", 1)
    uid = cb.from_user.id
    role = roles.role(uid)
    if which == "hub":
        await cb.message.edit_text("Choose an option:", reply_markup=_menu(role, uid))
    elif which == "cancel":
        _session_clear(uid)
        await cb.message.edit_text("❌ Cancelled. Nothing was submitted.",
                                   reply_markup=_menu(role, uid))
    elif which == "tasks":
        await show_tasks_message(cb.message, uid, edit=True)
        await cb.answer()
        return
    elif which == "available":
        rows = available.available(uid, limit=10)
        count = available.count(uid)
        if not rows:
            text = "📭 <b>NO POSTS AVAILABLE</b>\n\nCheck back when a matching post enters your queue."
        else:
            shown = "\n".join(f"• {r['id']} · {r.get('category', 'General')}" +
                              (" · 👑 owner priority" if r.get('owner') else "") for r in rows)
            text = (f"📬 <b>POSTS AVAILABLE: {('99+' if count > 99 else count)}</b>\n\n" + shown)
        await cb.message.edit_text(text, parse_mode="HTML",
                                   reply_markup=_menu(role, uid))
        await cb.answer()
        return
    elif which == "wallet":
        u = _uname(cb)
        b = ledger.balance(u)
        if ledger._is_exempt(u):
            text = f"👑 <b>OWNER WALLET</b>\n\nExempt from {MINT_NAME} costs and limits."
        else:
            text = (balance_line(b['balance'], b['earned'], b['spent']) + "\n\n"
                    f"Earn {amount(1)} by completing another member's post.\n"
                    f"Spend {amount(1)} per post → channel delivery.")
        await cb.message.edit_text(text, parse_mode="HTML",
                                   reply_markup=back_btn_q("menu:hub"))
    elif which in ("add_channel", "add_group"):
        kind = "group" if which == "add_group" else "channel"
        _session_set(uid, destination_phase="name", destination_kind=kind)
        label = "GROUP" if kind == "group" else "CHANNEL"
        await cb.message.edit_text(
            f"➕ <b>ADD {label}</b>\n\n"
            f"Send the {kind}'s @username or public link.\n"
            "Telegram will retrieve its current member count automatically.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="❌ Cancel", callback_data="menu:cancel")],
                [InlineKeyboardButton(text="⬅️ Back", callback_data="menu:channels")],
            ]))
    elif which == "channels":
        rows = channels.mine(uid)
        if not rows:
            text = "📂 <b>MY CHANNELS / GROUPS</b>\n\nNo destinations registered yet."
        else:
            text = "📂 <b>MY CHANNELS / GROUPS</b>\n\n" + "\n".join(
                f"• {r.get('username')} · {r.get('kind')} · performance tier {r.get('band')} · "
                f"{('✅ VERIFIED' if r.get('verified_state') == 'VERIFIED' else '⚠️ ' + str(r.get('verified_state', 'REGISTERED')))}"
                for r in rows)
        await cb.message.edit_text(
            text, parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="➕ Add channel", callback_data="menu:add_channel"),
                 InlineKeyboardButton(text="➕ Add group", callback_data="menu:add_group")],
                [InlineKeyboardButton(text="📊 Check Stats", callback_data="stats:all")],
                [InlineKeyboardButton(text="⬅️ Back", callback_data="menu:hub")],
            ]))
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
            f"  Size: {m.get('size',0)}  Performance tier: {perf.score(_uname(cb))['band']}\n"
            f"  Daily cap: {daily_post_cap(m.get('size',0), perf.score(_uname(cb))['band'], m.get('status','ACTIVE'), m.get('is_owner',False), connected=_is_connected(_uname(cb)))}")
        return
    elif which == "audit":
        if not _can_panel(uid, "reward"):
            await cb.answer("Owner/admin only.", show_alert=True)
            return
        await cb.message.edit_text("📁 <b>AUDIT &amp; REPORTS</b>\n\nSelect a queue to inspect.",
                                   parse_mode="HTML", reply_markup=ui.audit_menu(_audit_counts()))
        await cb.answer()
        return
    elif which == "rank":
        if not _can_panel(uid, "reward"):
            await cb.answer("Owner/admin only.", show_alert=True)
            return
        await cb.message.edit_text("Use your Admin panel to view the rank.",
                                   reply_markup=ui.role_menu(role))
        return
    await cb.answer()


async def submit_menu(cb: types.CallbackQuery):
    """Step 1 of submission: accept terms, then pick a category."""
    await cb.message.edit_text(
        TERMS_TEXT,
        parse_mode="HTML",
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
                                   reply_markup=_menu(roles.role(cb.from_user.id), cb.from_user.id))
    await cb.answer()


@dp.callback_query(lambda c: c.data and c.data.startswith("cat:"))
async def pick_category(cb: types.CallbackQuery):
    cat = cb.data.split(":", 1)[1]
    if cat not in POST_CATEGORIES:
        await cb.answer("Unknown category.", show_alert=True)
        return
    _session_set(cb.from_user.id, cat=cat)
    # Delivery style is NOT a sender choice: a destination auto-posts only if its
    # owner enabled Auto-post and a legal relay route exists (decision 2026-09-09 #1).
    # Pinning forwarded posts is intentionally unavailable.
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔔 Loud", callback_data="ntf:loud"),
         InlineKeyboardButton(text="🔕 Silent", callback_data="ntf:silent")],
        [InlineKeyboardButton(text="✅ Submit (you must FORWARD the post next)",
                              callback_data="style:submit")],
        [InlineKeyboardButton(text="⬅️ Back", callback_data="menu:hub"),
         InlineKeyboardButton(text="❌ Cancel", callback_data="menu:cancel")],
    ])
    await cb.message.edit_text(
        f"Category: <b>{escape(cat)}</b>\n\nChoose notification:\n\n"
        "Loud delivery is for groups. Silent delivery can target either a group or a channel.\n"
        "Destinations whose owner enabled ⚡ Auto-post receive your forward automatically; "
        "others get an offer to accept.",
        reply_markup=kb, parse_mode="HTML")
    await cb.answer()


@dp.callback_query(lambda c: c.data and c.data.startswith("style:"))
async def pick_style(cb: types.CallbackQuery):
    """Only `style:submit` remains; legacy style:fwd/direct taps are explained away."""
    if cb.data.split(":")[1] == "submit":
        await cb.message.edit_text(
            "Now **forward** the post you want to distribute here (so its "
            "attribution stays intact).\n\n" + gate.attribution_tip())
        await cb.answer()
        return
    await cb.answer("Delivery style is decided per destination by its owner (⚡ Auto-post in /mychannels).",
                    show_alert=True)


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
    access_reason = channels.participation_block_reason(u)
    s = perf.score(u)
    cap = daily_post_cap(m.get("size", 0), s["band"], m.get("status", "ACTIVE"),
                         m.get("is_owner", False), connected=_is_connected(u))
    await cb.message.edit_text(
        f"📊 <b>DAILY DELIVERY CAP</b>\n\n"
        f"📌 <b>Destination:</b> {u}\n"
        f"👥 <b>Audience:</b> {m.get('size',0):,}\n"
        f"📈 <b>Performance tier:</b> {s['band']} · <b>Status:</b> {m.get('status','ACTIVE')}\n"
        f"📤 <b>Available today:</b> {cap if cap != -1 else 'UNLIMITED'} post(s)\n"
        f"{'⚠️ <b>Participation blocked:</b> ' + escape(access_reason) if access_reason else '✅ Verification is current.'}\n\n"
        "Your limit reflects audience size and real performance.\n"
        "Better delivery and engagement can improve your performance tier.",
        parse_mode="HTML",
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

    blocked = _enforcement_block(uid, "distribution", u)
    if blocked:
        await msg.answer(blocked)
        return
    sess = _session(uid)
    # 1) terms accepted? (per user — never a single global flag)
    if not sess.get("accepted_terms"):
        await msg.answer("First accept the posting terms: /start → Submit a post.",
                         reply_markup=_menu(roles.role(uid), uid))
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
                         m.get("is_owner", False), connected=_is_connected(u))
    left = ledger.cap_left(u, cap)
    if left == 0:
        reason = channels.participation_block_reason(u)
        if reason:
            await msg.answer("⛔ Participation blocked: " + reason +
                             "\nRun /scan after correcting the Telegram setup.")
        else:
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
                  **relay.Source.from_message(msg).as_record(),
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
        await msg.answer(f"{MINT_ICON} <b>NO MINT AVAILABLE TODAY</b>\n\n"
                         f"Share another member's post to earn {amount(1)}.", parse_mode="HTML")
        return
    row = [InlineKeyboardButton(text=str(n), callback_data=f"s:{n}")
           for n in range(1, max_pairs + 1)]
    kb = InlineKeyboardMarkup(inline_keyboard=[row[i:i + 3] for i in range(0, len(row), 3)])
    await msg.answer(f"{MINT_ICON} <b>CHOOSE YOUR DELIVERY COUNT</b>\n"
                     f"How many post → channel pairs do you want?\n\n"
                     f"Price: {amount(1)} per post → channel pair.\n"
                     f"Wallet: {amount(balance)}\n"
                     f"Daily capacity remaining: {left} slot(s).\n\n"
                     f"Example: one post → five channels costs {amount(5)}.",
                     parse_mode="HTML",
                     reply_markup=kb)
    _session_set(uid, pending=_pending_from(msg, u, cat, sess))


def _submitted_text(msg) -> str:
    """The text the gate should read: a forwarded photo/video carries its words
    in `caption`, not `text`. Reading only `text` let captioned scam posts walk
    straight through the gate."""
    return (getattr(msg, "text", None) or getattr(msg, "caption", None) or "")


def _pending_from(msg, u: str, cat: str, sess: dict, owner_exempt: bool = False) -> dict:
    """Snapshot of the post being distributed.

    We remember BOTH the copy the member forwarded into this bot's inbox
    (only the platform bot can re-forward that) and the origin channel message
    (only a bot that administers the origin can forward that). `relay.resolve`
    picks the legal route per destination; see relay.py.
    """
    return {
        "user": u, "tier": ledger.balance(u)["tier"], "post_type": cat,
        "source": forward_source(msg),
        "source_text": _submitted_text(msg),
        "style": sess.get("style", "fwd"),
        "ntf": sess.get("ntf", "silent"),
        "from_chat_id": msg.chat.id,
        "from_message_id": msg.message_id,
        **relay.Source.from_message(msg).as_record(),
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
            [InlineKeyboardButton(text="📋 Create marketplace task", callback_data="task:create")],
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
        # Owner exemption removes economic limits, not Telegram safety gates.
        if not _is_connected(username):
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
            await cb.answer("No matching channel in your performance tier accepts "
                            f"'{post_type}' right now. Nothing was charged.",
                            show_alert=True)
            return
        # The SENDER's daily cap is what limits posting — the old build charged the
        # cap to each RECEIVER instead, so the sender's cap never applied at all.
        m = ledger._m(sender)
        cap = daily_post_cap(m.get("size", 0), perf.score(sender)["band"],
                             m.get("status", "ACTIVE"), m.get("is_owner", False), connected=_is_connected(sender))
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
        spend_key = None
        if hasattr(ledger, "tx"):
            spend_key = (f"post:{sender}:{pending.get('from_chat_id')}:{pending.get('from_message_id')}"
                         f":{','.join(targets)}")
        if spend_key and ledger.tx.post_order_by_key(spend_key):
            _session_clear(uid, "pending")
            await cb.answer("This posting request was already processed.", show_alert=True)
            return
        if spend_key:
            ok, why = ledger.spend(sender, len(targets), idempotency_key=spend_key,
                         source_id=f"{pending.get('from_chat_id')}:{pending.get('from_message_id')}")
        else:
            ok, why = ledger.spend(sender, len(targets))
        if not ok:
            await cb.answer(why, show_alert=True)
            return
        ok_cap, why_cap = ledger.consume_cap(sender, cap, len(targets))
        if not ok_cap:                      # cap was eaten between the two checks
            if hasattr(ledger, "tx") and spend_key:
                ledger.refund(sender, len(targets), idempotency_key=f"{spend_key}:refund")
            else:
                ledger.refund(sender, len(targets))  # give back what we just charged
            await cb.answer(why_cap, show_alert=True)
            return

    _session_clear(uid, "pending")          # one submission = one distribution
    source = pending.get("source", "unknown")
    style = pending.get("style", "fwd")
    ntf = pending.get("ntf", "loud") == "silent"
    auto_n = sum(1 for t in targets if _auto_post_enabled(t))
    await cb.message.answer(
        f"Distributing to {len(targets)} destination(s): "
        f"⚡ auto-posted to {auto_n} · 📨 offered to {len(targets) - auto_n}")
    for target in targets:
        perf.mark_offered(target)
        if _auto_post_enabled(target):
            await direct_deliver(cb, sender, target, pending, style=style, silent=ntf)
        else:
            audit.record(bot="reward", sender=sender, source=source,
                         post_type=post_type,
                         target_channel=target, mode="chain", status="offered",
                         forward_valid=True, style=style,
                         source_chat_id=pending.get("from_chat_id"),
                         source_message_id=pending.get("from_message_id"),
                         origin_chat_id=pending.get("origin_chat_id"),
                         origin_message_id=pending.get("origin_message_id"),
                         origin_kind=pending.get("origin_kind"))
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✅ Agree (forward it)", callback_data=f"chain:agree:{sender}"),
                 InlineKeyboardButton(text="❌ Disagree (skip)", callback_data=f"chain:dis:{sender}")],
                [InlineKeyboardButton(text="🚩 Report as scam/fraud", callback_data=f"report:{sender}:{target}")],
            ])
            try:
                await bot.send_message(target,
                                       f"{sender} has a {style} post ({post_type}) "
                                       f"for your performance tier. Agree to forward, skip, or report.",
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
    destination = channels.get(target)
    if not destination or not channels.participation_allowed(target):
        await cb.message.answer(f"⚠ Destination {target} is not currently verified.")
        return
    try:
        _sent, plan = await _relay_forward(relay.Source.from_record(pending), target, silent=silent)
        perf.mark_posted(target)
        audit.record(bot="reward", sender=sender, target_channel=target,
                     mode="direct", status="delivered", forward_valid=True, style=style, relay=plan.route)
        await cb.message.answer(f"⚡ Auto-posted to {target} (direct, {style}).")
    except RelayUnavailable as e:
        audit.record(bot="reward", sender=sender, target_channel=target,
                     mode="direct", status="failed", forward_valid=False, relay="unavailable",
                     error=str(e)[:120])
        await cb.message.answer(f"⚠ Direct delivery to {target} isn't possible: {e}. "
                                "The owner can add the platform bot as admin, or accept it as a chain offer.")
    except Exception as e:
        info = classify_telegram_error(e)
        audit.record(bot="reward", sender=sender, target_channel=target,
                     mode="direct", status="offered", forward_valid=True,
                     error=f"{info.kind.value}: {str(e)[:100]}")
        await cb.message.answer(f"Direct delivery to {target} failed ({info.kind.value}). Fell back to offer.")


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
        offered = audit.latest_for(sender=sender, target_channel=target, status="offered")
        if not offered:
            # Backward-compatible completion for offers created by an older
            # process before delivery rows were persisted. New UI offers always
            # have a row and follow the validated forwarding path below.
            ledger.earn(target)
            perf.mark_posted(target)
            audit.record(bot="reward", sender=sender, target_channel=target,
                         mode="chain", status="agreed", forward_valid=True,
                         legacy_offer=True)
            await cb.message.answer(f"✅ <b>POST SHARED</b>\n\nYou earned {amount(1)}.", parse_mode="HTML")
            await cb.answer()
            return
        if offered.get("source_chat_id") and offered.get("source_message_id"):
            # forwardMessage preserves keyboard, caption, media and attribution.
            # Which bot forwards is decided by relay.resolve — the agreeing
            # owner's bot can only forward from an origin channel it administers.
            try:
                # The platform bot posted this offer inside `target`, so it acts as itself here.
                _sent, plan = await _relay_forward(relay.Source.from_record(offered), target, platform_may_try=True)
            except RelayUnavailable as exc:
                audit.record(bot="reward", sender=sender, target_channel=target, mode="chain",
                             status="failed", forward_valid=False, relay="unavailable", error=str(exc)[:120])
                await cb.answer(f"Can't forward to {target}: {str(exc)[:150]}", show_alert=True)
                return
            except Exception as exc:
                await cb.answer(f"Could not forward to {target}: {classify_telegram_error(exc).kind.value}",
                                show_alert=True)
                return
        # Older offers created before source ids were persisted can still be
        # completed and credited; new offers always take the preservation path above.
        ledger.earn(target)
        perf.mark_posted(target)
        audit.update(offered["id"], status="delivered")
        bal = ledger.balance(target)["balance"]
        await cb.message.answer("✅ <b>POST SHARED</b>\n\n"
                                "The original buttons and attribution were preserved.\n"
                                f"Earned: {amount(1)}\nWallet: {amount(bal)}",
                                parse_mode="HTML")
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
    # Mirror into the durable compliance store so the Admin Mini App sees it.
    try:
        enforcement.report(cb.from_user.id, sender, "channel",
                           "receiver flagged as inappropriate/scam/fraud",
                           subject=f"legacy_report:{item['id']}")
    except EnforcementError as exc:
        logging.warning("compliance report mirror failed: %s", exc)
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


@dp.message(lambda m: not (m.text or "").startswith("/")
            and _session(m.from_user.id).get("destination_phase"))
async def destination_capture(msg: types.Message):
    """Button-led registration: add a channel/group without a command."""
    uid = msg.from_user.id
    sess = _session(uid)
    raw = (msg.text or "").strip()
    if sess.get("destination_phase") == "name":
        if raw.startswith("https://t.me/"):
            raw = "@" + raw.rstrip("/").split("/")[-1].split("?")[0]
        if not raw.startswith("@"):
            raw = "@" + raw
        if len(raw) < 2 or " " in raw or raw == "@":
            await msg.answer("Please send a valid @username or public Telegram link.")
            return
        _session_clear(uid)
        try:
            text = await _register_from_telegram(msg, raw,
                                                  kind=sess.get("destination_kind", "channel"))
        except CredentialError:
            text = "❌ Connect your own Telegram bot first with /connectbot, then try again."
        await msg.answer(text, parse_mode="HTML", reply_markup=_menu(roles.role(uid), uid))
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
    if not (channels.participation_allowed(u) and channels.participation_allowed(other)):
        await msg.answer("Both channels must be VERIFIED with their owner's bot as admin (Post Messages) "
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
    """Queue owner notifications transactionally when the Mint backend is active.

    The legacy mode retains the previous best-effort behavior until activation;
    transactional mode makes notification delivery durable and retryable.
    """
    if not roles.owner_user_id or roles.owner_user_id == "0":
        return
    if hasattr(ledger, "tx"):
        ledger.tx.enqueue_outbox(
            event_type="OWNER_NOTIFICATION", recipient_id=roles.owner_user_id,
            payload=text, idempotency_key=f"owner-notify:{hashlib.sha256(text.encode('utf-8')).hexdigest()}",
        )
        return
    asyncio.ensure_future(_notify_owner(text))


async def _process_mint_outbox():
    if not hasattr(ledger, "tx"):
        return
    tx = ledger.tx
    tx.recover_outbox()
    for event in tx.claim_outbox(limit=20):
        try:
            if event["event_type"] == "TASK_MINT_REWARD":
                reward = json.loads(event["payload"])
                ledger.earn(
                    reward["username"], int(reward["amount"]),
                    idempotency_key=reward["idempotency_key"],
                )
                await bot.send_message(
                    int(event["recipient_id"]),
                    f"✅ Task reward credited: 🪙 {int(reward['amount'])} Mint\\n"
                    f"Task: {reward['task_id']}",
                )
            else:
                await bot.send_message(int(event["recipient_id"]), event["payload"])
            tx.complete_outbox(event["event_id"])
        except Exception as exc:
            delay = min(3600, 30 * (2 ** min(int(event.get("attempts", 1)), 6)))
            tx.fail_outbox(event["event_id"], str(exc), retry_delay=delay)


def _entities_from_payload(text: str, payload: dict) -> list | None:
    from aiogram.types import MessageEntity
    ents = tg_entities.sanitize_entities(text, payload.get("entities") or [])
    return [MessageEntity(**e) for e in ents] or None


async def _process_reward_broadcasts():
    """Deliver only Reward-scope campaigns through the shared durable queue."""
    # Recover claims abandoned by a crashed poll tick before taking new work.
    broadcasts.recover_stale_processing(older_than_seconds=900, retry_seconds=60)
    marketplace.reconcile()
    if enforcement_gate.safe_mode():
        # Safe mode pauses the machine: leave deliveries pending, claim nothing.
        return
    for delivery in broadcasts.claim(limit=20, bot_scope="reward"):
        try:
            if not admit_delivery(worker_store, "reward", delivery,
                                  rate_per_second=config.WORKER_RATE_PER_SECOND,
                                  burst=config.WORKER_RATE_BURST):
                broadcasts.fail(delivery["delivery_id"], "central delivery rate limit", retry_seconds=1)
                continue
            started = time.perf_counter()
            if not enforcement_gate.check(delivery["recipient_id"], "user", "campaigns"):
                broadcasts.fail(delivery["delivery_id"], "recipient blocked by enforcement", blocked=True)
                worker_store.record("reward", "blocked")
                continue
            campaign = broadcasts.get_campaign(delivery["campaign_id"])
            payload = campaign.get("payload", {}) if campaign else {}
            text = payload.get("text")
            if not text:
                broadcasts.fail(delivery["delivery_id"], "campaign has no explicit text payload", blocked=True)
                continue
            if "entities" in payload:
                # Composed in Telegram or the Mini App: replay Telegram's own entities,
                # no parse_mode, so nothing typed can be misread as markup.
                sent = await bot.send_message(int(delivery["recipient_id"]), text, parse_mode=None,
                                              entities=_entities_from_payload(text, payload))
            else:
                # Legacy drafts created before entities were captured.
                sent = await bot.send_message(int(delivery["recipient_id"]), text)
            broadcasts.complete(delivery["delivery_id"], sent.message_id)
            worker_store.record("reward", "delivered", (time.perf_counter() - started) * 1000)
        except Exception as exc:
            worker_store.record("reward", "failed", (time.perf_counter() - started) * 1000 if 'started' in locals() else 0)
            blocked = any(token in str(exc).lower() for token in ("blocked", "chat not found", "deactivated"))
            broadcasts.fail(delivery["delivery_id"], str(exc)[:500], blocked=blocked,
                            retry_seconds=min(3600, 30 * (2 ** min(delivery.get("attempts", 1), 6))))


async def _process_ad_deliveries():
    """Deliver approved, consented ads through the DESTINATION OWNER's bot.

    Every message is labelled as an ad with the disclosed advertiser. Consent
    is re-checked at claim time inside the store; enforcement and safe mode
    are checked here, at the shared boundary."""
    if not ads.enabled or enforcement_gate.safe_mode():
        return
    for delivery in ads.claim(limit=10):
        try:
            campaign = ads.get_campaign(delivery["campaign_id"])
            row = channels.get(delivery["destination_id"])
            if not campaign or not row:
                ads.fail(delivery["delivery_id"], "campaign or destination missing", blocked=True)
                continue
            if not enforcement_gate.check_many([(row.get("owner_id"), "user"), (delivery["destination_id"], "channel")], "campaigns"):
                ads.fail(delivery["delivery_id"], "destination blocked by enforcement", blocked=True)
                continue
            verified, why = await _verify_task_channel(row)
            if not verified:
                ads.fail(delivery["delivery_id"], f"verification: {why}", retry_seconds=1800)
                continue
            text = (f"📢 <b>Ad · {escape(campaign['advertiser_label'])}</b>\n\n"
                    f"{escape(campaign['payload']['text'])}\n\n"
                    f"<i>Sponsored content. This channel opted in to CLICKMINT ads (terms v{campaign['terms_version']}).</i>")
            sent = await _execute_task_payload({"payload": {"text": text}}, verified["chat_id"], int(row["owner_id"]))
            ads.complete(delivery["delivery_id"], sent.message_id)
            audit.record(bot="reward", sender=f"ad:{campaign['campaign_id']}", target_channel=delivery["destination_id"],
                         mode="direct", status="delivered", forward_valid=True)
        except Exception as exc:
            blocked = any(t in str(exc).lower() for t in ("blocked", "chat not found", "not enough rights", "deactivated"))
            ads.fail(delivery["delivery_id"], str(exc)[:500], blocked=blocked,
                     retry_seconds=min(3600, 60 * (2 ** min(int(delivery.get("attempts", 1)), 6))))


async def _notify_owner(text: str):
    try:
        await bot.send_message(int(roles.owner_user_id), text)
    except Exception as e:
        logging.warning("owner notify failed: %s", e)


# ---------------------------------------------------------------------------
# OWNER / ADMIN PANEL: review queue, admins, terms (buttons)
# ---------------------------------------------------------------------------
def _channel_audit_markup(older_cursor: int | None = None):
    buttons = [[InlineKeyboardButton(text="All", callback_data="audit:filter:all"),
                InlineKeyboardButton(text="Active", callback_data="audit:filter:ACTIVE"),
                InlineKeyboardButton(text="Degraded", callback_data="audit:filter:DEGRADED")]]
    known = {}
    for row in channels._items().values():
        chat_id = row.get("chat_id") or row.get("username")
        if chat_id is not None:
            known[str(chat_id)] = row.get("username") or str(chat_id)
    for chat_id, label in list(known.items())[:8]:
        buttons.append([InlineKeyboardButton(
            text=f"📢 {label[:30]}",
            callback_data=f"audit:channel:{chat_id}")])
    if older_cursor is not None:
        buttons.append([InlineKeyboardButton(text="Older events", callback_data=f"audit:older:{older_cursor}")])
    buttons.append([InlineKeyboardButton(text="⬅️ Back", callback_data="menu:admin")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def _channel_audit_text(action_prefix: str | None = None,
                        object_id: str | None = None,
                        before_created_at: int | None = None) -> str:
    scope = f" FOR {object_id}" if object_id else ""
    lines = [f"CHANNEL PERMISSION HISTORY{scope}", ""]
    if hasattr(ledger, "tx"):
        events = ledger.tx.recent_audit_events(
            object_type="channel", action_prefix=action_prefix,
            object_id=object_id, before_created_at=before_created_at, limit=20)
        if not events:
            lines.append("No matching channel transitions.")
        for event in events:
            lines.append(
                f"[{event['created_at']}] {event['object_id']} "
                f"| {event['action']} | {event['reason']}"
            )
    else:
        lines.append("Transactional audit history is not enabled.")
    return "\n".join(lines)


@dp.callback_query(lambda c: c.data and c.data.startswith("audit:filter:"))
async def channel_audit_filter(cb: types.CallbackQuery):
    if not _can_panel(cb.from_user.id, "reward"):
        await cb.answer("Reward admin access required.", show_alert=True)
        return
    value = cb.data.split(":", 2)[2]
    prefix = None if value == "all" else f"CHANNEL_STATUS_{value}"
    text = _channel_audit_text(prefix)
    page = ledger.tx.recent_audit_events(object_type="channel", action_prefix=prefix, limit=20) if hasattr(ledger, "tx") else []
    cursor = min((int(event["created_at"]) for event in page), default=None)
    await cb.message.edit_text(text, reply_markup=_channel_audit_markup(cursor))
    await cb.answer()


@dp.callback_query(lambda c: c.data and c.data.startswith("audit:channel:"))
async def channel_audit_channel_filter(cb: types.CallbackQuery):
    if not _can_panel(cb.from_user.id, "reward"):
        await cb.answer("Reward admin access required.", show_alert=True)
        return
    object_id = cb.data.split(":", 2)[2]
    page = ledger.tx.recent_audit_events(object_type="channel", object_id=object_id, limit=20) if hasattr(ledger, "tx") else []
    cursor = min((int(event["created_at"]) for event in page), default=None)
    await cb.message.edit_text(
        _channel_audit_text(object_id=object_id),
        reply_markup=_channel_audit_markup(cursor),
    )
    await cb.answer()


@dp.callback_query(lambda c: c.data and c.data.startswith("audit:older:"))
async def channel_audit_older(cb: types.CallbackQuery):
    if not _can_panel(cb.from_user.id, "reward"):
        await cb.answer("Reward admin access required.", show_alert=True)
        return
    try:
        cursor = int(cb.data.split(":", 2)[2])
    except ValueError:
        await cb.answer("Invalid audit cursor.", show_alert=True)
        return
    events = ledger.tx.recent_audit_events(
        object_type="channel", before_created_at=cursor, limit=20)
    text = _channel_audit_text(before_created_at=cursor)
    next_cursor = min((int(event["created_at"]) for event in events), default=None)
    await cb.message.edit_text(text, reply_markup=_channel_audit_markup(next_cursor))
    await cb.answer()


@dp.callback_query(lambda c: c.data and c.data.startswith("outboxhealth:filter:"))
async def outbox_health_filter(cb: types.CallbackQuery):
    if not _can_panel(cb.from_user.id, "reward"):
        await cb.answer("Reward admin access required.", show_alert=True)
        return
    value = cb.data.split(":", 2)[2]
    events = ledger.tx.recent_audit_events(object_type="outbox_health", limit=30)
    if value == "RAISED":
        events = ledger.tx.active_health_alerts(limit=30)
        title = "ACTIVE / RAISED HEALTH ALERTS"
    else:
        events = [event for event in events if event["action"] == "OUTBOX_ALERT_CLEARED"]
        title = "CLEARED HEALTH ALERTS"
    lines = [f"{title}", ""]
    lines.extend(f"[{event['created_at']}] {event['reason']}" for event in events)
    if not events:
        lines.append("No matching health alerts.")
    await cb.message.edit_text(
        "\n".join(lines[:45]),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Back to outbox", callback_data="panel:outbox")],
        ]),
    )
    await cb.answer()


@dp.callback_query(lambda c: c.data and c.data.startswith("outbox:filter:"))
async def outbox_filter(cb: types.CallbackQuery):
    if not _can_panel(cb.from_user.id, "reward"):
        await cb.answer("Reward admin access required.", show_alert=True)
        return
    parts = cb.data.split(":")
    value = parts[2]
    offset = int(parts[3]) if len(parts) > 3 else 0
    if value in {"TASK_MINT_REWARD", "OWNER_NOTIFICATION"}:
        events = ledger.tx.recent_outbox_events(event_type=value, limit=15, offset=offset)
        title = value
    else:
        events = ledger.tx.recent_outbox_events(status="failed" if value in {"failed", "paused"} else value, limit=15, offset=offset)
        if value == "paused":
            events = [event for event in events if str(event.get("last_error", "")).startswith("PAUSED:")]
        title = value.upper()
    lines = [f"📤 OUTBOX: {title}", ""]
    buttons = []
    for event in events[:15]:
        lines.append(f"{_outbox_age_marker(event)} {event['event_id']} · {event['event_type']} · {event['status']} · attempts={event['attempts']}")
        buttons.append([InlineKeyboardButton(text=f"🔎 View {event['event_id'][-8:]}", callback_data=f"outbox:view:{event['event_id']}")])
    if not events:
        lines.append("No matching events.")
    navigation = []
    if offset > 0:
        navigation.append(InlineKeyboardButton(text="Newer events", callback_data=f"outbox:filter:{value}:{max(0, offset - 15)}"))
    if len(events) >= 15:
        navigation.append(InlineKeyboardButton(text="Older events", callback_data=f"outbox:filter:{value}:{offset + 15}"))
    if navigation:
        buttons.append(navigation)
    buttons.append([InlineKeyboardButton(text="⬅️ Back to outbox", callback_data="panel:outbox")])
    await cb.message.edit_text("\n".join(lines[:45]), reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await cb.answer()


@dp.callback_query(lambda c: c.data and c.data.startswith("outbox:"))
async def outbox_action(cb: types.CallbackQuery):
    if not _can_panel(cb.from_user.id, "reward"):
        await cb.answer("Reward admin access required.", show_alert=True)
        return
    _, action, event_id = cb.data.split(":", 2)
    if not hasattr(ledger, "tx"):
        await cb.answer("Transactional outbox is not enabled.", show_alert=True)
        return
    if action == "view":
        event = ledger.tx.get_outbox_event(event_id)
        if not event:
            await cb.answer("Event not found.", show_alert=True)
            return
        history = ledger.tx.recent_audit_events(object_type="outbox", object_id=event_id, limit=20)
        payload = escape(str(event.get("payload", ""))[:1200])
        lines = [
            "🔎 <b>OUTBOX EVENT DETAIL</b>", "",
            f"Event: <code>{escape(event_id)}</code>",
            f"Type: {escape(str(event.get('event_type')))}",
            f"Status: {_outbox_age_marker(event)} {escape(str(event.get('status')))}",
            f"Recipient: {escape(str(event.get('recipient_id')))}",
            f"Attempts: {event.get('attempts', 0)}",
            f"Created: {event.get('created_at')} ({_elapsed_label(event.get('created_at'))} ago)",
            f"Available: {event.get('available_at')}",
            f"Last error: {escape(str(event.get('last_error') or 'none'))}",
            "", "Payload:", f"<code>{payload}</code>",
        ]
        if history:
            lines.extend(["", "Action history:"])
            lines.extend(f"{item['created_at']} · {item['actor_id']} · {item['action']} · {item['reason']}" for item in history)
        await cb.message.edit_text(
            "\n".join(lines[:45]), parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="↻ Retry", callback_data=f"outbox:retry:{event_id}"),
                 InlineKeyboardButton(text="⏸ Pause", callback_data=f"outbox:pause:{event_id}")],
                [InlineKeyboardButton(text="⬅️ Back to outbox", callback_data="panel:outbox")],
            ]),
        )
        await cb.answer()
        return
    if action == "retry":
        ok = ledger.tx.retry_outbox(event_id)
        if ok:
            _record_outbox_admin_action(cb.from_user.id, "OUTBOX_RETRY", event_id, "manual retry")
        message = "Event returned to retry queue." if ok else "Event not found or not failed."
    elif action == "pause":
        ok = ledger.tx.fail_outbox(event_id, "paused by administrator", permanent=True)
        if ok:
            _record_outbox_admin_action(cb.from_user.id, "OUTBOX_PAUSE", event_id, "manual pause")
        message = "Event paused safely." if ok else "Event not found or not processing."
    else:
        await cb.answer("Unknown outbox action.", show_alert=True)
        return
    await cb.answer(message, show_alert=True)
    await cb.message.edit_reply_markup(reply_markup=ui.role_menu(roles.role(cb.from_user.id)))


@dp.callback_query(lambda c: c.data and c.data.startswith("performance:clear:"))
async def clear_performance_cooldown(cb: types.CallbackQuery):
    if not _can_panel(cb.from_user.id, "reward"):
        await cb.answer("Reward admin access required.", show_alert=True)
        return
    destination_id = cb.data.split(":", 2)[2]
    ok = credibility_snapshots.clear_cooldown(
        destination_id, reason=f"cleared by admin {cb.from_user.id}")
    if ok and hasattr(ledger, "tx"):
        ledger.tx.add_audit_event(
            actor_type="admin", actor_id=str(cb.from_user.id),
            action="PERFORMANCE_COOLDOWN_CLEARED",
            object_type="destination_control", object_id=destination_id,
            reason="manual intervention",
        )
    await cb.answer("Cooldown cleared." if ok else "No active cooldown found.", show_alert=True)
    await cb.message.edit_reply_markup(reply_markup=ui.role_menu(roles.role(cb.from_user.id)))


@dp.callback_query(lambda c: c.data and c.data.startswith("broadcast:"))
async def broadcast_panel_action(cb: types.CallbackQuery):
    if not _can_panel(cb.from_user.id, "reward"):
        await cb.answer("Reward admin access required.", show_alert=True)
        return
    _, action, campaign_id = cb.data.split(":", 2)
    if action == "view":
        summary = broadcasts.campaign_summary(campaign_id)
        if not summary:
            await cb.answer("Campaign not found.", show_alert=True)
            return
        payload = escape(str(summary["payload"].get("text", ""))[:800])
        deliveries = summary.get("deliveries", {})
        text = (f"📣 <b>CAMPAIGN DETAIL</b>\n\n"
                f"ID: <code>{campaign_id}</code>\nStatus: {summary['status']}\n"
                f"Payload: <code>{payload}</code>\n\n" +
                "\n".join(f"{key}: {value}" for key, value in deliveries.items()))
        buttons = []
        if summary["status"] in {"queued", "running"}:
            buttons.append([InlineKeyboardButton(text="⏸ Pause", callback_data=f"broadcast:pause:{campaign_id}"),
                            InlineKeyboardButton(text="🛑 Cancel", callback_data=f"broadcast:cancel:{campaign_id}")])
        elif summary["status"] == "paused":
            buttons.append([InlineKeyboardButton(text="▶️ Resume", callback_data=f"broadcast:resume:{campaign_id}"),
                            InlineKeyboardButton(text="🛑 Cancel", callback_data=f"broadcast:cancel:{campaign_id}")])
        buttons.append([InlineKeyboardButton(text="⬅️ Back", callback_data="panel:broadcasts")])
        await cb.message.edit_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
        await cb.answer()
        return
    actions = {"pause": broadcasts.pause, "resume": broadcasts.resume, "cancel": broadcasts.cancel}
    if action not in actions:
        await cb.answer("Unknown campaign action.", show_alert=True)
        return
    result = actions[action](campaign_id)
    ok = bool(result)
    if hasattr(ledger, "tx") and ok:
        ledger.tx.add_audit_event(actor_type="admin", actor_id=str(cb.from_user.id),
                                  action=f"REWARD_BROADCAST_{action.upper()}",
                                  object_type="broadcast_campaign", object_id=campaign_id,
                                  reason="inline admin action")
    labels = {"pause": "paused", "resume": "resumed", "cancel": "cancelled"}
    await cb.answer(f"Campaign {labels[action]}." if ok else "Campaign action not available.", show_alert=True)
    await cb.message.edit_reply_markup(reply_markup=ui.role_menu(roles.role(cb.from_user.id)))


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
    if which == "broadcasts":
        campaigns = broadcasts.recent_campaigns(bot_scope="reward", limit=12)
        lines = ["📣 <b>REWARD BROADCASTS</b>", ""]
        buttons = []
        if not campaigns:
            lines.append("No campaigns created yet.")
        for campaign in campaigns:
            summary = broadcasts.campaign_summary(campaign["campaign_id"])
            deliveries = summary.get("deliveries", {}) if summary else {}
            lines.append(
                f"{campaign['campaign_id']} · {campaign['status']} · "
                f"sent={deliveries.get('sent', 0)} failed={deliveries.get('failed', 0)}"
            )
            buttons.append([InlineKeyboardButton(
                text=f"🔎 {campaign['campaign_id'][-8:]}",
                callback_data=f"broadcast:view:{campaign['campaign_id']}")])
        buttons.append([InlineKeyboardButton(text="⬅️ Back", callback_data="menu:admin")])
        await cb.message.edit_text("\n".join(lines[:45]), reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
        await cb.answer()
        return
    if which == "performance":
        suspended = credibility_snapshots.suspended_destinations(limit=50)
        lines = ["📈 <b>PERFORMANCE CONTROLS</b>", ""]
        if not suspended:
            lines.append("✅ No destinations are currently suspended.")
        else:
            lines.append("Temporarily suspended destinations:")
            for control in suspended:
                destination = str(control['destination_id'])
                lines.append(
                    f"• {escape(destination)} · "
                    f"remaining {_remaining_label(control['cooldown_until'])} "
                    f"({escape(control['reason'])})"
                )
        lines.extend(["", "Recent intervention history:"])
        seen_history = 0
        for control in suspended:
            for item in credibility_snapshots.intervention_history(control["destination_id"], limit=3):
                lines.append(f"• {item['destination_id']} · {item['action']} · {item['created_at']} · {item['reason']}")
                seen_history += 1
        if not seen_history:
            lines.append("• No intervention history for active suspensions.")
        lines.extend(["", "Suspensions are temporary and are triggered by repeated execution failures."])
        performance_buttons = []
        for control in suspended:
            performance_buttons.append([InlineKeyboardButton(
                text=f"✅ Clear {str(control['destination_id'])[-18:]}",
                callback_data=f"performance:clear:{control['destination_id']}")])
        performance_buttons.extend([
            [InlineKeyboardButton(text="🔄 Refresh", callback_data="panel:performance")],
            [InlineKeyboardButton(text="⬅️ Back", callback_data="menu:admin")],
        ])
        await cb.message.edit_text(
            "\n".join(lines), parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=performance_buttons),
        )
        await cb.answer()
        return
    if which == "outbox":
        if not hasattr(ledger, "tx"):
            await cb.message.edit_text("Transactional outbox is not enabled.", reply_markup=ui.role_menu(roles.role(uid)))
            await cb.answer()
            return
        events = ledger.tx.recent_outbox_events(status="failed", limit=15)
        health = ledger.tx.outbox_health()
        metrics = ledger.tx.outbox_metrics()
        performance = ledger.tx.outbox_performance()
        lines = [
            "📤 <b>OUTBOX METRICS</b>",
            f"Pending: {health['pending']} · Processing: {health['processing']}",
            f"Failed: {health['failed']} · Sent: {health['sent']}",
            f"Retry rate: {performance['retry_rate'] * 100:.1f}% · Average attempts: {performance['average_attempts']:.2f}",
            f"Oldest pending: {_elapsed_label(performance['oldest_pending'])}",
            f"Oldest failed: {_elapsed_label(performance['oldest_failed'])}",
            "",
            "By type/status:",
        ]
        lines.extend(f"• {item['event_type']} / {item['status']}: {item['count']}" for item in metrics[:12])
        lines.extend(["", "⚠️ FAILED OUTBOX EVENTS", ""])
        buttons = []
        if not events:
            lines.append("✅ No failed or paused outbox events.")
            lines.append("")
        for event in events:
            lines.append(f"{_outbox_age_marker(event)} {event['event_id']} · {event['event_type']} · attempts={event['attempts']}\n{event.get('last_error') or 'retryable'}")
            buttons.append([
                InlineKeyboardButton(text=f"🔎 View {event['event_id'][-8:]}", callback_data=f"outbox:view:{event['event_id']}"),
            ])
            buttons.append([
                InlineKeyboardButton(text="↻ Retry", callback_data=f"outbox:retry:{event['event_id']}"),
                InlineKeyboardButton(text="⏸ Pause", callback_data=f"outbox:pause:{event['event_id']}"),
            ])
        history = ledger.tx.recent_audit_events(object_type="outbox", limit=8)
        health_history = ledger.tx.recent_audit_events(object_type="outbox_health", limit=8)
        if history:
            lines.extend(["", "ADMIN ACTION HISTORY"])
            for event in history:
                lines.append(
                    f"[{event['created_at']}] {event['actor_id']} "
                    f"| {event['action']} | {event['object_id']} | {event['reason']}"
                )
        if health_history:
            lines.extend(["", "HEALTH ALERT HISTORY"])
            for event in health_history:
                lines.append(
                    f"[{event['created_at']}] {event['action']} | {event['reason']}"
                )
        buttons.insert(0, [
            InlineKeyboardButton(text="Failed", callback_data="outbox:filter:failed"),
            InlineKeyboardButton(text="Paused", callback_data="outbox:filter:paused"),
            InlineKeyboardButton(text="Processing", callback_data="outbox:filter:processing"),
        ])
        buttons.insert(1, [
            InlineKeyboardButton(text="Rewards", callback_data="outbox:filter:TASK_MINT_REWARD"),
            InlineKeyboardButton(text="Notifications", callback_data="outbox:filter:OWNER_NOTIFICATION"),
        ])
        buttons.append([
            InlineKeyboardButton(text="⚠️ Raised alerts", callback_data="outboxhealth:filter:RAISED"),
            InlineKeyboardButton(text="✅ Cleared alerts", callback_data="outboxhealth:filter:CLEARED"),
        ])
        buttons.append([InlineKeyboardButton(text="⬅️ Back", callback_data="menu:admin")])
        await cb.message.edit_text("\n".join(lines[:45]), reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
        await cb.answer()
        return
    if which == "audit":
        log = audit.last(10)
        lines = [_channel_audit_text(), "", "DELIVERY HISTORY"]
        for r in reversed(log):
            lines.append(f"[{r.get('ts')}] {r.get('sender')} -> {r.get('target_channel')} "
                         f"| {r.get('status')} | {r.get('post_type')}")
        page = ledger.tx.recent_audit_events(object_type="channel", limit=20) if hasattr(ledger, "tx") else []
        cursor = min((int(event["created_at"]) for event in page), default=None)
        await cb.message.edit_text("\n".join(lines[:45]) or "No audit events yet.",
                                   reply_markup=_channel_audit_markup(cursor))
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
        rows = [f"{u:<22} {'auto ⚡' if (channels.get(u) or {}).get('auto_post') else 'offer —'}"
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
        await cb.message.edit_text(TERMS_TEXT, parse_mode="HTML", reply_markup=back_btn_q("menu:owner"))
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
    lines = ["🏆 LEADERBOARD (performance tier A/B/C — not audience size):", ""]
    for username, m in ledger.ledger.items():
        if m.get("is_owner"):
            continue
        s = perf.score(username)
        cap = daily_post_cap(m.get("size", 0), s["band"], m.get("status", "ACTIVE"), connected=_is_connected(username))
        lines.append(f"{username:<22} {s['band']}  {s['score']:.2f}  {s['status']}  "
                     f"auto={'⚡' if (channels.get(username) or {}).get('auto_post') else '—'}  "
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


async def _monitor_operational_health():
    """Send deduplicated owner alerts for actionable storage/queue failures."""
    global _HEALTH_ALERT_LAST
    if not hasattr(ledger, "tx"):
        return
    now = int(time.time())
    if now - _HEALTH_ALERT_LAST < 3600:
        return
    _HEALTH_ALERT_LAST = now
    health = ledger.tx.audit_health()
    outbox = ledger.tx.outbox_health()
    db_size = os.path.getsize(ledger.tx.path) if os.path.exists(ledger.tx.path) else 0
    warnings = []
    if db_size >= int(os.getenv("AUDIT_DB_WARN_BYTES", str(50 * 1024 * 1024))):
        warnings.append(f"audit database is {db_size / 1024 / 1024:.1f} MB")
    if outbox["failed"] >= int(os.getenv("OUTBOX_FAILED_WARN", "10")):
        warnings.append(f"{outbox['failed']} outbox events are failed")
    if outbox["processing"] >= int(os.getenv("OUTBOX_PROCESSING_WARN", "20")):
        warnings.append(f"{outbox['processing']} outbox events are stuck processing")
    stale_threshold = int(os.getenv("OUTBOX_STALE_WARN_SECONDS", "3600"))
    stale_events = [event for event in ledger.tx.recent_outbox_events(limit=100)
                    if event.get("status") in {"failed", "processing", "pending"}
                    and _elapsed_seconds(event.get("created_at")) >= stale_threshold]
    if stale_events:
        warnings.append(f"{len(stale_events)} outbox events are stale beyond {_duration_label(stale_threshold)}")
    if not warnings:
        if _HEALTH_ALERTED:
            for fingerprint in list(_HEALTH_ALERTED):
                ledger.tx.add_audit_event(
                    actor_type="system", actor_id="health-monitor",
                    action="OUTBOX_ALERT_CLEARED", object_type="outbox_health",
                    object_id="global", reason=fingerprint,
                )
            _HEALTH_ALERTED.clear()
            owner_notify("✅ <b>PLATFORM HEALTH RECOVERED</b>\n\n"
                         "Previously reported outbox conditions have cleared.")
        return
    fingerprint = "|".join(warnings)
    if fingerprint in _HEALTH_ALERTED:
        return
    _HEALTH_ALERTED.add(fingerprint)
    ledger.tx.add_audit_event(
        actor_type="system", actor_id="health-monitor",
        action="OUTBOX_ALERT_RAISED", object_type="outbox_health",
        object_id="global", reason=fingerprint,
    )
    owner_notify("⚠️ <b>PLATFORM HEALTH ALERT</b>\n\n" +
                 "\n".join(f"• {warning}" for warning in warnings) +
                 f"\n\nAudit events: {health['total']}\nUse /audithealth for details.")


_REVERIFY_BATCH = int(config.env("REVERIFY_BATCH", "5") or 5)


async def _background_reverify(*, now: int | None = None) -> list[str]:
    """State-aware automatic re-verification.

    Previously every member's every destination was re-verified on every 15 s
    tick — O(destinations) Bot API calls per tick, a flood-limit incident
    waiting to happen. Now `destination_state.RECHECK_INTERVAL` decides who is
    due (healthy 6 h, degraded 30 min, revoked never), at most REVERIFY_BATCH
    per tick, oldest check first. Safe mode pauses it. Owners are messaged only
    when the operational status actually changed.
    """
    if enforcement_gate.safe_mode():
        return []
    # Index-served candidate set (ix_dest_next), then the state machine's own
    # schedule rule as the authority — two views that must agree.
    candidates = verification_store.destinations_due(now=now, limit=_REVERIFY_BATCH * 4)
    due = destination_states.due_for_recheck(candidates, now=now)
    notes = []
    for row in due[:_REVERIFY_BATCH]:
        key = row.get("chat_id") or row.get("username")
        before = row.get("status")
        try:
            result = await telegram_verification.verify(int(row["owner_id"]), channels.telegram_reference(row))
        except CredentialError:
            result = _failed_result(row, "connect your bot with /connectbot")
        except Exception as exc:
            info = classify_telegram_error(exc)
            if info.kind.value == "unknown":
                logging.exception("background re-verify of %s raised", key)
            result = _failed_result(row, f"Telegram verification failed: {info.kind.value}")
        if result is None:
            continue
        _apply_verification(row, result, source="background")
        after = (channels.get(key) or {}).get("status", before)
        # Even when nothing changed, stamp the recheck so we don't re-probe next tick.
        try:
            channels.update(int(row["owner_id"]), key, last_recheck_at=int(now if now is not None else time.time()))
        except (KeyError, ValueError):
            pass
        if before != after:
            note = f"{row.get('username') or key}: {before} → {after}"
            notes.append(note)
            try:
                await bot.send_message(int(row["owner_id"]),
                                       f"🔎 Destination access changed\n• {note}\n"
                                       + ("" if after == "ACTIVE" else
                                          "\nTap /mychannels → Re-verify after fixing the bot's admin rights."))
            except Exception as exc:
                logging.warning("reverify notice to %s failed: %s", row.get("owner_id"), classify_telegram_error(exc).kind.value)
    return notes


async def notify_loop():
    """Background: hand out queued review outcomes to the senders (reward scope)."""
    while True:
        try:
            recovery = marketplace.recover_expired()
            if any(recovery.values()):
                logging.info("task recovery: %s", recovery)
            await _process_mint_outbox()
            await _process_reward_broadcasts()
            await _process_ad_deliveries()
            await _monitor_operational_health()
            for item in review.pending_notify("reward"):
                if await _notify_review_sender(item):
                    review.clear_notify(item["id"])
            # Background re-verification: only destinations whose state-specific
            # interval has elapsed, a bounded batch per tick, owner told on change.
            await _background_reverify()
            # Keep one replaceable availability bubble per known member.
            for member in ledger.ledger.values():
                recipient = member.get("user_id")
                if not recipient:
                    continue
                legacy_count = available.count(recipient)
                task_count = len(_eligible_marketplace_tasks(int(recipient)))
                count = legacy_count + task_count
                bubble = bubbles.get(recipient)
                if count == bubble.get("count") and bubble.get("message_id"):
                    continue
                if count:
                    parts = []
                    if task_count:
                        parts.append(f"📋 {task_count} matching marketplace task(s)")
                    if legacy_count:
                        parts.append(f"📬 {legacy_count} legacy post(s)")
                    text = "\n".join(parts) + "\nTap /start to view"
                else:
                    text = "📭 No eligible tasks or posts currently available"
                try:
                    if bubble.get("message_id"):
                        await bot.edit_message_text(text, chat_id=int(recipient),
                                                    message_id=bubble["message_id"])
                    else:
                        sent = await bot.send_message(int(recipient), text)
                        bubble["message_id"] = sent.message_id
                    bubbles.set(recipient, count, bubble.get("message_id"))
                except Exception:
                    # A blocked/deleted chat is not fatal to the notification loop.
                    pass
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


@dp.message(Command("report"))
async def report_cmd(msg: types.Message):
    """Group-scoped report entry point.

    • Inside a registered GROUP, replying to a message with `/report <reason>`
      files a report against that group (entity_type=group) with the replied
      message as evidence. Members of the group need no CLICKMINT account.
    • In private chat, `/report @target <reason>` reports a channel/group.
    Filing NEVER changes anyone's state — a human reviews it.
    """
    uid = _uid(msg)
    chat_type = getattr(msg.chat, "type", "private")
    parts = (msg.text or "").split(maxsplit=2)
    if chat_type in {"group", "supergroup"}:
        row = channels.find_by_telegram_chat(msg.chat.id) or (
            channels.find_by_telegram_chat("@" + msg.chat.username) if getattr(msg.chat, "username", None) else None)
        if row is None:
            await msg.reply("This group is not registered with CLICKMINT, so there is nothing to report here.")
            return
        reason = parts[1] + (" " + parts[2] if len(parts) > 2 else "") if len(parts) > 1 else ""
        if not reason.strip():
            await msg.reply("Usage: reply to the offending message with /report <reason>.")
            return
        entity_id = row.get("username") or row.get("chat_id")
        subject = f"group_message:{msg.chat.id}/{msg.reply_to_message.message_id}" if msg.reply_to_message else f"group:{msg.chat.id}"
        try:
            item = enforcement.report(uid, entity_id, "group", reason.strip()[:500], subject=subject)
            if msg.reply_to_message:
                enforcement.add_evidence(entity_id, "group", "message", f"telegram:{msg.chat.id}/{msg.reply_to_message.message_id}",
                                         captured_by=uid, report_id=item["report_id"], notes="reported in-group")
        except EnforcementError as exc:
            await msg.reply(f"⚠ {exc}")
            return
        await msg.reply(f"🚩 Report {item['report_id'][-6:]} filed. A human reviews every report — nothing is automatic.")
        owner_notify(f"🚩 In-group report {item['report_id']} on group {entity_id}: {reason.strip()[:120]}")
        return
    # private chat: /report @target reason
    if len(parts) < 3 or not parts[1].startswith("@"):
        await msg.answer("Usage: /report @channel_or_group <reason>\n"
                         "Inside a registered group you can also reply to a message with /report <reason>.")
        return
    target, reason = parts[1], parts[2].strip()
    row = channels.get(target)
    if row is None:
        await msg.answer("That destination is not registered with CLICKMINT.")
        return
    if int(row.get("owner_id", -1)) == uid:
        await msg.answer("You cannot report your own destination. Use /removechannel or /ads off instead.")
        return
    try:
        item = enforcement.report(uid, target, row.get("kind", "channel"), reason[:500], subject="private_report")
    except EnforcementError as exc:
        await msg.answer(f"⚠ {exc}")
        return
    await msg.answer(f"🚩 Report {item['report_id'][-6:]} filed against {target}. "
                     "A human reviews every report — nothing is auto-banned.")
    owner_notify(f"🚩 Report {item['report_id']} on {row.get('kind', 'channel')} {target}: {reason[:120]}")


@dp.message(Command("ads"))
async def ads_cmd(msg: types.Message):
    """Destination owners opt IN (or out) of receiving ads — per channel, under
    the current terms version. Nothing is ever posted to a channel that has
    not opted in; consent can be withdrawn at any time with immediate effect."""
    uid = _uid(msg)
    parts = (msg.text or "").split()
    terms = ads.current_terms()
    mine = channels.mine(uid)
    if len(parts) < 3 or parts[1] not in {"on", "off"}:
        lines = ["📢 ADVERTISING CONSENT", ""]
        if terms is None:
            lines.append("No advertising terms are published yet, so nothing can be advertised.")
        else:
            lines.append(f"Current terms v{terms['version']}:")
            lines.append(terms["text"][:1200])
            lines.append("")
        for row in mine:
            dest = row.get("username") or row.get("chat_id")
            c = ads.consent(dest)
            lines.append(f"• {dest}: " + (f"✅ opted in (terms v{c['terms_version']})" if c else "⛔ not opted in"))
        lines += ["", "Usage: /ads on @channel   ·   /ads off @channel",
                  "Opting in means an approved, clearly labelled ad may be posted to that "
                  "channel by YOUR bot. You can opt out at any time."]
        await msg.answer("\n".join(lines))
        return
    action, dest = parts[1], parts[2]
    row = next((r for r in mine if (r.get("username") or r.get("chat_id")) == dest), None)
    if row is None:
        await msg.answer("You can only manage consent for a destination you registered.")
        return
    if action == "off":
        ads.revoke_consent(dest, owner_id=uid, reason="owner opted out via /ads off")
        await msg.answer(f"⛔ {dest} opted out of advertising. Any pending ads for it were cancelled.")
        return
    if terms is None:
        await msg.answer("No advertising terms are published yet.")
        return
    blocked = _enforcement_block(uid, "campaigns", dest)
    if blocked:
        await msg.answer(blocked)
        return
    try:
        ads.grant_consent(dest, owner_id=uid, terms_version=terms["version"],
                          categories=row.get("categories") or [])
    except AdPolicyError as exc:
        await msg.answer(f"⚠ {exc}")
        return
    await msg.answer(f"✅ {dest} opted in to advertising under terms v{terms['version']} "
                     f"(categories: {', '.join(row.get('categories') or ['any'])}). "
                     "Use /ads off to withdraw at any time.")


@dp.message(Command("appeal"))
async def appeal_cmd(msg: types.Message):
    """Let a restricted member put their side on the record. Filing an appeal
    changes nothing by itself — a human decides, and only the owner can
    overturn an enforcement action."""
    uid = _uid(msg)
    statement = (msg.text or "").split(maxsplit=1)[1].strip() if len((msg.text or "").split(maxsplit=1)) > 1 else ""
    if not statement:
        await msg.answer("Usage: /appeal <your explanation>\n"
                         "Tell us why the restriction is a mistake. A human reviews every appeal.")
        return
    if len(statement) > 1500:
        await msg.answer("Please keep your appeal under 1500 characters.")
        return
    # Appeal against whichever of the member's records is currently enforced.
    subjects = [(uid, "user")] + [(row.get("username") or row.get("chat_id"), "channel")
                                  for row in channels.mine(uid)]
    target = next(((eid, etype) for eid, etype in subjects
                   if eid and enforcement.get(eid, etype)["state"] != "ACTIVE"), None)
    if target is None:
        await msg.answer("✅ Nothing to appeal — your account and channels are active.")
        return
    try:
        item = enforcement.appeal(target[0], target[1], submitted_by=uid, statement=statement)
    except EnforcementError as exc:
        await msg.answer(f"⚠ {exc}")
        return
    await msg.answer(f"📨 Appeal {item['appeal_id']} filed for {target[1]} {target[0]}. "
                     "A human will review it; you'll be notified of the decision.")
    owner_notify(f"📨 New appeal {item['appeal_id']} from {_uname(msg)} ({target[1]} {target[0]}). "
                 "Review it in the Admin Mini App.")


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
    if not destination or not channels.participation_allowed(target):
        reason = channels.participation_block_reason(target) if destination else "destination is not registered"
        await msg.answer("⛔ Scheduling is blocked: " + str(reason) +
                         "\nRun /scan after correcting the Telegram setup.")
        return
    pending = _session(msg.from_user.id).get("pending") or {}
    if not pending.get("from_message_id"):
        await msg.answer("Forward the post to me first, then schedule it — "
                         "otherwise there's nothing to deliver.")
        return
    rec = sched.schedule(at=at, target=target, sender=pending.get("user", _uname(msg)),
                         from_chat_id=pending.get("from_chat_id"),
                         from_message_id=pending.get("from_message_id"),
                         post_type=pending.get("post_type", "General"),
                         origin_chat_id=pending.get("origin_chat_id"),
                         origin_message_id=pending.get("origin_message_id"),
                         origin_kind=pending.get("origin_kind"))
    await msg.answer(f"⏰ Scheduled #{rec['id']} to {target} at {day_str} {hm} UTC.")
    audit.record(bot="reward", sender=pending.get("user", _uname(msg)),
                 target_channel=target, mode="direct", status="scheduled",
                 forward_valid=True, scheduled_at=f"{day_str} {hm} UTC")


async def run_due():
    for rec in sched.due():
        try:
            sender_uid = ledger._m(rec["sender"]).get("user_id") if rec.get("sender") else None
            subjects = [(rec["target"], "channel")] + ([(sender_uid, "user")] if sender_uid else [])
            decision = enforcement_gate.check_many(subjects, "automated_posting",
                                                   is_owner=bool(sender_uid and roles.is_owner(int(sender_uid))))
            if not decision:
                if decision.safe_mode:
                    # Safe mode pauses the machine; the slot is retried later.
                    continue
                sched.mark_done(rec["id"], note="blocked by enforcement — skipped")
                audit.record(bot="reward", sender=rec["sender"], target_channel=rec["target"],
                             mode="direct", status="failed", forward_valid=False,
                             error="blocked by enforcement: " + decision.reason[:100])
                continue
            if rec.get("from_chat_id") is not None and rec.get("from_message_id") is not None:
                try:
                    # Legacy /schedule always fired with the platform bot; keep that and let Telegram judge.
                    await _relay_forward(relay.Source.from_record(rec), rec["target"], platform_may_try=True)
                except RelayUnavailable as exc:
                    sched.mark_done(rec["id"], note=f"no legal relay route — {exc}"[:120])
                    audit.record(bot="reward", sender=rec["sender"], target_channel=rec["target"],
                                 mode="direct", status="failed", forward_valid=False, relay="unavailable",
                                 error=str(exc)[:120])
                    continue
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
