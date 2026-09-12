"""
PARTNERSHIP BOT  (aiogram)  — curated MAIN partners, with a terms contract.

SEPARATE from the reward bot. For the quality partners you chose and agreed terms with.
Shares core.py + governance.py so the rules/roles/limits are consistent.

NEW: button menus, role-gated (owner/admin/user via invite code), a submission
gate (niche category + no spam/ads + human review), and a post-style selector
(forward / direct / pin + loud / silent).
"""
import asyncio
import json
import logging
import time
from html import escape
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo

from core import (Contract, CreditLedger, DeliveryLog, ReportRegistry,
                  PerformanceEngine, POST_TYPES, is_forward, forward_source)
from broadcast_queue import BroadcastQueue
from enforcement import EnforcementStore, EnforcementError
from enforcement_gate import EnforcementGate
from destination_state import (DestinationStateMachine, VState, IllegalTransition, classify_telegram_error,
                               apply_verification_result, failed_result_from_reason)
from channel_registry import ChannelRegistry
from verification_store import VerificationStore
from onboarding_api import redact
from telegram_verification import BotCredentialStore, TelegramVerificationService, CredentialError
from features import StatsBook
from store import JsonStore
from governance import (SubmissionGate, ReviewQueue, RoleRegistry, POST_CATEGORIES,
                        TERMS_TEXT, daily_post_cap)
import config
import relay
import ui
from platform_store import DeliveryAudit, SharedKV
from delivery_worker import WorkerStore, admit_delivery

logging.basicConfig(level=logging.INFO)
BOT_TOKEN = config.PARTNER_BOT_TOKEN
OWNER_USER_ID = config.OWNER_USER_ID

store = JsonStore(config.PARTNER_STORE_PATH)
ledger = CreditLedger(store)
ledger.owner_user_id = config.OWNER_USER_ID      # owner recognised by numeric id too
contract = Contract()
audit = DeliveryAudit(config.PLATFORM_DB_PATH, legacy=store)     # shared SQLite, JSON migrated once
reports = ReportRegistry(store)
perf = PerformanceEngine(ledger, store, views_provider=None)
# Destinations + bot credentials live in the SQLite verification DB shared with
# the other bot; every other key still goes to this bot's JsonStore.
verification_store = VerificationStore(config.VERIFICATION_DB_PATH, legacy=store)
channels = ChannelRegistry(verification_store)
destination_states = DestinationStateMachine(channels)   # the ONLY writer of verified_state/status
bot_credentials = BotCredentialStore(verification_store)
telegram_verification = TelegramVerificationService(bot_credentials)
broadcasts = BroadcastQueue(config.BROADCAST_DB_PATH)
worker_store = WorkerStore(config.WORKER_DB_PATH)
stats = StatsBook(store)


def _is_connected(username: str) -> bool:
    return channels.participation_allowed(username)
perf.views_provider = stats.provider
gate = SubmissionGate(store)
review = ReviewQueue(store)
platform_kv = SharedKV(config.PLATFORM_DB_PATH, legacy=store)
roles = RoleRegistry(platform_kv, owner_user_id=OWNER_USER_ID)   # one role table for every bot
enforcement = EnforcementStore(config.ENFORCEMENT_DB_PATH)
enforcement_gate = EnforcementGate(enforcement, owner_user_id=config.OWNER_USER_ID)


def _apply_verification(row: dict, result, *, source: str) -> tuple[bool, str]:
    def _audit_change(r, previous, current, reason):
        audit.record(bot="partnership", sender=str(r.get("owner_id")), target_channel=str(r.get("chat_id") or r.get("username")),
                     mode="direct", status="failed" if current != "ACTIVE" else "delivered", forward_valid=True,
                     error=f"{previous} -> {current}: {reason}"[:120])
    return apply_verification_result(destination_states, row, result, source=source, on_change=_audit_change)


def _disconnect_all(uid: int, reason: str):
    for row in channels.mine(uid):
        try:
            destination_states.transition(uid, row.get("chat_id") or row.get("username"), VState.DISCONNECTED,
                                          reason=reason, source="owner", verification_reasons=[reason])
        except IllegalTransition:
            pass


def _enforcement_block(uid: int, capability: str, *destinations) -> str | None:
    subjects = [(uid, "user")] + [(d, "channel") for d in destinations if d]
    decision = enforcement_gate.check_many(subjects, capability, is_owner=roles.is_owner(uid))
    return None if decision else enforcement_gate.block_message(decision)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


async def _register_from_telegram(msg, destination: str, kind: str = "channel") -> str:
    blocked = _enforcement_block(msg.from_user.id, "registration", destination)
    if blocked:
        return blocked
    owner = msg.from_user.id
    channels.add(owner, destination, destination, kind, ["General"], size=0, bot_added=False)
    destination_states.transition(owner, destination, VState.VERIFYING, reason="registration requested", source="register")
    result = await telegram_verification.verify(owner, destination)
    ok, _ = _apply_verification(channels.get(destination), result, source="register")
    if not ok:
        return "❌ Verification failed: " + "; ".join(result.reasons) + "\nAdd your bot as administrator and try again."
    size = result.member_count or 0
    ledger.register(destination, size, is_partner=True); ledger.set_user_id(destination, owner)
    return f"✅ Verified {destination}; Telegram reports {size:,} members/subscribers."


@dp.update.outer_middleware()
async def bind_identity(handler, event, data):
    """Same identity binding as the reward bot: the owner must be recognised on
    the very first update, not only after their first forward."""
    src = getattr(event, "message", None) or getattr(event, "callback_query", None)
    fu = getattr(src, "from_user", None)
    if fu is not None and not fu.is_bot:
        ledger.set_user_id("@" + (fu.username or str(fu.id)), fu.id)
    return await handler(event, data)


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


def _session_clear(uid) -> None:
    sessions = _sessions()
    sessions.pop(str(uid), None)
    store["sessions"] = sessions
    store.sync()


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
        try:
            await msg.delete()
        except Exception:
            logging.warning("could not delete bot-token message")
        identity = await telegram_verification.connect_bot(msg.from_user.id, parts[1].strip())
    except Exception as exc:
        await msg.answer(f"❌ Bot connection failed: {redact(str(exc))[:200]}")
        return
    if previous and int(previous.get("bot_id", -1)) != int(identity.get("bot_id", -2)):
        _disconnect_all(msg.from_user.id, "Connected bot changed; destination must be re-verified")
    await msg.answer(f"✅ Connected @{identity.get('username') or 'your bot'}. Add it as an administrator, then register a destination."
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
        f"Username: @{identity.get('username') or 'unknown'}\n"
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
                f"Username: @{identity.get('username') or 'unknown'}\n"
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
    _disconnect_all(msg.from_user.id, "Telegram bot credential removed")
    await msg.answer("✅ Your Telegram bot credential and destination access were removed.")


@dp.message(Command("start"))
async def start(msg: types.Message):
    parts = (msg.text or "").split()
    if len(parts) >= 2:
        username = parts[1] if parts[1].startswith("@") else "@" + parts[1]
        try:
            text = await _register_from_telegram(msg, username)
        except CredentialError:
            text = "❌ Connect your own Telegram bot first with /connectbot <token from BotFather>."
        await msg.answer(text, reply_markup=ui.main_menu(roles.role(_uid(msg))))
        return
    role = roles.role(_uid(msg))
    head = ("Partnership bot. Set your terms with /contract (what you ACCEPT & RECEIVE). "
            "Button menu below.")
    if role == "owner":
        head = "🛠 Partnership bot — Owner menu."
    elif role == "admin":
        head = "🛠 Partnership bot — Admin menu."
    await msg.answer(head, reply_markup=ui.main_menu(role))


def _dest_label(row: dict) -> str:
    icon = {"ACTIVE": "✅", "DEGRADED": "⚠️", "VERIFYING": "⏳", "REGISTERED": "🆕"}.get(row.get("status"), "⛔")
    return f"{icon} {row.get('username') or row.get('chat_id')} · {row.get('kind')} · {row.get('status')}"


def _platform_bot_id() -> int | None:
    try:
        return int(str(BOT_TOKEN).split(":", 1)[0])
    except (ValueError, AttributeError):
        return None


def _mychannels_view(uid: int):
    rows = channels.mine(uid)
    if not rows:
        return "📂 No registered channels/groups. Connect your bot with /connectbot, then use /start @name.", None
    lines = ["📂 MY CHANNELS / GROUPS", ""]
    kb = []
    for i, row in enumerate(rows):
        lines.append(f"• {_dest_label(row)} · tier {row.get('band')}")
        if row.get("status") != "ACTIVE" and row.get("verification_reasons"):
            lines.append(f"    ↳ {row['verification_reasons'][0][:90]}")
        kb.append([InlineKeyboardButton(text=f"🔄 Re-verify {row.get('username') or row.get('chat_id')}"[:60],
                                        callback_data=f"dest:verify:{i}"),
                   InlineKeyboardButton(text="🗑", callback_data=f"dest:remove:{i}")])
        kb.append([InlineKeyboardButton(text="⚡ Auto-post ON" if row.get("auto_post") else "⚡ Auto-post off",
                                        callback_data=f"dest:auto:{i}")])
    kb.append([InlineKeyboardButton(text="⬅️ Back", callback_data="menu:hub")])
    lines += ["", "Re-verify runs a live Telegram permission check with YOUR bot.",
              "⚡ Auto-post lets partner forwards land here automatically (per destination, your call)."]
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=kb)


async def _send_mychannels(msg_or_cb, uid: int, *, edit: bool):
    text, markup = _mychannels_view(uid)
    try:
        if edit:
            await msg_or_cb.message.edit_text(text, reply_markup=markup)
        else:
            await msg_or_cb.answer(text, reply_markup=markup)
    except Exception as exc:
        if edit and "not modified" not in str(exc).lower():
            await msg_or_cb.message.answer(text, reply_markup=markup)


@dp.message(Command("mychannels"))
async def mychannels_cmd(msg: types.Message):
    await _send_mychannels(msg, _uid(msg), edit=False)


@dp.callback_query(lambda c: c.data and c.data.startswith("dest:"))
async def destination_control(cb: types.CallbackQuery):
    """Per-destination inline controls, scoped to the caller's own rows."""
    parts = cb.data.split(":")
    uid = cb.from_user.id
    rows = channels.mine(uid)
    try:
        action, row = parts[1], rows[int(parts[2])]
    except (ValueError, IndexError):
        await cb.answer("That list is out of date — reopening.", show_alert=True)
        await _send_mychannels(cb, uid, edit=True); return
    key = row.get("chat_id") or row.get("username")
    if action == "auto":
        if row.get("auto_post"):
            channels.update(uid, key, auto_post=False)
            await cb.answer("Auto-post off: partners' posts arrive as offers instead.")
        else:
            why = relay.auto_post_blocker(row, platform_bot_id=_platform_bot_id(), owner_rows=rows)
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
        await cb.answer(f"Removed {row.get('username') or key}.")
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
            result = failed_result_from_reason(row, "connect your bot with /connectbot")
        except Exception as exc:
            info = classify_telegram_error(exc)
            if info.kind.value == "unknown":
                logging.exception("re-verify of %s raised", key)
            result = failed_result_from_reason(row, f"Telegram verification failed: {info.kind.value}")
        ok, why = _apply_verification(channels.get(key), result, source="scan")
        await cb.answer(("✅ Verified" if ok else "❌ " + why)[:200], show_alert=not ok)
        await _send_mychannels(cb, uid, edit=True); return
    await cb.answer("Unknown action.", show_alert=True)


@dp.message(Command("verification"))
async def verification_cmd(msg: types.Message):
    parts = (msg.text or "").split()
    target = parts[1] if len(parts) > 1 else None
    row = next((r for r in channels.mine(_uid(msg))
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
        value = checks.get(key, "not checked")
        icon = "✅" if value == "passed" else "⚠️" if value == "unavailable" else "❌"
        lines.append(f"{icon} {label}: {escape(str(value))}")
    reasons = row.get("verification_reasons") or []
    if reasons:
        lines.extend(["", "<b>Corrective action:</b>", *[f"• {escape(str(reason))}" for reason in reasons]])
    lines.extend(["", "Run /scan to perform fresh Telegram checks."])
    await msg.answer("\n".join(lines), parse_mode="HTML")


@dp.message(Command("scan"))
async def scan_cmd(msg: types.Message):
    rows = channels.mine(_uid(msg))
    if not rows:
        await msg.answer("Register a destination first with /start @name.")
        return
    lines = ["🔍 PARTNERSHIP DESTINATION SCAN", ""]
    for row in rows:
        try:
            result = await telegram_verification.verify(_uid(msg), channels.telegram_reference(row))
            checks = result.checks or {}
            icon = lambda name: "✅" if checks.get(name) == "passed" else ("⚠️" if checks.get(name) == "unavailable" else "❌")
            lines.extend([
                f"🔍 Verifying {row.get('username')}",
                f"{icon('destination')} Resolving Telegram destination",
                f"{icon('bot_membership')} Checking bot membership",
                f"{icon('administrator')} Checking administrator status",
                f"{icon('permissions')} Checking required permissions",
            ])
            ok, _ = _apply_verification(row, result, source="scan")
            if not ok:
                lines.append(f"❌ {row.get('username')}: {'; '.join(result.reasons)}")
                continue
            lines.extend([
                f"{icon('accessibility')} Checking destination accessibility",
                f"{icon('member_count')} Retrieving member count",
                f"{icon('eligibility')} Checking eligibility",
            ])
            count = result.member_count or 0
            lines.append(f"✅ {row.get('username')}: {count:,} members (Telegram API)")
        except CredentialError:
            lines.append("❌ Connect your own bot first with /connectbot.")
            break
        except Exception as exc:
            lines.append(f"⚠️ {row.get('username')}: refresh failed ({classify_telegram_error(exc).kind.value})")
    await msg.answer("\n".join(lines))


@dp.message(Command("stats"))
async def stats_cmd(msg: types.Message):
    await scan_cmd(msg)


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
                lines.append(f"❌ {row.get('username')}: {'; '.join(result.reasons)}")
                continue
            if result.member_count is None:
                lines.append(f"⚠️ {row.get('username')}: member count unavailable")
                continue
            channels.update(cb.from_user.id, row.get("chat_id") or row.get("username"),
                            size=result.member_count,
                            telegram_member_count=result.member_count,
                            telegram_member_count_source="telegram_api",
                            telegram_member_count_checked_at=result.checked_at,
                            canonical_chat_id=result.chat_id,
                            verification_checks=result.checks or {},
                            last_verified_at=result.checked_at)
            lines.append(f"✅ {row.get('username')}: <b>{result.member_count:,}</b> members · Telegram API")
        except CredentialError:
            lines.append("❌ Connect your own bot first with /connectbot.")
            break
        except Exception:
            lines.append(f"⚠️ {row.get('username')}: Telegram refresh failed")
    await cb.message.edit_text("\n".join(lines), parse_mode="HTML",
                               reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                                   [InlineKeyboardButton(text="🔄 Refresh", callback_data="stats:all")],
                                   [InlineKeyboardButton(text="⬅️ Back", callback_data="menu:hub")]]))
    await cb.answer()


async def _send_offer_with_owner_bot(target: str, text: str, reply_markup):
    """Send a partnership offer with the destination owner's verified bot."""
    row = channels.get(target)
    if not row or not channels.participation_allowed(target):
        raise CredentialError("destination is not currently verified")
    from aiogram import Bot as TelegramBot
    token = telegram_verification.credentials.token(int(row["owner_id"]))
    owned_bot = (telegram_verification.bot_factory(token)
                 if telegram_verification.bot_factory else TelegramBot(token=token))
    try:
        return await owned_bot.send_message(
            channels.telegram_reference(row), text, reply_markup=reply_markup)
    finally:
        await owned_bot.session.close()


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
    uid = cb.from_user.id
    if which in ("add_channel", "add_group"):
        kind = "group" if which == "add_group" else "channel"
        _session_set(uid, destination_phase="name", destination_kind=kind)
        label = "GROUP" if kind == "group" else "CHANNEL"
        await cb.message.edit_text(
            f"➕ <b>ADD {label}</b>\n\nSend the {kind}'s @username or public link.\n"
            "Telegram will retrieve its current member count automatically.", parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="❌ Cancel", callback_data="menu:cancel")],
                [InlineKeyboardButton(text="⬅️ Back", callback_data="menu:channels")]]))
        await cb.answer(); return
    if which == "channels":
        rows = channels.mine(uid)
        text = "📂 <b>MY CHANNELS / GROUPS</b>\n\n" + ("\n".join(
            f"• {r.get('username')} · {r.get('kind')} · performance tier {r.get('band')} · "
            f"{('✅ VERIFIED' if r.get('verified_state') == 'VERIFIED' else '⚠️ ' + str(r.get('verified_state', 'REGISTERED')))}" for r in rows)
            if rows else "No destinations registered yet.")
        await cb.message.edit_text(text, parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="➕ Add channel", callback_data="menu:add_channel"),
                 InlineKeyboardButton(text="➕ Add group", callback_data="menu:add_group")],
                [InlineKeyboardButton(text="📊 Check Stats", callback_data="stats:all")],
                [InlineKeyboardButton(text="⬅️ Back", callback_data="menu:hub")]]))
        await cb.answer(); return
    if which == "cancel":
        _session_clear(uid)
        await cb.message.edit_text("❌ Cancelled.", reply_markup=ui.main_menu(roles.role(uid)))
        await cb.answer(); return
    if which == "submit":
        await cb.message.edit_text(
            TERMS_TEXT,
            parse_mode="HTML",
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
    blocked = _enforcement_block(uid, "partnerships", username)
    if blocked:
        await msg.answer(blocked)
        return
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
                         m.get("is_owner", False), connected=_is_connected(username))
    if cap == 0:
        await msg.answer("⛔ Participation blocked: " +
                         (channels.participation_block_reason(username) or
                          "destination is not currently eligible") +
                         "\nRun /scan after correcting the Telegram setup.")
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
        # Enforced partners are never offered anything (shared gate).
        if not enforcement_gate.check(other, "channel", "partnerships"):
            continue
        # Partnership offers cannot target an unverified destination.
        if not _is_connected(other):
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
            await _send_offer_with_owner_bot(
                target, f"{username} shared a '{cat}' post. Post it?", kb)
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
        try:
            enforcement.report(cb.from_user.id, sender, "channel", "partner flagged the post",
                               subject=f"legacy_report:{item['id']}")
        except EnforcementError as exc:
            logging.warning("compliance report mirror failed: %s", exc)
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


async def _process_partnership_broadcasts():
    """Deliver only Partnership-scope campaigns through the shared queue."""
    broadcasts.recover_stale_processing(older_than_seconds=900, retry_seconds=60)
    if enforcement_gate.safe_mode():
        # Safe mode pauses the machine: leave deliveries pending, claim nothing.
        return
    for delivery in broadcasts.claim(limit=20, bot_scope="partnership"):
        try:
            if not admit_delivery(worker_store, "partnership", delivery,
                                  rate_per_second=config.WORKER_RATE_PER_SECOND,
                                  burst=config.WORKER_RATE_BURST):
                broadcasts.fail(delivery["delivery_id"], "central delivery rate limit", retry_seconds=1)
                continue
            started = time.perf_counter()
            if not enforcement_gate.check(delivery["recipient_id"], "user", "campaigns"):
                broadcasts.fail(delivery["delivery_id"], "recipient blocked by enforcement", blocked=True)
                worker_store.record("partnership", "blocked")
                continue
            campaign = broadcasts.get_campaign(delivery["campaign_id"])
            payload = campaign.get("payload", {}) if campaign else {}
            text = payload.get("text")
            if not text:
                broadcasts.fail(delivery["delivery_id"], "campaign has no explicit text payload", blocked=True)
                continue
            sent = await bot.send_message(int(delivery["recipient_id"]), text)
            broadcasts.complete(delivery["delivery_id"], sent.message_id)
            worker_store.record("partnership", "delivered", (time.perf_counter() - started) * 1000)
        except Exception as exc:
            worker_store.record("partnership", "failed", (time.perf_counter() - started) * 1000 if 'started' in locals() else 0)
            blocked = any(token in str(exc).lower() for token in ("blocked", "chat not found", "deactivated"))
            broadcasts.fail(delivery["delivery_id"], str(exc)[:500], blocked=blocked,
                            retry_seconds=min(3600, 30 * (2 ** min(delivery.get("attempts", 1), 6))))


_REVERIFY_BATCH = int(config.env("REVERIFY_BATCH", "5") or 5)


async def _background_reverify(*, now: int | None = None) -> list[str]:
    """State-aware scheduled re-verification (mirrors reward_bot). Bounded batch,
    paused in safe mode, owner notified only on a real status change."""
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
            result = failed_result_from_reason(row, "connect your bot with /connectbot")
        except Exception as exc:
            info = classify_telegram_error(exc)
            if info.kind.value == "unknown":
                logging.exception("background re-verify of %s raised", key)
            result = failed_result_from_reason(row, f"Telegram verification failed: {info.kind.value}")
        if result is None:
            continue
        _apply_verification(row, result, source="background")
        after = (channels.get(key) or {}).get("status", before)
        try:
            channels.update(int(row["owner_id"]), key, last_recheck_at=int(now if now is not None else time.time()))
        except (KeyError, ValueError):
            pass
        if before != after:
            note = f"{row.get('username') or key}: {before} → {after}"
            notes.append(note)
            try:
                await bot.send_message(int(row["owner_id"]), f"🔎 Destination access changed\n• {note}"
                                       + ("" if after == "ACTIVE" else
                                          "\n\nTap /mychannels → Re-verify after fixing the bot's admin rights."))
            except Exception as exc:
                logging.warning("reverify notice to %s failed: %s", row.get("owner_id"), classify_telegram_error(exc).kind.value)
    return notes


async def notify_loop():
    """Hand out partner-scope review outcomes to their senders."""
    while True:
        try:
            await _background_reverify()
            await _process_partnership_broadcasts()
            for item in review.pending_notify("partnership"):
                if await _notify_review_sender(item):
                    review.clear_notify(item["id"])
        except Exception as e:
            logging.warning("notify_loop error: %s", e)
        await asyncio.sleep(15)


@dp.message(lambda m: not (m.text or "").startswith("/")
            and _session(m.from_user.id).get("destination_phase"))
async def destination_capture(msg: types.Message):
    """Button-led registration for partnership destinations."""
    uid = msg.from_user.id
    sess = _session(uid)
    raw = (msg.text or "").strip()
    if sess.get("destination_phase") == "name":
        if raw.startswith("https://t.me/"):
            raw = "@" + raw.rstrip("/").split("/")[-1].split("?")[0]
        if not raw.startswith("@"):
            raw = "@" + raw
        if len(raw) < 2 or " " in raw or raw == "@":
            await msg.answer("Please send a valid @username or public Telegram link."); return
        _session_clear(uid)
        try:
            text = await _register_from_telegram(msg, raw,
                                                  kind=sess.get("destination_kind", "channel"))
        except CredentialError:
            text = "❌ Connect your own Telegram bot first with /connectbot, then try again."
        await msg.answer(text, parse_mode="HTML", reply_markup=ui.main_menu(roles.role(uid)))
        return


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
